import pathlib,json,importlib.util,concurrent.futures,hashlib,time
ROOT=pathlib.Path(__file__).parent
s=importlib.util.spec_from_file_location('snapshot_driver',ROOT/'driver-base.py');d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
CODE=r'''import pathlib,json,subprocess,socket,hashlib,re,shlex,os,time
host=socket.gethostname()
def text(p):
 try:return pathlib.Path(p).read_text().strip()
 except OSError:return None
def digest(p):
 try:return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
 except OSError:return None
def cmd(a):
 p=subprocess.run(a,capture_output=True,text=True,timeout=3);return dict(returncode=p.returncode,stdout=p.stdout.strip(),stderr=p.stderr.strip())
osrelease={}
for line in (text('/etc/os-release') or '').splitlines():
 if '=' in line:
  k,v=line.split('=',1)
  if k in ['NAME','ID','VERSION','VERSION_ID','PRETTY_NAME','BUILD_ID']:osrelease[k]=v.strip('"')
unit='wfb-fl-'+('server' if host=='fl00' else 'client')+'-daemon.service'
props=cmd(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID','-p','FragmentPath','-p','DropInPaths','-p','ExecStart'])
allowed={'interface','channel','bandwidth-mhz','txpower','radio-txpower-dbm','downlink-mcs','uplink-mcs','fec-k','fec-n','uftp-rate-kbps','grant-duration-ms','guard-interval-ms'}
flags={k:v for k,v in re.findall(r'--([a-z0-9-]+)\s+([^\s;{}]+)',props['stdout']) if k in allowed}
service={k:v for k,v in (line.split('=',1) for line in props['stdout'].splitlines() if '=' in line) if k!='ExecStart'}
unitfiles=[]
for p in [service.get('FragmentPath',''),*service.get('DropInPaths','').split()]:
 if p:unitfiles.append(dict(path=p,sha256=digest(p)))
configpath='/etc/wfb-ng-fl/server.json' if host=='fl00' else '/etc/wfb-ng-fl/node.json'
config=json.loads(pathlib.Path(configpath).read_text());config_allowed={'node_id','channel','baseline_channel','bandwidth_mhz','radio_bandwidth','interface','air_interface','txpower','txpower_dbm','radio_txpower_dbm','downlink_mcs','uplink_mcs','fec_k','fec_n','uftp_rate_kbps','grant_duration_ms','guard_interval_ms','known_clients'}
selected={k:v for k,v in config.items() if k in config_allowed and isinstance(v,(str,int,float,bool,list,type(None)))}
interfaces=[];modules={}
for p in pathlib.Path('/sys/class/net').iterdir():
 dev=(p/'device').resolve();driver=(p/'device/driver').resolve()
 if driver.name!='rtl88xxau_wfb':continue
 mod=(p/'device/driver/module').resolve();modname=mod.name
 if mod.exists():
  info={}
  for key in ['filename','version','srcversion','vermagic']:
   info[key]=cmd(['modinfo','-F',key,modname])
  filename=info['filename']['stdout'];info['file_sha256']=digest(filename);info['sysfs_version']=text(mod/'version');info['sysfs_srcversion']=text(mod/'srcversion');modules[modname]=info
 usb=[]
 for q in [dev,*dev.parents]:
  if (q/'idVendor').exists():usb.append(dict(path=str(q),attributes={k:text(q/k) for k in ['idVendor','idProduct','speed','busnum','devnum','devpath']}))
 iw=cmd(['iw','dev',p.name,'info'])
 interfaces.append(dict(interface=p.name,mac=text(p/'address'),driver=str(driver),module=str(mod),device_path=str(dev),usb=usb,iw=[line.strip() for line in iw['stdout'].splitlines() if line.strip().startswith(('channel ','type ','txpower '))]))
print(json.dumps(dict(host=host,wall=time.time(),os_release=osrelease,kernel_release=os.uname().release,kernel_version=os.uname().version,nv_tegra_release=text('/etc/nv_tegra_release'),driver_modules=modules,resident_unit=unit,service=service,unit_file_digests=unitfiles,explicit_unit_radio_flags=flags,config_path=configpath,config_radio_whitelist=selected,interfaces=interfaces,privacy='OS/module/sysfs identity and explicit radio whitelist only; no environment or arbitrary config values')))
'''
initial=d.status();assert initial.get('active_job') is None and initial.get('server_state')=='IDLE'
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(lambda h:d.remote(h,CODE,timeout=24),d.HOSTS))
final=d.status();assert final.get('active_job') is None and final.get('server_state')=='IDLE'
d.write(ROOT/'final-idle-02-vs-all-targeted-snapshot.json',dict(initial_idle_status=initial,final_idle_status=final,rows=rows,ok=all(x.get('returncode')==0 for x in rows),wall=time.time()))
for row in rows:
 x=row.get('result',{});print(json.dumps(dict(host=row['host'],returncode=row.get('returncode'),os=x.get('os_release'),kernel=x.get('kernel_release'),module={k:v.get('file_sha256') for k,v in x.get('driver_modules',{}).items()},radio=x.get('explicit_unit_radio_flags'),config=x.get('config_radio_whitelist'),usb=[dict(interface=i['interface'],mac=i['mac'],speed=i['usb'][0]['attributes']['speed'],iw=i['iw']) for i in x.get('interfaces',[])])))
