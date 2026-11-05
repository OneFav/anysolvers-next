"""Generic read-only study discovery and launch metadata for DSH.

This entry does not create runs, launch models, measure candidates or change
budgets. It is separate from the execution broker pinned by existing runs.
"""
import contextlib
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jsonschema
from tools import common as c, dsh_assets as assets, dsh_bridge as bridge, study
from tools.module_runtime import read_module

MAX_REQUEST_BYTES = bridge.MAX_REQUEST_BYTES
_text, _run_id, _study_id, _sealed_asset = bridge._text, bridge._run_id, bridge._study_id, bridge._sealed_asset


def _belongs_to_method(path):
    marker=c.contained(path,'.runtime/run.json')
    return not marker.exists() or c.read_json(marker).get('implementationId')=='anysolvers'


def _launch_runs(directory, study_id):
    return [study_id + '/' + path.name for path in sorted(
        (path for path in directory.parent.iterdir()
         if path.name.isascii() and path.name.isdigit() and path.is_dir() and _belongs_to_method(path)),
        key=lambda path: int(path.name))]


def _launch_list():
    """Discover standard sealed studies, without choosing a task or creating work."""
    root = c.contained(c.ROOT, 'runs')
    values, invalid = [], []
    if not root.exists():
        return dict(schema_version=1, studies=values, invalid=invalid)
    for path in sorted(root.iterdir()):
        if not (path / 'initialization/manifest.json').exists():
            continue
        try:
            directory, manifest = study.load(c.identifier(path.name), require_sealed=True)
            values.append(dict(study_id=path.name, task_id=manifest['task']['task_id'],
                               config=manifest.get('source', {}).get('path'),
                               existing_runs=_launch_runs(directory, path.name)))
        except (ValueError, OSError) as error:
            invalid.append(dict(study_id=path.name, reason=str(error)[:240]))
    return dict(schema_version=1, studies=values, invalid=invalid)


def _launch_profile(request, payload):
    """Use standard sealed initialization plus its config; no benchmark-specific preset."""
    from tools.configuration import prepare
    from tools.module_runtime import compatible
    run_id = _run_id(request) if request.get('run_id') else None
    study_id = c.identifier(run_id.split('/')[0]) if run_id else _study_id(request)
    if request.get('study_id') and request['study_id'] != study_id:
        raise ValueError('launch study and run identities differ')
    directory, initialized = study.load(study_id, require_sealed=True)
    existing = _launch_runs(directory, study_id)
    selected = run_id or (existing[-1] if existing else None)
    if run_id and run_id not in existing:
        raise ValueError('unknown initialized research run')
    if selected:
        if payload.get('config'):
            raise ValueError('existing research uses its pinned configuration; do not replace it')
        _, cfg, _ = c.load_run(selected, read_only=True)
        config_path = selected + '/run.yaml'
        config_path = 'runs/' + config_path
    else:
        source = initialized.get('source', {})
        config_path = _text(payload.get('config') or source.get('path'),
                            'config; this study needs an explicit --config')
        raw = assets.read(c.ROOT, config_path)
        if not payload.get('config') and assets.sha(raw) != source.get('sha'):
            raise ValueError('initialization source config changed; select an explicit config')
        cfg = prepare(config_path)
        # Match run.init: sealed initialization is authoritative, even when the
        # original task library has moved or the base was reused across studies.
        for slot in ('task', 'datasets', 'ideas'):
            binding = cfg['modules'][slot]
            compatible(initialized['modules'][slot], read_module(binding['use'], binding.get('params')))
    ideas = sorted({item['idea_id'] for record in initialized['deliveries']['ideas'].values()
                    for item in record['manifest']['ideas']})
    if not ideas:
        raise ValueError('initialized research requires sealed initial ideas')
    assignments = [dict(actor='r' + str(index + 1), idea=ideas[index % len(ideas)])
                   for index in range(cfg['budgets']['researchers'])]
    datasets = []
    for dataset_id, record in sorted(initialized['deliveries']['dataset'].items()):
        data = _sealed_asset(directory, record, record['manifest']['cases_file'], limit=16 * 1024 * 1024)
        datasets.append({'dataset_id': dataset_id, 'purpose': record['manifest']['purpose'],
                         'cases': sum(bool(line.strip()) for line in data.splitlines())})
    if not any(item['purpose'] == 'anchor' for item in datasets):
        raise ValueError('initialized research requires a sealed anchor dataset')
    return dict(schema_version=1, study_id=study_id,
                config=config_path, config_sha=assets.sha(assets.read(c.ROOT, config_path)),
                task_id=initialized['task']['task_id'], assignments=assignments, budgets=cfg['budgets'],
                compute=cfg['compute'], research_model=cfg['agent']['profiles']['research'],
                final_permitted=cfg['final']['authorized'], datasets=datasets,
                existing_runs=existing, selected_run=selected,
                next_action='status' if existing else 'start')


def command(request):
    if not isinstance(request, dict) or set(request) - {
            'schema_version', 'operation', 'run_id', 'study_id', 'session_id', 'payload'}:
        raise ValueError('invalid launch request fields')
    if type(request.get('schema_version')) is not int or request['schema_version'] != 1:
        raise ValueError('unsupported launch schema_version')
    if 'session_id' in request:
        raise ValueError('coordinator operation does not accept a researcher session identity')
    operation, payload = request.get('operation'), request.get('payload', {})
    if operation not in ('launch-list', 'launch-profile'):
        raise ValueError('unsupported launch operation')
    allowed = {'config'} if operation == 'launch-profile' else set()
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('invalid launch payload fields')
    if operation == 'launch-list':
        if request.get('run_id') or request.get('study_id'):
            raise ValueError('launch discovery does not accept a selected study or run')
        return _launch_list()
    return _launch_profile(request, payload)


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
