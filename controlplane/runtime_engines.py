"""Materialize only the content-addressed engine pinned by a handed-off run."""
import hashlib,json,re,shutil,uuid
from pathlib import Path

def contained(root,relative):
    parts=Path(relative)
    if parts.is_absolute() or not parts.parts or '..' in parts.parts:raise ValueError('Invalid engine path')
    path=Path(root)
    if path.is_symlink():raise ValueError('Redirected engine directory')
    for part in parts.parts:
        path=path/part
        if path.is_symlink():raise ValueError('Redirected engine file')
    return path

def verified_engine(root,identity):
    root=Path(root);manifest=contained(root,'manifest.json')
    if not manifest.is_file():raise ValueError('Pinned engine '+identity+' is unavailable; restore its verified version')
    value=json.loads(manifest.read_text())
    raw=json.dumps(dict(files=value['files'],version=value['version']),sort_keys=True,separators=(',',':')).encode()
    if value.get('engine_id')!=identity or hashlib.sha256(raw).hexdigest()!=identity:raise ValueError('Pinned engine manifest mismatch')
    for name,sha in value['files'].items():
        path=contained(root,name)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('Pinned engine file changed: '+name)
    return value

def materialize_run_engine(workspace,run_id,destination,sources):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}/[0-9]{3,}',run_id):raise ValueError('Invalid handed-off run')
    run=contained(Path(workspace),'runs/'+run_id)
    state=json.loads(contained(run,'state.json').read_text())
    marker=contained(run,'.runtime/engine.json')
    ref=state.get('configuration',{}).get('_pins',{}).get('runtime-engine')
    if not ref or ref.get('path')!='runs/'+run_id+'/.runtime/engine.json' or hashlib.sha256(marker.read_bytes()).hexdigest()!=ref.get('sha'):
        raise ValueError('Handed-off run engine pin changed')
    identity=json.loads(marker.read_text())['engine_id']
    if not re.fullmatch('[a-f0-9]{64}',identity):raise ValueError('Invalid pinned engine identity')
    store=Path(destination);target=store/identity
    if target.exists():verified_engine(target,identity);return identity
    for source in sources:
        source=Path(source)/identity
        if not source.exists():continue
        value=verified_engine(source,identity)
        store.mkdir(parents=True,exist_ok=True)
        staging=store/('.install-'+uuid.uuid4().hex);staging.mkdir()
        try:
            # Copy only declared, verified bytes. Never replace an existing cache.
            for relative in [*value['files'],'manifest.json']:
                to=contained(staging,relative);to.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(contained(source,relative),to)
            verified_engine(staging,identity)
            try:staging.rename(target)
            except OSError:
                if not target.exists():raise
                verified_engine(target,identity)
        finally:
            if staging.exists():shutil.rmtree(staging)
        return identity
    raise ValueError('Pinned engine '+identity+' unavailable for '+run_id+'; install its verified version on this host, then resume. The run was not changed.')
