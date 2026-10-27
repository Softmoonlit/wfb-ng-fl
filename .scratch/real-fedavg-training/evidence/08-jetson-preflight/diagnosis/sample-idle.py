import urllib.request,json,time,datetime,pathlib
op=urllib.request.build_opener(urllib.request.ProxyHandler({})); out=[]
for i in range(61):
 try:
  with op.open('http://127.0.0.1:9090/api/v1/status',timeout=4) as f:s=json.load(f)
  out.append({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'server_state':s['server_state'],'active_job':s['active_job'],'link_process':s['link_process'],'nodes':{k:v for k,v in s['nodes'].items() if k in ['1','2','4']}})
 except Exception as e:out.append({'error':repr(e)})
 time.sleep(1)
pathlib.Path(__file__).with_name('idle-heartbeat-61s.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
