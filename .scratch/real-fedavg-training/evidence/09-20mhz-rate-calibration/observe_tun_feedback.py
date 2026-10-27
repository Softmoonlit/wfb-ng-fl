#!/usr/bin/env python3
"""09被动观测既有TUN反馈及本机控制口；FILESEG仅计数，不存文件内容。"""
import socket,select,struct,json,time,sys,pathlib,collections,os
out=pathlib.Path(sys.argv[1]);duration=int(sys.argv[2]);iface=sys.argv[3];out.parent.mkdir(parents=True,exist_ok=True);socks=[]
for name in ['lo',iface]:
 s=socket.socket(socket.AF_PACKET,socket.SOCK_DGRAM,socket.htons(0x0800));s.bind((name,0));socks.append(s)
end=time.monotonic()+duration;counts=collections.Counter();last=time.monotonic()
with out.open('a') as f:
 while time.monotonic()<end:
  for s in select.select(socks,[],[],.3)[0]:
   b,addr=s.recvfrom(65535)
   if len(b)<28 or b[0]>>4!=4 or b[9]!=17:continue
   ihl=(b[0]&15)*4
   if len(b)<ihl+8:continue
   sp,dp,length,_=struct.unpack('!HHHH',b[ihl:ihl+8])
   if not ({sp,dp}&{9000,9001,9200,1144}):continue
   payload=b[ihl+8:ihl+length]
   r={'utc':time.time(),'monotonic':time.monotonic(),'interface':addr[0],'packet_type':addr[2],'src':socket.inet_ntoa(b[12:16]),'dst':socket.inet_ntoa(b[16:20]),'sport':sp,'dport':dp,'udp_payload_bytes':len(payload)}
   if 1144 in [sp,dp]:
    func=payload[1] if len(payload)>1 else None;r['uftp_func']=func
    if len(payload)>=8:r['group_id_hex']=payload[4:8].hex()
    counts[(addr[0],addr[2],r['src'],r['dst'],func)]+=1
    if func==9:continue
    r['payload_hex']=payload.hex()
   elif 9200 in [sp,dp]:
    r['payload_hex']=payload.hex()
    if len(payload)==12:
     v=struct.unpack('!IB7B',payload);r['radio_request']={'req_id':v[0],'cmd':v[1],'bandwidth':v[5],'mcs':v[6]}
   else:
    try:r['payload']=json.loads(payload)
    except Exception:r['payload_hex']=payload.hex()
   f.write(json.dumps(r,ensure_ascii=False)+'\n');f.flush()
  if time.monotonic()-last>=1:
   f.write(json.dumps({'utc':time.time(),'packet_counts':[{'key':list(k),'count':v}for k,v in counts.items()]})+'\n');f.flush();counts.clear();last=time.monotonic()
 f.write(json.dumps({'utc':time.time(),'observer_finished':True,'remaining_counts':[{'key':list(k),'count':v}for k,v in counts.items()]})+'\n')
