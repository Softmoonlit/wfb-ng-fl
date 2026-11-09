import concurrent.futures,json,os,pathlib,subprocess,sys,urllib.request,datetime
BASE=pathlib.Path(__file__).resolve().parent
HOSTS={'fl00':'wfb-fl-server-daemon.service','fl10':'wfb-fl-client-daemon.service'}
def save(name,data): (BASE/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
PROBE=r'''
import json,pathlib,socket,subprocess,datetime
host=socket.gethostname(); unit='wfb-fl-server-daemon.service' if host=='fl00' else 'wfb-fl-client-daemon.service'
def read(p):
 try:return p.read_text().strip()
 except OSError:return None
interfaces=[]
for p in pathlib.Path('/sys/class/net').iterdir():
 dev=(p/'device').resolve(); driver=(p/'device/driver').resolve()
 if driver.name!='rtl88xxau_wfb':continue
 usb=[]
 for q in [dev,*dev.parents]:
  if (q/'idVendor').exists():usb.append(dict(path=str(q),attributes={k:read(q/k) for k in ['idVendor','idProduct','speed','busnum','devnum','devpath']}))
 interfaces.append(dict(interface=p.name,mac=read(p/'address'),device_path=str(dev),driver=str(driver),usb=usb))
native=[]
for p in pathlib.Path('/proc').iterdir():
 if p.name.isdigit() and read(p/'comm')=='wfb_v6_uplink':
  try:native.append(dict(pid=int(p.name),argv=(p/'cmdline').read_bytes().decode().split('\0')))
  except OSError:pass
s=subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID','-p','ExecStart'],capture_output=True,text=True,timeout=4)
print(json.dumps(dict(host=host,utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),unit=unit,service=s.stdout,service_returncode=s.returncode,service_stderr=s.stderr,interfaces=interfaces,native=native)))
'''
def invoke(host,code):
 argv=['sudo','-n','timeout','22s','python3','-'] if host=='fl00' else ['ssh','-T','-oBatchMode=yes','-oConnectTimeout=4','-oServerAliveInterval=3','-oServerAliveCountMax=1',host,'sudo -n timeout 22s python3 -']
 try:
  p=subprocess.run(argv,input=code,text=True,capture_output=True,timeout=26)
  result=dict(host=host,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
  try:result['result']=json.loads(p.stdout)
  except ValueError:pass
  return result
 except subprocess.TimeoutExpired as e:return dict(host=host,error='bounded timeout',stdout=str(e.stdout),stderr=str(e.stderr))
def preflight():
 ancestors=set(); p=os.getpid()
 while p>1:
  ancestors.add(p)
  try:p=int(pathlib.Path('/proc',str(p),'stat').read_text().split(') ',1)[1].split()[1])
  except (OSError,ValueError):break
 processes=[]; suspects=[]
 for p in pathlib.Path('/proc').iterdir():
  if not p.name.isdigit() or int(p.name) in ancestors:continue
  try:
   argv=p.joinpath('cmdline').read_bytes().decode().replace('\0',' ').strip()
   comm=p.joinpath('comm').read_text().strip()
  except OSError:continue
  if not argv:continue
  if any(t in argv for t in ['runner.py','orchestration','single-model','communication/','uftp','fl-testbed']):
   record=dict(pid=int(p.name),comm=comm,args=argv); processes.append(record)
   if comm not in ['bash','rg','timeout'] and 'wfb_ng.fl.server_daemon' not in argv:suspects.append(record)
 try:
  with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=4) as r:status=json.load(r)
  status_error=None
 except Exception as e:status={};status_error=str(e)
 online=sorted(int(k) for k,v in status.get('nodes',{}).items() if v.get('readiness')!='OFFLINE')
 result=dict(status=status,status_error=status_error,online_nodes=online,local_relevant_processes=processes,local_runner_candidates=suspects)
 result['ok']=status_error is None and status.get('active_job','missing') is None and status.get('server_state')=='IDLE' and online==[1,2,3,5,6,7,8,9,10] and not suspects
 save('preflight.json',result)
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:records=list(pool.map(lambda h:invoke(h,PROBE),HOSTS))
 save('before-nic-identities.json',records)
 expected={'fl00':'fc:a3:86:b3:86:72','fl10':'fc:22:1c:10:01:19'}
 identities_ok=all(r.get('returncode')==0 and r.get('result',{}).get('host')==r['host'] and r.get('result',{}).get('service_returncode')==0 and any(i['mac']==expected[r['host']] for i in r.get('result',{}).get('interfaces',[])) for r in records)
 gate=dict(preflight_ok=result['ok'],identities_ok=identities_ok,ok=result['ok'] and identities_ok)
 save('gate.json',gate);print(json.dumps(gate))
if __name__=='__main__':preflight()
