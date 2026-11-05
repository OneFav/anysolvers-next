"""Durable request identity and execution facts. Resource policy is adapter-owned.
No extra budget is deducted here: AnySolvers retains its existing quota authority.
"""
import contextlib, fcntl, hashlib, json, os, re, time, uuid
from pathlib import Path

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def safe(path):
    path = Path(path).absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError('Redirected execution record')
    return path

def atomic(path, value):
    path = safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+uuid.uuid4().hex)
    with temporary.open('x') as stream:
        json.dump(value, stream, allow_nan=False, sort_keys=True)
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)

class JobLedger:
    def __init__(self, directory, scope, executors, *, path_base=None):
        self.path_base = safe(path_base) if path_base is not None else None
        self.directory = safe(directory)
        self.scope = (safe(scope).relative_to(self.path_base).as_posix() if self.path_base else str(Path(scope).resolve()))
        self.executors = dict(executors)
        self.directory.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def lock(self):
        with safe(self.directory/'ledger.lock').open('a+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(stream, fcntl.LOCK_UN)

    def execute(self, request_id, executor, request, assets, resources):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,159}',request_id):
            raise ValueError('Invalid execution request ID')
        # Same-ID callers wait for the original result. A process crash releases
        # the lease but leaves the nonterminal record, which is never replayed.
        with safe(self.directory/(request_id+'.lease')).open('a+') as lease:
            fcntl.flock(lease,fcntl.LOCK_EX)
            try:return self._execute(request_id,executor,request,assets,resources)
            finally:fcntl.flock(lease,fcntl.LOCK_UN)

    def _execute(self, request_id, executor, request, assets, resources):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,159}',request_id):
            raise ValueError('Invalid execution request ID')
        if executor not in self.executors:
            raise ValueError('Executor is not registered')
        portable_assets=[]
        for asset in assets:
            if self.path_base and (Path(asset['path']).is_absolute() or '..' in Path(asset['path']).parts):
                raise ValueError('Execution assets must be project-relative paths')
            path=safe((self.path_base/asset['path']) if self.path_base else asset['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=asset['sha256']:
                raise ValueError('Execution input asset changed')
            portable_assets.append(dict(asset,path=path.relative_to(self.path_base).as_posix()) if self.path_base else asset)
        assets=portable_assets
        identity=digest(dict(executor=executor,request=request,assets=assets,resources=resources,scope=self.scope))
        path=self.directory/(request_id+'.json')
        with self.lock():
            if path.exists():
                record=json.loads(path.read_text())
                if record['identity']!=identity: raise ValueError('Request ID reused with different execution inputs')
                if record['state']=='completed':
                    result=safe(self.directory/record['resultRef'])
                    if hashlib.sha256(result.read_bytes()).hexdigest()!=record['resultSha256']:raise ValueError('Execution result changed')
                    return json.loads(result.read_text())
                raise ValueError('Unresolved execution request; do not replay')
            for other in self.directory.glob('*.json'):
                if other.name.endswith('.result.json'):continue
                record=json.loads(safe(other).read_text())
                if record['state'] not in ('completed','failed'):
                    active=False
                    with safe(self.directory/(record['requestId']+'.lease')).open('a+') as lease:
                        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        except BlockingIOError:active=record['state']=='running'
                        else:fcntl.flock(lease,fcntl.LOCK_UN)
                    if not active:raise ValueError('Unresolved execution blocks new request IDs')
            record=dict(schemaVersion=2 if self.path_base else 1,requestId=request_id,executor=executor,identity=identity,
                        scope=self.scope,assets=assets,resources=resources,state='running',
                        startedAt=time.time(),finishedAt=None,resultRef=None,issue=None)
            atomic(path,record)
        try:
            result=self.executors[executor](request)
            # The adapter states whether its backend confirmed cleanup; opaque payload stays opaque.
            if not isinstance(result,ExecutionResult):raise ValueError('Executor did not return execution facts')
            result_path=self.directory/(request_id+'.result.json')
            atomic(result_path,result.value)
            record.update(state='completed' if result.cleanup_confirmed else 'reconciliation',issue=None if result.cleanup_confirmed else 'Execution cleanup is unknown',finishedAt=time.time(),resultRef=result_path.name,
                          resultSha256=hashlib.sha256(result_path.read_bytes()).hexdigest())
            with self.lock():atomic(path,record)
            return result.value
        except BaseException as error:
            record.update(state='reconciliation',issue=str(error)[:1000],finishedAt=time.time())
            with self.lock():atomic(path,record)
            raise

class ExecutionResult:
    def __init__(self,value,cleanup_confirmed):
        self.value,self.cleanup_confirmed=value,cleanup_confirmed
