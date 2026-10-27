import subprocess,pathlib,time,json,urllib.request
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
P=pathlib.Path(__file__).resolve().parent
nodes=[('fl00',None,'server'),('fl01','201','client'),('fl02','202','client'),('fl04','204','client')]
def save(s): (P/'deployment-progress.json').write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n')
def call(node,ip,args,log,timeout=240,input=None):
 print(node,args,flush=True)
 cmd=args if ip is None else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=7','fl@192.168.108.'+ip,*args]
 r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,input=input,timeout=timeout);r.check_returncode()
try:
 with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=5) as f:state=json.load(f)
 assert state['server_state']=='IDLE' and state['active_job'] is None
 assert all(state['nodes'][str(n)]['readiness']=='READY' for n in [1,2,4])
 for node,ip,role in nodes:
  with (P/(node+'-transfer.txt')).open('wb') as log:
   if ip:
    r=subprocess.run(['scp','-o','BatchMode=yes','-o','ConnectTimeout=7','/tmp/wfb08-e53de00.tar','fl@192.168.108.'+ip+':/tmp/wfb08-e53de00.tar'],stdout=log,stderr=subprocess.STDOUT,timeout=120);r.check_returncode()
 for phase in ['backup-build','stop','install-config','start']:
  for node,ip,role in nodes:
   save({'phase':phase,'node':node,'state':'running','utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
   with (P/(node+'-'+phase+'.txt')).open('wb') as log:
    call(node,ip,['sudo','-n','env','PYTHONDONTWRITEBYTECODE=1','python3','-B','-',role,phase],log,input=(P/'deploy-node.py').read_bytes())
 save({'state':'deployment-complete-needs-readiness-verification','utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
except Exception as e:
 save({'state':'failed','error':repr(e),'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())});raise
