"""三阶段短诊断，绝不触碰service；finally仅回收本次记录PID+starttime的子进程。"""
import subprocess,pathlib,json,time,datetime,os,signal,urllib.request
P=pathlib.Path(__file__).resolve().parent;nodes=[('fl00',None),('fl01','201'),('fl02','202'),('fl04','204')];script=(P/'phase-node.py').read_bytes();owned=[];history=[]
def saveprogress(**kw):(P/'progress.json').write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),**kw},ensure_ascii=False,indent=2)+'\n')
def interrupted(sig,frame):raise SystemExit(128+sig)
signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
def getstatus():
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with op.open('http://127.0.0.1:9090/api/v1/status',timeout=5) as f:return json.load(f)
def rows(f):
 result=[]
 for l in f.read_text().splitlines():
  try:result.append(json.loads(l))
  except Exception:pass
 return result
def cleanup_owned(entry):
 proc,log,node,ip,file=entry
 if proc.poll() is None:
  # Read only PIDs emitted by this invocation; validate /proc starttime to avoid reused PIDs.
  rr=rows(file);targets=[]
  for r in rr:
   if r.get('kind')=='owned-ping-start':targets.append((r['pid'],r['start_tick'],2))
  for r in rr:
   if r.get('kind')=='begin':targets.append((r['owner_pid'],r['owner_start_tick'],15))
  code="import os,pathlib,json,signal\nfor pid,tick,sig in "+repr(targets)+":\n try:\n  stat=pathlib.Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split(); assert stat[19]==str(tick); os.kill(pid,sig)\n except (FileNotFoundError,ProcessLookupError):pass\n"
  cmd=['sudo','-n','python3','-B','-'] if not ip else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5','fl@192.168.108.'+ip,'sudo -n python3 -B -']
  with (P/(file.stem+'-owner-cleanup.txt')).open('wb') as f:subprocess.run(cmd,input=code.encode(),stdout=f,stderr=subprocess.STDOUT,timeout=8)
 try:proc.wait(timeout=8)
 except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=3)
 log.close()
try:
 for phase in ['baseline','activity','post']:
  before=getstatus();assert before['server_state']=='IDLE' and before['active_job'] is None
  (P/(phase+'-before.json')).write_text(json.dumps(before,ensure_ascii=False,indent=2)+'\n');saveprogress(phase=phase,state='capturing',owned_processes=[])
  stage=[];started=time.monotonic()
  for node,ip in nodes:
   file=P/(phase+'-'+node+'.jsonl');assert not file.exists(),'拒绝覆盖已有阶段证据'
   log=file.open('wb');cmd=['sudo','-n','env','PYTHONDONTWRITEBYTECODE=1','timeout','-k','5','55','python3','-B','-',phase] if not ip else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5','fl@192.168.108.'+ip,'sudo -n env PYTHONDONTWRITEBYTECODE=1 timeout -k 5 55 python3 -B - '+phase]
   proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,cwd='/tmp',start_new_session=True);entry=(proc,log,node,ip,file);owned.append(entry);stage.append(entry);proc.stdin.write(script);proc.stdin.close()
  saveprogress(phase=phase,state='capturing',owned_processes=[{'node':e[2],'local_pid':e[0].pid} for e in stage])
  while time.monotonic()-started<53:
   if all(e[0].poll() is not None for e in stage):break
   if any(e[0].poll() not in (None,0) for e in stage):raise RuntimeError('阶段子进程失败，先清理诊断进程并停止后续阶段')
   time.sleep(1)
  for entry in stage:
   cleanup_owned(entry);proc,log,node,ip,file=entry;assert proc.returncode==0,(phase,node,proc.returncode)
   rr=rows(file);assert any(r.get('kind')=='end' for r in rr),(phase,node,'missing end');assert any(r.get('kind')=='owner-cleanup-complete' and r.get('ping_reaped') for r in rr),(phase,node,'missing cleanup')
  after=getstatus();(P/(phase+'-after.json')).write_text(json.dumps(after,ensure_ascii=False,indent=2)+'\n');history.append({'phase':phase,'start_parent_monotonic':started,'end_parent_monotonic':time.monotonic(),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()});(P/'phases.json').write_text(json.dumps(history,indent=2)+'\n')
 saveprogress(state='complete',phases=history)
except BaseException as e:
 saveprogress(state='failed',error=repr(e),completed=history);raise
finally:
 for entry in owned:cleanup_owned(entry)
 (P/'finally-status.json').write_text(json.dumps(getstatus(),ensure_ascii=False,indent=2)+'\n')
