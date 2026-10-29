
import subprocess,json,sys,time
unit='wfb-fl-server-daemon.service' if sys.argv[1]=='fl00' else 'wfb-fl-client-daemon.service'
p=subprocess.run(['journalctl','-u',unit,'--since','2026-10-28 14:24:00 UTC','--until','2026-10-28 14:29:30 UTC','--output=json','--no-pager'],capture_output=True,text=True,timeout=15)
print(json.dumps({'unit':unit,'rc':p.returncode,'out':p.stdout,'stderr':p.stderr,'collected_mono':time.monotonic()}))
