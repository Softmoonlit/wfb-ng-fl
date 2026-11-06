import json
import os
import pathlib
import socket
import subprocess
import urllib.request

host = socket.gethostname()
assert host in ['fl00','fl01','fl02','fl04','fl05','fl06','fl07','fl08','fl09','fl10']
assert os.geteuid() == 0
backup = pathlib.Path('/srv/wfb-ng-fl/phase1-backup-20261106')
backup.mkdir(parents=True,exist_ok=True)

def run(args):
    p = subprocess.run(args,capture_output=True,text=True,timeout=40)
    print(json.dumps({'command':args,'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}),flush=True)
    p.check_returncode()
    return p.stdout

if host == 'fl00':
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:9090/api/v1/status',timeout=5) as response:
        state = json.load(response)
    assert state['active_job'] is None and state['server_state'] == 'IDLE', 'active job prevents maintenance'

interfaces=[]
for p in pathlib.Path('/sys/class/net').iterdir():
    driver=p/'device/driver'
    if driver.exists() and os.path.basename(os.path.realpath(driver))=='rtl88xxau_wfb':
        interfaces.append(p.name)
assert len(interfaces)==1, interfaces
nm = pathlib.Path('/etc/NetworkManager/conf.d/wfb-unmanaged.conf')
if nm.exists() and not (backup/'wfb-unmanaged.conf').exists():
    (backup/'wfb-unmanaged.conf').write_bytes(nm.read_bytes())
nm.parent.mkdir(parents=True,exist_ok=True)
nm.write_text('[keyfile]\nunmanaged-devices=driver:rtl88xxau_wfb\n')
run(['nmcli','general','reload','conf'])
run(['nmcli','device','set',interfaces[0],'managed','no'])
if host=='fl00':
    path=pathlib.Path('/etc/wfb-ng-fl/server.json')
    if not (backup/'server.json').exists():
        (backup/'server.json').write_bytes(path.read_bytes())
    config=json.loads(path.read_text())
    config['web_host']='192.168.0.190'
    config['known_clients']=[1,2,4,5,6,7,8,9,10]
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
unit='wfb-fl-server-daemon' if host=='fl00' else 'wfb-fl-client-daemon'
run(['systemctl','restart',unit])
run(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','SubState'])
run(['nmcli','-t','-f','DEVICE,TYPE,STATE','device'])
run(['iw','dev'])
