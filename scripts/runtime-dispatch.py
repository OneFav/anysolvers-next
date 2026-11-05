"""Verify a pinned installed engine before importing any engine or legacy code."""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import sys
import io

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def contained(root,relative):
    value=Path(relative)
    if value.is_absolute() or '..' in value.parts or not value.parts:raise ValueError('Invalid runtime path')
    path=root
    for part in value.parts:
        path=path/part
        if path.is_symlink():raise ValueError('Redirected runtime path')
    if not path.resolve().is_relative_to(root):raise ValueError('Runtime path escaped')
    return path

def checked_engine(store,identity):
    if not re.fullmatch('[a-f0-9]{64}',identity):raise ValueError('Invalid engine identity')
    root=contained(store,identity)
    manifest_path=contained(root,'manifest.json')
    if not manifest_path.is_file():raise ValueError('Pinned engine '+identity+' unavailable; restore its verified version on this host before retrying. Original run and artifacts are unchanged.')
    manifest=json.loads(manifest_path.read_text())
    encoded=json.dumps(dict(files=manifest['files'],version=manifest['version']),sort_keys=True,separators=(',',':')).encode()
    if hashlib.sha256(encoded).hexdigest()!=identity or manifest['engine_id']!=identity:raise ValueError('Installed engine manifest changed')
    for name,expected in manifest['files'].items():
        if digest(contained(root,name))!=expected:raise ValueError('Installed engine file changed: '+name)
    return root

try:
    project=Path(sys.argv[1]).resolve(strict=True);store=Path(sys.argv[2]).resolve(strict=True);identity=sys.argv[3]
    raw=sys.stdin.buffer.read(2*1024*1024+1)
    if len(raw)>2*1024*1024:raise ValueError('request too large')
    request=json.loads(raw);rid=request.get('run_id');legacy=False
    if rid:
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}/[0-9]{3,}',rid):raise ValueError('Invalid run ID')
        directory=contained(project,'runs/'+rid)
        state=json.loads(contained(directory,'state.json').read_text())
        marker=contained(directory,'.runtime/engine.json')
        ref=state.get('configuration',{}).get('_pins',{}).get('runtime-engine')
        if marker.exists() or ref:
            if not ref or ref['path']!='runs/'+rid+'/.runtime/engine.json' or digest(marker)!=ref['sha']:raise ValueError('Pinned run engine identity changed')
            identity=json.loads(marker.read_text())['engine_id']
        else:
            legacy=True
            # No archived-byte fallback: old files must exist and match their original seals.
            config=state['configuration'];refs=list(config['_pins'].values())
            for module in config['modules'].values():refs.extend([module,*module['resources'].values()])
            for ref in refs:
                if digest(contained(project,ref['path']))!=ref['sha']:raise ValueError('Legacy pinned file changed: '+ref['path'])
    engine=checked_engine(store,identity)
    os.environ['ANYSOLVERS_ENGINE_STORE']=str(store)
    os.environ['ANYSOLVERS_DEFAULT_ENGINE']=identity
    os.environ['ANYSOLVERS_DISPATCHER']=str(Path(__file__).resolve())
    os.environ['ANYSOLVERS_PROJECT_ROOT']=str(project)
    sys.argv=[str(engine/'dsh/entry.py'),str(project)]
    sys.stdin=io.TextIOWrapper(io.BytesIO(raw),encoding='utf-8')
    runpy.run_path(str(engine/('dsh/legacy_entry.py' if legacy else 'dsh/entry.py')),run_name='__main__')
except Exception as exc:
    print(json.dumps(dict(schema_version=1,ok=False,error=dict(code='RUNTIME_INTEGRITY',message=str(exc)[:1600])),ensure_ascii=False))
    sys.exit(2)
