"""Coordinator-generated, evidence-grounded directions; never calls or creates a model."""
from tools import common as c
from tools.module_runtime import output_schema
from modules.selections.research_directions import evidence as base
from modules.selections.research_directions import reports


def evidence(run, state, evaluation_id):
    return base._research_evidence(run, state, evaluation_id)


def _records(run, state):
    result = []
    for ref in state.get('extensions', {}).get('research_exchange', {}).values():
        path = c.contained(run, ref['path'])
        if c.sha(path) != ref['sha']:
            raise ValueError('sealed research record changed')
        result.append(c.read_json(path))
    return result


def context(run, cfg, state, actor=None):
    value = base.context(run, cfg, state, actor)
    if actor is None:
        raise ValueError('selection context requires a target actor')
    own = {s['session_id'] for s in state['sessions'] if s['actor'] == actor}
    findings = [r for r in _records(run, state) if r['kind'] == 'finding']
    shared = {r['payload']['candidate']['candidate_id'] for r in findings if r['payload']['candidate']}
    published = {e['evaluation_id'] for r in findings for e in r['payload']['trusted_evidence']}
    own_candidates = {r['candidate_id'] for r in value['candidates'] if r['session_id'] in own}
    value['candidates'] = [r for r in value['candidates'] if r['candidate_id'] in own_candidates | shared]
    value['evaluations'] = [r for r in value['evaluations'] if r['candidate_id'] in own_candidates
                            or r['evaluation_id'] in published or r['candidate_id'] == 'baseline']
    value['iterations'] = [r for r in value['iterations'] if r['actor'] == actor]
    value['findings'] = findings
    return value


def _slot(run, cfg, state, actor):
    c.identifier(actor)
    previous = [s for s in state['sessions'] if s['actor'] == actor]
    if not previous:
        return None
    last = previous[-1]
    if last['status'] != 'completed' or len(previous) >= cfg['budgets']['rounds_per_researcher']:
        return None
    notes = [r for r in _records(run, state) if r['kind'] == 'notebook' and r['session_id'] == last['session_id']]
    if not notes:
        raise ValueError('completed researcher needs a sealed notebook')
    return dict(actor=actor, generation=len(previous)+1, based_on_session=last['session_id'],
                source_notebook_id=notes[-1]['record_id'], notebook=notes[-1],
                previous_starting_idea=last.get('starting_idea'),
                expected_iteration_id='direction-'+c.digest({'session':last['session_id']})[:24])


def pending(run, cfg, state, actor=None):
    base._open_research(state)
    actors = [actor] if actor else sorted({s['actor'] for s in state['sessions']})
    waiting, selected = [], []
    for target in actors:
        slot = _slot(run, cfg, state, target)
        if slot is None:
            continue
        decisions = [r['manifest'] for r in state['deliveries']['iteration'].values()
                     if r['manifest']['actor'] == target and r['manifest']['generation'] == slot['generation']]
        if len(decisions) > 1:
            raise ValueError('ambiguous round direction')
        if decisions:
            selected.append(decisions[0])
        else:
            waiting.append({**slot, 'development':context(run,cfg,state,target)})
    return dict(pending=waiting, selected=selected, pending_reports=reports.pending_reports(run,cfg,state,actor),
                decision_schema=output_schema(cfg['modules']['selections'],'iteration'))


def validate_delivery(run, cfg, state, data, add, entry):
    base.validate_delivery(run, cfg, state, data, add, entry)
    actor = data['actor']
    slot = _slot(run, cfg, state, actor)
    if slot is None or any(data[k] != slot[k] for k in ('actor','generation','based_on_session','source_notebook_id')):
        raise ValueError('direction must bind the completed predecessor and next actor round')
    if data['iteration_id'] != slot['expected_iteration_id']:
        raise ValueError('use the deterministic round direction ID')
    reports.require_reports(run,cfg,state,slot['based_on_session'])
    visible = context(run,cfg,state,actor)
    for key, values in [('evidence_ids', {r['evaluation_id'] for r in visible['evaluations']}),
                        ('history_iteration_ids', {r['iteration_id'] for r in visible['iterations']})]:
        if not set(data[key]).issubset(values):
            raise ValueError('direction references private or unavailable '+key)
    if data['parent_candidate'] and data['parent_candidate'] not in {r['candidate_id'] for r in visible['candidates']}:
        raise ValueError('direction parent is private to another researcher')
    if add is None and data['iteration_id'] not in state['deliveries']['iteration']:
        raise ValueError('continuation requires a sealed selection direction')
    for rec in state['deliveries']['iteration'].values():
        other = rec['manifest']
        if other['actor'] == actor and other['generation'] == data['generation'] and other != data:
            raise ValueError('a round already has a frozen direction')


def submit(run, cfg, state, actor, data):
    from tools.run import deliver
    c.validate_schema(output_schema(cfg['modules']['selections'],'iteration'),data)
    if data['actor'] != actor:
        raise ValueError('target actor differs from direction')
    key = c.identifier(data['iteration_id'])
    existing = state['deliveries']['iteration'].get(key)
    if existing:
        if existing['manifest'] != data:
            raise ValueError('sealed direction cannot be changed')
        return dict(kind='iteration',id=key,cached=True)
    validate_delivery(run,cfg,state,data,True,cfg['modules']['selections'])
    relative = 'research/selections/'+key+'.json'
    path = c.contained(run,relative)
    if path.exists():
        if c.read_json(path) != data:
            raise ValueError('unregistered direction differs; reconcile before retrying')
    else:
        c.write_json(path,data)
    return deliver(run,cfg,state,'iteration',relative,'selections')
