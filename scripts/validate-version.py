"""Run the fixed local checks and bind evidence to the resulting implementation bytes."""
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from controlplane.versions import content_digest
from controlplane.processes import atomic
root=Path(__file__).resolve().parents[1]
checks=[]
# npm scripts must use the same Python environment as this validator.
environment=dict(os.environ,PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ.get("PATH",""))
for command in (['npm','run','check'],['npm','run','test:runtime'],[sys.executable,'-B','-m','unittest','discover','-s','tests/controlplane','-v'],[sys.executable,'-B','tests/test-recovery-clock.py','-v'],[sys.executable,'-B','tests/test-e2e-contracts.py','-v'],[sys.executable,'-B','tests/test-remote-evaluation.py'],[sys.executable,'-B','tests/test-evaluation-retention.py'],[sys.executable,'-B','tests/test-budget-handoff.py'],[sys.executable,'-B','tests/native-evaluation.py'],[sys.executable,'-B','tests/native-smoke.py'],[sys.executable,'-B','tests/host-smoke.py']):
    result=subprocess.run(command,cwd=root,env=environment,capture_output=True,text=True,timeout=600)
    checks.append(dict(command=command,code=result.returncode,tail=(result.stdout+result.stderr)[-6000:]))
    if result.returncode:
        atomic(root/'.state/validation.failed.json',dict(passed=False,checks=checks));raise SystemExit('Validation failed: '+str(command))
value=dict(passed=True,source_digest=content_digest(root),checks=checks,live_feishu=False,live_model_research=False)
atomic(root/'.state/validation.json',value)
print(json.dumps(dict(passed=True,source_digest=value['source_digest'],evidence=str(root/'.state/validation.json')),indent=2))
