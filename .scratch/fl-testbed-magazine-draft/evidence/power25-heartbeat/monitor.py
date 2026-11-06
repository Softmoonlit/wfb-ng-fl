import concurrent.futures
import datetime
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

folder = pathlib.Path(sys.argv[1])
duration = int(sys.argv[2])
assert 10 <= duration <= 180
nodes = [1,2,4,5,6,7,8,9,10]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
started = time.monotonic()
counts = {str(n): {'ready':0,'samples':0,'states':{},'max_heartbeat_age':0} for n in nodes}
errors = []
hardware_errors = []

def hardware(host):
    command = ['sudo','-n','iw','dev'] if host=='fl00' else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=3',host,'sudo','-n','iw','dev']
    try:
        p = subprocess.run(command,capture_output=True,text=True,timeout=5)
        # 专用网卡唯一；管理卡为正功率，此处只验证本次硬件覆盖是否保持。
        valid = p.returncode==0 and p.stdout.count('txpower -25.00 dBm')==1
        return {'host':host,'valid_index25':valid,'exit':p.returncode,'iw':p.stdout,'stderr':p.stderr}
    except Exception as exc:
        return {'host':host,'valid_index25':False,'error':str(exc)}

with (folder/'samples.jsonl').open('w',buffering=1) as output:
    sample = 0
    while time.monotonic()-started < duration:
        record = {'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'elapsed':round(time.monotonic()-started,3)}
        try:
            with opener.open('http://127.0.0.1:9090/api/v1/status',timeout=3) as response:
                state = json.load(response)
            record['state'] = state
            for n in nodes:
                node = state['nodes'].get(str(n),{})
                count = counts[str(n)]
                count['samples'] += 1
                ready = node.get('readiness')=='READY' and node.get('reported_state')=='IDLE' and node.get('current_channel')==149
                count['ready'] += int(ready)
                key = str(node.get('readiness'))+'/'+str(node.get('reported_state'))
                count['states'][key] = count['states'].get(key,0)+1
                age = node.get('last_heartbeat_ago_seconds')
                if age is not None:count['max_heartbeat_age']=max(count['max_heartbeat_age'],age)
        except Exception as exc:
            record['error']=str(exc);errors.append(str(exc))
        if sample % 15 == 0:
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                record['hardware']=list(pool.map(hardware,['fl00']+[f'fl{n:02d}' for n in nodes]))
            hardware_errors.extend(x for x in record['hardware'] if not x['valid_index25'])
        output.write(json.dumps(record,ensure_ascii=False)+'\n')
        summary = {'elapsed':round(time.monotonic()-started,2),'target_seconds':duration,'counts':counts,'ipc_errors':errors,'hardware_errors':hardware_errors,'finished':False}
        (folder/'progress.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        sample+=1
        time.sleep(1)
summary['finished']=True
summary['all_nodes_ready_every_sample']=not errors and not hardware_errors and all(c['ready']==c['samples'] and c['samples']>0 for c in counts.values())
(folder/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False))
sys.exit(0 if summary['all_nodes_ready_every_sample'] else 1)
