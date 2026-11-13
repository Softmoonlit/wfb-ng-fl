#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent
HOSTS = ['fl00', 'fl01', 'fl02', 'fl03', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09', 'fl10']
NODES = [1, 2, 3, 5, 6, 7, 8, 9, 10]
MIN_STORAGE = 512 * 1024 * 1024
PREFERRED_STORAGE = 1024 * 1024 * 1024

spec = importlib.util.spec_from_file_location('fresh_driver', ROOT / 'driver-base.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
d.MATRIX['parent_GO_verified'] = True
d.MATRIX['status'] = 'admission'
d.MATRIX['started_wall'] = time.time()
d.MATRIX['deadline_monotonic'] = time.monotonic() + 1800

def write(path, value):
    path = pathlib.Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)

def remote_probe(host, since):
    code = r'''import json, pathlib, socket, subprocess, time, os

def run(argv, timeout=5):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return dict(argv=argv, returncode=p.returncode, stdout=p.stdout[:200000], stderr=p.stderr[:200000])
    except Exception as exc:
        return dict(argv=argv, error=repr(exc))

def show(unit):
    return run(['systemctl', 'show', unit,
                '-p', 'Id', '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID',
                '-p', 'ExecMainPID', '-p', 'ExecMainStartTimestamp',
                '-p', 'ControlGroup', '-p', 'FragmentPath'])

host = socket.gethostname()
role = 'server' if host == 'fl00' else 'client'
units = run(['systemctl', 'list-units', '--all', '--no-legend', '--plain', 'comm-smoke-*'])
unit_names = []
for line in units.get('stdout', '').splitlines():
    fields = line.split()
    if fields and fields[0].startswith('comm-smoke-'):
        unit_names.append(fields[0])
unit_details = {name: show(name) for name in unit_names}
ps = run(['ps', '-eo', 'pid=,ppid=,stat=,lstart=,args=', '--forest'])
comm_processes = [line for line in ps.get('stdout', '').splitlines() if 'comm-smoke-' in line]
routes = {}
for group in ['239.80.41.1/32', '239.80.42.1/32']:
    routes[group] = run(['ip', '-j', 'route', 'show', 'exact', group])
route_all = run(['ip', '-j', 'route', 'show', 'dev', 'fl-s' if host == 'fl00' else 'fl-c' + host[2:]])
packagekit = {
    'unit': run(['systemctl', 'show', 'packagekit.service', '-p', 'ActiveState', '-p', 'SubState', '-p', 'UnitFileState', '-p', 'MainPID']),
    'transactions': run(['busctl', 'call', 'org.freedesktop.PackageKit', '/org/freedesktop/PackageKit', 'org.freedesktop.PackageKit', 'GetTransactionList']),
    'tree': run(['busctl', 'tree', 'org.freedesktop.PackageKit']),
    'daemon_processes': [line for line in run(['ps', '-eo', 'pid=,ppid=,stat=,args=']).get('stdout', '').splitlines() if 'packagekit' in line.lower() or 'apt' in line.lower() or 'dpkg' in line.lower()]
}
space = pathlib.Path('/srv/wfb-ng-fl')
st = os.statvfs(str(space))
limits = {'/sys/devices/system/cpu/online': '0-5',
          '/sys/devices/platform/17000000.gpu/devfreq_dev/max_freq': '612000000',
          '/sys/kernel/nvpmodel_clk_cap/emc': '2133000000',
          '/sys/kernel/debug/bpmp/debug/clk/emc/mrq_rate_locked': '0'}
for core in range(6):
    limits['/sys/devices/system/cpu/cpu' + str(core) + '/cpufreq/scaling_max_freq'] = '1497600'
actual_limits = {}
for path in limits:
    try:
        actual_limits[path] = pathlib.Path(path).read_text().strip()
    except Exception as exc:
        actual_limits[path] = 'ERROR:' + repr(exc)
services = {
    'daemon': run(['systemctl', 'show', 'wfb-fl-' + role + '-daemon.service', '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID']),
    'nvpmodel': run(['systemctl', 'show', 'nvpmodel.service', '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'ActiveState']),
    'jtop': run(['systemctl', 'is-active', 'jtop.service']),
    'power_query': run(['nvpmodel', '-q'])
}
logs = {
    'daemon': run(['journalctl', '-u', 'wfb-fl-' + role + '-daemon.service', '--since', '@' + str(SINCE), '--no-pager', '-o', 'short-iso', '-n', '300']),
    'kernel': run(['journalctl', '-k', '--since', '@' + str(SINCE), '--no-pager', '-o', 'short-iso', '-n', '300'])
}
boot = {
    'boot_id': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'uptime': pathlib.Path('/proc/uptime').read_text().strip(),
    'hostname': host,
    'monotonic': time.monotonic()
}
print(json.dumps(dict(host=host, boot=boot, services=services, limits=actual_limits,
    expected_limits=limits, power_limits_ok=actual_limits == limits,
    storage=dict(available_bytes=st.f_bavail * st.f_frsize, available_inodes=st.f_favail,
                 total_bytes=st.f_blocks * st.f_frsize), old_units=units,
    old_unit_names=unit_names, old_unit_details=unit_details,
    comm_processes=comm_processes, routes=routes, route_all=route_all,
    packagekit=packagekit, logs=logs), ensure_ascii=False))
'''.replace('SINCE', str(since))
    return d.remote(host, code, timeout=15)

since = int(time.time())
audit = d.audit(ROOT, 'full-admission', 30, 5, since=since, expected_fec=12, release_root=d.RELEASE)
with __import__('concurrent.futures').futures.ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
    rows = list(pool.map(lambda host: remote_probe(host, since), HOSTS))
try:
    registry = d.status()
    registry_error = None
except Exception as exc:
    registry = None
    registry_error = repr(exc)
storage = {row['host']: row.get('result', {}).get('storage') for row in rows if row.get('returncode') == 0}
low_storage = {host: value for host, value in storage.items()
               if value and value.get('available_bytes', 0) < MIN_STORAGE}
preferred_shortfall = {host: value for host, value in storage.items()
                       if value and value.get('available_bytes', 0) < PREFERRED_STORAGE}
active_old = {}
route_residue = {}
packagekit_rows = {}
for row in rows:
    if row.get('returncode') != 0:
        continue
    result = row['result']
    active_old[row['host']] = [name for name, detail in result.get('old_unit_details', {}).items()
                                if 'ActiveState=active' in detail.get('stdout', '')]
    route_residue[row['host']] = result.get('routes', {})
    packagekit_rows[row['host']] = result.get('packagekit', {})
registry_nodes = (registry or {}).get('nodes', {})
registry_online = sorted(int(key) for key, value in registry_nodes.items()
                         if value.get('readiness') != 'OFFLINE')
registry_bad = [node for node in NODES if any(
    registry_nodes.get(str(node), {}).get(key) != value
    for key, value in [('readiness', 'READY'), ('reported_state', 'IDLE'), ('current_channel', 149)])]
registry_ok = (registry_error is None and registry_online == NODES and not registry_bad
               and (registry or {}).get('active_job') is None
               and (registry or {}).get('server_state') == 'IDLE')
remote_ok = all(row.get('returncode') == 0 for row in rows)
no_old_active = all(not values for values in active_old.values())
no_route_residue = all(not any(item.get('result', {}).get('stdout', '').strip()
                               for item in routes.values())
                       for routes in route_residue.values())
packagekit_read = all('unit' in value for value in packagekit_rows.values())
storage_hard_ok = remote_ok and not low_storage
full_admission = bool(audit.get('ok') and remote_ok and registry_ok and no_old_active
                     and no_route_residue and packagekit_read and storage_hard_ok)
result = dict(
    status='passed' if full_admission else 'failed',
    full_admission=full_admission,
    since=since,
    audit=audit,
    probes=rows,
    registry=registry,
    registry_error=registry_error,
    registry_online_nodes=registry_online,
    registry_bad_nodes=registry_bad,
    storage=dict(rows=storage, hard_minimum_bytes=MIN_STORAGE,
                 preferred_bytes=PREFERRED_STORAGE, low_hosts=low_storage,
                 preferred_shortfall_hosts=preferred_shortfall,
                 hard_ok=storage_hard_ok),
    old_unit_active=active_old,
    route_residue=route_residue,
    packagekit_read=packagekit_read,
    packagekit=packagekit_rows,
    checks=dict(audit_ok=audit.get('ok'), remote_ok=remote_ok, registry_ok=registry_ok,
                no_old_active=no_old_active, no_route_residue=no_route_residue,
                packagekit_read=packagekit_read, storage_hard_ok=storage_hard_ok,
                fl06_probe=next((row for row in rows if row.get('host') == 'fl06'), None),
                fl10_probe=next((row for row in rows if row.get('host') == 'fl10'), None)),
    wall=time.time())
write(ROOT / 'full-admission.json', result)
print(json.dumps(dict(full_admission=full_admission, registry_ok=registry_ok,
                      remote_ok=remote_ok, low_storage=sorted(low_storage),
                      fl06_returncode=next((row.get('returncode') for row in rows if row.get('host') == 'fl06'), None),
                      fl10_returncode=next((row.get('returncode') for row in rows if row.get('host') == 'fl10'), None)), ensure_ascii=False))
raise SystemExit(0 if full_admission else 2)
