import pathlib,subprocess,json,hashlib,time,os,sys
host=os.uname().nodename;role='server' if host=='fl00' else 'client';phase=sys.argv[1];unit='wfb-fl-'+role+'-daemon.service';root=pathlib.Path('/srv/wfb-ng-fl/deploy-69a94d8/native-unification');sha='8ddb419be0c99e174eeb91239092f266398ea248e0c3c71eff643e28bbcc08e0'
def run(args,timeout=40,check=True):
 print('UTC',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'CMD',args,flush=True);r=subprocess.run(args,timeout=timeout);print('RC',r.returncode,flush=True)
 if check:r.check_returncode()
print(host,phase,flush=True)
if phase=='stop':
 root.mkdir(parents=True,exist_ok=False)
 old=pathlib.Path('/usr/bin/wfb_v6_uplink').read_bytes();(root/'native-before').write_bytes(old);print('backup SHA',hashlib.sha256(old).hexdigest(),flush=True)
 run(['systemctl','show',unit,'-p','ExecStart','-p','MainPID','-p','DropInPaths'])
 run(['systemctl','stop',unit],60)
 assert subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True).strip()=='0'
 assert subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','--value'],text=True).strip()=='inactive'
 r=subprocess.run(['pgrep','-a','-x','wfb_v6_uplink|uftp|uftpd|wfb-fl-server|wfb-fl-client'],capture_output=True,text=True);print(r.stdout,flush=True);assert r.returncode==1,'native/uftp remains'
elif phase=='install':
 assert subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True).strip()=='0'
 src=pathlib.Path('/srv/wfb-ng-fl/native-69a94d8-unified/source/wfb_v6_uplink') if host=='fl00' else pathlib.Path('/tmp/wfb08-native69-unified')
 assert hashlib.sha256(src.read_bytes()).hexdigest()==sha
 run(['install','-o','root','-g','root','-m','0755',str(src),'/usr/bin/wfb_v6_uplink'])
 assert hashlib.sha256(pathlib.Path('/usr/bin/wfb_v6_uplink').read_bytes()).hexdigest()==sha
 run(['readelf','-n','/usr/bin/wfb_v6_uplink']);run(['ldd','/usr/bin/wfb_v6_uplink'])
elif phase=='start':
 run(['systemctl','start',unit]);run(['systemctl','is-active',unit]);run(['systemctl','show',unit,'-p','MainPID','-p','ExecStart'])
else:raise ValueError(phase)
print('NATIVE_PHASE_PASS',host,phase,flush=True)
