"""Process identity and host locking on Windows and POSIX; never kill by stale PID."""
import contextlib
import os
import subprocess
from pathlib import Path


def birth(pid):
    if not isinstance(pid,int) or pid<=0:return None
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return None
        try:
            code=wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259:return None
            times=[wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):return None
            return str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
        finally:kernel.CloseHandle(handle)
    try:
        os.kill(pid,0)
        result=subprocess.run(['ps','-o','lstart=','-p',str(pid)],capture_output=True,text=True,timeout=5,check=True)
        return result.stdout.strip() or None
    except (OSError,subprocess.SubprocessError):return None


@contextlib.contextmanager
def host_lock(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with open(path,'a+b') as stream:
        if os.name=='nt':
            import msvcrt
            stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            if os.name=='nt':stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def atomic(path,value):
    import json,secrets
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+secrets.token_hex(6)+'.tmp')
    with open(temp,'w',encoding='utf-8') as stream:
        if os.name!='nt':os.chmod(temp,0o600)
        json.dump(value,stream,ensure_ascii=False,allow_nan=False)
        stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)
