"""按各机单调时间和进程身份核验待命窗口后的日志与射频。"""
import concurrent.futures
import json
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
script = r'''import hashlib,json,pathlib,socket,struct,subprocess,sys,time
expected_pid,expected_boot,marker=int(sys.argv[1]),sys.argv[2],float(sys.argv[3])
p=pathlib.Path
unit='wfb-fl-client-daemon.service'
def run(args):return subprocess.check_output(args,text=True,timeout=15)
pid=int(run(['systemctl','show',unit,'-p','MainPID','--value']).strip())
boot=p('/proc/sys/kernel/random/boot_id').read_text().strip()
assert pid==expected_pid and boot==expected_boot,(pid,boot)
logs=[json.loads(s) for s in run(['journalctl','-b','-u',unit,'_PID='+str(pid),'--no-pager','-o','json']).splitlines()]
recent=[x for x in logs if int(x['__MONOTONIC_TIMESTAMP'])/1e6>=marker]
hunting=[x for x in recent if '十秒内未确认有效心跳' in str(x.get('MESSAGE',''))]
warnings=[x for x in recent if int(x.get('PRIORITY',6))<=4]
natives=[]
for f in p('/proc').glob('[0-9]*/cmdline'):
 try:
  args=f.read_bytes().split(bytes([0]))
  if args and args[0]==b'/usr/bin/wfb_v6_uplink':natives.append((f.parent,args))
 except OSError:pass
assert len(natives)==1
proc,args=natives[0]
iface=args[args.index(b'--air-interface')+1].decode();card=p('/sys/class/net')/iface
assert (card/'device/driver').resolve().name=='rtl88xxau_wfb'
port=int(args[args.index(b'--radio-control-port')+1])
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
 sock.settimeout(4);sock.connect(('127.0.0.1',port));sock.send(struct.pack('!IB',0x54414918,4));raw=sock.recv(4096)
assert len(raw)==15
radio=struct.unpack('!II7B',raw);assert radio[:2]==(0x54414918,0) and radio[4:6]==(0,40)
info=run(['iw','dev',iface,'info'])
assert 'channel 149 ' in info and 'width: 40 MHz' in info and 'txpower -18.00 dBm' in info
print(json.dumps({'host':socket.gethostname(),'pid':pid,'boot_id':boot,'mono_start':marker,'mono_end':time.monotonic(),'hunting_warnings':hunting,'warnings':warnings,'journal_rows_after_marker':len(recent),'journal_all_current_pid':logs,'native_pid':int(proc.name),'native_sha':hashlib.sha256((proc/'exe').read_bytes()).hexdigest(),'interface':iface,'mac':(card/'address').read_text().strip(),'radio_raw':raw.hex(),'short_gi':radio[4],'bandwidth':radio[5],'radio':info},ensure_ascii=False))
'''

def collect(host):
    before = json.loads(json.loads((root / (host + '-journal-start.json')).read_text())['stdout'])
    args = f"{before['pid']} {before['boot_id']} {before['mono_marker']}"
    try:
        proc = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', host,
                               'sudo -n /usr/bin/python3 - ' + args], input=script, text=True,
                              capture_output=True, timeout=25)
        result = {'exit': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr}
    except Exception as exc:
        result = {'exit': -1, 'error': repr(exc)}
    (root / (host + '-journal-final.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    if result['exit'] == 0:
        row = json.loads(result['stdout'])
        print(host, 'PID', row['pid'], '新增寻频告警', len(row['hunting_warnings']), '其他告警', len(row['warnings']))
    else:
        print(host, result)
    return result['exit']

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(collect, ['fl01', 'fl02', 'fl04', 'fl05']))
assert results == [0, 0, 0, 0], '末尾核验有失败，不能认定整个窗口通过'
