"""Deterministic storage and receipt tests; no model or candidate execution."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'packages/research-runtime/python')]
from modules.evaluation.queued_sandbox import retention, receipts, remote, process, sandbox
from tools import module_runtime


class Retention(unittest.TestCase):
    def result(self, valid=True):
        return dict(valid=valid, score=7 if valid else None, metrics={'covered': 17},
                    steps=1000, cleanup_confirmed=True, infrastructure_error=False,
                    wall_s=.2, queue_wait_s=.1, solution={'action': 2},
                    trace=[{'step': i, 'observation': {'map': 'x'*4096}, 'action': 2,
                            'next_observation': {'map': 'y'*4096}, 'done': i == 999} for i in range(1000)])

    def test_summary_keeps_measurements_and_does_not_mutate_scoring_input(self):
        full = self.result()
        before = copy.deepcopy(full)
        compact = retention.retain(full)
        self.assertEqual(full, before)
        self.assertNotIn('trace', compact)
        for key in full.keys() - {'trace'}:
            self.assertEqual(compact[key], full[key])
        canonical = json.dumps(full['trace'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        self.assertEqual(compact['trace_summary']['sha256'], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(compact['trace_summary']['canonical_bytes'], len(canonical))
        self.assertLess(len(json.dumps(compact)), 1024)
        self.assertEqual(retention.retain(compact), compact)

    def test_failure_preserves_error_and_bounded_terminal_context(self):
        result = self.result(False)
        result.update(error='invalid_action', diagnostic={'stdout': '日'*10000, 'stderr': 'failure'})
        result['trace'] = [{'step': i, 'action': i} for i in range(50)]
        compact = retention.retain(result)
        self.assertEqual(compact['error'], result['error'])
        self.assertEqual([x['step'] for x in compact['trace_sample']], [47, 48, 49])
        self.assertTrue(compact['diagnostic_summary']['stdout']['truncated'])
        self.assertEqual(compact['diagnostic_summary']['stdout']['bytes'], 30000)
        self.assertLessEqual(len(compact['diagnostic']['stdout'].encode()), retention.DIAGNOSTIC_BYTES+13)
        self.assertEqual(retention.retain(compact), compact)
        self.assertEqual(retention.retain(self.result(False))['trace_sample'], [])

    def test_full_policy_is_explicit_and_parameter_is_pinned(self):
        full = self.result()
        self.assertEqual(retention.retain(full, 'full')['trace'], full['trace'])
        default = module_runtime.read_module('modules/evaluation/queued_sandbox')
        self.assertEqual(default['params'], {'trace_retention': 'summary'})
        explicit = module_runtime.read_module('modules/evaluation/queued_sandbox', {'trace_retention': 'full'})
        self.assertEqual(retention.policy({'modules': {'evaluation': explicit}}), 'full')
        with self.assertRaises(ValueError):
            retention.retain(full, 'drop_errors')


class ArchivedReceipt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()/'versions'/'fixture'
        self.root.mkdir(parents=True)
        code = 'def validate_input(case): pass\n'
        assets = {name: dict(content=value, sha=hashlib.sha256(value.encode()).hexdigest())
                  for name, value in [('eval.py', code), ('base.py', '# fixture'), ('tests.json', '{}')]}
        benchmark = dict(benchmark_id='retention', task_id='retention',
                         protocol=dict(kind='episode', max_steps=2, action_wall_s=1),
                         evaluator_file='eval.py', baseline_file='base.py', tests_file='tests.json', support_files=[])
        self.payload = dict(request_id='a'*64, source='# inert fixture', case={},
                            image='ssh-process:v1', executor='ssh-process',
                            process_authorization='explicit-user-development-only',
                            limits=dict(sandbox.DEFAULT_LIMITS),
                            environment=dict(benchmark=benchmark, assets=assets,
                                             signature=remote.signature(benchmark, assets)))
        self.directory = self.root.parent.parent/'execution-state'/self.payload['request_id']

    def create_archive(self):
        result = dict(valid=True, score=1, metrics={}, cleanup_confirmed=True,
                      infrastructure_error=False, queue_wait_s=0,
                      trace=[{'step': 0, 'observation': 'raw '*1000}])
        with patch.object(process, 'execute_episode', return_value=result) as execute:
            value = remote.run_case(self.root, self.payload)
            self.assertEqual(execute.call_count, 1)
        raw = (self.directory/'result.json').read_bytes()
        compressed = gzip.compress(raw, mtime=0)
        (self.directory/'result.json.gz').write_bytes(compressed)
        metadata = dict(schema_version=1, format='gzip', original='result.json', compressed='result.json.gz',
                        original_bytes=len(raw), original_sha256=hashlib.sha256(raw).hexdigest(),
                        compressed_bytes=len(compressed), compressed_sha256=hashlib.sha256(compressed).hexdigest(),
                        request_sha=value['request_sha'])
        (self.directory/'result.archive.json').write_text(json.dumps(metadata))
        (self.directory/'result.json').unlink()
        return value, metadata

    def test_archived_receipt_returns_identical_result_without_execution(self):
        value, _ = self.create_archive()
        with patch.object(process, 'execute_episode') as execute:
            self.assertEqual(remote.run_case(self.root, self.payload), value)
            execute.assert_not_called()
        self.assertNotIn('trace', value['result'])

    def test_corruption_never_becomes_permission_to_replay(self):
        self.create_archive()
        (self.directory/'result.json.gz').write_bytes(b'corrupt')
        with patch.object(process, 'execute_episode') as execute:
            with self.assertRaisesRegex(ValueError, 'changed archived'):
                remote.run_case(self.root, self.payload)
            execute.assert_not_called()

    def test_missing_archive_metadata_and_payload_changes_fail_closed(self):
        self.create_archive()
        with patch.object(process, 'execute_episode') as execute:
            with self.assertRaisesRegex(ValueError, 'collision'):
                remote.run_case(self.root, dict(self.payload, trace_retention='full'))
            (self.directory/'result.archive.json').unlink()
            with self.assertRaisesRegex(ValueError, 'unknown result'):
                remote.run_case(self.root, self.payload)
            execute.assert_not_called()

    def test_decompression_bound_and_plaintext_legacy_receipt(self):
        value, metadata = self.create_archive()
        metadata['original_bytes'] = receipts.MAX_RECEIPT_BYTES + 1
        (self.directory/'result.archive.json').write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, 'contract'):
            receipts.read_result(self.directory)
        (self.directory/'result.json').write_text(json.dumps(value))
        self.assertEqual(receipts.read_result(self.directory), value)

    def test_full_and_invalid_policy_checked_before_candidate(self):
        full = dict(valid=True, score=1, cleanup_confirmed=True, infrastructure_error=False,
                    queue_wait_s=0, trace=[{'step': 0}])
        with patch.object(process, 'execute_episode', return_value=full):
            value = remote.run_case(self.root, dict(self.payload, trace_retention='full'))
        self.assertEqual(value['result']['trace'], full['trace'])
        with patch.object(process, 'execute_episode') as execute:
            with self.assertRaisesRegex(ValueError, 'trace_retention'):
                remote.run_case(self.root, dict(self.payload, trace_retention='bad'))
            execute.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
