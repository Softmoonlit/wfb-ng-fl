from pathlib import Path
import subprocess,json,time,concurrent.futures,shlex,hashlib
ROOT=Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/evidence/grant-fec-nine-20261108/after-power-restored/model-fec14-g30-d10/fault-closeout-once')
management=json.loads((ROOT/'fl09-management.json').read_text());deadline=management['started_monotonic']+90
HOSTS=['fl00','fl01','fl02','fl03','fl05','fl06','fl07','fl08','fl10']
if management['ssh']['returncode']==0:HOSTS.insert(-1,'fl09')
SHA='44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2'
COMMIT='1cd6264d5d6be660998ce60f3a349063009ecfa7'
RELEASE='/srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261108T115743Z'
def write(name,data):(ROOT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2))
def remote(host,code,timeout):
 command=['sudo','-n','python3','-c',code];argv=command if host=='fl00' else ['ssh','-T','-oBatchMode=yes','-oConnectTimeout=4',host,shlex.join(command)]
 try:
  r=subprocess.run(argv,capture_output=True,text=True,timeout=min(timeout,max(.1,deadline-time.monotonic())))
  return dict(host=host,returncode=r.returncode,result=json.loads(r.stdout) if r.returncode==0 else r.stdout,stderr=r.stderr)
 except Exception as e:return dict(host=host,error=repr(e))
base='EXPECTED_SHA='+repr(SHA)+'\nCOMMIT='+repr(COMMIT)+'\nRELEASE='+repr(RELEASE)+'\n'
restart=base+'''import pathlib,subprocess,json,socket,hashlib,os,time
host=socket.gethostname();role='server' if host=='fl00' else 'client';unit='wfb-fl-'+role+'-daemon.service';owned='comm-smoke-model-fec14-g30-d10-'+host+'.service'
owned_state=subprocess.run(['systemctl','is-active',owned],capture_output=True,text=True,timeout=3).stdout.strip()
assert owned_state=='inactive',('owned unit not confirmed inactive',owned_state)
release=pathlib.Path(RELEASE);manifest=json.loads((release/'manifest.json').read_text())
assert manifest['commit']==COMMIT and manifest['native_sha']==EXPECTED_SHA
assert hashlib.sha256((release/'source/wfb_v6_uplink').read_bytes()).hexdigest()==EXPECTED_SHA
assert hashlib.sha256(pathlib.Path('/usr/bin/wfb_v6_uplink').read_bytes()).hexdigest()==EXPECTED_SHA
oldpid=subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True,timeout=3).strip()
oldargs=pathlib.Path('/proc',oldpid,'cmdline').read_bytes().decode().rstrip(chr(0)).split(chr(0))
assert oldargs[:5]==['/usr/bin/python3','-I','-B','-m','wfb_ng.fl.'+role+'_daemon']
assert oldargs[oldargs.index('--channel')+1]=='149'
config_before=None;config_after=None
if role=='server':
 path=pathlib.Path('/etc/wfb-ng-fl/server.json');config_before=json.loads(path.read_text())
 assert config_before['channel']==149 and config_before['bandwidth_mhz']==40 and config_before.get('fec_n',14)==14
 config_after=dict(config_before,grant_duration_ms=10,guard_interval_ms=10)
 if config_after!=config_before:
  temp=path.with_name('server.json.fault-recovery-tmp');temp.write_text(json.dumps(config_after,indent=2)+'\\n');os.chmod(temp,path.stat().st_mode);os.chown(temp,path.stat().st_uid,path.stat().st_gid);os.replace(temp,path)
subprocess.run(['systemctl','restart',unit],check=True,timeout=15)
newpid=subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True,timeout=3).strip()
print(json.dumps(dict(host=host,oldpid=oldpid,newpid=newpid,oldargs=oldargs,owned_unit_state=owned_state,config_before=config_before,config_after=config_after,monotonic=time.monotonic())))
'''
# The failed 09 management probe is final for this closeout; no retry or contact to04.
if 'fl09' in HOSTS:
 code=Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/communication/recover.py').read_text()
 command=['ssh','-T','-oBatchMode=yes','-oConnectTimeout=4','fl09',shlex.join(['sudo','-n','python3','-I','-B','-c',code,'model-fec14-g30-d10'])]
 result=subprocess.run(command,capture_output=True,text=True,timeout=min(18,deadline-time.monotonic()));write('fl09-exact-cleanup.json',dict(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr));assert result.returncode==0
server=remote('fl00',restart,20);write('server-standard-restart.json',server)
if server.get('returncode')!=0:
 write('result.json',dict(partial_recovery=True,complete_batch_restore=False,error='server precondition/restart failed',server=server));raise SystemExit(1)
with concurrent.futures.ThreadPoolExecutor(max_workers=len(HOSTS)-1) as pool:clients=list(pool.map(lambda h:remote(h,restart,20),[h for h in HOSTS if h!='fl00']))
write('client-standard-restarts.json',clients)
audit=base+'''import pathlib,subprocess,json,socket,struct,hashlib,time
host=socket.gethostname();role='server' if host=='fl00' else 'client';unit='wfb-fl-'+role+'-daemon.service';owned='comm-smoke-model-fec14-g30-d10-'+host+'.service';items=[]
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  argv=p.read_bytes().decode().rstrip(chr(0)).split(chr(0))
  if argv[0]!='/usr/bin/wfb_v6_uplink':continue
  port=int(argv[argv.index('--radio-control-port')+1]);interface=argv[argv.index('--air-interface')+1];queries={}
  for cmd,fmt,key in [(4,'!II7B','radio'),(3,'!II2B','fec')]:
   with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
    s.settimeout(2);s.sendto(struct.pack('!IB',0x434f4d4d,cmd),('127.0.0.1',port));raw,peer=s.recvfrom(4096);assert peer==('127.0.0.1',port);queries[key]=list(struct.unpack(fmt,raw))
  items.append(dict(pid=p.parent.name,argv=argv,sha256=hashlib.sha256((p.parent/'exe').read_bytes()).hexdigest(),queries=queries,iw=subprocess.check_output(['iw','dev',interface,'info'],text=True,timeout=3)))
 except (OSError,UnicodeError):continue
release=pathlib.Path(RELEASE);manifest=json.loads((release/'manifest.json').read_text());identity=json.loads((pathlib.Path(manifest['installed'])/'build_identity.json').read_text())
power=subprocess.check_output(['nvpmodel','-q'],text=True,timeout=3)
owned_state=subprocess.run(['systemctl','is-active',owned],capture_output=True,text=True,timeout=3).stdout.strip()
service=subprocess.check_output(['systemctl','show',unit,'-p','MainPID','-p','ActiveState'],text=True,timeout=3)
checks=dict(native_count=len(items)==1,service_active='ActiveState=active' in service,owned_inactive=owned_state=='inactive',power=power.strip()=='NV Power Mode: 15W\\n0',manifest=manifest['commit']==identity['commit']==COMMIT and manifest['native_sha']==EXPECTED_SHA,source_sha=hashlib.sha256((release/'source/wfb_v6_uplink').read_bytes()).hexdigest()==EXPECTED_SHA)
if len(items)==1:
 n=items[0];radio=n['queries']['radio'];checks.update(proc_sha=n['sha256']==EXPECTED_SHA,radio=radio[:2]==[0x434f4d4d,0] and radio[4:7]==[0,40,3 if role=='server' else 4],fec=n['queries']['fec']==[0x434f4d4d,0,8,14],iw=all(t in n['iw'] for t in ['channel 149 ','width: 40 MHz','txpower -18.00 dBm','type monitor']))
 if role=='server':checks['schedule']=n['argv'][n['argv'].index('--grant-duration-ms')+1]=='10' and n['argv'][n['argv'].index('--guard-interval-ms')+1]=='10'
print(json.dumps(dict(host=host,ok=all(checks.values()),checks=checks,service=service,owned_unit_state=owned_state,native=items,power=power,boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip())))
'''
with concurrent.futures.ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:audits=list(pool.map(lambda h:remote(h,audit,10),HOSTS))
write('reachable-actual-audit.json',audits)
all_ok=all(r.get('returncode')==0 and r.get('result',{}).get('ok') is True for r in audits)
result=dict(partial_recovery='fl09' not in HOSTS,reachable_recovered=all_ok,complete_batch_restore=all_ok and 'fl09' in HOSTS,hosts=HOSTS,fl09_management=management,fl09_owned_unit='unknown; not contacted again' if 'fl09' not in HOSTS else 'verified inactive',excluded_fl04_not_contacted=True,server_schedule='10/10',target_channel=149,grant5_executed=False,payload_sent=False,FEC_changed=False,elapsed=time.monotonic()-management['started_monotonic'],deadline_seconds=90,audit_checks=[dict(host=r['host'],returncode=r.get('returncode'),checks=r.get('result',{}).get('checks') if isinstance(r.get('result'),dict) else None,error=r.get('error'),stderr=r.get('stderr')) for r in audits])
write('result.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))
