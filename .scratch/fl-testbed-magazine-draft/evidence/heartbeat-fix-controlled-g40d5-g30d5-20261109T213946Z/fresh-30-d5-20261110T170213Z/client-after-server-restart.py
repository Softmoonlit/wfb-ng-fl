#!/usr/bin/env python3
import concurrent.futures
import importlib.util
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parent
CLIENTS = ['fl01','fl02','fl03','fl05','fl06','fl07','fl08','fl09','fl10']
s=importlib.util.spec_from_file_location('d',ROOT/'driver-base.py');d=importlib.util.module_from_spec(s);s.loader.exec_module(d);d.MATRIX['parent_GO_verified']=True;d.MATRIX['deadline_monotonic']=time.monotonic()+180
code="import subprocess,json,socket\np=subprocess.run(['systemctl','restart','wfb-fl-client-daemon.service'],capture_output=True,text=True,timeout=20)\nprint(json.dumps(dict(host=socket.gethostname(),returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)))\nraise SystemExit(p.returncode)"
with concurrent.futures.ThreadPoolExecutor(max_workers=len(CLIENTS)) as pool:
 rows=list(pool.map(lambda h:d.remote(h,code,timeout=30),CLIENTS))
result=dict(restarts=rows,wall=time.time())
(ROOT/'client-after-server-restart.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
if any(r.get('returncode')!=0 for r in rows): raise SystemExit(1)
end=time.monotonic()+90;observations=[]
while time.monotonic()<end:
 st=d.status(); nodes=st.get('nodes',{}); observations.append(dict(wall=time.time(),online=sorted(k for k,v in nodes.items() if v.get('readiness')!='OFFLINE'),bad={k:{x:nodes.get(k,{}).get(x) for x in ['readiness','reported_state','current_channel']} for k in map(str,d.NODES) if nodes.get(k,{}).get('readiness')!='READY'},radio=st.get('radio')))
 if observations[-1]['online']==[str(n) for n in d.NODES] and all(nodes[str(n)].get('readiness')=='READY' and nodes[str(n)].get('reported_state')=='IDLE' and nodes[str(n)].get('current_channel')==149 for n in d.NODES):break
 time.sleep(5)
(ROOT/'client-after-server-settle.json').write_text(json.dumps(dict(observations=observations,final=observations[-1]),ensure_ascii=False,indent=2)+'\n')
print(json.dumps(observations[-1],ensure_ascii=False))
