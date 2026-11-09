import concurrent.futures,json
from prepare import BASE,HOSTS,PROBE,invoke,save
assert json.loads((BASE/'gate.json').read_text())['ok']
STOP=r'''
import subprocess,json,socket,datetime,pathlib
host=socket.gethostname();unit='wfb-fl-server-daemon.service' if host=='fl00' else 'wfb-fl-client-daemon.service'
try:
 p=subprocess.run(['systemctl','stop',unit],capture_output=True,text=True,timeout=15)
 result=dict(host=host,unit=unit,utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),stop_returncode=p.returncode,stop_stdout=p.stdout,stop_stderr=p.stderr)
except subprocess.TimeoutExpired:result=dict(host=host,unit=unit,error='systemctl stop timeout 15s')
s=subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID'],capture_output=True,text=True,timeout=3)
result.update(service=s.stdout,service_returncode=s.returncode,remaining_native=[])
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  if (p/'comm').read_text().strip()=='wfb_v6_uplink':result['remaining_native'].append(int(p.name))
 except OSError:pass
props=dict(line.split('=',1) for line in s.stdout.splitlines() if '=' in line)
result['ok']=result.get('stop_returncode')==0 and s.returncode==0 and props.get('ActiveState')=='inactive' and props.get('SubState')=='dead' and props.get('MainPID')=='0' and not result['remaining_native']
print(json.dumps(result))
'''
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:records=list(pool.map(lambda h:invoke(h,STOP),HOSTS))
ready=all(r.get('returncode')==0 and r.get('result',{}).get('ok') for r in records)
save('standard-stop-results.json',dict(ready_for_user_physical_swap=ready,stops=records,host_shutdown=False,management_wifi_changed=False,interface_bindings_changed=False,power_or_radio_parameters_changed=False,new_trials_started=False,other_clients_including_05_unchanged=True,physical_swap_completed=False,expected_after_swap={'fl00':'fc:22:1c:10:01:19','fl10':'fc:a3:86:b3:86:72'}))
print(json.dumps(dict(ready_for_user_physical_swap=ready,stops=records),ensure_ascii=False,indent=2))
