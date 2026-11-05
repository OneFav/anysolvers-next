"""Read older batches through their original verified runtime, without hash fallback."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tools import common as c

def invoke(run_id, operation):
    request=dict(schema_version=1,operation=operation,run_id=run_id,payload={})
    response=subprocess.run([sys.executable,'-B',os.environ['ANYSOLVERS_DISPATCHER'],str(c.ROOT),os.environ['ANYSOLVERS_ENGINE_STORE'],os.environ['ANYSOLVERS_DEFAULT_ENGINE']],
                            input=json.dumps(request),text=True,capture_output=True,timeout=45)
    try: reply=json.loads(response.stdout)
    except ValueError: raise ValueError('旧运行读取失败，未替换其内核或绕过校验。') from None
    if not reply.get('ok'): raise ValueError(reply.get('error',{}).get('message','旧运行校验失败'))
    return reply['value']

def local(run_id):
    marker=c.contained(c.run_path(run_id),'.runtime/engine.json')
    return marker.exists() and c.read_json(marker)['engine_id']==c.read_json(c.ENGINE_ROOT/'manifest.json')['engine_id']

def snapshot(run_id):
    if local(run_id):return c.load_run(run_id,read_only=True)
    value=invoke(run_id,'runtime-snapshot')
    return c.run_path(run_id),value['config'],value['state']

def status(run_id):
    if local(run_id):
        from tools.dsh_bridge import _run_status
        return _run_status(run_id)
    return invoke(run_id,'status')


def terminal_status(run_id):
    if local(run_id):
        from dsh.terminal_bridge import snapshot, status_data
        with snapshot(run_id) as args:return status_data(*args)
    return invoke(run_id,'terminal-status')
