import concurrent.futures,json,pathlib,subprocess,time,urllib.request,datetime
p=pathlib.Path(__file__).parent
nodes=['1','2','4','5','6','7','8','9','10']
def inventory():
 r=subprocess.run(['python3',str(p/'collect-nine.py'),'after'],capture_output=True,text=True,timeout=30)
 (p/'nine-collection.log').write_text(r.stdout+r.stderr)
def sample():
 start=time.monotonic();samples=[]
 while time.monotonic()-start<58:
  try:
   with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=min(2,60-(time.monotonic()-start))) as response:d=json.load(response)
   item={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'elapsed_s':time.monotonic()-start,'snapshot':d};samples.append(item)
   states={n:d.get('nodes',{}).get(n,{}).get('readiness','MISSING') for n in nodes};print(states,flush=True)
   if all(x=='READY' for x in states.values()):break
  except Exception as e:samples.append({'elapsed_s':time.monotonic()-start,'error':repr(e)})
  time.sleep(min(2,max(0,58-(time.monotonic()-start))))
 (p/'ready-samples.json').write_text(json.dumps(samples,indent=2))
 print('OBSERVATION_SECONDS',time.monotonic()-start,flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 futures=[pool.submit(inventory),pool.submit(sample)]
 for f in futures:f.result()
