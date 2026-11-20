"""Actionable render errors; retry preserves captured inputs."""

def diagnose(job):
    text=(job.get('error') or '').lower();remote=bool(job.get('prompt_id'))
    retry='Reconnect render' if remote else 'Retry same inputs'
    if not text:return None
    if any(x in text for x in ('out of memory','cuda oom','allocation failed','mps backend out of memory','vram grow failed','vram allocation failed')):
        if 'cliptextencode' in text or 'text encoder' in text:
            return {'category':'memory','title':'The prompt encoder ran out of GPU memory','guidance':'Keep GPU encoding enabled. Check nvidia-smi on the server and unload competing models, such as Ollama, to free VRAM on the GPU ComfyUI uses. Total VRAM is not the same as free VRAM. Retry keeps the captured workflow; generate a NEW clip after changing the workflow. The shot-length notice is unrelated to this failure.','action':'servers','retry_label':retry}
        return {'category':'memory','title':'The GPU ran out of memory','guidance':'Review the shot and generate a new draft with a smaller image size or frame limit, or use a GPU with more free memory. Retrying the same captured inputs may fail again.','action':'shots','retry_label':retry}
    if any(x in text for x in ('missing node','node type','invalid class_type','missing class_type','unknown class_type','unknown node')):
        return {'category':'nodes','title':'A required ComfyUI node is unavailable','guidance':'Check this workflow’s required node packs on the assigned server, install/update them manually, restart ComfyUI and check readiness.','action':'workflows','retry_label':retry}
    if any(x in text for x in ('not in list','model not found','missing model','no such file','unsupported unet','unsupported ckpt')):
        return {'category':'model','title':'A model or input file is missing','guidance':'Check the exact model names and required input files on the assigned server. Restore the missing file, or generate a new job with a compatible workflow.','action':'workflows','retry_label':retry}
    if any(x in text for x in ('connection refused','urlopen error','connection reset','unreachable','failed to establish','http error 502','http error 503')):
        return {'category':'connection','title':'The ComfyUI connection was lost','guidance':'Check that the server is running and its address is reachable. Reconnect uses the saved remote prompt when available; it does not intentionally submit a duplicate render.','action':'servers','retry_label':retry}
    if any(x in text for x in ('timed out','timeout')):
        return {'category':'timeout','title':'The render exceeded its time limit','guidance':'Check the remote queue and server load. Increase the timeout for future jobs if needed. A new render may be required if the original was interrupted or its remote history is gone.','action':'generation','retry_label':retry}
    if any(x in text for x in ('changed','previous part','previous section','no approved','no voice','reference voice','dialogue line')):
        return {'category':'inputs','title':'This shot needs updated inputs','guidance':'Review the selected picture, dialogue, voice reference or continuation predecessor. Generate a new job after changing inputs; Retry keeps the original captured inputs.','action':'shots','retry_label':retry}
    return {'category':'other','title':'The render could not finish','guidance':'Review the server and technical details below. Retry uses the same captured inputs; workflow changes apply to a newly generated job.','action':'workflows','retry_label':retry}
