#!/usr/bin/env python3
import concurrent.futures, importlib.util, json, pathlib, shlex, subprocess, time
ROOT=pathlib.Path(__file__).resolve().parent
CLIENTS=['fl01','fl02','fl03','fl05','fl06','fl07','fl08','fl09','fl10']
IFACE='wlxfca386b38672'; DEVICE_ID='1-2.3:1.0'; EXPECTED_DRIVER='rtl88xxau_wfb'; EXPECTED_MAC='fc:a3:86:b3:86:72'

def run(a,timeout=15):
 p=subprocess.run(a,capture_output=True,text=True,timeout=timeout);return dict(argv=a,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
def remote(h,code):
 return run(['ssh','-T','-oBatchMode=yes','-oConnectTimeout=5',h,shlex.join(['sudo','-n','python3','-c',code])],30)

def facts():
 d=pathlib.Path('/sys/class/net')/IFACE
 return dict(wall=time.time(),driver=pathlib.Path(d/'device/driver').resolve().name,device_path=str((d/'device').resolve()),mac=(d/'address').read_text().strip(),ifindex=int((d/'ifindex').read_text()),operstate=(d/'operstate').read_text().strip(),usb_speed=(d/'device/../speed').read_text().strip(),iw=run(['iw','dev',IFACE,'info']),native=run(['ps','-eo','pid=,ppid=,args=']),kernel=run(['journalctl','-k','--since','-30s','--no-pager','-o','short-iso']),routes={g:run(['ip','-j','route','show','exact',g]) for g in ['239.80.41.1/32','239.80.42.1/32']})

def main():
 status=None
 try:
  import urllib.request
  with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=4) as r:status=json.load(r)
 except Exception as e:status={'error':repr(e)}
 pre=dict(status=status,iface=facts(),mgmt_addr=run(['ip','-j','addr','show','dev','enP8p1s0']),mgmt_route=run(['ip','route','get','192.168.0.191']),server_service=run(['sudo','-n','systemctl','show','wfb-fl-server-daemon.service','-p','MainPID','-p','ActiveState','-p','SubState']))
 (ROOT/'rebind-preflight.json').write_text(json.dumps(pre,ensure_ascii=False,indent=2)+'\n')
 if pre['iface']['driver']!=EXPECTED_DRIVER or pre['iface']['device_path'].split('/')[-1]!=DEVICE_ID or pre['iface']['mac']!=EXPECTED_MAC:
  raise RuntimeError('target interface preflight mismatch')
 if status.get('error'):
  if 'ActiveState=active' in pre['server_service'].get('stdout',''):raise RuntimeError('status unavailable while server claims active')
 elif status.get('active_job') is not None or status.get('server_state')!='IDLE':
  raise RuntimeError('active job or non-IDLE server')
 code="import subprocess,json,socket\np=subprocess.run(['systemctl','stop','wfb-fl-client-daemon.service'],capture_output=True,text=True,timeout=20)\nprint(json.dumps(dict(host=socket.gethostname(),returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)))\nraise SystemExit(p.returncode)"
 with concurrent.futures.ThreadPoolExecutor(max_workers=len(CLIENTS)) as pool:stops=list(pool.map(lambda h:remote(h,code),CLIENTS))
 server_stop=run(['sudo','-n','systemctl','stop','wfb-fl-server-daemon.service'],30)
 (ROOT/'rebind-standard-stop.json').write_text(json.dumps(dict(clients=stops,server=server_stop,wall=time.time()),ensure_ascii=False,indent=2)+'\n')
 if server_stop['returncode']!=0 or any(x.get('returncode')!=0 for x in stops):raise RuntimeError('standard stop failed')
 time.sleep(3)
 post=dict(server_native=run(['ps','-eo','pid=,ppid=,args=']),server_units=run(['sudo','-n','systemctl','list-units','--all','--no-legend','--plain','comm-smoke-fresh30d5-*']),iface=facts())
 (ROOT/'rebind-native-exit-check.json').write_text(json.dumps(post,ensure_ascii=False,indent=2)+'\n')
 if any('wfb_v6_uplink' in x for x in post['server_native']['stdout'].splitlines()):raise RuntimeError('server native still running')
 print(json.dumps(dict(status='stopped-native-exited',preflight=pre['iface'],stop=dict(server=server_stop,client_returncodes=[x.get('returncode') for x in stops]),post_iface=post['iface']),ensure_ascii=False))
if __name__=='__main__':main()
