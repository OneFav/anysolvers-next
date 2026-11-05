"""Durable logical observers. This database never holds a research writer lock.

Only Router writes K/scopes/reviews. Observers read evidence and atomically commit
judgments + cursors + an outbox. Neither path grants execution or researcher input.
"""
import contextlib
import copy
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from .engine import Conflict, digest, required
from .store import Store, encode
from .communication import notification

ID = re.compile(r'[A-Za-z0-9_.:-]{1,256}')
KINDS = ('analysis', 'tool_input', 'tool_result', 'material', 'source', 'evaluation', 'progress', 'scope_end')
K_FIELDS = ('shared_background', 'research_goals', 'expectations', 'judgment_basis', 'human_decisions', 'communication')
DEFAULTS = dict(window_s=30, max_wait_s=120, max_events=80, max_batch_bytes=48000,
                per_batch_events=20, read_bytes=32768, page_events=50, max_material_bytes=524288,
                max_session_batches=12, max_session_bytes=240000)


def ident(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Invalid observer identity')
    return value


def bounded(value, limit, name):
    if len(encode(value).encode()) > limit:
        raise ValueError(name + ' exceeds size limit')
    return value


def fit_text(value, byte_limit):
    """Bound JSON-escaped UTF-8 text, including quotes/control characters."""
    low,high=0,len(value)
    while low<high:
        mid=(low+high+1)//2
        size=len(json.dumps(value[:mid],ensure_ascii=False).encode())-2
        if size<=byte_limit:low=mid
        else:high=mid-1
    return value[:low]


class Observer:
    def __init__(self, path, options=None, clock=time.time):
        self.store = Store(path)
        self.options = dict(DEFAULTS, **(options or {}))
        for key in DEFAULTS:
            value = self.options[key]
            if type(value) not in (int, float) or not 0 < value <= 1048576:
                raise ValueError('Invalid observer option ' + key)
        if self.options['window_s'] > self.options['max_wait_s'] or self.options['max_wait_s'] > 120:
            raise ValueError('Observer oldest wait must not exceed 120s')
        if self.options['max_batch_bytes'] < 8192:
            raise ValueError('Observer batch must fit one bounded overview')
        self.clock = clock
        self.read_gate = threading.BoundedSemaphore(1)
        with contextlib.closing(self.store.connect()) as conn:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS evidence(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,job TEXT NOT NULL,
              batch TEXT NOT NULL,session TEXT NOT NULL,position INTEGER NOT NULL,kind TEXT NOT NULL,
              at REAL NOT NULL,received REAL NOT NULL,body TEXT NOT NULL,content TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS evidence_job_seq ON evidence(job,seq);
            CREATE INDEX IF NOT EXISTS evidence_session_pos ON evidence(session,position,seq);
            CREATE INDEX IF NOT EXISTS evidence_call ON evidence(json_extract(body,'$.call_id'),session,seq);
            CREATE INDEX IF NOT EXISTS observer_report_recent ON records(json_extract(body,'$.observer_id'),json_extract(body,'$.created_at') DESC) WHERE kind='observer_reports';
            ''')
        with self.store.transaction() as tx:
            if not tx.get('observer_k', 'current'):
                tx.put('observer_k', 'current', dict(revision=0, people={}, updated_at=self.clock()))
            if not tx.get('observers', 'default'):
                tx.put('observers', 'default', self._new('default'))

    def _new(self, oid):
        return dict(id=oid, all_research=True, excluded=[], comparisons=[], cursors={}, notes='',
                    comparisons_state={}, clues=[], k_revision=-1, scope_revision=0, seen_scope_revision=0,
                    active=None, last_batch='', session=None, session_batches=0, session_bytes=0)

    def k(self):
        return self.store.get('observer_k', 'current')

    def replace_k(self, expected_revision, people):
        bounded(people, 48000, 'K')
        if not isinstance(people, dict):
            raise ValueError('K.people must be keyed by person identity')
        fields = K_FIELDS
        for pid, person in people.items():
            ident(pid)
            if not isinstance(person, dict) or set(person) != set(fields):
                raise ValueError('K person requires ' + ', '.join(fields))
            for entries in person.values():
                if not isinstance(entries, list):
                    raise ValueError('K fields must contain explicit/assumed entries')
                for entry in entries:
                    if not isinstance(entry, dict) or entry.get('certainty') not in ('explicit', 'assumed'):
                        raise ValueError('K must distinguish explicit consensus from assumptions')
                    ident(entry.get('id'))
                    required(entry.get('text'), 'K entry', 4000)
                    if not isinstance(entry.get('basis'), list) or not entry['basis']:
                        raise ValueError('K entry basis must be a nonempty array of instruction/correction/feedback/report references, e.g. ["instruction:event-id"]')
        with self.store.transaction() as tx:
            old = tx.get('observer_k', 'current')
            if old['revision'] != expected_revision:
                raise Conflict('K changed; read current K before replacing')
            value = dict(revision=old['revision'] + 1, people=people, updated_at=self.clock())
            # No audit snapshot or historical K record, including in events.
            tx.put('observer_k', 'current', value)
            return value

    def configure(self, oid='default', *, all_research=None, add=None, remove=None, comparisons=None, independent_reason=None):
        if not isinstance(oid,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',oid):
            raise ValueError('Observer process identity must be a safe directory name')
        with self.store.transaction() as tx:
            state = tx.get('observers', oid)
            if not state:
                required(independent_reason, 'reason for independent observer', 1000)
                state = self._new(oid)
                state['all_research'] = False
                state['independent_reason'] = independent_reason
            if all_research is not None:
                if type(all_research) is not bool:
                    raise ValueError('all_research must be boolean')
                state['all_research'] = all_research
            for job in add or []:
                ident(job)
                state['excluded'] = [v for v in state['excluded'] if v != job]
                tx.put('observer_scope', oid + ':' + job, dict(id=oid + ':' + job, observer_id=oid, job_id=job))
            for job in remove or []:
                ident(job)
                state['excluded'] = sorted(set(state['excluded'] + [job]))
            if comparisons is not None:
                bounded(comparisons, 16000, 'comparisons')
                if not isinstance(comparisons, list):
                    raise ValueError('comparisons must be a list')
                for comparison in comparisons:
                    ident(comparison.get('id'))
                    if len(comparison.get('jobs', [])) < 2:
                        raise ValueError('Comparison needs at least two batches/jobs')
                    for job in comparison['jobs']:
                        ident(job)
                    for field in ('expectation', 'metrics', 'conditions'):
                        if not comparison.get(field):
                            raise ValueError('Comparison requires ' + field)
                state['comparisons'] = comparisons
            state['scope_revision'] += 1
            tx.put('observers', oid, state)
            return state

    def register_job(self, job):
        if job.get('stage') != 'research':
            raise ValueError('Only research jobs enter observation')
        safe = {k: job.get(k) for k in ('id', 'project_id', 'host_id', 'state', 'version_id', 'version_digest', 'owner', 'channel', 'title')}
        safe['run_id'] = (job.get('progress') or {}).get('run_id') or (job.get('output') or {}).get('run_id')
        sessions=(job.get('progress') or {}).get('sessions') or []
        safe['sessions']=[{k:s.get(k) for k in ('session_id','native_id','status','actor','generation')} for s in sessions[:100]]
        safe['sessions_truncated']=len(sessions)>100
        ident(safe['id'])
        with self.store.transaction() as tx:
            old = tx.get('observer_jobs', safe['id'])
            tx.put('observer_jobs', safe['id'], safe)
            if safe['state'] in ('completed', 'stopped') and (not old or old['state'] != safe['state']):
                self._ingest(tx, dict(job_id=safe['id'], batch=safe['run_id'] or safe['id'], session='host', position=0,
                    kind='scope_end', at=self.clock(), object='lifecycle', version=digest(safe), content=encode(dict(state=safe['state']))))
        return safe

    @staticmethod
    def _jobs(tx, state):
        explicit = {s['job_id'] for s in tx.list('observer_scope') if s['observer_id'] == state['id']}
        return {j['id'] for j in tx.list('observer_jobs')
                if (state['all_research'] or j['id'] in explicit) and j['id'] not in state['excluded']}

    def ingest(self, records):
        if not isinstance(records, list) or len(records) > 64:
            raise ValueError('At most 64 observation records per ingest')
        bounded(records, 900000, 'observation ingest')
        with self.store.transaction() as tx:
            return [self._ingest(tx, record) for record in records]

    def _ingest(self, tx, record):
        record = dict(record)
        job = tx.get('observer_jobs', record.get('job_id', ''))
        if not job:
            raise ValueError('Observation needs a registered research job')
        if record.get('kind') not in KINDS or record.get('purpose') == 'final':
            raise ValueError('Observation outside research/development boundary')
        for key in ('session', 'object', 'version', 'batch'):
            required(record.get(key), key, 512)
        if any(part in ('final','visualization') for part in Path(record['object'].split('#')[0]).parts):
            raise ValueError('Observation cannot read terminal materials')
        if not re.fullmatch(r'[a-f0-9]{64}', record['version']):
            raise ValueError('Fixed content version must be SHA256')
        pos = record.get('position')
        if type(pos) is not int or pos < 0 or not isinstance(record.get('at'), (float, int)):
            raise ValueError('Observation requires sequence and timestamp')
        content = required(record.get('content'), 'observation content', int(self.options['max_material_bytes']))
        if len(content.encode()) > self.options['max_material_bytes']:
            raise ValueError('Observation content exceeds byte limit')
        if record.get('content_sha', hashlib.sha256(content.encode()).hexdigest()) != hashlib.sha256(content.encode()).hexdigest():
            raise ValueError('Observation integrity mismatch')
        eid = digest([record['job_id'], record['batch'], record['session'], pos, record['object'], record['version']])
        old = tx.connection.execute('SELECT body,content FROM evidence WHERE id=?', (eid,)).fetchone()
        # Excerpts are mechanical, never filtered by K/share/score/author opinion.
        body = {k: v for k, v in record.items() if k != 'content'}
        body.update(id=eid, excerpt=content[:600], truncated=len(content) > 600, content_chars=len(content),
                    content_bytes=len(content.encode()), read=dict(kind='material', id=eid, offset=0),
                    trust='measurement_requires_validation' if record['kind'] == 'evaluation' else 'author_unverified')
        bounded(body,8192,'evidence overview')
        if old:
            if old[1] != content or json.loads(old[0])['kind'] != body['kind']:
                raise Conflict('Observation identity collision')
            return eid
        tx.connection.execute('INSERT INTO evidence(id,job,batch,session,position,kind,at,received,body,content) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (eid, record['job_id'], record['batch'], record['session'], pos, record['kind'], record['at'], self.clock(), encode(body), content))
        return eid

    def open_session(self, oid):
        """Supervisor calls only after verifying its previous OS process exited."""
        with self.store.transaction() as tx:
            state = tx.get('observers', ident(oid))
            if not state:
                raise ValueError('Unknown observer')
            session = dict(id=str(uuid.uuid4()), token=secrets.token_urlsafe(32), ready=False, at=self.clock())
            state.update(session=session, session_batches=0, session_bytes=0)
            if state['active']:
                state['active']['session_id'] = session['id']
            tx.put('observers', oid, state)
            return session

    def _auth(self, tx, oid, sid, token, ready=True):
        state = tx.get('observers', oid)
        session = (state or {}).get('session') or {}
        if session.get('id') != sid or not hmac.compare_digest(session.get('token', ''), token):
            raise Conflict('Observer session has been fenced')
        if ready and not session.get('ready'):
            raise Conflict('Observer session is not ready')
        return state

    def ready(self, oid, sid, token):
        with self.store.transaction() as tx:
            state = self._auth(tx, oid, sid, token, False)
            state['session']['ready'] = True
            tx.put('observers', oid, state)
            return dict(k=tx.get('observer_k', 'current'), notes=state['notes'], cursors=state['cursors'],
                        comparisons=state['comparisons'], comparisons_state=state['comparisons_state'], clues=state['clues'])

    def _pending(self, tx, state, limit=81):
        heads = []
        for job in sorted(self._jobs(tx, state)):
            cursor=state['cursors'].get(job,0)
            head=tx.connection.execute('SELECT received FROM evidence WHERE job=? AND seq>? ORDER BY seq LIMIT 1',(job,cursor)).fetchone()
            if head:
                count=tx.connection.execute('SELECT COUNT(*) FROM (SELECT 1 FROM evidence WHERE job=? AND seq>? LIMIT ?)',(job,cursor,limit)).fetchone()[0]
                heads.append((job,head[0],count))
        # Round-robin jobs; a high-density batch cannot monopolize model input.
        heads.sort(key=lambda pair: (pair[0] <= state['last_batch'], pair[0]))
        selected=heads[:limit]
        per_job=max(1,(limit+len(selected)-1)//max(1,len(selected)))
        groups=[]
        remaining=limit
        for job,_,_ in selected:
            rows=tx.connection.execute('SELECT seq,received,body FROM evidence WHERE job=? AND seq>? ORDER BY seq LIMIT ?',
                (job,state['cursors'].get(job,0),min(per_job,remaining))).fetchall()
            groups.append((job,rows));remaining-=len(rows)
            if not remaining:break
        return groups,min((h[1] for h in heads),default=self.clock()),sum(h[2] for h in heads)

    def next(self, oid, sid, token):
        with self.store.transaction() as tx:
            state = self._auth(tx, oid, sid, token)
            k = tx.get('observer_k', 'current')
            if state['active']:
                return dict(batch=state['active'], k=k,scope_revision=state['scope_revision'])
            groups,oldest,count = self._pending(tx, state, int(self.options['max_events']) + 1)
            k_changed = k['revision'] != state['k_revision']
            scope_changed = state['scope_revision'] != state['seen_scope_revision']
            due = [c for c in state['clues'] if c.get('check_at') is not None and c['check_at'] <= self.clock()
                   and c.get('last_checked_at', 0) < c['check_at']]
            size = sum(len(r[2].encode()) for _, rows in groups for r in rows)
            end = any(json.loads(r[2])['kind'] == 'scope_end' for _, rows in groups for r in rows)
            wake = (bool(groups) and (self.clock() - oldest >= self.options['window_s']
                    or self.clock() - oldest >= self.options['max_wait_s'] or count >= self.options['max_events']
                    or size >= self.options['max_batch_bytes'] or end))
            wake = wake or bool(state['clues'] and (k_changed or scope_changed or due))
            if not wake:
                return dict(batch=None, next_check_at=min(oldest + self.options['window_s'], self.clock()+30))
            events, cursors, used = [], {}, 0
            per_job = int(self.options['per_batch_events'])
            full=set()
            for i in range(per_job):
                for job, rows in groups:
                    if i >= len(rows) or job in full:
                        continue
                    seq, _, body = rows[i]
                    item = dict(json.loads(body), seq=seq)
                    item_size = len(encode(item).encode())
                    if len(events) >= self.options['max_events'] or used + item_size > self.options['max_batch_bytes']:
                        full.add(job)
                        continue
                    events.append(item)
                    used += item_size
                    cursors[job] = seq
            if not events and groups:
                raise ValueError('A single evidence overview exceeds batch input capacity')
            cutoff = tx.connection.execute('SELECT COALESCE(MAX(seq),0) FROM evidence').fetchone()[0]
            batch = dict(id='judgment-' + uuid.uuid4().hex, session_id=sid, events=events, cursors=cursors, cutoff=cutoff,
                         k_revision=k['revision'], scope_revision=state['scope_revision'], notes=state['notes'],
                         clues=state['clues'], comparisons=state['comparisons'], comparisons_state=state['comparisons_state'],
                         triggers=dict(k_changed=k_changed, scope_changed=scope_changed, due=[c['id'] for c in due]), at=self.clock())
            state['active'] = batch
            tx.put('observers', oid, state)
            return dict(batch=batch, k=k)

    def read(self, oid, sid, token, query):
        # Serialize source reading, bound result bytes and rows; release read snapshot before model work.
        with self.read_gate, contextlib.closing(self.store.connect()) as conn:
            from .store import Transaction
            tx = Transaction(conn)
            state = self._auth(tx, oid, sid, token)
            active = state['active']
            if not active:
                raise Conflict('Deep reads require a current judgment')
            cutoff = min(int(query.get('cutoff', active['cutoff'])), active['cutoff'])
            jobs = self._jobs(tx, state)
            kind = query.get('kind')
            limit = max(1, min(int(query.get('limit', 20)), int(self.options['page_events'])))
            if kind == 'context':
                # A new native session must know what the logical observer has
                # already reported, even when model-authored notes omitted it.
                # Project only bounded fields; do not parse every full report.
                recent=[]
                for row in conn.execute("""SELECT id,json_extract(body,'$.state'),json_extract(body,'$.created_at'),
                    substr(json_extract(body,'$.report.judgment'),1,600),json_extract(body,'$.report.references')
                    FROM records WHERE kind='observer_reports' AND json_extract(body,'$.observer_id')=?
                    ORDER BY json_extract(body,'$.created_at') DESC LIMIT 4""",(oid,)):
                    refs=json.loads(row[4] or '[]')
                    recent.append(dict(id=row[0],state=row[1],at=row[2],judgment_excerpt=row[3],references=refs[:6],references_truncated=len(refs)>6))
                return dict(judgment_id=active['id'],k=tx.get('observer_k', 'current'), cutoff=cutoff, scope_revision=state['scope_revision'],comparisons=state['comparisons'],jobs=sorted(jobs),recent_reports=recent)
            if kind == 'status':
                start=max(0,int(query.get('after',0)))
                rows=[{k:v for k,v in j.items() if k != 'channel'} for j in tx.list('observer_jobs') if j['id'] in jobs]
                items=[];used=0
                for row in rows[start:start+limit]:
                    size=len(encode(row).encode())
                    if used+size>self.options['read_bytes']+4000:break
                    items.append(row);used+=size
                return dict(jobs=items,cutoff=cutoff,next=start+len(items),more=start+len(items)<len(rows))
            after = max(0, int(query.get('after', 0)))
            args = [after, cutoff]
            where = 'seq>? AND seq<=?'
            if kind == 'material':
                where += ' AND id=?'
                args.append(query.get('id'))
            elif kind == 'session':
                where += ' AND session=? AND position>=? AND position<=?'
                position = max(0, int(query.get('position', 0)))
                before = min(50, max(0, int(query.get('before', 5))))
                window = min(50, max(0, int(query.get('after_window', 5))))
                args.extend([query.get('session'), max(0, position-before), position+window])
            elif kind == 'tool':
                # Call ID is explicit metadata, never inferred from a text search.
                where += " AND json_extract(body,'$.call_id')=?"
                args.append(query.get('call_id'))
                if query.get('session'):
                    where += ' AND session=?'
                    args.append(query['session'])
            elif kind not in ('index', 'events'):
                raise ValueError('Unknown read kind')
            if query.get('job_id'):
                jobs &= {query['job_id']}
            if not jobs:
                return dict(items=[], cutoff=cutoff, next=after)
            where += ' AND job IN (' + ','.join('?' for _ in jobs) + ')'
            args.extend(sorted(jobs))
            # substr avoids copying large content columns for index/overview reads.
            offset = max(0, int(query.get('offset', 0)))
            cap = min(int(query.get('bytes', self.options['read_bytes'])), int(self.options['read_bytes']))
            if cap < 16:
                raise ValueError('Evidence read limit must be at least 16 bytes')
            sql = 'SELECT seq,body' + (',substr(content,?,?)' if kind in ('material','session','tool') else '') + ' FROM evidence WHERE ' + where + ' ORDER BY seq LIMIT ?'
            if kind in ('material','session','tool'):
                args = [offset+1, cap, *args]
            rows = conn.execute(sql, [*args, limit])
            items, used, cursor,more = [], 0, after,False
            for row in rows:
                item = dict(json.loads(row[1]), seq=row[0])
                if len(row) == 3:
                    content = fit_text(row[2],cap)
                    item.update(content=content, offset=offset, next_offset=offset+len(content),
                                content_truncated=offset+len(content) < item.get('content_chars', item['content_bytes']))
                size = len(encode(item).encode())
                if used + size > cap + 9216:
                    more=True
                    break
                items.append(item)
                used += size
                cursor = row[0]
            return dict(items=items, cutoff=cutoff, next=cursor, more=more or len(items)==limit,
                        offset_unit='unicode characters', returned_bytes=used)

    def commit(self, oid, sid, token, value):
        bounded(value, 64000, 'judgment')
        fingerprint=digest(value)
        value=copy.deepcopy(value)
        bid = required(value.get('batch_id'), 'judgment batch')
        with self.store.transaction() as tx:
            state = self._auth(tx, oid, sid, token)
            old = tx.get('observer_commits', bid)
            if old:
                if old['fingerprint'] != fingerprint:
                    raise Conflict('Committed judgment is immutable')
                return old['receipt']
            batch = state['active']
            if not batch or batch['session_id'] != sid:
                raise Conflict('No judgment owned by this session')
            if batch['id'] != bid:
                raise ValueError('batch_id must be the current judgment_id '+batch['id']+'; a research run ID is not a judgment ID. Read kind=context to recover it.')
            # The model must re-read changed K/scopes before publishing its judgment.
            if value.get('k_revision') != tx.get('observer_k','current')['revision'] or value.get('scope_revision') != state['scope_revision']:
                raise Conflict('K/scope changed during judgment; re-read current context')
            if value.get('disposition') not in ('ignore', 'merge', 'wait', 'report'):
                raise ValueError('Invalid judgment disposition')
            notes = value.get('notes', '')
            if not isinstance(notes, str) or len(notes) > 12000:
                raise ValueError('Current notes exceed limit')
            clues = value.get('clues', [])
            if not isinstance(clues, list) or len(clues) > 40:
                raise ValueError('At most 40 unresolved propositions')
            if value['disposition']=='wait' and not clues:
                raise ValueError('Waiting requires a pending clue with missing evidence and recheck conditions')
            seen=set()
            for clue in clues:
                for key in ('id', 'proposition', 'missing', 'recheck_when'):
                    required(clue.get(key), 'clue '+key, 2000)
                if not isinstance(clue.get('support'), list) or not isinstance(clue.get('against'), list):
                    raise ValueError('Clues require separate support and counter evidence')
                if any(not isinstance(ref,str) for field in ('support','against') for ref in clue[field]):
                    raise ValueError('Clue support/against must be arrays of evidence-ID strings')
                if clue['id'] in seen:raise ValueError('Merge duplicate proposition IDs')
                seen.add(clue['id'])
                for field in ('support','against'):
                    clue[field]=list(dict.fromkeys(clue[field]))
                    for ref in clue[field]:
                        row=tx.connection.execute('SELECT job FROM evidence WHERE id=? AND seq<=?',(ref,batch['cutoff'])).fetchone()
                        if not row:raise ValueError('Clue evidence requires a fixed observed reference')
                check = clue.get('check_at')
                if check is not None and (not isinstance(check, (float,int)) or check <= self.clock()):
                    raise ValueError('Recheck time must be future or null')
                clue['last_checked_at'] = self.clock()
            report = value.get('report')
            if bool(report) != (value['disposition'] == 'report'):
                raise ValueError('Exactly one combined report per report judgment')
            rid = None
            if report:
                for field in ('judgment', 'people', 'decisions', 'confidence', 'limitations', 'references', 'timing'):
                    if not report.get(field):
                        raise ValueError('Report requires ' + field)
                if not isinstance(report['references'], list) or not isinstance(report['people'], list):
                    raise ValueError('Report people/references must be lists')
                if any(not isinstance(ref,str) for field in ('references','people') for ref in report[field]):
                    raise ValueError('Report people/references must be arrays of ID strings')
                legacy=report.get('expectations',[])
                if not isinstance(legacy,list) or any(not isinstance(e,str) for e in legacy):
                    raise ValueError('Report expectations must be an array of current K expectation-ID strings')
                k_refs=report.get('k_refs',[])
                if not isinstance(k_refs,list) or len(k_refs)>24:
                    raise ValueError('Report k_refs must be an array of at most 24 person/field/id objects')
                people=tx.get('observer_k','current')['people']
                for person in report['people']:
                    if person not in people:
                        raise ValueError('Report person is not in current K')
                for ref in k_refs:
                    if not isinstance(ref,dict) or set(ref)!={'person','field','id'} or any(not isinstance(v,str) for v in ref.values()):
                        raise ValueError('Each k_refs entry requires person, field and id strings')
                    if ref['person'] not in report['people'] or ref['field'] not in K_FIELDS:
                        raise ValueError('K reference must belong to a reported person and a current K field')
                    if ref['id'] not in {entry['id'] for entry in people[ref['person']][ref['field']]}:
                        raise ValueError('K reference must identify an entry in the latest K')
                expectations={entry['id'] for person in report['people'] for entry in people[person]['expectations']}
                if any(e not in expectations for e in legacy):
                    raise ValueError('Report expectations must reference current K entries for reported people')
                if not legacy and not k_refs:
                    raise ValueError('Report requires at least one current K reference: k_refs (e.g. a research goal) or legacy expectations; do not invent a human expectation')
                if len(report['references'])>24:raise ValueError('Combined report may cite at most 24 fixed references')
                jobs = self._jobs(tx, state)
                details=[]
                for ref in report['references']:
                    row = tx.connection.execute('SELECT job,body FROM evidence WHERE id=? AND seq<=?', (ref, batch['cutoff'])).fetchone()
                    if not row or row[0] not in jobs:
                        raise ValueError('Report reference outside fixed read cutoff/scope')
                    details.append(json.loads(row[1]))
                report['expectations']=list(dict.fromkeys(legacy))
                report['k_refs']=[dict(zip(('person','field','id'),key)) for key in dict.fromkeys((r['person'],r['field'],r['id']) for r in k_refs)]
                report['references']=list(dict.fromkeys(report['references']))
                rid = 'report-' + digest([oid, bid])[:32]
                tx.put('observer_reports', rid, dict(id=rid, observer_id=oid, batch_id=bid, report=report,
                       evidence=details,cutoff=batch['cutoff'],k_revision=value['k_revision'], state='pending', created_at=self.clock(), receipt=None))
            state.update(notes=notes, comparisons_state=value.get('comparisons_state', {}), clues=clues,
                         k_revision=value['k_revision'], seen_scope_revision=value['scope_revision'], active=None,
                         session_batches=state['session_batches']+1,
                         session_bytes=state['session_bytes']+len(encode(batch).encode())+len(encode(value).encode()))
            state['cursors'].update(batch['cursors'])
            if batch['cursors']:
                state['last_batch'] = next(reversed(batch['cursors']))
            tx.put('observers', oid, state)
            rotate = bool(value.get('rotate')) or state['session_batches'] >= self.options['max_session_batches'] or state['session_bytes'] >= self.options['max_session_bytes']
            receipt = dict(batch_id=bid, report_id=rid, committed=True, rotate=rotate)
            tx.put('observer_commits', bid, dict(id=bid, fingerprint=fingerprint, receipt=receipt))
            return receipt

    def rotate(self, oid, sid, token):
        with self.store.transaction() as tx:
            current=tx.get('observers',oid)
            previous=(current or {}).get('rotation',{})
            if previous.get('from')==sid and hmac.compare_digest(previous.get('token_hash',''),digest(token)) and previous.get('to')==current['session']['id']:
                return current['session']
            state = self._auth(tx, oid, sid, token)
            if state['active']:
                raise Conflict('Finish the current batch before context handover')
            session=dict(id=str(uuid.uuid4()),token=secrets.token_urlsafe(32),ready=False,at=self.clock())
            state.update(session=session,session_batches=0,session_bytes=0,rotation=dict(from_=sid,to=session['id'],token_hash=digest(token)))
            state['rotation']['from']=state['rotation'].pop('from_')
            tx.put('observers', oid, state)
            return session

    def health(self, oid, sid, token, state, detail):
        if state not in ('unavailable', 'verified_recovered'):
            raise ValueError('Invalid observer provider health')
        required(detail, 'provider health detail', 1000)
        with self.store.transaction() as tx:
            self._auth(tx, oid, sid, token, False)
            key = oid + ':provider'
            old = tx.get('observer_incidents', key)
            if state == 'unavailable':
                value = dict(id=key, state='open', detail=detail, at=self.clock(), episode=(old or {}).get('episode',0)+(not old or old['state']!='open'))
            else:
                value = dict(old or {}, id=key, state='resolved', detail=detail, recovered_at=self.clock())
            tx.put('observer_incidents', key, value)
            return value

    def router_inbox(self, engine):
        # Durable idempotent receive in Router DB, then ack observer outbox.
        for report in self.store.list('observer_reports'):
            if report['state'] != 'pending':
                continue
            with engine.store.transaction() as tx:
                if not tx.get('observer_inbox', report['id']):
                    tx.put('observer_inbox', report['id'], dict(report, state='received', received_at=self.clock()))
            with self.store.transaction() as tx:
                record = tx.get('observer_reports', report['id'])
                record.update(state='received', receipt='router:'+report['id'], received_at=self.clock())
                tx.put('observer_reports', report['id'], record)
        outbox = engine.store.list('outbox')
        sent = sorted((r for r in outbox if r.get('observer_report_id') and r.get('state')=='sent'),
                      key=lambda r:(r.get('sent_at') or r.get('created_at',0), r['id']))[-12:]
        # A bounded delivery history helps Router describe changes relative to
        # earlier messages, without treating an attempted send as human knowledge.
        recent = [dict(id=r['id'], observer_report_id=r['observer_report_id'], job_id=r.get('job_id'),
                       sent_at=r.get('sent_at'), text=r.get('text','')[:2000],
                       truncated=len(r.get('text',''))>2000) for r in sent]
        return dict(k=self.k(), observers=self.public(), reports=[r for r in engine.store.list('observer_inbox') if r['state']=='received'],
                    recent_communications=recent,
                    deliveries=[{k:r.get(k) for k in ('id','observer_report_id','state','delivery_receipt','error')}
                        for r in outbox if r.get('observer_report_id')],
                    feedback=engine.store.list('observer_feedback')[-50:],incidents=self.store.list('observer_incidents'))

    def router_read(self, engine, report_id, evidence_id, offset=0, bytes=32768):
        report=engine.store.get('observer_inbox',report_id)
        if not report or evidence_id not in report['report']['references']:
            raise ValueError('Router may expand fixed references from its received report')
        offset=max(0,int(offset));cap=min(int(self.options['read_bytes']),int(bytes))
        if cap<16:raise ValueError('Evidence read limit must be at least 16 bytes')
        with self.read_gate,contextlib.closing(self.store.connect()) as conn:
            row=conn.execute('SELECT body,substr(content,?,?) FROM evidence WHERE id=? AND seq<=?',
                (offset+1,cap,evidence_id,report['cutoff'])).fetchone()
            if not row:raise ValueError('Referenced evidence unavailable')
            body=json.loads(row[0]);content=fit_text(row[1],cap)
            return dict(evidence=body,content=content,cutoff=report['cutoff'],offset=offset,
                        next_offset=offset+len(content),more=offset+len(content)<body['content_chars'])

    def review(self, engine, report_id, decision):
        if not isinstance(decision,dict):
            raise ValueError('review decision must be an object with action, k_revision and reason; send also needs job_id, text and people inside decision')
        required(decision.get('reason'), 'report review reason', 2000)
        if decision.get('action') not in ('send', 'silent', 'defer'):
            raise ValueError('Invalid report review action')
        with self.store.transaction() as observer_tx, engine.store.transaction() as tx:
            if decision.get('k_revision') != observer_tx.get('observer_k','current')['revision']:
                raise Conflict('Router must review against latest K')
            report = tx.get('observer_inbox', report_id)
            if not report:
                raise ValueError('Router has not received report')
            if report['state'] == 'reviewed':
                return report
            if decision['action'] == 'defer':
                report['review_after'] = decision.get('review_after', self.clock()+120)
                report['last_review'] = decision
            else:
                report.update(state='reviewed', review=decision, reviewed_at=self.clock())
                if decision['action'] == 'send':
                    job = self.store.get('observer_jobs', decision.get('job_id',''))
                    if not job:
                        raise ValueError('Report delivery needs a registered research job')
                    allowed=set()
                    for ref in report['report']['references']:
                        row=observer_tx.connection.execute('SELECT job FROM evidence WHERE id=?',(ref,)).fetchone()
                        if row:allowed.add(row[0])
                    if job['id'] not in allowed:
                        raise ValueError('Delivery job outside observer scope')
                    text = required(decision.get('text'), 'Router outward expression', 6000)
                    people = decision.get('people', [])
                    mapping = engine.config.get('feishu', {}).get('identity_map', {})
                    mentions = []
                    for person in people:
                        open_id = mapping.get(person)
                        if not isinstance(open_id,str) or not re.fullmatch(r'ou_[A-Za-z0-9_-]+',open_id):
                            raise ValueError('Configure Feishu identity_map for ' + str(person))
                        mentions.append(open_id)
                    if not people:
                        raise ValueError('Report delivery must designate people')
                    oid = digest(['observer-report', report_id])[:32]
                    if not tx.get('outbox', oid):
                        tx.put('outbox', oid, dict(id=oid, job_id=job['id'], channel=job['channel'],
                            **notification(text, 'research_signal', report_id),
                            mention=None, mentions=sorted(set(mentions)), state='pending', attempts=0, due_at=self.clock(),
                            created_at=self.clock(), observer_report_id=report_id, receipt_policy='reconcile_unknown'))
                    report['delivery_id'] = oid
            tx.put('observer_inbox', report_id, report)
            return report

    def feedback(self, engine, report_id, trigger_id, interpretation):
        trigger = engine.store.get('triggers', trigger_id)
        report = engine.store.get('observer_inbox', report_id)
        if not trigger or not trigger['admitted'] or not report:
            raise ValueError('Feedback needs an admitted human message and received report')
        required(interpretation, 'Router feedback interpretation', 2000)
        with engine.store.transaction() as tx:
            key = digest([report_id, trigger_id])
            tx.put('observer_feedback', key, dict(id=key, report_id=report_id, trigger_id=trigger_id,
                person=trigger['actor'], interpretation=interpretation, at=self.clock()))
        return dict(recorded=True, k_updated=False, note='Router must explicitly replace K; sent does not mean understood')

    def public(self):
        with contextlib.closing(self.store.connect()) as conn:
            from .store import Transaction
            tx = Transaction(conn)
            return [dict(id=s['id'], jobs=sorted(self._jobs(tx,s)), cursors=s['cursors'],
                         notes=s['notes'], clues=s['clues'], comparisons=s['comparisons'], scope_revision=s['scope_revision'],
                         active_batch=(s['active'] or {}).get('id'), session_id=(s['session'] or {}).get('id'))
                    for s in tx.list('observers')]
