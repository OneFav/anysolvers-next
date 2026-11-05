import json
import urllib.request
import urllib.error


class Client:
    def __init__(self, url, token):
        self.url,self.token=url.rstrip('/'),token
        if not self.url.startswith(('http://127.0.0.1:', 'http://localhost:', 'https://')):
            raise ValueError('Use HTTPS for a remote control plane')

    def request(self, path, payload=None):
        request=urllib.request.Request(self.url+path,data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode(),
                    headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)
        except urllib.error.HTTPError as exc:
            body=json.loads(exc.read(10000))
            raise ValueError(body.get('error','HTTP request failed')) from None
