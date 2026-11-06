"""Feishu CLI contract follows DeepSeek's official prototype (MIT).

Authentication stays in the operator-configured lark-cli. No account files or
credentials are opened here. Stable outgoing IDs survive retries and restarts.
"""
import json
import os
import subprocess
import shutil
import time
import threading
from pathlib import Path
from .engine import digest


def normalize(raw, config):
    if raw.get('schema') == '2.0':
        event = raw.get('event', {})
        message = event.get('message', {})
        sender = event.get('sender', {})
        raw = dict(message, sender_id=sender.get('sender_id', {}).get('open_id'),
                   sender_type=sender.get('sender_type'), content=message.get('content', ''),
                   mentions=[dict(id=m.get('id', {}).get('open_id'), key=m.get('key'), name=m.get('name')) for m in message.get('mentions', [])])
    for field in ('message_id', 'chat_id', 'sender_id', 'content'):
        if not isinstance(raw.get(field), str) or not raw[field]:
            raise ValueError('Malformed Feishu event: '+field)
    chat = next((c for c in config.get('chats', []) if c['chat_id'] == raw['chat_id']), None)
    if not chat or raw['sender_id'] in (config.get('bot_id'), config.get('app_id')):
        return None
    # Human context can reach subscriptions; Engine separately checks per-chat execution authority.
    if raw.get('sender_type') != 'user':
        return None
    content = raw['content']
    try:
        body = json.loads(content)
        if isinstance(body, dict):
            content = body.get('text') or json.dumps(body, ensure_ascii=False)
    except (ValueError, TypeError):
        pass
    for mention in raw.get('mentions', []):
        if mention.get('key') and mention.get('name'):
            content = content.replace(mention['key'], '@'+mention['name'])
    message_id = raw['message_id']
    parent_id = raw.get('parent_id') or raw.get('reply_to')
    root_id = raw.get('root_id') or parent_id or message_id
    return dict(id='feishu:'+message_id, source='feishu', thread=raw['chat_id']+':'+root_id,
                actor=raw['sender_id'], text=content, bot=False, at=raw.get('create_time'),
                reply_to='feishu:'+parent_id if parent_id else None,
                channel=dict(chat_id=raw['chat_id'], message_id=message_id),
                meta=dict(root_id=root_id, thread_id=raw.get('thread_id'), mentions=raw.get('mentions', [])))


class Feishu:
    def __init__(self, engine, config, run=subprocess.run):
        self.engine, self.config, self.run = engine, config, run

    def environment(self, setup=False):
        # Each deployment owns its CLI configuration. Never inherit another app's
        # credentials, auth proxy, profile, or agent-workspace selection.
        excluded = {'LARKSUITE_CLI_APP_ID','LARKSUITE_CLI_APP_SECRET','LARKSUITE_CLI_BRAND','LARKSUITE_CLI_USER_ACCESS_TOKEN',
                    'LARKSUITE_CLI_TENANT_ACCESS_TOKEN','LARKSUITE_CLI_TENANT_ACCESS_TOKEN_SOURCE',
                    'LARKSUITE_CLI_AUTH_PROXY','LARKSUITE_CLI_PROXY_KEY','LARKSUITE_CLI_PROFILE',
                    'LARKSUITE_CLI_STRICT_MODE','LARK_CHANNEL'}
        env = {k:v for k,v in os.environ.items() if k not in excluded and not k.startswith(('OPENCLAW_', 'HERMES_'))}
        env.update(LARKSUITE_CLI_CONFIG_DIR=str(Path(self.config.get('cli_config_dir','.state/feishu-cli')).resolve()),
                   LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1',LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1',
                   LARKSUITE_CLI_DEFAULT_AS='bot')
        if not setup: env['LARKSUITE_CLI_STRICT_MODE']='bot'
        if self.config.get('cli_profile'): env['LARKSUITE_CLI_PROFILE']=self.config['cli_profile']
        return env

    def health(self, component, state, detail):
        if self.engine is None:return
        key='feishu:'+component
        with self.engine.store.transaction() as tx:
            old=tx.get('source_health',key)
            value=dict(id=key,component=component,state=state,detail=detail,at=self.engine.clock())
            tx.put('source_health',key,value)
            if old is None or old['state']!=state or old['detail']!=detail:
                tx.emit('source:'+key+':'+digest(value),'source.health',value['at'],value)

    def command(self, args):
        command = self.config.get('cli_command', ['lark-cli'])
        if not isinstance(command, list) or not command or not all(isinstance(x,str) for x in command):
            raise ValueError('cli_command must be an argument array')
        resolved=shutil.which(command[0]) or command[0]
        if os.name == 'nt' and resolved.lower().endswith(('.cmd','.bat')):
            raise ValueError('Use the CLI executable or node + CLI entry.js, not a Windows batch wrapper')
        return [resolved,*command[1:],*args]

    def call(self, args, *, cwd=None):
        args=list(args)
        if args[0] in ('api','im'):
            if '--as' not in args:args+=['--as','bot']
            # Record formats strip pagination metadata and may emit zero lines for
            # a chat with empty arrays. Keep the complete JSON business envelope.
            if args[0]=='api' and '--format' not in args:args+=['--format','json']
        result = self.run(self.command(args), check=True, capture_output=True, text=True, timeout=45,
                          encoding='utf-8', env=self.environment(), **({'cwd':str(cwd)} if cwd is not None else {}))
        if not result.stdout.strip():raise ValueError('Feishu CLI returned no structured response')
        value = json.loads(result.stdout)
        if not isinstance(value,dict) or value.get('ok') is False or value.get('code', 0) != 0:
            raise ValueError('Feishu CLI returned an application error')
        return value.get('data', value)

    def receive(self, event):
        trigger = normalize(event, self.config)
        return self.engine.receive(trigger) if trigger else None

    def _payload(self, message):
        """Validate and prepare local bytes before committing a network attempt."""
        text = message['text']
        if message.get('communication_version') == 1 and not message.get('media'):
            from .communication import notification
            notification(text, message.get('purpose'), message.get('reference'))
        import re
        for open_id, label in ([(message['mention'], '负责人')] if message.get('mention') else []) + [(x, '相关人员') for x in message.get('mentions', [])]:
            if not re.fullmatch(r'ou_[A-Za-z0-9_-]+', open_id):
                raise ValueError('A Feishu mention requires an open_id')
            text = '<at user_id="'+open_id+'">'+label+'</at> '+text
        base = ['im', '+messages-reply', '--message-id', message['channel']['message_id'],
                '--reply-in-thread', '--idempotency-key', message['id']]
        if not message.get('media'): return [*base, '--markdown', text], None
        import base64, hashlib
        media=message['media']; raw=base64.b64decode(media['content'], validate=True)
        if media['mime']!='image/png' or hashlib.sha256(raw).hexdigest()!=media['sha256'] or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Outgoing PNG integrity mismatch')
        directory=Path(self.engine.store.path).parent/'outgoing-media'; directory.mkdir(exist_ok=True)
        file=directory/(media['sha256']+'.png')
        try:
            with file.open('xb') as stream: stream.write(raw)
        except FileExistsError:
            if file.is_symlink() or file.read_bytes()!=raw: raise ValueError('Outgoing media cache changed')
        return [*base, '--image', file.name], directory

    def flush(self):
        if not self.config.get('enabled'): return
        from . import delivery
        chats = {c['chat_id'] for c in self.config.get('chats', [])}
        now = self.engine.clock()
        with self.engine.store.transaction() as tx:
            delivery.prepare(tx, now, chats)
            uncertain = delivery.due(tx, now, chats, ('sending', 'unknown'), 2)
            pending = sorted([m for chat in sorted(chats)
                for m in delivery.due(tx, now, {chat}, ('pending',), 8)],
                key=lambda m: (m['created_at'], m['id']))
        # Bound receipt I/O separately, so an unresolved old receipt cannot block
        # new human decisions. It can never authorize an automatic retransmission.
        for message in uncertain:
            with self.engine.store.transaction() as tx:
                fresh=tx.get('outbox', message['id'])
                if fresh['state'] not in ('sending','unknown') or fresh.get('due_at',0)>self.engine.clock(): continue
                fresh['due_at']=self.engine.clock()+60
                tx.put('outbox', fresh['id'], fresh)
            self.reconcile_receipt(fresh)
        sent_chats=set()
        interval=max(1, float(self.config.get('min_send_interval_s',30)))
        for message in pending:
            chat=message['channel']['chat_id']
            if chat in sent_chats: continue
            try:
                args, directory = self._payload(message)
            except (OSError, ValueError, KeyError, TypeError):
                with self.engine.store.transaction() as tx:
                    fresh=tx.get('outbox',message['id'])
                    if fresh == message:
                        fresh.update(state='needs_rewrite',error='Outgoing message failed local validation; no network attempt.')
                        tx.put('outbox',fresh['id'],fresh)
                continue
            with self.engine.store.transaction() as tx:
                now=self.engine.clock()
                delivery.prepare(tx,now,chats)
                fresh=tx.get('outbox',message['id'])
                gate=tx.get('delivery_channels',chat) or {}
                if fresh != message or fresh['state']!='pending' or gate.get('next_send_at',0)>now: continue
                # All text, images and observer reports use the same durable claim.
                # The per-chat gate survives crash/restart and multiple consumers.
                message.update(state='sending',receipt_policy='reconcile_unknown',
                               attempted_at=now,due_at=now+60,attempts=message.get('attempts',0)+1)
                tx.put('outbox',message['id'],message)
                tx.put('delivery_channels',chat,dict(id=chat,next_send_at=now+interval,last_delivery_id=message['id']))
            sent_chats.add(chat)
            try:
                reply=self.call(args,**({'cwd':directory} if directory else {}))
                receipt={k:reply[k] for k in ('message_id','msg_type') if k in reply}
                if not receipt.get('message_id'): raise ValueError('Missing Feishu delivery receipt')
                message.update(state='sent',sent_at=self.engine.clock(),delivery_receipt=receipt)
                message.pop('error',None)
            except (OSError,ValueError,subprocess.SubprocessError):
                message.update(state='unknown',due_at=self.engine.clock()+60,
                               error='Feishu send outcome unknown; receipt lookup required, no automatic resend.')
            with self.engine.store.transaction() as tx:
                current=tx.get('outbox',message['id'])
                if current['state']!='sent': tx.put('outbox',message['id'],message)

    def reconcile_receipt(self,message):
        """Unknown sends are read-checked first. Unprovable absence never resends.

        Feishu deployments may expose uuid/idempotency_key on history items. When
        unavailable, preserve unknown for operator receipt reconciliation instead
        of treating an empty/eventually-consistent history as proof of absence.
        """
        try:
            channel=message['channel'];page=None
            for _ in range(3):
                params=dict(container_id_type='chat',container_id=channel['chat_id'],page_size=50,
                    start_time=str(max(0,int(message.get('attempted_at',message['created_at']))-5)),
                    sort_type='ByCreateTimeDesc',**({'page_token':page} if page else {}))
                value=self.call(['api','GET','/open-apis/im/v1/messages','--params',json.dumps(params)])
                found=next((item for item in value.get('items',[]) if item.get('uuid')==message['id'] or item.get('idempotency_key')==message['id']),None)
                if found and found.get('message_id'):
                    message.update(state='sent',sent_at=self.engine.clock(),delivery_receipt=dict(message_id=found['message_id'],reconciled=True))
                    break
                if not value.get('has_more'):break
                page=value.get('page_token')
                if not page:break
            if message['state']!='sent':
                message.update(state='unknown',due_at=self.engine.clock()+60,error='Receipt checked; delivery still unknown. No automatic resend.')
        except (OSError,ValueError,subprocess.SubprocessError):
            message.update(state='unknown',due_at=self.engine.clock()+60,error='Receipt lookup unavailable; no automatic resend.')
        with self.engine.store.transaction() as tx:
            current=tx.get('outbox',message['id'])
            if current and current['state']=='sent':return
            tx.put('outbox',message['id'],message)

    # Compatibility for operators using the older observer-specific entry point.
    reconcile_observer_receipt = reconcile_receipt

    def backfill(self):
        """Advance each cursor only after a complete page range is durably ingested."""
        for chat in self.config.get('chats', []):
            key = 'feishu:'+chat['chat_id']
            old = self.engine.store.get('cursors', key)
            started = int(time.time())
            if old is None:
                with self.engine.store.transaction() as tx:
                    tx.put('cursors', key, dict(id=key, since=started))
                continue
            topics=set(old.get('topics',[]))
            for trigger in self.engine.store.list('triggers'):
                if trigger['source']=='feishu' and trigger['channel']['chat_id']==chat['chat_id'] and trigger.get('meta',{}).get('thread_id'):
                    topics.add(trigger['meta']['thread_id'])
            def scan(kind,container):
                page=None;seen=set()
                for _ in range(1000):
                    args=['api','GET','/open-apis/im/v1/messages','--params',json.dumps(dict(container_id_type=kind,container_id=container,
                        start_time=str(max(0,old['since']-5)),end_time=str(started),sort_type='ByCreateTimeAsc',page_size=50,**({'page_token':page} if page else {})))]
                    value=self.call(args)
                    for item in value.get('items',[]):
                        if item.get('chat_id') != chat['chat_id'] or kind=='thread' and item.get('thread_id')!=container:
                            raise ValueError('Feishu history returned messages outside the requested container')
                        if item.get('deleted'):continue
                        if item.get('thread_id'):topics.add(item['thread_id'])
                        sender=item.get('sender',{})
                        self.receive(dict(item,sender_id=sender.get('id'),sender_type=sender.get('sender_type'),content=item.get('body',{}).get('content','')))
                    if not value.get('has_more'):return
                    page=value.get('page_token')
                    if not page or page in seen:raise ValueError('Feishu history did not advance')
                    seen.add(page)
                raise ValueError('Feishu history exceeded bounded pagination')
            scan('chat',chat['chat_id'])
            for topic in sorted(topics):scan('thread',topic)
            with self.engine.store.transaction() as tx:
                tx.put('cursors', key, dict(id=key, since=started,topics=sorted(topics)))

    def consume(self, stop):
        """The CLI's documented ready marker, not process creation, proves listening."""
        child=subprocess.Popen(self.command(['event','consume','im.message.receive_v1','--as','bot']),
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               text=True,encoding='utf-8',env=self.environment())
        ready=threading.Event();finished=threading.Event();started=time.monotonic()
        self.health('stream','connecting','正在等待飞书长连接就绪')
        def stderr():
            for line in child.stderr:
                if line.startswith('[event] ready'):
                    ready.set();self.health('stream','ready','正在接收机器人消息')
                elif any(word in line.lower() for word in ('error','warn','drop')):
                    # Do not persist raw CLI diagnostics, which can contain URLs or credentials.
                    self.health('stream','error','飞书连接报告异常，请运行接入检查')
        def guard():
            while not finished.wait(.2):
                if stop.is_set() or (not ready.is_set() and time.monotonic()-started>45):
                    if child.poll() is None:child.terminate()
                    return
        readers=[threading.Thread(target=stderr,daemon=True),threading.Thread(target=guard,daemon=True)]
        for thread in readers:thread.start()
        try:
            while not stop.is_set():
                line=child.stdout.readline(1048577)
                if not line:break
                if len(line)>1048576:raise ValueError('Feishu event exceeds 1 MiB')
                try:self.receive(json.loads(line))
                except (ValueError,TypeError,KeyError):self.health('events','error','收到无法解析的消息，历史补拉将重试')
        finally:
            finished.set()
            if child.poll() is None:child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()
            for pipe in (child.stdin,child.stdout):pipe.close()
            for thread in readers:thread.join(timeout=2)
            child.stderr.close()
        if not stop.is_set():raise ValueError('Feishu event consumer disconnected')

    def serve(self, stop):
        # Local leases prevent two managed instances from dispatching the same
        # group. Across machines designate one owner per group explicitly.
        import tempfile, hashlib
        from contextlib import ExitStack
        from .processes import host_lock
        scope=str(Path.home())+'\0'+self.config.get('app_id',str(self.config.get('cli_config_dir','')))
        def lease(kind):
            return Path(tempfile.gettempdir())/('anysolvers-feishu-'+hashlib.sha256((scope+'\0'+kind).encode()).hexdigest()+'.lock')
        try:
            with ExitStack() as stack:
                for chat in sorted(self.config.get('chats',[]),key=lambda c:c['chat_id']):
                    stack.enter_context(host_lock(lease('chat:'+chat['chat_id'])))
                if self.config.get('receive_mode','stream')=='history':
                    self.health('stream','disabled','历史轮询模式；不创建额外长连接')
                    self.maintenance(stop);return
                stack.enter_context(host_lock(lease('stream')))
                threading.Thread(target=self.maintenance,args=(stop,),daemon=True).start()
                self.stream(stop)
        except (OSError,RuntimeError):
            self.health('stream','blocked','本机已有接收器或此群已有实例；请核对群归属后停用重复实例')
            stop.wait()

    def maintenance(self, stop):
        while not stop.is_set():
            for name,action in (('delivery',self.flush),('history',self.backfill)):
                try:
                    action()
                    uncertain=False
                    if name=='delivery':
                        import contextlib
                        with contextlib.closing(self.engine.store.connect()) as conn:
                            uncertain=conn.execute("""SELECT 1 FROM records WHERE kind='outbox'
                              AND json_extract(body,'$.state') IN ('unknown','needs_rewrite') LIMIT 1""").fetchone() is not None
                    self.health(name,'error' if uncertain else 'ready','消息待核对回执或修正文案；不会盲目重发' if uncertain else '检查正常')
                except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError):
                    self.health(name,'error','飞书调用失败，保留游标与待发消息重试')
            if stop.wait(10):return

    def stream(self, stop):
        delay=1
        while not stop.is_set():
            started=time.monotonic()
            try:self.consume(stop)
            except (OSError,ValueError,subprocess.SubprocessError):self.health('stream','error','长连接不可用，正在重连')
            if stop.wait(delay):break
            delay=1 if time.monotonic()-started>=60 else min(60,delay*2)
        self.health('stream','stopped','接入进程已停止')
