"""Explicit deterministic TEST executor. No candidate program is imported or run."""
import hashlib, json, sys, time
from pathlib import Path
from research_runtime.jobs import JobLedger, ExecutionResult, safe
from research_runtime.docker import evaluation_slot
request=json.load(sys.stdin)
root=Path(request['root']).resolve();run=root/'runs'/request['runId'];scope=root/'runs/.sandbox-v2.lock'
state=json.loads(safe(run/'dynamic/state.json').read_text())
outputs=[]
for dataset in state['snapshotRefs']:
    path=safe(run/dataset['path'])
    for configuration in state['configurations']:
        def execute(payload):
            with evaluation_slot(scope,1):
                rows=json.loads(path.read_text())['values']
                loss=sum((value-payload['bias'])**2 for value in rows)/len(rows)
                return ExecutionResult(dict(testOnly=True,datasetVersion=dataset['version'],configuration=configuration['id'],fixtureLoss=loss),True)
        ledger=JobLedger(run/'.runtime/jobs',scope,{'dynamic-fixture-v1':execute})
        outputs.append(ledger.execute(dataset['version']+'-'+configuration['id'],'dynamic-fixture-v1',
            configuration,[dict(path=str(path),sha256=dataset['sha256'])],dict(capacity=1,testOnly=True)))
print(json.dumps(dict(schema_version=1,ok=True,value=dict(measurements=outputs))))
