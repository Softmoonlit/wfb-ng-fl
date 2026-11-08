"""恢复后的只读观测；原样保留iw/GET_RADIO/READY，不修改状态。"""
import concurrent.futures
import datetime
import json
from pathlib import Path
import subprocess
import time
import urllib.request

OUT = Path(__file__).resolve().parent
PROBE = r'''
import datetime,json,pathlib,socket,struct,subprocess

def run(args):
 p=subprocess.run(args,text=True,capture_output=True,timeout=6)
 return {'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
unit='wfb-fl-client-daemon.service'
pid=run(['systemctl','show',unit,'-p','MainPID','--value'])['stdout'].strip()
argv=[p.decode() for p in pathlib.Path('/proc',pid,'cmdline').read_bytes().split(b'\0') if p]
native=[]
for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  args=[p.decode() for p in path.read_bytes().split(b'\0') if p]
  if args and args[0]=='/usr/bin/wfb_v6_uplink':native.append((path.parent.name,args))
 except (OSError,UnicodeError):pass
assert len(native)==1,native
native_pid,args=native[0];iface=args[args.index('--air-interface')+1];port=int(args[args.index('--radio-control-port')+1])
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pid':pid,'actualargs':argv,'native_pid':native_pid,'native_args':args,'actualiface':iface,'iw':run(['iw','dev',iface,'info'])}
try:
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
  sock.settimeout(6);sock.sendto(struct.pack('!IB',0x464c3034,4),('127.0.0.1',port));raw,address=sock.recvfrom(4096)
 values=struct.unpack('!II7B',raw)
 r['native_get_radio']={'raw_hex':raw.hex(),'address':address,'values':values,'request_ok':values[:2]==(0x464c3034,0),'short_gi':values[4],'bandwidth':values[5],'mcs':values[6]}
except Exception as exc:r['native_get_radio']={'error':repr(exc)}
started=run(['systemctl','show',unit,'-p','ActiveEnterTimestamp','--value'])['stdout'].strip()
r['journal_since_activation']=run(['journalctl','-u',unit,'--since',started,'--no-pager','-o','short-iso'])
print(json.dumps(r))
'''

for number in (1, 2):
    time.sleep(10)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        remote = pool.submit(subprocess.run,
                             ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', 'fl04',
                              'sudo -n /usr/bin/python3 -I -B -'],
                             input=PROBE, text=True, capture_output=True, timeout=25)
        status_future = pool.submit(lambda: json.load(urllib.request.urlopen(
            'http://127.0.0.1:9090/api/v1/status', timeout=6)))
        process = remote.result()
        status = status_future.result()
    observation = {'number': number, 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'fl04_probe': {'exit': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr},
                   'status': status}
    (OUT / ('fl04-observation-' + str(number) + '.json')).write_text(json.dumps(observation, indent=2) + '\n')
    print('sample', number, 'probe_exit', process.returncode,
          'readiness', {k: v['readiness'] for k, v in status['nodes'].items()}, flush=True)
    if process.returncode == 0:
        data = json.loads(process.stdout)
        print('iw', data['iw'], 'get_radio', data['native_get_radio'], flush=True)
