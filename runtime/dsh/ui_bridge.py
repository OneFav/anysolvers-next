"""Owner UI adapter. Keeps pinned tools untouched; never runs models or solvers."""
import contextlib
import copy
import datetime as dt
import fcntl
import json
import math
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yaml
from tools import common as c, dsh_assets as assets, dsh_bridge as bridge, dsh_launch as launch, study
from tools.configuration import read_base, normalize, frozen_base
from tools.module_runtime import compatible, read_module, resolve_bindings, invoke
from tools.execution import validate_execution
from dsh.engine_access import snapshot as project_snapshot, status as project_status

FIELDS = {
    'researchers': ('budgets', 'researchers'),
    'rounds_per_researcher': ('budgets', 'rounds_per_researcher'),
    'session_wall_s': ('budgets', 'session_wall_s'),
    'run_wall_s': ('budgets', 'run_wall_s'),
    'evaluation_calls': ('budgets', 'evaluation_calls'),
    'max_parallel_evaluations': ('compute', 'max_parallel_evaluations'),
    'evaluation_wall_s': ('compute', 'evaluation_wall_s'),
    **{key: ('compute', 'limits', key) for key in ('wall_s', 'cpu_s', 'memory_mb', 'pids', 'output_bytes', 'tmp_mb')},
}


def get_values(cfg):
    result = {}
    for key, path in FIELDS.items():
        value = cfg
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        result[key] = value
    return result


def base_profile(study_id):
    directory, initialized = study.load(c.identifier(study_id), require_sealed=True)
    runs = launch._launch_runs(directory, study_id)
    if runs:
        _, cfg, state = project_snapshot(runs[-1])
        raw = frozen_base(state['configuration'])
        # Keep the last batch's effective resource overrides as defaults.
        for key in ('budgets', 'compute', 'agent', 'final'):
            raw[key] = copy.deepcopy(cfg[key])
        source = 'runs/' + runs[-1] + '/run.yaml'
    else:
        source = initialized.get('source', {}).get('path')
        if not source:
            raise ValueError('初始化缺少配置来源，请先为研究指定基础配置。')
        data = assets.read(c.ROOT, source)
        if assets.sha(data) != initialized['source']['sha']:
            raise ValueError('初始化来源配置已改变，请先核对该研究的配置。')
        raw = read_base(source)
    cfg = normalize(raw)
    revision = c.digest({'initialization': initialized, 'base': raw, 'runs': runs})
    return directory, initialized, runs, raw, cfg, source, revision


def configuration(study_id):
    directory, init, runs, _, cfg, source, revision = base_profile(study_id)
    saved = None
    drafts = c.contained(directory.parent, 'ui-drafts')
    claim_path = c.contained(directory.parent, 'ui-drafts/claim-' + c.digest(runs)[:24] + '.json')
    if drafts.exists() and not claim_path.exists():
        candidates = sorted((p for p in drafts.glob('*.json') if not p.name.startswith('claim-')),
                            key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for candidate in candidates[:100]:
            meta = c.read_json(c.contained(drafts, candidate.name))
            if meta.get('revision') != revision or meta.get('previous_runs') != runs:
                continue
            path = meta['config']
            draft(study_id, path)
            cfg = normalize(read_base(path))
            saved = dict(config=path, values=get_values(cfg), command=f'/anysolvers new {study_id} --config {path}')
            break
    return dict(study_id=study_id, revision=revision, existing_runs=runs, source=source,
                task_id=init['task']['task_id'], values=get_values(cfg),
                model=cfg['agent']['profiles']['research'], image=cfg['compute']['image'],
                executor=cfg['compute']['executor'], saved=saved)


def apply_values(raw, values):
    if not isinstance(values, dict) or set(values) != set(FIELDS):
        raise ValueError('参数字段不完整或含未知字段。')
    raw = copy.deepcopy(raw)
    for key, path in FIELDS.items():
        value = values[key]
        if not (key == 'evaluation_calls' and value is None):
            if type(value) is not int or value < 1 or value > 2**31 - 1:
                raise ValueError(key + ' 必须是正整数。')
        obj = raw
        for part in path[:-1]:
            obj = obj[part]
        obj[path[-1]] = value
    cfg = normalize(raw)
    resolved = copy.deepcopy(cfg)
    resolved['modules'] = resolve_bindings(cfg['modules'])
    validate_execution(resolved)
    return raw, cfg


def assert_settled(runs):
    for rid in runs:
        status = project_status(rid)
        if status.get('pending_requests') or any(s.get('status') not in ('completed', 'failed') for s in status.get('sessions', [])):
            raise ValueError('已有批次仍有活动会话或未决评测，请先在主会话核对。')


def save(study_id, payload):
    directory, init, runs, raw, cfg, source, revision = base_profile(study_id)
    if payload.get('revision') != revision:
        raise ValueError('研究或配置已更新，请刷新后重新调整参数。')
    raw, cfg = apply_values(raw, payload.get('values'))
    for slot in ('task', 'datasets', 'ideas'):
        b = cfg['modules'][slot]
        compatible(init['modules'][slot], read_module(b['use'], b.get('params')))
    for record in init['deliveries']['dataset'].values():
        cap = cfg['budgets']['max_cases_per_dataset']
        data = launch._sealed_asset(directory, record, record['manifest']['cases_file'], limit=16*1024*1024)
        if cap is not None and sum(bool(line.strip()) for line in data.splitlines()) > cap:
            raise ValueError('封存数据超过配置中的实例数量上限。')
    # Unique immutable config per explicit save; a later batch never alters a pinned base.
    draft_id = uuid.uuid4().hex
    path = f'configs/ui/{study_id}-{draft_id}.yaml'
    target = c.contained(c.ROOT, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False).encode()
    with target.open('xb') as stream:
        stream.write(encoded)
    meta = dict(study_id=study_id, config=path, config_sha=assets.sha(encoded),
                revision=revision, previous_runs=runs, initialization_sha=c.sha(directory/'manifest.json'))
    metadata = c.contained(directory.parent, 'ui-drafts/' + draft_id + '.json')
    metadata.parent.mkdir(exist_ok=True)
    with metadata.open('x') as stream:
        json.dump(meta, stream, ensure_ascii=False)
    return dict(config=path, values=get_values(cfg), command=f'/anysolvers new {study_id} --config {path}', saved=True)


def draft(study_id, path):
    import re
    if not isinstance(path, str) or not re.fullmatch(r'configs/ui/' + re.escape(study_id) + r'-[a-f0-9]{32}\.yaml', path):
        raise ValueError('请选择界面保存的本次运行配置。')
    directory, init = study.load(study_id, require_sealed=True)
    draft_id = Path(path).stem.rsplit('-', 1)[1]
    meta = c.read_json(c.contained(directory.parent, 'ui-drafts/' + draft_id + '.json'))
    if meta['study_id'] != study_id or meta['config'] != path or assets.sha(assets.read(c.ROOT, path)) != meta['config_sha']:
        raise ValueError('保存的配置发生变化，请重新保存。')
    if c.sha(directory/'manifest.json') != meta['initialization_sha']:
        raise ValueError('研究初始化发生变化。')
    runs = launch._launch_runs(directory, study_id)
    if runs != meta['previous_runs']:
        raise ValueError('保存后已产生新批次，请刷新并检查记录，勿重复启动。')
    return directory, init, meta, draft_id


def profile(study_id, path):
    directory, init, meta, _ = draft(study_id, path)
    assert_settled(meta['previous_runs'])
    cfg = normalize(read_base(path))
    ideas = sorted({v['idea_id'] for r in init['deliveries']['ideas'].values() for v in r['manifest']['ideas']})
    if not ideas:
        raise ValueError('研究缺少封存的初始想法。')
    datasets = []
    for key, record in sorted(init['deliveries']['dataset'].items()):
        data = launch._sealed_asset(directory, record, record['manifest']['cases_file'], limit=16*1024*1024)
        datasets.append(dict(dataset_id=key, purpose=record['manifest']['purpose'], cases=sum(bool(x.strip()) for x in data.splitlines())))
    return dict(schema_version=1, study_id=study_id, config=path, config_sha=meta['config_sha'],
                task_id=init['task']['task_id'], assignments=[dict(actor='r'+str(i+1), idea=ideas[i % len(ideas)]) for i in range(cfg['budgets']['researchers'])],
                budgets=cfg['budgets'], compute=cfg['compute'], research_model=cfg['agent']['profiles']['research'],
                final_permitted=cfg['final']['authorized'], datasets=datasets, existing_runs=[], selected_run=None,
                previous_runs=meta['previous_runs'], new_batch=True, next_action='start')


def claim(study_id, path, session):
    directory, _, meta, draft_id = draft(study_id, path)
    if not isinstance(session, str) or not session or len(session) > 200:
        raise ValueError('启动需要真实主会话标识。')
    # One durable claim per observed batch list; competing pages cannot enqueue two runs.
    claim_id = c.digest(meta['previous_runs'])[:24]
    target = c.contained(directory.parent, 'ui-drafts/claim-' + claim_id + '.json')
    record = dict(config=path, session_id=session, created_at=c.utc(), previous_runs=meta['previous_runs'])
    try:
        with target.open('x') as stream:
            json.dump(record, stream, ensure_ascii=False)
    except FileExistsError:
        raise ValueError('该研究已有启动请求。请查看对应主会话；投递状态不明时不自动重试。')
    return record


def evaluation_rows(directory, cfg, state):
    """Join sealed requests to their actual submitter, never infer ownership from IDs."""
    records = invoke(cfg, 'research', 'audit', directory, cfg, state)
    requests = {}
    for record in records.values():
        if record['kind'] == 'request':
            eid = record['payload']['evaluation_id']
            if eid in requests:
                raise ValueError('评测关联了多个提交请求。')
            requests[eid] = record
    sessions = {s['session_id']: s for s in state['sessions']}
    rows = []
    for eid in dict.fromkeys([*state['evaluations'], *requests]):
        request = requests.get(eid)
        if eid in state['evaluations']:
            value = bridge._summary(directory, state, eid)
        else:
            payload = request['payload']
            response = records.get(request['record_id'] + '-response')
            value = dict(evaluation_id=eid, candidate_id=payload['candidate_id'],
                         dataset_id=payload['dataset_id'], probe=payload.get('probe', False),
                         status=response['payload'].get('status', 'failed') if response else 'queued')
        dataset = state['deliveries']['dataset'].get(value['dataset_id'])
        if not dataset:
            raise ValueError('评测数据集未登记。')
        purpose = dataset['manifest']['purpose']
        if value.get('dataset_purpose', purpose) != purpose:
            raise ValueError('评测数据集用途不一致。')
        candidate = state['deliveries']['candidate'].get(value['candidate_id'])
        if value['candidate_id'] != 'baseline' and not candidate:
            raise ValueError('评测候选未封存。')
        if candidate and value.get('source_sha') and value['source_sha'] != candidate['files'][candidate['manifest']['source_file']]:
            raise ValueError('评测候选源码不一致。')
        if request:
            if any(request['payload'][key] != value[key] for key in ('candidate_id', 'dataset_id')):
                raise ValueError('评测与提交请求不一致。')
            session = sessions.get(request['session_id'])
            if not session or (candidate and candidate['manifest']['session_id'] != request['session_id']):
                raise ValueError('评测所属会话不一致。')
        else:
            # Administrative / legacy measurements have no researcher submission record.
            session = None
        number = value.get('score')
        eligible = (value.get('status') == 'completed' and value.get('all_valid') is True
                    and type(number) in (int, float) and math.isfinite(number)
                    and not value.get('probe') and not value.get('infrastructure_error')
                    and value.get('cleanup_confirmed') is True and value.get('total_cases', 0) > 0
                    and value.get('completed_cases') == value['total_cases'])
        row = {key: value.get(key) for key in ('evaluation_id', 'candidate_id', 'dataset_id', 'status',
               'completed_cases', 'total_cases', 'valid_cases', 'all_valid', 'probe')}
        row.update(dataset_purpose=purpose, score=number if eligible else None, eligible=eligible,
                   actor=session['actor'] if session else None,
                   session_id=session['session_id'] if session else None,
                   generation=session['generation'] if session else None,
                   submitted_at=request['at'] if request else value.get('started_at'),
                   time_source='submitted' if request else 'started')
        rows.append(row)
    def order(row):
        timestamp = row['submitted_at']
        return (dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp() if timestamp else float('inf'), row['evaluation_id'])
    return sorted(rows, key=order)


def progress(run_id, offset=0, final_offset=0):
    if any(type(value) is not int or value < 0 for value in (offset, final_offset)):
        raise ValueError('无效的分页位置。')
    # Share the existing writer lease; browsing must not create a lock or other run artifact.
    with contextlib.ExitStack() as stack:
        try:
            lease = stack.enter_context((c.run_path(run_id) / '.mutation.lock').open('rb'))
        except FileNotFoundError:
            lease = None
        if lease:
            fcntl.flock(lease, fcntl.LOCK_SH)
        return _progress(run_id, offset, final_offset)


def _progress(run_id, offset, final_offset):
    status = bridge._run_status(run_id)
    directory, cfg, state = c.load_run(run_id, read_only=True)
    rows = evaluation_rows(directory, cfg, state)
    development = [r for r in rows if r['dataset_purpose'] != 'final']
    final = [r for r in rows if r['dataset_purpose'] == 'final']
    page, final_page = development[offset:offset+50], final[final_offset:final_offset+50]
    # Chart history is independent of table pagination; final scores never enter development curves.
    chart_keys = ('evaluation_id', 'candidate_id', 'dataset_id', 'actor', 'session_id', 'generation',
                  'submitted_at', 'status', 'score', 'eligible', 'probe')
    chart = dict(metric=cfg['task']['objective']['name'], direction=cfg['task']['objective']['direction'],
                 datasets=[dict(id=key, purpose=record['manifest']['purpose'])
                           for key, record in sorted(state['deliveries']['dataset'].items())
                           if record['manifest']['purpose'] != 'final'],
                 evaluations=[{k: row[k] for k in chart_keys} for row in development])
    # Whole-study score quality is not an estimated completion percentage.
    sessions = state['sessions']
    planned = cfg['budgets']['researchers'] * cfg['budgets']['rounds_per_researcher']
    completed = sum(s['status'] == 'completed' for s in sessions)
    created = dt.datetime.fromisoformat(cfg['created_at'])
    remain = max(0, cfg['budgets']['run_wall_s'] - c.available_wall_s(cfg,state))
    from dsh.terminal_bridge import status_data
    terminal = status_data(directory, cfg, state)
    return dict(run_id=run_id, phase=status['phase'], sessions=sessions, research_time=status['research_time'],
                pending_requests=status['pending_requests'], selection=state['selection'], terminal=terminal,
                planned_sessions=planned, completed_sessions=completed,
                failed_sessions=sum(s['status']=='failed' for s in sessions),
                missing_sessions=max(0, planned-len(sessions)),
                remaining_evaluations=status['remaining_evaluations'], evaluation_limit=cfg['budgets']['evaluation_calls'],
                remaining_run_s=round(remain), final_coverage=status['final_coverage'],
                chart=chart, evaluations=page, total_evaluations=len(rows), total_development_evaluations=len(development), offset=offset,
                next_offset=offset+len(page) if offset+len(page)<len(development) else None,
                final_evaluations=final_page, total_final_evaluations=len(final), final_offset=final_offset,
                final_next_offset=final_offset+len(final_page) if final_offset+len(final_page)<len(final) else None,
                values=get_values(cfg), model=cfg['agent']['profiles']['research'], image=cfg['compute']['image'],
                reports=[dict(id=key, manifest_path=r['manifest_path']) for key,r in state['deliveries']['report'].items()],
                updated_at=c.utc())


def command(request):
    if not isinstance(request, dict) or set(request)-{'schema_version','operation','payload','study_id','run_id'} or type(request.get('schema_version')) is not int or request['schema_version'] != 1:
        raise ValueError('invalid UI request')
    op, payload = request.get('operation'), request.get('payload', {})
    allowed = {'ui-configuration': set(), 'ui-save': {'revision','values'}, 'ui-launch-profile': {'config'},
               'ui-claim': {'config','session'}, 'ui-progress': {'offset', 'final_offset'}}
    if op not in allowed or not isinstance(payload, dict) or set(payload)-allowed[op]:
        raise ValueError('invalid UI operation or fields')
    if op == 'ui-progress':
        if 'study_id' in request: raise ValueError('progress expects a run')
        return progress(bridge._run_id(request), payload.get('offset',0), payload.get('final_offset',0))
    if 'run_id' in request: raise ValueError('configuration expects a study, never an existing run')
    sid = bridge._study_id(request)
    if op == 'ui-configuration': return configuration(sid)
    if op == 'ui-save': return save(sid, payload)
    if op == 'ui-launch-profile': return profile(sid, payload.get('config'))
    if op == 'ui-claim': return claim(sid, payload.get('config'), payload.get('session'))


def response(request):
    try:
        return dict(schema_version=1, ok=True, value=command(request))
    except Exception as error:
        message = str(error).replace(str(c.ROOT), '<repository>')[:1000]
        return dict(schema_version=1, ok=False, error=dict(code='UI_REQUEST', message=message))


if __name__ == '__main__':
    try:
        raw = sys.stdin.buffer.read(bridge.MAX_REQUEST_BYTES+1)
        if len(raw)>bridge.MAX_REQUEST_BYTES: raise ValueError('request too large')
        request=c.parse_json(raw.decode())
        with contextlib.redirect_stdout(sys.stderr): result=response(request)
    except Exception:
        result=dict(schema_version=1,ok=False,error=dict(code='INVALID_JSON',message='Invalid UI request'))
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    sys.exit(0 if result['ok'] else 2)
