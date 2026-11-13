#!/usr/bin/env python3
import concurrent.futures
import hashlib
import json
import pathlib
import re
import socket
import struct
import subprocess
import shlex
import time

ROOT=pathlib.Path(__file__).resolve().parent
HOSTS=['fl00','fl01','fl02','fl03','fl05','fl06','fl07','fl08','fl09','fl10']
NODES=[1,2,3,5,6,7,8,9,10]
RELEASE=pathlib.Path('/srv/wfb-ng-fl/releases/heartbeat-cccac233291c-20261109T183910Z')

def sha(path):
 h=hashlib.sha256();
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def run(argv, timeout=8):
 try:
  p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
  return dict(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
 except Exception as e:return dict(error=repr(e))

def server_native():
 rows=[]
 for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
  try:
   a=p.read_bytes().rstrip(b'\0').decode(errors='replace').split('\0')
   if a and a[0]=='/usr/bin/wfb_v6_uplink':
    rows.append(dict(pid=int(p.parent.name),args=a,exe_sha256=sha(p.parent/'exe')))
  except Exception:pass
 return rows

def get_control(native):
 if len(native)!=1:return {}
 a=native[0]['args'];port=int(a[a.index('--radio-control-port')+1]);out={}
 for cmd,fmt,key in [(4,'!II7B','radio'),(3,'!II2B','fec')]:
  try:
   with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
    s.settimeout(2);s.sendto(struct.pack('!IB',0x434f4d4d,cmd),('127.0.0.1',port));out[key]=list(struct.unpack(fmt,s.recvfrom(4096)[0]))
  except Exception as e:out[key]='ERROR:'+repr(e)
 return out

def local_server():
 native=server_native(); pid=native[0]['pid'] if len(native)==1 else None
 source=RELEASE/'source/wfb_v6_uplink'; installed=pathlib.Path('/usr/bin/wfb_v6_uplink')
 server_config=pathlib.Path('/etc/wfb-ng-fl/server.json')
 key_files={}
 for p in [server_config,pathlib.Path('/etc/systemd/system/wfb-fl-server-daemon.service'),pathlib.Path('/lib/systemd/system/wfb-fl-server-daemon.service')]:
  if p.exists():key_files[str(p)]=dict(size=p.stat().st_size,sha256=sha(p),content=p.read_text(errors='replace')[:20000])
 logs={
  'daemon':run(['journalctl','-u','wfb-fl-server-daemon.service','--no-pager','-o','short-iso','-n','100']),
  'kernel':run(['journalctl','-k','--no-pager','-o','short-iso','-n','100']),
  'all_errors':run(['journalctl','-u','wfb-fl-server-daemon.service','-k','--no-pager','-o','cat','-n','300'])
 }
 text='\n'.join(v.get('stdout','') for v in logs.values())
 return dict(host='fl00',native=native,control=get_control(native),native_source_sha256=sha(source) if source.exists() else None,installed_native_sha256=sha(installed) if installed.exists() else None,server_service=run(['systemctl','show','wfb-fl-server-daemon.service','-p','MainPID','-p','ActiveState','-p','SubState','-p','ExecMainStartTimestamp']),iw=run(['iw','dev','wlxfca386b38672','info']),ip_addr=run(['ip','-j','addr','show','dev','fl-s']),routes=run(['ip','-j','route','show','dev','fl-s']),sockets=dict(tcp=run(['ss','-lntup']),udp=run(['ss','-lunp'])),interface_stats=run(['ip','-s','link','show','dev','wlxfca386b38672']),daemon_traceback='Traceback' in text,logs=logs,key_files=key_files,config_observation=json.loads(server_config.read_text()) if server_config.exists() else None)

def remote_probe(host):
 code=r'''import hashlib,json,pathlib,socket,struct,subprocess

def run(a):
 try:
  p=subprocess.run(a,capture_output=True,text=True,timeout=8);return dict(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
 except Exception as e:return dict(error=repr(e))
def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
native=[]
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  a=p.read_bytes().rstrip(b'\\0').decode(errors='replace').split('\\0')
  if a and a[0]=='/usr/bin/wfb_v6_uplink':native.append(dict(pid=int(p.parent.name),args=a,exe_sha256=sha(p.parent/'exe')))
 except Exception:pass
q={}
if len(native)==1:
 a=native[0]['args'];port=int(a[a.index('--radio-control-port')+1])
 for cmd,fmt,key in [(4,'!II7B','radio'),(3,'!II2B','fec')]:
  try:
   with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
    s.settimeout(2);s.sendto(struct.pack('!IB',0x434f4d4d,cmd),('127.0.0.1',port));q[key]=list(struct.unpack(fmt,s.recvfrom(4096)[0]))
  except Exception as e:q[key]='ERROR:'+repr(e)
iface=native[0]['args'][native[0]['args'].index('--air-interface')+1] if native else None
print(json.dumps(dict(host=socket.gethostname(),boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),service=run(['systemctl','show','wfb-fl-client-daemon.service','-p','MainPID','-p','ActiveState','-p','SubState']),native=native,control=q,iw=run(['iw','dev',iface,'info']) if iface else None,link_stats=run(['ip','-s','link','show','dev',iface]) if iface else None,journal=run(['journalctl','-u','wfb-fl-client-daemon.service','--no-pager','-o','short-iso','-n','100']),kernel=run(['journalctl','-k','--no-pager','-o','short-iso','-n','100'])),ensure_ascii=False))
'''
 cmd=['ssh','-T','-oBatchMode=yes','-oConnectTimeout=5',host,shlex.join(['sudo','-n','python3','-c',code])]
 try:
  p=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
  return dict(host=host,returncode=p.returncode,result=json.loads(p.stdout) if p.returncode==0 else None,stderr=p.stderr)
 except Exception as e:return dict(host=host,error=repr(e))

server=local_server()
with concurrent.futures.ThreadPoolExecutor(max_workers=9) as pool:clients=list(pool.map(remote_probe,HOSTS[1:]))
# Historical normalized server startup from the prior successful 40/5 model.
hist=ROOT.parent/'continuation-cache-reclaimed-20261109T223355Z/model-40-d5/fec12-g40-d5-model-20261110/runner.log'
historical=[]
if hist.exists():
 for line in hist.read_text().splitlines():
  try:
   v=json.loads(line)
   if v.get('phase')=='prepare' and v.get('host')=='fl00' and v.get('ok'):
    historical.append(v['result'].get('release_identity',{}).get('actualargs'));break
  except Exception:pass
out=dict(status='read-only-common-offline-diagnostic',wall=time.time(),server=server,clients=clients,historical_success_server_actualargs=historical,comparison_note='Current server native args/source/native hashes and client native args compared with prior successful 40/5 startup; no secrets collected.')
(ROOT/'common-offline-diagnostic.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(server_pid=server['native'][0]['pid'] if len(server['native'])==1 else None,server_control=server['control'],client_returncodes={x['host']:x.get('returncode') for x in clients}),ensure_ascii=False))
