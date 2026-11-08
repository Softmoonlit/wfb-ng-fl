import json,select,socket,struct,time,subprocess,pathlib,os,collections
host=socket.gethostname(); iface='fl-s' if host=='fl00' else 'fl-c'+str(int(host[2:])); start=time.monotonic()
def cmd(args):
 try:
  p=subprocess.run(args,capture_output=True,text=True,timeout=3);return p.stdout+p.stderr
 except Exception as e:return str(e)
def snapshot():
 logs=[]
 for p in pathlib.Path('/proc').glob('[0-9]*/exe'):
  try:
   if 'wfb_v6' not in os.readlink(p):continue
   entry={'pid':int(p.parent.name),'cmd':(p.parent/'cmdline').read_bytes().replace(b'\0',b' ').decode()}
   for fd in ['1','2']:
    target=os.readlink(p.parent/'fd'/fd);entry['fd'+fd]=target
    if pathlib.Path(target).is_file():
     with open(target,'rb') as f:f.seek(0,2);f.seek(max(0,f.tell()-45000));entry['tail'+fd]=f.read().decode(errors='replace')
   logs.append(entry)
  except OSError:pass
 return {'mono':time.monotonic(),'wall':time.time(),'iw':cmd(['iw','dev']),'threads':cmd(['ps','-eLo','pid,tid,pcpu,stat,wchan:24,comm,args']),'native':logs,'service':cmd(['systemctl','show','wfb-fl-server-daemon' if host=='fl00' else 'wfb-fl-client-daemon','-p','ExecStart','-p','MainPID','-p','ActiveState'])}
s=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3));s.bind((iface,0));s.setblocking(False)
before=snapshot(); events=[]; capture_start=time.monotonic(); until=capture_start+45
while time.monotonic()<until:
 if not select.select([s],[],[],min(.2,max(0,until-time.monotonic())))[0]:continue
 packet,addr=s.recvfrom(65535); mono=time.monotonic(); ip=packet if packet and packet[0]>>4==4 else packet[14:]
 if len(ip)<28 or ip[0]>>4!=4 or ip[9]!=17:continue
 off=(ip[0]&15)*4
 if len(ip)<off+8:continue
 sp,dp=struct.unpack_from('!HH',ip,off)
 if not ({sp,dp}&{9000,9001}):continue
 try:body=json.loads(ip[off+8:])
 except Exception:body={'raw_hex':ip[off+8:].hex()}
 events.append({'mono':mono,'delta':mono-capture_start,'direction':'out' if addr[2]==socket.PACKET_OUTGOING else 'in','src':socket.inet_ntoa(ip[12:16]),'dst':socket.inet_ntoa(ip[16:20]),'sport':sp,'dport':dp,'body':body})
s.close();after=snapshot();journal=cmd(['journalctl','-u','wfb-fl-server-daemon' if host=='fl00' else 'wfb-fl-client-daemon','--since','-65 seconds','--no-pager','-o','short-monotonic','-n','1500'])
print(json.dumps({'host':host,'interface':iface,'capture_start':capture_start,'capture_end':time.monotonic(),'before':before,'after':after,'events':events,'journal':journal},ensure_ascii=False))
