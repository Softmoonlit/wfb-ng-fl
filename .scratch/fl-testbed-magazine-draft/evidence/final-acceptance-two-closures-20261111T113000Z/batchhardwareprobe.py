#!/usr/bin/env python3
"""Batch-only raw hardware guard around the reviewed communication runner."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
TOOL = Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/communication')
HOSTS = ['fl00', 'fl01', 'fl02', 'fl03', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09']
NODES = [1, 2, 3, 5, 6, 7, 8, 9]
MIN_STORAGE = 512 * 1024 * 1024
MODEL_SIZE = 44_770_302
MODEL_SHA = '4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'
SERVER_EXPECTED = dict(interface='wlxfc221c100119', mac='fc:22:1c:10:01:19',
                       driver='rtl88xxau_wfb', usb_path='2-1.1:1.0', usb_speed='5000')

REMOTE_PROBE = r'''import hashlib,json,pathlib,socket,struct,subprocess,os
host=socket.gethostname(); role='server' if host=='fl00' else 'client'
unit='wfb-fl-'+role+'-daemon.service'
def run(argv):
 p=subprocess.run(argv,capture_output=True,text=True,timeout=8)
 return {'argv':argv,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
service=run(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID'])
items=[]
for proc in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  argv=proc.read_bytes().decode().rstrip(chr(0)).split(chr(0))
  if not argv or argv[0]!='/usr/bin/wfb_v6_uplink': continue
  iface=argv[argv.index('--air-interface')+1]; dev=pathlib.Path('/sys/class/net')/iface
  usbdev=(dev/'device').resolve(); usb_path=usbdev.name
  speed=next((p/'speed' for p in [usbdev,*usbdev.parents] if (p/'speed').exists()),None)
  port=int(argv[argv.index('--radio-control-port')+1]); queries={}
  for code,fmt,key in [(4,'!II7B','radio'),(3,'!II2B','fec')]:
   with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
    s.settimeout(2); s.sendto(struct.pack('!IB',0x434f4d4d,code),('127.0.0.1',port)); raw,peer=s.recvfrom(4096)
    queries[key]=list(struct.unpack(fmt,raw))
  items.append(dict(pid=proc.parent.name,argv=argv,interface=iface,mac=(dev/'address').read_text().strip(),driver=(dev/'device/driver').resolve().name,usb_path=usb_path,usb_speed=speed.read_text().strip() if speed else None,native_sha=hashlib.sha256((proc.parent/'exe').read_bytes()).hexdigest(),queries=queries,iw=run(['iw','dev',iface,'info'])))
 except Exception as exc: items.append({'probe_error':repr(exc)})
st=os.statvfs('/srv/wfb-ng-fl')
print(json.dumps({'host':host,'role':role,'service':service,'native':items,'storage':{'available_bytes':st.f_bavail*st.f_frsize,'available_inodes':st.f_favail},'management':run(['ip','-j','addr','show','dev','enP8p1s0']) if host=='fl00' else None},ensure_ascii=False))
'''

def write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def probe(host):
    command = ['sudo', '-n', '/usr/bin/python3', '-I', '-c', REMOTE_PROBE]
    argv = command if host == 'fl00' else [
        'ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=5', host, shlex.join(command)]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=18)
        parsed = None
        if result.returncode == 0:
            parsed = json.loads(result.stdout)
        return dict(host=host, command=argv, returncode=result.returncode,
                    stdout=result.stdout, stderr=result.stderr, parsed=parsed)
    except Exception as exc:
        return dict(host=host, command=argv, error=repr(exc))


def expected_for(host, hardware):
    if host == 'fl00':
        return SERVER_EXPECTED
    node = hardware['nodes'][host]
    return dict(interface=node['interface'], mac=node['mac'], driver=node['driver'],
                usb_path=node['usb_path'], usb_speed=node['usb_speed'])


def validate(phase, rows, hardware, require_model_capacity=False):
    errors = []
    for row in rows:
        host = row['host']
        if row.get('returncode') != 0 or row.get('parsed') is None:
            errors.append(host + ': management/raw probe failed')
            continue
        data = row['parsed']
        native = data.get('native', [])
        expected = expected_for(host, hardware)
        expected['usb_speed'] = str(expected['usb_speed']).removesuffix('M')
        if len(native) != 1:
            errors.append(host + ': native count != 1')
            continue
        item = native[0]
        for key in ('interface', 'mac', 'driver', 'usb_path', 'usb_speed'):
            if item.get(key) != expected[key]:
                errors.append('%s: %s expected=%r actual=%r' %
                              (host, key, expected[key], item.get(key)))
        if item.get('native_sha') != hashlib.sha256(Path('/usr/bin/wfb_v6_uplink').read_bytes()).hexdigest():
            errors.append(host + ': proc native SHA differs from local expected binary')
        if 'ActiveState=active' not in data.get('service', {}).get('stdout', ''):
            errors.append(host + ': daemon inactive')
        if phase == 'pre' and data.get('storage', {}).get('available_bytes', 0) < MIN_STORAGE:
            errors.append(host + ': storage below hard minimum')
        if require_model_capacity and data.get('storage', {}).get('available_bytes', 0) < MIN_STORAGE + 3 * MODEL_SIZE:
            errors.append(host + ': storage lacks three-model-copy reserve')
    return errors


def remote_absent(run_id):
    def one(host):
        command = ['test', '!', '-e', '/srv/wfb-ng-fl/comm-' + run_id]
        argv = command if host == 'fl00' else ['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=5', host, shlex.join(command)]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
            return dict(host=host, command=argv, returncode=result.returncode,
                        stdout=result.stdout, stderr=result.stderr)
        except Exception as exc:
            return dict(host=host, command=argv, error=repr(exc))
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
        return list(pool.map(one, HOSTS))


def get_run_id(args):
    for index, value in enumerate(args):
        if value == '--run-id' and index + 1 < len(args):
            run_id = args[index + 1]
            if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id):
                return run_id
    raise SystemExit('batch executor requires runner --run-id')


def main():
    separator = sys.argv.index('--') if '--' in sys.argv else -1
    if separator < 0 or not sys.argv[separator + 1:]:
        raise SystemExit('usage: batchhardwareprobe.py -- <reviewed runner command>')
    runner_args = sys.argv[separator + 1:]
    if '--execute' not in runner_args:
        raise SystemExit('batch executor requires runner --execute')
    run_id = get_run_id(runner_args)
    hardware = json.loads((ROOT / 'hardware-from-actual.json').read_text())
    write(ROOT / 'executor-process.json', dict(pid=os.getpid(), command=[str(TOOL / 'runner.py'), *runner_args],
                                                run_id=run_id, started_wall=time.time()))
    write(ROOT / 'run-id-preflight.json', dict(run_id=run_id, remote=remote_absent(run_id), wall=time.time()))
    remote = json.loads((ROOT / 'run-id-preflight.json').read_text())['remote']
    if any(row.get('returncode') != 0 for row in remote):
        raise SystemExit('run ID already exists or management check failed')
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
        pre = list(pool.map(probe, HOSTS))
    pre_result = dict(phase='pre', wall=time.time(), rows=pre,
                      errors=validate('pre', pre, hardware,
                                      require_model_capacity=('--payload' in runner_args)))
    write(ROOT / 'raw-inventory-pre.json', pre_result)
    if pre_result['errors']:
        raise SystemExit('pre-transfer hardware gate failed: ' + repr(pre_result['errors']))
    runner_log = (ROOT / 'batch-runner.log').open('a', buffering=1)
    command = [sys.executable, str(TOOL / 'runner.py'), *runner_args]
    child = subprocess.Popen(command, stdout=runner_log, stderr=subprocess.STDOUT, text=True)
    write(ROOT / 'runner-process.json', dict(pid=child.pid, command=command, started_wall=time.time()))
    returncode = child.wait()
    runner_log.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
        post = list(pool.map(probe, HOSTS))
    post_result = dict(phase='post', wall=time.time(), runner_returncode=returncode, rows=post,
                       errors=validate('post', post, hardware))
    write(ROOT / 'raw-inventory-post.json', post_result)
    write(ROOT / 'executor-result.json', dict(run_id=run_id, runner_returncode=returncode,
                                              post_errors=post_result['errors'], wall=time.time()))
    return returncode if not post_result['errors'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
