import tempfile
from pathlib import Path
import unittest
from controlplane.host import patch

class DshInheritance(unittest.TestCase):
    def test_account_reuses_native_store_but_sessions_stay_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);shared=root/'existing';launch=root/'isolated'/'launch.json'
            entries=patch(root,launch,dict(provider='deepseek-account',dsh_credentials_home=str(shared)),router=True)
            by_id={x['id']:x for x in entries if 'id' in x}
            self.assertEqual(by_id['credentials']['config']['dshHome'],str(shared))
            self.assertFalse(by_id['credentials']['disabled'])
            self.assertFalse(by_id['deepseek-account']['disabled'])
            self.assertEqual(by_id['session-persistence-jsonl']['config']['root'],str(launch.parent/'dsh-sessions'))

    def test_inheritance_is_explicit_and_requires_absolute_path(self):
        entries=patch('/tmp/source','/tmp/task/launch.json',dict(provider='deepseek-official'),router=True)
        self.assertNotIn('credentials',[x.get('id') for x in entries])
        with self.assertRaises(ValueError):patch('/tmp/source','/tmp/task/launch.json',dict(provider='deepseek-account',dsh_credentials_home='relative'),router=True)

if __name__=='__main__':unittest.main()
