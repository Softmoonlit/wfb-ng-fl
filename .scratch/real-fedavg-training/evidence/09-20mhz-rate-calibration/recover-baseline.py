#!/usr/bin/env python3
"""测量恢复被全员待命门禁拒绝后，保留现场并标准重启四端服务；不改配置或注册表。"""
import concurrent.futures as cf
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import shlex
import time

B=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('measurement',B/'run_measurements.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
OUT=B/'runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-standard-recovery')
OUT.mkdir(exist_ok=False)
(B/'current-recovery.txt').write_text(str(OUT)+'\n')
m.save(OUT/'before-status.json',m.api('status'))
m.save(OUT/'before.json',m.allhosts(m.SNAP))
JOURNAL=r'''
import subprocess,json,sys
unit='wfb-fl-server-daemon.service' if sys.argv[1]=='fl00' else 'wfb-fl-client-daemon.service'
r=subprocess.run(['journalctl','-u',unit,'--since','2026-10-28 14:24:00 UTC','--output=json','--no-pager'],capture_output=True,text=True,timeout=15)
print(json.dumps({'rc':r.returncode,'out':r.stdout,'err':r.stderr}))
'''
with cf.ThreadPoolExecutor(4) as ex:
 fut={ex.submit(m.remote,h,JOURNAL,[h]):h for h in m.HOSTS}
 for f in cf.as_completed(fut):m.save(OUT/(fut[f]+'-journal.json'),json.loads(f.result()))

def action(host,verb):
 unit='wfb-fl-server-daemon.service' if host=='fl00' else 'wfb-fl-client-daemon.service'
 argv=['sudo','-n','systemctl',verb,unit]
 if host!='fl00':argv=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',host,shlex.join(argv)]
 p=subprocess.run(argv,capture_output=True,text=True,timeout=45)
 return host,{'rc':p.returncode,'out':p.stdout,'err':p.stderr,'argv':argv}

for verb in ['stop','start']:
 if verb=='start':
  stopped=m.allhosts(m.SNAP);m.save(OUT/'stopped.json',stopped)
  assert all(not v['links'] for v in stopped.values()),'native尚未退出，不启动下一阶段'
 with cf.ThreadPoolExecutor(4) as ex:result=dict(ex.map(lambda h:action(h,verb),m.HOSTS))
 m.save(OUT/(verb+'.json'),result)
 assert all(v['rc']==0 for v in result.values()),result
 print(verb+' all completed',flush=True)

history=[];consecutive=0
for _ in range(60):
 ok,r=m.ready();history.append(r);m.save(OUT/'ready-history.json',history)
 consecutive=consecutive+1 if ok else 0
 if consecutive>=3:break
 time.sleep(1)
assert consecutive>=3,'标准重启后未全员恢复READY'
final=m.allhosts(m.SNAP);m.save(OUT/'after.json',final)
for h,v in final.items():
 assert len(v['links'])==1,h
 text=v['iw']['out']
 assert 'channel 149 ' in text and 'width: 40 MHz' in text and 'txpower -14.00 dBm' in text,(h,text)
 radio=v['links'][0]['runtime_radio']
 # 重启时尚未在线更新的native，可由startup v6_config及cmdline核验配置。
 assert '--radio-bandwidth 40' in v['links'][0]['args'],(h,v['links'][0]['args'])
status=m.api('status');m.save(OUT/'after-status.json',status)
assert all(status['body']['radio'].get(k)==v for k,v in m.FROZEN_NORMAL.items()),status
m.save(OUT/'summary.json',{'success':True,'method':'现场保存后四端标准systemctl stop/start；未修改配置、注册表或制品',
    'complete_utc':datetime.now(timezone.utc).isoformat()})
print('recovery verified',OUT,flush=True)
