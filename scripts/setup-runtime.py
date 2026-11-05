"""Install the plugin's private Python dependencies independently of research projects."""
import argparse
from pathlib import Path
import subprocess
import sys
import venv

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--state-root',type=Path,default=Path.home()/'.dsh/anysolvers')
args=parser.parse_args()
if sys.version_info < (3,10):parser.error('Python 3.10 or newer is required')
directory=args.state_root.expanduser().resolve()/'python'
venv.EnvBuilder(with_pip=True).create(directory)
python=directory/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
requirements=Path(__file__).resolve().parents[1]/'runtime/requirements.txt'
subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(requirements)],check=True)
subprocess.run([str(python),'-c','import yaml,jsonschema,matplotlib; print("AnySolvers runtime ready")'],check=True)
print('DSH study-runtime python: '+str(python))
