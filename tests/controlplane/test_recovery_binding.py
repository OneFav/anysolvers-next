import unittest
from controlplane.host import recovered_run_binding
class RecoveryBinding(unittest.TestCase):
 def test_old_batch_replay_preserves_explicit_migration(self):
  job={'execution_migration':{'from_run':'study/001','to_run':'study/002','reason':'explicit_user_requested_dsh_native_backend'}}
  self.assertEqual(recovered_run_binding(job,'study/001'),'study/002')
  self.assertEqual(recovered_run_binding(job,'study/002'),'study/002')
  with self.assertRaises(ValueError):recovered_run_binding(job,'other/001')
 def test_regular_recovery_stays_on_original_run(self):
  self.assertEqual(recovered_run_binding({},'study/001'),'study/001')
 def test_untrusted_or_cross_study_migration_rejected(self):
  for value in [{'from_run':'study/001','to_run':'other/002','reason':'explicit_user_requested_dsh_native_backend'},{'from_run':'study/001','to_run':'study/002','reason':'model-request'}]:
   with self.assertRaises(ValueError):recovered_run_binding({'execution_migration':value},'study/001')
