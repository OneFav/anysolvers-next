"""SSH whole-episode transport. The sealed backend determines remote execution.

The local evaluator remains the accounting owner. An ambiguous transport result
is an infrastructure failure, never permission to execute the case again.
"""
import fcntl,hashlib,importlib.util,json,os,re,shlex,subprocess,sys,time
from pathlib import Path,PurePosixPath
from types import SimpleNamespace

REMOTE_IMAGE='sha256:541096ae9b05d3b850341b78790e2ed31e94d5aa0ea086933c6ff0c09ffa2e63'
INPUT_LIMIT=16*1024*1024
OUTPUT_LIMIT=128*1024*1024

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def settings(compute):
    remote=compute.get('remote',{})
    if compute.get('executor')=='ssh-process':
        if compute.get('image')!='ssh-process:v1' or compute.get('process_authorization')!='explicit-user-development-only':
            raise ValueError('SSH process backend requires explicit user authorization and its fixed runtime')
    elif compute.get('executor')!='ssh-docker' or compute.get('image')!=REMOTE_IMAGE:
        raise ValueError('SSH episode executor requires its reviewed pinned image')
    if set(remote)!={'host','root','python'} or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}',remote['host']):
        raise ValueError('Remote host must be an explicit SSH config alias')
    for key in ('root','python'):
        path=remote[key]
        if (not isinstance(path,str) or not re.fullmatch(r'[A-Za-z0-9_./ ~-]+',path)
                or not path.strip() or path.startswith('-') or '..' in PurePosixPath(path).parts
                or '~' in path and not path.startswith('~/')):
            raise ValueError('Remote paths must be fixed paths, relative to the SSH home or absolute')
    return remote

def signature(benchmark,assets):
    parent=PurePosixPath(benchmark['evaluator_file']).parent
    return digest(dict(benchmark_id=benchmark['benchmark_id'],task_id=benchmark['task_id'],protocol=benchmark['protocol'],
        assets={key:assets[benchmark[key]]['sha'] for key in ('evaluator_file','baseline_file','tests_file')},
        support={os.path.relpath(path,start=parent):assets[path]['sha'] for path in benchmark['support_files']}))

def bundle(task):
    root=task.baseline_path
    for _ in PurePosixPath(task.benchmark['baseline_file']).parts:root=root.parent
    assets={}
    for name in [task.benchmark[k] for k in ('evaluator_file','baseline_file','tests_file')]+task.benchmark['support_files']:
        path=(root/name).resolve();path.relative_to(root.resolve())
        data=path.read_bytes()
        assets[name]=dict(sha=hashlib.sha256(data).hexdigest(),content=data.decode('utf-8'))
    if signature(task.benchmark,assets)!=task.signature:raise ValueError('Trusted environment changed before remote dispatch')
    return dict(benchmark=task.benchmark,assets=assets,signature=task.signature)

def shell_command(compute,operation):
    """SSH home is the stable base; no dependence on the login shell's cwd."""
    remote=settings(compute);root=remote['root']
    def home_path(value):
        if value.startswith('/'):
            return shlex.quote(value)
        return '"$HOME"/'+shlex.quote(value.removeprefix('~/'))
    python=remote['python']
    python_arg=shlex.quote(python) if '/' not in python else home_path(python)
    entry='runtime/modules/evaluation/queued_sandbox/remote.py'
    # Verify this entrypoint's bytes before starting it; SSH authenticates the host.
    expected=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    guard='test "$(sha256sum '+shlex.quote(entry)+' | cut -d " " -f1)" = '+shlex.quote(expected)
    return ('cd -- '+home_path(root)+' && '+guard+' && exec env PYTHONDONTWRITEBYTECODE=1 '
            'PYTHONPATH=runtime:packages/research-runtime/python '+python_arg+' '+shlex.join([entry,operation,'.']))

def call(compute,operation,payload,timeout):
    from research_runtime.docker import _read_bounded,InfrastructureError
    remote=settings(compute);shell=shell_command(compute,operation)
    raw=json.dumps(payload,allow_nan=False,separators=(',',':')).encode()
    if len(raw)>INPUT_LIMIT:raise ValueError('Remote request exceeds bounded input size')
    process=subprocess.Popen(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2',remote['host'],shell],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    streams,reason=_read_bounded(process,raw,time.monotonic()+timeout,OUTPUT_LIMIT)
    if reason or process.returncode:
        message='Remote read-only preflight failed; no candidate execution: ' if operation=='preflight' else 'Remote outcome unknown; inspect original receipt, do not replay: '
        raise InfrastructureError(message+str(reason or process.returncode)+' '+streams['stderr'][:600].decode(errors='replace'))
    reply=json.loads(streams['stdout'])
    if reply.get('ok') is not True:raise InfrastructureError(reply.get('error','Remote operation failed'))
    return reply['value']

def preflight(compute):
    value=call(compute,'preflight',dict(image=compute['image'],executor=compute['executor'],
        process_authorization=compute.get('process_authorization')),30)
    return dict(value,executor=compute['executor'],host=compute['remote']['host'],checks_are_read_only=True)

def execute(source,case,compute,task,request_id,deadline,*,trace_retention='summary'):
    from modules.evaluation.queued_sandbox.retention import validate_policy
    validate_policy(trace_retention)
    if task.protocol['kind']!='episode':raise ValueError('SSH executor currently supports episode only')
    payload=dict(request_id=request_id,source=source,case=case,image=compute['image'],limits=compute['limits'],environment=bundle(task),trace_retention=trace_retention)
    if compute['executor']=='ssh-process':
        payload.update(executor='ssh-process',process_authorization=compute['process_authorization'])
    expected=digest(payload)
    value=call(compute,'case',payload,max(.001,min(deadline-time.monotonic(),compute['limits']['wall_s']+70)))
    if value.get('request_sha')!=expected or value.get('request_id')!=request_id:
        raise ValueError('Remote receipt does not match immutable request')
    result=value['result']
    result['remote_execution']=dict(host=compute['remote']['host'],request_id=request_id,request_sha=expected,
        ledger=value['ledger'],backend=compute['executor'],trusted_environment_on_remote=True)
    return result

def atomic(path,value):
    data=json.dumps(value,allow_nan=False,separators=(',',':'))
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    temp.replace(path)
    descriptor=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)

def safe_path(root,name):
    p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or not p.parts:raise ValueError('Invalid asset path')
    path=root.joinpath(*p.parts)
    for parent in [root,*path.parents]:
        if parent==root.parent:break
        if parent.is_symlink():raise ValueError('Redirected remote assets')
    return path

def run_case(root,payload):
    from modules.evaluation.queued_sandbox import sandbox
    from modules.evaluation.queued_sandbox.receipts import read_result
    from modules.evaluation.queued_sandbox.retention import retain,validate_policy
    retention=validate_policy(payload.get('trace_retention','summary'))
    request_id=payload['request_id']
    if not re.fullmatch(r'[a-f0-9]{64}',request_id):raise ValueError('Invalid remote request ID')
    process_backend=payload.get('executor')=='ssh-process'
    if process_backend:
        if payload['image']!='ssh-process:v1' or payload.get('process_authorization')!='explicit-user-development-only':
            raise ValueError('Missing SSH process authorization')
    elif payload['image']!=REMOTE_IMAGE:raise ValueError('Unreviewed remote image')
    state=root.parent.parent/'execution-state';state.mkdir(exist_ok=True)
    directory=state/request_id;directory.mkdir(exist_ok=True)
    if directory.is_symlink():raise ValueError('Redirected receipt directory')
    with (directory/'lease').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        request_sha=digest(payload);receipt=directory/'request.json';result_file=directory/'result.json'
        if receipt.exists():
            if json.loads(receipt.read_text())['sha']!=request_sha:raise ValueError('Remote request ID collision')
            previous=read_result(directory)
            if previous is not None:return previous
            raise ValueError('Remote execution already attempted with unknown result; reconciliation required')
        env=payload['environment'];benchmark=env['benchmark'];assets=env['assets']
        if signature(benchmark,assets)!=env['signature']:raise ValueError('Environment signature mismatch')
        work=directory/'environment';work.mkdir()
        for name,item in assets.items():
            data=item['content'].encode()
            if hashlib.sha256(data).hexdigest()!=item['sha']:raise ValueError('Asset hash mismatch')
            path=safe_path(work,name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);path.chmod(0o444)
        path=safe_path(work,benchmark['evaluator_file'])
        spec=importlib.util.spec_from_file_location('trusted_environment',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.validate_input(payload['case'])
        protocol=benchmark['protocol']
        if protocol['kind']!='episode':raise ValueError('Expected episode protocol')
        # Durable intent precedes all candidate execution. Unknown intent never reruns.
        atomic(receipt,dict(sha=request_sha,at=time.time(),image=payload['image'],source_sha=hashlib.sha256(payload['source'].encode()).hexdigest()))
        queued=time.monotonic()
        with sandbox.evaluation_slot(state/'sandbox.lock',3) as slot:
            queue_wait=time.monotonic()-queued
            if process_backend:
                from modules.evaluation.queued_sandbox.process import execute_episode
                result=execute_episode(payload['source'],payload['case'],payload['limits'],module,
                    max_steps=protocol['max_steps'],action_wall_s=protocol['action_wall_s'],request_id=request_id,
                    process_intent=lambda value:atomic(directory/'process.json',dict(value,request_sha=request_sha)))
            else:
                result=sandbox.execute_episode(payload['source'],payload['case'],payload['image'],payload['limits'],module,state/'sandbox.lock',
                    max_steps=protocol['max_steps'],action_wall_s=protocol['action_wall_s'],evaluation_slot_token=slot,code_staging='volume',
                    container_intent=lambda name:atomic(directory/'container.json',dict(name=name,request_sha=request_sha)))
        result['queue_wait_s']+=queue_wait
        result=retain(result,retention)
        value=dict(request_id=request_id,request_sha=request_sha,ledger=str(directory),result=result)
        atomic(result_file,value);return value

def main():
    raw=sys.stdin.buffer.read(INPUT_LIMIT+1)
    if len(raw)>INPUT_LIMIT:raise ValueError('Oversized remote request')
    payload=json.loads(raw);operation=sys.argv[1];root=Path(sys.argv[2]).resolve()
    if operation=='preflight':
        if payload.get('executor')=='ssh-process':
            from modules.evaluation.queued_sandbox.process import preflight as inspect_process
            if payload['image']!='ssh-process:v1' or payload.get('process_authorization')!='explicit-user-development-only':
                raise ValueError('Missing SSH process authorization')
            value=inspect_process()
        else:
            from research_runtime.docker import preflight as inspect_docker
            if payload['image']!=REMOTE_IMAGE:raise ValueError('Unreviewed remote image')
            value=inspect_docker(payload['image'])
    elif operation=='case':value=run_case(root,payload)
    else:raise ValueError('Unsupported remote operation')
    print(json.dumps(dict(ok=True,value=value),allow_nan=False,separators=(',',':')))

if __name__=='__main__':
    try:main()
    except Exception as exc:print(json.dumps(dict(ok=False,error=str(exc)[:1500])))
