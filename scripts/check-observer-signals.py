"""Opt-in real-model observer replay. Synthetic evidence, ZERO candidate/Feishu calls.

The model gets neutral payloads only; frozen expected behavior is retained in
rubric.json for separate assessment. No harness-generated judgments are submitted.
"""
import argparse,concurrent.futures,copy,hashlib,json,os,signal,subprocess,sys,threading,time
from pathlib import Path
from http.server import ThreadingHTTPServer

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'tests'))
from observer_signal_cases import cases,variant

def stop(process):
    if process.poll() is None:
        os.killpg(process.pid,signal.SIGTERM)
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

def launch(source,root,manifest,options,observer):
    root.mkdir(parents=True,exist_ok=True)
    path=root/'launch.json';atomic(path,manifest)
    overlay=root/'patch.json';atomic(overlay,patch(source,path,options,observer=observer,router=not observer))
    with (root/'process.log').open('ab') as log:
        return subprocess.Popen(options['dsh_command']+['--patch',str(overlay)],cwd=root,
            env=dict(os.environ,DSH_HOME=str(root/'dsh-home')),stdout=log,stderr=log,start_new_session=True)

def api(engine,cfg,source):
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(engine,cfg,source/'controlplane/web'))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server,f'http://127.0.0.1:{server.server_port}'

def main():
    global atomic,patch,handler
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live-model',action='store_true',required=True)
    p.add_argument('--config',default=str(REPO/'config/settings.json'))
    p.add_argument('--source',required=True,help='Immutable validated implementation directory')
    p.add_argument('--output',required=True)
    p.add_argument('--order',choices=['a','b'],default='a')
    p.add_argument('--cases',default='1,2,3,4,5,6')
    p.add_argument('--k-source',help='Existing real-Router K snapshot from this replay suite')
    p.add_argument('--parallel',type=int,choices=[1,2],default=2)
    p.add_argument('--timeout',type=int,default=240)
    args=p.parse_args();source=Path(args.source).resolve();root=Path(args.output).resolve()
    # Pin the API/observer implementation as well as the native plugin. The
    # mutable checkout supplies only this harness and the hashed input fixtures.
    sys.path.insert(0,str(source))
    from controlplane.config import load
    from controlplane.engine import Engine
    from controlplane.store import Store
    from controlplane.server import handler
    from controlplane.host import patch,native_id
    from controlplane.processes import atomic
    from controlplane.versions import content_digest
    import controlplane.observer
    if Path(controlplane.observer.__file__).resolve().parent.parent!=source:
        raise SystemExit('Observer API must load from the selected immutable implementation')
    if root.exists():raise SystemExit('Use a fresh output directory; live model runs are never overwritten')
    root.mkdir(parents=True)
    config=load(args.config);options=config['router_agent']
    selected=[variant(c,args.order) for c in cases() if int(c['id'][-2:]) in [int(s) for s in args.cases.split(',')]]
    atomic(root/'rubric.json',{c['id']:c['rubric'] for c in selected})
    atomic(root/'inputs.json',{c['id']:c['phases'] for c in selected})
    atomic(root/'execution.json',dict(source=str(source),source_digest=content_digest(source),fixture_digest=hashlib.sha256((REPO/'tests/observer_signal_cases.py').read_bytes()).hexdigest(),
        controlplane_source=str(Path(controlplane.observer.__file__).resolve()),provider=options['provider'],model=options['model'],order=args.order,actual_model=True,synthetic_evidence=True,sandbox_evaluations=0,feishu_sends=0,parallel=args.parallel))
    if args.k_source:
        prior=json.loads(Path(args.k_source).read_text());people=prior['people']
        atomic(root/'router-k-origin.json',dict(copied_from=str(Path(args.k_source).resolve()),sha256=hashlib.sha256(Path(args.k_source).read_bytes()).hexdigest()))
    else:
        kr=root/'router';kr.mkdir()
        env_key='AS_OBSERVER_SIGNAL_OPERATOR';os.environ[env_key]=__import__('secrets').token_urlsafe(32)
        cfg=dict(_base=str(kr),operator_token_env=env_key,observer=dict(enabled=True,state_root=str(kr/'observer')),
            feishu=dict(enabled=False),sources={'local-validation':dict(chats=['desktop-local'],allowed_users=['desktop-owner'],allowed_stages=[])})
        e=Engine(Store(kr/'router.sqlite3'),cfg);server,url=api(e,cfg,source)
        e.receive(dict(id='method-goal',source='local-validation',thread='method-goal',actor='desktop-owner',channel=dict(chat_id='desktop-local',message_id='method-goal'),
            text='真实用户原话：我关注于可以扩大研究的足够新且有效方法的苗头。请将此登记为 research_owner 的明确 research_goals，保持 expectations 为空，不制造算法胜负预期，也不推测用户已知哪些方法。当前只做 observer 模型回放，输入都是有标记的合成记录；应判断在给定记录条件下哪些线索值得后续研究，不能称其为真实科学测量。请将这种回放边界写入 shared_background。所有结果留在本地，不发飞书，不创建 Worker。请用 router_observer 更新当前 K，再用 router_decide 回复或忽略此条即可。'))
        manifest=dict(role='router',observer_enabled=True,router_url=url,token_env=env_key,native_id=native_id('signal-router'),workspace=str(kr),local_state=str(kr/'session'),
            provider=options['provider'],model=options['model'],reasoning_effort=options.get('reasoning_effort','high'))
        process=launch(source,kr,manifest,options,False)
        try:
            deadline=time.monotonic()+args.timeout
            while time.monotonic()<deadline:
                if e.observer.k()['revision'] and e.store.get('decisions','method-goal'):break
                if process.poll() is not None:raise RuntimeError('Router exited before creating current K')
                time.sleep(.5)
            else:raise TimeoutError('Router K model timeout')
            people=e.observer.k()['people']
            assert 'research_owner' in people and people['research_owner']['research_goals']
            assert not people['research_owner']['expectations'],'Router invented a directional expectation'
        finally:stop(process);server.shutdown();server.server_close()
    atomic(root/'router-k.json',dict(people=people))
    results=[];lock=threading.Lock()
    def run(case):
        directory=root/case['id'];directory.mkdir()
        cfg=dict(_base=str(directory),observer=dict(enabled=True,state_root=str(directory/'observer'),batching=dict(window_s=.2,max_session_batches=2)),feishu=dict(enabled=False))
        engine=Engine(Store(directory/'router.sqlite3'),cfg);o=engine.observer
        # Deterministic fixture initialization copies the actual Router's current
        # K. The observer itself only has read/commit capabilities, never K writes.
        o.replace_k(0,copy.deepcopy(people))
        for job in case['jobs']:
            o.register_job(dict(id=job,stage='research',project_id='synthetic-replay',host_id='fixture',state='running',progress=dict(run_id=job+'/001')))
        if len(case['jobs'])>1:o.configure(comparisons=[dict(id='comparison',jobs=case['jobs'],expectation='Explore whether a mechanism has transferable support; no direction of superiority is assumed',metrics=['paired quality at fixed objective calls'],conditions=['check independent inputs, source mechanisms, equal budgets, development-only'])])
        session=o.open_session('default');server,url=api(engine,cfg,source)
        manifest=dict(role='observer',observer_id='default',session=session,router_url=url,workspace=str(directory),local_state=str(directory/'session'),
            provider=options['provider'],model=options['model'],reasoning_effort=options.get('reasoning_effort','high'),max_context_bytes=240000)
        process=launch(source,directory,manifest,options,True);snapshots=[];error=None
        try:
            for phase,records in enumerate([*case['phases'],None],1):
                if records is None:
                    for job in case['jobs']:o.register_job(dict(id=job,stage='research',project_id='synthetic-replay',host_id='fixture',state='completed',output=dict(run_id=job+'/001')))
                else:o.ingest(records)
                with o.store.connect() as c:maxima=dict(c.execute('SELECT job,MAX(seq) FROM evidence GROUP BY job'))
                deadline=time.monotonic()+args.timeout
                while time.monotonic()<deadline:
                    state=o.store.get('observers','default')
                    if not state['active'] and all(state['cursors'].get(j,0)==seq for j,seq in maxima.items()):break
                    if process.poll() is not None:raise RuntimeError('Observer exited before committing')
                    time.sleep(.5)
                else:raise TimeoutError('Observer phase timeout; records and uncommitted judgment retained')
                received=o.router_inbox(engine)
                snapshots.append(dict(phase=phase,cursors=state['cursors'],notes=state['notes'],clues=state['clues'],
                    reports=received['reports'],commits=o.store.list('observer_commits')))
                atomic(directory/'observations.json',snapshots)
            count=len(o.store.list('observer_commits'));time.sleep(2)
            assert len(o.store.list('observer_commits'))==count,'Idle observer kept committing without new evidence'
        except Exception as exc:error=type(exc).__name__+': '+str(exc)
        finally:stop(process);server.shutdown();server.server_close()
        value=dict(case_id=case['id'],order=args.order,error=error,phases=len(snapshots),report_count=len(o.store.list('observer_reports')),
            expected_signal=case['rubric']['expected_signal'],artifacts=str(directory/'observations.json'))
        with lock:
            results.append(value);atomic(root/'summary.json',dict(results=results,synthetic_evidence=True,actual_model=True,complete=len(results)==len(selected)))
            print(json.dumps(value,ensure_ascii=False),flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        for done in concurrent.futures.as_completed([pool.submit(run,c) for c in selected]):done.result()
    if any(r['error'] for r in results):raise SystemExit(1)

if __name__=='__main__':main()
