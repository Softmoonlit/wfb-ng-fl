import urllib.request,json,time,pathlib,datetime
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
p=pathlib.Path(__file__).resolve().parent;job='web-f317d54238864f64ab66164f151d3622';out=[];t0=time.time()
while time.time()-t0<1900:
 try:
  with urllib.request.urlopen('http://192.168.108.100:8080/api/v1/state',timeout=8) as f:j=json.load(f)
  rec={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'state':j};out.append(rec)
  cur=j.get('current_job'); recent=j.get('recent_job');x=cur or recent
  print(rec['utc'],'server',j['server']['state'],'current',cur and cur.get('job_id'),'phase',x and x.get('server_phase'),'round',x and x.get('current_round'),'done',x and x.get('rounds_completed'),'result',x and x.get('execution_result'),'recovery',j.get('recovery_state'),flush=True)
  if recent and recent.get('job_id')==job and recent.get('execution_result') in ('success','failed','aborted','stopped'):break
 except Exception as e: print('poll_error',repr(e),flush=True);out.append({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'error':repr(e)})
 time.sleep(10)
(p/'two-round-retry-status-history.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
