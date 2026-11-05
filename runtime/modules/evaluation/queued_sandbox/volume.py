"""Materialize code through Docker's archive API; never start the staging helper.

Used by remote Docker installations that cannot bind their SSH filesystem.
Only the candidate container starts, after the usual complete isolation checks.
"""
import hashlib,io,subprocess,tarfile,time
from research_runtime.docker import _command,_json_command,_create_args,_cleanup,_read_bounded,InfrastructureError,LABEL

LIMIT=16*1024*1024

def verify_archive(raw, expected):
    actual={}
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:*') as archive:
        for item in archive:
            name=item.name
            if item.isdir() and name in ('.','./'):continue
            if name.startswith('./'):name=name[2:]
            if not item.isfile() or name not in expected or name in actual or item.size>LIMIT:
                raise InfrastructureError('Unexpected staged code archive member')
            actual[name]=archive.extractfile(item).read(LIMIT+1)
    if actual!=expected:raise InfrastructureError('Staged code bytes do not match immutable request')
    return {k:hashlib.sha256(v).hexdigest() for k,v in actual.items()}

class CodeVolume:
    def __init__(self,name,image,limits):
        self.name=name;self.volume=name+'-code';self.helper=name+'-stage';self.image=image;self.limits=limits
        self.volume_attempted=False;self.helper_attempted=False

    def pipe(self,args,payload,deadline):
        process=subprocess.Popen(['docker',*args],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        streams,reason=_read_bounded(process,payload,deadline,LIMIT)
        if reason or process.returncode:raise InfrastructureError('Docker archive operation failed: '+str(reason or process.returncode)+' '+streams['stderr'][:600].decode(errors='replace'))
        return streams['stdout']

    def prepare(self,source,wrapper):
        deadline=time.monotonic()+self.limits['wall_s']
        if _command(['volume','ls','--filter','name=^'+self.volume+'$','--format','{{.Name}}']).strip():
            raise InfrastructureError('Refusing to reuse an existing code volume')
        self.volume_attempted=True
        _command(['volume','create','--driver','local','--label',LABEL+'='+self.name,self.volume])
        info=_json_command(['volume','inspect',self.volume])[0]
        if info['Name']!=self.volume or info['Driver']!='local' or info.get('Options') or info.get('Labels',{}).get(LABEL)!=self.name:
            raise InfrastructureError('Unexpected code volume configuration')
        args=_create_args(self.name,None,None,self.image,self.limits,code_volume=self.volume)
        args[args.index('--name')+1]=self.helper;args[args.index('--label')+1]=LABEL+'='+self.helper
        index=args.index('--mount')+1;args[index]=args[index].replace(',readonly','')
        self.helper_attempted=True;_command(args,max(.001,deadline-time.monotonic()))
        helper=_json_command(['inspect',self.helper])[0]
        if helper['State']['Running'] or helper['Name']!='/'+self.helper or helper['Config']['Labels'].get(LABEL)!=self.helper:
            raise InfrastructureError('Staging helper must remain stopped')
        expected={'solver.py':source.encode(),'entrypoint.py':wrapper.encode()};buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w') as archive:
            for name,data in expected.items():
                item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o444;item.uid=item.gid=0;archive.addfile(item,io.BytesIO(data))
        if len(buffer.getvalue())>LIMIT:raise InfrastructureError('Code archive exceeds bounded size')
        self.pipe(['cp','-',self.helper+':/code'],buffer.getvalue(),deadline)
        hashes=verify_archive(self.pipe(['cp',self.helper+':/code/.','-'],b'',deadline),expected)
        _cleanup(self.helper);self.helper_attempted=False
        return dict(backend='docker-volume',volume=self.volume,hashes=hashes,helper_started=False)

    def cleanup(self):
        if self.helper_attempted:_cleanup(self.helper)
        if self.volume_attempted:
            names=_command(['volume','ls','--filter','name=^'+self.volume+'$','--format','{{.Name}}']).splitlines()
            if self.volume in names:
                info=_json_command(['volume','inspect',self.volume])[0]
                if info.get('Labels',{}).get(LABEL)!=self.name:raise InfrastructureError('Refusing to remove an unowned code volume')
                _command(['volume','rm',self.volume])
            if _command(['volume','ls','--filter','name=^'+self.volume+'$','--format','{{.Name}}']).strip():
                raise InfrastructureError('Code volume cleanup not confirmed')
