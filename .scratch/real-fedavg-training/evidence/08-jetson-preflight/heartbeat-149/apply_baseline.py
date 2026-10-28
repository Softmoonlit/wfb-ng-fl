#!/usr/bin/env python3
"""通过管理平面固化已授权的四板149基线，并保留身份与配置证据。"""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

HERE = Path(__file__).resolve().parent
NODES = {'fl00': None, 'fl01': '192.168.108.201', 'fl02': '192.168.108.202', 'fl04': '192.168.108.204'}

def run(node, argv):
    command = argv if node == 'fl00' else ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', 'fl@' + NODES[node], shlex.join(argv)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=35)
    if result.returncode:
        raise RuntimeError((node, argv, result.stdout, result.stderr))
    return result.stdout

def main():
    # 在任何停服动作之前核对实际运行制品。
    probe = """import pathlib,os,json,hashlib
p=pathlib.Path('/usr/local/lib/python3.10/dist-packages/wfb_ng/fl/build_identity.json')
n=[]
for f in pathlib.Path('/proc').iterdir():
 if not f.name.isdigit(): continue
 try:
  if os.readlink(f/'exe')=='/usr/bin/wfb_v6_uplink': n.append({'pid':int(f.name),'sha256':hashlib.sha256((f/'exe').read_bytes()).hexdigest()})
 except (FileNotFoundError,ProcessLookupError,PermissionError): pass
print(json.dumps({'identity':json.loads(p.read_text()),'native':n}))
"""
    identities = {node: json.loads(run(node, ['sudo', '-n', 'python3', '-c', probe])) for node in NODES}
    for value in identities.values():
        assert value['identity']['commit'] == '701963ac254261c31c56a074d06966a9217346f6'
        assert len(value['native']) == 1 and value['native'][0]['sha256'] == 'f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1'
    (HERE / 'baseline-identities.json').write_text(json.dumps(identities, indent=2))
    for node in ['fl01', 'fl02', 'fl04', 'fl00']:
        unit = 'wfb-fl-server-daemon.service' if node == 'fl00' else 'wfb-fl-client-daemon.service'
        run(node, ['sudo', '-n', 'systemctl', 'stop', unit])
    for node in NODES:
        if node == 'fl00':
            code = """import pathlib,json,os
p=pathlib.Path('/etc/wfb-ng-fl/server.json');d=json.loads(p.read_text());d.update(channel=149,bandwidth_mhz=40,radio_txpower_dbm=14,downlink_mcs=3,uplink_mcs=4,uftp_rate_kbps=15000)
t=p.with_name('server.json.radio149.tmp');t.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\\n');t.chmod(p.stat().st_mode);os.replace(t,p)
"""
        else:
            code = """import pathlib,os
p=pathlib.Path('/etc/systemd/system/wfb-fl-client-daemon.service.d/08-mnist.conf');s=p.read_text();assert s.count('--channel 157')==1
s=s.replace('--channel 157','--channel 149');t=p.with_name('08-mnist.conf.radio149.tmp');t.write_text(s);t.chmod(p.stat().st_mode);os.replace(t,p)
"""
        run(node, ['sudo', '-n', 'python3', '-c', code])
        run(node, ['sudo', '-n', 'systemctl', 'daemon-reload'])
    for node in NODES:
        unit = 'wfb-fl-server-daemon.service' if node == 'fl00' else 'wfb-fl-client-daemon.service'
        run(node, ['sudo', '-n', 'systemctl', 'start', unit])
        print(node, '启动完成', flush=True)

if __name__ == '__main__':
    main()
