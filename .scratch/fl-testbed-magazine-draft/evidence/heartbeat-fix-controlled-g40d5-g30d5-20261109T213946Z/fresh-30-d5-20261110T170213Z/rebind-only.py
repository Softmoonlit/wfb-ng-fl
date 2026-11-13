#!/usr/bin/env python3
import json,pathlib,subprocess,time
ROOT=pathlib.Path(__file__).resolve().parent
IFACE='wlxfca386b38672'; DEVICE='1-2.3:1.0'; DRIVER='rtl88xxau_wfb'; MAC='fc:a3:86:b3:86:72'
def run(a,t=20):
 p=subprocess.run(a,capture_output=True,text=True,timeout=t);return dict(argv=a,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
def fact():
 d=pathlib.Path('/sys/class/net')/IFACE
 if not d.exists():return dict(exists=False,wall=time.time())
 return dict(exists=True,wall=time.time(),driver=pathlib.Path(d/'device/driver').resolve().name,device_path=str((d/'device').resolve()),mac=(d/'address').read_text().strip(),ifindex=int((d/'ifindex').read_text()),usb_speed=(d/'device/../speed').read_text().strip(),iw=run(['iw','dev',IFACE,'info']),kernel=run(['journalctl','-k','--since','-10s','--no-pager','-o','short-iso']))
pre=fact()
if not pre.get('exists') or pre['driver']!=DRIVER or pre['device_path'].split('/')[-1]!=DEVICE or pre['mac']!=MAC:raise SystemExit('pre-rebind identity mismatch')
cmd=f"set -eu; echo -n {DEVICE} > /sys/bus/usb/drivers/{DRIVER}/unbind; sleep 2; echo -n {DEVICE} > /sys/bus/usb/drivers/{DRIVER}/bind; udevadm settle"
operation=run(['sudo','-n','bash','-c',cmd],30)
(ROOT/'rebind-operation.json').write_text(json.dumps(operation,ensure_ascii=False,indent=2)+'\n')
end=time.monotonic()+30;after=None
while time.monotonic()<end:
 after=fact()
 if after.get('exists') and after.get('driver')==DRIVER and after.get('device_path','').split('/')[-1]==DEVICE and after.get('mac')==MAC:break
 time.sleep(1)
result=dict(pre=pre,operation=operation,after=after,wall=time.time(),scope='single USB interface driver unbind/bind only; no module unload/autosuspend/power/management interface change')
(ROOT/'rebind-after.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(operation_returncode=operation['returncode'],pre_ifindex=pre.get('ifindex'),after_ifindex=after.get('ifindex') if after else None,after_exists=after.get('exists') if after else None,after_iw=after.get('iw') if after else None),ensure_ascii=False))
raise SystemExit(0 if operation['returncode']==0 and after and after.get('exists') and after.get('driver')==DRIVER and after.get('mac')==MAC else 1)
