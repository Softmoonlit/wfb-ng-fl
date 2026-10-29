
import subprocess,json,sys
unit='wfb-fl-server-daemon.service' if sys.argv[1]=='fl00' else 'wfb-fl-client-daemon.service'
p=subprocess.run(['systemctl','start',unit],capture_output=True,text=True,timeout=35)
assert p.returncode==0,p.stderr
print(json.dumps({'rc':p.returncode,'unit':unit,'out':p.stdout,'err':p.stderr}))
