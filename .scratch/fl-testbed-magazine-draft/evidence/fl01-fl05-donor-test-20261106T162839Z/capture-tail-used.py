import concurrent.futures,json,pathlib,subprocess,time
root=pathlib.Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/evidence/fl01-fl05-donor-test-20261106T162839Z/donor-10min')
first=json.loads((root/'samples.jsonl').read_text().splitlines()[0])
deadline=first['mono']+540
while time.monotonic()<deadline:
 if (root/'result.json').exists():raise SystemExit('主观察已提前结束，不启动尾部抓包')
 time.sleep(min(10,deadline-time.monotonic()))
out=root/'tail-capture';out.mkdir()
script=pathlib.Path('/home/fl/projects/wfb-ng-fl/.scratch/heartbeat-restart/capture-heartbeats.py').read_text()
def capture(host):
 command=['sudo','-n','/usr/bin/python3','-'] if host=='fl00' else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=6',host,'sudo -n /usr/bin/python3 -']
 try:
  p=subprocess.run(command,input=script,text=True,capture_output=True,timeout=50)
  (out/(host+'-capture.json')).write_text(p.stdout);(out/(host+'-capture.err')).write_text(p.stderr)
  return {'host':host,'exit':p.returncode}
 except Exception as e:return {'host':host,'exit':-1,'error':repr(e)}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(capture,['fl00','fl01','fl05']))
(out/'capture-outcomes.json').write_text(json.dumps(results,indent=2)+'\n')
print(results)
