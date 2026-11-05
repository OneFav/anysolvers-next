"""Final/visualization adapter. Does not execute candidates or alter pinned runtime files."""
import base64
import contextlib
import fcntl
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import common as c, dsh_assets as assets, dsh_bridge as bridge, run
from tools.module_runtime import invoke

MAX_REQUEST = 2 * 1024 * 1024


@contextlib.contextmanager
def snapshot(run_id):
    directory = c.run_path(run_id)
    with contextlib.ExitStack() as stack:
        try: lease = stack.enter_context((directory / '.mutation.lock').open('rb'))
        except FileNotFoundError: lease = None
        if lease: fcntl.flock(lease, fcntl.LOCK_SH)
        yield c.load_run(run_id, read_only=True)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def eligible(s):
    return (s.get('status') == 'completed' and s.get('all_valid') is True
            and not s.get('probe') and not s.get('infrastructure_error') and s.get('cleanup_confirmed') is True
            and s.get('total_cases', 0) > 0 and s.get('completed_cases') == s['total_cases']
            and s.get('valid_cases') == s['total_cases'] and number(s.get('score')))


def evidence(directory, state, ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or len(set(ids)) != len(ids):
        raise ValueError('Provide 1–100 distinct evaluation IDs')
    rows = []
    for eid in ids:
        c.identifier(eid)
        if eid not in state['evaluations']:
            raise ValueError('Unknown evaluation: ' + eid)
        s = bridge._summary(directory, state, eid)
        if s['status'] not in ('completed', 'failed'):
            raise ValueError('Evaluation is not settled: ' + eid)
        row = {key: s.get(key) for key in ('evaluation_id', 'candidate_id', 'source_sha', 'dataset_id',
               'dataset_purpose', 'status', 'probe', 'total_cases', 'completed_cases', 'valid_cases',
               'all_valid', 'infrastructure_error', 'cleanup_confirmed', 'wall_s')}
        row.update(score=s.get('score') if eligible(s) else None, score_available=eligible(s),
                   summary_sha=state['evaluation_hashes'][eid]['summary_sha'],
                   results_sha=state['evaluation_hashes'][eid]['results_sha'])
        rows.append(row)
    return rows


def settled(directory, cfg, state):
    if any(s['status'] not in ('completed', 'failed') for s in state['sessions']):
        return False
    records = invoke(cfg, 'research', 'audit', directory, cfg, state)
    if any(v['kind'] == 'request' and k + '-response' not in records for k, v in records.items()):
        return False
    if any(w.get('finished_at') is None for s in state['sessions'] for w in s.get('preflight_waits', {}).values()):
        return False
    return all(bridge._summary(directory, state, eid)['status'] in ('completed', 'failed')
               for eid in state['evaluations'])


def require_settled(directory, cfg, state):
    if not settled(directory, cfg, state):
        raise ValueError('End research and reconcile pending evaluations before terminal work')


def status_data(directory, cfg, state):
    datasets = [dict(dataset_id=k) for k, r in state['deliveries']['dataset'].items()
                if r['manifest']['purpose'] == 'final']
    coverage = bridge._final_coverage(directory, state, datasets)
    reports = [dict(id=k, manifest_path=r['manifest_path'], report_file=r['manifest']['report_file'],
                    evaluation_ids=r['manifest']['evaluation_ids']) for k, r in state['deliveries']['report'].items()]
    figures = [dict(id=k, manifest_path=r['manifest_path'], image_file=r['manifest']['image_file'],
                    data_file=r['manifest']['data_file'], evaluation_ids=r['manifest']['evaluation_ids'])
               for k, r in state['deliveries']['figure'].items()]
    pairs = [set(pair['evaluation_ids']) for pair in coverage['evaluations']]
    final_reports = [r['id'] for r in reports if coverage['complete'] and all(p & set(r['evaluation_ids']) for p in pairs)]
    final_ids = set().union(*pairs) if pairs else set()
    final_figures = [f['id'] for f in figures if final_ids & set(f['evaluation_ids'])]
    idle = settled(directory, cfg, state)
    complete = bool(idle and coverage['complete'] and final_reports and final_figures)
    phase = ('research' if not idle else 'complete' if complete else
             'final-evaluation' if not coverage['complete'] and cfg['final']['authorized'] else
             'report' if not (final_reports if coverage['complete'] else reports) else
             'visualization' if not (final_figures if coverage['complete'] else figures) else 'development-only')
    return dict(run_id=cfg['run_id'], phase=phase, complete=complete, settled=idle,
                final_authorized=cfg['final']['authorized'], selection=state.get('selection'),
                coverage=coverage, reports=reports, figures=figures,
                final_report_ids=final_reports, final_figure_ids=final_figures)


def artifact(directory, state, payload):
    kind, key, part = payload.get('kind'), payload.get('id'), payload.get('asset')
    if kind not in ('report', 'figure') or part not in (('report',) if kind == 'report' else ('image', 'data')):
        raise ValueError('Unsupported artifact kind or asset')
    record = state['deliveries'][kind].get(c.identifier(key))
    if record is None:
        raise ValueError('Artifact is not registered')
    path = record['manifest'][part + '_file']
    raw = assets.read(directory, path)
    if assets.sha(raw) != record['files'][path]:
        raise ValueError('Artifact seal differs')
    if part == 'image':
        mime = 'image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else None
        if not mime:
            raise ValueError('Only registered PNG/JPEG images can be previewed')
        return dict(filename=Path(path).name, mime=mime, encoding='base64', content=base64.b64encode(raw).decode())
    return dict(filename=Path(path).name, mime='text/markdown' if part == 'report' else 'application/json',
                encoding='utf8', content=raw.decode())


def report_write(directory, state, payload):
    path = payload.get('path')
    names = assets.parts(path)
    if names[0] != 'final' or len(names) < 2 or 'evaluations' in names or any(n.startswith('.') for n in names):
        raise ValueError('Report drafts must be under final/ outside evaluations/')
    if Path(path).suffix not in ('.md', '.json', '.txt') or not isinstance(payload.get('content'), str):
        raise ValueError('Reports accept UTF-8 markdown, JSON or text only')
    if payload.get('encoding', 'utf8') != 'utf8':
        raise ValueError('Use visualization_render to produce images')
    if any(path in r['files'] for records in state['deliveries'].values() for r in records.values()):
        raise ValueError('Sealed artifact cannot be overwritten')
    raw = payload['content'].encode()
    assets.write(directory, path, raw, overwrite=True)
    return dict(path=path, sha=assets.sha(raw), bytes=len(raw))


def report_deliver(directory, cfg, state, payload):
    if payload.get('kind', 'report') != 'report':
        raise ValueError('Use visualization_deliver for figures')
    relative = payload.get('manifest')
    data = c.parse_json(assets.read(directory, relative).decode())
    if data.get('evaluation_ids'):
        evidence(directory, state, data['evaluation_ids'])
    existing = state['deliveries']['report'].get(data.get('report_id'))
    if existing and existing['manifest_path'] == relative and existing['manifest'] == data:
        return dict(kind='report', id=data['report_id'], already_registered=True, next_action='visualization_status')
    result = run.deliver(directory, cfg, state, 'report', relative, 'final')
    return dict(result, next_action='visualization_render, then visualization_deliver; check final_status')


def chart_data(directory, cfg, state, payload):
    ids = payload.get('evaluation_ids')
    rows = evidence(directory, state, ids)
    if len(rows) > 12:
        raise ValueError('A chart supports at most 12 evaluations')
    refs = {eid for r in state['deliveries']['report'].values() for eid in r['manifest']['evaluation_ids']}
    if not set(ids) <= refs:
        raise ValueError('Register a report referencing every requested evaluation before visualization')
    kind = payload.get('chart', 'comparison')
    if kind not in ('comparison', 'by_metric'):
        raise ValueError('Unsupported chart type')
    group, metric = payload.get('group_by'), payload.get('metric', 'score')
    if kind == 'comparison' and (group is not None or metric != 'score'):
        raise ValueError('Comparison charts use the trusted objective score')
    if kind == 'by_metric' and any(not isinstance(s, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', s) for s in (group, metric)):
        raise ValueError('by_metric requires a metrics object key and numeric metric key')
    if len({r['dataset_id'] for r in rows}) > 4:
        raise ValueError('Use at most four datasets per figure')
    for row in rows:
        row['groups'] = []
        if kind == 'comparison':
            continue
        summary = bridge._summary(directory, state, row['evaluation_id'])
        raw = assets.read(directory, summary['details_file'], limit=64*1024*1024)
        if assets.sha(raw) != row['results_sha']:
            raise ValueError('Evaluation detail seal differs')
        groups, seen = {}, set()
        for line in raw.splitlines():
            record = c.parse_json(line.decode())
            if record.get('type') != 'result':
                continue
            case = record['case_id']
            if case in seen:
                raise ValueError('Duplicate case result')
            seen.add(case)
            result = record.get('result', {})
            values = result.get('metrics', {}).get(group, {})
            if not isinstance(values, dict) or len(values) > 100:
                raise ValueError('Grouping metric must be a bounded object')
            for label, measures in values.items():
                if not isinstance(label, str) or len(label) > 80:
                    raise ValueError('Invalid group label')
                value = measures.get(metric) if isinstance(measures, dict) else None
                bucket = groups.setdefault(label, [])
                if result.get('valid') is True and number(value):
                    bucket.append(value)
        if not groups:
            raise ValueError('No recorded metrics group: ' + group)
        if len(groups) > 100:
            raise ValueError('Too many groups')
        for label, values in sorted(groups.items(), key=lambda x: (0, float(x[0])) if re.fullmatch(r'-?\d+(\.\d+)?', x[0]) else (1, x[0])):
            valid = row['score_available'] and len(values) == row['total_cases']
            row['groups'].append(dict(label=label, value=sum(values)/len(values) if valid else None,
                                      valid_cases=len(values), total_cases=row['total_cases']))
    title = payload.get('title', '')
    if not isinstance(title, str) or len(title) > 160 or any(ord(x)<32 for x in title):
        raise ValueError('Invalid chart title')
    return dict(schema_version=1, run_id=cfg['run_id'], config_sha=state['config_sha'],
                chart=kind, group_by=group, metric=metric if kind == 'by_metric' else cfg['task']['objective']['name'],
                direction=cfg['task']['objective']['direction'], title=title, evaluations=rows,
                report_ids=sorted(state['deliveries']['report']),
                renderer_sha=c.sha(Path(__file__).with_name('chart_renderer.py')),
                adapter_sha=c.sha(Path(__file__)))


def render(directory, cfg, state, payload):
    fid = c.identifier(payload.get('figure_id'))
    data = chart_data(directory, cfg, state, payload)
    prefix = 'visualization/' + fid
    manifest = dict(schema_version=1, figure_id=fid, image_file=prefix+'/image.png',
                    data_file=prefix+'/data.json', evaluation_ids=payload['evaluation_ids'])
    raw = bridge._json_bytes(data)
    existing = state['deliveries']['figure'].get(fid)
    if existing:
        previous = c.parse_json(assets.read(directory, existing['manifest']['data_file']).decode())
        comparable = lambda value: {k:v for k,v in value.items() if k not in ('renderer_sha','adapter_sha','report_ids')}
        if comparable(previous) != comparable(data):
            raise ValueError('Figure ID is already sealed with a different specification; use a new ID')
        return dict(manifest=existing['manifest_path'], registered=True, **manifest)
    from dsh.chart_renderer import draw
    image = draw(data)
    assets.write(directory, manifest['data_file'], raw)
    assets.write(directory, manifest['image_file'], image)
    assets.write(directory, prefix+'/manifest.json', bridge._json_bytes(manifest))
    return dict(manifest=prefix+'/manifest.json', registered=False, **manifest)


def figure_deliver(directory, cfg, state, payload):
    relative = payload.get('manifest')
    manifest = c.parse_json(assets.read(directory, relative).decode())
    fid = c.identifier(manifest.get('figure_id'))
    prefix = 'visualization/'+fid
    expected = dict(schema_version=1, figure_id=fid, image_file=prefix+'/image.png', data_file=prefix+'/data.json',
                    evaluation_ids=manifest.get('evaluation_ids'))
    if manifest != expected or relative != prefix+'/manifest.json':
        raise ValueError('Deliver the manifest returned by visualization_render')
    existing = state['deliveries']['figure'].get(fid)
    if existing and existing['manifest_path'] == relative and existing['manifest'] == manifest:
        return dict(kind='figure', id=fid, checked_files=len(existing['files']), already_registered=True)
    data = c.parse_json(assets.read(directory, manifest['data_file']).decode())
    spec = dict(figure_id=fid, evaluation_ids=manifest['evaluation_ids'], chart=data.get('chart'),
                group_by=data.get('group_by'), metric=data.get('metric'), title=data.get('title'))
    if spec['chart'] == 'comparison':
        spec.pop('metric')
    fresh = chart_data(directory, cfg, state, spec)
    # A later additional report need not invalidate already prepared plotting evidence.
    fresh['report_ids'] = data.get('report_ids')
    if fresh != data:
        raise ValueError('Plot data no longer matches trusted evaluation records')
    from dsh.chart_renderer import draw
    if assets.read(directory, manifest['image_file']) != draw(data):
        raise ValueError('Figure differs from the fixed renderer output')
    existing = state['deliveries']['figure'].get(fid)
    if existing:
        return dict(kind='figure', id=fid, checked_files=len(existing['files']), already_registered=True)
    return run.deliver(directory, cfg, state, 'figure', relative, 'visualization')


OPERATIONS = {
    'terminal-status': set(), 'terminal-evidence': {'evaluation_ids'},
    'terminal-report-write': {'path', 'content', 'encoding'},
    'terminal-report-deliver': {'manifest', 'kind'},
    'terminal-artifact': {'kind', 'id', 'asset'},
    'visualization-render': {'figure_id', 'evaluation_ids', 'chart', 'group_by', 'metric', 'title'},
    'visualization-deliver': {'manifest'},
}


def command(request):
    if (not isinstance(request, dict) or set(request)-{'schema_version','operation','payload','run_id'}
            or type(request.get('schema_version')) is not int or request['schema_version'] != 1):
        raise ValueError('Invalid terminal request')
    op, payload = request.get('operation'), request.get('payload', {})
    if op not in OPERATIONS or not isinstance(payload, dict) or set(payload)-OPERATIONS[op]:
        raise ValueError('Invalid terminal operation or fields')
    rid = bridge._run_id(request)
    if op in ('terminal-status', 'terminal-evidence', 'terminal-artifact'):
        with snapshot(rid) as (directory, cfg, state):
            if op == 'terminal-status': return status_data(directory, cfg, state)
            if op == 'terminal-artifact': return artifact(directory, state, payload)
            require_settled(directory, cfg, state)
            return dict(evaluations=evidence(directory, state, payload.get('evaluation_ids')))
    with c.mutation(c.run_path(rid), wait=True):
        directory, cfg, state = c.load_run(rid)
        require_settled(directory, cfg, state)
        if op == 'terminal-report-write': return report_write(directory, state, payload)
        if op == 'terminal-report-deliver': return report_deliver(directory, cfg, state, payload)
        if op == 'visualization-render': return render(directory, cfg, state, payload)
        if op == 'visualization-deliver': return figure_deliver(directory, cfg, state, payload)


if __name__ == '__main__':
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST+1)
        if len(raw)>MAX_REQUEST: raise ValueError('Request too large')
        value = command(c.parse_json(raw.decode()))
        reply = dict(schema_version=1, ok=True, value=value)
    except Exception as error:
        reply = dict(schema_version=1, ok=False, error=dict(code='TERMINAL_REQUEST', message=str(error).replace(str(c.ROOT), '<repository>')[:1000]))
    print(json.dumps(reply, ensure_ascii=False, allow_nan=False))
