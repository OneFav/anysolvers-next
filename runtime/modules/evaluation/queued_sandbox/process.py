"""Explicitly authorized SSH process backend. Resource control is not isolation.

The DSH agent stays local. This Linux process has no filesystem/network sandbox;
only the accounted evaluator may invoke it. Final datasets are never admitted.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time

RUNTIME = 'ssh-process:v1'


def preflight():
    if sys.platform != 'linux' or not Path('/proc/self/stat').is_file():
        raise ValueError('SSH process evaluator requires Linux /proc')
    if not all(hasattr(resource, key) for key in ('RLIMIT_AS', 'RLIMIT_CPU', 'RLIMIT_FSIZE')):
        raise ValueError('Required POSIX resource controls are unavailable')
    cpus = sorted(os.sched_getaffinity(0))
    if not cpus:
        raise ValueError('No CPU available to evaluator')
    return dict(executor='ssh-process', runtime=RUNTIME, python_version=platform.python_version(),
                python_executable_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                kernel=platform.release(), filesystem_isolation=False, network_isolation=False,
                dsh_remote_sandbox=False, memory_enforcement='per-process RLIMIT_AS (address space, not RSS)',
                cpu_enforcement='per-process RLIMIT_CPU', cpu_affinity='one allowed CPU',
                pids_limit_enforced=False, temporary_storage_limit_enforced=False,
                regular_file_output_limit='RLIMIT_FSIZE=0', cleanup_scope='process group',
                candidate_executed=False, checks_are_read_only=True)


def _prologue(limits, cpu):
    # Set controls before importing source. No environment credentials are inherited.
    return f'''import os, resource, signal, ctypes
resource.setrlimit(resource.RLIMIT_CPU, ({limits['cpu_s']}, {limits['cpu_s']}))
resource.setrlimit(resource.RLIMIT_AS, ({limits['memory_mb'] * 1048576}, {limits['memory_mb'] * 1048576}))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
os.sched_setaffinity(0, {{{cpu}}})
parent = os.getppid()
if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
    raise RuntimeError('Unable to set parent-death signal')
if os.getppid() != parent or parent == 1:
    raise RuntimeError('Evaluator disappeared before execution')
'''


def _cleanup_group(process):
    """Reap the leader and verify its group is gone; escaped groups are not isolated."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)
    until = time.monotonic() + 3
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= until:
            raise RuntimeError('Remote process group cleanup is unknown')
        time.sleep(.05)


def execute_episode(source, case, limits, environment, *, max_steps, action_wall_s,
                    process_intent, request_id):
    from .sandbox import (_EPISODE_BRIDGE, _EpisodeChannel, _EpisodeFailure,
                          _finite_json, _observation_json, _trusted_call, InfrastructureError,
                          DEFAULT_LIMITS)
    if set(limits) != set(DEFAULT_LIMITS) or any(type(v) is not int or v <= 0 for v in limits.values()):
        raise ValueError('Expected complete positive resource limits')
    if type(max_steps) is not int or max_steps <= 0 or not 0 < action_wall_s <= limits['wall_s']:
        raise ValueError('Invalid episode limits')
    result = dict(valid=False, score=None, solution=None, metrics={}, wall_s=0.0,
                  infrastructure_error=False, cleanup_confirmed=False, trace=[], steps=0,
                  isolation_verified=False, queue_wait_s=0.0)
    process = channel = folder = None
    started = time.monotonic()
    try:
        result['execution_boundary'] = preflight()
        folder = Path(tempfile.mkdtemp(prefix='anysolvers-process-'))
        code, wrapper = folder / 'solver.py', folder / 'entrypoint.py'
        code.write_text(source, encoding='utf-8')
        cpus = sorted(os.sched_getaffinity(0))
        cpu = cpus[int(request_id[:8], 16) % len(cpus)]
        wrapper.write_text(_prologue(limits, cpu) + _EPISODE_BRIDGE.replace("'/solver.py'", repr(str(code))), encoding='utf-8')
        code.chmod(0o444); wrapper.chmod(0o444)
        started = time.monotonic(); deadline = started + limits['wall_s']
        reset = _trusted_call(deadline, environment.reset, _finite_json(case)[1])
        if not isinstance(reset, (tuple, list)) or len(reset) != 2:
            raise InfrastructureError('Environment reset must return (state, observation)')
        state, observation = reset
        process = subprocess.Popen([sys.executable, '-I', '-B', str(wrapper)], cwd=folder,
                                   env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        stat = Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()
        process_intent(dict(pid=process.pid, pgid=process.pid, start_ticks=stat[19],
                            directory=str(folder), request_id=request_id, cpu=cpu))
        result['execution_boundary'].update(candidate_executed=True, selected_cpu=cpu)
        channel = _EpisodeChannel(process, limits['output_bytes'])
        for index in range(max_steps):
            action_deadline = min(deadline, time.monotonic() + action_wall_s)
            delivered, action = channel.request(observation, action_deadline,
                                                'wall_timeout' if action_deadline == deadline else 'action_timeout')
            result['solution'] = action
            try:
                transition = _trusted_call(deadline, environment.step, state, _finite_json(action)[1])
            except (ValueError, TypeError, KeyError) as exc:
                raise _EpisodeFailure('invalid_action: ' + str(exc)[:500]) from exc
            if not isinstance(transition, (tuple, list)) or len(transition) != 3 or type(transition[2]) is not bool:
                raise InfrastructureError('Environment step must return (state, observation, bool done)')
            state, observation, done = transition
            raw, next_observation = _observation_json(observation)
            if len(raw) + 1 > limits['output_bytes']:
                raise InfrastructureError('Observation exceeds configured message limit')
            result['trace'].append(dict(step=index, observation=delivered, action=action,
                                        next_observation=next_observation, done=done))
            result['steps'] += 1
            if done:
                break
        else:
            raise _EpisodeFailure('max_steps_exceeded')
        channel.finish(deadline)
        result['exit_code'] = process.returncode
        scored = _trusted_call(deadline, environment.score_episode, state,
                               _finite_json(result['trace'])[1], _finite_json(case)[1])
        score, metrics = scored['score'], scored.get('metrics', {})
        if scored.get('valid', True) is not True or type(score) not in (int, float) or not math.isfinite(score):
            raise InfrastructureError('Environment returned an invalid score')
        if not isinstance(metrics, dict):
            raise InfrastructureError('Invalid environment metrics')
        _finite_json(metrics)
        result.update(valid=True, score=score, metrics=metrics)
    except _EpisodeFailure as exc:
        result.update(error=str(exc), infrastructure_error=str(exc) == 'environment_timeout')
    except Exception as exc:
        result.update(error=str(exc)[:1500], infrastructure_error=True)
    finally:
        if channel:
            result['stdout_sha256'] = hashlib.sha256(channel.streams['stdout']).hexdigest()
            if not result['valid'] or result['infrastructure_error']:
                result['diagnostic'] = {k: bytes(v).decode(errors='replace') for k, v in channel.streams.items()}
        try:
            if process:
                _cleanup_group(process)
            if channel:
                channel.close()
            elif process:
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
            if folder:
                shutil.rmtree(folder)
            result['cleanup_confirmed'] = True
        except Exception as exc:
            result.update(infrastructure_error=True, cleanup_error=str(exc)[:1000], cleanup_confirmed=False)
        result['wall_s'] = time.monotonic() - started
    return result
