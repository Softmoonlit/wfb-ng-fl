#!/usr/bin/env python3
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
HOSTS = ['fl00', 'fl01', 'fl02', 'fl03', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09']
NODES = [1, 2, 3, 5, 6, 7, 8, 9]
RELEASE = '/srv/wfb-ng-fl/releases/heartbeat-66f15358d309-20261110T114225Z'
COMMIT = '66f15358d309d646d7dca887624a66c46099a54a'
NATIVE = '44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2'
HARDWARE = json.loads((ROOT / 'hardware-from-actual.json').read_text())['nodes']
EXPECTED_SERVER = dict(interface='wlxfc221c100119', mac='fc:22:1c:10:01:19',
                       driver='rtl88xxau_wfb', usb_path='2-1.1:1.0', usb_speed='5000')

REMOTE = r'''import hashlib,json,pathlib,socket,struct,subprocess,os,importlib
RELEASE='/srv/wfb-ng-fl/releases/heartbeat-66f15358d309-20261110T114225Z'
host=socket.gethostname(); role='server' if host=='fl00' else 'client'
def run(argv):
 try:
  p=subprocess.run(argv,capture_output=True,text=True,timeout=15)
  return {'argv':argv,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
 except Exception as e:return {'argv':argv,'error':repr(e)}
def sha(p):
 h=hashlib.sha256()
 with pathlib.Path(p).open('rb') as s:
  for b in iter(lambda:s.read(1048576),b''):h.update(b)
 return h.hexdigest()
unit='wfb-fl-'+role+'-daemon.service'; svc=run(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID'])
mainpid=int(next((x.split('=',1)[1] for x in svc['stdout'].splitlines() if x.startswith('MainPID=')),'0'))
daemon_argv=pathlib.Path('/proc',str(mainpid),'cmdline').read_bytes().decode().rstrip(chr(0)).split(chr(0)) if mainpid else []
native=[]
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  a=p.read_bytes().decode().rstrip(chr(0)).split(chr(0))
  if not a or a[0]!='/usr/bin/wfb_v6_uplink':continue
  iface=a[a.index('--air-interface')+1]; port=int(a[a.index('--radio-control-port')+1]); q={}
  for code,fmt,key in [(4,'!II7B','radio'),(3,'!II2B','fec')]:
   with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
    s.settimeout(3);s.sendto(struct.pack('!IB',0x434f4d4d,code),('127.0.0.1',port));raw,peer=s.recvfrom(4096);q[key]=list(struct.unpack(fmt,raw))
  dev=pathlib.Path('/sys/class/net')/iface;usbdev=(dev/'device').resolve();speed=next((p/'speed' for p in [usbdev,*usbdev.parents] if (p/'speed').exists()),None)
  native.append(dict(pid=p.parent.name,argv=a,proc_exe=str((p.parent/'exe').resolve()),proc_sha=sha(p.parent/'exe'),interface=iface,mac=(dev/'address').read_text().strip(),driver=(dev/'device/driver').resolve().name,usb_path=usbdev.name,usb_speed=speed.read_text().strip() if speed else None,queries=q,iw=run(['iw','dev',iface,'info'])))
 except Exception as e:native.append({'probe_error':repr(e)})
manifest=json.loads((pathlib.Path(RELEASE)/'manifest.json').read_text()); installed=pathlib.Path(manifest['installed']); source=pathlib.Path(RELEASE)/'source/wfb_ng/fl'; source_files={}
for name,expected in manifest['files'].items():
 source_files[name]=dict(expected=expected,installed=sha(installed/name),source=(sha(source/name) if name!='build_identity.json' else expected))
identity=json.loads((installed/'build_identity.json').read_text())
limits={'/sys/devices/system/cpu/online':'0-5','/sys/devices/platform/17000000.gpu/devfreq_dev/max_freq':'612000000','/sys/kernel/nvpmodel_clk_cap/emc':'2133000000','/sys/kernel/debug/bpmp/debug/clk/emc/mrq_rate_locked':'0'}
for i in range(6): limits['/sys/devices/system/cpu/cpu'+str(i)+'/online']='1';limits['/sys/devices/system/cpu/cpu'+str(i)+'/cpufreq/scaling_max_freq']='1497600'
actual_limits={};
for p in limits:
 try:actual_limits[p]=pathlib.Path(p).read_text().strip()
 except Exception as e:actual_limits[p]='ERROR:'+repr(e)
comm_units=run(['systemctl','list-units','--all','--no-legend','--plain','comm-smoke-*'])
routes={g:run(['ip','-j','route','show','exact',g]) for g in ['239.80.41.1/32','239.80.42.1/32']}
st=os.statvfs('/srv/wfb-ng-fl')
print(json.dumps(dict(host=host,service=svc,daemon_pid=mainpid,daemon_argv=daemon_argv,native=native,manifest=manifest,identity=identity,source_files=source_files,source_native_sha=sha(pathlib.Path(RELEASE)/'source/wfb_v6_uplink'),installed_native_sha=sha('/usr/bin/wfb_v6_uplink'),power_query=run(['nvpmodel','-q']),power_service=run(['systemctl','show','nvpmodel.service','-p','Result','-p','ExecMainStatus','-p','ActiveState']),limits=actual_limits,expected_limits=limits,comm_units=comm_units,routes=routes,storage={'available_bytes':st.f_bavail*st.f_frsize,'available_inodes':st.f_favail},kernel=run(['journalctl','-k','--since','-5 minutes','--no-pager','-o','short-iso'])),ensure_ascii=False))
'''

def probe(host):
 command=['sudo','-n','/usr/bin/python3','-I','-c',REMOTE]
 argv=command if host=='fl00' else ['ssh','-T','-oBatchMode=yes','-oConnectTimeout=5',host,shlex.join(command)]
 try:
  p=subprocess.run(argv,capture_output=True,text=True,timeout=35)
  return dict(host=host,command=argv,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr,parsed=(json.loads(p.stdout) if p.returncode==0 else None))
 except Exception as e:return dict(host=host,command=argv,error=repr(e))

with concurrent.futures.ThreadPoolExecutor(max_workers=len(HOSTS)) as pool: rows=list(pool.map(probe,HOSTS))
try:
 with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=5) as r: status=json.load(r)
 status_error=None
except Exception as e: status=None;status_error=repr(e)
errors=[]
def expected(host):
 if host=='fl00':return EXPECTED_SERVER
 n=HARDWARE[host];return dict(interface=n['interface'],mac=n['mac'],driver=n['driver'],usb_path=n['usb_path'],usb_speed=str(n['usb_speed']).removesuffix('M'))
for row in rows:
 if row.get('returncode')!=0 or row.get('parsed') is None:errors.append(row['host']+': raw probe failed');continue
 d=row['parsed']; exp=expected(row['host']); ns=d.get('native',[])
 if len(ns)!=1:errors.append(row['host']+': native count');continue
 n=ns[0]
 for k in ('interface','mac','driver','usb_path','usb_speed'):
  if n.get(k)!=exp[k]:errors.append('%s:%s expected=%r actual=%r'%(row['host'],k,exp[k],n.get(k)))
 if n.get('proc_sha')!=NATIVE:errors.append(row['host']+': proc native sha')
 if d.get('manifest',{}).get('commit')!=COMMIT or d.get('manifest',{}).get('native_sha')!=NATIVE:errors.append(row['host']+': manifest identity')
 if d.get('identity',{}).get('commit')!=COMMIT:errors.append(row['host']+': installed identity')
 if d.get('source_native_sha')!=NATIVE or d.get('installed_native_sha')!=NATIVE:errors.append(row['host']+': source/installed native')
 if len(d.get('source_files',{}))!=40 or any(x['expected']!=x['installed'] or x['expected']!=x['source'] for x in d.get('source_files',{}).values()):errors.append(row['host']+': source file sha')
 if d.get('power_query',{}).get('stdout','').strip()!='NV Power Mode: 15W\n0':errors.append(row['host']+': power query')
 if 'Result=success' not in d.get('power_service',{}).get('stdout','') or 'ExecMainStatus=0' not in d.get('power_service',{}).get('stdout',''):errors.append(row['host']+': power service')
 if d.get('limits')!=d.get('expected_limits'):errors.append(row['host']+': power limits')
 if any((json.loads(d.get('routes',{}).get(g,{}).get('stdout','')) if d.get('routes',{}).get(g,{}).get('stdout','').strip() else []) for g in d.get('routes',{})):errors.append(row['host']+': route residue')
 if any('active' in line and 'comm-smoke-' in line for line in d.get('comm_units',{}).get('stdout','').splitlines()):errors.append(row['host']+': active old unit')
 if n.get('queries',{}).get('fec') != [0x434f4d4d,0,8,12]:errors.append(row['host']+': GET_FEC')
 expected_mcs=3 if row['host']=='fl00' else 4
 if n.get('queries',{}).get('radio') != [0x434f4d4d,0,0,0,0,40,expected_mcs,0,1]:errors.append(row['host']+': GET_RADIO')
 iw=n.get('iw',{}).get('stdout','')
 if not all(x in iw for x in ['channel 149 ','width: 40 MHz','type monitor','txpower -18.00 dBm']):errors.append(row['host']+': iw')
 if 'ActiveState=active' not in d.get('service',{}).get('stdout',''):errors.append(row['host']+': service')
registry_ok=bool(status and status.get('server_state')=='IDLE' and status.get('active_job') is None and sorted(int(k) for k,v in status.get('nodes',{}).items() if v.get('readiness')!='OFFLINE')==NODES and all(status.get('nodes',{}).get(str(n),{}).get('readiness')=='READY' and status.get('nodes',{}).get(str(n),{}).get('reported_state')=='IDLE' and status.get('nodes',{}).get(str(n),{}).get('current_channel')==149 for n in NODES))
if not registry_ok:errors.append('server registry final gate')
result=dict(audit_type='independent_final_recovery_audit',batch_id='second-eight-model-20261110T150044Z',wall=time.time(),prior_runner_restore_ok=False,independent_audit_ok=not errors,errors=errors,registry=status,registry_error=status_error,rows=rows)
(ROOT/'final-recovery-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'independent_audit_ok':not errors,'errors':errors,'registry_ok':registry_ok}))
raise SystemExit(0 if not errors else 2)
