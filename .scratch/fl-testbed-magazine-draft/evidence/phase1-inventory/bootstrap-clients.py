import hashlib
import json
import os
import pathlib
import socket
import subprocess
import tarfile

host = socket.gethostname()
assert host in ('fl07', 'fl09'), host
assert os.geteuid() == 0
commit = '5443ca22658202805317a9a974c261cde7c751b9'
archive = pathlib.Path('/tmp/bootstrap-source.tar')
native = pathlib.Path('/tmp/wfb_v6_uplink')
assert hashlib.sha256(archive.read_bytes()).hexdigest() == '400a4beb4ea9c5b1fd3cfe1c5970036d9785cf04b5b975683455a94df1ff1e59'
assert hashlib.sha256(native.read_bytes()).hexdigest() == '1be18f71f1c41884756cf4549cb335fe2baa7b53c8b37c47e113eb97f7610127'

def run(args, **kwargs):
    print(json.dumps({'command': args}), flush=True)
    p = subprocess.run(args, capture_output=True, text=True, timeout=45, **kwargs)
    print(json.dumps({'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}), flush=True)
    p.check_returncode()
    return p.stdout

native.chmod(0o755)
assert 'not found' not in run(['ldd', str(native)])
root = pathlib.Path('/srv/wfb-ng-fl/bootstrap-5443ca-20261106')
root.mkdir(parents=True, exist_ok=False)
with tarfile.open(archive) as tar:
    assert all(not pathlib.Path(m.name).is_absolute() and '..' not in pathlib.Path(m.name).parts and (m.isfile() or m.isdir()) for m in tar.getmembers())
    tar.extractall(root)
(root/'wfb_v6_uplink').write_bytes(native.read_bytes())
(root/'wfb_v6_uplink').chmod(0o755)
env = dict(os.environ, COMMIT=commit, VERSION='26.11.3', PREFIX='/usr', PYTHON='python3', PYTHONDONTWRITEBYTECODE='1')
run(['sh','scripts/install/install-v8.sh'],cwd=root,env=env)
node = int(host[2:])
path = pathlib.Path('/etc/wfb-ng-fl/node.json')
path.write_text(json.dumps({'node_id':node,'tun_ip':f'10.80.0.{10+node}/24'},indent=2)+'\n')
work = pathlib.Path('/srv/wfb-ng-fl/client')
work.mkdir(parents=True,exist_ok=True)
drop = pathlib.Path('/etc/systemd/system/wfb-fl-client-daemon.service.d/phase1-baseline.conf')
drop.parent.mkdir(parents=True,exist_ok=True)
drop.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/wfb-fl-client-daemon --config /etc/wfb-ng-fl/node.json --work-dir /srv/wfb-ng-fl/client --channel 149 --bandwidth-mhz 40 --radio-txpower-dbm 14 --uplink-mcs 4\n')
run(['systemctl','daemon-reload'])
run(['systemctl','enable','wfb-fl-client-daemon'])
print(json.dumps({'host':host,'installed_commit':commit,'state':'installed-not-started'}),flush=True)
