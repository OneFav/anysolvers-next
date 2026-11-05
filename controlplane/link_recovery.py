"""Reconnect a live attempt without replacing it or replaying an uncertain operation."""
import json
import math
from .communication import subject

TRANSPORT_STATES=('starting','running','waiting_input','waiting_provider','reconciliation')
PROVIDER_FAULTS=('network','provider_quota','provider_rate_limit','model_unavailable')


def previous_state(tx,job):
    # Also repairs incidents recorded before connection_loss was introduced.
    row=tx.connection.execute("""SELECT type,body FROM events
      WHERE type IN ('worker.started','worker.waiting_input','worker.fault','worker.recovered')
        AND json_extract(body,'$.job_id')=? AND json_extract(body,'$.attempt_id')=?
      ORDER BY seq DESC LIMIT 1""",(job['id'],job['attempt_id'])).fetchone()
    if row:
        kind,data=row[0],json.loads(row[1]).get('data',{})
        if kind=='worker.waiting_input':return 'waiting_input'
        if kind=='worker.fault':return 'waiting_provider' if data.get('kind') in PROVIDER_FAULTS else 'reconciliation'
        return 'running'
    before=(job.get('connection_loss') or {}).get('previous_state')
    return before if before in TRANSPORT_STATES else 'reconciliation'


def lost(engine,tx,job):
    if job['state']!='host_lost':
        prior=job['state']
        job['state']='host_lost'
        engine._incident(tx,job,'host_lost','执行端失联。当前显示最后已知进度；尚不能确认研究进程是否停止。')
        incident=tx.get('incidents',job['id']+':host_lost')
        job['connection_loss']=dict(attempt_id=job['attempt_id'],episode=incident['episode'],lost_at=incident['opened_at'],previous_state=prior)
    else:
        engine._incident(tx,job,'host_lost','执行端失联。当前显示最后已知进度；尚不能确认研究进程是否停止。')
    job['connection_recovery']=dict(phase='offline',detail='等待执行端重新连接并核验原进程。')
    tx.put('jobs',job['id'],job)


def recover_live(engine,tx,job,attempt,record):
    incident=tx.get('incidents',job['id']+':host_lost')
    if not incident or incident['state']!='open' or job['state'] not in (*TRANSPORT_STATES,'host_lost'):return
    now=engine.clock();options=engine.config.get('recovery',{})
    freshness=options.get('native_heartbeat_timeout_s',15)
    stable=options.get('stable_connection_s',6)
    age=record.get('native_heartbeat_age_s')
    contact=attempt.get('last_contact_at',0)
    verified=(record.get('alive') is True and record.get('native_alive') is True
              and record.get('native_pid')==attempt.get('pid') and attempt.get('pid') is not None
              and record.get('native_id')==attempt.get('native_id') and bool(attempt.get('native_id'))
              and type(age) in (int,float) and math.isfinite(age) and 0<=age<=freshness
              and contact>=incident['opened_at'] and 0<=now-contact<=freshness)
    recovery=job.get('connection_recovery') or {}
    if not verified:
        job['connection_recovery']=dict(phase='verifying',detail='主机已连接，等待原生 Worker 心跳、进程身份与通信核验。')
        tx.put('jobs',job['id'],job);return
    since=recovery.get('verified_since',now)
    # A new long heartbeat gap starts a new stability window even without a monitor tick.
    if now-recovery.get('checked_at',now)>freshness:since=now
    recovery=dict(phase='verifying',detail='原进程已确认，等待连接稳定。',verified_since=since,checked_at=now)
    if now-since<stable:
        job['connection_recovery']=recovery;tx.put('jobs',job['id'],job);return
    pending=engine._pending(tx,job['id'])
    if pending:
        recovery.update(phase='waiting_operations',detail=f'连接已恢复，等待 {len(pending)} 个在途或未决操作的原始结果；不会重复执行。',pending_count=len(pending))
        job['connection_recovery']=recovery;tx.put('jobs',job['id'],job)
        if now-since>=options.get('pending_notice_s',30):
            engine._notify(tx,job,f"recovery-pending:{incident['episode']}",
                f"{subject(job)}的连接已恢复，仍在等待原执行结果，暂未继续。",True,purpose='recovery')
        return
    restored=previous_state(tx,job)
    key=f"connection-restored:{attempt['id']}:{incident['episode']}"
    job.update(state=restored,updated_at=now,connection_recovery=dict(phase='restored',restored_at=now,previous_state=restored,episode=incident['episode']))
    # A running native turn resumes through its existing gate. An idle one needs one durable input.
    resume=(restored=='running' and record.get('agent_status')=='idle' and not job.get('paused')
            and not job.get('resume_requested') and options.get('auto_resume',True))
    if resume:
        engine._input(tx,job,key,dict(kind='connection_recovered',instruction='执行端连接已自动恢复，原进程、会话和未决操作已核验。读取 next_context，沿用原任务和原批次继续。先检查已提交成果，不新建替代任务、不重测已完成评测、不重置预算。若仍存在此前已确认的业务或配置错误，不要反复重试；通过 next_ask 明确报告阻塞并等待修复。',attempt_id=attempt['id'],episode=incident['episode']))
    tx.put('jobs',job['id'],job)
    incident.update(state='resolved',resolved_at=now,resolution='same_native_process_reconnected')
    tx.put('incidents',incident['id'],incident)
    states={'running':'已提交原任务续接' if resume else '已恢复执行准入','waiting_input':'仍等待你的补充信息','waiting_provider':'仍等待模型服务恢复','reconciliation':'仍需核对原执行问题','starting':'原任务启动中'}
    detail='仍保持人工暂停' if job.get('paused') else states[restored]
    engine._notify(tx,job,key,f"{subject(job)}的执行端连接已恢复，{detail}。",True,purpose='recovery')
    engine._event(tx,'worker.connection_restored',dict(job_id=job['id'],attempt_id=attempt['id'],episode=incident['episode'],restored_state=restored,resume_input_queued=resume),key)


def restarted(engine,tx,job,attempt):
    """A new attempt is admitted only by the existing durable recovery protocol."""
    if not job.get('recovery_from'):return
    for kind in ('host_lost','worker_exit'):
        incident=tx.get('incidents',job['id']+':'+kind)
        if incident and incident['state']=='open':
            incident.update(state='resolved',resolved_at=engine.clock(),resolution='verified_same_session_restart')
            tx.put('incidents',incident['id'],incident)
    job.pop('restart_waiting_input',None)
    job['connection_recovery']=dict(phase='restored',restored_at=engine.clock(),mode='same_session_new_attempt')
    engine._notify(tx,job,'restarted:'+attempt['id'],f"{subject(job)}已续接原会话。",True,purpose='recovery')
