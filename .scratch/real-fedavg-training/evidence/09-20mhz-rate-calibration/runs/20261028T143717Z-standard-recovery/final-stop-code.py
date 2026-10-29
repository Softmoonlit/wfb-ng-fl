
import subprocess,sys,json,pathlib,os
unit='wfb-fl-server-daemon.service' if sys.argv[1]=='fl00' else 'wfb-fl-client-daemon.service'
p=subprocess.run(['systemctl','stop',unit],capture_output=True,text=True,timeout=35)
s=subprocess.run(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','SubState'],capture_output=True,text=True,timeout=5)
native=[]
for f in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  if os.readlink(f/'exe')=='/usr/bin/wfb_v6_uplink':native.append(int(f.name))
 except OSError:pass
print(json.dumps({'stop_rc':p.returncode,'err':p.stderr,'status':s.stdout,'native_pids':native}))
