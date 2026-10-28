import ast
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

script = Path('/home/fl/projects/wfb-ng-fl/.scratch/real-fedavg-training/evidence/08-jetson-preflight/heartbeat-149/release-node.py')
source = script.read_text()
tree = ast.parse(source)
# Execute the exact native identity and final tick checks from preflight, without main/system calls.
start = source.index("            natives = [r for r in rows if 'wfb_v6_uplink' in r['exe']]")
end = source.index('            for native in natives:', start)
identity = compile('\n'.join(line[12:] for line in source[start:end].splitlines()), str(script), 'exec')
start = source.index("            require(tick(pid) == process['tick'] and tick(natives[0]['pid']) == natives[0]['tick'],")
end = source.index("            out['backup_sha256']", start)
ticks = compile('\n'.join(line[12:] for line in source[start:end].splitlines()), str(script), 'exec')

def require(condition, message):
    if not condition: raise RuntimeError(message)
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def reject(label, action):
    try: action()
    except RuntimeError as exc: print('PASS reject', label, ':', exc)
    else: raise AssertionError('accepted ' + label)

with tempfile.TemporaryDirectory(prefix='release-native-test-') as temp:
    root = Path(temp)
    old = root / 'source/wfb_v6_uplink'
    old.parent.mkdir(); old.write_bytes(b'old installed native')
    installed = root / 'usr/bin/wfb_v6_uplink'
    installed.parent.mkdir(parents=True); installed.write_bytes(old.read_bytes())
    native = {'pid': 20, 'tick': '200', 'exe': str(installed), 'exe_sha256': sha(old)}
    protected = Mock(side_effect=lambda source, relative: source / relative)
    ns = dict(Path=Path, previous={'root': str(root)}, rows=[native], require=require,
              sha=sha, NATIVE=installed, protected_source_file=protected,
              pid=10, process={'tick': '100'}, tick=lambda pid: {10: '100', 20: '200'}[pid])
    check = lambda: exec(identity, ns)
    finish = lambda: exec(ticks, ns)
    check(); finish(); protected.assert_called_with(root / 'source', 'wfb_v6_uplink')
    print('PASS previous artifact == installed native == running proc exe digest; protected path checked')
    installed.write_bytes(b'replaced installed native'); reject('installed native replaced', check)
    installed.write_bytes(old.read_bytes())
    native['exe_sha256'] = hashlib.sha256(b'replaced running native').hexdigest(); reject('running native replaced', check)
    native['exe_sha256'] = sha(old)
    old.write_bytes(b'replaced old artifact'); reject('previous artifact replaced', check)
    old.write_bytes(installed.read_bytes())
    native['exe'] = str(installed) + ' (deleted)'; reject('running native path changed', check)
    native['exe'] = str(installed)
    ns['rows'] = []; reject('missing native', check)
    ns['rows'] = [native, dict(native)]; reject('duplicate native', check)
    ns['rows'] = [native]; check()
    ns['tick'] = lambda pid: {10: '100', 20: '201'}[pid]; reject('native tick changed at completion', finish)
    ns['tick'] = lambda pid: {10: '101', 20: '200'}[pid]; reject('daemon tick changed at completion', finish)
    ns['tick'] = lambda pid: {10: '100', 20: '200'}[pid]; check(); finish()
    print('PASS restored valid native and ticks')
