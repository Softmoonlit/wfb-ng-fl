#!/usr/bin/env python3
"""重新只读核验四端实际安装/运行制品和射频，并观察120秒常驻活性。"""
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time
import urllib.request

BASE = Path(__file__).resolve().parent
OUT = BASE/'runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-preflight')
OUT.mkdir(parents=True, exist_ok=False)
(BASE/'current-preflight.txt').write_text(str(OUT)+'\n')
COMMIT = 'c62cde266ef509d605c8c923b0f4fa35e42fc4dd'
CODE = r'''
import hashlib,json,os,pathlib,subprocess
os.chdir('/tmp')
import wfb_ng
root=pathlib.Path(wfb_ng.__file__).parent
identity=json.loads((root/'fl/build_identity.json').read_text())
def cmd(a):
 p=subprocess.run(a,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=15)
 return {'rc':p.returncode,'out':p.stdout}
procs=[]
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  args=(p/'cmdline').read_bytes().decode().rstrip(chr(0)).split(chr(0))
  if not args:continue
  if args[0]=='/usr/bin/wfb_v6_uplink' or ('daemon' in args and any(x.endswith('/release-node.py') for x in args)):
   exe=p/'exe'
   procs.append({'pid':int(p.name),'args':args,'ticks':(p/'stat').read_text().split()[21],
     'exe':os.readlink(exe),'sha256':hashlib.sha256(exe.read_bytes()).hexdigest()})
 except (OSError,UnicodeError):pass
native=[p for p in procs if p['exe']=='/usr/bin/wfb_v6_uplink']
daemon=[p for p in procs if p not in native]
assert len(native)==len(daemon)==1,procs
args=daemon[0]['args'];idx=args.index('daemon')
release=pathlib.Path(args[idx+3]);host=args[idx+2]
loaded_file=release/'python-loaded.json'
# 运行wrapper由其持久manifest绑定已加载模块，不以本次import替代daemon身份。
loaded=json.loads(loaded_file.read_text())
assert loaded['pid']==daemon[0]['pid'] and str(loaded['tick'])==daemon[0]['ticks'],loaded
assert loaded['expectedCommit']==args[idx+1] and loaded['host']==host,loaded
for entry in loaded['pythonLoaded']['files'].values():
 assert hashlib.sha256(pathlib.Path(entry['path']).read_bytes()).hexdigest()==entry['sha256'],entry
print(json.dumps({'host':os.uname().nodename,'identity':identity,'python_path':str(root),
 'processes':procs,'loaded_path':str(loaded_file),'loaded':loaded,
 'iw':cmd(['iw','dev']),'usb':cmd(['lsusb','-t']),
 'interfaces':cmd(['ip','-j','address']),'sockets':cmd(['ss','-lunp']),
 'driver':cmd(['sh','-c','for p in /sys/class/net/*/device/driver/module/version; do echo "$p"; cat "$p"; done']),
 'uftp':cmd(['uftp','-h']),'uftpd':cmd(['uftpd','-h']),
 'load':pathlib.Path('/proc/loadavg').read_text(),
 'ps':cmd(['ps','-eo','pid,pcpu,pmem,args','--sort=-pcpu']),
 'disk':cmd(['df','-B1','/srv/wfb-ng-fl'])}))
'''

def collect(host):
    cmd=['sudo','-n','python3','-B','-']
    if host!='fl00':cmd=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',host,shlex.join(cmd)]
    p=subprocess.run(cmd,input=CODE,text=True,capture_output=True,timeout=35)
    (OUT/(host+'-stderr.txt')).write_text(p.stderr)
    if p.returncode:raise RuntimeError(host+': '+p.stderr)
    value=json.loads(p.stdout)
    (OUT/(host+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    return host,value

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    nodes=dict(pool.map(collect,['fl00','fl01','fl02','fl04']))
sha=set()
for host,node in nodes.items():
    assert node['identity']['commit']==COMMIT,(host,node['identity'])
    native=next(p for p in node['processes'] if p['exe']=='/usr/bin/wfb_v6_uplink')
    sha.add(native['sha256'])
    daemon=next(p for p in node['processes'] if p is not native)
    assert daemon['args'][daemon['args'].index('daemon')+1]==COMMIT,host
    iw=node['iw']['out']
    assert 'channel 149 ' in iw and 'width: 40 MHz' in iw and 'txpower -14.00 dBm' in iw,(host,iw)
    assert ':1144 ' not in node['sockets']['out'],host
assert len(sha)==1,sha
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:9090/api/v1/status',timeout=10) as response:
    state=json.load(response)
(OUT/'before-status.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
start=time.monotonic()
samples=[]
with (OUT/'stable-ready.jsonl').open('x') as f:
    while True:
        with opener.open('http://127.0.0.1:9090/api/v1/status',timeout=10) as response:
            state=json.load(response)
        elapsed=time.monotonic()-start
        online=sorted(int(k) for k,v in state['nodes'].items() if v['readiness']!='OFFLINE')
        ok=(state['server_state']=='IDLE' and state['active_job'] is None and online==[1,2,4]
             and all(state['nodes'][str(n)]['readiness']=='READY' and state['nodes'][str(n)]['reported_state']=='IDLE' for n in [1,2,4]))
        row={'elapsed_seconds':elapsed,'ok':ok,'status':state}
        f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();samples.append(row)
        if elapsed>=120:break
        time.sleep(1)
summary={'commit':COMMIT,'native_sha256':sha.pop(),'seconds':samples[-1]['elapsed_seconds'],
 'samples':len(samples),'all_ready':all(r['ok'] for r in samples),
 'positions':'未调整设备、天线与距离；未测量位置坐标及现场频谱，结论仅限当前固定现场',
 'max_age':{str(n):max(r['status']['nodes'][str(n)]['last_heartbeat_ago_seconds'] for r in samples) for n in [1,2,4]}}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False),flush=True)
assert summary['all_ready'],summary
