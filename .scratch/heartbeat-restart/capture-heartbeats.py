"""被动记录心跳，绑定TUN且只收取指定客户端的控制面数据报。"""
import json
import select
import socket
import struct
import sys
import time

host = socket.gethostname()
interface = 'fl-s' if host == 'fl00' else 'fl-c' + str(int(host[2:]))
clients = {'10.80.0.11', '10.80.0.15'}
sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
sock.bind((interface, 0))
sock.setblocking(False)
start = time.monotonic()
events = []
while time.monotonic() - start < 40:
    if not select.select([sock], [], [], .2)[0]:
        continue
    packet, addr = sock.recvfrom(65535)
    ip = packet if packet and packet[0] >> 4 == 4 else packet[14:]
    if len(ip) < 28 or ip[0] >> 4 != 4 or ip[9] != 17:
        continue
    offset = (ip[0] & 15) * 4
    if len(ip) < offset + 8:
        continue
    srcport, dstport = struct.unpack_from('!HH', ip, offset)
    src, dst = socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20])
    if 9001 not in (srcport, dstport) or not ({src, dst} & clients):
        continue
    try:
        body = json.loads(ip[offset + 8:])
    except (ValueError, UnicodeError):
        continue
    if body.get('type') != 'NODE_HEARTBEAT' and 'heartbeat_timestamp_ms' not in body:
        continue
    events.append({'mono': time.monotonic(), 'direction': 'out' if addr[2] == socket.PACKET_OUTGOING else 'in',
                   'src': src, 'dst': dst, 'body': body})
print(json.dumps({'host': host, 'interface': interface, 'events': events}))
