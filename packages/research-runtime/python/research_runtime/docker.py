"""Method-neutral Docker boundaries, bounded I/O, queue leases and cleanup.
The legacy lock marker is retained so existing scopes share the same capacity.
"""
from contextlib import contextmanager, nullcontext
import fcntl, json, os, re, selectors, subprocess, threading, time
from pathlib import Path
LABEL = "research.runtime.v1"
MAX_PARALLEL_EVALUATIONS = 3
_LOCK_MARKER = b"anysolvers.v2 sandbox lock\n"
class InfrastructureError(RuntimeError):
    """The sandbox could not establish or confirm its execution boundary."""


class _AdmissionClosed(RuntimeError):
    pass


def _command(args, timeout=10):
    if timeout <= 0:
        raise InfrastructureError("Docker operation exceeded wall budget")
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True,
                                timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InfrastructureError(f"Docker {args[0]} unavailable or timed out") from exc
    if result.returncode:
        raise InfrastructureError(f"Docker {args[0]} failed: {result.stderr[:1000]}")
    return result.stdout


def _json_command(args, timeout=10):
    try:
        return json.loads(_command(args, timeout))
    except (ValueError, TypeError) as exc:
        raise InfrastructureError(f"Malformed Docker {args[0]} response") from exc


def preflight(image):
    """Read local Docker/image facts; never pull images or alter containers."""
    if not isinstance(image,str) or not re.fullmatch(r"sha256:[a-f0-9]{64}",image):
        raise ValueError("A pinned image digest is required")
    info = _json_command(["info", "--format", "{{json .}}"])
    images = _json_command(["image", "inspect", image])
    try:
        security = info["SecurityOptions"]
        if not any("seccomp" in option and "builtin" in option for option in security):
            raise InfrastructureError("Docker built-in seccomp is required")
        if images[0]["Id"] != image:
            raise InfrastructureError("Installed image ID mismatch")
        if info["NCPU"] < 2 or info["MemTotal"] < 512 * 1024**2:
            raise InfrastructureError("Insufficient Docker capacity")
        return {"image_id": images[0]["Id"], "architecture": images[0]["Architecture"],
                "docker_version": info["ServerVersion"], "cpus": info["NCPU"],
                "memory_bytes": info["MemTotal"], "security": security}
    except (KeyError, IndexError, TypeError) as exc:
        raise InfrastructureError("Incomplete Docker preflight response") from exc


def _lock_file(path):
    path = Path(path).absolute()
    # Paths are supplied only by installed adapters, never model tool arguments.
    current = Path(path.anchor)
    for part in path.parts[1:-1]:
        current = current / part
        current.mkdir(exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise ValueError("Redirected execution scope")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    return os.fdopen(descriptor, "r+b")


def _mark_lock(handle):
    handle.seek(0)
    marker = handle.read(len(_LOCK_MARKER) + 1)
    if marker and marker != _LOCK_MARKER:
        raise ValueError("Refusing to reuse an unrelated lock file")
    if not marker:
        handle.seek(0)
        handle.write(_LOCK_MARKER)
        handle.flush()


def _acquire(handle, mode, deadline=None):
    while True:
        try:
            fcntl.flock(handle, mode | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if deadline is not None and time.monotonic() >= deadline:
                raise _AdmissionClosed("run_wall_budget")
            time.sleep(.025)


@contextmanager
def _lock(path):
    # Preserve the existing exclusive global lock protocol for standalone calls.
    with _lock_file(path) as handle:
        _acquire(handle, fcntl.LOCK_EX)
        try:
            _mark_lock(handle)
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class _EvaluationSlot:
    def __init__(self, path, capacity):
        self.path = Path(path).resolve()
        self.capacity = capacity
        self.pid = os.getpid()
        self.thread_id = threading.get_ident()
        self.active = True

    def check(self, path):
        if (not self.active or self.path != Path(path).resolve()
                or self.pid != os.getpid() or self.thread_id != threading.get_ident()):
            raise ValueError("Evaluation slot is not held by this executor")


@contextmanager
def evaluation_slot(path, capacity=MAX_PARALLEL_EVALUATIONS, *, deadline_monotonic=None):
    """Reserve one global dataset slot, respecting the original exclusive lock.

    The shared flock never bypasses an older standalone/exclusive holder. All
    configurations use the same three slot files, so their total cannot exceed
    three even across runs and processes. Smaller capacities use a fixed prefix.
    """
    if type(capacity) is not int or not 1 <= capacity <= MAX_PARALLEL_EVALUATIONS:
        raise ValueError("max_parallel_evaluations must be between one and three")
    path = Path(path)
    with _lock_file(path) as gate:
        _acquire(gate, fcntl.LOCK_SH, deadline_monotonic)
        try:
            # Marker content is constant; concurrent first writers are identical.
            _mark_lock(gate)
            handles = []
            held = None
            try:
                for index in range(capacity):
                    handles.append(_lock_file(path.with_name(path.name + '.slot-' + str(index))))
                while held is None:
                    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                        raise _AdmissionClosed("run_wall_budget")
                    for handle in handles:
                        try:
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            continue
                        held = handle
                        _mark_lock(held)
                        break
                    if held is None:
                        time.sleep(.025)
                token = _EvaluationSlot(path, capacity)
                try:
                    yield token
                finally:
                    token.active = False
            finally:
                for handle in handles:
                    handle.close()
        finally:
            fcntl.flock(gate, fcntl.LOCK_UN)


def _slot_guard(path, token):
    if token is None:
        return _lock(path)
    if not isinstance(token, _EvaluationSlot):
        raise ValueError("Invalid evaluation slot")
    token.check(path)
    return nullcontext()


def _check_capacity(facts, slots, limits):
    if slots > 1 and (facts.get('cpus', 0) < slots
            or facts.get('memory_bytes', 0) < slots * limits['memory_mb'] * 1024**2):
        raise InfrastructureError("Insufficient Docker capacity for the configured evaluation slots")


def _configuration_checks(info, image, source, wrapper, limits, name, *, code_volume=None):
    """Fail closed on the actual created container, before it can start."""
    try:
        host, config = info["HostConfig"], info["Config"]
        if code_volume is not None:
            if code_volume != name+'-code':raise ValueError('Unexpected code volume identity')
            mounts=[(m['Type'],m.get('Name'),m['Destination'],m['RW'],m.get('Driver')) for m in info['Mounts']]
            expected=[('volume',code_volume,'/code',False,'local')]
        else:
            mounts = [(m["Type"], m["Destination"], str(Path(m["Source"]).resolve()), m["RW"]) for m in info["Mounts"]]
            expected = [("bind", "/solver.py", str(source.resolve()), False), ("bind", "/entrypoint.py", str(wrapper.resolve()), False)]
        ulimits = {u["Name"]: (u["Soft"], u["Hard"]) for u in host["Ulimits"]}
        security = host["SecurityOpt"]
        checks = {
            "identity": info["Name"] == "/" + name and config["Labels"].get(LABEL) == name,
            "image": info["Image"] == image,
            "command": config["Entrypoint"] == ["python"] and config["Cmd"] == ["-I", "-B", "/code/entrypoint.py" if code_volume else "/entrypoint.py"],
            "nonroot": config["User"] == "65534:65534",
            "readonly_root": host["ReadonlyRootfs"] is True,
            "no_network": host["NetworkMode"] == "none",
            "one_cpu": host["NanoCpus"] == 1_000_000_000,
            "memory": host["Memory"] == host["MemorySwap"] == limits["memory_mb"] * 1024**2,
            "pids": host["PidsLimit"] == limits["pids"],
            "capabilities": host["CapDrop"] == ["ALL"] and not host.get("CapAdd"),
            "security": security in (["no-new-privileges"], ["no-new-privileges=true"]),
            "no_privileged_devices": host["Privileged"] is False and not host.get("Devices") and not host.get("DeviceRequests"),
            "private_namespaces": host.get("PidMode", "") == "" and host.get("IpcMode") == "private" and host.get("UTSMode", "") == "",
            "init": host["Init"] is True,
            "ulimits": ulimits == {"cpu": (limits["cpu_s"], limits["cpu_s"]), "nofile": (64, 64), "core": (0, 0)},
            "logs": host["LogConfig"]["Type"] == "none",
            "tmpfs": host["Tmpfs"] == {"/tmp": f"rw,noexec,nosuid,nodev,size={limits['tmp_mb']}m,mode=1777"},
            "only_readonly_code_volume" if code_volume else "only_two_readonly_files": sorted(mounts) == sorted(expected),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise InfrastructureError("Incomplete container configuration") from exc
    if not all(checks.values()):
        failed = ", ".join(key for key, passed in checks.items() if not passed)
        raise InfrastructureError("Sandbox configuration mismatch: " + failed)
    return checks


def _create_args(name, source, wrapper, image, limits, *, code_volume=None):
    if code_volume is not None and code_volume != name+'-code':raise ValueError('Unexpected code volume identity')
    mounts=(["--mount",f"type=volume,source={code_volume},target=/code,readonly,volume-nocopy"] if code_volume else
            ["--mount",f"type=bind,source={source},target=/solver.py,readonly", "--mount",f"type=bind,source={wrapper},target=/entrypoint.py,readonly"])
    return ["create", "--pull=never", "--name", name, "--label", f"{LABEL}={name}",
            "-i", "--init", "--read-only", "--user", "65534:65534", "--network", "none",
            "--ipc", "private", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--cpus", "1", "--memory", f"{limits['memory_mb']}m",
            "--memory-swap", f"{limits['memory_mb']}m", "--pids-limit", str(limits["pids"]),
            "--ulimit", f"cpu={limits['cpu_s']}:{limits['cpu_s']}", "--ulimit", "nofile=64:64",
            "--ulimit", "core=0:0", "--log-driver", "none", "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={limits['tmp_mb']}m,mode=1777",
            *mounts, "--entrypoint", "python", image, "-I", "-B", "/code/entrypoint.py" if code_volume else "/entrypoint.py"]


def _read_bounded(process, payload, deadline, limit):
    """Bound combined output and wall time, including a blocked input pipe."""
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    reason, offset, used = None, 0, 0
    try:
        with selectors.DefaultSelector() as selector:
            for stream in ("stdin", "stdout", "stderr"):
                pipe = getattr(process, stream)
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_WRITE if stream == "stdin" else selectors.EVENT_READ, stream)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = "wall_timeout"
                    break
                for key, _ in selector.select(min(0.1, remaining)):
                    if key.data == "stdin":
                        try:
                            offset += os.write(key.fd, payload[offset:offset + 8192])
                        except BrokenPipeError:
                            offset = len(payload)
                        if offset == len(payload):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    streams[key.data].extend(chunk[:max(0, limit - used)])
                    used += len(chunk)
                    if used > limit:
                        reason = "output_limit"
                        break
                if reason:
                    break
        if not reason:
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                reason = "wall_timeout"
        return {key: bytes(value) for key, value in streams.items()}, reason
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def _cleanup(name):
    owned = _command(["ps", "-a", "--filter", f"name=^/{name}$", "--filter",
                      f"label={LABEL}={name}", "--format", "{{.Names}}"])
    if name in owned.splitlines():
        _command(["rm", "-f", name])
    if _command(["ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"]).strip():
        raise InfrastructureError("Cleanup not confirmed; never delete an unlabelled container")


