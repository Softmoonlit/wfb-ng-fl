#!/usr/bin/env python3
"""Issue08 射频实机补充验收；仅显式 --execute 后发送请求。

从 Server 仓库执行，使用管理网 Web 和模型库已有 canonical 40 MiB 文件。
每次变更通过 Web validate/apply 确认令牌，不等待人工输入。
创建独立新归档，不恢复或覆盖已有 Stage3/4 成功归档。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from tests.fl_runtime.hardware import inspect_wireless_device
from tests.fl_runtime.evidence_collection import collect_tree
from tests.fl_runtime.stage3_runner import fault_rules
from tests.fl_runtime.stage4_hardware_acceptance import (
    CANONICAL_MODEL_SHA256, Stage4HardwareRunner, _request,
)
from tests.fl_runtime.stage4_hardware_archive import validate_terminal_state, validate_transfer_start
from wfb_ng.fl.artifacts import write_json_atomic

ROOT = Path(__file__).resolve().parents[2]
BASELINE = dict(channel=157, radio_txpower_dbm=12, downlink_mcs=3,
                uplink_mcs=6, uftp_rate_kbps=15000)
CHANGED = dict(channel=157, radio_txpower_dbm=11, downlink_mcs=4,
               uplink_mcs=5, uftp_rate_kbps=18000)

# Read live process argv, not ps text containing its own search expression.
PROCESS_PROBE = """import json,pathlib
rows=[]
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdigit(): continue
 try:
  comm=(p/'comm').read_text().strip()
  if comm != 'wfb_v6_uplink': continue
  argv=(p/'cmdline').read_bytes().decode().rstrip('\\0').split('\\0')
  stat=(p/'stat').read_text().rsplit(')',1)[1].split()
  if stat[0]=='Z': continue
  rows.append(dict(pid=int(p.name),start_ticks=int(stat[19]),argv=argv))
 except (OSError,UnicodeError,ValueError): continue
print(json.dumps(rows))"""


def ready(state, nodes):
    registered = [n for n in state['nodes'] if n.get('reported_state') is not None]
    return (state['current_job'] is None and not state['server']['start_blockers']
            and {n['node_id'] for n in registered} == set(nodes)
            and all(n['state'] == 'idle' and n['readiness'] == 'READY'
                    and n['last_heartbeat_ago_seconds'] is not None
                    and n['last_heartbeat_ago_seconds'] <= 10 for n in registered))


def option(argv, key):
    if argv.count(key) != 1 or argv.index(key) + 1 == len(argv):
        raise ValueError(f'missing/duplicate {key}: {argv}')
    return argv[argv.index(key) + 1]


def validate_observation(observation, config, node):
    if observation['daemon_pid'] <= 0:
        raise ValueError('daemon is not running')
    processes = observation['processes']
    if len(processes) != 1:
        raise ValueError('expected exactly one live idle link process')
    argv = processes[0]['argv']
    for key, expected in {'--radio-mcs-index': config['uplink_mcs'] if node else config['downlink_mcs'],
                          '--radio-bandwidth': 20 if config['channel'] == 165 else 40}.items():
        if option(argv, key) != str(expected):
            raise ValueError(f'actual link {key} mismatch')
    iw = observation['iw']
    width = 20 if config['channel'] == 165 else 40
    center = 5825 if config['channel'] == 165 else 5795
    if not re.search(rf'channel {config["channel"]} .*width: {width} MHz.*center1: {center} MHz', iw):
        raise ValueError('iw channel/width/center readback mismatch')
    power = re.search(r'txpower (-?[0-9.]+) dBm', iw)
    if power is None or abs(float(power[1])) != config['radio_txpower_dbm']:
        raise ValueError('iw power readback mismatch')
    endpoints = {line.split()[3] for line in observation['sockets'].splitlines() if len(line.split()) >= 5}
    expected = {'0.0.0.0:9000', '*:9000'} if node else {'10.80.0.1:9001'}
    control = {endpoint for endpoint in endpoints if endpoint.rsplit(':', 1)[-1] in ('9000', '9001')}
    if not control or not control <= expected:
        raise ValueError('control socket bind mismatch')
    if len(observation['tun']) != 1 or observation['tun'][0]['ifindex'] <= 0:
        raise ValueError('missing idle TUN ifindex')


def validate_rebuild(before, after):
    for role in before:
        old, new = before[role], after[role]
        if (old['processes'][0]['pid'], old['processes'][0]['start_ticks']) == (
                new['processes'][0]['pid'], new['processes'][0]['start_ticks']):
            raise ValueError(f'{role}: link process did not rebuild')
        if old['tun'][0]['ifindex'] == new['tun'][0]['ifindex']:
            raise ValueError(f'{role}: TUN did not rebuild')


class Executor:
    def __init__(self, archive, clients):
        self.archive, self.clients = archive, clients
        self.job_window = False

    def run(self, role, command, timeout=30):
        if role != 'server' and self.job_window:
            raise RuntimeError('SSH forbidden during job window')
        argv = ['bash', '-lc', command] if role == 'server' else [
            'ssh', '-o', 'BatchMode=yes', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
            '-o', 'ConnectTimeout=10', self.clients[role], command]
        started = time.time()
        try:
            result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            with (self.archive / 'summary/orchestration.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(role=role, command=command, started_at=started,
                                             ended_at=time.time(), returncode=124)) + '\n')
            raise
        with (self.archive / 'summary/orchestration.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(role=role, command=command, started_at=started,
                                         ended_at=time.time(), returncode=result.returncode)) + '\n')
        return result.returncode, result.stdout, result.stderr

    def checked(self, role, command, timeout=30):
        code, out, err = self.run(role, command, timeout)
        if code:
            raise RuntimeError(f'{role}: {code}: {err or out}')
        return out


class RadioRunner:
    def __init__(self, archive, web_url, clients):
        self.archive, self.web_url = archive, web_url
        self.clients = {f'client{node}': host for node, host in clients.items()}
        self.nodes = sorted(clients)
        self.roles = {'server': 0, **{f'client{node}': node for node in self.nodes}}
        self.executor = Executor(archive, self.clients)
        self.run_id = archive.name
        self.started = time.time()

    def save(self, path, value):
        write_json_atomic(self.archive / path, value)

    def web(self, path, body=None, *, headers=None, timeout=60):
        status, response = _request(self.web_url, '/api/v1/' + path,
            method='GET' if body is None else 'POST',
            body=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', **(headers or {})}, timeout=timeout)
        with (self.archive / 'management-web/requests.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(at=time.time(), path=path, request=body,
                                         status=status, response=response)) + '\n')
        return response

    def wait_ready(self, config, name, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.web('state')
            config_matches = all(state['server']['radio'][k] == v for k, v in config.items())
            node_matches = all(n.get('current_channel') == config['channel']
                               and n.get('txpower_dbm') == config['radio_txpower_dbm']
                               and n.get('uplink_mcs') == config['uplink_mcs']
                               for n in state['nodes'] if n['node_id'] in self.nodes)
            if ready(state, self.nodes) and config_matches and node_matches:
                self.save(f'control-plane/{name}-ready.json', state)
                return state
            time.sleep(.5)
        raise TimeoutError(f'{name}: READY/config timeout')

    def capture(self, name, config):
        self.wait_ready(config, name)
        rows = {}
        for role, node in self.roles.items():
            device = inspect_wireless_device(self.executor, role)
            tun = f'fl-c{node}' if node else 'fl-s'
            unit = 'wfb-fl-client-daemon.service' if node else 'wfb-fl-server-daemon.service'
            rows[role] = dict(observed_at=time.time(), wireless=device,
                daemon_pid=int(self.executor.checked(role, f'systemctl show {unit} --property MainPID --value')),
                processes=json.loads(self.executor.checked(role, 'sudo -n python3 -I -c ' + shlex.quote(PROCESS_PROBE))),
                tun=json.loads(self.executor.checked(role, 'ip -j link show dev ' + tun)),
                iw=self.executor.checked(role, 'iw dev ' + shlex.quote(device['interface']) + ' info'),
                sockets=self.executor.checked(role, 'sudo -n ss -H -lunp'))
            self.save(f'lifecycle/{name}-{role}.json', rows[role])
            validate_observation(rows[role], config, node)
        return rows

    def apply(self, config, name, expected='finalized'):
        print(f'{name}: 管理 Web 射频配置 {json.dumps(config)}; 预期 {expected}', flush=True)
        validated = self.web('radio/config/validate', {'config': config})
        if validated['warning']:
            raise RuntimeError('acceptance only uses safe rates; unexpected warning')
        result = self.web('radio/config/apply', {
            'confirmation_token': validated['confirmation']['token'], 'confirm_risk': False})
        self.save(f'control-plane/{name}-result.json', result)
        if result.get('status') != expected:
            raise ValueError(f'{name}: unexpected radio transaction: {result}')
        return result

    def rollback(self):
        # Reuse the EXACT Stage3 fault; fl-c2 is intentionally not generalized.
        if 2 not in self.nodes:
            raise ValueError('Stage3 controlled fault requires node_id=2 / fl-c2')
        self.executor.checked('client2', fault_rules('probe', self.run_id))
        start = time.time()
        self.save('control-plane/fault.json', dict(run_id=self.run_id, installed=True, started_at=start))
        try:
            self.executor.checked('client2', fault_rules('install', self.run_id))
            result = self.apply({**CHANGED, 'channel': 149}, 'rollback', 'rolled_back')
            if result.get('failed_phase') != 'COMMIT' or result.get('unresponsive_nodes') != [2]:
                raise ValueError('fault did not match Stage3 precommit rollback')
            session = result['session_id']
            deadline = time.monotonic() + 35
            while True:
                matches = []
                for node in self.nodes:
                    journal = self.executor.checked(f'client{node}',
                        f'sudo -n journalctl -u wfb-fl-client-daemon.service --since @{start} --no-pager -o short-unix')
                    source = f'control-plane/rollback-client{node}-journal.txt'
                    (self.archive / source).write_text(journal)
                    matches.extend(dict(lease_node=node, line=line, source=source, session_id=session)
                                   for line in journal.splitlines() if session in line and '射频租约耗尽' in line)
                if matches:
                    if len(matches) != 1:
                        raise ValueError('ambiguous session-bound lease expiry')
                    self.save('control-plane/lease-expiry.json', matches[0])
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('missing session-bound Stage3 lease expiry evidence')
                time.sleep(.5)
            self.capture('rollback-restored', CHANGED)
        finally:
            self.executor.checked('client2', fault_rules('remove', self.run_id))
            rules = self.executor.checked('client2', fault_rules('observe', self.run_id))
            self.save('control-plane/fault.json', dict(run_id=self.run_id, installed=False, rules=rules))
            if self.run_id in rules:
                raise RuntimeError('fault rules remain')

    def rate_job(self, digest):
        body = dict(model_sha256=digest, target_nodes=self.nodes, rounds=2)
        print(f'管理 Web 启动两轮作业: {json.dumps(body)}', flush=True)
        self.executor.job_window = True
        # Stable key lets the parent resolve ambiguous HTTP acceptance without a new job.
        self.save('data-plane/job-request.json', dict(request=body, idempotency_key=self.run_id))
        accepted = self.web('jobs', body, headers={'Idempotency-Key': self.run_id})
        job_id = accepted['job_id']
        deadline = time.monotonic() + 600
        transfer = None
        while time.monotonic() < deadline:
            if transfer is None:
                transfer = Stage4HardwareRunner._sample_uftp(self, job_id)
                if transfer:
                    self.save('data-plane/uftp-argv.json', transfer)
            state = self.web('state')
            recent = state.get('recent_job') or {}
            if state['current_job'] is None and recent.get('job_id') == job_id and recent.get('recovery_state') == 'ready':
                self.save('data-plane/job-terminal.json', state)
                self.executor.job_window = False
                validate_terminal_state(state, job_id, self.nodes, aborted=False)
                break
            time.sleep(.05)
        else:
            raise TimeoutError('job not recovered; SSH remains forbidden, parent must use Web abort/state')
        # Archive actual production logs after recovery, independent of argv validation.
        collect_tree(self.executor, 'server', f'/tmp/wfb-ng-fl/server/job_{job_id}_role',
                     self.archive / 'data-plane/server-role')
        logs = list((self.archive / 'data-plane/server-role').rglob('uftp-*.log'))
        if not logs or not any(p.stat().st_size for p in logs):
            raise ValueError('no real UFTP logs')
        if transfer is None or option(transfer['argv'], '-R') != str(CHANGED['uftp_rate_kbps']):
            raise ValueError('missing job-bound real UFTP argv with confirmed -R')
        self.capture('post-job', CHANGED)

    def run(self, digest, include_rollback):
        commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
        for role, node in self.roles.items():
            head = self.executor.checked(role, f'git -C {shlex.quote(str(ROOT))} rev-parse HEAD').strip()
            dirty = self.executor.checked(role, f'git -C {shlex.quote(str(ROOT))} status --porcelain').strip()
            self.save(f'summary/{role}-checkout.json', dict(commit=head, dirty=dirty))
            if head != commit or dirty:
                raise RuntimeError(f'{role}: formal evidence requires commit parity and clean worktree')
            if node:
                identity = json.loads(self.executor.checked(role, 'sudo -n cat /etc/wfb-ng-fl/node.json'))
                self.save(f'summary/{role}-identity.json', identity)
                if identity['node_id'] != node:
                    raise ValueError('node identity mismatch')
        before = self.capture('baseline', BASELINE)
        for name, config in [('mcs-power', CHANGED), ('165-HT20', {**CHANGED, 'channel': 165}), ('157-HT40', CHANGED)]:
            self.apply(config, name)
            after = self.capture(name, config)
            validate_rebuild(before, after)
            before = after
        if include_rollback:
            self.rollback()
        self.rate_job(digest)
        self.apply(BASELINE, 'restore-baseline')
        self.capture('restored-baseline', BASELINE)
        for role, node in self.roles.items():
            unit = 'wfb-fl-client-daemon.service' if node else 'wfb-fl-server-daemon.service'
            journal = self.executor.checked(role,
                f'sudo -n journalctl -u {unit} --since @{self.started} --no-pager -o short-unix', timeout=60)
            (self.archive / f'control-plane/{role}-journal.txt').write_text(journal)
        return dict(status='passed', commit=commit, rollback='passed' if include_rollback else 'not_run',
                    scope='Issue08 supplementary radio evidence only')



def recheck(archive):
    """只读取已归档事实，重新判断射频、重建、故障回退和真实 -R。"""
    def read(name):
        return json.loads((archive / name).read_text())
    request = read('summary/request.json')
    nodes = sorted(int(n) for n in request['clients'])
    roles = {'server': 0, **{f'client{n}': n for n in nodes}}
    previous = None
    steps = [('baseline', BASELINE), ('mcs-power', CHANGED),
             ('165-HT20', {**CHANGED, 'channel': 165}), ('157-HT40', CHANGED)]
    if request['include_rollback']:
        steps.append(('rollback-restored', CHANGED))
    steps.extend([('post-job', CHANGED), ('restored-baseline', BASELINE)])
    for name, config in steps:
        state = read(f'control-plane/{name}-ready.json')
        if not ready(state, nodes) or any(state['server']['radio'][k] != v for k, v in config.items()):
            raise ValueError(f'{name}: archived READY/config mismatch')
        for node in state['nodes']:
            if node['node_id'] in nodes and (node['current_channel'], node['txpower_dbm'], node['uplink_mcs']) != (
                    config['channel'], config['radio_txpower_dbm'], config['uplink_mcs']):
                raise ValueError(f'{name}: archived client config mismatch')
        rows = {role: read(f'lifecycle/{name}-{role}.json') for role in roles}
        for role, node in roles.items():
            validate_observation(rows[role], config, node)
        if name in ('mcs-power', '165-HT20', '157-HT40'):
            validate_rebuild(previous, rows)
            result = read(f'control-plane/{name}-result.json')
            if result['status'] != 'finalized' or any(result['effective_config'][k] != v for k, v in config.items()):
                raise ValueError('archived finalized result mismatch')
        previous = rows
    if request['include_rollback']:
        result = read('control-plane/rollback-result.json')
        if result['status'] != 'rolled_back' or result['failed_phase'] != 'COMMIT' or result['unresponsive_nodes'] != [2]:
            raise ValueError('archived rollback mismatch')
        if any(result['effective_config'][k] != v for k, v in CHANGED.items()):
            raise ValueError('rollback did not preserve previous MCS/power/rate')
        lease = read('control-plane/lease-expiry.json')
        if lease['lease_node'] not in nodes or lease['session_id'] != result['session_id']:
            raise ValueError('lease session/node mismatch')
        if lease['line'] not in (archive / lease['source']).read_text().splitlines() or '射频租约耗尽' not in lease['line'] or result['session_id'] not in lease['line']:
            raise ValueError('lease lacks raw journal evidence')
        fault = read('control-plane/fault.json')
        if fault['installed'] or request['run_id'] in fault['rules']:
            raise ValueError('fault rules remain')
    terminal = read('data-plane/job-terminal.json')
    job_id = terminal['recent_job']['job_id']
    validate_terminal_state(terminal, job_id, nodes, aborted=False)
    transfer = read('data-plane/uftp-argv.json')
    validate_transfer_start(transfer, job_id)
    if option(transfer['argv'], '-R') != str(CHANGED['uftp_rate_kbps']):
        raise ValueError('archived actual -R mismatch')
    log = archive / 'data-plane/server-role' / Path(option(transfer['argv'], '-L')).relative_to(
        Path('/tmp/wfb-ng-fl/server') / ('job_' + job_id + '_role'))
    if not log.is_file() or not log.stat().st_size:
        raise ValueError('actual UFTP argv lacks original log')
    seal_path = archive / 'summary/seal.json'
    if seal_path.exists():
        files = {str(p.relative_to(archive)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in archive.rglob('*') if p.is_file() and p != seal_path}
        if files != read('summary/seal.json')['files']:
            raise ValueError('archive hash inventory mismatch')
    return {'status': 'passed', 'scope': 'Issue08 supplementary radio evidence only'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='明确授权执行全部射频变更及两轮 Web 作业')
    parser.add_argument('--recheck', type=Path, help='仅离线重新判断已有补充归档，不访问实机')
    parser.add_argument('--web-url', help='显式管理网地址，例如 http://192.168.1.1:8080')
    parser.add_argument('--client', action='append', metavar='NODE_ID=SSH_HOST',
                        help='重复指定客户端，例如 --client 1=vm1 --client 2=vm2')
    parser.add_argument('--model-sha256', default=CANONICAL_MODEL_SHA256, help='模型库已有40 MiB模型SHA，默认canonical文件')
    parser.add_argument('--include-rollback', action='store_true', help='复用Stage3 fl-c2受控故障并验证149失败回退157')
    parser.add_argument('--archive-root', type=Path, default=ROOT / 'tests/logs', help='新补充归档的父目录，不覆盖既有归档')
    args = parser.parse_args(argv)
    if args.recheck:
        print(json.dumps(recheck(args.recheck), ensure_ascii=False))
        return 0
    if not args.web_url or not args.client:
        parser.error('执行必须提供 --web-url 和 --client')
    if not args.execute:
        parser.error('仅帮助/检查不会执行；实机请求必须显式指定 --execute')
    url = urlparse(args.web_url)
    if url.scheme != 'http' or not url.hostname or url.hostname in ('localhost', '127.0.0.1', '0.0.0.0', '10.80.0.1') or url.port != 8080:
        parser.error('必须使用显式管理网IP和8080端口')
    clients = {}
    for entry in args.client:
        node, host = entry.split('=', 1)
        node = int(node)
        if node <= 0 or node in clients or not re.fullmatch(r'[A-Za-z0-9_.@-]+', host) or host.startswith('-'):
            parser.error('invalid/duplicate client mapping')
        clients[node] = host
    if not re.fullmatch(r'[0-9a-f]{64}', args.model_sha256):
        parser.error('invalid model SHA-256')
    run_id = 'stage4-radio-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
    archive = args.archive_root / run_id
    archive.mkdir(parents=True, exist_ok=False)
    for part in ('management-web', 'control-plane', 'data-plane', 'lifecycle', 'summary'):
        (archive / part).mkdir()
    runner = RadioRunner(archive, args.web_url, clients)
    runner.save('summary/request.json', dict(run_id=run_id, web_url=args.web_url, clients=clients,
                model_sha256=args.model_sha256, include_rollback=args.include_rollback))
    try:
        verdict = runner.run(args.model_sha256, args.include_rollback)
        recheck(archive)
    except (Exception, KeyboardInterrupt) as exc:
        verdict = dict(status='failed', error=str(exc), job_window_open=runner.executor.job_window,
                       recovery='Use Web state/abort if job_window_open; inspect archived fault intent before cleanup')
    runner.save('summary/verdict.json', verdict)
    inventory = {str(p.relative_to(archive)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in archive.rglob('*') if p.is_file()}
    runner.save('summary/seal.json', dict(run_id=run_id, started_at=runner.started, ended_at=time.time(), files=inventory))
    print(json.dumps(dict(archive=str(archive), **verdict), ensure_ascii=False))
    return 0 if verdict['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
