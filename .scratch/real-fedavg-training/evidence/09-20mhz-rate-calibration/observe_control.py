#!/usr/bin/env python3
"""09被动诊断：仅监听本机lo和现有TUN的UDP控制报文，不发送。"""
import socket,select,struct,json,time,sys,pathlib
out=pathlib.Path(sys.argv[1]);duration=int(sys.argv[2]);socks=[]
for name in ['lo','fl-s']:
 s=socket.socket(socket.AF_PACKET,socket.SOCK_DGRAM,socket.htons(0x0800));s.bind((name,0));socks.append(s)
end=time.monotonic()+duration
with out.open('a') as f:
 while time.monotonic()<end:
  for s in select.select(socks,[],[],1)[0]:
   b,addr=s.recvfrom(65535)
   if len(b)<28 or b[0]>>4!=4 or b[9]!=17:continue
   ihl=(b[0]&15)*4
   if len(b)<ihl+8:continue
   sp,dp,length,_=struct.unpack('!HHHH',b[ihl:ihl+8])
   if not ({sp,dp}&{9000,9001,9200}):continue
   payload=b[ihl+8:ihl+length]
   r={'utc':time.time(),'interface':addr[0],'src':socket.inet_ntoa(b[12:16]),'dst':socket.inet_ntoa(b[16:20]),'sport':sp,'dport':dp}
   try:r['payload']=json.loads(payload)
   except Exception:r['payload_hex']=payload.hex()
   f.write(json.dumps(r,ensure_ascii=False)+'\n');f.flush()
