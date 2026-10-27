import pathlib,subprocess,urllib.request,json,datetime
p=pathlib.Path(__file__).resolve().parent;script=(p/'install-node.py').read_bytes();op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with op.open('http://127.0.0.1:9090/api/v1/status',timeout=5) as f:j=json.load(f)
(p/'before-install-status.json').write_text(json.dumps(j,ensure_ascii=False,indent=2)+'\n');assert j['server_state']=='IDLE' and j['active_job'] is None
for phase in ['stop','install','start']:
 for n,ip in [('fl00',None),('fl01','201'),('fl02','202'),('fl04','204')]:
  (p/'progress.json').write_text(json.dumps({'phase':phase,'node':n,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})+'\n')
  cmd=['sudo','-n','python3','-B','-',phase] if not ip else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=7','fl@192.168.108.'+ip,'sudo -n python3 -B - '+phase]
  with (p/(n+'-'+phase+'.txt')).open('wb') as log:
   r=subprocess.run(cmd,input=script,stdout=log,stderr=subprocess.STDOUT,cwd='/tmp',timeout=75);log.write(('\nExit '+str(r.returncode)+'\n').encode());r.check_returncode()
(p/'progress.json').write_text(json.dumps({'state':'installed-and-started-needs-verification','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})+'\n')
