import json,socket,select,struct,time
host=socket.gethostname(); interface='fl-s' if host=='fl00' else 'fl-c1'
sock=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3));sock.bind((interface,0));sock.setblocking(False)
start=time.monotonic();events=[]
while time.monotonic()-start<40:
 if not select.select([sock],[],[],.2)[0]:continue
 data,addr=sock.recvfrom(65535)
 ip=data if data and data[0]>>4==4 else data[14:]
 if len(ip)<28 or ip[0]>>4!=4 or ip[9]!=17:continue
 offset=(ip[0]&15)*4;src,dst=struct.unpack_from('!HH',ip,offset)
 if 9001 not in (src,dst):continue
 if host=='fl00' and socket.inet_ntoa(ip[12:16])!='10.80.0.11' and socket.inet_ntoa(ip[16:20])!='10.80.0.11':continue
 try:body=json.loads(ip[offset+8:])
 except Exception:continue
 events.append({'mono':time.monotonic(),'elapsed':time.monotonic()-start,'direction':'out' if addr[2]==socket.PACKET_OUTGOING else 'in','src':socket.inet_ntoa(ip[12:16]),'dst':socket.inet_ntoa(ip[16:20]),'body':body})
print(json.dumps({'host':host,'events':events}))
