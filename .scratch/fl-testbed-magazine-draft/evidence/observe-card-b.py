import concurrent.futures, datetime, json, os, pathlib, subprocess, time, urllib.request
root=pathlib.Path(__file__).resolve().parent / (os.environ.get('CARD_RUN_LABEL', 'fl01-card-b')+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
root.mkdir()
capture=pathlib.Path(__file__).resolve().parent.joinpath('board-power/heartbeat-capture.py').read_text()
def heartbeat(host):
 cmd=['sudo','-n','python3','-'] if host=='fl00' else ['ssh','-o','ConnectTimeout=6',host,'sudo -n python3 -']
 p=subprocess.run(cmd,input=capture,text=True,capture_output=True,timeout=50)
 (root/(host+'-heartbeat.json')).write_text(p.stdout)
 (root/(host+'-heartbeat.err')).write_text(p.stderr)
def radio():
 p=subprocess.run(['ssh','-o','ConnectTimeout=6','fl01','sudo -n iw dev '+os.environ.get('CARD_INTERFACE','wlxfc221c300e99')+' info'],capture_output=True,text=True,timeout=12)
 return {'exit':p.returncode,'out':p.stdout,'err':p.stderr}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
 futures=[ex.submit(heartbeat,h) for h in ['fl00','fl01']]
 with (root/'samples.jsonl').open('w') as f:
  start=time.monotonic()
  for i in range(24):
   r={'sample':i,'mono':time.monotonic(),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
   try:
    with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=3) as response:r['status']=json.load(response)
   except Exception as e:r['error']=repr(e)
   if i in (0,12,23):r['radio']=radio()
   f.write(json.dumps(r)+'\n'); f.flush()
   time.sleep(max(0,start+(i+1)*5-time.monotonic()))
 for f in futures:f.result()
p=subprocess.run(['ssh','fl01','sudo -n journalctl -u wfb-fl-client-daemon.service --since "4 minutes ago" --no-pager'],capture_output=True,text=True,timeout=15)
(root/'fl01-journal.txt').write_text(p.stdout)
print(root,flush=True)
