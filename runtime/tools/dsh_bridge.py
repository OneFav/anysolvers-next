"""Versioned JSON broker for trusted DSH Host plugins; Python owns research rules.

The Host binds the caller's session identity. This protocol exposes no arbitrary
command, Python evaluation or host file reader, and never calls research models.
"""
import argparse
import base64
import contextlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jsonschema
from tools import common as c, dsh_assets as assets, measure, research, run, study
from tools.module_runtime import invoke, dependency, output_schema, read_module

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 4 * 1024 * 1024
RESEARCH_OPERATIONS = {'context', 'evaluate', 'result', 'findings', 'share', 'reuse', 'checkpoint', 'iteration'}
SESSION_OPERATIONS = {'research', 'research-materials', 'research-development',
                      'candidate-import', 'research-record', 'reuse-export'}
MODULE_SLOTS = {'task', 'datasets', 'ideas', 'research', 'evaluation', 'selections', 'final', 'visualization'}
PAYLOADS = {
    'runtime-wait': {'native_id','id','action','reason','supervisor'},
    'stage-validate': {'stage'},
    'selection-context': {'actor'}, 'selection-submit': {'actor','decision'},
    'selection-report-context': {'target_session'}, 'selection-report-submit': {'target_session','report'},
    'status': set(), 'config': set(), 'check': set(), 'audit': set(),
    'module': {'slot', 'config'}, 'study-init': {'config'}, 'study-check': set(),
    'study-seal': set(), 'study-deliver': {'kind', 'manifest', 'module'},
    'study-plan-resources': {'config', 'measured'},
    'batch-init': {'config', 'researchers', 'rounds'},
    'begin-session': {'actor', 'idea', 'iteration'},
    'activate-session': {'actor', 'native_id'},
    'end-session': {'actor', 'status', 'native_id'},
    'select': {'candidate'}, 'measure': {'candidate', 'dataset', 'evaluation', 'probe', 'scope'},
    'research': {'operation', 'manifest', 'id', 'dataset', 'evaluation', 'probe', 'details', 'case'},
    'research-materials': set(), 'prepare-write': {'path', 'content'},
    'research-development': {'dataset', 'case', 'offset', 'limit'},
    'evidence': {'evaluation', 'offset', 'limit'},
    'launch-profile': {'benchmark'},
    'candidate-import': {'candidate_id', 'source', 'rationale', 'parent_id'},
    'research-record': {'operation', 'record'}, 'reuse-export': {'record'},
    'artifact-write': {'path', 'content', 'encoding'},
    'artifact-deliver': {'kind', 'manifest'}, 'doctor': {'image'},
}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + ' must be a nonempty string')
    return value


def _request(request):
    if not isinstance(request, dict) or set(request) - {
            'schema_version', 'operation', 'run_id', 'study_id', 'session_id', 'payload'}:
        raise ValueError('invalid bridge request fields')
    if type(request.get('schema_version')) is not int or request['schema_version'] != SCHEMA_VERSION:
        raise ValueError('unsupported bridge schema_version')
    operation = request.get('operation')
    if not isinstance(operation, str) or operation not in PAYLOADS:
        raise ValueError('unsupported bridge operation')
    if operation not in SESSION_OPERATIONS and 'session_id' in request:
        raise ValueError('coordinator operation does not accept a researcher session identity')
    if operation in SESSION_OPERATIONS:
        _text(request.get('session_id'), 'session_id')
    payload = request.get('payload', {})
    if not isinstance(payload, dict) or set(payload) - PAYLOADS[operation]:
        raise ValueError('invalid operation payload fields')
    for name in ('probe', 'details'):
        if name in payload and type(payload[name]) is not bool:
            raise ValueError(name + ' must be boolean')
    for name in ('researchers', 'rounds'):
        if name in payload and (type(payload[name]) is not int or payload[name] < 1):
            raise ValueError(name + ' must be a positive integer')
    return operation, payload


def _run_id(request):
    value = _text(request.get('run_id'), 'run_id')
    c.run_identifier(value)
    return value


def _study_id(request):
    return c.identifier(request.get('study_id'))


def _session(state, sid, active=True):
    matches = [item for item in state['sessions'] if item['session_id'] == sid]
    if len(matches) != 1 or (active and matches[0]['status'] != 'active'):
        raise ValueError('a registered active researcher session is required')
    return matches[0]


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def _run_command(operation, run_id, **payload):
    return run.command(argparse.Namespace(command=operation, run=run_id, **payload))


def _status(request):
    if not request.get('run_id'):
        result = study.check(_study_id(request))
        return dict(result, schema_version=1, phase='ready' if result['status'] == 'sealed' else 'initialize')
    run_id = _run_id(request)
    # One metadata transaction keeps lifecycle, reservations and final hashes in
    # the same snapshot while native researchers continue working concurrently.
    with c.mutation(c.run_path(run_id), wait=True):
        return _run_status(run_id)


def _run_status(run_id):
    directory, cfg, state = c.load_run(run_id, read_only=True)
    result = _run_command('status', run_id)
    records = invoke(cfg, 'research', 'audit', directory, cfg, state)
    pending = [key for key, item in records.items()
               if item['kind'] == 'request' and key + '-response' not in records]
    from modules.research.notebook_collaboration.sessions import research_time
    from modules.research.notebook_collaboration.runtime import remaining
    timings = {item['session_id']: research_time(cfg, item) for item in state['sessions']}
    phase = 'reported' if state['deliveries']['report'] else ('finalize' if state['selection'] else 'orchestrate')
    final_datasets = [dict(dataset_id=key, manifest_sha=record['files'][record['manifest_path']],
                           files=record['files'])
                      for key, record in sorted(state['deliveries']['dataset'].items())
                      if record['manifest']['purpose'] == 'final']
    return dict(result, schema_version=1, phase=phase, pending_requests=pending,
                research_time=timings, remaining_evaluations=remaining(cfg, state, records),
                final_authorized=cfg['final']['authorized'], final_datasets=final_datasets,
                final_coverage=_final_coverage(directory, state, final_datasets))


def _page(payload):
    offset, limit = payload.get('offset', 0), payload.get('limit', 50)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('offset must be nonnegative and limit must be between 1 and 100')
    return offset, limit


def _summary(directory, state, evaluation_id):
    data = assets.read(directory, state['evaluations'][evaluation_id], limit=65536)
    value = c.parse_json(data.decode())
    if value.get('evaluation_id') != evaluation_id:
        raise ValueError('evaluation identity differs from its registry')
    seal = state.get('evaluation_hashes', {}).get(evaluation_id)
    if value.get('status') in {'completed', 'failed'}:
        if not seal or assets.sha(data) != seal['summary_sha']:
            raise ValueError('completed evaluation has no matching trusted seal')
    return value


def _final_coverage(directory, state, datasets):
    selected = state.get('selection')
    candidates = ['baseline'] + ([selected['candidate_id']] if selected else [])
    covered = {(item['dataset_id'], cid): [] for item in datasets for cid in candidates}
    for key in state['evaluations']:
        summary = _summary(directory, state, key)
        pair = (summary.get('dataset_id'), summary.get('candidate_id'))
        if (pair in covered and summary.get('dataset_purpose') == 'final'
                and summary.get('status') == 'completed' and not summary.get('probe')
                and not summary.get('infrastructure_error') and summary.get('cleanup_confirmed')
                and summary.get('total_cases', 0) > 0
                and summary.get('completed_cases') == summary['total_cases']
                and (pair[1] == 'baseline' or summary.get('source_sha') == selected['source_sha'])):
            covered[pair].append(key)
    missing = [dict(dataset_id=did, candidate_id=cid) for (did, cid), ids in covered.items() if not ids]
    return dict(complete=bool(selected and datasets and not missing), selection_frozen=bool(selected),
                expected_pairs=len(datasets) * 2, covered_pairs=sum(bool(ids) for ids in covered.values()),
                missing=missing, evaluations=[dict(dataset_id=did, candidate_id=cid, evaluation_ids=ids)
                                              for (did, cid), ids in covered.items() if ids])


def _evidence(request, payload):
    run_id = _run_id(request)
    directory = c.run_path(run_id)
    offset, limit = _page(payload)
    with c.mutation(directory, wait=True):
        _, _, state = c.load_run(run_id)
        keys = sorted(state['evaluations'])
        if payload.get('evaluation') is not None:
            keys = [c.identifier(payload['evaluation'])]
            if keys[0] not in state['evaluations']:
                raise ValueError('unknown evaluation')
            offset = 0
        page = keys[offset:offset + limit]
        candidate_keys = sorted(state['deliveries']['candidate'])
        candidates = []
        for key in candidate_keys[:500]:
            record = state['deliveries']['candidate'][key]
            manifest = record['manifest']
            candidates.append(dict(candidate_id=key, session_id=manifest['session_id'],
                                   source_sha=record['files'][manifest['source_file']],
                                   rationale=manifest['rationale'][:240]))
        result = dict(schema_version=1, run_id=run_id, candidates=candidates,
                      candidates_truncated=len(candidate_keys) > 500, total_candidates=len(candidate_keys),
                      evaluations=[_summary(directory, state, key) for key in page],
                      next_offset=offset + len(page) if offset + len(page) < len(keys) else None,
                      total_evaluations=len(keys))
        if len(_json_bytes(result)) > 1024 * 1024:
            raise ValueError('evidence page is too large; request fewer evaluations')
        return result


def _launch_profile(payload):
    """Read one prepared shortcut definition, without creating work or granting final access."""
    from tools.configuration import prepare
    import re
    benchmark_id = c.identifier(payload.get('benchmark', 'observe-knapsack'))
    raw = assets.read(c.ROOT, 'benchmarks/' + benchmark_id + '/launch.json', limit=32768)
    profile = c.parse_json(raw.decode())
    fields = {'schema_version', 'title', 'study_id', 'config', 'development_dataset', 'final_dataset', 'assignments'}
    if not isinstance(profile, dict) or set(profile) != fields or type(profile['schema_version']) is not int or profile['schema_version'] != 1:
        raise ValueError('invalid benchmark launch definition')
    title = _text(profile['title'], 'title')
    if len(title) > 120:
        raise ValueError('benchmark title is too long')
    study_id = c.identifier(profile['study_id'])
    config_path = _text(profile['config'], 'config')
    cfg = prepare(config_path)
    if type(cfg['budgets']['evaluation_calls']) is not int or cfg['budgets']['evaluation_calls'] < 1:
        raise ValueError('shortcut launch requires a finite evaluation limit')
    directory, initialized = study.load(study_id, require_sealed=True)
    task = c.parse_json(assets.read(c.ROOT, cfg['task']).decode())
    if task['task_id'] != initialized['task']['task_id']:
        raise ValueError('launch config and sealed task differ')
    assignments = profile['assignments']
    if not isinstance(assignments, list) or len(assignments) != cfg['budgets']['researchers']:
        raise ValueError('launch assignments must match the configured researcher count')
    ideas = {item['idea_id'] for record in initialized['deliveries']['ideas'].values() for item in record['manifest']['ideas']}
    actors = set()
    for assignment in assignments:
        if not isinstance(assignment, dict) or set(assignment) != {'actor', 'idea'}:
            raise ValueError('invalid researcher assignment')
        actor, idea = c.identifier(assignment['actor']), c.identifier(assignment['idea'])
        if actor in actors or idea not in ideas:
            raise ValueError('duplicate actor or unknown initial idea')
        actors.add(actor)
    datasets = []
    for key, purpose in [('development_dataset', 'anchor'), ('final_dataset', 'final')]:
        dataset_id = c.identifier(profile[key])
        record = initialized['deliveries']['dataset'].get(dataset_id)
        if record is None or record['manifest']['purpose'] != purpose:
            raise ValueError('launch dataset purpose differs from its sealed delivery')
        data = _sealed_asset(directory, record, record['manifest']['cases_file'], limit=16 * 1024 * 1024)
        datasets.append({'dataset_id': dataset_id, 'purpose': purpose,
                         'cases': sum(bool(line.strip()) for line in data.splitlines())})
    existing = sorted((path.name for path in directory.parent.iterdir()
                       if re.fullmatch(r'[0-9]{3,}', path.name)), key=int)
    return dict(schema_version=1, benchmark=benchmark_id, title=title, study_id=study_id,
                config=config_path, config_sha=assets.sha(assets.read(c.ROOT, config_path)),
                task_id=task['task_id'], assignments=assignments, budgets=cfg['budgets'],
                compute=cfg['compute'], research_model=cfg['agent']['profiles']['research'],
                final_permitted=cfg['final']['authorized'], datasets=datasets,
                existing_runs=[study_id + '/' + item for item in existing],
                next_action='status' if existing else 'start')


def _module(request, payload):
    slot = payload.get('slot')
    if slot not in MODULE_SLOTS:
        raise ValueError('unknown active module category')
    if request.get('run_id'):
        if 'config' in payload:
            raise ValueError('run module reads its pinned configuration')
        run_id = _run_id(request)
        with c.mutation(c.run_path(run_id), wait=True):
            _, cfg, _ = c.load_run(run_id, read_only=True)
            entry = cfg['modules'][slot]
            result = dict(_run_command('module', run_id, slot=slot),
                          schemas={kind: output_schema(entry, kind) for kind in entry['contracts']})
            if len(_json_bytes(result)) > 1024 * 1024:
                raise ValueError('module materials exceed response limit')
            return result
    if request.get('study_id'):
        _study_id(request)
    config_path = payload.get('config')
    if config_path is None and request.get('study_id'):
        manifest = c.contained(c.ROOT, 'runs/' + request['study_id'] + '/initialization/manifest.json')
        if manifest.exists():
            _, prepared = study.load(request['study_id'])
            entry = prepared['modules'].get(slot)
        else:
            entry = None
    else:
        entry = None
    if entry is None:
        from tools.configuration import prepare
        cfg = prepare(_text(config_path, 'config') if config_path is not None else 'configs/smoke.yaml')
        binding = cfg['modules'][slot]
        entry = read_module(binding['use'], binding.get('params'))
    schemas = {kind: output_schema(entry, kind) for kind in entry['contracts']}
    result = dict(slot=slot, use=str(Path(entry['path']).parent), module_id=entry['id'],
                  sha=entry['sha'], text=c.read_pinned(entry, 'module file').decode(),
                  schemas=schemas, execution=entry['execution'], configuration=config_path,
                  historical_read_only=False)
    if not request.get('study_id') and config_path is None:
        # Supply an executable configuration shape; ids in frontmatter are not paths.
        from tools.configuration import prepare
        result['configuration_template'] = prepare('configs/smoke.yaml')
        result['configuration_template']['task'] = 'runs/STUDY/initialization/task/task.json'
        result['configuration_note'] = 'Replace STUDY and review task, agent profiles, image and resource bounds. This template does not authorize research.'
    if len(_json_bytes(result)) > 1024 * 1024:
        raise ValueError('module materials exceed response limit')
    return result


def _prepare_write(request, payload):
    study_id = _study_id(request)
    relative = _text(payload.get('path'), 'path')
    names = assets.parts(relative)
    config = relative == 'configs/' + study_id + '.yaml'
    if not config and (names[:3] != ['runs', study_id, 'initialization'] or len(names) < 5
                       or names[3] not in {'task', 'datasets', 'ideas'}):
        raise ValueError('prepare-write only accepts this study config or initialization drafts')
    data = _text(payload.get('content'), 'content').encode()
    runs = c.contained(c.ROOT, 'runs')
    runs.mkdir(exist_ok=True)
    directory = c.contained(runs, study_id)
    with study._lock(runs):
        lock = study._lock(directory) if directory.exists() else contextlib.nullcontext()
        with lock:
            initialization = c.contained(directory, 'initialization')
            manifest_path = c.contained(initialization, 'manifest.json')
            if manifest_path.exists():
                _, manifest = study._load(study_id)
                if manifest['status'] == 'sealed':
                    raise ValueError('sealed study initialization is immutable')
                protected = {ref['path'] for ref in manifest['task']['files'].values()}
                for records in manifest['deliveries'].values():
                    for record in records.values():
                        protected.update('runs/' + study_id + '/initialization/' + name for name in record['files'])
                if relative in protected:
                    raise ValueError('registered initialization files are immutable')
            elif not config and names[3] != 'task':
                raise ValueError('initialize the task before preparing datasets or ideas')
            if config:
                for state_file in runs.glob('*/*/state.json'):
                    if state_file.is_symlink():
                        continue
                    state = c.read_json(state_file)
                    if state.get('configuration', {}).get('base', {}).get('path') == relative:
                        raise ValueError('configuration is already pinned by a run')
            assets.write(c.ROOT, relative, data, overwrite=True)
    return {'path': relative, 'sha': assets.sha(data), 'bytes': len(data)}


def _candidate_import(request, payload):
    run_id, sid = _run_id(request), _text(request.get('session_id'), 'session_id')
    directory = c.run_path(run_id)
    candidate_id = c.identifier(payload.get('candidate_id'))
    source_name = _text(payload.get('source'), 'source')
    rationale = _text(payload.get('rationale'), 'rationale')
    parent_id = payload.get('parent_id')
    if parent_id is not None:
        c.identifier(parent_id)
    with c.mutation(directory, wait=True):
        _, cfg, state = c.load_run(run_id)
        _session(state, sid)
        workspace = assets.workspace(directory, sid)
        assets.parts(source_name)
        source = assets.read(workspace, source_name)
        source.decode('utf-8')
        prefix = 'research/candidates/' + candidate_id
        manifest = dict(schema_version=1, candidate_id=candidate_id, task_id=cfg['task']['task_id'],
                        source_file=prefix + '/solver.py', entrypoint='solve(case)', dependencies=[],
                        parent_id=parent_id, rationale=rationale, session_id=sid)
        c.validate('candidate', manifest, cfg)
        destination = c.contained(directory, prefix)
        encoded = _json_bytes(manifest)
        if destination.exists():
            if assets.read(destination, 'solver.py') != source or assets.read(destination, 'manifest.json') != encoded:
                raise ValueError('candidate ID already contains different bytes')
            cached = True
        else:
            invoke(cfg, 'research', 'admit', cfg, state, sid)
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix='.dsh-candidate-', dir=destination.parent))
            try:
                assets.write(staging, 'solver.py', source, mode=0o444)
                assets.write(staging, 'manifest.json', encoded, mode=0o444)
                staging.rename(destination)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            cached = False
    return {'candidate_id': candidate_id, 'manifest': prefix + '/manifest.json',
            'source_sha': assets.sha(source), 'cached': cached}


def _research_record(request, payload, export=False):
    operation = 'reuse' if export else payload.get('operation')
    if operation not in {'share', 'reuse', 'checkpoint', 'iteration'}:
        raise ValueError('unsupported research record operation')
    data = payload.get('record')
    if not isinstance(data, dict):
        raise ValueError('record must be an object')
    data = dict(data)
    run_id, sid = _run_id(request), _text(request.get('session_id'), 'session_id')
    author = 'author_session' if operation == 'iteration' else 'session_id'
    if author in data and data[author] != sid:
        raise ValueError('record author differs from the bound session')
    data[author] = sid
    key = c.identifier(data.get('iteration_id' if operation == 'iteration' else 'id'))
    directory = c.run_path(run_id)
    relative = ('research/selections/' if operation == 'iteration' else 'research/inbox/') + key + '.json'
    with c.mutation(directory, wait=True):
        _, cfg, state = c.load_run(run_id)
        _session(state, sid)
        if state.get('selection'):
            raise ValueError('research is frozen')
        schema_kind = {'share': 'share', 'reuse': 'reuse', 'checkpoint': 'notebook', 'iteration': 'iteration'}[operation]
        c.validate(schema_kind, data, cfg)
        encoded = _json_bytes(data)
        if len(encoded) > 32768:
            raise ValueError('research record exceeds size limit')
        assets.write(directory, relative, encoded)
    result = research.command(run_id, operation, session_id=sid, relative=relative)
    if not export:
        return result
    candidate = result['payload']['candidate']
    with c.mutation(directory, wait=True):
        _, _, state = c.load_run(run_id)
        _session(state, sid)
        source = assets.read(directory, candidate['source_file'])
        if assets.sha(source) != candidate['source_sha']:
            raise ValueError('published source changed during export')
        workspace = assets.workspace(directory, sid, create=True)
        target = 'reused/' + candidate['candidate_id'] + '-' + candidate['source_sha'][:16] + '.py'
        assets.write(workspace, target, source)
    return {'record_id': result['record_id'], 'candidate_id': candidate['candidate_id'],
            'path': target, 'source_sha': candidate['source_sha']}


def _research_materials(request):
    run_id, sid = _run_id(request), _text(request.get('session_id'), 'session_id')
    directory = c.run_path(run_id)
    with c.mutation(directory, wait=True):
        _, cfg, state = c.load_run(run_id)
        session = _session(state, sid)
        if state.get('selection'):
            raise ValueError('research is frozen')
        records = invoke(cfg, 'research', 'audit', directory, cfg, state)
        task = c.parse_json(c.read_pinned(cfg['task']['files']['manifest']).decode())
        description = c.read_pinned(cfg['task']['files']['description_file']).decode()
        datasets = [dict(dataset_id=key, purpose=record['manifest']['purpose'],
                         manifest={name: record['manifest'][name] for name in
                                   ('schema_version', 'dataset_id', 'task_id', 'purpose')})
                    for key, record in state['deliveries']['dataset'].items()
                    if record['manifest']['purpose'] in ('anchor', 'challenge')]
        if session.get('idea_id'):
            matches = [idea for record in state['deliveries']['ideas'].values()
                       for idea in record['manifest']['ideas'] if idea['idea_id'] == session['idea_id']]
            if len(matches) != 1:
                raise ValueError('initial idea is missing or ambiguous')
            start = {'kind': 'idea', 'value': matches[0]}
        else:
            record = state['deliveries']['iteration'][session['iteration_id']]
            author = ({'actor':record['manifest']['actor']} if cfg['modules']['selections']['capabilities'].get('coordinator_authored')
                      else _session(state, record['manifest']['author_session'], active=False))
            if author['actor'] != session['actor']:
                raise ValueError('continuation belongs to another researcher')
            start = {'kind': 'iteration', 'value': record['manifest']}
        notebook = None
        recovery_context = (invoke(cfg, 'research', 'recovery_context', directory, cfg, session)
                            if 'recovery_context' in cfg['modules']['research']['handlers'] else None)
        ref = session.get('resume_notebook')
        if ref:
            notebook = next((record for record in records.values()
                             if record['kind'] == 'notebook' and
                             state['extensions']['research_exchange'][record['record_id']] == ref), None)
            if notebook is None or notebook['payload']['actor'] != session['actor']:
                raise ValueError('continuation notebook belongs to another researcher')
        method = cfg['modules']['research']
        instructions_path = dependency(method, 'RESEARCHER.md')
        instructions_ref = method['resources'][c.logical_path(instructions_path)]
        instructions = c.read_pinned(instructions_ref, 'researcher instructions').decode()
        schemas = {kind: output_schema(method, kind) for kind in ('candidate', 'notebook', 'share', 'reuse')}
        schemas['iteration'] = output_schema(cfg['modules']['selections'], 'iteration')
        baselines, protocols = _development_interfaces(directory, state)
        result = {'schema_version': 1, 'run_id': run_id, 'session_id': sid, 'actor': session['actor'],
                  'recovery_context': recovery_context,
                  'research_plan': {'current_generation': session['generation'],
                                    'rounds_per_researcher': cfg['budgets']['rounds_per_researcher'],
                                    'next_iteration_required': not cfg['modules']['selections']['capabilities'].get('coordinator_authored') and session['generation'] < cfg['budgets']['rounds_per_researcher'],
                                    'selection_generates_next_idea': bool(cfg['modules']['selections']['capabilities'].get('coordinator_authored')),
                                    'report_final_checkpoint': bool(cfg['modules']['selections']['capabilities'].get('final_checkpoint_report')),
                                    'active_seconds_per_session': cfg['budgets']['session_wall_s'],
                                    'global_evaluation_limit': cfg['budgets']['evaluation_calls']},
                  'task': task, 'task_description': description, 'development_datasets': datasets,
                  'starting_idea': session.get('starting_idea'), 'starting_point': start, 'notebook': notebook, 'researcher_instructions': instructions,
                  'schemas': schemas, 'baseline_sources': baselines, 'benchmark_protocols': protocols}
        if len(_json_bytes(result)) > 2 * 1024 * 1024:
            raise ValueError('research materials exceed response limit')
        return result


def _sealed_asset(directory, record, relative, limit=262144):
    if relative not in record['files']:
        raise ValueError('requested material is not registered in this sealed delivery')
    data = assets.read(c.delivery_root(directory, record), relative, limit=limit)
    if assets.sha(data) != record['files'][relative]:
        raise ValueError('sealed material changed')
    return data


def _development_interfaces(directory, state):
    datasets = {key: record for key, record in state['deliveries']['dataset'].items()
                if record['manifest']['purpose'] in {'anchor', 'challenge'}}
    groups = {}
    for key, record in datasets.items():
        owner = record
        if record['manifest'].get('benchmark_file') == '@anchor':
            anchors = [item for item in datasets.values() if item['manifest']['purpose'] == 'anchor'
                       and item.get('scope') == 'initialization']
            if len(anchors) != 1:
                raise ValueError('development benchmark needs exactly one shared anchor')
            owner = anchors[0]
        relative = owner['manifest']['benchmark_file']
        raw = _sealed_asset(directory, owner, relative)
        benchmark = c.parse_json(raw.decode())
        group = (str(c.delivery_root(directory, owner)), assets.sha(raw))
        if group not in groups:
            baseline = _sealed_asset(directory, owner, benchmark['baseline_file'])
            fixtures = _sealed_asset(directory, owner, benchmark['tests_file'])
            groups[group] = (dict(benchmark_id=benchmark['benchmark_id'], dataset_ids=[],
                                 source_sha=assets.sha(baseline), source=baseline.decode()),
                             dict(benchmark_id=benchmark['benchmark_id'], dataset_ids=[],
                                  protocol=benchmark['protocol'], interface_fixtures=c.parse_json(fixtures.decode())))
        groups[group][0]['dataset_ids'].append(key)
        groups[group][1]['dataset_ids'].append(key)
    return [pair[0] for pair in groups.values()], [pair[1] for pair in groups.values()]


def _research_development(request, payload):
    run_id, sid = _run_id(request), _text(request.get('session_id'), 'session_id')
    dataset_id = c.identifier(payload.get('dataset'))
    offset, limit = _page(payload)
    case_id = payload.get('case')
    if case_id is not None:
        _text(case_id, 'case')
    directory = c.run_path(run_id)
    with c.mutation(directory, wait=True):
        _, cfg, state = c.load_run(run_id)
        _session(state, sid)
        if state.get('selection'):
            raise ValueError('research is frozen')
        invoke(cfg, 'research', 'audit', directory, cfg, state)
        record = state['deliveries']['dataset'].get(dataset_id)
        if record is None or record['manifest']['purpose'] not in {'anchor', 'challenge'}:
            raise ValueError('only sealed development datasets are readable by researchers')
        raw = _sealed_asset(directory, record, record['manifest']['cases_file'], limit=16 * 1024 * 1024)
        cases, found, total = [], None, 0
        for line in raw.splitlines():
            if not line.strip():
                continue
            value = c.parse_json(line.decode())
            if case_id is not None and value['case_id'] == case_id:
                found = value
            if offset <= total < offset + limit:
                cases.append({'case_id': value['case_id']})
            total += 1
        result = dict(dataset_id=dataset_id, purpose=record['manifest']['purpose'])
        if case_id is not None:
            if found is None:
                raise ValueError('unknown development case')
            if len(_json_bytes(found)) > 262144:
                raise ValueError('development case exceeds response limit')
            return dict(result, case=found)
        return dict(result, cases=cases, total_cases=total,
                    next_offset=offset + len(cases) if offset + len(cases) < total else None)


def _artifact_write(request, payload):
    directory = c.run_path(_run_id(request))
    relative = _text(payload.get('path'), 'path')
    names = assets.parts(relative)
    if (len(names) < 2 or names[0] not in {'final', 'visualization'} or 'evaluations' in names
            or any(name.startswith('.') for name in names)):
        raise ValueError('artifact-write only accepts final/report or visualization drafts')
    content = _text(payload.get('content'), 'content')
    encoding = payload.get('encoding', 'utf8')
    if encoding not in {'utf8', 'base64'}:
        raise ValueError('unsupported artifact encoding')
    data = content.encode() if encoding == 'utf8' else base64.b64decode(content, validate=True)
    with c.mutation(directory, wait=True):
        _, cfg, state = c.load_run(request['run_id'])
        _artifact_boundary(directory, cfg, state)
        protected = {name for records in state['deliveries'].values()
                     for record in records.values() for name in record['files']}
        if relative in protected or relative in state['evaluations'].values():
            raise ValueError('sealed artifact cannot be overwritten')
        assets.write(directory, relative, data, overwrite=True)
    return {'path': relative, 'sha': assets.sha(data), 'bytes': len(data)}


def _artifact_boundary(directory, cfg, state):
    """A settled failed/aborted run may report without manufacturing a winner."""
    if state.get('selection'):
        return
    if any(session['status'] not in {'completed', 'failed'} for session in state['sessions']):
        raise ValueError('freeze a candidate or end active research sessions before reporting failure')
    records = invoke(cfg, 'research', 'audit', directory, cfg, state)
    if any(item['kind'] == 'request' and key + '-response' not in records for key, item in records.items()):
        raise ValueError('pending requests require reconciliation before reporting failure')
    if any(wait.get('finished_at') is None for session in state['sessions']
           for wait in session.get('preflight_waits', {}).values()):
        raise ValueError('pending preflight requires reconciliation before reporting failure')
    if any(_summary(directory, state, key).get('status') not in {'completed', 'failed'}
           for key in state['evaluations']):
        raise ValueError('end active measurements before reporting failure')


def _artifact_deliver(request, payload):
    kind = payload.get('kind')
    if kind not in {'report', 'figure'}:
        raise ValueError('artifact delivery must be report or figure')
    run_id = _run_id(request)
    directory = c.run_path(run_id)
    with c.mutation(directory, wait=True):
        _, cfg, state = c.load_run(run_id)
        _artifact_boundary(directory, cfg, state)
        return run.deliver(directory, cfg, state, kind, _text(payload.get('manifest'), 'manifest'),
                           'final' if kind == 'report' else 'visualization')


def _doctor(payload):
    if payload.get('image') in (None, 'dsh-native:0.2.1-rc.5'):
        from modules.evaluation.queued_sandbox.native import preflight
        try:
            return dict(preflight(), checks_are_read_only=True)
        except Exception as exc:
            return dict(ok=False, executor='dsh-native', error=str(exc), checks_are_read_only=True)
    executable = shutil.which('docker')
    result = {'python_version': sys.version.split()[0], 'docker_available': executable is not None,
              'daemon_ready': False, 'image_available': None, 'checks_are_read_only': True}
    if executable:
        try:
            probe = subprocess.run([executable, 'info', '--format', '{{json .ServerVersion}}'],
                                   capture_output=True, timeout=10, check=False)
            result['daemon_ready'] = probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    image = payload.get('image')
    if image is not None:
        import re
        if not isinstance(image, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
            raise ValueError('doctor image must be a pinned sha256 digest')
        result['image_available'] = False
        if executable and result['daemon_ready']:
            try:
                probe = subprocess.run([executable, 'image', 'inspect', image],
                                       capture_output=True, timeout=10, check=False)
                result['image_available'] = probe.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
    return result


def command(request):
    """Dispatch a validated request using only fixed authority-preserving operations."""
    operation, payload = _request(request)
    if operation in ('runtime-wait','stage-validate'):
        from dsh.stage import command as stage_command
        return stage_command(request)
    if operation == 'doctor':
        if request.get('run_id'):
            _,cfg,_=c.load_run(request['run_id'])
            if cfg['compute']['executor'] in ('ssh-docker','ssh-process'):
                from modules.evaluation.queued_sandbox.remote import preflight
                return preflight(cfg['compute'])
        return _doctor(payload)
    if operation == 'launch-profile':
        return _launch_profile(payload)
    if operation == 'status':
        return _status(request)
    if operation == 'module':
        return _module(request, payload)
    if operation == 'prepare-write':
        return _prepare_write(request, payload)
    if operation.startswith('study-'):
        study_id = _study_id(request)
        if operation == 'study-init':
            return study.create(study_id, _text(payload.get('config'), 'config'))
        if operation == 'study-check':
            return study.check(study_id)
        if operation == 'study-seal':
            return study.seal(study_id)
        if operation == 'study-plan-resources':
            from tools.plan_resources import plan
            return plan(study_id, _text(payload.get('config'), 'config'), payload.get('measured'))
        kind = payload.get('kind')
        if kind not in ('dataset', 'ideas'):
            raise ValueError('study delivery must be dataset or ideas')
        return study.deliver(study_id, kind, _text(payload.get('manifest'), 'manifest'), payload.get('module'))
    if operation == 'batch-init':
        reference = _run_id(request) if request.get('run_id') else _study_id(request)
        return run.init(reference, payload.get('config'), payload.get('researchers'), payload.get('rounds'))
    run_id = _run_id(request)
    if operation in {'config', 'check'}:
        return _run_command(operation, run_id, **payload)
    if operation in ('selection-context','selection-submit','selection-report-context','selection-report-submit'):
        directory = c.run_path(run_id)
        with c.mutation(directory,wait=True):
            directory,cfg,state = c.load_run(run_id)
            invoke(cfg,'research','audit',directory,cfg,state)
            if operation == 'selection-context':
                return invoke(cfg,'selections','pending',directory,cfg,state,payload.get('actor'))
            if operation == 'selection-report-context':
                return invoke(cfg,'selections','report_context',directory,cfg,state,payload.get('target_session'))
            if operation == 'selection-report-submit':
                return invoke(cfg,'selections','report_submit',directory,cfg,state,payload.get('target_session'),payload.get('report'))
            return invoke(cfg,'selections','submit',directory,cfg,state,payload.get('actor'),payload.get('decision'))
    if operation == 'evidence':
        return _evidence(request, payload)
    if operation == 'audit':
        return research.command(run_id, 'audit')
    if operation == 'begin-session':
        return _run_command(operation, run_id, module='research', role='researcher', **payload)
    if operation in {'activate-session', 'end-session', 'select'}:
        return _run_command(operation, run_id, **payload)
    if operation == 'measure':
        _, _, state = c.load_run(run_id)
        purpose = state['deliveries']['dataset'][payload.get('dataset')]['manifest']['purpose']
        scope = payload.get('scope')
        if scope == 'development':
            if payload.get('candidate') != 'baseline' or purpose not in {'anchor', 'challenge'}:
                raise ValueError('development measurement only accepts baseline on development datasets')
        elif scope == 'final':
            if purpose != 'final':
                raise ValueError('final measurement requires a final dataset')
        else:
            raise ValueError('measurement scope must be development or final')
        return measure.evaluate(run_id, _text(payload.get('candidate'), 'candidate'),
                                _text(payload.get('dataset'), 'dataset'),
                                _text(payload.get('evaluation'), 'evaluation'), payload.get('probe', False))
    if operation == 'research':
        action = payload.get('operation')
        if action not in RESEARCH_OPERATIONS:
            raise ValueError('unsupported researcher operation')
        return research.command(run_id, action, session_id=_text(request.get('session_id'), 'session_id'),
                                relative=payload.get('manifest'), request_id=payload.get('id'),
                                dataset_id=payload.get('dataset'), evaluation_id=payload.get('evaluation'),
                                probe=payload.get('probe', False), details=payload.get('details', False),
                                case_id=payload.get('case'))
    if operation == 'candidate-import':
        return _candidate_import(request, payload)
    if operation == 'research-record':
        return _research_record(request, payload)
    if operation == 'reuse-export':
        return _research_record(request, payload, export=True)
    if operation == 'research-materials':
        return _research_materials(request)
    if operation == 'research-development':
        return _research_development(request, payload)
    if operation == 'artifact-write':
        return _artifact_write(request, payload)
    if operation == 'artifact-deliver':
        return _artifact_deliver(request, payload)
    raise ValueError('unsupported bridge operation')


def response(request):
    try:
        value = command(request)
        if not isinstance(value, dict):
            value = {'items': value} if isinstance(value, (list, tuple)) else {'result': value}
        return {'schema_version': 1, 'ok': True, 'value': value}
    except jsonschema.ValidationError as exc:
        code, message = 'INVALID_CONTRACT', 'contract validation failed at ' + '/'.join(map(str, exc.absolute_path))
    except (ValueError, KeyError, TypeError) as exc:
        code, message = 'INVALID_REQUEST', str(exc).replace(str(c.ROOT), '<repository>')[:1000]
    except OSError:
        code, message = 'FILESYSTEM_ERROR', 'file is missing, unsafe, unavailable, or immutable'
    except Exception:
        code, message = 'INTERNAL_ERROR', 'bridge operation failed'
    return {'schema_version': 1, 'ok': False, 'error': {'code': code, 'message': message}}


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError('bridge request exceeds size limit')
        request = c.parse_json(raw.decode('utf-8'))
        with contextlib.redirect_stdout(sys.stderr):
            result = response(request)
    except (ValueError, UnicodeError):
        result = {'schema_version': 1, 'ok': False,
                  'error': {'code': 'INVALID_JSON', 'message': 'invalid or oversized JSON request'}}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
