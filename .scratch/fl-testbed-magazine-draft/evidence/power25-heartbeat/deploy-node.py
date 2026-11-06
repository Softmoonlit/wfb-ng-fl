import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tarfile

commit, archive_sha = sys.argv[1:]
assert len(commit)==40 and all(c in '0123456789abcdef' for c in commit)
host=socket.gethostname()
assert host in ['fl00','fl01','fl02','fl04','fl05','fl06','fl07','fl08','fl09','fl10'] and os.geteuid()==0
archive=pathlib.Path('/tmp/phase1-source.tar')
native=pathlib.Path('/tmp/phase1-native')
assert hashlib.sha256(archive.read_bytes()).hexdigest()==archive_sha
assert hashlib.sha256(native.read_bytes()).hexdigest()=='1be18f71f1c41884756cf4549cb335fe2baa7b53c8b37c47e113eb97f7610127'
root=pathlib.Path('/srv/wfb-ng-fl/releases')/commit
assert not root.exists(), root
interfaces=[]
for p in pathlib.Path('/sys/class/net').iterdir():
    driver=p/'device/driver'
    if driver.exists() and pathlib.Path(os.path.realpath(driver)).name=='rtl88xxau_wfb':interfaces.append(p.name)
assert len(interfaces)==1, interfaces
iface=interfaces[0]

def run(args,**kwargs):
    p=subprocess.run(args,capture_output=True,text=True,timeout=45,**kwargs)
    print(json.dumps({'command':args,'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr},ensure_ascii=False),flush=True)
    p.check_returncode()
    return p.stdout

unit='wfb-fl-server-daemon' if host=='fl00' else 'wfb-fl-client-daemon'
assert 'not found' not in run(['ldd',str(native)])
old=run(['systemctl','show',unit,'-p','MainPID','--value']).strip()
run(['systemctl','stop',unit])
if old!='0':assert not pathlib.Path('/proc',old).exists(),'old daemon did not exit'
assert not any('wfb_v6_uplink' in os.readlink(p) for p in pathlib.Path('/proc').glob('[0-9]*/exe') if p.exists()),'old native remains'
root.mkdir(parents=True)
with tarfile.open(archive) as tar:
    assert all(not pathlib.Path(m.name).is_absolute() and '..' not in pathlib.Path(m.name).parts and (m.isfile() or m.isdir()) for m in tar.getmembers())
    tar.extractall(root)
shutil.copyfile(native,root/'wfb_v6_uplink');(root/'wfb_v6_uplink').chmod(0o755)
env=dict(os.environ,COMMIT=commit,VERSION='26.11.6',PREFIX='/usr',PYTHON='/usr/bin/python3',PYTHONDONTWRITEBYTECODE='1')
run(['sh','scripts/install/install-v8.sh'],cwd=root,env=env)
backup=root/'previous-config';backup.mkdir()
if host=='fl00':
    path=pathlib.Path('/etc/wfb-ng-fl/server.json');shutil.copy2(path,backup/path.name)
    config=json.loads(path.read_text());config['radio_txpower_dbm']=25;config['air_interface']=iface
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
    args='/usr/bin/python3 -I -m wfb_ng.fl.server_daemon --config /etc/wfb-ng-fl/server.json'
    work='/srv/wfb-ng-fl/server'
else:
    node=int(host[2:]);identity=json.loads(pathlib.Path('/etc/wfb-ng-fl/node.json').read_text())
    assert identity['node_id']==node and identity['tun_ip'].split('/')[0]==f'10.80.0.{10+node}'
    args=f'/usr/bin/python3 -I -m wfb_ng.fl.client_daemon --config /etc/wfb-ng-fl/node.json --work-dir /srv/wfb-ng-fl/client --channel 149 --bandwidth-mhz 40 --radio-txpower-dbm 25 --uplink-mcs 4 --interface {iface}'
    work='/srv/wfb-ng-fl/client'
pathlib.Path(work).mkdir(parents=True,exist_ok=True)
drop=pathlib.Path('/etc/systemd/system')/(unit+'.service.d')/'zz-phase1-baseline.conf'
drop.parent.mkdir(parents=True,exist_ok=True)
assert not drop.exists()
drop.write_text(f'[Service]\nExecStart=\nExecStart={args}\nWorkingDirectory={work}\nUnsetEnvironment=PYTHONPATH PYTHONHOME\nKillMode=control-group\n')
identity_script="import json, wfb_ng.fl.client_daemon as c, wfb_ng.fl.server_daemon as s, wfb_ng.fl.radio as r; from pathlib import Path; print(json.dumps({'client':c.__file__,'server':s.__file__,'radio':r.__file__,'gi':r.FIXED_GUARD_INTERVAL,'power25':r.validate_radio_config({'radio_txpower_dbm':25}).to_dict(),'identity':json.loads((Path(r.__file__).parent/'build_identity.json').read_text())}))"
run(['/usr/bin/python3','-I','-c',identity_script],cwd=work)
run(['systemctl','daemon-reload'])
run(['systemctl','start',unit])
run(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','SubState','-p','ExecStart'])
print(json.dumps({'host':host,'commit':commit,'interface':iface,'power_index':25,'gi':'long','source_archive_sha256':archive_sha,'state':'started'}),flush=True)
