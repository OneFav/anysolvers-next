"""Bounded file transfer between an untrusted researcher workspace and the broker.

These helpers never execute candidate code. Directory descriptors keep a concurrent
workspace rename or symlink replacement from redirecting a read into the host.
"""
import hashlib
import os
from pathlib import Path
import stat
import uuid

from tools import common as c

MAX_FILE_BYTES = 2 * 1024 * 1024


def parts(relative):
    """Accept a nonempty portable relative filename, without traversal or aliases."""
    if not isinstance(relative, str) or not relative or '\\' in relative or '\x00' in relative:
        raise ValueError('invalid relative asset path')
    names = relative.split('/')
    if any(name in ('', '.', '..') for name in names) or Path(relative).is_absolute():
        raise ValueError('expected a contained relative asset path')
    return names


def _directory(root, names, create=False):
    # root is constructed by the trusted bridge, never supplied by a researcher.
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in names:
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read(root, relative, limit=MAX_FILE_BYTES):
    """Read one regular, non-hardlinked file through pinned directory descriptors."""
    names = parts(relative)
    parent = _directory(root, names[:-1])
    descriptor = None
    try:
        descriptor = os.open(names[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError('asset must be a regular file without hard links')
        if before.st_size > limit:
            raise ValueError('asset exceeds size limit')
        chunks, length = [], 0
        while True:
            chunk = os.read(descriptor, min(65536, limit + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > limit:
                raise ValueError('asset exceeds size limit')
        after = os.fstat(descriptor)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise ValueError('asset changed while being copied')
        value = b''.join(chunks)
        if len(value) != before.st_size:
            raise ValueError('asset changed while being copied')
        return value
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def write(root, relative, content, overwrite=False, mode=0o600):
    """Publish one bounded file atomically; immutable writes are idempotent."""
    if not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES:
        raise ValueError('asset exceeds size limit')
    names = parts(relative)
    parent = _directory(root, names[:-1], create=True)
    temporary = '.dsh-' + uuid.uuid4().hex
    created = False
    try:
        try:
            existing = os.stat(names[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                raise ValueError('target must be a regular file without links')
            current = read(root, relative)
            if current == content:
                return False
            if not overwrite:
                raise ValueError('asset ID already contains different bytes')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             mode, dir_fd=parent)
        created = True
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, names[-1], src_dir_fd=parent, dst_dir_fd=parent)
            created = False
        else:
            # link() is a no-clobber publication primitive; the temporary link is
            # removed immediately so callers only ever consume a single-link file.
            try:
                os.link(temporary, names[-1], src_dir_fd=parent, dst_dir_fd=parent,
                        follow_symlinks=False)
            except FileExistsError:
                if read(root, relative) != content:
                    raise ValueError('asset ID already contains different bytes') from None
            finally:
                os.unlink(temporary, dir_fd=parent)
                created = False
        os.fsync(parent)
        return True
    finally:
        if created:
            os.unlink(temporary, dir_fd=parent)
        os.close(parent)


def sha(content):
    return hashlib.sha256(content).hexdigest()


def workspace(run, session_id, create=False):
    """Resolve the one workspace granted to this registered researcher session."""
    # Session ids use the schema's 96-character bound, rather than artifact ids.
    import re
    if not isinstance(session_id, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,95}', session_id):
        raise ValueError('invalid researcher session identity')
    relative = c.runtime_directory(run).name + '/workspaces/' + session_id + '/work'
    storage = os.environ.get('ANYSOLVERS_DSH_WORKSPACES')
    if storage:
        base = Path(storage)
        if not base.is_absolute():
            base=c.contained(c.ROOT,storage)
            if base==c.ROOT or ',' in storage:raise ValueError('invalid trusted workspace storage root')
            run_id=run.resolve().relative_to(c.ROOT/'runs').as_posix()
            c.run_identifier(run_id)
            descriptor=_directory(c.ROOT,parts(storage)+['runs']+parts(run_id)+parts(relative),create=create)
            os.close(descriptor)
            return base/'runs'/run_id/relative
        if base == Path(base.anchor) or ',' in storage:
            raise ValueError('invalid trusted workspace storage root')
        root = c.ROOT.resolve()
        run_id = run.resolve().relative_to(root / 'runs').as_posix()
        c.run_identifier(run_id)
        owner = hashlib.sha256(str(root).encode()).hexdigest()
        names = parts(str(base).lstrip('/')) + [owner, 'runs'] + parts(run_id) + parts(relative)
        descriptor = _directory(Path(base.anchor), names, create=create)
        try:
            if os.fstat(descriptor).st_uid != os.getuid():
                raise ValueError('workspace storage belongs to another user')
        finally:
            os.close(descriptor)
        return base / owner / 'runs' / run_id / relative
    path = c.contained(run, relative)
    if create:
        descriptor = _directory(run, parts(relative), create=True)
        os.close(descriptor)
    return path
