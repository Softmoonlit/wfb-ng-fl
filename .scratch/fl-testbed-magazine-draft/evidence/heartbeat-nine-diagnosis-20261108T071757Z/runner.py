import concurrent.futures,subprocess,pathlib,datetime,json,time
root=pathlib.Path('evidence')/('heartbeat-nine-diagnosis-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));root.mkdir();(pathlib.Path('/tmp/hb-nine-root')).write_text(str(root.resolve())); probe=pathlib.Path('/tmp/hb-nine-probe.py').read_text();(root/'probe.py').write_text(probe); start=time.monotonic()
def run(host):
 args=['sudo','-n','timeout','58','python3','-'] if host=='fl00' else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=3',host,'sudo -n timeout 58 python3 -']
 try:
  p=subprocess.run(args,input=probe,text=True,capture_output=True,timeout=63);(root/(host+'.json')).write_text(p.stdout);(root/(host+'.stderr')).write_text(p.stderr);return {'host':host,'exit':p.returncode}
 except Exception as e:return {'host':host,'error':str(e)}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(run,['fl00','fl01','fl05','fl04']))
(root/'outcomes.json').write_text(json.dumps({'elapsed':time.monotonic()-start,'results':results},indent=2));print(root);print(results)
