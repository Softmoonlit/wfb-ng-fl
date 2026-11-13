#!/usr/bin/env python3
"""总 owner：fl02 精确缓存 hold、1915Z worker 子进程监督和 finally 恢复。"""
import importlib.util
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
HOSTS = ['fl00'] + [f'fl{n:02d}' for n in [1, 2, 3, 5, 6, 7, 8, 9, 10]]
NODES = [1, 2, 3, 5, 6, 7, 8, 9, 10]
HARDWARE_TABLE = json.loads((ROOT / 'hardware-from-actual.json').read_text())
HARDWARE = HARDWARE_TABLE['hosts']
ACTUAL_DEVICES = HARDWARE_TABLE['actual_devices']
NATIVE_SHA = '44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2'
COMMIT = '1cd6264d5d6be660998ce60f3a349063009ecfa7'
RELEASE = '/srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261109T083151Z'
CACHE_PATHS = ['/var/cache/apt/pkgcache.bin', '/var/cache/apt/srcpkgcache.bin']
LOCK_PATHS = ['/var/lib/dpkg/lock-frontend', '/var/lib/dpkg/lock',
              '/var/lib/apt/lists/lock', '/var/cache/apt/archives/lock']

# A restart establishes the boot-id baseline; no prior boot-id is accepted.
ALL_HOST_PROBE_TIMEOUT = 8


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
   fd=os.open(path,os.O_RDWR|os.O_CLOEXEC);row['exists']=True
   try:
    fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);row['held']=False;fcntl.lockf(fd,fcntl.LOCK_UN)
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


def probe(query, label, expected_boot_id=None):
    result = remote('QUERY=' + ('True' if query else 'False') + '\n' + PROBE, 15)
    if result.get('returncode') != 0:
        raise RuntimeError('fl02 probe failed: ' + repr(result))
    value = result['result']; value['label'] = label; value['local_wall'] = time.time()
    if expected_boot_id is not None and value.get('boot_id') != expected_boot_id:
        raise RuntimeError('fl02 boot id changed during cache maintenance: ' + repr(dict(expected=expected_boot_id, actual=value.get('boot_id'))))
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


def startup_all_check():
    started = time.monotonic(); deadline = started + 180; attempts = []
    spec = importlib.util.spec_from_file_location('startup_driver', ROOT / 'driver-base.py')
    d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
    d.MATRIX['parent_GO_verified'] = True
    d.MATRIX['status'] = 'startup-readonly-admission'
    d.MATRIX['deadline_monotonic'] = deadline
    since = int(time.time())
    attempt_number = 0
    while time.monotonic() < deadline:
        attempt_number += 1
        label = 'startup-all-' + str(attempt_number)
        audit = d.audit(ROOT, label, 30, 5, since=since, expected_fec=12, release_root=RELEASE)
        try:
            status = d.status()
        except Exception as exc:
            status = {'error': repr(exc)}
        boots = {row['host']: row['result'].get('boot_id') for row in audit['rows']
                 if row.get('returncode') == 0 and row.get('result', {}).get('boot_id')}
        errors = [error for row in audit['checks'] for error in row['errors']]
        fatal_errors = [error for error in errors if error != 'iw baseline mismatch']
        online = sorted(int(k) for k, value in status.get('nodes', {}).items()
                        if value.get('readiness') != 'OFFLINE') if 'nodes' in status else []
        bad = [node for node in NODES if any(status.get('nodes', {}).get(str(node), {}).get(key) != value
                                             for key, value in [('readiness', 'READY'),
                                                                ('reported_state', 'IDLE'),
                                                                ('current_channel', 149)])]
        channel_only_bad = bool(bad) and all(
            status.get('nodes', {}).get(str(node), {}).get('readiness') == 'READY'
            and status.get('nodes', {}).get(str(node), {}).get('reported_state') == 'IDLE'
            and status.get('nodes', {}).get(str(node), {}).get('current_channel') != 149
            for node in bad)
        registry_ok = (status.get('server_state') == 'IDLE' and status.get('active_job') is None
                       and online == NODES)
        passed = (audit['ok'] or (audit['checks'] and not fatal_errors)) and registry_ok \
                 and (not bad or channel_only_bad) and len(boots) == len(HOSTS)
        attempt = dict(wall=time.time(), label=label, audit=audit, status=status,
                       boot_ids=boots, fatal_errors=fatal_errors, fail_nodes=bad,
                       channel_only_bad=channel_only_bad, registry_ok=registry_ok,
                       passed=passed)
        attempts.append(attempt)
        write(ROOT / 'startup-all-check.json', dict(hosts=HOSTS, deadline_seconds=180,
                                                     passed=passed, attempts=attempts,
                                                     policy='per-host current boot baseline; USB speed recorded, not a standalone failure'))
        if passed:
            write(ROOT / 'boot-ids-maintenance.json', dict(hosts=boots, captured_wall=time.time(),
                source='startup-all-check', policy='require per-host continuity during maintenance'))
            return attempt
        if time.monotonic() < deadline:
            time.sleep(min(5, deadline - time.monotonic()))
    raise TimeoutError('all-ten new-boot startup admission exceeded 180 seconds')


def request_stop(child, reason, details=None):
    payload = dict(reason=reason, wall=time.time(), pid=os.getpid(), **(details or {}))
    if not (ROOT / 'STOP').exists():
        write(ROOT / 'STOP', payload)
    if child.poll() is None:
        child.send_signal(signal.SIGINT)
    return payload


def readonly_query_service_stop_allowed(tx):
    # 只允许已核验的更新详情查询；安装、升级和其他事务均不进入维护。
    if not tx.get('valid') or tx.get('count') != 1 or tx.get('paths') != ['/372_cbdeeced']:
        return False
    code = r'''import subprocess,json
out={}
for key in ['Role','AllowCancel']:
 p=subprocess.run(['busctl','--system','get-property','org.freedesktop.PackageKit','/372_cbdeeced','org.freedesktop.PackageKit.Transaction',key],capture_output=True,text=True,timeout=5)
 out[key]=dict(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
print(json.dumps(out))
'''
    result = remote(code, 15)
    write(CACHE_ROOT / 'readonly-query-admin-stop-admission.json', result)
    props = result.get('result', {})
    return (result.get('returncode') == 0
            and props.get('Role', {}).get('returncode') == 0
            and props.get('Role', {}).get('stdout', '').strip() == 'u 8'
            and props.get('AllowCancel', {}).get('returncode') == 0
            and props.get('AllowCancel', {}).get('stdout', '').strip() == 'b true')


def main():
    CACHE_ROOT.mkdir(exist_ok=True)
    start_wall = time.time(); start_mono = time.monotonic(); deadline = start_mono + PHASE_SECONDS
    owner = dict(owner='cache-hold-wrapper', pid=os.getpid(), root=str(ROOT), host='fl00',
                 started_wall=start_wall, deadline_monotonic=deadline, phase_budget_seconds=PHASE_SECONDS,
                 stop_file=str(ROOT / 'STOP'), child_worker=str(WORKER), child_run_ids=[],
                 maintenance_boot_id=None)
    write(ROOT / 'owner-wrapper.json', owner)
    write(ROOT / 'checkpoint.json', dict(stage='all-ten-new-boot-startup-check', pid=os.getpid(),
                                         wall=start_wall, monotonic=start_mono, deadline_monotonic=deadline))
    initial = None; hold = False; child = None; hold_failure = None; child_returncode = None
    maintenance_boot_id = None
    try:
        startup = startup_all_check()
        write(ROOT / 'power-event-checkpoint.json', dict(stage='all-ten-new-boot-before-execute',
            status='confirmed-infrastructure-restart-admission', hosts=HOSTS, confirmed_by_user=True,
            parameter_failure=False, boot_ids=startup['boot_ids'], model_transfer_started=False,
            model_files_sent=False, execute_count=0, prior_trial_reuse=False,
            startup_check_deadline_seconds=180, startup_check=startup, wall=time.time()))
        start_wall = time.time(); start_mono = time.monotonic(); deadline = start_mono + PHASE_SECONDS
        owner.update(started_wall=start_wall, deadline_monotonic=deadline)
        write(ROOT / 'owner-wrapper.json', owner)
        write(ROOT / 'checkpoint.json', dict(stage='before-fl02-readonly-preflight', pid=os.getpid(),
                                             wall=start_wall, monotonic=start_mono, deadline_monotonic=deadline,
                                             maintenance_boot_ids=startup['boot_ids']))
        initial = probe(True, 'before-stop-readonly')
        write(CACHE_ROOT / 'preflight.json', initial)
        tx = initial.get('transaction', {})
        if not tx.get('valid') or (tx.get('count') != 0 and not readonly_query_service_stop_allowed(tx)):
            raise RuntimeError('PackageKit 存在未获准维护的事务: ' + repr(tx))
        maintenance_boot_id = initial.get('boot_id')
        if not maintenance_boot_id:
            raise RuntimeError('fl02 current boot id missing from cache preflight')
        owner['maintenance_boot_id'] = maintenance_boot_id
        write(ROOT / 'owner-wrapper.json', owner)
        write(CACHE_ROOT / 'boot-baseline.json', dict(host='fl02', boot_id=maintenance_boot_id,
            captured_wall=time.time(), policy='current boot after restart; require continuity until finally'))
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
        after_stop = probe(False, 'after-stop-no-dbus', expected_boot_id=maintenance_boot_id); write(CACHE_ROOT / 'after-stop.json', after_stop)
        if after_stop.get('service', {}).get('active') != 'inactive' or after_stop.get('packagekit_processes'):
            raise RuntimeError('PackageKit did not remain inactive after stop')
        if after_stop.get('apt_processes') or any(x.get('held') is not False for x in after_stop.get('locks', {}).values()):
            raise RuntimeError('apt/dpkg process or lock appeared after stop')
        expected = {path: after_stop['files'][path] for path in CACHE_PATHS}
        deleted = exact_delete(expected); write(CACHE_ROOT / 'exact-unlink.json', deleted)
        if deleted.get('returncode') != 0:
            raise RuntimeError('exact cache unlink failed: ' + repr(deleted))
        after = probe(False, 'after-exact-unlink-hold', expected_boot_id=maintenance_boot_id); write(CACHE_ROOT / 'after-unlink.json', after)
        if after.get('service', {}).get('active') != 'inactive' or after.get('packagekit_processes'):
            raise RuntimeError('PackageKit not held inactive after unlink')
        if any(after.get('files', {}).get(path, {}).get('exists') is not False for path in CACHE_PATHS):
            raise RuntimeError('exact cache still exists after unlink')
        write(CACHE_ROOT / 'cache-maintenance-report.json', dict(ok=True, host='fl02',
            boot_id=maintenance_boot_id, boot_id_changed=False,
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
                    event = probe(False, 'worker-hold-supervision', expected_boot_id=maintenance_boot_id)
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
        restore = restore_packagekit(initial['service']) if hold and initial else dict(ok=True, attempted=False)
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
