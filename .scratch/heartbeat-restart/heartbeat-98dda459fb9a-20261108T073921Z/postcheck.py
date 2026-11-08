import datetime,hashlib,json,pathlib,socket,subprocess,time,urllib.request
P=pathlib.Path
host=socket.gethostname()
unit='wfb-fl-'+('server' if host=='fl00' else 'client')+'-daemon.service'
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=8)
 return dict(exit=p.returncode,stdout=p.stdout,stderr=p.stderr)
result={'host':host,'samples':[]}
for i in range(3):
 if i: time.sleep(15)
 sample={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'service':run(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID','-p','ExecStart','-p','Environment']), 'radios':[]}
 for card in P('/sys/class/net').iterdir():
  if (card/'device/driver').resolve().name=='rtl88xxau_wfb':
   sample['radios'].append({'interface':card.name,**run(['iw','dev',card.name,'info'])})
 if host=='fl00':
  try: sample['status']=json.load(urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=6))
  except Exception as e: sample['status_error']=repr(e)
 result['samples'].append(sample)
result['power']=run(['/usr/sbin/nvpmodel','-q'])
result['native_sha256']=hashlib.sha256(P('/usr/bin/wfb_v6_uplink').read_bytes()).hexdigest()
result['journal']=run(['journalctl','-u',unit,'--since','2026-11-08 07:39:21 UTC','-n','250','--no-pager','-o','short-iso'])
print(json.dumps(result,ensure_ascii=False))
