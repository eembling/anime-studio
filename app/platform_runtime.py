"""Platform-owned storage, file locking and cancellation of owned child processes."""
import contextlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def config_directory():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local')))/'AnimeStudio'
    return Path.home()/'Library/Application Support/AnimeStudio'


@contextlib.contextmanager
def storage_lock(path):
    with path.open('a+b') as handle:
        if sys.platform == 'win32':
            import msvcrt
            handle.seek(0);handle.write(b'\0');handle.flush();handle.seek(0)
            while True:
                try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:time.sleep(.1)
            try:yield
            finally:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(handle,fcntl.LOCK_UN)


def terminate_process(process, force=False):
    if process.poll() is not None:return
    if sys.platform == 'win32':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
    else:
        try:os.killpg(process.pid,signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:pass


@contextlib.contextmanager
def project_controller(path):
    """Hold one nonblocking lock for the lifetime of a project controller."""
    with path.open('a+b') as handle:
        handle.seek(0)
        if sys.platform == 'win32':
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('This project already has a render controller.') from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('This project already has a render controller.') from exc
        yield
