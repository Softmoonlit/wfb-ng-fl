import collections
import json
import os
import pathlib
import select
import socket
import struct
import subprocess
import time

host = socket.gethostname()
interfaces=[]
for p in pathlib.Path('/sys/class/net').iterdir():
    driver=p/'device/driver'
    if driver.exists() and pathlib.Path(os.path.realpath(driver)).name=='rtl88xxau_wfb':interfaces.append(p.name)
assert len(interfaces)==1
iface=interfaces[0]
tun='fl-s' if host=='fl00' else 'fl-c'+str(int(host[2:]))
counts=collections.Counter()
udp=collections.Counter()
sockets={}
for interface in [iface,tun]:
    sock=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3));sock.bind((interface,0));sock.setblocking(False);sockets[sock]=interface
started=time.monotonic()
while time.monotonic()-started<15:
    readable,_,_=select.select(list(sockets),[],[],0.2)
    for sock in readable:
        data,address=sock.recvfrom(65535);interface=sockets[sock];direction='out' if address[2]==socket.PACKET_OUTGOING else 'in'
        counts[interface+'/'+direction]+=1
        if interface==iface and len(data)>32:
            length=struct.unpack_from('<H',data,2)[0]
            frame=data[length:]
            if len(frame)>16 and frame[10:12]==b'WB':counts['wfb/'+direction+'/'+frame[12:16].hex()]+=1
        elif interface==tun:
            ip=data if data and data[0]>>4==4 else data[14:]
            if len(ip)>28 and ip[0]>>4==4 and ip[9]==17:
                offset=(ip[0]&15)*4
                src,dst=struct.unpack_from('!HH',ip,offset)
                payload=ip[offset+8:]
                kind='unknown'
                try:
                    body=json.loads(payload);kind=str(body.get('type',body.get('message_type',body.get('kind','unknown'))))
                except Exception:pass
                udp[f'{direction} {socket.inet_ntoa(ip[12:16])}:{src}->{socket.inet_ntoa(ip[16:20])}:{dst} {kind}']+=1
for sock in sockets:sock.close()
logs=[]
for p in pathlib.Path('/proc').glob('[0-9]*/exe'):
    try:
        if 'wfb_v6' in os.readlink(p):
            target=os.readlink(p.parent/'fd/1')
            entry={'pid':int(p.parent.name),'stdout_path':target}
            path=pathlib.Path(target)
            if path.is_file():
                with path.open('rb') as stream:
                    stream.seek(0,2);size=stream.tell();stream.seek(max(0,size-18000));entry['tail']=stream.read().decode(errors='replace')
            logs.append(entry)
    except OSError:pass
p=subprocess.run(['iw','dev',iface,'info'],capture_output=True,text=True,timeout=5)
print(json.dumps({'host':host,'seconds':round(time.monotonic()-started,2),'counts':dict(counts),'tun_udp':dict(udp),'iw':p.stdout,'native_logs':logs},ensure_ascii=False,indent=2))
