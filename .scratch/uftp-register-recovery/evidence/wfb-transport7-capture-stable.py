import socket,select,struct,json,time,sys,pathlib,collections
out=pathlib.Path(sys.argv[1]);duration=int(sys.argv[2]);iface=sys.argv[3]
# 不绑定易被作业重建的TUN索引，仅被动读取命名接口；无需混杂模式或组播成员变更。
sock=socket.socket(socket.AF_PACKET,socket.SOCK_DGRAM,socket.htons(3));sock.setblocking(False)
end=time.monotonic()+duration
with out.open('w') as f:
 while time.monotonic()<end:
  if not select.select([sock],[],[],.2)[0]:continue
  b,addr=sock.recvfrom(65535)
  if addr[0]!=iface or len(b)<28 or b[0]>>4!=4 or b[9]!=17:continue
  ihl=(b[0]&15)*4
  if len(b)<ihl+8:continue
  sp,dp,length,_=struct.unpack('!HHHH',b[ihl:ihl+8]);payload=b[ihl+8:ihl+length]
  if not {sp,dp}&{1044,9000,9001}:continue
  r={'utc':time.time(),'monotonic':time.monotonic(),'interface':addr[0],'packet_type':addr[2],'src':socket.inet_ntoa(b[12:16]),'dst':socket.inet_ntoa(b[16:20]),'sport':sp,'dport':dp}
  if 1044 in [sp,dp]:
   if len(payload)<16:continue
   r.update(uftp_func=payload[1],source_id=payload[4:8].hex(),group_id=payload[8:12].hex())
   if payload[1]==9:continue
   r['payload_hex']=payload.hex()
  else:
   try:r['payload']=json.loads(payload)
   except ValueError:continue
  f.write(json.dumps(r,ensure_ascii=False)+'\n');f.flush()
sock.close()
