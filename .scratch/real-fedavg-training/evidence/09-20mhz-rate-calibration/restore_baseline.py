#!/usr/bin/env python3
"""只通过03普通IPC恢复指定基线；READY准入拒绝保留并等待，不重启。"""
import pathlib,sys,json,time,datetime,urllib.request,urllib.error
r=pathlib.Path(sys.argv[1]);op=urllib.request.build_opener(urllib.request.ProxyHandler({}));history=[]
request={'confirmed':True,'target_nodes':[1,2,4],'patch':{'channel':157,'bandwidth_mhz':40,'radio_txpower_dbm':14,'downlink_mcs':3,'uplink_mcs':4,'uftp_rate_kbps':15000}}
for i in range(90):
    s=json.load(op.open('http://127.0.0.1:9090/api/v1/status',timeout=5));entry={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':s}
    ready= s['server_state']=='IDLE' and s.get('active_job') is None and all(s['nodes'][str(n)]['readiness']=='READY' for n in [1,2,4])
    if ready:
        req=urllib.request.Request('http://127.0.0.1:9090/api/v1/radio/reconfigure',data=json.dumps(request).encode(),headers={'Content-Type':'application/json'})
        try:
            with op.open(req,timeout=45) as resp:entry['response']={'http':resp.status,'body':json.load(resp)}
        except urllib.error.HTTPError as e:entry['response']={'http':e.code,'body':json.load(e)}
        print('restore',entry.get('response'),flush=True)
    history.append(entry);(r/'restore-retries.json').write_text(json.dumps(history,ensure_ascii=False,indent=2))
    if entry.get('response',{}).get('body',{}).get('status')=='finalized':break
    time.sleep(1)
(r/'restore-retries-finished.txt').write_text(str(time.time()))
