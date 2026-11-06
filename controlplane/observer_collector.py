"""Host-side read-only incremental collector, outside the worker tick/locks.

No event listener is added to worker hot paths. One reader, bounded frames/files,
stat-based change detection, fixed snapshots; failures retain their cursors.
"""
import hashlib
import copy
import json
import os
from pathlib import Path
import subprocess
import time

from .config import local_path
from .processes import atomic

TOOLS = {'study_selection_report_context', 'study_selection_report_submit', 'study_selection_context', 'study_selection_submit', 'batch_init', 'baseline_measure', 'study_module', 'study_status', 'study_config', 'study_evidence',
         'study_plan', 'study_audit', 'study_check', 'research_start', 'study_recover', 'session_begin', 'session_end',
         'solver_context', 'solver_read', 'solver_write', 'solver_evaluate', 'solver_result', 'solver_findings',
         'solver_share', 'solver_reuse', 'solver_checkpoint', 'solver_iteration', 'solver_list'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Collector:
    def __init__(self, config, post):
        self.config, self.post = config, post
        options = config['worker_host']
        self.root = local_path(config, options['state_root'])
        self.work = local_path(config, options['work_root'])
        self.state = self.root/'observer-collector.json'
        self.saved = json.loads(self.state.read_text()) if self.state.exists() else dict(streams={}, files={}, file_cursor={})
        self.reader = Path(__file__).resolve().parents[1]/'scripts/read-dsh-observations.mjs'
        self.metrics = dict(read_bytes=0, parsed_bytes=0, snapshots=0, worker_locks=0, worker_flushes=0)
        self.walkers={}

    def entries(self, roots):
        for root in roots:
            if root.is_symlink() or not root.exists():continue
            def walk(directory):
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.name.startswith('.') or entry.is_symlink():
                            yield None
                        elif entry.is_dir(follow_symlinks=False):
                            yield None
                            yield from walk(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False) and entry.name.endswith(('.json','.jsonl','.py','.md')):
                            yield Path(entry.path)
                        else:yield None
            yield from walk(root)

    def record(self, job, batch, session, position, kind, obj, version, content, at, **extra):
        return dict(job_id=job['id'], batch=batch, session=session, position=position, kind=kind,
                    object=obj, version=version, content=content, content_sha=sha(content.encode()),
                    content_chars=len(content), at=at, **extra)

    def stream(self, job, run, file):
        key=str(file)
        saved=copy.deepcopy(self.saved['streams'].get(key,dict(offset=0, calls={}, next_seq=0)))
        stat=file.stat()
        signature=[stat.st_size,stat.st_mtime_ns]
        if saved.get('stat')==signature:
            return
        result=subprocess.run(['node',str(self.reader)],input=json.dumps(dict(path=key,offset=saved['offset'],max_input=1048576,max_output=2097152)),
            text=True,capture_output=True,timeout=15,check=True)
        page=json.loads(result.stdout)
        if saved.get('identity') and saved['identity']!=page['source_identity']:
            raise ValueError('DSH source replaced; cursor retained')
        header=page.get('header') or saved.get('header')
        if not header:
            raise ValueError('DSH stream needs its format-4 header')
        if header.get('agentPreset') not in ('anysolvers-main','anysolvers-researcher'):
            return
        session=header['id']
        records=[]
        for event in page['records']:
            pos=event['seq']
            if pos!=saved['next_seq']:
                raise ValueError('DSH session sequence gap; source cursor retained')
            saved['next_seq']=pos+1
            kind=event['type'];data=event.get('data',{})
            content=None;call=None;obj=kind
            if kind=='tool/call':
                call=data['callId']
                if data.get('name') in TOOLS:
                    saved['calls'][call]=data['name']
                    content=json.dumps(data,ensure_ascii=False);obj=data['name'];kind='tool_input'
            elif kind=='tool/result':
                message=data.get('message',{})
                call=message.get('toolCallId')
                if call in saved['calls']:
                    obj=saved['calls'].pop(call)
                    content=json.dumps(data,ensure_ascii=False);kind='tool_result'
            elif kind=='assistant/message':
                # Explicitly emitted analysis/text only. Embedded request/stream/usage is omitted.
                parts=[dict(type=p['type'],text=p['text']) for p in data.get('message',{}).get('content',[])
                       if p.get('type') in ('text','reasoning') and p.get('text')]
                if parts:
                    content=json.dumps(dict(analysis=parts,interrupted=data.get('interrupted',False)),ensure_ascii=False)
                    kind='analysis'
            elif kind=='user/message':
                # Research instructions/context make surrounding session reads
                # intelligible; they remain untrusted material, never authority.
                content=json.dumps(data,ensure_ascii=False);kind='material';obj='session_instruction'
            elif kind in ('turn/end','agent/status'):
                content=json.dumps(data,ensure_ascii=False);kind='progress'
            if content:
                # Unicode slices are bounded in bytes even for four-byte codepoints.
                pieces=[content[i:i+65536] for i in range(0,len(content),65536)]
                for chunk,piece in enumerate(pieces):
                    records.append(self.record(job,run,session,pos,kind,obj+('#chunk:'+str(chunk) if len(pieces)>1 else ''),sha(piece.encode()),piece,event['time']/1000,
                        source=dict(path=key,frame_cutoff=page['next'],format=4,message_sha=sha(content.encode()),chunk=chunk,chunks=len(pieces)),
                        snapshot_truncated=len(pieces)>1,**({'call_id':call} if call else {})))
        # Upload pages with stable IDs. A failed upload/checkpoint replays deduplicated evidence.
        for start in range(0,len(records),1):
            self.post('observer-ingest',dict(records=records[start:start+1]))
        self.metrics['read_bytes']+=page['read_bytes'];self.metrics['parsed_bytes']+=page['parsed_bytes']
        saved.update(offset=page['next'],header=header,identity=page['source_identity'])
        if page['next']==page['size']:
            saved['stat']=signature
        self.saved['streams'][key]=saved

    def artifacts(self, job, run, workspace):
        # Development data/outputs only. Never traverse final, visualization, state.json,
        # run.yaml, credentials, arbitrary tools' requested file paths, or other projects.
        base=workspace/'runs'/run
        roots=[base/'research',base.parent/'initialization/task',base.parent/'initialization/ideas',base.parent/'initialization/datasets/environment']
        walker=self.walkers.setdefault(job['id'],self.entries(roots))
        # A bounded iterator advances directory entries instead of enumerating the tree.
        for _ in range(12):
            try:file=next(walker)
            except StopIteration:
                self.walkers.pop(job['id'],None);break
            self.metrics['metadata_entries']=self.metrics.get('metadata_entries',0)+1
            if file is None:continue
            if not file.resolve().is_relative_to(workspace.resolve()):raise ValueError('Redirected research material')
            stat=file.stat();key=str(file);stamp=[stat.st_ino,stat.st_size,stat.st_mtime_ns]
            relative=file.relative_to(workspace).as_posix()
            if '/initialization/datasets/environment/' in relative and file.suffix!='.py' and file.name!='benchmark.json':continue
            if self.saved['files'].get(key)==stamp:
                continue
            if stat.st_size>524288:
                # Details JSONL is read as fixed, independently versioned chunks.
                chunks=(stat.st_size+262143)//262144
            else:chunks=1
            kind='source' if file.suffix=='.py' else 'evaluation' if '/evaluations/' in relative else 'material'
            with file.open('rb') as stream:
                for chunk in range(chunks):
                    # At most two chunks/tick/file, durable offset lets large details finish.
                    chunk_key=key+':chunks'
                    if self.saved['file_cursor'].get(chunk_key+':stat')!=stamp:
                        self.saved['file_cursor'][chunk_key]=0
                        self.saved['file_cursor'][chunk_key+':stat']=stamp
                    begin=self.saved['file_cursor'].get(chunk_key,0)
                    stream.seek(begin*262144)
                    raw=stream.read(262144 if chunks>1 else 524288)
                    # Preserve exact raw bytes even if a chunk cuts a UTF-8 codepoint.
                    # Normal text remains directly readable; binary boundary chunks
                    # carry reversible base64 plus a labeled lossy preview.
                    try:content=raw.decode('utf-8');encoding='utf-8'
                    except UnicodeDecodeError:
                        import base64
                        content=json.dumps(dict(encoding='base64',raw=base64.b64encode(raw).decode(),preview=raw[:600].decode('utf-8',errors='replace')),ensure_ascii=False)
                        encoding='base64-envelope'
                    version=sha(content.encode())
                    record=self.record(job,run,'artifact',begin if chunks>1 else 0,kind,relative+('#chunk:'+str(begin) if chunks>1 else ''),version,
                        content,stat.st_mtime,source=dict(path=relative,stat=stamp,chunk=begin,chunks=chunks,raw_sha=sha(raw),encoding=encoding), snapshot_truncated=chunks>1)
                    if [file.stat().st_ino,file.stat().st_size,file.stat().st_mtime_ns]!=stamp:
                        break
                    self.post('observer-ingest',dict(records=[record]))
                    self.metrics['read_bytes']+=len(raw);self.metrics['snapshots']+=1
                    self.saved['file_cursor'][chunk_key]=begin+1
                    if begin+1>=chunks:
                        self.saved['file_cursor'].pop(chunk_key,None);self.saved['file_cursor'].pop(chunk_key+':stat',None);self.saved['files'][key]=stamp;break
                    if chunk>=1:break
            # A changing file is read again; no claim of a coherent snapshot across chunks.
            if [file.stat().st_ino,file.stat().st_size,file.stat().st_mtime_ns]!=stamp:
                self.saved['files'].pop(key,None)
                self.saved['file_cursor'].pop(key+':chunks',None)
                self.saved['file_cursor'].pop(key+':chunks:stat',None)

    def tick(self):
        jobs=self.post('observer-jobs',{})
        # Rotate job order so a busy stream cannot starve another research batch.
        cursor=self.saved.get('job_cursor',0)
        jobs=jobs[cursor:]+jobs[:cursor]
        for job in jobs[:4]:
            directory=self.root/job['id'];binding=directory/'session/run.json'
            if not binding.exists():
                continue
            run=json.loads(binding.read_text()).get('run_id')
            if not isinstance(run,str) or not __import__('re').fullmatch(r'[a-z][a-z0-9-]{0,63}/[0-9]{3,}',run):
                continue
            workspace=self.work/job['project_id']
            try:
                streams=sorted((directory/'dsh-sessions').glob('*/*/session.v4.jsonl.zstd'))
                sc=self.saved.get('stream_cursor',{}).get(job['id'],0)
                for file in (streams[sc:]+streams[:sc])[:2]:
                    if file.is_symlink() or not file.resolve().is_relative_to(directory.resolve()):
                        raise ValueError('Unsafe DSH stream path')
                    self.stream(job,run,file)
                self.saved.setdefault('stream_cursor',{})[job['id']]=(sc+2)%max(1,len(streams))
                self.artifacts(job,run,workspace)
            except (ValueError,OSError,subprocess.SubprocessError,UnicodeError):
                self.saved['error']=dict(job_id=job['id'],at=time.time(),detail='Source read failed; cursors retained, no worker action requested')
            atomic(self.state,dict(self.saved,metrics=self.metrics))
        self.saved['job_cursor']=(cursor+4)%max(1,len(jobs))

    def serve(self, stop):
        while not stop.is_set():
            try:self.tick()
            except (ValueError,OSError):pass
            stop.wait(3)
