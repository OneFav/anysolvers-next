"""Durable delivery admission. Only connection status history is coalesced.

Evidence and messages are retained; supersession is a delivery decision, never
permission to resume a job. Unknown sends remain unknown until receipt proof.
"""
import json


def connection_notice(message):
    job = message.get('job_id')
    ref = message.get('reference', '')
    if not job or message.get('purpose') not in ('incident', 'recovery'):
        return None
    prefixes = ((f'incident:{job}:host_lost:', 'lost'),
                (f'recovered:{job}:host_lost:', 'restored'),
                ('connection-restored:', 'restored'), ('recovery-pending:', 'waiting_operations'))
    for prefix, phase in prefixes:
        if ref.startswith(prefix):
            try: episode = int(ref.rsplit(':', 1)[-1])
            except ValueError: return None
            attempt = ref[len(prefix):].rsplit(':', 1)[0] if prefix == 'connection-restored:' else None
            return dict(phase=phase, episode=episode, attempt=attempt)
    return None


def prepare(tx, now, chats):
    """Migrate ambiguous legacy attempts, then discard obsolete unsent status.

    Called in the admission transaction as well as before enumerating work, so a
    heartbeat that resolves an incident cannot race an earlier queue snapshot.
    Only small incident/recovery rows are read here, never tool or research logs.
    """
    changes = []
    rows = tx.connection.execute("""SELECT body FROM records WHERE kind='outbox'
      AND json_extract(body,'$.state')='pending'
      AND (json_extract(body,'$.attempted_at') IS NOT NULL OR json_extract(body,'$.attempts')>0)""")
    for row in rows.fetchall():
        message = json.loads(row[0])
        if message.get('channel', {}).get('chat_id') not in chats: continue
        message.update(state='unknown', receipt_policy='reconcile_unknown', due_at=now,
                       error='Legacy send may have reached Feishu; receipt lookup required, no automatic resend.')
        tx.put('outbox', message['id'], message)
        changes.append(dict(id=message['id'], state='unknown'))
    groups = {}
    rows = tx.connection.execute("""SELECT body FROM records WHERE kind='outbox'
      AND json_extract(body,'$.purpose') IN ('incident','recovery')
      AND json_extract(body,'$.job_id') IN (
        SELECT json_extract(body,'$.job_id') FROM records WHERE kind='outbox'
        AND json_extract(body,'$.state')='pending'
        AND json_extract(body,'$.purpose') IN ('incident','recovery'))""")
    for row in rows.fetchall():
        message = json.loads(row[0]); notice = connection_notice(message)
        chat = message.get('channel', {}).get('chat_id')
        if notice and chat in chats:
            groups.setdefault((message['job_id'], chat), []).append((message, notice))
    for (job_id, _), items in groups.items():
        pending = [(m, n) for m, n in items if m['state'] == 'pending']
        if not pending: continue
        job = tx.get('jobs', job_id)
        incident = tx.get('incidents', job_id+':host_lost')
        if not job or not incident: continue  # Missing proof is not proof of obsolescence.
        phase = 'restored' if incident['state'] == 'resolved' else 'lost'
        if incident['state'] == 'open' and (job.get('connection_recovery') or {}).get('phase') == 'waiting_operations':
            phase = 'waiting_operations'
        terminal = job['state'] in ('completed', 'stopped')
        eligible = [(m, n) for m, n in pending if not terminal and n['phase'] == phase
                    and n['episode'] == incident['episode']
                    and (not n['attempt'] or n['attempt'] == job.get('attempt_id'))]
        latest = max(eligible, key=lambda pair: (pair[0]['created_at'], pair[0]['id']), default=None)
        # A whole outage that happened while delivery was offline need not create
        # an unsolicited recovery message. A visible/ambiguous outage DOES need it.
        visible = [(m, n) for m, n in items if m['state'] in ('sent','sending','unknown')]
        last_visible = max(visible, key=lambda pair: (pair[1]['episode'], pair[0]['created_at'],
                           pair[1]['phase'] == 'restored', pair[0]['id']), default=None)
        if phase == 'restored' and (not last_visible or last_visible[1]['phase'] == 'restored'):
            latest = None
        keep = latest[0]['id'] if latest else None
        for message, _ in pending:
            if message['id'] == keep: continue
            message.update(state='superseded', superseded_at=now, superseded_by=keep,
                           superseded_reason='Connection status is obsolete or the outage was never delivered.')
            tx.put('outbox', message['id'], message)
            changes.append(dict(id=message['id'], state='superseded', superseded_by=keep))
    for change in changes:
        tx.emit('delivery-admission:'+change['id']+':'+change['state'], 'delivery.admission', now, change)
    return changes


def due(tx, now, chats, states, limit):
    placeholders = ','.join('?' for _ in states)
    chat_slots = ','.join('?' for _ in chats)
    if not chats: return []
    rows = tx.connection.execute(f"""SELECT body FROM records WHERE kind='outbox'
      AND json_extract(body,'$.state') IN ({placeholders})
      AND json_extract(body,'$.channel.chat_id') IN ({chat_slots})
      AND COALESCE(json_extract(body,'$.due_at'),0)<=?
      ORDER BY json_extract(body,'$.created_at'), id LIMIT ?""", (*states, *sorted(chats), now, limit))
    return [json.loads(row[0]) for row in rows.fetchall()]
