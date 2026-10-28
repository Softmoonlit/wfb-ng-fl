#!/usr/bin/env python3
"""终态之后只读核验四板进程、射频、制品摘要及指定窗口的服务日志。"""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import urllib.request

base = Path(__file__).resolve().parent
output = Path(json.loads((base/'normal-stop-active.json').read_text())['output'])
manifest = json.loads((base/'release-c62cde266ef5-20261028T130351850367Z/manifest.json').read_text())
probe = r'''
import hashlib,json,os,subprocess
from pathlib import Path
processes=[]
for path in Path('/proc').iterdir():
    if not path.name.isdigit():continue
    try:
        raw=(path/'cmdline').read_bytes()
        if not raw:continue
        args=raw.decode(errors='replace').rstrip('\0').split('\0')
        if any('wfb_ng.fl.service' in x or 'wfb_v6_uplink' in x or 'release-node.py' in x for x in args):
            processes.append({'pid':int(path.name),'argv':args,'exe':os.readlink(path/'exe')})
    except (FileNotFoundError,PermissionError,ProcessLookupError):pass
native=[p for p in processes if p['exe']=='/usr/bin/wfb_v6_uplink']
assert len(native)==1,native
native[0]['sha256']=hashlib.sha256(Path('/proc',str(native[0]['pid']),'exe').read_bytes()).hexdigest()
print(json.dumps({'host':os.uname().nodename,'processes':processes,'native':native[0],
                 'iw':subprocess.check_output(['iw','dev'],text=True)}))
'''

def collect(node):
    host, ip, role = node
    prefix = [] if ip is None else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=7','fl@'+ip]
    command = ['sudo','-n','python3','-'] if not prefix else prefix+['sudo -n python3 -']
    result = subprocess.run(command,input=probe,text=True,capture_output=True,timeout=20,check=True)
    facts = json.loads(result.stdout)
    assert facts['host']==host and facts['native']['sha256']==manifest['native_sha256'],facts
    assert not any('wfb_ng.fl.service' in a for p in facts['processes'] for a in p['argv']),facts
    assert 'channel 149 ' in facts['iw'] and 'width: 40 MHz' in facts['iw'] and 'txpower -14.00 dBm' in facts['iw'],facts
    journal=['sudo','-n','journalctl','-u','wfb-fl-'+role+'-daemon.service',
             '--since','@1793193710','--until','@1793193740','--no-pager','-o','short-monotonic']
    if prefix:
        import shlex
        journal=prefix+[shlex.join(journal)]
    result = subprocess.run(journal,text=True,capture_output=True,timeout=20,check=True)
    (output/(host+'-daemon-journal.txt')).write_text(result.stdout)
    return host,facts

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    facts = dict(pool.map(collect,[('fl00',None,'server'),('fl01','192.168.108.201','client'),
                                   ('fl02','192.168.108.202','client'),('fl04','192.168.108.204','client')]))
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:9090/api/v1/status',timeout=5) as response:
    state=json.load(response)
assert state['server_state']=='IDLE' and state['active_job'] is None,state
assert all(state['nodes'][n]['readiness']=='READY' for n in ['1','2','4']),state
(output/'post-stop-runtime.json').write_text(json.dumps({'nodes':facts,'status':state},ensure_ascii=False,indent=2))
print('PASS: 四端无作业计算进程，统一native与149/HT40+/14档一致，Server IDLE、三端READY')
