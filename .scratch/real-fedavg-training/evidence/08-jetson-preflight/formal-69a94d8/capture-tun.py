"""仅被动复制TUN上的既有IPv4 UDP报文；不进入promiscuous、不绑定业务端口、不发送报文。"""
import socket,struct,time,json,os,pathlib,subprocess,select,urllib.request,hashlib
host=os.uname().nodename;server=host=='fl00';iface='fl-s' if server else 'fl-c'+str(int(host[-2:]));role='server' if server else 'client'
duration=110.0;start=time.monotonic();deadline=start+duration;counter=0
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def emit(kind,**kw):
 print(json.dumps({'kind':kind,'host':host,'mono':time.monotonic(),'wall_ns':time.time_ns(),**kw},ensure_ascii=False),flush=True)
def command(args):
 try:
  r=subprocess.run(args,capture_output=True,text=True,timeout=4);return {'args':args,'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
 except Exception as e:return {'args':args,'error':repr(e)}
unit='wfb-fl-'+role+'-daemon.service'
pid=int(subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True).strip())
emit('begin',interface=iface,duration=duration,daemon_pid=pid,method='AF_PACKET SOCK_DGRAM ETH_P_ALL; no membership/promiscuous; read-only clone',current=command(['systemctl','show',unit,'-p','ExecStart','-p','DropInPaths']))
sock=socket.socket(socket.AF_PACKET,socket.SOCK_DGRAM,socket.htons(3));sock.bind((iface,0));sock.setblocking(False)
emit('capture-bound',socket_name=repr(sock.getsockname()))
next_tick=start
while time.monotonic()<deadline:
 ready,_,_=select.select([sock],[],[],min(.15,max(0,deadline-time.monotonic())))
 if ready:
  for _ in range(128):
   try:raw,addr=sock.recvfrom(65535)
   except BlockingIOError:break
   if len(raw)<28 or raw[0]>>4!=4:continue
   ihl=(raw[0]&15)*4
   if raw[9]!=17 or len(raw)<ihl+8:continue
   src=socket.inet_ntoa(raw[12:16]);dst=socket.inet_ntoa(raw[16:20]);sport,dport,length,check=struct.unpack('!HHHH',raw[ihl:ihl+8])
   if sport not in (9000,9001) and dport not in (9000,9001):continue
   payload=raw[ihl+8:ihl+max(8,length)];counter+=1
   try:body=json.loads(payload)
   except Exception:body={'unparsed_hex':payload[:128].hex(),'payload_bytes':len(payload)}
   emit('udp',capture_index=counter,packet_type=addr[2],src=src,dst=dst,sport=sport,dport=dport,ip_id=struct.unpack('!H',raw[4:6])[0],payload_sha256=hashlib.sha256(payload).hexdigest(),body=body)
 if time.monotonic()>=next_tick:
  next_tick+=5
  tasks=[]
  for t in sorted(pathlib.Path('/proc',str(pid),'task').glob('*')):
   try:tasks.append({'tid':int(t.name),'comm':(t/'comm').read_text().strip(),'wchan':(t/'wchan').read_text().strip()})
   except Exception as e:tasks.append({'tid':t.name,'error':repr(e)})
  stats={}
  for key in ['rx_packets','tx_packets','rx_bytes','tx_bytes','rx_dropped','tx_dropped','rx_errors','tx_errors']:
   f=pathlib.Path('/sys/class/net',iface,'statistics',key)
   try:stats[key]=int(f.read_text())
   except Exception as e:stats[key]=repr(e)
  log=pathlib.Path('/srv/wfb-ng-fl')/role/'wfb_uplink.log';native=command(['tail','-n','150',str(log)])
  selected=[line for line in native.get('stdout','').splitlines() if any(k in line for k in ['\tPKT\t','\tPKT_SRC\t','\tGRANT_FILTER\t','\tREADY_FILTER\t','\tTOKEN_AUTH\t','\tREASSEMBLY\t','grant_accept','first_declare','state_transition','packets lost'])]
  emit('snapshot',threads=tasks,tun_stats=stats,native_tail_selected=selected[-55:],radio=command(['iw','dev',subprocess.check_output(['bash','-c',"for i in /sys/class/net/*; do d=$(readlink -f \"$i/device/driver\"); case $d in */rtl88xxau_wfb) basename \"$i\";; esac; done"],text=True).strip(),'info']))
  if server:
   try:
    with op.open('http://127.0.0.1:9090/api/v1/status',timeout=3) as f:j=json.load(f)
    emit('api',server_state=j['server_state'],active_job=j['active_job'],nodes={k:v for k,v in j['nodes'].items() if k in ['1','2','4']})
   except Exception as e:emit('api-error',error=repr(e))
emit('end',udp_packets=counter,socket_stats=list(struct.unpack('II',sock.getsockopt(263,6,8))),final=command(['ss','-uapn']))
sock.close()
