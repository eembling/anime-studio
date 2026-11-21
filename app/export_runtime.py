"""Cancellation and progress for subprocesses owned by an export worker."""
import os
import signal
import subprocess
import threading
from platform_runtime import terminate_process

LOCAL=threading.local()
LOCK=threading.Lock()
PROCESSES={}


class ExportCancelled(RuntimeError):
    pass


def run(command, timeout=3600, binary=False):
    event=getattr(LOCAL,'cancel',None)
    if event and event.is_set():
        raise ExportCancelled('Export cancelled.')
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=not binary,start_new_session=True)
    if event:
        with LOCK:PROCESSES.setdefault(event,set()).add(process)
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
        if event:
            with LOCK:
                group=PROCESSES.get(event,set())
                group.discard(process)
                if not group:PROCESSES.pop(event,None)
        if process.poll() is None:
            terminate_process(process)
            try:process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                terminate_process(process,force=True);process.communicate()


def stop(event):
    event.set()
    with LOCK:
        processes=list(PROCESSES.get(event,set()))
    for process in processes:
        if process.poll() is None:
            terminate_process(process)
