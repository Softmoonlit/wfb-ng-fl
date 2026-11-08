"""Read-only standard-module identity probe; never inspects resident sys.modules."""
import hashlib
import importlib
import json
from pathlib import Path
import socket
import subprocess

COMMIT = 'c98853fa008a945e3a87503051bae49f5d0083bd'
RELEASE = Path('/srv/wfb-ng-fl/releases/heartbeat-c98853fa008a-20261108T093030Z')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


host = socket.gethostname()
role = 'server' if host == 'fl00' else 'client'
unit = 'wfb-fl-' + role + '-daemon.service'
pid = int(subprocess.check_output(['systemctl', 'show', unit, '-p', 'MainPID', '--value'], text=True))
assert pid > 0
proc = Path('/proc', str(pid))
argv = [part.decode() for part in (proc / 'cmdline').read_bytes().split(b'\0') if part]
assert argv[:5] == ['/usr/bin/python3', '-I', '-B', '-m', 'wfb_ng.fl.' + role + '_daemon'], argv
assert (proc / 'exe').resolve() == Path('/usr/bin/python3').resolve()
manifest = json.loads((RELEASE / 'manifest.json').read_text())
assert manifest['commit'] == COMMIT
base = Path(manifest['installed']).resolve()
assert base == Path('/usr/local/lib/python3.10/dist-packages/wfb_ng/fl')
source = RELEASE / 'source/wfb_ng/fl'
files = manifest['files']
assert {p.name for p in base.glob('*.py')} == {p.name for p in source.glob('*.py')} == {n for n in files if '/' not in n and n.endswith('.py')}
for name, digest in files.items():
    relative = Path(name)
    assert not relative.is_absolute() and '..' not in relative.parts
    assert sha(base / name) == digest, name
    if name != 'build_identity.json':
        assert sha(source / name) == digest, name
assert json.loads((base / 'build_identity.json').read_text())['commit'] == COMMIT
mapped = {}
for name in ('wfb_ng.fl.' + role + '_daemon', 'wfb_ng.fl.role', 'wfb_ng.fl.radio', 'wfb_ng.fl.control', 'wfb_ng.fl.transport'):
    module = importlib.import_module(name)
    path = Path(module.__file__).resolve()
    assert path.parent == base, (name, str(path))
    assert sha(path) == files[path.name], name
    mapped[name] = {'path': str(path), 'sha256': sha(path)}
print(json.dumps({'host': host, 'resident_pid': pid, 'resident_actualargs': argv,
                  'manifest_path': str(RELEASE / 'manifest.json'), 'commit': COMMIT,
                  'mapped_imports': mapped,
                  'verification': 'resident proc identity plus imports in separate isolated probe interpreter; not resident sys.modules'}, indent=2))
