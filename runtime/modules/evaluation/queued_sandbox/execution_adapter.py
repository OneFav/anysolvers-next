"""AnySolvers-to-runtime adapter. Existing measure code is the only quota owner."""
from research_runtime.jobs import JobLedger, ExecutionResult, safe
import hashlib
from tools import common

def execute(run_id, request_id, inputs, resources, asset_payloads, callback):
    run=common.run_path(run_id)
    directory=common.runtime_directory(run)/'input-assets'
    directory.mkdir(parents=True,exist_ok=True)
    assets=[]
    for kind,content in asset_payloads.items():
        data=content.encode();sha=hashlib.sha256(data).hexdigest();path=safe(directory/(sha+'.'+kind))
        try:
            with path.open('xb') as stream:stream.write(data)
        except FileExistsError:
            if path.read_bytes()!=data:raise ValueError('Registered execution input changed')
        assets.append(dict(path=path.relative_to(common.ROOT).as_posix(),sha256=sha))
    ledger=JobLedger(common.runtime_directory(run)/'jobs', common.ROOT/'runs/.sandbox-v2.lock', {
        'anysolvers-evaluation': lambda request: _result(callback())},path_base=common.ROOT)
    return ledger.execute(request_id,'anysolvers-evaluation',inputs,assets,resources)

def _result(value):
    return ExecutionResult(value,value.get('cleanup_confirmed') is True and not value.get('infrastructure_error'))
