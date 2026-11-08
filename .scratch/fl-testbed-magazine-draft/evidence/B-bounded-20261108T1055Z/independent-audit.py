import json,pathlib,socket,struct,subprocess

def run(a):return subprocess.check_output(a,text=True,timeout=8).strip()
host=socket.gethostname();node=int(host[2:]);unit='wfb-fl-'+('server' if node==0 else 'client')+'-daemon.service'
pid=run(['systemctl','show',unit,'-p','MainPID','--value']); da=pathlib.Path('/proc',pid,'cmdline').read_bytes().decode().strip('\0').split('\0')
ns=[]
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  a=p.read_bytes().decode().strip('\0').split('\0')
  if a[0]=='/usr/bin/wfb_v6_uplink':ns.append((p.parent.name,a))
 except (OSError,UnicodeError):pass
assert len(ns)==1,ns
np,argv=ns[0]
def opt(n):return argv[argv.index(n)+1]
port=int(opt('--radio-control-port'))
def get(cmd,fmt):
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
  s.settimeout(3);s.sendto(struct.pack('!IB',0x434f4d4d,cmd),('127.0.0.1',port));raw,addr=s.recvfrom(4096)
  v=struct.unpack(fmt,raw);assert v[:2]==(0x434f4d4d,0);assert addr==('127.0.0.1',port);return list(v)
fec=get(3,'!II2B');radio=get(4,'!II7B');iw=run(['iw','dev',opt('--air-interface'),'info'])
units=run(['systemctl','list-units','--all','--no-legend','--plain','comm-*.service'])
assert not any((' active ' in l or ' activating ' in l or ' deactivating ' in l) for l in units.splitlines()),units
assert fec[2:]==[8,14] and opt('--fec-k')=='8' and opt('--fec-n')=='14'
assert radio[4]==0 and radio[5:7]==[40,3 if node==0 else 4]
assert 'channel 149 ' in iw and 'width: 40 MHz' in iw and 'txpower -18.00 dBm' in iw
if node==0:assert opt('--grant-duration-ms')=='18' and opt('--guard-interval-ms')=='2'
routes=run(['ip','-j','route','show','proto','186']);assert json.loads(routes)==[],routes
out=dict(host=host,ok=True,daemon_pid=pid,native_pid=np,daemon_argv=da,native_argv=argv,GetFEC=fec,get_radio=radio,iw=iw,comm_units=units,owned_routes=json.loads(routes))
if node==0:
 import urllib.request
 with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=5) as r:status=json.load(r)
 out['status']=status
 assert status['active_job'] is None and status['server_state']=='IDLE'
 assert all(status['nodes'][str(n)]['readiness']=='READY' and status['nodes'][str(n)]['reported_state']=='IDLE' for n in range(1,11))
print(json.dumps(out))
