"""Export reviewed public source, excluding credentials, runs and private validation records."""
import argparse
import hashlib
import re
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from controlplane.versions import inventory
TOP={'.gitignore','AGENTS.md','README.md','INSTALL.md','NOTICE.md','config','controlplane','cordis.patch.yml','examples','icon.svg','locale','package-lock.json','package.json','packages','runtime','scripts','src','tests','tsconfig.json','.github'}
DOCS={'PATHS.md','RECOVERY.md','IMPLEMENTATION.md','OBSERVER.md','DEPLOYMENT-ACCEPTANCE.md'}
PRIVATE_SCRIPT_PREFIX=('inspect-live','check-live','inspect-observer-signals','assess-observer-signals','replay-communication-model','check-communication-model')

def export(target):
    if target.exists() and any(target.iterdir()):raise ValueError('Export requires an empty destination')
    target.mkdir(parents=True,exist_ok=True)
    count=0
    for name in inventory(ROOT):
        path=Path(name)
        if path.parts[0]=='docs':
            if path.name not in DOCS:continue
        elif path.parts[0] not in TOP:continue
        if path.parts[0]=='scripts' and path.name.startswith(PRIVATE_SCRIPT_PREFIX):continue
        if '/lib/' in '/'+name or name.startswith('lib/'):continue
        raw=(ROOT/name).read_bytes()
        try:text=raw.decode('utf-8')
        except UnicodeDecodeError:
            if path.suffix not in ('.png',):raise ValueError('Unreviewed binary: '+name)
        else:
            if name=='docs/OBSERVER.md':
                text=text.split('后续已执行 [真实模型与候选验收]')[0]+'真实部署证据见 DEPLOYMENT-ACCEPTANCE.md。持续研究质量、跨机器故障和 Windows 实机不能由上述固定测试推断。\n'
            # Public fixtures retain equality relationships, never live identities.
            if name.startswith('tests/fixtures/'):
                text=re.sub(r'\b(oc|ou|cli)_[A-Za-z0-9]{16,}',lambda m:m.group(1)+'_fixture'+hashlib.sha256(m.group().encode()).hexdigest()[:8],text)
            text=text.replace('/home/example/','/home/example/').replace('compute-node','compute-node')
            if re.search(r'\b(oc|ou|cli)_[A-Za-z0-9]{16,}',text):raise ValueError('Live platform identifier in '+name)
            if re.search(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{30,}',text):raise ValueError('Potential secret in '+name)
            raw=text.encode()
        output=target/name;output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(raw);count+=1
    print('Exported '+str(count)+' reviewed public files')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('destination',type=Path);export(p.parse_args().destination.resolve())
