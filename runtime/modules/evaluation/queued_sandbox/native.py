"""DSH native macOS evaluator. All candidate execution uses enforcing argv.

DSH supplies write confinement; the broker adds read/network/fork denial.
CPU uses RLIMIT_CPU; RSS is sampled (not a Docker hard memory ceiling).
"""
import hashlib, json, math, os, signal, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path
from research_runtime.docker import InfrastructureError, _AdmissionClosed, _read_bounded
IMAGE = "dsh-native:0.2.1-rc.5"

def confine(argv, workspace):
    broker = os.environ.get('ANYSOLVERS_NATIVE_CONFINE')
    if not broker or not Path(broker).is_file():
        raise InfrastructureError('DSH native confinement broker is unavailable; no host fallback')
    request = {'argv': argv, 'workspace': str(workspace),
               'pythonRoots': [sys.prefix, sys.base_prefix]}
    try:
        p = subprocess.run([os.environ.get('ANYSOLVERS_NODE', 'node'), broker], input=json.dumps(request),
                           capture_output=True, text=True, timeout=15)
        reply = json.loads(p.stdout)
        if p.returncode or reply.get('ok') is not True:
            raise InfrastructureError(reply.get('error', 'DSH native confinement failed'))
        if reply.get('enforcement') != 'full':
            raise InfrastructureError('Full native file confinement is required')
        return reply
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise InfrastructureError('DSH native confinement failed: ' + str(exc)[:500]) from exc

def preflight():
    with tempfile.TemporaryDirectory(prefix='as-native-preflight-') as folder:
        wrapped = confine([sys.executable, '-I', '-B', '-c', 'print("native-ready")'], folder)
        try:
            p = subprocess.run(wrapped['argv'], capture_output=True, text=True, timeout=10,
                               cwd=folder, env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'})
        except (OSError, subprocess.SubprocessError) as exc:
            raise InfrastructureError('DSH native sandbox runner unavailable') from exc
        if p.returncode or p.stdout.strip() != 'native-ready':
            raise InfrastructureError('DSH native sandbox preflight failed: ' + p.stderr[:1000])
        return {k: v for k, v in wrapped.items() if k not in ('argv','runnerFailureRules','denialSignatures')} | {
            'executor': 'dsh-native', 'runtime': IMAGE, 'python_version': sys.version.split()[0],
            'python_executable_sha256': hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            'single_process': True, 'hard_memory_limit': False, 'hard_cpu_affinity': False}

def execute(source, case, limits, scorer, payload, deadline=None):
    from .sandbox import _BRIDGE, _trusted_call, _reject_constant, _finite_json, _EpisodeFailure
    result = dict(valid=False, score=None, solution=None, metrics={}, wall_s=0.0,
                  infrastructure_error=False, cleanup_confirmed=False)
    started=time.monotonic(); process=None; streams={}; stop=threading.Event(); watcher=None; watch_reason=[]; folder=None
    try:
        if deadline is not None and started >= deadline: raise _AdmissionClosed('run_wall_budget')
        # Run the trusted preflight before loading any candidate code.
        result['execution_boundary']=preflight()
        folder=tempfile.mkdtemp(prefix='as-native-case-')
        root=Path(folder); code=root/'solver.py'; wrapper=root/'entrypoint.py'
        code.write_text(source,encoding='utf-8')
        prologue = "import resource\nresource.setrlimit(resource.RLIMIT_CPU, (%d,%d))\nresource.setrlimit(resource.RLIMIT_CORE,(0,0))\nresource.setrlimit(resource.RLIMIT_NOFILE,(64,64))\nresource.setrlimit(resource.RLIMIT_FSIZE,(0,0))\n" % (limits['cpu_s'],limits['cpu_s'])
        wrapper.write_text(prologue + _BRIDGE.replace("'/solver.py'",repr(str(code))),encoding='utf-8')
        code.chmod(0o444); wrapper.chmod(0o444)
        wrapped=confine([sys.executable,'-I','-B',str(wrapper)],folder)
        if deadline is not None and time.monotonic() >= deadline: raise _AdmissionClosed('run_wall_budget')
        started=time.monotonic()
        process=subprocess.Popen(wrapped['argv'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 cwd=folder,start_new_session=True,env={'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8'})
        def watch():
            while not stop.wait(.1):
                if process.poll() is not None: return
                try:
                    p=subprocess.run(['/bin/ps','-o','rss=','-p',str(process.pid)],capture_output=True,text=True,timeout=2)
                    if p.returncode==0 and p.stdout.strip() and int(p.stdout.strip()) > limits['memory_mb']*1024:
                        watch_reason.append('memory_limit_sampled');os.killpg(process.pid,signal.SIGKILL);return
                except (OSError,ValueError,subprocess.SubprocessError):
                    if process.poll() is None:
                        watch_reason.append('memory_monitor_unavailable')
                        try: os.killpg(process.pid,signal.SIGKILL)
                        except ProcessLookupError: pass
                    return
        watcher=threading.Thread(target=watch,daemon=True); watcher.start()
        streams,reason=_read_bounded(process,payload,started+limits['wall_s'],limits['output_bytes'])
        stop.set();watcher.join(3)
        if watcher.is_alive(): raise InfrastructureError('Memory monitor did not terminate')
        result.update(exit_code=process.returncode,stdout_sha256=hashlib.sha256(streams['stdout']).hexdigest(),
                      isolation_verified=True)
        stderr=streams['stderr'].decode(errors='replace')
        runner_failed=any(signature.lower() in stderr.lower() for rule in wrapped['runnerFailureRules']
                          for signature in rule['fatalSignatures']) if process.returncode else False
        if runner_failed: raise InfrastructureError('DSH sandbox runner failed: '+stderr[:1000])
        if watch_reason and watch_reason[0]=='memory_monitor_unavailable': raise InfrastructureError(watch_reason[0])
        if reason or watch_reason: result['error']=reason or watch_reason[0]
        elif process.returncode: result['error']='candidate_runtime_error'
        else:
            try:
                output=json.loads(streams['stdout'],parse_constant=_reject_constant)
                if not isinstance(output,dict) or set(output)!={'solution'}: raise ValueError('Output must contain only solution')
                scored=_trusted_call(started+limits['wall_s'],scorer,output['solution'],case)
                score=scored['score'];metrics=scored.get('metrics',{})
                if scored.get('valid',True) is not True or type(score) not in (int,float) or not math.isfinite(score): raise ValueError('Invalid score')
                if not isinstance(metrics,dict): raise ValueError('Invalid metrics')
                json.dumps(metrics,allow_nan=False)
                if 'trace' in scored: _,result['trace']=_finite_json(scored['trace'])
                result.update(valid=True,score=score,solution=output['solution'],metrics=metrics)
            except (_EpisodeFailure,InfrastructureError): raise
            except Exception as exc: result['error']='invalid_output: '+str(exc)[:500]
        result['cleanup_confirmed']=True
    except _AdmissionClosed as exc: result.update(error=str(exc),admission_closed=True,cleanup_confirmed=True)
    except _EpisodeFailure as exc: result.update(error=str(exc),infrastructure_error=str(exc)=='environment_timeout')
    except (InfrastructureError,OSError,subprocess.SubprocessError) as exc: result.update(error=str(exc)[:1500],infrastructure_error=True)
    finally:
        stop.set()
        if process is not None:
            try:
                if process.poll() is None: os.killpg(process.pid,signal.SIGKILL)
                process.wait(timeout=5)
                # Fork is prohibited by Seatbelt, so reaping this process establishes cleanup.
                result['cleanup_confirmed']=True
            except (OSError,subprocess.SubprocessError) as exc: result.update(infrastructure_error=True,cleanup_confirmed=False,cleanup_error=str(exc))
        elif not result.get('cleanup_error'): result['cleanup_confirmed']=True
        if watcher: watcher.join(3)
        if folder is not None:
            try: shutil.rmtree(folder)
            except OSError as exc: result.update(infrastructure_error=True,cleanup_confirmed=False,cleanup_error=str(exc))
        result['wall_s']=time.monotonic()-started
    if not result['valid'] or result['infrastructure_error']:
        result['diagnostic']={k:v.decode(errors='replace') for k,v in streams.items()}
    return result
