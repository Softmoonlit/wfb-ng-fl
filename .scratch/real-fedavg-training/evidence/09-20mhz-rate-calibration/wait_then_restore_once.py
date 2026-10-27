#!/usr/bin/env python3
"""只读等待全部READY后仅请求一次普通恢复；每步写回执，失败不重试。"""
import importlib.util,pathlib,time,json,datetime
root=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('measurement09',root/'run_measurements.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
path=m.RUN/'recovery-wait-status.json'
for i in range(450):
    ok,response=m.ready();s=response['body'];v={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'phase':'waiting_ready_readonly','checks':i+1,'status':response};m.save(path,v)
    if i%15==0:print(v['utc'],'READONLY',[(n,s['nodes'][n]['readiness']) for n in ['1','2','4']],flush=True)
    if ok:
        req={'confirmed':True,'target_nodes':[1,2,4],'patch':{'channel':157,'bandwidth_mhz':40,'radio_txpower_dbm':14,'downlink_mcs':3,'uplink_mcs':4,'uftp_rate_kbps':15000}}
        m.save(m.RUN/'recovery-single-request.json',req);r=m.api('radio/reconfigure',req);m.save(m.RUN/'recovery-single-response.json',r);print('RESTORE',json.dumps(r),flush=True)
        actual=m.allhosts(m.SNAP);m.save(m.RUN/'recovery-actual.json',actual);final=m.api('status');m.save(m.RUN/'recovery-final-status.json',final)
        m.save(path,{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'phase':'finished','response':r,'status':final});break
    time.sleep(2)
else:m.save(path,{'phase':'timeout_no_request_sent','last_status':response})
