import urllib.request,urllib.error,time,json,datetime,pathlib,subprocess,os
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
p=pathlib.Path(__file__).resolve().parent
job='web-6727da200d4e49b992134ea35989a4a1'; started=time.time(); out=[]
while time.time()-started<1900:
 try:
  with urllib.request.urlopen('http://192.168.108.100:8080/api/v1/state',timeout=8) as f:j=json.load(f)
  rec={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'state':j};out.append(rec)
  s=j.get('server',{});cur=s.get('current_job') or j.get('current_job');print(rec['utc'],'server',s.get('state'),'job',cur and cur.get('job_id'),'round',cur and cur.get('current_round'),'completed',cur and cur.get('rounds_completed'),'recovery',s.get('recovery_state'),flush=True)
  if cur and cur.get('job_id')==job and cur.get('execution_result') in ('success','failed','aborted','stopped'):break
  # once current job disappears, recent_job carries terminal fact
  recent=s.get('recent_job') or j.get('recent_job')
  if recent and recent.get('job_id')==job and recent.get('execution_result') in ('success','failed','aborted','stopped'):break
 except Exception as e: print('poll failure',repr(e),flush=True);out.append({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'poll_error':repr(e)})
 time.sleep(10)
(p/'two-round-status-history.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
