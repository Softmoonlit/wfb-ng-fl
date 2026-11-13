#!/usr/bin/env python3
"""总 owner：fl02 精确缓存 hold、1915Z worker 子进程监督和 finally 恢复。"""
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
CACHE_ROOT = ROOT / 'cache-maintenance'
WORKER = ROOT / 'worker.py'
PHASE_SECONDS = 4500
EXPECTED_BOOT_ID = '8831475d-7f89-458a-bfe5-e59bf8843eb4'
FL10_MAC = 'fc:22:1c:10:01:19'
NODES = [1, 2, 3, 5, 6, 7, 8, 9, 10]
NATIVE_SHA = '44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2'
COMMIT = '1cd6264d5d6be660998ce60f3a349063009ecfa7'
RELEASE = '/srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261109T083151Z'
CACHE_PATHS = ['/var/cache/apt/pkgcache.bin', '/var/cache/apt/srcpkgcache.bin']
LOCK_PATHS = ['/var/lib/dpkg/lock-frontend', '/var/lib/dpkg/lock',
              '/var/lib/apt/lists/lock', '/var/cache/apt/archives/lock']

# fl10 断电后的启动准入必须先通过，且总等待上限为 180 秒。
FL10_PROBE = r'''import hashlib,json,pathlib,socket,struct,subprocess
MAC='fc:22:1c:10:01:19';SHA='44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2';COMMIT='1cd6264d5d6be660998ce60f3a349063009ecfa7';RELEASE='/srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261109T083151Z'
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as stream:
  for block in iter(lambda:stream.read(1048576),b''):h.update(block)
 return h.hexdigest()
def option(argv,key):return argv[argv.index(key)+1]
iface=next(p.name for p in pathlib.Path('/sys/class/net').iterdir() if (p/'address').exists() and (p/'address').read_text().strip().lower()==MAC)
dev=pathlib.Path('/sys/class/net')/iface;usb=next(q for q in [dev/'device',*(dev/'device').resolve().parents] if (q/'idVendor').exists())
procs=[]
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  argv=p.read_bytes().decode().rstrip(chr(0)).split(chr(0))
  if argv and argv[0]=='/usr/bin/wfb_v6_uplink':procs.append(argv)
 except (OSError,UnicodeError):pass
native=procs[0] if len(procs)==1 else None;queries={}
if native:
 port=int(option(native,'--radio-control-port'))
 for cmd,fmt,key in [(3,'!II2B','fec'),(4,'!II7B','radio')]:
  with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
   sock.settimeout(3);sock.sendto(struct.pack('!IB',0x434f4d4d,cmd),('127.0.0.1',port));raw,peer=sock.recvfrom(4096);queries[key]=list(struct.unpack(fmt,raw))
iw=subprocess.check_output(['iw','dev',iface,'info'],text=True);manifest=json.loads((pathlib.Path(RELEASE)/'manifest.json').read_text())
print(json.dumps(dict(host=socket.gethostname(),boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),iface=iface,mac=(dev/'address').read_text().strip(),driver=(dev/'device/driver').resolve().name,usb_speed=(usb/'speed').read_text().strip(),native=native,native_sha=sha('/usr/bin/wfb_v6_uplink'),source_native_sha=sha(str(pathlib.Path(RELEASE)/'source/wfb_v6_uplink')),manifest_commit=manifest['commit'],manifest_native_sha=manifest['native_sha'],queries=queries,iw=iw,power=subprocess.check_output(['nvpmodel','-q'],text=True),daemon=subprocess.run(['systemctl','is-active','wfb-fl-client-daemon.service'],capture_output=True,text=True).stdout.strip())))
'''


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def append_jsonl(path, value):
    with Path(path).open('a', buffering=1) as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + '\n')


# stop 后所有探针都走 systemctl/proc/statvfs，不再调用会触发 PackageKit D-Bus 激活的方法。
PROBE = r'''import fcntl,json,os,pathlib,shlex,stat,subprocess,time
CACHE_PATHS=['/var/cache/apt/pkgcache.bin','/var/cache/apt/srcpkgcache.bin']
LOCK_PATHS=['/var/lib/dpkg/lock-frontend','/var/lib/dpkg/lock','/var/lib/apt/lists/lock','/var/cache/apt/archives/lock']
QUERY=bool(QUERY)
def run(argv):
 p=subprocess.run(argv,capture_output=True,text=True,timeout=5)
 return dict(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
def metadata(path):
 try:
  s=os.lstat(path);q=run(['findmnt','--noheadings','--output','TARGET','--target',path])
  targets=[x.strip() for x in q['stdout'].splitlines() if x.strip()]
  return dict(path=path,exists=True,type='regular' if stat.S_ISREG(s.st_mode) else 'other',
   is_symlink=stat.S_ISLNK(s.st_mode),is_mount=os.path.normpath(path) in [os.path.normpath(x) for x in targets],
   mount_targets=targets,size_bytes=s.st_size,blocks=s.st_blocks,inode=s.st_ino,device=s.st_dev,
   mtime_ns=s.st_mtime_ns,mode=oct(s.st_mode & 0o7777),nlink=s.st_nlink)
 except FileNotFoundError:return dict(path=path,exists=False)
 except OSError as exc:return dict(path=path,error=repr(exc))
def processes():
 apt=[];packagekit=[];names={'apt','apt-get','dpkg','unattended-upgrade'}
 for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
  try:
   argv=p.read_bytes().decode(errors='replace').rstrip(chr(0)).split(chr(0)); base={pathlib.Path(x).name for x in argv if x}
   row=dict(pid=p.parent.name,argv=argv)
   if base & names:apt.append(row)
   if 'packagekitd' in base:packagekit.append(row)
  except (OSError,UnicodeError):pass
 return apt,packagekit
def locks():
 out={}
 for path in LOCK_PATHS:
  row=dict(path=path)
  try:
   fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC);row['exists']=True
   try:
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);row['held']=False;fcntl.flock(fd,fcntl.LOCK_UN)
   except BlockingIOError:row['held']=True
   finally:os.close(fd)
  except FileNotFoundError:row.update(exists=False,held=False)
  except OSError as exc:row.update(held=None,error=repr(exc))
  out[path]=row
 return out
def service():
 q=run(['systemctl','show','packagekit.service','-p','ActiveState','-p','UnitFileState','-p','MainPID']);v={}
 for line in q['stdout'].splitlines():
  if '=' in line:k,x=line.split('=',1);v[k]=x
 return dict(active=v.get('ActiveState'),enabled=v.get('UnitFileState'),main_pid=v.get('MainPID'),command=q)
apt,packagekit=processes();s=os.statvfs('/srv/wfb-ng-fl')
out=dict(host=os.uname().nodename,boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 service=service(),apt_processes=apt,packagekit_processes=packagekit,locks=locks(),
 files={p:metadata(p) for p in CACHE_PATHS},free=dict(available_bytes=s.f_bavail*s.f_frsize,free_inodes=s.f_favail),wall=time.time())
if QUERY:
 q=run(['busctl','--system','call','org.freedesktop.PackageKit','/org/freedesktop/PackageKit','org.freedesktop.PackageKit','GetTransactionList'])
 t=shlex.split(q['stdout']);tx=dict(command=q,raw_stdout=q['stdout'],raw_stderr=q['stderr'],valid=False,count=None,paths=[])
 try:
  if q['returncode']==0 and len(t)>=2 and t[0]=='ao':tx.update(count=int(t[1]),paths=t[2:],valid=len(t[2:])==int(t[1]))
 except (ValueError,IndexError):pass
 out['transaction']=tx
print(json.dumps(out))
'''


def remote(code, timeout=25, host='fl02'):
    argv = ['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=4', host,
            shlex.join(['sudo', '-n', 'python3', '-c', code])]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=min(timeout, 30))
        value = dict(host=host, returncode=result.returncode, stdout=result.stdout,
                     stderr=result.stderr)
        if result.returncode == 0:
            value['result'] = json.loads(result.stdout)
        return value
    except Exception as exc:
        return dict(host='fl02', error=repr(exc))


def probe(query, label):
    result = remote('QUERY=' + ('True' if query else 'False') + '\n' + PROBE, 15)
    if result.get('returncode') != 0:
        raise RuntimeError('fl02 probe failed: ' + repr(result))
    value = result['result']; value['label'] = label; value['local_wall'] = time.time()
    return value


def stop_packagekit():
    code = "import json,subprocess\np=subprocess.run(['systemctl','stop','packagekit.service'],capture_output=True,text=True,timeout=15)\nprint(json.dumps(dict(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)))"
    return remote(code, 20)


def exact_delete(expected):
    code = 'EXPECTED=' + repr(expected) + '\n' + r'''import json,os,stat,subprocess
def mounted(path):
 q=subprocess.run(['findmnt','--noheadings','--output','TARGET','--target',path],capture_output=True,text=True,timeout=5)
 return os.path.normpath(path) in [os.path.normpath(x.strip()) for x in q.stdout.splitlines() if x.strip()]
def meta(path):
 s=os.lstat(path)
 return dict(path=path,type='regular' if stat.S_ISREG(s.st_mode) else 'other',is_symlink=stat.S_ISLNK(s.st_mode),
  size_bytes=s.st_size,blocks=s.st_blocks,inode=s.st_ino,device=s.st_dev,mtime_ns=s.st_mtime_ns,
  mode=oct(s.st_mode & 0o7777),nlink=s.st_nlink)
rows=[]
for path,want in EXPECTED.items():
 pre=meta(path)
 if pre['type']!='regular' or pre['is_symlink'] or mounted(path):raise RuntimeError('cache changed type/symlink/mount: '+path)
 for key in ['inode','device','mtime_ns','size_bytes','mode','blocks']:
  if pre[key]!=want[key]:raise RuntimeError('pre-delete lstat mismatch '+path+' '+key)
 os.unlink(path);rows.append(dict(path=path,predelete=pre,exact_unlink=True))
print(json.dumps(dict(rows=rows)))
'''
    return remote(code, 20)


def restore_packagekit(initial):
    active = initial.get('active') == 'active'
    enabled = initial.get('enabled')
    code = 'EXPECTED_ACTIVE=' + repr(active) + '\nEXPECTED_ENABLED=' + repr(enabled) + '\n' + r'''import json,subprocess
action='start' if EXPECTED_ACTIVE else 'stop'
p=subprocess.run(['systemctl',action,'packagekit.service'],capture_output=True,text=True,timeout=15)
s=subprocess.run(['systemctl','show','packagekit.service','-p','ActiveState','-p','UnitFileState','-p','MainPID'],capture_output=True,text=True,timeout=5)
v={}
for line in s.stdout.splitlines():
 if '=' in line:k,x=line.split('=',1);v[k]=x
print(json.dumps(dict(action=action,action_returncode=p.returncode,action_stderr=p.stderr,active=v.get('ActiveState'),enabled=v.get('UnitFileState'),main_pid=v.get('MainPID'),active_ok=v.get('ActiveState')==('active' if EXPECTED_ACTIVE else 'inactive'),enabled_ok=v.get('UnitFileState')==EXPECTED_ENABLED)))
'''
    result = remote(code, 25)
    if result.get('returncode') == 0:
        value = result.get('result', {})
        result['ok'] = value.get('action_returncode') == 0 and value.get('active_ok') and value.get('enabled_ok')
    else:
        result['ok'] = False
    write(CACHE_ROOT / 'finally-packagekit-restore.json', result)
    return result


def startup_fl10_check():
    started = time.monotonic(); deadline = started + 180; attempts = []
    while time.monotonic() < deadline:
        remaining = max(1, min(25, deadline - time.monotonic()))
        remote_result = remote(FL10_PROBE, remaining, host='fl10')
        data = remote_result.get('result', {}) if remote_result.get('returncode') == 0 else {}
        try:
            with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status', timeout=5) as response:
                status = json.load(response)
        except Exception as exc:
            status = {'error': repr(exc)}
        online = sorted(int(k) for k, v in status.get('nodes', {}).items()
                        if v.get('readiness') != 'OFFLINE') if 'nodes' in status else []
        bad = [node for node in NODES if any(status.get('nodes', {}).get(str(node), {}).get(key) != value
                                             for key, value in [('readiness', 'READY'), ('reported_state', 'IDLE'),
                                                                ('current_channel', 149)])]
        native = data.get('native')
        argv = native or []
        native_ok = False
        if isinstance(native, list) and native and native[0] == '/usr/bin/wfb_v6_uplink':
            try:
                native_ok = (data.get('native_sha') == NATIVE_SHA
                              and data.get('source_native_sha') == NATIVE_SHA
                              and data.get('manifest_commit') == COMMIT
                              and data.get('manifest_native_sha') == NATIVE_SHA
                              and data.get('mac') == FL10_MAC and data.get('driver') == 'rtl88xxau_wfb'
                              and data.get('usb_speed') == '5000'
                              and data.get('power', '').strip() == 'NV Power Mode: 15W\n0'
                              and argv[argv.index('--fec-k') + 1] == '8'
                              and argv[argv.index('--fec-n') + 1] == '12'
                              and argv[argv.index('--radio-bandwidth') + 1] == '40'
                              and argv[argv.index('--radio-mcs-index') + 1] == '4')
            except (ValueError, IndexError):
                native_ok = False
        radio_ok = data.get('queries', {}).get('fec') == [0x434f4d4d, 0, 8, 12] and data.get('queries', {}).get('radio') == [0x434f4d4d, 0, 0, 0, 0, 40, 4, 0, 1]
        iw_ok = all(text in data.get('iw', '') for text in ['channel 149 ', 'width: 40 MHz', 'type monitor', 'txpower -18.00 dBm'])
        check = subprocess.run([sys.executable, str(Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/communication/check-all-ready.py'))],
                               capture_output=True, text=True, timeout=10)
        registry_ok = (status.get('server_state') == 'IDLE' and status.get('active_job') is None
                       and online == NODES and not bad)
        passed = remote_result.get('returncode') == 0 and native_ok and radio_ok and iw_ok \
                 and data.get('daemon') == 'active' and registry_ok and check.returncode == 0
        attempt = dict(wall=time.time(), remote=remote_result, data=data, status=status,
                       online=online, bad_nodes=bad, native_ok=native_ok, radio_ok=radio_ok,
                       iw_ok=iw_ok, registry_ok=registry_ok, check_all_ready=check.returncode,
                       passed=passed)
        attempts.append(attempt)
        write(ROOT / 'fl10-startup-check.json', dict(host='fl10', deadline_seconds=180,
                                                     passed=passed, attempts=attempts))
        if passed:
            return attempt
        if time.monotonic() < deadline:
            time.sleep(min(5, deadline - time.monotonic()))
    raise TimeoutError('fl10 power-restart startup check exceeded 180 seconds')


def request_stop(child, reason, details=None):
    payload = dict(reason=reason, wall=time.time(), pid=os.getpid(), **(details or {}))
    if not (ROOT / 'STOP').exists():
        write(ROOT / 'STOP', payload)
    if child.poll() is None:
        child.send_signal(signal.SIGINT)
    return payload


def main():
    CACHE_ROOT.mkdir(exist_ok=True)
    start_wall = time.time(); start_mono = time.monotonic(); deadline = start_mono + PHASE_SECONDS
    owner = dict(owner='cache-hold-wrapper', pid=os.getpid(), root=str(ROOT), host='fl00',
                 started_wall=start_wall, deadline_monotonic=deadline, phase_budget_seconds=PHASE_SECONDS,
                 stop_file=str(ROOT / 'STOP'), child_worker=str(WORKER), child_run_ids=[],
                 expected_fl02_boot_id=EXPECTED_BOOT_ID)
    write(ROOT / 'owner-wrapper.json', owner)
    write(ROOT / 'checkpoint.json', dict(stage='fl10-power-restart-startup-check', pid=os.getpid(),
                                         wall=start_wall, monotonic=start_mono, deadline_monotonic=deadline))
    initial = None; hold = False; child = None; hold_failure = None; child_returncode = None
    try:
        startup = startup_fl10_check()
        write(ROOT / 'power-event-checkpoint.json', dict(stage='fl10-power-interruption-before-execute',
            status='confirmed-infrastructure-power-event', host='fl10', confirmed_by_user=True,
            parameter_failure=False, boot_id=startup['data'].get('boot_id'), model_transfer_started=False,
            model_files_sent=False, execute_count=0, prior_trial_reuse=False,
            startup_check_deadline_seconds=180, startup_check=startup, wall=time.time()))
        start_wall = time.time(); start_mono = time.monotonic(); deadline = start_mono + PHASE_SECONDS
        owner.update(started_wall=start_wall, deadline_monotonic=deadline)
        write(ROOT / 'owner-wrapper.json', owner)
        write(ROOT / 'checkpoint.json', dict(stage='before-fl02-readonly-preflight', pid=os.getpid(),
                                             wall=start_wall, monotonic=start_mono, deadline_monotonic=deadline,
                                             fl10_boot_id=startup['data'].get('boot_id')))
        initial = probe(True, 'before-stop-readonly')
        write(CACHE_ROOT / 'preflight.json', initial)
        tx = initial.get('transaction', {})
        if not tx.get('valid') or tx.get('count') != 0:
            raise RuntimeError('PackageKit GetTransactionList guard failed: ' + repr(tx))
        if initial.get('boot_id') != EXPECTED_BOOT_ID:
            write(CACHE_ROOT / 'boot-change-requires-fresh-audit.json', dict(expected=EXPECTED_BOOT_ID,
                actual=initial.get('boot_id'), full_audit_required=True, wall=time.time()))
        if initial.get('apt_processes') or any(x.get('held') is not False for x in initial.get('locks', {}).values()):
            raise RuntimeError('apt/dpkg/unattended-upgrade process or lock present')
        if initial.get('service', {}).get('active') != 'active':
            raise RuntimeError('PackageKit initial active state is not active')
        for path in CACHE_PATHS:
            meta = initial.get('files', {}).get(path, {})
            if meta.get('type') != 'regular' or meta.get('is_symlink') or meta.get('is_mount') or meta.get('exists') is False:
                raise RuntimeError('cache is not exact regular non-symlink non-mount file: ' + repr(meta))
        hold = True
        stopped = stop_packagekit(); write(CACHE_ROOT / 'stop-command.json', stopped)
        if stopped.get('returncode') != 0 or stopped.get('result', {}).get('returncode') != 0:
            raise RuntimeError('systemctl stop packagekit.service failed: ' + repr(stopped))
        after_stop = probe(False, 'after-stop-no-dbus'); write(CACHE_ROOT / 'after-stop.json', after_stop)
        if after_stop.get('service', {}).get('active') != 'inactive' or after_stop.get('packagekit_processes'):
            raise RuntimeError('PackageKit did not remain inactive after stop')
        if after_stop.get('apt_processes') or any(x.get('held') is not False for x in after_stop.get('locks', {}).values()):
            raise RuntimeError('apt/dpkg process or lock appeared after stop')
        expected = {path: initial['files'][path] for path in CACHE_PATHS}
        deleted = exact_delete(expected); write(CACHE_ROOT / 'exact-unlink.json', deleted)
        if deleted.get('returncode') != 0:
            raise RuntimeError('exact cache unlink failed: ' + repr(deleted))
        after = probe(False, 'after-exact-unlink-hold'); write(CACHE_ROOT / 'after-unlink.json', after)
        if after.get('service', {}).get('active') != 'inactive' or after.get('packagekit_processes'):
            raise RuntimeError('PackageKit not held inactive after unlink')
        if any(after.get('files', {}).get(path, {}).get('exists') is not False for path in CACHE_PATHS):
            raise RuntimeError('exact cache still exists after unlink')
        write(CACHE_ROOT / 'cache-maintenance-report.json', dict(ok=True, host='fl02',
            boot_id=initial.get('boot_id'), boot_id_changed=initial.get('boot_id') != EXPECTED_BOOT_ID,
            initial_service=initial.get('service'), stop_service=stopped, after_stop=after_stop,
            files_before=initial.get('files'), second_lstat_unchanged=True, exact_unlink=deleted,
            before_free=initial.get('free'), after_free=after.get('free'),
            protected_paths=['/var/cache/apt/archives/*.deb','/var/lib/apt/lists','/var/lib/dpkg','/srv/log/journal'],
            apt_actions=False, packagekit_dbus_after_stop=False, maintenance_started_wall=start_wall,
            maintenance_finished_wall=time.time()))
        write(ROOT / 'checkpoint.json', dict(stage='cache-hold-established-starting-1915Z-worker', pid=os.getpid(),
                                             wall=time.time(), monotonic=time.monotonic(), deadline_monotonic=deadline))
        with (ROOT / 'owner-wrapper.log').open('w') as log:
            child = subprocess.Popen([sys.executable, str(WORKER)], stdout=log, stderr=subprocess.STDOUT)
        owner['child_pid'] = child.pid; write(ROOT / 'owner-wrapper.json', owner)
        next_probe = 0
        while child.poll() is None:
            now = time.monotonic()
            if now >= deadline - 60:
                request_stop(child, '75 minute owner deadline reserve', {'child_pid': child.pid})
                break
            if now >= next_probe:
                try:
                    event = probe(False, 'worker-hold-supervision')
                    append_jsonl(CACHE_ROOT / 'hold-events.jsonl', event)
                    regenerated = [path for path in CACHE_PATHS if event.get('files', {}).get(path, {}).get('exists')]
                    if event.get('service', {}).get('active') != 'inactive' or event.get('packagekit_processes') or regenerated:
                        hold_failure = request_stop(child, 'PackageKit auto-start/cache regeneration',
                                                    {'regenerated': regenerated, 'service': event.get('service')})
                        append_jsonl(CACHE_ROOT / 'hold-events.jsonl', hold_failure)
                        break
                except Exception as exc:
                    hold_failure = request_stop(child, 'PackageKit hold probe failure', {'error': repr(exc)})
                    append_jsonl(CACHE_ROOT / 'hold-events.jsonl', hold_failure)
                    break
                next_probe = time.monotonic() + 15
            write(ROOT / 'checkpoint.json', dict(stage='1915Z-worker-running', pid=os.getpid(), child_pid=child.pid,
                                                 child_returncode=None, wall=time.time(), monotonic=time.monotonic(),
                                                 deadline_monotonic=deadline, hold_failure=hold_failure))
            time.sleep(2)
        if child is not None:
            try:
                child_returncode = child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                child.terminate()
                try: child_returncode = child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill(); child_returncode = child.wait(timeout=5)
    except BaseException as exc:
        hold_failure = dict(reason='wrapper failure', error=repr(exc), wall=time.time())
        write(ROOT / 'wrapper-error.json', hold_failure)
        if child is not None and child.poll() is None:
            request_stop(child, 'wrapper exception', {'error': repr(exc)})
            try: child.wait(timeout=45)
            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
    finally:
        restore = restore_packagekit(initial) if hold and initial else dict(ok=True, attempted=False)
        worker_exit = json.loads((ROOT / 'worker-exit.json').read_text()) if (ROOT / 'worker-exit.json').exists() else {}
        final_closeout = json.loads((ROOT / 'final-closeout.json').read_text()) if (ROOT / 'final-closeout.json').exists() else {}
        final = dict(wrapper_pid=os.getpid(), child_pid=child.pid if child else None,
                     child_returncode=child_returncode, hold_failure=hold_failure,
                     worker_exit=worker_exit, final_closeout=final_closeout,
                     packagekit_restore=restore, wall=time.time())
        write(ROOT / 'wrapper-final.json', final)
    ok = bool(restore.get('ok') is True and worker_exit.get('status') == 'complete'
              and final_closeout.get('ok') is True)
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
