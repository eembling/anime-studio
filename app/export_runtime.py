"""Cancellation and progress for subprocesses owned by an export worker."""
import os
import signal
import subprocess
import threading

LOCAL=threading.local()


class ExportCancelled(RuntimeError):
    pass


def run(command, timeout=3600, binary=False):
    event=getattr(LOCAL,'cancel',None)
    if event and event.is_set():
        raise ExportCancelled('Export cancelled.')
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=not binary,start_new_session=True)
    elapsed=0
    try:
        while True:
            try:
                out,err=process.communicate(timeout=.2)
                result=subprocess.CompletedProcess(command,process.returncode,out,err)
                callback=getattr(LOCAL,'progress',None)
                if callback:callback()
                return result
            except subprocess.TimeoutExpired:
                elapsed+=.2
                if event and event.is_set():raise ExportCancelled('Export cancelled.')
                if elapsed>=timeout:raise TimeoutError('Export media operation exceeded one hour.')
    finally:
        if process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.communicate()
