import concurrent.futures, datetime, json, pathlib, subprocess
root=pathlib.Path(__file__).resolve().parent
run_dir=root/('15w-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
run_dir.mkdir()
probe=r'''
import json,pathlib,re,socket,subprocess
result={'host':socket.gethostname()}
def cmd(args):
 p=subprocess.run(args,capture_output=True,text=True,input='',timeout=25)
 return {'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
try:
 config=pathlib.Path('/etc/nvpmodel.conf').resolve()
 result['config']=str(config)
 modes=re.findall(r'<\s*POWER_MODEL\s+ID=(\d+)\s+NAME=([^\s>]+)\s*>',config.read_text())
 matches=[int(i) for i,name in modes if name=='15W']
 assert len(matches)==1, modes
 result['target_mode']=matches[0]
 result['before']=cmd(['sudo','-n','/usr/sbin/nvpmodel','-q'])
 assert result['before']['exit']==0,result['before']
 result['set']=cmd(['sudo','-n','/usr/sbin/nvpmodel','-m',str(matches[0])])
 result['after']=cmd(['sudo','-n','/usr/sbin/nvpmodel','-q'])
 result['verified']=result['set']['exit']==0 and result['after']['exit']==0 and bool(re.search(r'Power Mode:\s*15W\s*\n\s*'+str(matches[0])+r'\s*$',result['after']['stdout']))
except Exception as exc:result['error']=repr(exc);result['verified']=False
print(json.dumps(result))
'''
def apply(host):
 command=['python3','-'] if host=='fl00' else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=7',host,'python3','-']
 try:
  p=subprocess.run(command,input=probe,text=True,capture_output=True,timeout=60)
  (run_dir/(host+'.json')).write_text(p.stdout)
  (run_dir/(host+'.err')).write_text(p.stderr)
  result=json.loads(p.stdout)
 except Exception as exc:result={'host':host,'verified':False,'error':repr(exc)}
 print(json.dumps(result),flush=True)
 return result
hosts=['fl00','fl01','fl02','fl04','fl05','fl06','fl07','fl08','fl09','fl10']
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:results=list(ex.map(apply,hosts))
(run_dir/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print('EVIDENCE',str(run_dir),'VERIFIED',sum(r['verified'] for r in results),'/',len(hosts))
