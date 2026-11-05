#!/usr/bin/env python3
"""Local Mac/Linux deployment; credentials stay private and out of command lines."""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from controlplane.config import load, local_path
from controlplane.processes import atomic, birth, host_lock


def read(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def live(record):
    return bool(record.get('birth')) and birth(record.get('pid')) == record['birth']


def identity(process):
    return dict(pid=process.pid, birth=birth(process.pid))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'run', 'stop', 'status'])
    parser.add_argument('--config', default=str(ROOT / 'config/settings.json'))
    args = parser.parse_args()
    if os.name == 'nt':
        raise SystemExit('Use scripts/run-windows.ps1 on Windows.')
    config = load(args.config)
    if config.get('listen', '127.0.0.1') != '127.0.0.1':
        raise SystemExit('The local launcher requires listen=127.0.0.1.')
    root = local_path(config, '.state/local-services')
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    status_path = root / 'status.json'
    stop_path = root / 'stop'
    current = read(status_path)
    if args.action == 'status':
        print(json.dumps(dict(running=live(current), state=current.get('state', 'stopped'),
            components={name:live(value) for name,value in current.get('children', {}).items()},
            url='http://127.0.0.1:'+str(config.get('port', 8787))), ensure_ascii=False))
        return
    if args.action == 'stop':
        if not live(current):
            print('Local services are already stopped.'); return
        stop_path.touch(mode=0o600)
        for _ in range(100):
            if not live(current):
                print('Local services stopped; durable state retained.'); return
            time.sleep(.2)
        raise SystemExit('Stop is pending; inspect status before starting another instance.')
    if args.action == 'start':
        if live(current):
            print('Local services already running.'); return
        with (root / 'supervisor.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), 'run',
                '--config', str(Path(args.config).resolve())], cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, start_new_session=True)
        for _ in range(120):
            status = read(status_path)
            if status.get('pid') == process.pid and status.get('state') == 'running':
                print('Local API, Router and Host started.'); return
            if process.poll() is not None:
                raise SystemExit('Local startup failed; inspect .state/local-services/status.json.')
            time.sleep(.25)
        raise SystemExit('Startup is still pending; use status before retrying.')

    with host_lock(root / 'supervisor.lock'):
        stop_path.unlink(missing_ok=True)
        status = dict(pid=os.getpid(), birth=birth(os.getpid()), state='starting', children={})
        atomic(status_path, status)
        # Only local control-plane tokens are stored here, never DSH/Feishu credentials.
        token_path = root / 'tokens.json'
        saved = read(token_path)
        env = dict(os.environ, PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH', ''))
        for key in [config['operator_token_env'], *[v['token_env'] for v in config['hosts'].values()]]:
            if not env.get(key):
                env[key] = saved.setdefault(key, secrets.token_urlsafe(32))
        atomic(token_path, saved)
        children = {}
        stopping = False
        def stop(*_):
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        def launch(name, command):
            with (root / (name+'.log')).open('ab') as log:
                child = subprocess.Popen([sys.executable, '-B', '-m', 'controlplane', command,
                    '--config', str(Path(args.config).resolve())], cwd=ROOT, env=env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            children[name] = child
            status['children'][name] = identity(child)
            atomic(status_path, status)
        try:
            launch('api', 'serve')
            url = 'http://127.0.0.1:'+str(config.get('port', 8787))+'/api/state'
            request = urllib.request.Request(url, headers={'Authorization':'Bearer '+env[config['operator_token_env']]})
            for _ in range(80):
                if stopping or stop_path.exists():
                    raise RuntimeError('Startup interrupted')
                if children['api'].poll() is not None:
                    raise RuntimeError('API exited during startup')
                try:
                    with urllib.request.urlopen(request, timeout=1) as response:
                        response.read()
                    break
                except OSError:
                    time.sleep(.25)
            else:
                raise RuntimeError('API readiness timed out')
            launch('router', 'router')
            launch('host', 'host')
            status['state'] = 'running'; atomic(status_path, status)
            while not stopping and not stop_path.exists():
                for name, child in children.items():
                    if child.poll() is not None:
                        raise RuntimeError(name+' exited; durable state retained')
                time.sleep(.5)
        except Exception as error:
            status.update(state='failed', error=type(error).__name__)
            raise
        finally:
            # Each component owns its process group. Worker attempts have their own
            # persisted process identities and remain the Host recovery protocol's responsibility.
            for child in reversed(list(children.values())):
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
            for child in children.values():
                try: child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL); child.wait()
            if status['state'] != 'failed': status['state'] = 'stopped'
            atomic(status_path, status)


if __name__ == '__main__':
    main()
