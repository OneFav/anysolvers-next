"""Workspace-local /as-research admission. No model or candidate execution here."""
import copy
import json
from pathlib import Path
import re
import yaml
from tools import common as c, study, run, dsh_launch as launch
from tools.module_runtime import compatible, resolve_bindings
from tools.execution import validate_execution
from dsh import ui_bridge as ui


def settings_path(sid):
    return c.contained(c.ROOT, '.anysolvers/implementations/anysolvers/ui/' + c.identifier(sid) + '.json')


def configuration(sid):
    directory, initialized, runs, raw, cfg, source, revision = ui.base_profile(sid)
    # First use also adopts the parameters saved by the previous UI.
    old = ui.configuration(sid)
    values = old['values']
    path = settings_path(sid)
    if not path.exists():
        path=c.contained(c.ROOT,'.anysolvers/ui/'+c.identifier(sid)+'.json')
    if path.exists():
        values = c.read_json(path)['values']
    raw, cfg = ui.apply_values(raw, values)
    revision = c.digest(dict(base=revision, values=values))
    return dict(old, revision=revision, values=values,
                saved=dict(config='工作区已保存参数', values=values, command='/as-research ' + sid)), raw, initialized


def save(sid, payload):
    current, raw, _ = configuration(sid)
    if payload.get('revision') != current['revision']:
        raise ValueError('配置已更新，请刷新后保存；本次未覆盖新配置。')
    _, cfg = ui.apply_values(raw, payload.get('values'))
    path = settings_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    with study._lock(path.parent):
        latest, _, _ = configuration(sid)
        if latest['revision'] != payload['revision']:
            raise ValueError('配置已更新，请刷新后保存。')
        c.write_json(path, dict(values=ui.get_values(cfg), saved_at=c.utc()))
    selected = c.contained(c.ROOT, '.anysolvers/selected-study.json')
    c.write_json(selected, dict(study_id=sid))
    return configuration(sid)[0]['saved']


def preflight(sid=None):
    """Collect actionable missing references before strict protocol verification. Read-only."""
    issues = []
    def issue(code, path, message):
        value = dict(code=code, path=str(path), message=message)
        if value not in issues:
            issues.append(value)
    def file(relative, expected=None):
        try:
            path = c.workspace_file(relative)
            if not path.is_file():
                issue('missing', relative, '缺少文件，请按对应模块协议补齐并封存。')
            elif expected is not None and c.sha(path) != expected:
                issue('changed', relative, '内容与封存哈希不一致；请恢复原件，或创建新的初始化。')
        except (ValueError, OSError) as exc:
            issue('unsafe_path', relative, str(exc))
    runs = c.contained(c.ROOT, 'runs')
    candidates = sorted(p.name for p in runs.iterdir() if p.is_dir() and (p/'initialization').is_dir()) if runs.exists() else []
    if not sid:
        selected = c.contained(c.ROOT, '.anysolvers/selected-study.json')
        if selected.exists():
            sid = c.read_json(selected).get('study_id')
        elif len(candidates) == 1:
            sid = candidates[0]
    if not sid:
        issue('choose_study' if candidates else 'missing', 'runs/<STUDY>/initialization/',
              '当前仓库有多个初始化，请在运行配置页选择并保存，或使用 /as-research STUDY_ID。' if candidates else
              '缺少初始化：需要 manifest.json、task、datasets（开发集和封存 final 集及评测包）、ideas，以及 configs 中的基础配置。')
        return dict(ready=False, study_id=None, choices=candidates, issues=issues)
    sid = c.identifier(sid)
    prefix = f'runs/{sid}/initialization/'
    manifest_path = c.contained(c.ROOT, prefix + 'manifest.json')
    file(prefix + 'manifest.json')
    for part in ('task', 'datasets', 'ideas'):
        if not c.contained(c.ROOT, prefix+part).is_dir():
            issue('missing', prefix+part+'/', '缺少 ' + part + ' 初始化模块产物。')
    manifest = None
    if manifest_path.is_file():
        try:
            manifest = c.read_json(manifest_path)
            if manifest.get('status') != 'sealed':
                issue('unsealed', prefix+'manifest.json', '初始化尚未封存，请先完成三模块校验与封存。')
            for ref in manifest.get('task', {}).get('files', {}).values():
                file(ref['path'], ref['sha'])
            for module in manifest.get('modules', {}).values():
                for ref in module.get('resources', {}).values():
                    file(ref['path'], ref['sha'])
            deliveries = manifest.get('deliveries', {})
            for kind in ('dataset', 'ideas'):
                records = deliveries.get(kind, {})
                if not records:
                    issue('missing', prefix+('datasets' if kind=='dataset' else kind)+'/', '未登记 ' + kind + ' 交付物。')
                for record in records.values():
                    for path, sha in record.get('files', {}).items():
                        file(prefix+path, sha)
            purposes = {r['manifest']['purpose'] for r in deliveries.get('dataset', {}).values()}
            for purpose in ('anchor', 'final'):
                if purpose not in purposes:
                    issue('missing_dataset', prefix+'datasets/', '缺少已登记且封存的 '+purpose+' 数据集。/as-research 包含最终测试。')
            source = manifest.get('source', {}).get('path')
            if source:
                file(source)
            else:
                issue('missing', 'configs/<STUDY>.yaml', '初始化缺少基础配置来源 source.path。')
        except (ValueError, KeyError, TypeError, OSError) as exc:
            issue('invalid_manifest', prefix+'manifest.json', str(exc))
    config = None
    if not issues:
        try:
            config, raw, initialized = configuration(sid)
            raw = copy.deepcopy(raw)
            raw['final']['authorized'] = True  # Only the NEW command's future batch snapshot.
            raw, cfg = ui.apply_values(raw, config['values'])
            resolved = dict(cfg, modules=resolve_bindings(cfg['modules']))
            validate_execution(resolved)
            for slot in study.SLOTS:
                compatible(initialized['modules'][slot], resolved['modules'][slot])
            datasets = []
            for key, record in initialized['deliveries']['dataset'].items():
                content = launch._sealed_asset(c.ROOT/prefix, record, record['manifest']['cases_file'], limit=16*1024*1024)
                cases = sum(bool(line.strip()) for line in content.splitlines())
                if cases == 0:
                    issue('empty_dataset', prefix+record['manifest']['cases_file'], '数据集没有实例。')
                cap = cfg['budgets']['max_cases_per_dataset']
                if cap is not None and cases > cap:
                    issue('case_limit', prefix+record['manifest']['cases_file'], '实例数超过本次配置上限。')
                datasets.append(dict(dataset_id=key, purpose=record['manifest']['purpose'], cases=cases))
            minimum = sum(d['purpose']=='anchor' for d in datasets) + 2*sum(d['purpose']=='final' for d in datasets) + 1
            calls = cfg['budgets']['evaluation_calls']
            if calls is not None and calls < minimum:
                issue('budget', 'UI.evaluation_calls', f'评测额度至少需要 {minimum} 次，才能容纳开发基线、一个候选及最终基线/候选。')
            ui.assert_settled(config['existing_runs'])
            # Do not enqueue another run behind an interrupted full-cycle launch.
            for rid in config['existing_runs']:
                if (c.runtime_directory(c.run_path(rid))/'as-launch.json').exists():
                    from dsh.engine_access import terminal_status
                    if not terminal_status(rid)['complete']:
                        issue('unfinished_run', 'runs/'+rid, '前次 /as-research 尚未完成，请在原主会话继续或核对失败现场，不自动重放。')
            ideas = sorted({x['idea_id'] for r in initialized['deliveries']['ideas'].values() for x in r['manifest']['ideas']})
            if not ideas:
                issue('missing', prefix+'ideas/', '缺少可分配的初始想法。')
            profile = dict(study_id=sid, config_revision=config['revision'], budgets=cfg['budgets'], compute=cfg['compute'],
                           research_model=cfg['agent']['profiles']['research'], datasets=datasets,
                           assignments=[dict(actor='r'+str(i+1), idea=ideas[i%len(ideas)]) for i in range(cfg['budgets']['researchers'])] if ideas else [],
                           final_permitted=True)
        except Exception as exc:
            issue('invalid_protocol', prefix, str(exc).replace(str(c.ROOT), '<workspace>')[:1200])
    result = dict(ready=not issues, study_id=sid, choices=candidates, issues=issues)
    if not issues:
        result.update(profile=profile, revision=c.digest(dict(configuration=config['revision'], initialization=c.sha(manifest_path))))
    return result


def start(sid, payload):
    session = payload.get('session')
    if not isinstance(session, str) or not session or len(session) > 200:
        raise ValueError('需要原生主会话身份。')
    request_id = payload.get('request_id')
    if not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', request_id):
        raise ValueError('需要唯一启动请求 ID。')
    check = preflight(sid)
    if not check['ready']:
        return check
    if check['revision'] != payload.get('revision'):
        return dict(ready=False, issues=[dict(code='stale',path='UI',message='检定后配置或初始化已变化，请重新检定。')])
    directory = c.contained(c.ROOT, '.anysolvers/implementations/anysolvers/launches')
    directory.mkdir(parents=True, exist_ok=True)
    with study._lock(directory):
        claim = directory/(request_id+'.json')
        if claim.exists():
            raise ValueError('此启动请求已登记，请核对原会话，勿重复提交。')
        check = preflight(sid)
        if not check['ready'] or check['revision'] != payload['revision']:
            return dict(ready=False, issues=check['issues'] or [dict(code='stale',path='UI',message='配置已变化，请重新检定。')])
        # Durable admission before allocation; an uncertain crash is never silently retried.
        unresolved = [p for p in directory.glob('*.json') if c.read_json(p).get('state') == 'allocating']
        if unresolved:
            raise ValueError('存在状态不明的启动请求：'+unresolved[0].name+'；请先核对现场。')
        config, raw, _ = configuration(sid)
        if config['revision'] != check['profile']['config_revision']:
            return dict(ready=False, issues=[dict(code='stale', path='UI', message='保存参数在创建前发生变化，请重新检定。')])
        record = dict(state='allocating', session=session, request_id=request_id, study_id=sid, created_at=c.utc(), revision=check['revision'])
        c.write_json(claim, record)
        raw['final']['authorized'] = True
        config_path = 'configs/as-runs/' + request_id + '.yaml'
        path = c.contained(c.ROOT, config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as stream:
            yaml.safe_dump(raw, stream, allow_unicode=True, sort_keys=False)
        result = run.init(sid, config_path)
        rid = result['run_id']
        _, cfg, state = c.load_run(rid, read_only=True)
        record.update(state='created', run_id=rid, config_sha=state['config_sha'], final_authorized=True)
        target = c.contained(c.runtime_directory(c.run_path(rid)), 'as-launch.json')
        target.parent.mkdir(exist_ok=True)
        c.write_json(target, record)
        c.write_json(claim, record)
        return dict(ready=True, run_id=rid, profile=check['profile'], created_at=record['created_at'])


def consent(rid, session):
    path = c.contained(c.runtime_directory(c.run_path(rid)), 'as-launch.json')
    if not path.exists():
        return dict(authorized=False)
    record = c.read_json(path)
    _, cfg, state = c.load_run(rid, read_only=True)
    return dict(authorized=record.get('run_id')==rid and record.get('session')==session and
                record.get('config_sha')==state['config_sha'] and record.get('final_authorized') is True and cfg['final']['authorized'] is True)


def command(request):
    op = request.get('operation')
    payload = request.get('payload', {})
    if request.get('schema_version') != 1 or not isinstance(payload, dict):
        raise ValueError('Invalid as-research request')
    sid = request.get('study_id')
    if op == 'as-preflight': return preflight(sid)
    if op == 'as-start': return start(c.identifier(sid), payload)
    if op == 'as-configuration': return configuration(c.identifier(sid))[0]
    if op == 'as-save': return save(c.identifier(sid), payload)
    if op == 'as-consent': return consent(request['run_id'], payload.get('session'))
    raise ValueError('Unknown as-research operation')
