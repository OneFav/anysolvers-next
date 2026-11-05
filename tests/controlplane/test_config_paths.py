import json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from controlplane.config import load,local_path,executable,command
from controlplane.host import Host

class ConfigPaths(unittest.TestCase):
    def test_relocated_config_and_different_cwd_keep_relative_roots_and_venv(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()/'original project';root.mkdir();(root/'config').mkdir()
            subprocess.run([sys.executable,'-m','venv','--without-pip',str(root/'.venv')],check=True,capture_output=True)
            python='.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python'
            cfg=dict(schema_version=1,path_base='..',state_db='.state/router.sqlite3',
                worker_host=dict(id='fixture',python=python,state_root='.state/host',work_root='projects',router_url='http://127.0.0.1:1',token_env='AS_PATH_FIXTURE',dsh_command=['node','bin/entry.mjs','--profile','headless']),
                feishu=dict(cli_command=['.state/tools/cli'],cli_config_dir='.state/feishu-cli'))
            (root/'config/settings.json').write_text(json.dumps(cfg))
            moved=root.with_name('relocated project');root.rename(moved)
            old=Path.cwd()
            try:
                os.chdir(temp)
                loaded=load(moved/'config/settings.json')
                with patch.dict(os.environ,AS_PATH_FIXTURE='fixture'):host=Host(loaded)
                self.assertEqual(host.options['python'],str(moved/python))
                self.assertEqual(host.work,moved/'projects')
                self.assertEqual(local_path(loaded,loaded['state_db']),moved/'.state/router.sqlite3')
                self.assertEqual(loaded['worker_host']['dsh_command'][1],str(moved/'bin/entry.mjs'))
                self.assertEqual(loaded['worker_host']['dsh_command'][-1],'headless')
                self.assertEqual(loaded['feishu']['cli_command'],[str(moved/'.state/tools/cli')])
                prefix=subprocess.check_output([host.options['python'],'-c','import sys; print(sys.prefix)'],text=True).strip()
                self.assertEqual(Path(prefix),moved/'.venv')
            finally:os.chdir(old)

    def test_home_paths_and_code_arguments_are_not_blindly_rewritten(self):
        cfg={'_base':'/tmp/fixture'}
        self.assertEqual(local_path(cfg,'~/.dsh'),Path.home()/'.dsh')
        argv=command(cfg,['node','-e','console.log("a/b")'])
        self.assertEqual(argv[1:],['-e','console.log("a/b")'])
        self.assertEqual(executable(cfg,'bin/python'),'/tmp/fixture/bin/python')
        self.assertEqual(command(cfg,['node','--config','settings.json'])[2],str(Path('/tmp/fixture/settings.json').resolve()))

if __name__=='__main__':unittest.main()
