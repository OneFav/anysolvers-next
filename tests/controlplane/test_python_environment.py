import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from controlplane.host import Host


class PythonEnvironment(unittest.TestCase):
    def test_host_keeps_virtual_environment_identity_and_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);env=root/'venv'
            subprocess.run([sys.executable,'-m','venv','--without-pip',str(env)],check=True,capture_output=True)
            python=env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            site=subprocess.check_output([str(python),'-c','import sysconfig; print(sysconfig.get_path("purelib"))'],text=True).strip()
            (Path(site)/'anysolvers_environment_probe.py').write_text('VALUE = "venv-only"\n')
            config=dict(_base=str(root),worker_host=dict(id='test',python=str(python),state_root='state',work_root='projects',router_url='http://127.0.0.1:1',token_env='AS_ENV_TEST_TOKEN'))
            with patch.dict(os.environ,AS_ENV_TEST_TOKEN='fixture'):
                host=Host(config)
            self.assertEqual(host.options['python'],str(python.absolute()))
            report=json.loads(subprocess.check_output([host.options['python'],'-c','import sys,json,anysolvers_environment_probe as p; print(json.dumps(dict(prefix=sys.prefix,value=p.VALUE)))'],text=True))
            self.assertEqual(Path(report['prefix']),env)
            self.assertEqual(report['value'],'venv-only')


if __name__=='__main__':unittest.main()
