"""换入已指定供体后，更新两端接口绑定并核验运行参数。"""
import concurrent.futures
import json
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
for host in ['fl01', 'fl05']:
    assert json.loads((root / (host + '-donor-before.json')).read_text())['exit'] == 0
script = r'''import hashlib,json,pathlib,re,socket,struct,subprocess,time
p=pathlib.Path
host=socket.gethostname()
expected={'fl01':'fc:22:1c:30:0e:99','fl05':'fc:22:1c:50:0a:88'}
assert host in expected
unit='wfb-fl-client-daemon.service'
cards=[c for c in p('/sys/class/net').iterdir() if (c/'device/driver').exists() and (c/'device/driver').resolve().name=='rtl88xxau_wfb']
assert len(cards)==1
card=cards[0];iface=card.name;assert (card/'address').read_text().strip()==expected[host]
def run(args):return subprocess.check_output(args,text=True,timeout=30)
def native():
 entries=[]
 for f in p('/proc').glob('[0-9]*/cmdline'):
  try:
   a=f.read_bytes().split(bytes([0]))
   if a and a[0]==b'/usr/bin/wfb_v6_uplink':entries.append((f.parent,a))
  except OSError:pass
 return entries
drop=p('/etc/systemd/system')/(unit+'.d')/'zzzzz-heartbeat-release.conf'
old=drop.read_text();assert len(re.findall(r'--interface \S+',old))==1
assert '--radio-txpower-dbm 18' in old and '--channel 149' in old and '--bandwidth-mhz 40' in old
backup=p('/srv/wfb-ng-fl/card-tests/fl01-fl05-donor-test-20261106T162839Z');backup.mkdir(parents=True)
(backup/'original-dropin.conf').write_text(old)
run(['systemctl','stop',unit]);assert run(['systemctl','show',unit,'-p','MainPID','--value']).strip()=='0';assert not native()
new=re.sub(r'(--interface )\S+',lambda m:m.group(1)+iface,old)
drop.write_text(new);run(['systemctl','daemon-reload']);run(['systemctl','start',unit])
deadline=time.monotonic()+25
while True:
 entries=native()
 if len(entries)==1:break
 if time.monotonic()>=deadline:raise RuntimeError('25秒内native未就绪')
 time.sleep(.25)
proc,args=entries[0]
assert args[args.index(b'--air-interface')+1].decode()==iface
assert args[args.index(b'--node-id')+1].decode()==str(int(host[2:]))
assert b'--radio-short-gi' not in args
native_sha=hashlib.sha256((proc/'exe').read_bytes()).hexdigest();assert native_sha=='ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56'
port=int(args[args.index(b'--radio-control-port')+1])
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
 sock.settimeout(4);sock.connect(('127.0.0.1',port));sock.send(struct.pack('!IB',0x41424418,4));raw=sock.recv(4096)
assert len(raw)==15
radio=struct.unpack('!II7B',raw);assert radio[:2]==(0x41424418,0) and radio[4:7]==(0,40,4),radio
pid=int(run(['systemctl','show',unit,'-p','MainPID','--value']).strip())
loaded=json.loads(p('/srv/wfb-ng-fl/releases/heartbeat-c6659488f649-20261106T160330Z/loaded.json').read_text());assert loaded['pid']==pid and loaded['commit'].startswith('c6659488f649')
info=run(['iw','dev',iface,'info']);assert 'channel 149 ' in info and 'width: 40 MHz' in info and 'txpower -18.00 dBm' in info
cpu=[p('/sys/devices/system/cpu/cpu'+str(i)+'/cpufreq/scaling_max_freq').read_text().strip() for i in range(6)]
gpu=p('/sys/devices/platform/17000000.gpu/devfreq_dev/max_freq').read_text().strip();emc=p('/sys/kernel/nvpmodel_clk_cap/emc').read_text().strip()
assert set(cpu)=={'1497600'} and gpu=='612000000' and emc=='2133000000'
print(json.dumps({'host':host,'interface':iface,'mac':expected[host],'device_path':str((card/'device').resolve()),'daemon_pid':pid,'loaded':loaded,'native_pid':int(proc.name),'native_args':[a.decode() for a in args if a],'native_sha':native_sha,'radio_raw':raw.hex(),'short_gi':radio[4],'bandwidth':radio[5],'mcs':radio[6],'radio':info,'cpu_max':cpu,'gpu_max':gpu,'emc_cap':emc,'backup':str(backup),'old_dropin':old,'new_dropin':new},ensure_ascii=False))
'''

def apply(host):
    try:
        proc = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', host,
                               'sudo -n /usr/bin/python3 -'], input=script, text=True,
                              capture_output=True, timeout=55)
        result = {'exit': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr}
    except Exception as exc:
        result = {'exit': -1, 'error': repr(exc)}
    (root / (host + '-donor-applied.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(host, result['exit'])
    if result['exit'] != 0:
        print(result)
    return result['exit']

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(apply, ['fl01', 'fl05']))
assert results == [0, 0], '换卡启动或审计失败，不进入观察'
