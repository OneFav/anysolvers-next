"""Cross-platform control-plane entry points. No service is installed implicitly."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import threading
from .config import load,local_path
from .store import Store
from .engine import Engine
from .server import serve
from .feishu import Feishu
from .host import Host,patch,native_id
from .processes import atomic
from .versions import content_digest,seal


def main():
    parser=argparse.ArgumentParser(prog='python -m controlplane')
    parser.add_argument('command',choices=['serve','host','router','digest','publish','doctor'])
    parser.add_argument('--config',default='config/settings.json')
    parser.add_argument('--source',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--version')
    parser.add_argument('--validation')
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    if args.command=='digest':print(content_digest(args.source));return
    cfg=load(args.config)
    if args.command=='doctor':
        report=dict(config=str(Path(args.config).resolve()),platform=sys.platform,
            feishu_enabled=cfg.get('feishu',{}).get('enabled',False),
            operator_token_set=bool(os.environ.get(cfg['operator_token_env'])),
            hosts={k:dict(token_set=bool(os.environ.get(v['token_env']))) for k,v in cfg.get('hosts',{}).items()},
            worker_versions={k:str(local_path(cfg,v)) for k,v in cfg['worker_host'].get('versions',{}).items()},
            live_feishu_tested=False,live_model_tested=False)
        print(json.dumps(report,ensure_ascii=False,indent=2));return
    if args.command=='host':Host(cfg).serve();return
    if args.command=='router':
        options=cfg['router_agent'];state=local_path(cfg,options.get('state_root','.state/router-agent'));state.mkdir(parents=True,exist_ok=True)
        source=Path(args.source).resolve();manifest=state/'router.json'
        atomic(manifest,dict(role='router',router_url=options['router_url'],token_env=cfg['operator_token_env'],
            observer_enabled=bool(cfg.get('observer',{}).get('enabled')),
            native_id=native_id('router'),workspace=str(state),local_state=str(state/'session'),
            provider=options['provider'],model=options['model'],reasoning_effort=options.get('reasoning_effort','high'),verify_only=args.verify_only))
        overlay=state/'router.patch.json';atomic(overlay,patch(source,manifest,options,router=True))
        env=dict(os.environ,DSH_HOME=str(state/'dsh-home'))
        command=options['dsh_command']
        if os.name=='nt' and (shutil.which(command[0]) or command[0]).lower().endswith(('.cmd','.bat')):raise ValueError('Use node + DSH entry.js')
        raise SystemExit(subprocess.call(command+['--patch',str(overlay)],cwd=state,env=env))
    engine=Engine(Store(local_path(cfg,cfg['state_db'])),cfg)
    if args.command=='publish':
        if not args.version or not args.validation:raise ValueError('Supply --version and the validation evidence file')
        proof=json.loads(Path(args.validation).read_text())
        if proof.get('source_digest')!=content_digest(args.source):raise ValueError('Validation evidence must match current source content')
        record,path=seal(args.source,local_path(cfg,'versions'),args.version,proof)
        engine.register_version(record)
        print(json.dumps(dict(version=record,path=str(path)),ensure_ascii=False,indent=2));return
    stop=threading.Event()
    if cfg.get('feishu',{}).get('enabled'):
        threading.Thread(target=Feishu(engine,cfg['feishu']).serve,args=(stop,),daemon=True).start()
    print(f"Router API: http://{cfg.get('listen','127.0.0.1')}:{cfg.get('port',8787)}",flush=True)
    try:serve(engine,cfg,Path(__file__).parent/'web',stop)
    except KeyboardInterrupt:stop.set()


if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError) as exc:raise SystemExit(str(exc))
