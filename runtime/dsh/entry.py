"""Installed engine + project data. Never import executable files from the project."""
import contextlib
import importlib
import json
import os
from pathlib import Path
import sys
import types

engine_root=Path(__file__).resolve().parents[1]
project_root=Path(sys.argv[1]).resolve(strict=True)
os.environ['ANYSOLVERS_PROJECT_ROOT']=str(project_root)
sys.path.insert(0,str(engine_root))
for name in ('tools','modules','dsh'):
    package=types.ModuleType(name);package.__path__=[str(engine_root/name)];sys.modules[name]=package
try:
    raw=sys.stdin.buffer.read(2*1024*1024+1)
    if len(raw)>2*1024*1024:raise ValueError('request too large')
    request=json.loads(raw)
    from tools import common as c
    from modules.evaluation.queued_sandbox import sandbox
    sandbox.PROJECT_ROOT=project_root
    op=request.get('operation','')
    with contextlib.redirect_stdout(sys.stderr):
        if op=='runtime-snapshot':
            _,cfg,state=c.load_run(request['run_id'],read_only=True)
            value=dict(config=cfg,state=state)
        else:
            module=('dsh.as_research' if op.startswith('as-') else 'dsh.ui_bridge' if op.startswith('ui-') else
                    'dsh.terminal_bridge' if op.startswith(('terminal-','visualization-')) else
                    'tools.dsh_launch' if op.startswith('launch-') else 'tools.dsh_bridge')
            value=importlib.import_module(module).command(request)
    reply=dict(schema_version=1,ok=True,value=value)
except Exception as exc:
    reply=dict(schema_version=1,ok=False,error=dict(code='PROJECT_REQUEST',message=str(exc).replace(str(project_root),'<workspace>').replace(str(engine_root),'<engine>')[:1800]))
print(json.dumps(reply,ensure_ascii=False,allow_nan=False));sys.exit(0 if reply['ok'] else 2)
