"""Pinned operator handoff for an interrupted first research session.

No workers create handoffs. This contract only subtracts already-consumed time,
caps admission at the original deadline and blocks lost-result snapshots.
"""
import datetime as dt
import math
from tools import common as c


def contract(cfg):
    ref = cfg.get('modules', {}).get('research', {}).get('params', {}).get('recovery_proof')
    if not ref:
        return None
    raw = c.read_pinned(ref, 'operator recovery proof')
    if len(raw) > 65536:
        raise ValueError('Recovery proof exceeds limit')
    value = c.parse_json(raw.decode())
    if (value.get('schema_version') != 1 or value.get('kind') != 'interrupted-first-session'
            or value.get('destination_run_id') != cfg['run_id']
            or value.get('initialization_sha') != cfg['initialization']['sha']
            or value.get('researchers') != cfg['budgets']['researchers']
            or value.get('rounds_per_researcher') != cfg['budgets']['rounds_per_researcher']
            or value.get('session_wall_s') != cfg['budgets']['session_wall_s']
            or len(value.get('actors', {})) != cfg['budgets']['researchers']):
        raise ValueError('Recovery handoff differs from original research contract')
    deadline = value.get('original_deadline')
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError('Invalid original admission deadline')
    for actor, row in value['actors'].items():
        c.identifier(actor)
        spent = row.get('charged_active_s')
        if (type(spent) not in (int, float) or not math.isfinite(spent)
                or not 0 <= spent <= cfg['budgets']['session_wall_s']
                or row.get('generation') != 1):
            raise ValueError('Invalid carried research time')
    return value


def charge(cfg, session):
    value = contract(cfg)
    if not value:
        return 0.0
    if session['actor'] not in value['actors']:
        raise ValueError('Research actor is outside recovery handoff')
    return value['actors'][session['actor']]['charged_active_s'] if session['generation'] == 1 else 0.0


def remaining(cfg, now=None):
    value = contract(cfg)
    if not value:
        return float('inf')
    at = dt.datetime.fromisoformat(now or c.utc()).timestamp()
    return value['original_deadline'] - at


def assert_open(cfg, now=None):
    if remaining(cfg, now) <= 0:
        raise ValueError('original recovery admission deadline reached')


def reject_replay(cfg, source_sha):
    value = contract(cfg)
    if value and source_sha in value['blocked_source_sha256']:
        raise ValueError('Original failed snapshot is closed without replay; change the method before new evaluation')


def context(run, cfg, session):
    value = contract(cfg)
    if not value or session['generation'] != 1:
        return None
    ref = value['actors'][session['actor']]['context']
    path = c.contained(run, ref['path'])
    if path.stat().st_size > 32768 or c.sha(path) != ref['sha']:
        raise ValueError('Recovery context changed')
    result = c.read_json(path)
    if result['actor'] != session['actor'] or result['source_run_id'] != value['source_run_id']:
        raise ValueError('Recovery context belongs to a different researcher')
    return result


def recovery_context(run, cfg, session):
    return context(run, cfg, session)


def remaining_admission(cfg):
    return remaining(cfg)


def replay_guard(cfg, source_sha):
    return reject_replay(cfg, source_sha)
