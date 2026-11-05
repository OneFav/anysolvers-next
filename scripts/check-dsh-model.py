"""Check native DSH account reuse. No credential files are opened by this script."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from controlplane.config import load
from controlplane.host import patch
from controlplane.processes import atomic

p=argparse.ArgumentParser()
p.add_argument('--config',default=str(ROOT/'config/settings.json'))
p.add_argument('--inference',action='store_true',help='Send one fixed short test prompt, no tools or research')
args=p.parse_args();c=load(args.config);options=c['router_agent']
if not options.get('dsh_credentials_home'):raise SystemExit('Set router_agent.dsh_credentials_home first')
with tempfile.TemporaryDirectory(prefix='as-next-model-') as temp:
    root=Path(temp);output=root/'result.json'
    entries=patch(ROOT,root/'manifest.json',options,router=True)
    entries=[v for v in entries if 'insert' not in v]
    entries.append(dict(insert=[dict(id='next-model-probe',name=str(ROOT/'scripts/dsh-model-probe.mjs'),
        config=dict(provider=options['provider'],model=options['model'],inference=args.inference,output=str(output)))]))
    overlay=root/'patch.json';atomic(overlay,entries)
    try:
        result=subprocess.run(options['dsh_command']+['--patch',str(overlay)],cwd=root,
            env=dict(os.environ,DSH_HOME=str(root/'dsh-home')),capture_output=True,timeout=80)
        value=json.loads(output.read_text()) if output.exists() else dict(available=False,error_code='DSH_BOOT_FAILED',exit_code=result.returncode)
    except subprocess.TimeoutExpired:value=dict(available=False,error_code='PROBE_TIMEOUT')
    evidence=ROOT/'.state/dsh-model-check.json';atomic(evidence,value)
    print(json.dumps(value,ensure_ascii=False,indent=2))
    raise SystemExit(0 if value.get('available') and (not args.inference or value.get('inference_ok')) else 1)
