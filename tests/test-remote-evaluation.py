"""Deterministic remote receipt fixtures. No candidate or remote OS execution."""
import copy,hashlib,io,json,sys,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'packages/research-runtime/python')]
from modules.evaluation.queued_sandbox import remote,sandbox,process,measure
from modules.evaluation.queued_sandbox.volume import verify_archive
from research_runtime.docker import _configuration_checks,_create_args,InfrastructureError,LABEL

class RemoteCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()/'versions'/'fixture';self.root.mkdir(parents=True)
        content='def validate_input(case): pass\ndef reset(case): pass\ndef step(state, action): pass\ndef score_episode(state, trace, case): pass\n'
        assets={name:dict(content=data,sha=hashlib.sha256(data.encode()).hexdigest()) for name,data in [('eval.py',content),('base.py','# inert fixture'),('tests.json','{}')]}
        b=dict(benchmark_id='fixture',task_id='fixture',protocol=dict(kind='episode',max_steps=1,action_wall_s=1),evaluator_file='eval.py',baseline_file='base.py',tests_file='tests.json',support_files=[])
        self.payload=dict(request_id='a'*64,source='# inert source fixture',case={},image=remote.REMOTE_IMAGE,limits=dict(sandbox.DEFAULT_LIMITS),environment=dict(benchmark=b,assets=assets,signature=remote.signature(b,assets)))

    def test_process_backend_requires_authorization_and_never_dispatches_docker(self):
        compute=dict(executor='ssh-process',image=process.RUNTIME,
            remote=dict(host='compute-node',root='/root/as/versions/fixed',python='/usr/bin/python3'))
        with self.assertRaisesRegex(ValueError,'authorization'):remote.settings(compute)
        compute['process_authorization']='explicit-user-development-only'
        self.assertEqual(remote.settings(compute)['host'],'compute-node')
        payload={**self.payload,'executor':'ssh-process','image':process.RUNTIME,
                 'process_authorization':compute['process_authorization']}
        result=dict(valid=True,score=1,cleanup_confirmed=True,infrastructure_error=False,queue_wait_s=0,isolation_verified=False)
        with patch.object(process,'execute_episode',return_value=result) as execute, patch.object(sandbox,'execute_episode') as docker:
            first=remote.run_case(self.root,payload)
            self.assertEqual(first,remote.run_case(self.root,payload))
            self.assertFalse(first['result']['isolation_verified'])
            self.assertEqual(execute.call_count,1);docker.assert_not_called()

    def test_process_unknown_receipt_blocks_new_execution(self):
        payload={**self.payload,'executor':'ssh-process','image':process.RUNTIME,
                 'process_authorization':'explicit-user-development-only'}
        with patch.object(process,'execute_episode',side_effect=RuntimeError('lost SSH')) as execute:
            with self.assertRaisesRegex(RuntimeError,'lost SSH'):remote.run_case(self.root,payload)
            with self.assertRaisesRegex(ValueError,'unknown result'):remote.run_case(self.root,payload)
            self.assertEqual(execute.call_count,1)

    def test_preflight_uses_sealed_remote_compute_not_local_image(self):
        compute=dict(executor='ssh-process',image=process.RUNTIME,
            process_authorization='explicit-user-development-only',
            remote=dict(host='compute-node',root='/root/as/versions/fixed',python='/usr/bin/python3'))
        with patch.object(remote,'preflight',return_value={'executor':'ssh-process'}) as inspect, patch.object(sandbox,'preflight') as local:
            self.assertEqual(measure.preflight(compute),{'executor':'ssh-process'})
            inspect.assert_called_once_with(compute);local.assert_not_called()

    def test_process_final_data_rejected_before_loading_or_transport(self):
        cfg={'compute':{'executor':'ssh-process'}}
        state={'deliveries':{'dataset':{'holdout':{'manifest':{'purpose':'final'}}}}}
        with patch.object(remote,'execute') as dispatch:
            with self.assertRaisesRegex(ValueError,'development-only'):
                measure._inputs(self.root,cfg,state,'baseline','holdout')
            dispatch.assert_not_called()

    def test_receipt_replay_returns_original_without_second_execution(self):
        result=dict(valid=True,score=1,cleanup_confirmed=True,infrastructure_error=False,queue_wait_s=0)
        with patch.object(sandbox,'execute_episode',return_value=result) as execute:
            first=remote.run_case(self.root,self.payload)
            again=remote.run_case(self.root,self.payload)
            self.assertEqual(first,again);self.assertEqual(execute.call_count,1)
            changed=copy.deepcopy(self.payload);changed['source']='other'
            with self.assertRaisesRegex(ValueError,'collision'):remote.run_case(self.root,changed)

    def test_unknown_intent_never_reexecutes(self):
        with patch.object(sandbox,'execute_episode',side_effect=RuntimeError('process lost')) as execute:
            with self.assertRaises(RuntimeError):remote.run_case(self.root,self.payload)
            with self.assertRaisesRegex(ValueError,'unknown result'):remote.run_case(self.root,self.payload)
            self.assertEqual(execute.call_count,1)

    def test_assets_and_paths_are_verified_before_execution(self):
        bad=copy.deepcopy(self.payload);bad['environment']['assets']['eval.py']['content']='changed'
        with patch.object(sandbox,'execute_episode') as execute:
            with self.assertRaisesRegex(ValueError,'hash mismatch'):remote.run_case(self.root,bad)
            execute.assert_not_called()
        for path in ('/tmp/escape','../escape','a/../../escape'):
            with self.assertRaises(ValueError):remote.safe_path(self.root,path)

    def test_remote_arguments_are_not_shell_fragments(self):
        good=dict(executor='ssh-docker',image=remote.REMOTE_IMAGE,remote=dict(host='compute-node',root='/root/anysolvers/versions/abc',python='/usr/bin/python3'))
        self.assertEqual(remote.settings(good)['host'],'compute-node')
        for field,value in [('host','x; touch file'),('host','-oProxyCommand=x'),('root','/root/../etc'),('python','/bin/python;echo')]:
            bad=copy.deepcopy(good);bad['remote'][field]=value
            with self.assertRaises(ValueError):remote.settings(bad)

    def test_remote_relative_paths_anchor_to_ssh_home_and_quote_spaces(self):
        import subprocess,os
        # Real shell/OS fixture, no SSH, Docker or candidate execution. A tiny
        # trusted entry echoes the cwd and Python path chosen by the transport.
        home=self.root/'home with spaces';version=home/'deploy with spaces/versions/fixed'
        entry=version/'runtime/modules/evaluation/queued_sandbox/remote.py';entry.parent.mkdir(parents=True)
        entry.write_bytes(Path(remote.__file__).read_bytes())
        bin=home/'bin';bin.mkdir(parents=True)
        python=bin/'fixture-python';python.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD"\n');python.chmod(0o755)
        compute=dict(executor='ssh-process',image='ssh-process:v1',process_authorization='explicit-user-development-only',
            remote=dict(host='compute-node',root='deploy with spaces/versions/fixed',python='bin/fixture-python'))
        command=remote.shell_command(compute,'preflight')
        result=subprocess.run(['/bin/sh','-c',command],cwd='/',env=dict(os.environ,HOME=str(home)),capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),str(version))
        compute['remote']['root']='~/deploy with spaces/versions/fixed'
        self.assertEqual(remote.shell_command(compute,'preflight'),command)

    def test_remote_schema_accepts_transport_paths_in_every_config_form(self):
        import jsonschema
        schemas=json.loads((ROOT/'runtime/tools/schemas.json').read_text())
        found=[]
        def visit(value):
            if isinstance(value,dict):
                props=value.get('properties',{})
                if set(props)=={'host','root','python'}:found.append(value)
                for child in value.values():visit(child)
            elif isinstance(value,list):
                for child in value:visit(child)
        visit(schemas);self.assertEqual(len(found),5)
        for schema in found:
            for path in ('deploy/versions/fixed','~/deploy with spaces/fixed','/root/deploy/fixed'):
                jsonschema.validate(dict(host='compute-node',root=path,python='miniconda3/bin/python3'),schema)
            for bad in ('../escape','/root/../escape','~/../escape','-x','a;echo bad','$(secret)'):
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(dict(host='compute-node',root=bad,python='python3'),schema)

    def test_archive_bytes_and_members_fail_closed(self):
        expected={'solver.py':b'one','entrypoint.py':b'two'}
        def archive(items):
            output=io.BytesIO()
            with tarfile.open(fileobj=output,mode='w') as tar:
                for name,data in items:
                    info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
            return output.getvalue()
        self.assertEqual(set(verify_archive(archive(list(expected.items())),expected)),set(expected))
        for items in ([('solver.py',b'changed'),('entrypoint.py',b'two')],list(expected.items())+[('unexpected',b'')],list(expected.items())+[('solver.py',b'one')],[('../solver.py',b'one')]):
            with self.assertRaises(InfrastructureError):verify_archive(archive(items),expected)

    def test_volume_container_still_requires_every_execution_boundary(self):
        name='fixture';volume=name+'-code';limits=sandbox.DEFAULT_LIMITS
        info={'Name':'/'+name,'Image':remote.REMOTE_IMAGE,
          'Config':{'Labels':{LABEL:name},'Entrypoint':['python'],'Cmd':['-I','-B','/code/entrypoint.py'],'User':'65534:65534'},
          'HostConfig':{'ReadonlyRootfs':True,'NetworkMode':'none','NanoCpus':1000000000,'Memory':128*1024**2,'MemorySwap':128*1024**2,'PidsLimit':32,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],'Privileged':False,'IpcMode':'private','Init':True,'Ulimits':[{'Name':k,'Soft':v,'Hard':v} for k,v in [('cpu',2),('nofile',64),('core',0)]],'LogConfig':{'Type':'none'},'Tmpfs':{'/tmp':'rw,noexec,nosuid,nodev,size=16m,mode=1777'}},
          'Mounts':[{'Type':'volume','Name':volume,'Destination':'/code','RW':False,'Driver':'local'}]}
        self.assertTrue(all(_configuration_checks(info,remote.REMOTE_IMAGE,None,None,limits,name,code_volume=volume).values()))
        for field,value in [('NetworkMode','host'),('ReadonlyRootfs',False),('Memory',0)]:
            bad=copy.deepcopy(info);bad['HostConfig'][field]=value
            with self.assertRaises(InfrastructureError):_configuration_checks(bad,remote.REMOTE_IMAGE,None,None,limits,name,code_volume=volume)
        for field,value in [('RW',True),('Name','other-volume'),('Type','bind')]:
            bad=copy.deepcopy(info);bad['Mounts'][0][field]=value
            with self.assertRaises(InfrastructureError):_configuration_checks(bad,remote.REMOTE_IMAGE,None,None,limits,name,code_volume=volume)
        args=_create_args(name,None,None,remote.REMOTE_IMAGE,limits,code_volume=volume)
        self.assertFalse(any('type=bind' in a for a in args));self.assertIn('--read-only',args)
        with self.assertRaises(ValueError):_create_args(name,None,None,remote.REMOTE_IMAGE,limits,code_volume='unrelated')

if __name__=='__main__':unittest.main(verbosity=2)
