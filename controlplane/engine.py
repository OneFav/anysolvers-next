import hashlib
import hmac
import re
import secrets
import time
import uuid
from .store import encode
from .communication import checked_text, notification, subject, incident_text, STAGES as STAGE_NAMES

STAGES = ('initialize', 'research', 'terminal')
ACTIVE = ('queued', 'starting', 'running', 'waiting_input', 'waiting_provider', 'reconciliation', 'host_lost')


class Conflict(ValueError):
    pass


def identity(prefix):
    return prefix + '-' + uuid.uuid4().hex[:16]


def required(value, name, limit=8000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Invalid ' + name)
    return value


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Engine:
    def __init__(self, store, config, clock=time.time):
        self.store, self.config, self.clock = store, config, clock
        self.observer = None
        if config.get('observer',{}).get('enabled'):
            from .observer import Observer
            from .config import local_path
            root=local_path(config,config['observer'].get('state_root','.state/observer'))
            self.observer=Observer(root/'observer.sqlite3',config['observer'].get('batching'),clock)

    def _event(self, tx, kind, data, event_id=None):
        tx.emit(event_id or identity('event'), kind, self.clock(), data)

    def _notify(self, tx, job, key, text, mention=False, channel=None, *, purpose):
        channel = channel or job['channel']
        responsible=job['owner']
        if key.startswith(('incident:','recovered:')):
            chat=next((c for c in self.config.get('feishu',{}).get('chats',[]) if c['chat_id']==channel.get('chat_id')),{})
            responsible=chat.get('notify_user') or responsible
        oid = digest([job['id'], key, channel])[:32]
        if not tx.get('outbox', oid):
            tx.put('outbox', oid, dict(id=oid, job_id=job['id'], channel=channel, **notification(text, purpose, key),
                   mention=responsible if mention else None, state='delivered' if channel.get('chat_id')=='desktop-local' else 'pending', attempts=0,
                   due_at=self.clock(), created_at=self.clock()))
        return oid

    def _attention(self, tx, job, question, attention_id=None):
        """An issue and its wait event share one outward question, not authority.

        Changing the question is a new revision. Exact repeats retain the original
        delivery receipt even across attempts; a new occurrence needs a new ID.
        """
        checked_text(question)
        if attention_id is not None:
            required(attention_id, 'attention id', 128)
        key = digest([job['id'], attention_id or question, question])
        prior = tx.get('attention_requests', key)
        oid = prior['delivery_id'] if prior else None
        # Upgrade an unsent generic fault notice with the concrete action, but
        # never rewrite text whose delivery may already have been attempted.
        if not oid and attention_id and attention_id.startswith('incident:'):
            incident = tx.get('incidents', job['id']+':'+attention_id.removeprefix('incident:'))
            if incident and incident['state'] == 'open':
                ref = f"incident:{incident['id']}:{incident['episode']}"
                candidate_id = digest([job['id'], ref, job['channel']])[:32]
                candidate = tx.get('outbox', candidate_id)
                if candidate and candidate['state'] == 'pending' and not candidate['attempts'] and 'attempted_at' not in candidate:
                    candidate.update(notification(question, 'attention', 'attention:'+key))
                    tx.put('outbox', candidate_id, candidate)
                    oid = candidate_id
        if not oid:
            oid = self._notify(tx, job, 'attention:'+key, question, True, purpose='attention')
        tx.put('attention_requests', key, dict(id=key, job_id=job['id'], question=question,
            attention_id=attention_id, delivery_id=oid))
        job['attention_question'] = question
        return oid

    def _in_scope(self, job, event):
        policy=self.config.get('sources',{}).get(event['source'],{})
        return (job['owner']==event['actor'] or job['channel'].get('chat_id')==event['channel'].get('chat_id')
                or event['source']=='desktop-owner' and policy.get('local_owner') is True)

    def receive(self, trigger):
        for name in ('id', 'source', 'thread', 'actor', 'text'):
            required(trigger.get(name), name, 32768 if name == 'text' else 256)
        source = self.config.get('sources', {}).get(trigger['source'], {})
        channel = trigger.get('channel', {})
        allowed = channel.get('chat_id') in source.get('chats', []) and trigger['actor'] in source.get('members',{}).get(channel.get('chat_id'),source.get('allowed_users', []))
        bot = trigger.get('bot', False) or trigger['actor'] in source.get('bot_ids', [])
        with self.store.transaction() as tx:
            old = tx.get('triggers', trigger['id'])
            if old:
                return old
            record = dict(trigger, admitted=bool(allowed and not bot), received_at=self.clock(),
                          state='pending' if allowed and not bot else 'filtered')
            tx.put('triggers', record['id'], record)
            # Subscriptions convey context, never a grant, and bot echoes are discarded.
            if not bot and channel.get('chat_id') in source.get('chats', []):
                for sub in tx.list('subscriptions'):
                    if sub['thread'] == trigger['thread'] and sub['source'] == trigger['source']:
                        job = tx.get('jobs', sub['job_id'])
                        if job and job['state'] in ACTIVE:
                            self._input(tx, job, 'context:'+record['id'], dict(kind='context', trigger=record))
            self._event(tx, 'trigger.received', dict(id=record['id'], state=record['state']), record['id'])
            return record

    def routing_context(self, event_id):
        event = self.store.get('triggers', event_id)
        if not event or not event['admitted']:
            raise ValueError('Trigger is not admitted')
        jobs = [self.public(job) for job in self.store.list('jobs')
                if job['channel'].get('chat_id') == event['channel'].get('chat_id')
                or job['owner'] == event['actor']]
        recent = [t for t in self.store.list('triggers') if t['thread'] == event['thread'] and t['admitted']]
        return dict(trigger=event, jobs=jobs, thread_messages=sorted(recent, key=lambda t:t['received_at'])[-12:],
                    stages=list(STAGES), versions=[{k:v[k] for k in ('id','digest','state')} for v in self.store.list('versions')])

    def _input(self, tx, job, key, payload):
        iid = digest([job['id'], key])
        if not tx.get('inputs', iid):
            tx.put('inputs', iid, dict(id=iid, job_id=job['id'], payload=payload, state='pending', at=self.clock()))

    def decide(self, event_id, decision):
        action = decision.get('action')
        if action not in ('create', 'attach', 'status', 'clarify', 'reply', 'ignore', 'resume', 'pause'):
            raise ValueError('Unknown routing action')
        required(decision.get('reason'), 'routing reason', 2000)
        with self.store.transaction() as tx:
            event = tx.get('triggers', event_id)
            if not event or not event['admitted']:
                raise ValueError('No authority for this trigger')
            previous = tx.get('decisions', event_id)
            if previous:
                return previous
            job = None
            if action == 'create':
                job = self._create(tx, event, decision)
            elif action in ('attach', 'status', 'resume', 'pause'):
                job = tx.get('jobs', decision.get('job_id', ''))
                if not job or not (job['owner'] == event['actor'] or job['channel'].get('chat_id') == event['channel'].get('chat_id')):
                    raise ValueError('Task is outside the message scope')
                if action == 'attach':
                    if job['state'] == 'completed':
                        raise Conflict('Completed stages require a new work item')
                    context=tx.get('inputs',digest([job['id'],'context:'+event_id]))
                    if context and context['state']=='pending':
                        context['state']='read';tx.put('inputs',context['id'],context)
                    self._input(tx, job, event_id, dict(kind='followup', trigger=event))
                    tx.put('subscriptions', digest([job['id'], event['thread']]), dict(job_id=job['id'], source=event['source'], thread=event['thread']))
                    if job['state'] == 'waiting_input':
                        job['state'] = 'reconciliation' if job.get('restart_waiting_input') else 'running' if job.get('attempt_id') else 'queued'
                        tx.put('jobs', job['id'], job)
                elif action == 'status':
                    self._notify(tx, job, event_id, self.status_text(job), channel=event['channel'], purpose='answer')
                elif action == 'pause':
                    job['paused'] = True
                    tx.put('jobs', job['id'], job)
                    self._notify(tx, job, event_id, '已暂停新工作；正在执行的评测将按原限制结束。', purpose='answer')
                else:
                    job['resume_requested'] = True
                    job['paused'] = False
                    if job['state']=='waiting_input':
                        # A human reply to a waiting worker must reach its inbox,
                        # once. A persistent resume bit otherwise reawakens the
                        # same failed turn after every progress/ask update.
                        context=tx.get('inputs',digest([job['id'],'context:'+event_id]))
                        if context and context['state']=='pending':
                            context['state']='read';tx.put('inputs',context['id'],context)
                        self._input(tx,job,event_id,dict(kind='followup',trigger=event))
                        tx.put('subscriptions',digest([job['id'],event['thread']]),dict(job_id=job['id'],source=event['source'],thread=event['thread']))
                        if job.get('restart_waiting_input'):job['state']='reconciliation'
                        else:
                            job['state']='running' if job.get('attempt_id') else 'queued'
                            job['resume_requested']=False
                    elif job.get('restart_waiting_input'):job['state']='reconciliation'
                    tx.put('jobs', job['id'], job)
                    self._notify(tx, job, event_id, '已收到继续请求，核对执行状态后续接。', purpose='answer')
            elif action == 'clarify':
                question = required(decision.get('question'), 'question', 1000)
                temporary = dict(id='question:'+event_id, channel=event['channel'], owner=event['actor'])
                self._notify(tx, temporary, event_id, question, True, purpose='answer')
            elif action == 'reply':
                text = required(decision.get('text'), 'reply text', 4000)
                temporary = dict(id='reply:'+event_id, channel=event['channel'], owner=event['actor'])
                self._notify(tx, temporary, event_id, text, True, purpose='answer')
            result = dict(event_id=event_id, decision=decision, job_id=job['id'] if job else None, at=self.clock())
            tx.put('decisions', event_id, result)
            event['state'] = 'routed'
            tx.put('triggers', event_id, event)
            self._event(tx, 'routing.decided', result)
            return result

    def _create(self, tx, event, decision):
        stage = decision.get('stage', 'initialize')
        policy = self.config.get('sources', {}).get(event['source'], {})
        if stage not in STAGES or stage not in policy.get('allowed_stages', ['initialize']):
            raise ValueError('Stage is not authorized by this source')
        predecessor = None
        project_id = decision.get('project_id') or identity('project')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', project_id):
            raise ValueError('Invalid project identity')
        if stage in ('research', 'terminal'):
            expected = 'initialize' if stage == 'research' else 'research'
            predecessor = tx.get('jobs', decision.get('predecessor_id', ''))
            if not predecessor and not decision.get('predecessor_id'):
                candidates=[j for j in tx.list('jobs') if j['project_id']==project_id and j['stage']==expected and j['state']=='completed' and self._in_scope(j,event)]
                if len(candidates)==1:predecessor=candidates[0]
            if predecessor and not self._in_scope(predecessor,event):raise ValueError('Predecessor outside source scope')
            if not predecessor or predecessor['stage'] != expected or predecessor['state'] != 'completed' or predecessor['project_id'] != project_id:
                raise Conflict('The preceding stage has not delivered a validated handoff')
        delivery_only=decision.get('delivery_only',False)
        if type(delivery_only) is not bool or delivery_only and stage!='terminal':raise ValueError('Delivery-only mode requires terminal stage')
        if delivery_only:
            if not any(j['project_id']==project_id and j['stage']=='terminal' and j['state']=='completed' and (j.get('output') or {}).get('run_id')==(predecessor.get('output') or {}).get('run_id') for j in tx.list('jobs')):
                raise Conflict('Read-only delivery requires a completed terminal run')
            # Display tooling may update independently; the run still selects its sealed engine.
            version_id=self.config.get('default_version')
        elif event['source']=='desktop-owner' and stage=='research':version_id=self.config.get('default_version')
        else:version_id = predecessor['version_id'] if predecessor else decision.get('version_id', self.config.get('default_version'))
        version = tx.get('versions', version_id or '')
        if not version or version.get('state') != 'ready':
            raise Conflict('An explicitly registered, verified implementation is required')
        if any(j['project_id'] == project_id and j['stage'] == stage and j['state'] in ACTIVE for j in tx.list('jobs')):
            raise Conflict('Project stage already has active work')
        job = dict(id=identity('job'), project_id=project_id, stage=stage, delivery_only=delivery_only,
                   title=required(decision.get('title'), 'title', 200), scope=required(decision.get('scope'), 'scope'),
                   version_id=version_id, version_digest=tx.get('versions',version_id)['digest'], host_id=self.config.get('default_host', 'development'),
                   owner=event['actor'], channel=event['channel'], source=event['source'], source_event=event['id'],
                   state='queued', created_at=self.clock(), updated_at=self.clock(), attempt_id=None, generation=0,
                   predecessor_id=predecessor['id'] if predecessor else None,
                   handoff=predecessor.get('output') if predecessor else None,
                   output=None, progress={}, paused=False, resume_requested=False,
                   mandate=dict(source=event['source'], actor=event['actor'], stage=stage,
                                policy_hash=digest(policy), requested_text=event['text']))
        # A desktop launch keeps the project's existing IM contact, while its
        # mandate still records the authenticated local owner as the initiator.
        if event['source']=='desktop-owner' and predecessor:
            job['owner']=predecessor['owner'];job['channel']=dict(predecessor['channel'])
            source_event=tx.get('triggers',predecessor['source_event']) if predecessor.get('source_event') else None
            if source_event:
                tx.put('subscriptions',digest([job['id'],source_event['thread']]),dict(job_id=job['id'],source=source_event['source'],thread=source_event['thread']))
        tx.put('jobs', job['id'], job)
        self._input(tx, job, event['id'], dict(kind='request', trigger=event))
        tx.put('subscriptions', digest([job['id'], event['thread']]), dict(job_id=job['id'], source=event['source'], thread=event['thread']))
        self._notify(tx, job, 'accepted', f"已接下{subject(job)}的{STAGE_NAMES[stage]}工作，等待执行端接单。", purpose='acknowledgement')
        self._event(tx, 'job.created', self.public(job))
        return job

    def register_version(self, version):
        required(version.get('id'), 'version id', 128)
        if not re.fullmatch(r'[a-f0-9]{64}', version.get('digest', '')):
            raise ValueError('Version requires a content SHA256')
        if version.get('state') != 'ready' or not version.get('validation'):
            raise ValueError('Version requires validation evidence')
        with self.store.transaction() as tx:
            old = tx.get('versions', version['id'])
            if old and old != version:
                raise Conflict('Version registration is immutable')
            tx.put('versions', version['id'], version)

    def claim(self, host_id, job_id):
        with self.store.transaction() as tx:
            job = tx.get('jobs', job_id)
            if job and job['stage'] not in STAGES:raise Conflict('Framework worker capability is disabled')
            if job and job['host_id']==host_id and job['state']=='starting' and job.get('attempt_id'):
                attempt=tx.get('attempts',job['attempt_id'])
                if attempt['pid'] is None:return dict(job=job,attempt=attempt,version=tx.get('versions',job['version_id']))
            if not job or job['host_id'] != host_id or job['state'] != 'queued' or job['paused']:
                raise Conflict('Work is not available for this host')
            job['generation'] += 1
            attempt = dict(id=identity('attempt'), job_id=job_id, host_id=host_id, generation=job['generation'],
                           token=secrets.token_urlsafe(32), state='launching', pid=None, native_id=None,
                           created_at=self.clock(), updated_at=self.clock())
            job.update(state='starting', attempt_id=attempt['id'], updated_at=self.clock())
            tx.put('attempts', attempt['id'], attempt)
            tx.put('jobs', job_id, job)
            self._event(tx, 'worker.claimed', dict(job_id=job_id, attempt_id=attempt['id']))
            return dict(job=job, attempt=attempt, version=tx.get('versions', job['version_id']))

    def _authorize(self, tx, attempt_id, token, allow_wait=False):
        attempt = tx.get('attempts', attempt_id)
        if not attempt or not hmac.compare_digest(attempt['token'], token):
            raise ValueError('Invalid worker capability')
        job = tx.get('jobs', attempt['job_id'])
        if job['stage'] not in STAGES:raise Conflict('Framework worker capability is disabled')
        if job['attempt_id'] != attempt_id or job['generation'] != attempt['generation'] or job['state'] in ('completed', 'stopped'):
            raise Conflict('Worker attempt is fenced')
        if not allow_wait and (job['paused'] or job['state'] not in ('starting', 'running', 'waiting_input')):
            raise Conflict('Worker must reconcile before new actions')
        return attempt, job

    def worker_event(self, attempt_id, token, event):
        eid = required(event.get('id'), 'worker event id', 256)
        kind = event.get('type')
        with self.store.transaction() as tx:
            key = attempt_id + ':' + eid
            known=tx.get('attempts',attempt_id)
            if known and hmac.compare_digest(known['token'],token) and tx.get('worker_events',key):
                return self.public(tx.get('jobs',known['job_id']))
            attempt, job = self._authorize(tx, attempt_id, token, allow_wait=True)
            if tx.get('worker_events', key):
                return self.public(job)
            data = event.get('data', {})
            now = self.clock()
            if kind == 'started':
                if attempt['pid'] is not None and (attempt['pid'] != data.get('pid') or attempt['native_id'] != data.get('native_id')):
                    raise Conflict('An attempt cannot change process/native identity')
                if not isinstance(data.get('pid'), int) or data['pid'] <= 0:
                    raise ValueError('Actual PID required')
                attempt.update(pid=data['pid'], native_id=required(data.get('native_id'), 'native id'), state='running')
                job['state'] = 'running'
                from .link_recovery import restarted
                restarted(self,tx,job,attempt)
            elif kind == 'progress':
                job['progress'] = data
            elif kind == 'waiting_input':
                job.update(state='waiting_input',resume_requested=False)
                self._attention(tx, job, required(data.get('question'), 'question', 1000), data.get('attention_id'))
            elif kind == 'fault':
                fault = data.get('kind', 'execution_unknown')
                job['state'] = 'waiting_provider' if fault in ('network', 'provider_quota', 'provider_rate_limit', 'model_unavailable') else 'reconciliation'
                self._incident(tx, job, fault, required(data.get('message'), 'fault message', 2000))
            elif kind == 'recovered':
                if job['state']=='host_lost':raise Conflict('Host must verify the original live process before reconnecting')
                if not data.get('verified') or self._pending(tx, job['id']):
                    raise Conflict('Unresolved tool execution prevents recovery')
                job.update(state='running', resume_requested=False)
                self._resolve_incidents(tx, job)
            elif kind == 'completed':
                if self._pending(tx, job['id']) or not data.get('validated') or not isinstance(data.get('artifacts'), list):
                    raise Conflict('Stage requires trusted validation and settled operations')
                if not data['artifacts']:
                    raise Conflict('Stage completion requires artifact evidence')
                job.update(state='completed', output=data)
                attempt['state'] = 'completed'
                self._resolve_incidents(tx, job)
                message = f"{subject(job)}的{STAGE_NAMES[job['stage']]}已完成。"
                if job.get('delivery_only'):
                    message = f"{subject(job)}的图表已提交发送。"
                unsuccessful = job['stage'] == 'research' and data.get('outcome') == 'no_valid_candidate'
                if unsuccessful:
                    message = f"{subject(job)}未获得完整合法的候选成绩，无法进入最终评测；需要先处理失败原因。"
                # A desktop workflow already authorizes the following stage. Its
                # internal transition is not a second human delivery milestone.
                continuing = any(w.get('research_id') == job['id'] and w.get('authorized')
                                 and w.get('state') == 'research' for w in tx.list('desktop_workflows'))
                if unsuccessful or not continuing:
                    self._notify(tx, job, 'completed', message, unsuccessful, purpose='result')
            elif kind in ('issue', 'framework_request'):
                request = dict(id=key, job_id=job['id'], project_id=job['project_id'],
                               request=required(data.get('request'), 'framework request'), evidence=data.get('evidence', []),
                               state='report_only', at=now)
                # New workers separate their detailed defect record from the one
                # human question. Legacy workers get a bounded fallback, never a
                # forwarded diagnostic. No execution or maintenance is authorized.
                question = data.get('question') or f"{subject(job)}遇到执行问题，需要维护方检查后才能继续。"
                request['notification_id'] = self._attention(tx, job, question, data.get('attention_id'))
                tx.put('framework_requests', key, request)
            elif kind == 'evaluation':
                self._evaluation(tx, job, data)
            else:
                raise ValueError('Unknown worker event')
            job['updated_at'] = now
            attempt['updated_at'] = now
            tx.put('attempts', attempt_id, attempt)
            tx.put('jobs', job['id'], job)
            tx.put('worker_events', key, dict(id=key, type=kind, at=now))
            self._event(tx, 'worker.'+kind, dict(job_id=job['id'], attempt_id=attempt_id, data=data), key)
            return self.public(job)

    def _pending(self, tx, job_id):
        return [r for r in tx.list('operations') if r['job_id'] == job_id and r['state'] == 'started']

    def tool_begin(self, attempt_id, token, operation_id, name, arguments):
        with self.store.transaction() as tx:
            attempt, job = self._authorize(tx, attempt_id, token)
            key = job['id']+':'+required(operation_id, 'operation id', 256)
            old = tx.get('operations', key)
            fingerprint = digest([name, arguments])
            if old:
                if old['fingerprint'] != fingerprint:
                    raise Conflict('Same operation ID has different arguments')
                return old
            if self._pending(tx, job['id']):
                # Parallel researchers may proceed independently; an abandoned attempt may not.
                if any(r['attempt_id'] != attempt_id for r in self._pending(tx, job['id'])):
                    raise Conflict('Earlier attempt has unresolved execution')
            item = dict(id=key, job_id=job['id'], attempt_id=attempt_id, name=name, arguments=arguments,
                        fingerprint=fingerprint, state='started', at=self.clock(), result=None)
            tx.put('operations', key, item)
            return item

    def tool_finish(self, attempt_id, token, operation_id, result):
        with self.store.transaction() as tx:
            _, job = self._authorize(tx, attempt_id, token, allow_wait=True)
            key = job['id']+':'+operation_id
            item = tx.get('operations', key)
            if not item or item['attempt_id'] != attempt_id:
                raise Conflict('No operation owned by this attempt')
            if item['state'] == 'completed':
                if digest(item['result']) != digest(result):
                    raise Conflict('Completed result is immutable')
                return item
            item.update(state='completed', result=result, finished_at=self.clock())
            tx.put('operations', key, item)
            return item

    def worker_context(self, attempt_id, token, ack=None):
        with self.store.transaction() as tx:
            known=tx.get('attempts',attempt_id)
            if known and hmac.compare_digest(known['token'],token):
                done=tx.get('jobs',known['job_id'])
                if done['attempt_id']==attempt_id and done['state']=='completed':return dict(job=done,pending_operations=[],inputs=[])
            attempt, job = self._authorize(tx, attempt_id, token, allow_wait=True)
            attempt['last_contact_at']=self.clock()
            tx.put('attempts',attempt_id,attempt)
            if ack:
                item = tx.get('inputs', ack)
                if not item or item['job_id'] != job['id']:
                    raise ValueError('Input does not belong to worker')
                item['state'] = 'read'
                tx.put('inputs', ack, item)
            return dict(job=job, pending_operations=self._pending(tx, job['id']),
                        inputs=sorted([i for i in tx.list('inputs') if i['job_id'] == job['id'] and i['state'] == 'pending'], key=lambda i:i['at']))

    def worker_artifact(self,attempt_id,token,body):
        import base64,binascii
        with self.store.transaction() as tx:
            attempt,job=self._authorize(tx,attempt_id,token)
            if job['stage']!='terminal' or body.get('run_id')!=(job.get('handoff') or {}).get('run_id'):
                raise ValueError('Artifact delivery is limited to the terminal handoff')
            figure=body.get('figure_id','')
            if not isinstance(figure,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,95}',figure):raise ValueError('Invalid registered figure ID')
            if body.get('mime')!='image/png' or body.get('encoding')!='base64' or not isinstance(body.get('content'),str) or len(body['content'])>700000:raise ValueError('Invalid PNG delivery payload')
            try:data=base64.b64decode(body['content'],validate=True)
            except (ValueError,binascii.Error):raise ValueError('Invalid image encoding') from None
            sha=hashlib.sha256(data).hexdigest()
            if not 8<len(data)<=512*1024 or not data.startswith(b'\x89PNG\r\n\x1a\n') or body.get('sha256')!=sha:raise ValueError('PNG integrity verification failed')
            oid=digest([job['id'],'figure',body['run_id'],figure,sha])[:32]
            message=tx.get('outbox',oid)
            if not message:
                message=dict(id=oid,job_id=job['id'],channel=job['channel'],text='图表：'+figure,mention=None,state='pending',attempts=0,due_at=self.clock(),created_at=self.clock(),
                             media=dict(mime='image/png',encoding='base64',content=body['content'],sha256=sha,figure_id=figure,run_id=body['run_id']))
                tx.put('outbox',oid,message)
                self._event(tx,'artifact.delivery_queued',dict(job_id=job['id'],run_id=body['run_id'],figure_id=figure,sha256=sha,delivery_id=oid))
            return dict(delivery_id=oid,state=message['state'],figure_id=figure,run_id=body['run_id'],sha256=sha)

    def subscribe(self, attempt_id, token, thread):
        with self.store.transaction() as tx:
            _,job=self._authorize(tx,attempt_id,token)
            if not any(t['thread']==thread and t['source']==job['source'] and t['channel']['chat_id']==job['channel']['chat_id'] for t in tx.list('triggers')):
                raise ValueError('Subscription must reference an observed thread in the authorized chat')
            key=digest([job['id'],thread])
            tx.put('subscriptions',key,dict(job_id=job['id'],source=job['source'],thread=thread))
            return dict(subscribed=True,thread=thread,authority='context-only')

    def host_heartbeat(self, host_id, workers):
        with self.store.transaction() as tx:
            tx.put('hosts', host_id, dict(id=host_id, last_seen=self.clock(), workers=workers, state='online'))
            for record in workers:
                attempt = tx.get('attempts', record.get('attempt_id', ''))
                if not attempt or attempt['host_id'] != host_id:
                    continue
                job = tx.get('jobs', attempt['job_id'])
                if job['attempt_id'] != attempt['id'] or job['state'] in ('completed', 'stopped'):
                    continue
                if record.get('alive') is False:
                    from .link_recovery import previous_state
                    waiting=(job.get('restart_waiting_input') or previous_state(tx,job)=='waiting_input')
                    followup=any(i['job_id']==job['id'] and i['state']=='pending' and i['payload'].get('kind')=='followup' for i in tx.list('inputs'))
                    if waiting and not job.get('resume_requested') and not followup:
                        job['state']='waiting_input';job['restart_waiting_input']=True
                    else:job['state'] = 'reconciliation'
                    loss=tx.get('incidents',job['id']+':host_lost')
                    if loss and loss['state']=='open':
                        loss.update(state='resolved',resolved_at=self.clock(),resolution='host_returned_worker_exited');tx.put('incidents',loss['id'],loss)
                    job['connection_recovery']=dict(phase='waiting_input' if job['state']=='waiting_input' else 'waiting_restart',detail='执行端已连接，原进程已退出；保留原会话等待补充信息。' if job['state']=='waiting_input' else '执行端已连接，核对原会话和未决操作后自动续接。')
                    self._incident(tx, job, 'worker_exit', job['connection_recovery']['detail'])
                    tx.put('jobs', job['id'], job)
                elif record.get('alive') is True:
                    from .link_recovery import recover_live
                    recover_live(self,tx,job,attempt,record)
            return [j for j in tx.list('jobs') if j['host_id'] == host_id and j['state'] in ACTIVE]

    def recover_attempt(self, host_id, job_id, evidence):
        """Trusted host must prove the old process exited and durable state is usable.

        The host may reconcile a completed backend operation, but may never declare
        an uncertain side effect absent merely because its process disappeared.
        """
        with self.store.transaction() as tx:
            job = tx.get('jobs', job_id)
            if not job or job['host_id'] != host_id or job['state'] not in ('reconciliation','host_lost','waiting_provider'):
                raise Conflict('Task is not awaiting recovery on this host')
            attempt = tx.get('attempts', job['attempt_id'])
            host = tx.get('hosts', host_id)
            record = next((w for w in (host or {}).get('workers',[]) if w.get('attempt_id') == attempt['id']), None)
            if not record or record.get('alive') is not False or self.clock()-host['last_seen'] > self.config.get('host_timeout_s',45):
                raise Conflict('A recent host check must confirm the old process has exited')
            if evidence.get('attempt_id') != attempt['id'] or not evidence.get('native_session_persisted') or not evidence.get('implementation_matches'):
                raise Conflict('Recovery needs persisted native identity and the pinned implementation')
            for resolved in evidence.get('resolved_operations',[]):
                item = tx.get('operations', resolved.get('id',''))
                if not item or item['job_id'] != job_id or item['fingerprint'] != resolved.get('fingerprint'):
                    raise Conflict('Recovery evidence does not match the original operation')
                if item['state']=='completed':continue
                if not resolved.get('local_result_sha') and (item['name'] not in ('solver_evaluate','baseline_measure','final_measure') or not resolved.get('backend_result_sha')):
                    raise Conflict('Only verified, durable evaluation results can resolve an interrupted tool')
                item.update(state='completed',result=resolved['result'],reconciled_at=self.clock(),evidence_sha=resolved.get('local_result_sha') or resolved.get('backend_result_sha'))
                tx.put('operations',item['id'],item)
            if self._pending(tx,job_id):
                raise Conflict('Unresolved side effects require reconciliation before resume')
            attempt.update(state='interrupted',recovery_evidence=evidence)
            job.update(state='queued',resume_requested=False,recovery_from=attempt['id'])
            tx.put('attempts',attempt['id'],attempt);tx.put('jobs',job_id,job)
            self._event(tx,'worker.recovery_queued',dict(job_id=job_id,previous_attempt=attempt['id']))
            # This authenticated Host response needs the same handoff as claim/heartbeat.
            # Public UI projection omits it and would skip pinned-engine restoration.
            return dict(job)

    def dispatch_framework(self, request_id):
        raise Conflict('Autonomous harness modification and framework workers are disabled')

    def monitor(self):
        with self.store.transaction() as tx:
            from .desktop_workflows import advance
            advance(self,tx)
            for host in tx.list('hosts'):
                if self.clock() - host['last_seen'] <= self.config.get('host_timeout_s', 45):
                    continue
                host['state'] = 'offline'
                tx.put('hosts', host['id'], host)
                for job in tx.list('jobs'):
                    if job['host_id'] == host['id'] and job['state'] in ACTIVE and job.get('attempt_id'):
                        from .link_recovery import lost
                        lost(self,tx,job)

    def _incident(self, tx, job, kind, message):
        key = job['id']+':'+kind
        old = tx.get('incidents', key)
        if old and old['state'] == 'open':
            old.update(last_seen=self.clock(), count=old['count']+1)
            tx.put('incidents', key, old)
            return
        incident = dict(id=key, job_id=job['id'], kind=kind, message=message, state='open',
                        opened_at=self.clock(), last_seen=self.clock(), count=1, episode=(old or {}).get('episode', 0)+1)
        tx.put('incidents', key, incident)
        self._notify(tx, job, f"incident:{key}:{incident['episode']}",
                     incident_text(job, kind), True, purpose='incident')
        self._event(tx, 'incident.opened', incident)

    def _resolve_incidents(self, tx, job):
        for incident in tx.list('incidents'):
            if incident['job_id'] == job['id'] and incident['state'] == 'open' and incident['kind'] not in ('negative_results', 'stagnation'):
                incident.update(state='resolved', resolved_at=self.clock())
                tx.put('incidents', incident['id'], incident)
                if job['state'] != 'completed':
                    self._notify(tx, job, f"recovered:{incident['id']}:{incident['episode']}",
                                 f"{subject(job)}已完成恢复核对，可以继续原任务。", True, purpose='recovery')

    def _evaluation(self, tx, job, data):
        if job['stage'] != 'research' or data.get('scope') != 'development':
            raise ValueError('Research monitoring accepts development evidence only')
        eid = required(data.get('evaluation_id'), 'evaluation id', 256)
        key = job['id']+':'+eid
        if tx.get('evaluations', key):
            return
        tx.put('evaluations', key, dict(data, id=key, job_id=job['id'], at=self.clock()))
        # Compare only the same dataset/objective. Candidate scores are evidence, not instructions.
        rows = sorted([r for r in tx.list('evaluations') if r['job_id'] == job['id'] and r.get('dataset') == data.get('dataset')], key=lambda r:r['at'])
        count = self.config.get('monitoring', {}).get('consecutive_invalid', 3)
        if len(rows) >= count and all(not r.get('all_valid') for r in rows[-count:]):
            self._incident(tx, job, 'negative_results', f'同一开发集连续 {count} 次评测未全部合法。建议检查失败类型；这不是最终成绩。')
        window = self.config.get('monitoring', {}).get('stagnation_window', 8)
        valid = [r for r in rows if r.get('all_valid') and isinstance(r.get('score'), (int,float))]
        if len(valid) > window:
            earlier = valid[:-window]
            recent = valid[-window:]
            best = min if data.get('direction') == 'minimize' else max
            recent_best=best(r['score'] for r in recent); earlier_best=best(r['score'] for r in earlier)
            stalled=recent_best >= earlier_best if best is min else recent_best <= earlier_best
            if stalled:
                self._incident(tx, job, 'stagnation', f'最近 {window} 次完整合法的开发评测没有刷新历史最佳；这是停滞信号，不是无效性证明。')

    def status_text(self, job):
        states = {'queued':'等待执行端接单','starting':'正在启动','running':'正在运行',
                  'waiting_input':'等待你的回复','waiting_provider':'等待模型服务恢复',
                  'reconciliation':'正在核对执行状态','host_lost':'执行端失联，当前进度尚未确认',
                  'completed':'已完成','stopped':'已停止'}
        state = '已暂停新工作' if job.get('paused') else states.get(job['state'], '状态待核对')
        text = f"{subject(job)}的{STAGE_NAMES.get(job['stage'], '任务')}{state}。"
        if (job.get('output') or {}).get('outcome') == 'no_valid_candidate':
            return text + '未获得完整合法的候选成绩，无法进入最终评测。'
        if job['state'] == 'waiting_input' and not job.get('paused') and job.get('attention_question'):
            question = job['attention_question']
            try:
                return checked_text(text + question)
            except ValueError:
                return checked_text(question)
        if job.get('paused') or job['state'] not in ('running', 'starting'):
            return text
        operations = [o for o in self.store.list('operations') if o['job_id'] == job['id']]
        latest = max(operations, key=lambda o:o.get('finished_at',o['at']), default=None)
        if not latest:
            return text
        names = {'study_write':'准备初始化材料','study_init':'登记任务定义','study_deliver':'校验初始化材料',
                 'study_seal':'封存初始化','batch_init':'创建研究批次','baseline_measure':'评测开发集基线',
                 'solver_evaluate':'评测研究候选','solver_checkpoint':'保存研究记录','final_select':'确定最终候选',
                 'final_measure':'执行最终评测','final_deliver':'登记报告','visualization_render':'生成图表',
                 'visualization_deliver':'登记图表','next_send_figure':'提交图表发送'}
        name = names.get(latest['name'])
        if name:
            result = latest.get('result') or {}
            if isinstance(result, dict) and result.get('isError'):
                text += name + '遇到问题，正在等待处理。'
            elif latest['state'] == 'completed':
                text += name + '已返回。'
            else:
                text += '当前正在' + name + '。'
        return text

    @staticmethod
    def public(job):
        return {k:v for k,v in job.items() if k not in ('mandate', 'handoff')}

    def snapshot(self):
        return dict(jobs=[self.public(j) for j in self.store.list('jobs')], hosts=self.store.list('hosts'),
                    incidents=self.store.list('incidents'), versions=self.store.list('versions'),
                    source_health=self.store.list('source_health'),
                    framework_requests=self.store.list('framework_requests'),
                    outbox=[{k:({a:b for a,b in v.items() if a!='content'} if k=='media' else v) for k,v in o.items() if k!='channel'} for o in self.store.list('outbox')])

    def router_health(self, state, detail):
        if state not in ('ready', 'error'):raise ValueError('Invalid Router health state')
        required(detail,'Router health detail',500)
        with self.store.transaction() as tx:
            record=dict(id='router:model',component='router',state=state,detail=detail,at=self.clock())
            tx.put('source_health',record['id'],record)
        return record
