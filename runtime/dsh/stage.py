"""Supervisor-only operations. No model tool exposes the research clock writer."""
from tools import common as c, study
from tools.module_runtime import invoke


def command(request):
    payload = request.get('payload', {})
    if request['operation'] == 'runtime-wait':
        directory = c.run_path(request['run_id'])
        with c.mutation(directory, wait=True):
            directory, cfg, state = c.load_run(request['run_id'])
            matches = [s for s in state['sessions'] if s.get('native_id') == payload['native_id'] and s['status'] == 'active']
            if payload.get('supervisor'):
                waits=state.setdefault('supervisor_waits',{})
            else:
                if len(matches) != 1:raise ValueError('Expected exactly one active native session')
                waits=matches[0].setdefault('runtime_waits', {})
            key = c.identifier(payload['id'])
            now = c.utc()
            if payload['action'] == 'begin':
                waits.setdefault(key, dict(started_at=now, finished_at=None, reason=payload['reason']))
            elif payload['action'] == 'end':
                # Closing all unclosed waits also handles a provider outage across process recovery.
                for wait in waits.values():
                    if wait['finished_at'] is None:
                        wait['finished_at'] = now
            else:
                raise ValueError('Unknown wait action')
            c.save_state(directory, state)
            return dict(ok=True)
    stage = payload['stage']
    if stage == 'initialize':
        checked = study.check(request['study_id'])
        if checked.get('status') != 'sealed':
            raise ValueError('Initialization must be validated and sealed')
        return dict(validated=True, study_id=request['study_id'], artifacts=[checked])
    directory, cfg, state = c.load_run(request['run_id'], read_only=True)
    from dsh.terminal_bridge import settled, status_data
    if not settled(directory, cfg, state):
        raise ValueError('Research sessions, requests and evaluation cleanup must be settled')
    if stage == 'research':
        if not state['sessions'] or any(s['status'] != 'completed' for s in state['sessions']):
            raise ValueError('Research requires completed sessions, not an empty or failed run')
        from tools.dsh_bridge import _evidence
        evidence = _evidence(request, {'offset': 0, 'limit': 100})
        from dsh.terminal_bridge import eligible
        from tools.dsh_bridge import _summary
        summaries = [_summary(directory, state, eid) for eid in state['evaluations']]
        valid_candidates = [s for s in summaries if eligible(s) and s.get('dataset_purpose') == 'anchor'
                            and s.get('candidate_id') != 'baseline']
        outcome = 'measured' if valid_candidates else 'no_valid_candidate'
        return dict(validated=True, run_id=request['run_id'], outcome=outcome,
                    valid_candidate_evaluations=len(valid_candidates),
                    artifacts=[dict(sessions=state['sessions']), evidence])
    if stage == 'terminal':
        value = status_data(directory, cfg, state)
        if not value['complete']:
            raise ValueError('Final evaluations, report and visualization are not complete')
        return dict(validated=True, run_id=request['run_id'], artifacts=[value])
    raise ValueError('Unknown stage')
