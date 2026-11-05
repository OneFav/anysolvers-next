import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from .engine import Conflict


def handler(engine, config, web_root):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'AnySolversNext/0.7'

        def log_message(self, *_):
            pass  # Never put capability tokens or message bodies in access logs.

        def reply(self, code, value):
            data=json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)

        def authorized(self, env_name):
            expected=os.environ.get(env_name,'')
            actual=self.headers.get('Authorization','').removeprefix('Bearer ')
            return bool(expected) and hmac.compare_digest(expected,actual)

        def operator(self):
            return self.authorized(config.get('operator_token_env','AS_NEXT_OPERATOR_TOKEN'))

        def host(self, host_id):
            hosts=config.get('hosts',{})
            return host_id in hosts and self.authorized(hosts[host_id]['token_env'])

        def do_GET(self):
            parsed=urlsplit(self.path)
            if parsed.path in ('/','/app.js','/style.css'):
                name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[parsed.path]
                data=(Path(web_root)/name).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type',{'index.html':'text/html; charset=utf-8','app.js':'text/javascript','style.css':'text/css'}[name])
                self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
                self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
            if not self.operator():self.reply(401,dict(error='Operator authentication required'));return
            if parsed.path=='/api/state':self.reply(200,engine.snapshot());return
            if parsed.path=='/api/events':
                self.reply(200,dict(events=engine.store.events(parse_qs(parsed.query).get('after',[0])[0])));return
            if parsed.path=='/api/routing':
                pending=[t for t in engine.store.list('triggers') if t['state']=='pending']
                self.reply(200,dict(candidates=[engine.routing_context(t['id']) for t in sorted(pending,key=lambda x:x['received_at'])[:20]]));return
            if parsed.path=='/api/observer/router' and engine.observer:
                value=engine.observer.router_inbox(engine)
                value['reports']=[r for r in value['reports'] if r.get('review_after',0)<=engine.clock()]
                self.reply(200,value);return
            self.reply(404,dict(error='Not found'))

        def do_POST(self):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<=0 or length>1048576:raise ValueError('Request body limit exceeded')
                body=json.loads(self.rfile.read(length))
                path=urlsplit(self.path).path
                if path.startswith('/api/observer/session/'):
                    if not engine.observer:raise ValueError('Observer is disabled')
                    oid,sid=body['observer_id'],body['session_id']
                    token=self.headers.get('Authorization','').removeprefix('Bearer ')
                    args=(oid,sid,token)
                    if path.endswith('/ready'):value=engine.observer.ready(*args)
                    elif path.endswith('/next'):value=engine.observer.next(*args)
                    elif path.endswith('/read'):value=engine.observer.read(*args,body['query'])
                    elif path.endswith('/commit'):value=engine.observer.commit(*args,body['judgment'])
                    elif path.endswith('/rotate'):value=engine.observer.rotate(*args)
                    elif path.endswith('/health'):value=engine.observer.health(*args,body['state'],body['detail'])
                    else:raise ValueError('Unknown observer session operation')
                elif path.startswith('/api/worker/'):
                    aid,token=body['attempt_id'],self.headers.get('Authorization','').removeprefix('Bearer ')
                    if path.endswith('/event'):value=engine.worker_event(aid,token,body['event'])
                    elif path.endswith('/subscribe'):value=engine.subscribe(aid,token,body['thread'])
                    elif path.endswith('/artifact'):value=engine.worker_artifact(aid,token,body)
                    elif path.endswith('/context'):value=engine.worker_context(aid,token,body.get('ack'))
                    elif path.endswith('/tool-begin'):value=engine.tool_begin(aid,token,body['operation_id'],body['name'],body['arguments'])
                    elif path.endswith('/tool-finish'):value=engine.tool_finish(aid,token,body['operation_id'],body['result'])
                    else:raise ValueError('Unknown worker operation')
                elif path.startswith('/api/host/'):
                    hid=body['host_id']
                    if not self.host(hid):self.reply(401,dict(error='Host authentication required'));return
                    if path.endswith('/heartbeat'):value=engine.host_heartbeat(hid,body.get('workers',[]))
                    elif path.endswith('/claim'):value=engine.claim(hid,body['job_id'])
                    elif path.endswith('/recover'):value=engine.recover_attempt(hid,body['job_id'],body['evidence'])
                    elif path.endswith('/observer-jobs'):
                        if not engine.observer:raise ValueError('Observer is disabled')
                        for job in engine.store.list('jobs'):
                            if job['stage']=='research':engine.observer.register_job(job)
                        scope={j for o in engine.observer.public() for j in o['jobs']}
                        value=[{k:j[k] for k in ('id','project_id')} for j in engine.store.list('jobs') if j['host_id']==hid and j['id'] in scope]
                    elif path.endswith('/observer-ingest'):
                        if not engine.observer:raise ValueError('Observer is disabled')
                        for record in body['records']:
                            job=engine.store.get('jobs',record.get('job_id',''))
                            if not job or job['host_id']!=hid or job['stage']!='research':raise ValueError('Observation outside host scope')
                        value=engine.observer.ingest(body['records'])
                    else:raise ValueError('Unknown host operation')
                else:
                    if not self.operator():self.reply(401,dict(error='Operator authentication required'));return
                    if path=='/api/receive':value=engine.receive(body)
                    elif path=='/api/router-health':value=engine.router_health(body['state'],body['detail'])
                    elif path=='/api/decide':value=engine.decide(body['event_id'],body['decision'])
                    elif path.startswith('/api/observer/router/'):
                        if not engine.observer:raise ValueError('Observer is disabled')
                        if path.endswith('/k'):value=engine.observer.replace_k(body['expected_revision'],body['people'])
                        elif path.endswith('/scope'):value=engine.observer.configure(**body)
                        elif path.endswith('/review'):value=engine.observer.review(engine,body['report_id'],body['decision'])
                        elif path.endswith('/feedback'):value=engine.observer.feedback(engine,body['report_id'],body['trigger_id'],body['interpretation'])
                        elif path.endswith('/evidence'):value=engine.observer.router_read(engine,**body)
                        else:raise ValueError('Unknown Router observer operation')
                    elif path=='/api/version':engine.register_version(body);value=dict(ok=True)
                    elif path=='/api/framework-dispatch':value=engine.dispatch_framework(body['request_id'])
                    else:raise ValueError('Unknown operator operation')
                self.reply(200,dict(ok=True,value=value))
            except Conflict as exc:self.reply(409,dict(error=str(exc)))
            except (ValueError,KeyError,TypeError) as exc:self.reply(400,dict(error=str(exc)))
            except Exception:self.reply(500,dict(error='Internal error; preserve execution state'))
    return Handler


def serve(engine, config, web_root, stop=None):
    stop=stop or threading.Event()
    address=config.get('listen','127.0.0.1')
    if address not in ('127.0.0.1','localhost','::1') and not config.get('behind_tls_proxy'):
        raise ValueError('Remote listening requires behind_tls_proxy=true and an HTTPS reverse proxy')
    if not os.environ.get(config.get('operator_token_env','AS_NEXT_OPERATOR_TOKEN')):
        raise ValueError('Set the operator token environment variable before starting the service')
    server=ThreadingHTTPServer((address,int(config.get('port',8787))),handler(engine,config,web_root))
    from .observer_runtime import start
    start(engine,Path(__file__).resolve().parents[1],stop)
    def monitor():
        while not stop.wait(3):engine.monitor()
    threading.Thread(target=monitor,daemon=True).start()
    try:server.serve_forever(poll_interval=.5)
    finally:stop.set();server.server_close()
