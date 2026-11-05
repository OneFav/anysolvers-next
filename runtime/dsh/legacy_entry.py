"""Compatibility for verified original repositories; no kernel substitution."""
import contextlib
import importlib
import json
from pathlib import Path
import sys
import types
engine_root=Path(__file__).resolve().parents[1]
project_root=Path(sys.argv[1]).resolve(strict=True)
sys.path.insert(0,str(project_root))
for name in ('tools','modules','dsh'):
    package=types.ModuleType(name)
    package.__path__=[str(engine_root/'legacy-adapters' if name=='dsh' else project_root/name)]
    sys.modules[name]=package
try:
    request=json.loads(sys.stdin.buffer.read());op=request['operation']
    from tools import common as c
    if c.ROOT.resolve()!=project_root:raise ValueError('Legacy root mismatch')
    with contextlib.redirect_stdout(sys.stderr):
        if op=='runtime-snapshot':
            _,cfg,state=c.load_run(request['run_id'],read_only=True);value=dict(config=cfg,state=state)
        else:
            module=('dsh.ui_bridge' if op.startswith('ui-') else 'dsh.terminal_bridge' if op.startswith(('terminal-','visualization-')) else
                    'tools.dsh_launch' if op.startswith('launch-') else 'tools.dsh_bridge')
            value=importlib.import_module(module).command(request)
    reply=dict(schema_version=1,ok=True,value=value)
except Exception as exc:
    reply=dict(schema_version=1,ok=False,error=dict(code='LEGACY_RUNTIME',message=str(exc).replace(str(project_root),'<workspace>')[:1600]))
print(json.dumps(reply,ensure_ascii=False,allow_nan=False));sys.exit(0 if reply['ok'] else 2)
