#!/usr/bin/env python3
"""Agent-operated, repeatable installation. No global installs or implicit research."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from controlplane.processes import atomic
from controlplane.config import load, local_path

DSH_VERSION = '0.2.1-rc.5'

def python_at(directory):
    return directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

def npm_command():
    npm = shutil.which('npm')
    if not npm: raise ValueError('Install Node >=22.19 with npm first')
    if os.name == 'nt':
        cli = Path(npm).parent / 'node_modules/npm/bin/npm-cli.js'
        if not cli.is_file(): raise ValueError('Use the official Node distribution with npm-cli.js')
        return [shutil.which('node'), str(cli)]
    return [npm]

def run(command, cwd=ROOT, env=None):
    subprocess.run([str(v) for v in command], cwd=cwd, env=env, check=True)

def init(instance, args):
    path = instance / 'config/settings.json'
    if path.exists():
        raise ValueError('Instance already configured; inspect/edit its configuration instead of overwriting it')
    cfg = json.loads((ROOT / 'config/settings.example.json').read_text())
    mode = args.mode
    url = args.router_url or 'http://127.0.0.1:' + str(args.port)
    if mode == 'host' and not args.router_url: raise ValueError('Host mode requires --router-url')
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost','127.0.0.1','::1')):
        raise ValueError('Remote Router requires HTTPS (or a local SSH tunnel)')
    if parsed.username or parsed.password or parsed.query or parsed.fragment: raise ValueError('Router URL must not contain credentials, query or fragment')
    cfg.update(port=args.port, deployment=dict(mode=mode, source=str(ROOT), instance=str(instance)), default_version='anysolvers-next-'+json.loads((ROOT/'package.json').read_text())['version'])
    cfg['worker_host']['work_root'] = str(Path(args.projects).expanduser().resolve()) if args.projects else 'projects'
    cfg['worker_host']['python'] = str(python_at(instance / '.venv'))
    node = shutil.which('node')
    if not node: raise ValueError('Node is required')
    dsh = instance / '.state/tools/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js'
    for name in ('router_agent','worker_host'):
        cfg[name].update(router_url=url, provider=args.provider, model=args.model,
            dsh_command=[node, str(dsh), '--profile', 'headless'])
        if args.dsh_credentials_home:
            cfg[name]['dsh_credentials_home'] = str(Path(args.dsh_credentials_home).expanduser().resolve())
    cfg['feishu']['chats'] = []
    atomic(path, cfg)
    return dict(config=str(path), mode=mode, next='install', research_started=False)

def install(instance, config):
    if sys.version_info < (3,10): raise ValueError('Python >=3.10 required (3.12 recommended)')
    version = subprocess.check_output(['node','--version'], text=True).strip().lstrip('v').split('.')
    if tuple(map(int,version[:2])) < (22,19): raise ValueError('Node >=22.19 required')
    npm = npm_command()
    run([*npm,'ci','--ignore-scripts','--no-audit','--no-fund'])
    run([*npm,'run','build'])
    venv.EnvBuilder(with_pip=True).create(instance / '.venv')
    py = python_at(instance / '.venv')
    run([py,'-m','pip','install','--disable-pip-version-check','-r',ROOT/'runtime/requirements.txt'])
    tools = instance / '.state/tools/dsh'
    tools.mkdir(parents=True, exist_ok=True)
    run([*npm,'install','--prefix',tools,'--no-audit','--no-fund','--save-exact','@deepseek-ai/dsh@'+DSH_VERSION])
    run([py,'-B',ROOT/'scripts/feishu-setup.py','prepare','--config',config])
    return inspect(config)

def inspect(config):
    cfg = load(config)
    checks = dict(build=(ROOT/'lib/plugins/next-runner.js').is_file(),
        python=Path(cfg['worker_host']['python']).is_file(),
        dsh=Path(cfg['router_agent']['dsh_command'][1]).is_file(),
        feishu_cli=Path(cfg['feishu']['cli_command'][0]).is_file())
    versions = cfg['worker_host']['versions']
    checks['sealed_version'] = cfg['default_version'] in versions and local_path(cfg, versions.get(cfg['default_version'],'.')) .is_dir()
    return dict(config=str(config), mode=cfg.get('deployment',{}).get('mode','standalone'),
        ready=all(checks.values()), checks=checks, feishu_enabled=cfg['feishu']['enabled'],
        configured_chats=len(cfg['feishu']['chats']), credentials_inspected=False,
        actual_model_tested=False, actual_evaluation_tested=False)

def seal(config):
    from controlplane.versions import content_digest, seal as freeze
    from controlplane.engine import Engine
    from controlplane.store import Store
    cfg = load(config)
    from controlplane.deployment import service
    if service('status',config)['running']:raise ValueError('Stop services before sealing a new deployment')
    from controlplane.engine import ACTIVE
    store=Store(local_path(cfg,cfg['state_db']))
    if any(j['state'] in ACTIVE for j in store.list('jobs')):raise ValueError('Settle active tasks before changing the default version')
    cfg['default_version']='anysolvers-next-'+json.loads((ROOT/'package.json').read_text())['version']
    py = cfg['worker_host']['python']
    env = dict(os.environ, PATH=str(Path(py).parent)+os.pathsep+os.environ.get('PATH',''))
    commands = [[*npm_command(),'test'],[*npm_command(),'run','test:runtime'],
        [py,'-B','-m','unittest','discover','-s','tests/controlplane','-v']]
    evidence = []
    before = content_digest(ROOT)
    cfg['default_version'] += '-'+before[:12]
    for command in commands:
        run(command, env=env)
        evidence.append(dict(command=command, passed=True, kind='automated-fixtures-and-process-tests'))
    if before != content_digest(ROOT): raise ValueError('Source changed during validation; retry on a stable version')
    proof = dict(passed=True, source_digest=before, checks=evidence, live_model=False, live_feishu=False, actual_sandbox_evaluation=False)
    record, target = freeze(ROOT, local_path(cfg,'versions'), cfg['default_version'], proof)
    # Dependencies are installed within the sealed release, not borrowed from a developer checkout.
    run([*npm_command(),'ci','--ignore-scripts','--no-audit','--no-fund'], cwd=target)
    if content_digest(target) != before: raise ValueError('Sealed files changed during dependency installation')
    Engine(Store(local_path(cfg,cfg['state_db'])),cfg).register_version(record)
    raw=json.loads(Path(config).read_text());raw['default_version']=cfg['default_version'];raw.setdefault('deployment',{})['source']=str(ROOT);raw['worker_host']['versions'][cfg['default_version']]=str(target)
    atomic(config,raw);atomic(local_path(cfg,'.state/deployment-validation.json'),proof)
    return dict(version=record['id'], digest=before, path=str(target), actual_research=False)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['init','install','check','seal','start','stop','status'])
    p.add_argument('--instance',required=True,help='Persistent instance directory, outside the program source directory')
    p.add_argument('--mode',choices=['standalone','control','host'],default='standalone')
    p.add_argument('--port',type=int,default=8787);p.add_argument('--router-url');p.add_argument('--projects')
    p.add_argument('--provider',default='deepseek-official');p.add_argument('--model',default='deepseek-flash')
    p.add_argument('--dsh-credentials-home')
    args=p.parse_args();instance=Path(args.instance).expanduser().resolve()
    if instance==ROOT or ROOT in instance.parents: raise ValueError('Keep instance state outside the program source directory')
    if not 1<=args.port<=65535: raise ValueError('Invalid port')
    config=instance/'config/settings.json'
    if args.action=='init': value=init(instance,args)
    elif args.action=='install':
        if not config.exists(): raise ValueError('Run init first')
        value=install(instance,config)
    elif args.action=='check':value=inspect(config)
    elif args.action=='seal':value=seal(config)
    else:
        from controlplane.deployment import service
        value=service(args.action,config)
    print(json.dumps(value,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc) if not isinstance(exc,subprocess.CalledProcessError) else 'Deployment command failed; preceding installer output contains the diagnostic')
