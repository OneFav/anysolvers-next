"""Explicit live SSH process fixtures, with durable per-case evaluator receipts.

Never run as routine local tests. Requires a deployed immutable version, SSH
authorization and a designated evidence output. These are not formal research.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'packages/research-runtime/python')]
from modules.evaluation.queued_sandbox import remote

parser = argparse.ArgumentParser()
parser.add_argument('--config', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
compute = json.loads(Path(args.config).read_text())
evidence = dict(kind='real-SSH-accounted-process-fixtures', formal_research=False,
                preflight=remote.preflight(compute), cases=[])
output = Path(args.output)

evaluator = '''def validate_input(case): pass
def reset(case): return 0, {'n': 0}
def step(state, action):
    if type(action) is not int or action != state + 1: raise ValueError('wrong increment')
    return action, {'n': action}, action == 3
def score_episode(state, trace, case): return {'score': state, 'metrics': {'steps': len(trace)}}
'''
assets = {name: dict(content=text, sha=hashlib.sha256(text.encode()).hexdigest())
          for name, text in [('eval.py', evaluator), ('baseline.py', '# fixture'), ('tests.json', '{}')]}
benchmark = dict(benchmark_id='ssh-process-live-fixture', task_id='ssh-process-live-fixture',
                 protocol=dict(kind='episode', max_steps=3, action_wall_s=.5),
                 evaluator_file='eval.py', baseline_file='baseline.py', tests_file='tests.json', support_files=[])
environment = dict(benchmark=benchmark, assets=assets, signature=remote.signature(benchmark, assets))
fixtures = [
    ('three_steps', "def solve(o): return o['n'] + 1\n", True, None),
    ('invalid_action', "def solve(o): return -1\n", False, 'invalid_action'),
    ('action_timeout', "import time\ndef solve(o): time.sleep(2); return 0\n", False, 'action_timeout'),
    ('output_limit', "def solve(o): print('x' * 20000); return 1\n", False, 'output_limit'),
    ('memory_limit', "def solve(o): return len(bytearray(256 * 1024 * 1024))\n", False, 'candidate_runtime_error'),
]
try:
    for name, source, valid, error in fixtures:
        payload = dict(executor='ssh-process', image='ssh-process:v1',
                       process_authorization='explicit-user-development-only', source=source,
                       case={}, environment=environment,
                       limits=dict(wall_s=4, cpu_s=1, memory_mb=64, pids=1, output_bytes=16384, tmp_mb=1))
        payload['request_id'] = remote.digest(dict(validation=str(output.resolve()), name=name,
                                                  root=compute['remote']['root'], payload=payload))
        first = remote.call(compute, 'case', payload, 15)
        result = first['result']
        record = dict(name=name, request_id=first['request_id'], ledger=first['ledger'],
                      request_sha=first['request_sha'], result=result)
        evidence['cases'].append(record)
        remote.atomic(output, evidence)
        assert first['request_sha'] == remote.digest(payload)
        assert result['valid'] == valid and result['cleanup_confirmed'] and not result['infrastructure_error'], record
        assert result['isolation_verified'] is False
        if error:
            assert result['error'].startswith(error), record
        else:
            assert result['score'] == 3 and result['steps'] == 3
        again = remote.call(compute, 'case', payload, 15)
        assert again == first, 'Repeated ID did not return the identical durable receipt'
        record['same_id_receipt_equal'] = True
    evidence['passed'] = True
finally:
    remote.atomic(output, evidence)
print(json.dumps(dict(passed=evidence.get('passed', False), cases=len(evidence['cases']), evidence=str(output))))
