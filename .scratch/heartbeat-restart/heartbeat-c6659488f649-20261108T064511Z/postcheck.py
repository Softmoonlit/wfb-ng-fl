import datetime,json,os,pathlib,socket,struct,subprocess,urllib.request
P=pathlib.Path
host=socket.gethostname()
unit='wfb-fl-'+('server' if host=='fl00' else 'client')+'-daemon.service'
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=8)
 return dict(exit=p.returncode,stdout=p.stdout,stderr=p.stderr)
r=dict(host=host,utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),service=run(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID','-p','ExecStart']),radios=[])
for card in P('/sys/class/net').iterdir():
 if (card/'device/driver').resolve().name=='rtl88xxau_wfb':r['radios'].append(run(['iw','dev',card.name,'info']))
r['journal']=run(['journalctl','-u',unit,'--since','2026-11-08 06:45:00 UTC','--no-pager','-o','short-iso'])
r['channel_cache']=P('/srv/wfb-ng-fl/client/channel_cache.json').read_text() if P('/srv/wfb-ng-fl/client/channel_cache.json').exists() else None
if host=='fl00':
 r['status']=json.load(urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=6))
print(json.dumps(r,ensure_ascii=False))
