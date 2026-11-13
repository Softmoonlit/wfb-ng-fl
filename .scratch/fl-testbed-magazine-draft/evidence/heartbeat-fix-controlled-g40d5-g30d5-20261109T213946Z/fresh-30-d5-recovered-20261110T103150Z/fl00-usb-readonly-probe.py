#!/usr/bin/env python3
import json
import pathlib
import shlex
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / 'fl00-usb-readonly-probe.json'
OLD_REBIND_WALL = 1794305865
CODE = r'''import json,pathlib,subprocess,socket,time,re
TARGET_MAC='fc:a3:86:b3:86:72'
TARGET_USB='1-2.3:1.0'
OLD_REBIND_WALL=1794305865

def read(path):
    p=pathlib.Path(path)
    try:
        return p.read_text().strip()
    except Exception as exc:
        return 'ERROR:'+repr(exc)

def run(argv, timeout=8):
    try:
        p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
        return dict(argv=argv,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
    except Exception as exc:
        return dict(argv=argv,error=repr(exc))

net=[]
for p in sorted(pathlib.Path('/sys/class/net').glob('*')):
    if not p.is_dir():
        continue
    dev=(p/'device')
    resolved=str(dev.resolve()) if dev.exists() else None
    driver=str((dev/'driver').resolve()) if (dev/'driver').exists() else None
    net.append(dict(name=p.name,address=read(p/'address'),operstate=read(p/'operstate'),ifindex=read(p/'ifindex'),device=resolved,driver=driver,carrier=read(p/'carrier')))

target_usb_parent=[]
for item in target:
    iface=pathlib.Path(item['device'])
    parent=next((q for q in [iface,*iface.parents] if (q/'idVendor').exists()),None)
    if parent is not None:
        target_usb_parent.append(dict(interface_device=item['device'],usb_parent=str(parent),usb_port=parent.name,idVendor=read(parent/'idVendor'),idProduct=read(parent/'idProduct'),manufacturer=read(parent/'manufacturer'),product=read(parent/'product'),serial=read(parent/'serial'),speed=read(parent/'speed'),busnum=read(parent/'busnum'),devnum=read(parent/'devnum'),authorized=read(parent/'authorized'),configuration=read(parent/'bConfigurationValue')))

kernel=run(['journalctl','-k','--since','@'+str(OLD_REBIND_WALL),'--no-pager','-o','short-unix'],timeout=12)
lines=kernel.get('stdout','').splitlines()
usb_events=[line for line in lines if re.search(r'(?i)(usb|error -71|unable to enumer|descriptor|disconnect|reset high|device firmware changed)',line)]
new_71=[line for line in lines if re.search(r"(?i)(error -71|unable to enumer|descriptor read|can't read configurations)",line)]
new_disconnect=[line for line in lines if re.search(r'(?i)(USB disconnect|usb .*disconnect|reset (?:high|full|super|low)-speed USB)',line)]
current_port=target_usb_parent[0]['usb_port'] if target_usb_parent else None
def timestamp(line):
    try:return float(line.split()[0])
    except (IndexError,ValueError):return None
success_candidates=[line for line in lines if current_port and re.search(r'new high-speed USB device',line) and ('usb '+current_port+':' in line)]
success_times=[timestamp(line) for line in success_candidates if timestamp(line) is not None]
latest_successful_enumeration_wall=max(success_times) if success_times and target and target_usb_parent else None
post_success_71=[line for line in new_71 if latest_successful_enumeration_wall is not None and (timestamp(line) or 0)>latest_successful_enumeration_wall]
post_success_disconnect=[line for line in new_disconnect if latest_successful_enumeration_wall is not None and (timestamp(line) or 0)>latest_successful_enumeration_wall]
services={u:run(['systemctl','show',u,'-p','Id','-p','ActiveState','-p','SubState','-p','MainPID','-p','ExecMainStartTimestamp']) for u in ['wfb-fl-server-daemon.service']}
net_addr=run(['ip','-j','addr','show','dev','enP8p1s0'])
net_link=run(['ip','-j','link','show','dev','enP8p1s0'])
print(json.dumps(dict(host=socket.gethostname(),wall=time.time(),old_rebind_wall=OLD_REBIND_WALL,target_mac=TARGET_MAC,target_usb_historical=TARGET_USB,target_netdev=target,all_netdev=net,target_usb_parent=target_usb_parent,kernel_since_old_rebind=kernel,kernel_usb_event_lines=usb_events,new_error_71_lines=new_71,new_disconnect_or_reset_lines=new_disconnect,latest_successful_enumeration_wall=latest_successful_enumeration_wall,post_success_error_71_lines=post_success_71,post_success_disconnect_or_reset_lines=post_success_disconnect,services=services,management_enP8p1s0=dict(link=net_link,addr=net_addr)),ensure_ascii=False))
'''
compile(CODE, '<fl00-usb-readonly-remote>', 'exec')
REMOTE_PROGRAM = "exec(compile(" + repr(CODE) + ", '<fl00-usb-readonly-remote>', 'exec'))"
argv=['sudo','-n','python3','-c',REMOTE_PROGRAM]
started=time.time()
try:
    p=subprocess.run(argv,capture_output=True,text=True,timeout=30)
    result=dict(local_started_wall=started,local_finished_wall=time.time(),ssh_argv=argv,returncode=p.returncode,stderr=p.stderr)
    if p.returncode==0:
        try:
            result['remote']=json.loads(p.stdout)
        except Exception as exc:
            result['stdout']=p.stdout
            result['parse_error']=repr(exc)
    else:
        result['stdout']=p.stdout
except Exception as exc:
    result=dict(local_started_wall=started,local_finished_wall=time.time(),ssh_argv=argv,error=repr(exc))
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(output=str(OUT),returncode=result.get('returncode'),probe_source_compile='ok',remote_host=result.get('remote',{}).get('host'),target_netdev=result.get('remote',{}).get('target_netdev'),target_usb_parent=result.get('remote',{}).get('target_usb_parent'),latest_successful_enumeration_wall=result.get('remote',{}).get('latest_successful_enumeration_wall'),post_success_error_71=len(result.get('remote',{}).get('post_success_error_71_lines',[])),post_success_disconnect=len(result.get('remote',{}).get('post_success_disconnect_or_reset_lines',[]))),ensure_ascii=False))
raise SystemExit(0 if result.get('returncode')==0 else 1)
