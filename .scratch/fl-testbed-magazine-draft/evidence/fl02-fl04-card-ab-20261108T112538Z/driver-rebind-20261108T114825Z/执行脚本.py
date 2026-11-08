
import os,sys,time,json,subprocess,pathlib,hashlib,glob
T=time.monotonic(); S='wfb-fl-client-daemon.service'; I='wlxbcec23372588'; U='1-2.3:1.0'; D='/sys/bus/usb/drivers/rtl88xxau_wfb'; L='/srv/wfb-ng-fl/client/wfb_uplink.log'
def emit(step,rc,**kw): print(json.dumps(dict(step=step,rc=rc,elapsed=time.monotonic()-T,**kw),ensure_ascii=False),flush=True)
def cmd(step,args,required=False):
 try:
  p=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=min(20,max(1,170-(time.monotonic()-T)))); emit(step,p.returncode,stdout=p.stdout,stderr=p.stderr)
  if required and p.returncode: raise RuntimeError(step+' failed')
  return p.stdout
 except subprocess.TimeoutExpired: emit(step,124); raise

def read(step,p,required=False):
 try: v=pathlib.Path(p).read_text(); emit(step,0,path=p,value=v); return v
 except Exception as e:
  emit(step,1,path=p,error=str(e))
  if required: raise

def native():
 out=[]
 for p in pathlib.Path('/proc').iterdir():
  if p.name.isdigit():
   try:
    exe=os.path.realpath(str(p/'exe'))
    if os.path.basename(exe)=='wfb_v6_uplink': out.append((int(p.name),(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(),exe))
   except OSError: pass
 return out

def pm(prefix):
 for n in ('control','runtime_status','autosuspend_delay_ms'): read(prefix+'-usb-pm-'+n,'/sys/bus/usb/devices/1-2.3/power/'+n)
 for n in ('rtw_power_mgnt','rtw_enusbss','rtw_switch_usb_mode'):
  p='/sys/module/88XXau_wfb/parameters/'+n
  if os.path.exists(p): read(prefix+'-module-'+n,p)
  else: emit(prefix+'-module-'+n,0,present=False)
def identity():
 dev=os.path.realpath('/sys/class/net/'+I+'/device'); driver=os.path.realpath(dev+'/driver'); module=os.path.realpath(driver+'/module'); mgmt=os.path.realpath('/sys/class/net/wlP4p1s0/device/driver/module'); mac=pathlib.Path('/sys/class/net/'+I+'/address').read_text().strip()
 good=(os.path.basename(dev)==U and driver==D and os.path.basename(driver)=='rtl88xxau_wfb' and module=='/sys/module/88XXau_wfb' and mgmt=='/sys/module/rtl8822ce' and mac=='bc:ec:23:37:25:88')
 emit('identity',0 if good else 1,device=dev,canonicaldriver=driver,module=module,management_module=mgmt,mac=mac)
 if not good: raise RuntimeError('identity mismatch')
 return (dev,driver,module,mgmt,mac)
try:
 emit('window-start',0,utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
 identity(); pm('before')
 for n in ('unbind','bind'):
  p=D+'/'+n; ok=os.path.exists(p) and os.access(p,os.W_OK); emit('check-'+n,0 if ok else 1,path=p)
  if not ok: raise RuntimeError('bind/unbind unavailable: '+p)
 cmd('before-kernel',['uname','-a']); cmd('before-service',['systemctl','show',S,'-p','ExecStart','-p','MainPID','-p','ActiveState']); beforeexec=cmd('before-service-cat',['systemctl','cat',S]); cmd('before-processes',['ps','-eo','pid,ppid,args']); cmd('before-iw',['iw','dev',I,'info']); cmd('before-nmcli',['nmcli','-t','-f','GENERAL.DEVICE,GENERAL.STATE,GENERAL.NM-MANAGED','device','show',I],True); cmd('before-power',['nvpmodel','-q']); cmd('before-route',['ip','route','get','192.168.0.190']); read('before-speed','/sys/bus/usb/devices/1-2.3/speed'); read('before-rx','/sys/class/net/'+I+'/statistics/rx_packets'); cmd('before-usb',['lsusb','-t']); cmd('before-kernel-log',['dmesg','--ctime']); cmd('before-journal',['journalctl','-u',S,'-n','60','--no-pager']); cmd('before-native-log',['tail','-n','180',L])
 cmd('before-proc',['find','/proc/net','-maxdepth','3','-type','d']);
 for proc in glob.glob('/proc/net/rtl*/'+I+'/*'):
  if os.path.basename(proc) in ('rx_info','trx_info','monitor','rx_signal','usb_info','rtw_power_mgnt'): read('before-proc-'+os.path.basename(proc),proc)
 natives=native(); emit('before-native',0,processes=natives)
 if len(natives)!=1: raise RuntimeError('expected exactly one native')
 oldargs=natives[0][1]; oldhash=hashlib.sha256(pathlib.Path('/proc/'+str(natives[0][0])+'/exe').read_bytes()).hexdigest(); emit('before-native-hash',0,sha256=oldhash)
 if not oldhash.startswith('ad3340'): raise RuntimeError('unexpected native hash')
 cmd('stop-service',['systemctl','stop',S],True)
 left=native(); emit('confirm-no-native',0 if not left else 1,processes=left)
 if left: raise RuntimeError('native remains')
 identity()
 for action in ('unbind','bind'):
  try:
   with open(D+'/'+action,'w') as f: f.write(U)
   emit(action,0,device=U)
  except Exception as e: emit(action,1,error=str(e)); raise
 end=time.monotonic()+20
 while not os.path.exists('/sys/class/net/'+I) and time.monotonic()<end: time.sleep(.2)
 identity(); cmd('post-bind-nmcli',['nmcli','-t','-f','GENERAL.DEVICE,GENERAL.STATE,GENERAL.NM-MANAGED','device','show',I],True)
 cmd('start-service',['systemctl','start',S],True)
 end=time.monotonic()+20
 while not native() and time.monotonic()<end: time.sleep(.2)
 ns=native(); emit('after-native',0 if len(ns)==1 else 1,processes=ns)
 if len(ns)!=1: raise RuntimeError('native not running')
 nh=hashlib.sha256(pathlib.Path('/proc/'+str(ns[0][0])+'/exe').read_bytes()).hexdigest(); same=(ns[0][1]==oldargs and nh==oldhash); emit('preserved-native-args-hash',0 if same else 1,sha256=nh)
 if not same: raise RuntimeError('native changed')
 # Allow standard startup configuration to settle, without issuing radio commands.
 time.sleep(3)
 t0=time.monotonic(); r0=int(pathlib.Path('/sys/class/net/'+I+'/statistics/rx_packets').read_text()); offset=os.stat(L).st_size; emit('rx-window-start',0,monotonic=t0,rx_packets=r0,log_offset=offset)
 time.sleep(30)
 t1=time.monotonic(); r1=int(pathlib.Path('/sys/class/net/'+I+'/statistics/rx_packets').read_text()); emit('rx-window-end',0,monotonic=t1,rx_packets=r1,delta=r1-r0,duration=t1-t0)
 with open(L) as f: f.seek(offset); emit('current-native-30s-log',0,text=f.read())
 cmd('post-current-native-log',['tail','-n','200',L]); identity(); pm('post'); cmd('post-iw',['iw','dev',I,'info']); cmd('post-power',['nvpmodel','-q']); cmd('post-service',['systemctl','show',S,'-p','ActiveState','-p','MainPID','-p','ExecStart']); afterexec=cmd('post-service-cat',['systemctl','cat',S]); emit('service-definition-preserved',0 if beforeexec==afterexec else 1); cmd('post-journal',['journalctl','-u',S,'-n','45','--no-pager']); cmd('post-kernel-log',['dmesg','--ctime']); emit('complete',0)
except Exception as e:
 emit('SAFE-STOP',1,error=str(e),policy='不重试，不reload module，不reboot，不forceUSB3，不改PM；保留当前状态'); sys.exit(1)
