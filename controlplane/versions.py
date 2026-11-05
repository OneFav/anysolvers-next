import hashlib
import json
import os
from pathlib import Path
import shutil
from .store import encode

EXCLUDED={'.git','node_modules','.state','projects','versions','__pycache__','.venv','.DS_Store'}


def inventory(root):
    root=Path(root).resolve()
    result={}
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=sorted(d for d in dirs if d not in EXCLUDED)
        for d in dirs:
            if (Path(directory)/d).is_symlink():raise ValueError('Implementation contains a directory symlink')
        for name in sorted(files):
            path=Path(directory)/name
            if name.endswith('.pyc') or name in EXCLUDED or path.relative_to(root).as_posix()=='config/settings.json':continue
            if path.is_symlink():raise ValueError('Implementation contains a file symlink')
            result[path.relative_to(root).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def content_digest(root):
    return hashlib.sha256(encode(inventory(root)).encode()).hexdigest()


def seal(source, target_root, version_id, validation):
    """Create a content-addressed copy. Never patch an active implementation."""
    source=Path(source).resolve();target_root=Path(target_root).resolve()
    if not validation.get('passed') or not validation.get('checks'):
        raise ValueError('Successful validation evidence is required')
    files=inventory(source)
    sha=hashlib.sha256(encode(files).encode()).hexdigest()
    target=target_root/sha
    if not target.exists():
        temp=target_root/('.staging-'+sha)
        if temp.exists():raise ValueError('Unresolved version staging directory')
        temp.mkdir(parents=True)
        for relative in files:
            to=temp/relative;to.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source/relative,to)
        if content_digest(temp)!=sha:raise ValueError('Source changed while copying version')
        temp.rename(target)
    if content_digest(target)!=sha:raise ValueError('Existing version content differs')
    return dict(id=version_id,digest=sha,state='ready',validation=validation),target
