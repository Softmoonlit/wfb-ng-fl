#!/usr/bin/env python3
"""保留所有观察样本，在最多十分钟内等待连续120秒全员待命。"""
import json
from pathlib import Path
import time
import urllib.request
b=Path(__file__).resolve().parent
out=Path((b/'current-preflight.txt').read_text().strip())
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
start=time.monotonic()
ready_since=None
samples=0
with (out/'continuous-ready-recheck.jsonl').open('x') as f:
 while True:
  with opener.open('http://127.0.0.1:9090/api/v1/status',timeout=10) as response:state=json.load(response)
  now=time.monotonic();samples+=1
  ok=(state['server_state']=='IDLE' and state['active_job'] is None
      and sorted(int(k) for k,v in state['nodes'].items() if v['readiness']!='OFFLINE')==[1,2,4]
      and all(state['nodes'][str(n)]['readiness']=='READY' and state['nodes'][str(n)]['reported_state']=='IDLE' for n in [1,2,4]))
  if not ok:ready_since=None
  elif ready_since is None:ready_since=now
  continuous=0 if ready_since is None else now-ready_since
  f.write(json.dumps({'elapsed':now-start,'continuous_seconds':continuous,'ok':ok,'status':state})+'\n');f.flush()
  if continuous>=120 or now-start>=600:
   (out/'continuous-ready-summary.json').write_text(json.dumps({'success':continuous>=120,'elapsed':now-start,
      'continuous_seconds':continuous,'samples':samples,'history':'保留本次及首次失败完整样本；不将恢复后成功窗口改写为全程无失稳'},ensure_ascii=False,indent=2))
   break
  time.sleep(1)
