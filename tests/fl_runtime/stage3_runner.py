#!/usr/bin/env python3
"""Stage 3 physical acceptance orchestration; never orchestrates client jobs via SSH.

Reuses canonical fixtures, artifact SHA-256, hardware discovery and Issue 41's
parameterized stopped-node audit. Debian installation, REST orchestration and
radio fault injection are Stage 3 specific. Every process is awaited: no SSH
connection can survive into the protected job window.
"""
from __future__ import annotations

import argparse
import io
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import tarfile
import tempfile
import time
import uuid
import urllib.request
import urllib.error

from wfb_ng.fl.artifacts import file_sha256, write_json_atomic, validate_path_safe_identifier
from tests.fl_runtime.issue41_lifecycle import LifecycleConfig, audit_stopped_node

from tests.fl_runtime.stage3_archive import STAGES

ROLES = ('server', 'client1', 'client2')
from tests.fl_runtime.stage3_archive import FIXED_CONFIG, init_envelope



def python_command(code: str) -> str:
    # Runtime probes must resolve installed modules, never the orchestration
    # checkout via Python's current-directory import path.
    return 'cd / && sudo -n /usr/bin/python3 -I -c ' + shlex.quote(code)


def fault_rules(action: str, run_id: str) -> str:
    """Exact quoted JSON type values avoid matching prefix CONFIRMED/ACK types.

    Probe uses an unreferenced chain: it cannot affect packets. Actual rules
    only inspect Client 2's TUN UDP control input, with a run-scoped comment.
    """
    if action not in ('install', 'remove', 'observe', 'probe'):
        raise ValueError(action)
    chain = 'S3P' + run_id[-12:]
    def rule(verb: str, message: str, dest: str = 'INPUT') -> str:
        scope = '' if dest != 'INPUT' else '-i fl-c2 -p udp --dport 9000 '
        return ('sudo -n iptables -w 5 ' + verb + ' ' + dest + ' ' + scope
                + '-m string --algo bm --string ' + shlex.quote('"' + message + '"')
                + ' -m comment --comment ' + shlex.quote(run_id) + ' -j DROP')
    messages = ('NEW_CHANNEL_PING', 'RADIO_SWITCH_FINALIZED')
    if action == 'probe':
        # finally/trap removes even a partially populated probe chain.
        body = '\n'.join(rule('-A', m, chain) for m in messages)
        return (f"set -eu\nsudo -n iptables -w 5 -N {chain}\n"
                f"trap 'sudo -n iptables -w 5 -F {chain}; sudo -n iptables -w 5 -X {chain}' EXIT HUP INT TERM\n"
                + body + '\n')
    if action == 'install':
        return 'set -eu\n' + '\n'.join(rule('-I', m) for m in messages)
    if action == 'remove':
        return '\n'.join('if ' + rule('-C', m) + '; then ' + rule('-D', m)
                         + '; fi' for m in messages)
    return 'sudo -n iptables -w 5 -S INPUT'


class AuditedExecutor:
    def __init__(self, archive: Path, backend=None):
        self.archive = archive
        self.stage = 'preflight'
        self.purpose = 'inspection'
        self.job_window = False
        self.backend = backend

    def run(self, target: str, cmd: str, timeout: float = 30, input_data=None):
        if target not in ROLES:
            raise ValueError('unknown target')
        if target != 'server' and (self.job_window or self.stage in ('run-sync', 'validate')):
            raise RuntimeError('SSH forbidden during job window/validation')
        if target != 'server' and self.stage == 'run-radio-recovery' and self.purpose not in (
                'fault-install', 'fault-observe', 'fault-remove'):
            raise RuntimeError('radio phase permits only fixed fault commands')
        started = time.time()
        rc, out, err = 125, '', ''
        try:
            if self.backend:
                parameters=inspect.signature(self.backend).parameters
                if 'input_data' in parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
                    rc, out, err = self.backend(target, cmd, timeout, input_data=input_data)
                else:
                    rc, out, err = self.backend(target, cmd, timeout)
            else:
                argv = ['bash', '-c', cmd] if target == 'server' else [
                    'ssh', '-o', 'BatchMode=yes', '-o', 'ControlMaster=no',
                    '-o', 'ControlPath=none', '-o', 'ControlPersist=no',
                    '-o', 'ConnectTimeout=10', 'vm' + target[-1], cmd]
                process=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                         stdin=subprocess.PIPE if input_data is not None else None,
                                         text=input_data is None,start_new_session=True)
                try:
                    stdout,stderr=process.communicate(input_data,timeout=timeout)
                except BaseException:
                    # A signal/timeout must reap the foreground SSH process
                    # before any next command or protected runtime window.
                    try:
                        os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid,signal.SIGKILL)
                        process.communicate()
                    raise
                rc=process.returncode
                out=stdout if isinstance(stdout,str) else stdout.decode(errors='replace')
                err=stderr if isinstance(stderr,str) else stderr.decode(errors='replace')

        except subprocess.TimeoutExpired:
            rc, err = 124, 'command timeout; SSH process reaped'
        finally:
            meta=json.loads((self.archive/'envelope.json').read_text())
            record = dict(run_id=meta['run_id'],job_id=meta['job_id'], started_at=started, ended_at=time.time(), target='vm0' if target=='server' else 'vm'+target[-1],
                          stage=self.stage, purpose=self.purpose, category=self.purpose, command=cmd,
                          transport='local' if target == 'server' else 'ssh',
                          exit_code=rc, returncode=rc, stderr=err)
            with (self.archive / 'orchestration.jsonl').open('a') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        return rc, out, err

    def checked(self, target, cmd, timeout=30, input_data=None):
        rc, out, err = self.run(target, cmd, timeout, input_data=input_data)
        if rc:
            raise RuntimeError(f'{target}: command exited {rc}: {err or out}')
        return out


class Stage3Runner:
    def __init__(self, archive: Path, repo: Path, executor=None):
        self.archive, self.repo = archive.resolve(), repo.resolve()
        self.meta = json.loads((self.archive / 'envelope.json').read_text())
        self.run_id, self.job_id = self.meta['run_id'], self.meta['job_id']
        self.executor = executor or AuditedExecutor(self.archive)
        self.lifecycle = LifecycleConfig(roles=ROLES,
            server_unit='wfb-fl-server-daemon.service', client_unit='wfb-fl-client-daemon.service',
            server_tun='fl-s', client_tuns={'client1': 'fl-c1', 'client2': 'fl-c2'})
        self.fixture_root = '/var/tmp/wfb-stage3/' + self.run_id
        self.job_root = Path('/tmp/wfb-ng-fl/server') / ('job_' + self.job_id)

    def save(self, name, value):
        path = self.archive / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(path), value)

    def bound(self, **fields):
        return dict(run_id=self.run_id, job_id=self.job_id, **fields)

    def rest(self, path='status', payload=None, timeout=20):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request('http://127.0.0.1:9090/api/v1/' + path,
                    data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    @staticmethod
    def ready(status, channel=157):
        return status.get('server_state') == 'IDLE' and all(
            status.get('nodes', {}).get(str(n), {}).get('reported_state') == 'IDLE'
            and status['nodes'][str(n)].get('readiness') == 'READY'
            and status['nodes'][str(n)].get('current_channel') == channel for n in (1, 2))

    def wait_ready(self, timeout):
        started = time.monotonic()
        deadline = started + timeout
        prefix = 'readiness/' + self.executor.stage
        timeline = self.archive / prefix / 'timeline.jsonl'
        timeline.parent.mkdir(parents=True, exist_ok=True)
        wait_id = uuid.uuid4().hex
        result = self.bound(wait_id=wait_id, started_at=time.time(), timeout_seconds=timeout,
                            status='failed', samples=0, last_status=None)
        try:
            while time.monotonic() < deadline:
                sample = self.bound(wait_id=wait_id, observed_at=time.time(),
                                    elapsed_seconds=time.monotonic() - started)
                try:
                    status = self.rest()
                except (urllib.error.URLError,TimeoutError,ConnectionError) as exc:
                    sample.update(error_type=type(exc).__name__, error=str(exc))
                else:
                    sample['status'] = status
                    result['last_status'] = status
                    self.save(prefix + '/last-status.json', status)
                with timeline.open('a') as stream:
                    stream.write(json.dumps(sample, ensure_ascii=False) + '\n')
                result['samples'] += 1
                if 'status' in sample and self.ready(sample['status']):
                    result['status'] = 'passed'
                    return sample['status']
                time.sleep(.25)
            raise TimeoutError('cluster did not restore IDLE/READY')
        except BaseException as exc:
            result.update(error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            result.update(ended_at=time.time(), elapsed_seconds=time.monotonic() - started)
            self.save(prefix + '/result.json', result)

    def stage(self, name):
        if name not in STAGES:
            raise ValueError(name)
        completed = self.meta.get('completed_stages', [])
        if list(STAGES[:STAGES.index(name)]) != completed:
            raise RuntimeError('stages must execute once in strict order')
        self.executor.stage = name
        started = time.time()
        if name == 'validate':
            # Validation reads the archive sealed after stop-services. Only its
            # excluded machine verdict is written; no SSH or evidence mutation.
            self.validate()
            return
        getattr(self, name.replace('-', '_'))()
        self.save('stages/' + name + '.json', dict(status='passed', started_at=started,
                                                 ended_at=time.time(), run_id=self.run_id))
        self.meta['completed_stages'] = completed + [name]
        self.save('envelope.json', self.meta)
        if name == 'stop-services':
            from tests.fl_runtime.stage3_archive import seal_archive
            seal_archive(self.archive)

    def preflight(self):
        from tests.fl_runtime.hardware import inspect_wireless_device
        topology = {}
        for role in ROLES:
            repo = str(self.repo)
            commit = self.executor.checked(role, 'git -C ' + shlex.quote(repo) + ' rev-parse HEAD').strip()
            clean = not self.executor.checked(role, 'git -C ' + shlex.quote(repo) + ' status --porcelain').strip()
            if commit != self.meta['commit'] or not clean:
                raise RuntimeError(role + ': commit parity/clean worktree failed')
            self.executor.checked(role, 'sudo -n true')
            self.executor.checked(role, 'for c in ip iw ss systemctl journalctl python3 dpkg dpkg-deb iptables tar uftp uftpd git; do command -v "$c" >/dev/null || exit 1; done')
            wireless = inspect_wireless_device(self.executor, role)
            if wireless['driver'] not in ('rtl88xxau_wfb', '88XXau_wfb') or wireless['usb_controller'] != 'xhci_hcd':
                raise RuntimeError(role + ': requires rtl88xxau_wfb and xHCI')
            if float(wireless['usb_speed']) < 480:
                raise RuntimeError(role + ': insufficient USB speed')
            ports = self.executor.checked(role, 'ss -H -tuln')
            if re.search(r':(?:9000|9001|9090|1044|8080)\b', ports):
                raise RuntimeError(role + ': port conflict')
            free = int(self.executor.checked(role, "df -Pk /var/tmp | awk 'NR==2 {print $4}'").strip())
            if free < 1024 * 1024:
                raise RuntimeError(role + ': requires 1 GiB free space')
            sandbox_probe="""import json
from pathlib import Path
roots=[Path('/tmp/wfb-ng-fl/server'),Path('/tmp/wfb-ng-fl/client')]
assert not any(p for r in roots for p in r.glob('job_*')), 'stale job sandbox'
cache=Path('/tmp/wfb-ng-fl/client/channel_cache.json')
if cache.exists():
 value=json.loads(cache.read_text())
 assert isinstance(value,dict) and type(value.get('channel')) is int and value['channel']==157,'channel cache differs from fixed Channel 157'
"""
            self.executor.checked(role,python_command(sandbox_probe))
            resources = self.resources(role, stopped=True)
            if not resources['clean']:
                raise RuntimeError(role + ': residual resources')
            identity = None
            if role != 'server':
                identity = json.loads(self.executor.checked(role, 'sudo -n cat /etc/wfb-ng-fl/node.json'))
                n = int(role[-1])
                if type(identity.get('node_id')) is not int or identity.get('node_id') != n or identity.get('tun_ip') not in (f'10.80.0.{10+n}',f'10.80.0.{10+n}/24'):
                    raise RuntimeError(role + ': invalid node identity')
                self.executor.checked(role, 'sudo -n test ! -e ' + shlex.quote('/var/lib/wfb-ng-fl/evidence/' + self.job_id))
            self.executor.checked(role,'sudo -n test ! -e '+shlex.quote(self.fixture_root))
            topology[role] = dict(commit=commit, workspace_clean=clean, wireless=wireless,
                                  identity=identity, resources=resources)
        self.executor.checked('client2', fault_rules('probe', self.run_id))
        self.executor.checked('server', 'command -v make >/dev/null')
        self.save('topology.json', topology)
        self.save('preflight.json', self.bound(nodes={str(i): dict(
            commit=t['commit'],clean=t['workspace_clean'],driver=t['wireless']['driver'],
            usb_driver=t['wireless']['usb_controller'],interface=t['wireless']['interface'],
            identity_valid=True,ports_clear=True,resources_clear=True,disk_ok=True,payload_matcher=True)
            for i,t in enumerate(topology.values())}))

    def resources(self, role, stopped=False):
        # Existing Issue 41 helper supplies cgroup/unit/TUN/short process audit.
        data = audit_stopped_node(self.executor, role, self.lifecycle)
        code = """import json,pathlib,subprocess
rows=[]
for line in subprocess.check_output(['ps','-eo','pid=,comm='],text=True).splitlines():
 pid,comm=line.split()
 try: argv=(pathlib.Path('/proc')/pid/'cmdline').read_bytes().decode().strip('\\0').split('\\0')
 except (OSError,UnicodeError): continue
 names=('wfb_ng.fl.server_daemon','wfb_ng.fl.client_daemon','wfb_ng.fl.role_service','wfb_ng.fl.service','/usr/bin/wfb-fl-server-daemon','/usr/bin/wfb-fl-client-daemon')
 if comm in ('wfb_v6_uplink','uftp','uftpd') or any(x in argv for x in names):
  rows.append(dict(pid=int(pid),comm=comm,args=argv))
print(json.dumps(rows))"""
        data['processes'] = json.loads(self.executor.checked(role, python_command(code)))
        data['tuns']=[x['ifname'] for x in json.loads(self.executor.checked(role,'ip -j link show'))
                      if re.fullmatch(r'fl-(s|c[0-9]+)',x['ifname'])]
        data['clean'] = data['clean'] and not data['processes'] and not data['tuns']

        return data

    def install(self):
        build_dir = Path(tempfile.mkdtemp(prefix='wfb-stage3-build-')) / 'source'
        command = 'git -C ' + shlex.quote(str(self.repo)) + ' worktree '
        try:
            self.executor.checked('server', command + 'add --detach ' + shlex.quote(str(build_dir)) + ' ' + self.meta['commit'])
            self.executor.checked('server', 'test -z "$(git -C ' + shlex.quote(str(build_dir)) + ' status --porcelain)"')
            self.executor.checked('server', 'make -C ' + shlex.quote(str(build_dir)) + ' deb', timeout=1200)
            packages = list(build_dir.glob('**/*.deb')) + list(build_dir.parent.glob('*.deb'))
            if len(packages) != 1:
                raise RuntimeError('independent build must produce exactly one Debian package')
            package = packages[0]
            dest = self.archive / 'package' / package.name
            dest.parent.mkdir()
            dest.write_bytes(package.read_bytes())
            digest = file_sha256(str(dest))
            package_records = {}
            for role in ROLES:
                remote = '/var/tmp/' + self.run_id + '.deb'
                if role == 'server':
                    self.executor.checked(role, 'cp ' + shlex.quote(str(dest)) + ' ' + shlex.quote(remote))
                else:
                    # Transfer over a foreground audited SSH stdin stream; no
                    # command-length limit, unlogged scp or retained sockets.
                    self.executor.checked(role, 'umask 077; set -C; cat > ' + shlex.quote(remote),
                                          timeout=180, input_data=dest.read_bytes())
                actual = self.executor.checked(role, 'sha256sum ' + shlex.quote(remote)).split()[0]
                if actual != digest:
                    raise RuntimeError('package transfer digest mismatch')
                self.audit_package_scripts(role,remote)
                units = 'wfb-fl-client-daemon.service wfb-fl-client.service wfb-fl-server-daemon.service wfb-fl-server.service'
                self.executor.checked(role, 'sudo -n systemctl mask --runtime --now ' + units)
                self.executor.checked(role, 'sudo -n env DEBIAN_FRONTEND=noninteractive dpkg --force-confold --force-confdef -i ' + shlex.quote(remote), timeout=180)
                if role!='server':
                    before=json.loads((self.archive/'topology.json').read_text())[role]['identity']
                    after=json.loads(self.executor.checked(role,'sudo -n cat /etc/wfb-ng-fl/node.json'))
                    if after!=before:
                        raise RuntimeError(role+': package installation changed node identity')
                    self.save('nodes/'+role+'/installed-identity.json',after)
                if not self.resources(role,stopped=True)['clean']:
                    self.save('services-managed.json',dict(run_id=self.run_id,reason='package unexpectedly started managed services'))
                    raise RuntimeError('package install unexpectedly started resources')
                self.executor.checked(role, 'sudo -n systemctl unmask --runtime ' + units + ' && sudo -n systemctl daemon-reload')
                package_records[role] = self.verify_package(role, remote)
            self.save('install.json', dict(package_sha256=digest, package_path=str(dest.relative_to(self.archive)),
                                          commit=self.meta['commit'], nodes=package_records))
            for record in package_records.values():
                record.update(commit=self.meta['commit'],package_sha256=digest)
                for info in record['files'].values():
                    info.update(package_sha256=digest,installed_sha256=info['sha256'],expected_sha256=info['sha256'])
            self.save('installation.json',self.bound(package=dict(commit=self.meta['commit'],
                build_clean=True,build_isolated=True,package_count=1,sha256=digest,
                path=str(dest.relative_to(self.archive))),nodes={str(i):package_records[r] for i,r in enumerate(ROLES)}))
            self.generate_fixtures()
        finally:
            cleanup_error=None
            if build_dir.exists():
                try:
                    self.executor.checked('server', command + 'remove --force ' + shlex.quote(str(build_dir)), timeout=60)
                except Exception as exc:
                    cleanup_error=str(exc)
            try:
                build_dir.parent.rmdir()
            except OSError as exc:
                cleanup_error=cleanup_error or str(exc)
            if cleanup_error:
                self.save('build-cleanup-error.json',dict(run_id=self.run_id,error=cleanup_error,at=time.time()))

    def audit_package_scripts(self,role,path):
        # stdeb emits no service-start hooks today. Reject any package that
        # introduces them before dpkg, instead of relying on that assumption.
        code="""import pathlib,re,subprocess,tempfile
with tempfile.TemporaryDirectory() as directory:
 subprocess.check_call(['dpkg-deb','-e',PACKAGE,directory],stdout=subprocess.DEVNULL)
 conffiles=pathlib.Path(directory)/'conffiles'
 assert conffiles.is_file() and '/etc/wfb-ng-fl/node.json' in conffiles.read_text().splitlines(),'node.json must be a protected Debian conffile'
 for name in ('preinst','postinst','prerm','postrm'):
  p=pathlib.Path(directory)/name
  if p.exists():
   assert not re.search(r'\\b(systemctl|invoke-rc\\.d|deb-systemd-invoke|start-stop-daemon|service)\\b',p.read_text()),'service-control maintainer hook: '+name
print('no service-control maintainer hooks')""".replace('PACKAGE',repr(path),1)
        self.executor.checked(role,python_command(code))

    def verify_package(self, role, path):
        code = """import hashlib,json,pathlib,subprocess,tempfile
package=PATH
name=subprocess.check_output(['dpkg-deb','-f',package,'Package'],text=True).strip()
version=subprocess.check_output(['dpkg-deb','-f',package,'Version'],text=True).strip()
installed=subprocess.check_output(['dpkg-query','-W','-f=${Version}',name],text=True).strip()
assert installed==version
required=['usr/bin/wfb-fl-server-daemon','usr/bin/wfb-fl-client-daemon','usr/bin/wfb_v6_uplink','lib/systemd/system/wfb-fl-server-daemon.service','lib/systemd/system/wfb-fl-client-daemon.service']
files={}
with tempfile.TemporaryDirectory() as temp:
 subprocess.check_call(['dpkg-deb','-x',package,temp],stdout=subprocess.DEVNULL)
 identities=list(pathlib.Path(temp).glob('**/wfb_ng/fl/build_identity.json'))
 assert len(identities)==1,'missing immutable installed build identity'
 identity=identities[0]; identity_rel=str(identity.relative_to(temp))
 actual_identity=pathlib.Path('/')/identity_rel
 assert json.loads(actual_identity.read_text())['commit']==COMMIT
 required.append(identity_rel)
 import importlib
 for module in ('wfb_ng.fl.server_daemon','wfb_ng.fl.client_daemon','wfb_ng.fl.service'):
  origin=pathlib.Path(importlib.import_module(module).__file__).resolve()
  relative=str(origin).lstrip('/')
  assert (pathlib.Path(temp)/relative).is_file(),'imported runtime outside installed package: '+module
  required.append(relative)
 for rel in required:
  src=pathlib.Path(temp)/rel
  if not src.exists() and rel.startswith('lib/'):
   rel='usr/'+rel; src=pathlib.Path(temp)/rel
  dst=pathlib.Path('/')/rel
  assert src.is_file() and dst.is_file(),rel
  a=hashlib.sha256(src.read_bytes()).hexdigest(); b=hashlib.sha256(dst.read_bytes()).hexdigest()
  assert a==b,rel
  owner=subprocess.check_output(['dpkg-query','-S',str(dst)],text=True).strip()
  assert owner.split(': ',1)[0]==name,owner
  key='wfb_ng/fl/build_identity.json' if rel==identity_rel else str(dst)
  files[key]={'sha256':b,'owner':name,'installed_sha256':b,'expected_sha256':a}
print(json.dumps({'package':name,'version':version,'files':files}))""".replace('PATH', repr(path), 1).replace('COMMIT',repr(self.meta['commit']),1)
        return json.loads(self.executor.checked(role, python_command(code)))

    def generate_fixtures(self):
        records = {}
        for role in ROLES:
            name = 'model.bin' if role == 'server' else 'update-client' + role[-1] + '-template.bin'
            fn = 'generate_model_fixture' if role == 'server' else 'generate_client_fixture'
            args = repr(self.fixture_root + '/' + name)
            if role != 'server':
                args += ', ' + role[-1]
            code = f'import json; from wfb_ng.fl.issue41_fixtures import {fn}; print(json.dumps({fn}({args}, size_bytes={FIXED_CONFIG["artifact_size_bytes"]})))'
            records[role] = json.loads(self.executor.checked(role, 'cd / && ' + python_command(code), timeout=120))
        if records['client1']['sha256'] == records['client2']['sha256']:
            raise RuntimeError('node-specific canonical update digests must differ')
        self.save('fixtures.json', records)

    def inspect_unit(self,role):
        output=self.executor.checked(role,'sudo -n systemctl show -p FragmentPath,ExecStart,KillMode,DropInPaths '+self.lifecycle.unit_for_role(role))
        values=dict(line.split('=',1) for line in output.splitlines() if '=' in line)
        entry='/usr/bin/wfb-fl-'+('server' if role=='server' else 'client')+'-daemon'
        executable=re.search(r'path=([^ ;]+)',values.get('ExecStart',''))
        if values.get('KillMode')!='control-group' or not executable or executable[1]!=entry:
            raise RuntimeError('formal packaged unit executable/cgroup mismatch')
        if values.get('DropInPaths')!='':
            raise RuntimeError('formal package units must not have unbound drop-ins')
        fragment=values.get('FragmentPath','')
        installation=json.loads((self.archive/'installation.json').read_text())
        files=installation['nodes'][str(ROLES.index(role))]['files']
        packaged=[p for p in files if p.endswith('/'+self.lifecycle.unit_for_role(role))]
        if len(packaged)!=1 or not fragment:
            raise RuntimeError('missing package unit provenance')
        command='test "$(readlink -f '+shlex.quote(fragment)+')" = "$(readlink -f '+shlex.quote(packaged[0])+')" && sha256sum '+shlex.quote(fragment)
        digest=self.executor.checked(role,command).split()[0]
        if digest!=files[packaged[0]]['installed_sha256']:
            raise RuntimeError('active unit is not the installed package unit')
        return dict(properties=values,sha256=digest,package_path=packaged[0],package_sha256=installation['package']['sha256'])

    def start_services(self):
        started = time.time()
        self.save('services-managed.json', dict(run_id=self.run_id, units={r:self.lifecycle.unit_for_role(r) for r in ROLES}))
        for role in ROLES:
            self.executor.checked(role, 'sudo -n systemctl start ' + self.lifecycle.unit_for_role(role))
            self.executor.checked(role, 'sudo -n systemctl is-active ' + self.lifecycle.unit_for_role(role))
            properties=self.inspect_unit(role)
            (self.archive / 'nodes' / role).mkdir(parents=True, exist_ok=True)
            self.save('nodes/'+role+'/unit.json',properties)
        status = self.wait_ready(max(0, 120 - (time.time() - started)))
        if status.get('radio') != {k: FIXED_CONFIG[k] for k in ('channel','radio_txpower_dbm','downlink_mcs','uplink_mcs','uftp_rate_kbps')}:
            # RadioConfig exposes precisely these fields; extra stable fields are allowed.
            radio = status.get('radio') or {}
            if any(radio.get(k) != FIXED_CONFIG[k] for k in ('channel','radio_txpower_dbm','downlink_mcs','uplink_mcs','uftp_rate_kbps')):
                raise RuntimeError('actual radio configuration differs from fixed contract')
        self.save('services-ready.json', dict(started_at=started, ready_at=time.time(), status=status))

    def run_sync(self):
        payload = dict(run_id=self.run_id, job_id=self.job_id,
            **{k: FIXED_CONFIG[k] for k in ('mode','rounds','target_nodes',
               'round_timeout_seconds','io_timeout_seconds','live_observation')},
            model_path=self.fixture_root+'/model.bin', model_size_bytes=FIXED_CONFIG['artifact_size_bytes'],
            algorithm='wfb_ng.fl.issue41_algorithm:client_main',
            algorithm_config=dict(update_template_path=self.fixture_root+'/update-client{node_id}-template.bin'))
        self.save('job-request.json', payload)
        self.save('job/request.json',payload)
        requested = time.time()
        # Conservative window includes REST readiness handshake and acceptance.
        self.executor.job_window = True
        try:
            accepted = self.rest('jobs/start', payload, timeout=30)
            if accepted.get('status') != 'accepted':
                raise RuntimeError('job not accepted')
            self.save('job-accepted.json', dict(at=time.time(), requested_at=requested, response=accepted))
            deadline = time.monotonic() + max(0, 400 - (time.time()-requested))
            summary_path = self.job_root / 'coordinator_summary.json'
            while time.monotonic() < deadline:
                status = self.rest()
                self.save('job-latest-status.json', status)
                if summary_path.is_file():
                    summary = json.loads(summary_path.read_text())
                    if summary.get('status') in ('succeeded','failed','aborted'):
                        terminal = time.time()
                        self.save('job-window.json', dict(started_at=requested, ended_at=terminal,
                                  accepted_at=json.loads((self.archive/'job-accepted.json').read_text())['at']))
                        self.save('coordinator_summary.json', summary)
                        if summary.get('status') != 'succeeded':
                            raise RuntimeError('coordinator did not succeed')
                        break
                time.sleep(.25)
            else:
                raise TimeoutError('400-second coordinator deadline exceeded')
        finally:
            # No remote cleanup until local abort closes a still-running job.
            if not (self.archive / 'job-window.json').exists():
                try:
                    self.rest('jobs/abort', {'job_id':self.job_id}, timeout=30)
                    if self.rest().get('server_state') != 'IDLE':
                        raise RuntimeError('abort did not close coordinator')
                    self.save('job-window.json', dict(started_at=requested, ended_at=time.time(), outcome='aborted'))
                finally:
                    self.executor.job_window = not (self.archive/'job-window.json').exists()
            else:
                self.executor.job_window = False
        services=json.loads((self.archive/'services-ready.json').read_text())
        window=json.loads((self.archive/'job-window.json').read_text())
        self.save('job/window.json',self.bound(services_started_at=services['started_at'],
            nodes_ready_at=services['ready_at'],ready_nodes=[1,2],
            accepted_at=window['accepted_at'],terminal_at=window['ended_at']))
        self.save('job/coordinator.json',summary)
        recovery = self.bound(started_at=time.time(), timeout_seconds=20, status='failed',
                              evidence='readiness/' + self.executor.stage)
        try:
            idle = self.wait_ready(20)
            self.save('job-idle.json', idle)
            recovery.update(status='passed', idle_status=idle)
        except BaseException as exc:
            recovery.update(error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            recovery['ended_at'] = time.time()
            self.save('job/recovery.json', recovery)

    def radio_journal(self, role):
        # Only fixed, observational fault-scenario commands are allowed here.
        self.executor.purpose = 'fault-observe'
        since = json.loads((self.archive/'radio-request.json').read_text())['started_at']
        return self.executor.checked(role, 'sudo -n journalctl -u ' + self.lifecycle.unit_for_role(role)
                     + ' --since @' + str(since) + ' --no-pager -o short-unix')

    def run_radio_recovery(self):
        self.wait_ready(20)
        self.save('radio-request.json', dict(started_at=time.time(), patch={'channel':149}, target_nodes=[1,2], confirmed=True))
        try:
            self.executor.purpose = 'fault-install'
            # Persist intent first: partial install and interpreter termination are cleanable.
            self.save('fault-state.json', dict(installed=True, installed_at=time.time(), run_id=self.run_id))
            self.executor.checked('client2', fault_rules('install', self.run_id))
            result = self.rest('radio/reconfigure', dict(patch={'channel':149},target_nodes=[1,2],confirmed=True), timeout=60)
            self.save('radio-result.json', result)
            self.save('radio/result.json',result)
            if result.get('status') != 'rolled_back' or result.get('failed_phase') != 'COMMIT':
                raise RuntimeError('radio must roll back before irreversible commit')
            session = result['session_id']
            deadline = time.monotonic()+35
            lease_at = None
            while time.monotonic() < deadline:
                journal = self.radio_journal('client2')
                (self.archive/'radio-client2.log').write_text(journal)
                matching = [line for line in journal.splitlines() if session in line and '射频租约耗尽' in line]
                if matching:
                    lease_at = float(matching[-1].split()[0])
                    break
                time.sleep(.25)
            if lease_at is None:
                raise TimeoutError('no session-bound client lease expiry evidence')
            ready = self.wait_ready(max(0, 20-(time.time()-lease_at)))
            observed=time.time()
            self.save('radio-recovered.json', dict(lease_expired_at=lease_at, ready_at=observed, status=ready))
            status_line=json.dumps(dict(observed_at=observed,status=ready))
            (self.archive/'radio').mkdir(exist_ok=True)
            (self.archive/'radio/status.jsonl').write_text(status_line+'\n')
        finally:
            self.remove_fault()
        self.executor.purpose = 'inspection'

    def remove_fault(self):
        if not (self.archive/'fault-state.json').exists():
            return
        self.executor.purpose = 'fault-remove'
        self.executor.checked('client2', fault_rules('remove', self.run_id))
        rules = self.executor.checked('client2', fault_rules('observe', self.run_id))
        if self.run_id in rules:
            raise RuntimeError('fault rules remain installed')
        installed=json.loads((self.archive/'fault-state.json').read_text()).get('installed_at')
        removed=time.time()
        self.save('fault-state.json', dict(installed=False,installed_at=installed,run_id=self.run_id,rules=rules,checked_at=removed))
        self.save('radio/fault.json', self.bound(node_id=2,drop_types=['NEW_CHANNEL_PING','RADIO_SWITCH_FINALIZED'],
                 present=False,installed_at=installed,removed_at=removed))

    def collect_server_file(self, source, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.executor.checked('server', 'sudo -n install -o ' + str(os.getuid())
                              + ' -g ' + str(os.getgid()) + ' -m 0600 -- '
                              + shlex.quote(str(source)) + ' ' + shlex.quote(str(dest)), timeout=60)

    def collect_tree(self, role, source, dest):
        # Server binaries remain available for offline SHA checking. Client
        # evidence remains the daemon's small, original whitelist archive.
        if role=='server':
            dest.mkdir(parents=True,exist_ok=True)
            self.executor.checked(role,'sudo -n cp -a '+shlex.quote(source+'/.')+' '+shlex.quote(str(dest)),timeout=120)
            # cp -a also preserves the source directory's root ownership on
            # dest. Return only the copied tree to the archive caller; do not
            # follow copied symlinks into runtime paths or other archives.
            self.executor.checked(role,'sudo -n chown -R -h -- '
                                  +str(os.getuid())+':'+str(os.getgid())+' '
                                  +shlex.quote(str(dest)),timeout=120)
            return
        code = """import base64,io,pathlib,tarfile
root=pathlib.Path(SOURCE)
assert root.is_dir(),str(root)
buf=io.BytesIO()
with tarfile.open(fileobj=buf,mode='w:gz') as tar:
 for p in sorted(root.rglob('*')):
  if p.is_symlink(): raise RuntimeError('symlink in evidence')
  if p.is_file():
   if p.suffix=='.bin' or p.stat().st_size>16*1024*1024: raise RuntimeError('large binary in client evidence')
   tar.add(p,arcname=str(p.relative_to(root)),recursive=False)
print(base64.b64encode(buf.getvalue()).decode())""".replace('SOURCE',repr(source),1)
        import base64
        blob = base64.b64decode(self.executor.checked(role, python_command(code),timeout=120))
        dest.mkdir(parents=True,exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as tar:
            for member in tar.getmembers():
                relative=Path(member.name)
                if not member.isfile() or relative.is_absolute() or '..' in relative.parts:
                    raise RuntimeError('unsafe evidence archive member')
                target=dest/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as src:
                    target.write_bytes(src.read())

    def resource_report(self, raw, idle=False, status=None):
        nodes={}
        for i,role in enumerate(ROLES):
            processes=raw[role]['processes']
            names=[p['args'] for p in processes]
            counts=dict(daemon=sum(any(x in a for x in ('wfb_ng.fl.server_daemon','wfb_ng.fl.client_daemon',
                        '/usr/bin/wfb-fl-server-daemon','/usr/bin/wfb-fl-client-daemon')) for a in names),
                        role_service=sum(any(x in a for x in ('wfb_ng.fl.role_service','wfb_ng.fl.service')) for a in names),
                        wfb_v6_uplink=sum(p['comm']=='wfb_v6_uplink' for p in processes),
                        uftp=sum(p['comm'] in ('uftp','uftpd') for p in processes))
            node=dict(processes=counts,tuns=raw[role]['tuns'],raw=raw[role])
            if idle:
                reported=status if i==0 else status['nodes'][str(i)]
                node.update(state=reported.get('server_state') if i==0 else reported.get('reported_state'),
                            phase='READY' if i==0 and status['link_process']['running'] else reported.get('readiness'),
                            channel=status['radio']['channel'] if i==0 else reported.get('current_channel'))
            nodes[str(i)]=node
        return self.bound(nodes=nodes)

    def collect_idle_link(self, role, destination=None):
        root = 'server' if role == 'server' else 'client'
        output = self.executor.checked(role, 'sudo -n cat /tmp/wfb-ng-fl/' + root + '/wfb_uplink.log')
        destination = destination or ('server/link.log' if role == 'server'
                                      else 'nodes/' + role + '/idle-link.log')
        path = self.archive / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output)

    def collect(self):
        from tests.fl_runtime.stage3_archive import CONFIG_PATHS
        status=self.rest()
        self.save('collected-status.json',status)
        idle={}
        postflight={}
        for role in ROLES:
            node = self.archive/'nodes'/role
            node.mkdir(parents=True,exist_ok=True)
            journal = self.executor.checked(role, 'sudo -n journalctl -u '+self.lifecycle.unit_for_role(role)
                      +' --since @'+str(self.meta['created_at'])+' --no-pager -o short-unix')
            (node/'daemon.log').write_text(journal)
            idle[role] = self.resources(role)
            self.collect_wireless(role)
            if role=='server':
                self.collect_tree(role,str(self.job_root),node/'job')
                self.collect_tree(role,str(self.job_root)+'_role',self.archive/'server')
                # Copy actual JobConfig emitted by the coordinator.
                self.collect_server_file(self.job_root/'job_config.json',
                                         self.archive/'server/job_config.json')
                (self.archive/'server/models').mkdir(exist_ok=True)
                summary=json.loads((self.archive/'job/coordinator.json').read_text())
                for index,r in enumerate(summary['rounds'],1):
                    self.collect_server_file(r['output_model_path'],
                                             self.archive/f'server/models/{index}.bin')
                links=[p['args'] for p in idle[role]['processes'] if p['comm']=='wfb_v6_uplink']
                if len(links)!=1: raise RuntimeError('server must have exactly one persistent link')
                self.collect_idle_link(role)
                self.save('server/link.json',dict(argv=links[0]))
                physical=json.loads((self.archive/'nodes/server/radio.json').read_text())
                self.save('server/radio.json',physical)
            else:
                self.collect_idle_link(role)
                self.collect_tree(role,'/var/lib/wfb-ng-fl/evidence/'+self.job_id,self.archive/'clients'/role[-1])
            repo=shlex.quote(str(self.repo))
            head=self.executor.checked(role,'git -C '+repo+' rev-parse HEAD').strip()
            clean=not self.executor.checked(role,'git -C '+repo+' status --porcelain').strip()
            if head!=self.meta['commit'] or not clean:
                raise RuntimeError(role+': postflight commit parity/clean worktree failed')
            postflight[str(ROLES.index(role))]=dict(commit=head,clean=clean,unit=self.inspect_unit(role))
        self.save('postflight.json',self.bound(nodes=postflight))
        self.save('idle-resources.json',idle)
        self.save('resources/idle.json',self.resource_report(idle,True,status))
        self.meta['generated_config_sha256']={p:file_sha256(str(self.archive/p)) for p in CONFIG_PATHS}
        self.save('envelope.json',self.meta)
        self.radio_events()

    def collect_wireless(self,role):
        from tests.fl_runtime.hardware import discover_wireless_interface
        interface=discover_wireless_interface(self.executor,role)
        info=self.executor.checked(role,'iw dev '+shlex.quote(interface)+' info')
        path=self.archive/'nodes'/role/'iw.txt'
        path.write_text(info)
        # Channel 157 with center frequency 5795 MHz proves HT40+.
        if not re.search(r'channel 157 .*width: 40 MHz.*center1: 5795 MHz',info):
            raise RuntimeError(role+': physical channel/HT40+ readback mismatch')
        power=re.search(r'txpower (-?[0-9.]+) dBm',info)
        if not power or abs(float(power[1]))!=12:
            raise RuntimeError(role+': physical TX power readback mismatch')
        self.save('nodes/'+role+'/radio.json',dict(interface=interface,channel=157,channel_width='HT40+',
             radio_txpower_dbm=abs(float(power[1])),source=str(path.relative_to(self.archive))))

    def radio_events(self):
        result=json.loads((self.archive/'radio/result.json').read_text())
        sid=result['session_id']
        events=[]
        specs=[('PREPARE',0,'PREPARE',157),('LEASE_ARM',2,'LEASE_ARM',157),
               ('COMMIT',2,'COMMIT session=',149),('SERVER_ROLLBACK',0,'回退至 Channel',157),
               ('LEASE_TIMEOUT',2,'射频租约耗尽',157)]
        for event,n,token,ch in specs:
            source='nodes/'+('server' if n==0 else 'client'+str(n))+'/daemon.log'
            lines=(self.archive/source).read_text().splitlines()
            matching=[line for line in lines if sid in line and token in line]
            if len(matching)!=1: raise RuntimeError('missing/ambiguous radio event '+event)
            line=matching[0]
            record=dict(session_id=sid,event=event,node_id=n,channel=ch,timestamp=float(line.split()[0]),source=source,line=line)
            if event=='LEASE_ARM':
                lease=re.search(r'lease_seconds=([0-9.]+)',line)
                if not lease: raise RuntimeError('missing actual lease timeout')
                record['lease_seconds']=float(lease[1])
            events.append(record)
        line=(self.archive/'radio/status.jsonl').read_text().strip()
        observed=json.loads(line)['observed_at']
        for n in (1,2):
            events.append(dict(session_id=sid,event='IDLE_READY',node_id=n,channel=157,timestamp=observed,
                               source='radio/status.jsonl',line=line))
        (self.archive/'radio/events.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in events))

    def stop_services(self):
        errors=[]
        for role in ROLES:
            try:
                self.executor.checked(role,'sudo -n systemctl stop '+self.lifecycle.unit_for_role(role))
            except Exception as exc:
                errors.append(role+': '+str(exc))
        deadline=time.monotonic()+20
        while True:
            stopped={}
            for role in ROLES:
                try:
                    stopped[role]=self.resources(role,stopped=True)
                except Exception as exc:
                    stopped[role]=dict(clean=False,error=str(exc),processes=[],tuns=[])
                    errors.append(role+': '+str(exc))
            self.save('stopped-resources.json',stopped)
            if all('error' not in x for x in stopped.values()):
                self.save('resources/stopped.json',self.resource_report(stopped))
            if all(x['clean'] for x in stopped.values()):
                break
            if time.monotonic()>=deadline or any('error' in x for x in stopped.values()):
                raise RuntimeError('resources survive shutdown or inspection failed: '+str(errors))
            time.sleep(.25)
        if errors:
            raise RuntimeError('service stop commands failed: '+str(errors))

    def validate(self):
        from tests.fl_runtime import stage3_archive
        if not (self.archive/'archive_manifest.json').is_file():
            raise RuntimeError('validation requires a sealed archive')
        result=stage3_archive.validate_archive(self.archive)
        self.save('validation.json',result)
        if result.get('status')!='passed':
            raise RuntimeError('archive validation failed: '+str(result))

    def failure_cleanup(self, reason):
        # Order is mandatory; preserve all completed evidence and errors.
        errors=[]
        failed_stage=self.executor.stage
        had_primary= (self.archive/'failure.json').exists()
        primary={'status':'failed','run_id':self.run_id,'stage':failed_stage,'completed_stages':self.meta.get('completed_stages',[]),
                 'category':{'preflight':'environment','install':'tooling','start-services':'environment','run-sync':'runtime','run-radio-recovery':'radio_recovery','collect':'evidence','stop-services':'resource_cleanup','validate':'archive_validation'}.get(failed_stage,'implementation'),
                 'reason':str(reason),'at':time.time()}
        if not (self.archive/'failure.json').exists():
            self.save('failure.json',primary)
        self.executor.stage='stop-services'
        if self.executor.job_window:
            try:
                # Stop the local server cgroup before allowing remote cleanup
                # if REST abort failed. This cannot assist the failed job.
                self.executor.checked('server', 'sudo -n systemctl stop '+self.lifecycle.server_unit)
                self.executor.job_window=False
                self.save('job-window.json',dict(started_at=json.loads((self.archive/'job-accepted.json').read_text())['requested_at'],
                          ended_at=time.time(),outcome='forced-local-stop'))
            except Exception as exc:
                errors.append(str(exc))
        operations=[self.remove_fault]
        if (self.archive/'services-managed.json').exists():
            operations.append(self.stop_services)
        for operation in operations:
            try:
                operation()
            except Exception as exc:
                errors.append(str(exc))
        retry_token=str(time.time_ns()) if had_primary else ''
        log_name='failure-daemon'+('-'+retry_token if retry_token else '')+'.log'
        resource_name='failure-resources'+('-'+retry_token if retry_token else '')+'.json'
        idle_name='failure-idle-link'+('-'+retry_token if retry_token else '')+'.log'
        for role in ROLES:
            node=self.archive/'nodes'/role
            node.mkdir(parents=True,exist_ok=True)
            try:
                self.collect_idle_link(role, 'nodes/' + role + '/' + idle_name)
            except Exception as exc:
                errors.append(role + ': idle link log: ' + str(exc))
            try:
                out=self.executor.checked(role,'sudo -n journalctl -u '+self.lifecycle.unit_for_role(role)
                     +' --since @'+str(self.meta['created_at'])+' --no-pager -o short-unix')
                (node/log_name).write_text(out)
                self.save('nodes/'+role+'/'+resource_name,self.resources(role,stopped=True))
            except Exception as exc:
                errors.append(str(exc))
        self.save('cleanup-attempts/'+retry_token+'.json' if retry_token else 'cleanup-result.json',dict(status='failed',run_id=self.run_id,
                 stage=failed_stage,completed_stages=self.meta.get('completed_stages',[]),
                 category={'preflight':'environment','install':'tooling','start-services':'environment',
                           'run-sync':'runtime','run-radio-recovery':'radio_recovery','collect':'evidence',
                           'stop-services':'resource_cleanup','validate':'archive_validation'}.get(failed_stage,'implementation'),
                 reason=str(reason),cleanup_errors=errors,at=time.time()))


def initialize(root: Path, repo: Path, run_id=None):
    run_id=run_id or 'stage3_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:12]
    validate_path_safe_identifier(run_id, 'run_id')
    job_id=run_id+'_sync'
    # Keep the existing 111-character run limit: job_<id>_role then uses
    # 125 bytes, leaving ample room for evidence's .<id>.<random> names.
    if len(run_id)>111:
        raise ValueError('run_id 超过作业与 evidence 临时目录的安全长度上限 111')
    archive=root.resolve()/run_id
    commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    meta=init_envelope(archive,run_id=run_id,job_id=job_id,commit=commit)
    meta.update(created_at=time.time(),completed_stages=[],
              conclusion_scope='作业运行时无 SSH 依赖',topology={'server':'local','client1':'vm1','client2':'vm2'})
    write_json_atomic(str(archive/'envelope.json'),meta)
    return archive


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=STAGES+('run-all','cleanup'))
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    args=parser.parse_args(argv)
    if args.archive is None:
        if args.stage not in ('run-all','preflight'):
            parser.error('--archive required when continuing a run')
        args.archive=initialize(Path(os.environ.get('STAGE3_ARCHIVE_ROOT',str(args.repo/'tests/logs'))),args.repo)
    print(str(args.archive),flush=True)
    runner=Stage3Runner(args.archive,args.repo)
    def interrupt(signum,frame):
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,signal.SIG_IGN)
        raise InterruptedError('signal '+str(signum))
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,interrupt)
    if args.stage=='cleanup':
        if (args.archive/'archive_manifest.json').exists():
            raise RuntimeError('refusing to change sealed archive during cleanup')
        runner.failure_cleanup(RuntimeError('shell failure/interruption'))
        return 1
    try:
        for stage in STAGES if args.stage=='run-all' else (args.stage,):
            runner.stage(stage)
    except BaseException as exc:
        if not (args.archive/'archive_manifest.json').exists():
            runner.failure_cleanup(exc)
        raise
    return 0


if __name__=='__main__':
    raise SystemExit(main())
