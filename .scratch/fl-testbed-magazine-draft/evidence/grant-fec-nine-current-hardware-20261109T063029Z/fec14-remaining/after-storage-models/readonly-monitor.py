import pathlib,json,time,importlib.util,concurrent.futures
r=pathlib.Path(__file__).parent
a=json.loads((r/'checkpoint.json').read_text());c=a.get('current') or {}
print(a['status'],a['stage'],c.get('grant_duration_ms'),c.get('runner_last_phase'))
print('completed',c.get('completed_hosts'))
print('upload',[(x['node'],round(x['bytes']/1048576,1),x['committed']) for x in c.get('client_upload_progress',{}).get('result',{}).get('nodes',[])])
if a['stage']=='finished':
 for n in ['final-closeout/result.json','final-closeout/error.json','STOP']:
  p=r/n
  if p.exists():print(n,p.read_text()[-2000:])
else:
 s=importlib.util.spec_from_file_location('readonly_health',r/'driver-base.py');d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
 p=pathlib.Path(c['rawpath']) if c else r
 progress=list(p.glob('*/progress.jsonl'));since=int(json.loads(progress[0].read_text().splitlines()[0])['wall']) if progress else int(p.stat().st_mtime)-10
 code='SINCE='+repr(since)+'\n'+r"""import socket,subprocess,json,re,time
p=subprocess.run(['journalctl','-k','--since','@'+str(SINCE),'--no-pager','-o','cat'],capture_output=True,text=True,timeout=4)
hits=[s for s in p.stdout.splitlines() if re.search(r'(?i)(reset\s+(?:high|full|super|low)[^\n]*USB|USB[^\n]*reset)',s)]
print(json.dumps(dict(host=socket.gethostname(),usb_reset_lines=hits,kernel_returncode=p.returncode,wall=time.time())))
"""
 with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(lambda h:d.remote(h,code,timeout=8),d.HOSTS))
 bad=[x for x in rows if x.get('returncode')!=0 or x.get('result',{}).get('usb_reset_lines')]
 d.write(r/('live-management-usb-health-'+str(int(time.time()))+'.json'),dict(rows=rows,ok=not bad,since=since,wall=time.time()))
 print('health10',not bad,'bad',bad)
