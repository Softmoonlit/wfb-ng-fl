#!/usr/bin/env python3
"""fl04 单节点 4 MiB 射频对照；每次运行后恢复四端常驻服务。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shlex
import signal
import subprocess
import sys
import time
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
NODES = {'fl00': None, 'fl01': '192.168.108.201', 'fl02': '192.168.108.202', 'fl04': '192.168.108.204'}
CLIENTS = ['fl04']
ALL_CLIENTS = ['fl01', 'fl02', 'fl04']
BUNDLE = ['runner.py', 'node_agent.py', 'expected-files.json']


def bundle_sha():
    digest = hashlib.sha256()
    for name in BUNDLE:
        digest.update(name.encode() + b'\0')
        digest.update((HERE / name).read_bytes())
    return digest.hexdigest()


def status():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:9090/api/v1/status', timeout=4) as response:
        return json.load(response)


def idle_gate(value):
    # 701 _reserve_radio_reconfiguration 在锁内将state改为SWITCHING_RADIO，完成后才回IDLE。
    if value.get('server_state') != 'IDLE' or value.get('active_job') is not None:
        raise RuntimeError('有作业/无线事务或非IDLE，拒绝维护')
    radio = value.get('radio')
    if not isinstance(radio, dict):
        raise RuntimeError('无线配置未确认')
    required = {'channel': 157, 'bandwidth_mhz': 40, 'radio_txpower_dbm': 14,
                'downlink_mcs': 3, 'uplink_mcs': 4, 'uftp_rate_kbps': 15000}
    if any(radio.get(key) != expected for key, expected in required.items()):
        raise RuntimeError('server当前不是正常40MHz基线')
    recent = value.get('recent_job')
    if recent is not None and recent.get('recovery_state') != 'ready':
        raise RuntimeError('上一作业资源恢复未确认')


class Session:
    """每节点一个连接，远端代理直接拥有所有子进程；连接EOF触发其finally。"""
    def __init__(self, node, evidence):
        self.node = node
        self.evidence = evidence
        self.broken = False
        self.closed = False
        self.prepared = False
        self.stdout_buffer = b''
        code = (HERE / 'node_agent.py').read_text()
        command = ['/usr/bin/python3', '-I', '-B', '-c', code]
        if node == 'fl00':
            argv = ['sudo', '-n', *command]
        else:
            remote = shlex.join(['sudo', '-n', *command])
            argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                    '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
                    'fl@' + NODES[node], remote]
        self.err = (evidence / (node + '-agent.stderr')).open('xb')
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.err, cwd='/', start_new_session=True)
        self.rpc_log = evidence / (node + '-rpc.jsonl')

    def rpc(self, action, params=None, timeout=12):
        if self.closed or self.broken:
            raise RuntimeError(self.node + '控制连接不可用')
        ident = uuid.uuid4().hex
        request = {'id': ident, 'action': action, 'params': params or {}}
        started = time.monotonic()
        with self.rpc_log.open('a') as log:
            log.write(json.dumps({'direction': 'request', 'mono': started, **request}, ensure_ascii=False) + '\n')
        try:
            self.proc.stdin.write((json.dumps(request) + '\n').encode())
            self.proc.stdin.flush()
            limit = started + timeout
            while b'\n' not in self.stdout_buffer:
                remaining = limit - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(self.node + ':' + action + ' RPC期限')
                ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
                if not ready:
                    raise TimeoutError(self.node + ':' + action + ' RPC期限')
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError(self.node + ' agent EOF')
                self.stdout_buffer += chunk
                if len(self.stdout_buffer) > 8_000_000:
                    raise RuntimeError('响应过大')
            line, self.stdout_buffer = self.stdout_buffer.split(b'\n', 1)
            reply = json.loads(line)
            if reply['id'] != ident:
                raise RuntimeError('RPC响应身份不符')
        except Exception:
            self.broken = True
            # 不能向存在未完成响应的连接继续发命令；EOF交给远端finally回收/恢复。
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            raise
        with self.rpc_log.open('a') as log:
            log.write(json.dumps({'direction': 'response', 'mono': time.monotonic(), **reply}, ensure_ascii=False) + '\n')
        if not reply['ok']:
            raise RuntimeError(self.node + ':' + action + ': ' + reply['error'])
        return reply['result']

    def close(self):
        if self.closed:
            return
        self.closed = True
        if not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=35)
        except subprocess.TimeoutExpired:
            # 只终止自有ssh/Python会话。远端处理HUP/TERM，另有300秒节点闹钟兜底。
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        self.proc.stdout.close()
        self.err.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--reviewed-sha', default='')
    parser.add_argument('--run-id', default='')
    parser.add_argument('--uplink-mcs', type=int, choices=[4, 5], required=True)
    parser.add_argument('--channel', type=int, choices=[149, 157], required=True)
    parser.add_argument('--txpower', type=int, choices=[14, 18], required=True)
    args = parser.parse_args()
    if (args.channel, args.txpower, args.uplink_mcs) not in ((149, 14, 4), (149, 18, 4), (149, 18, 5)):
        raise SystemExit('仅允许本次三组射频对照')
    digest = bundle_sha()
    if not args.execute:
        print(json.dumps({'mode': 'local-review-only', 'bundle_sha256': digest,
                          'files': BUNDLE, 'nodes': NODES, 'payload_bytes': 4194304,
                          'active_clients': [4],
                          'mutations': '备份四端、停服、临时应用指定信道和功率、仅fl00/fl04传输、finally恢复原服务'},
                         ensure_ascii=False, indent=2))
        return 0
    if args.reviewed_sha != digest:
        raise SystemExit('reviewed-sha与当前脚本不符，拒绝执行；先由主线代码审查')
    run_id = args.run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,70}', run_id):
        raise SystemExit('run-id非法')
    evidence = HERE / 'runs' / run_id
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    lock = (HERE / 'owner.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    sessions = {}
    facts = {'run_id': run_id, 'bundle_sha256': digest, 'expected_nodes': [4],
             'recovery_nodes': [1, 2, 4],
             'payload_bytes': 4194304, 'channel': args.channel, 'txpower': args.txpower, 'uplink_mcs': args.uplink_mcs, 'status': 'preparing', 'errors': [], 'recovery_errors': []}

    def save(phase):
        facts['phase'] = phase
        facts['updated_utc'] = datetime.now(timezone.utc).isoformat()
        (evidence / 'result.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'phase': phase, 'status': facts['status'], 'evidence': str(evidence)}, ensure_ascii=False), flush=True)

    def interrupted(signum, frame):
        raise SystemExit(128 + signum)

    for signum in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]:
        signal.signal(signum, interrupted)
    original = signal.signal(signal.SIGALRM, interrupted)
    expected = json.loads((HERE / 'expected-files.json').read_text())['installed_fl_hashes']
    try:
        first = status()
        idle_gate(first)
        (evidence / 'status-before.json').write_text(json.dumps(first, ensure_ascii=False, indent=2) + '\n')
        save('preflight-backup')
        snapshots = {}
        # 全部四端prepare/备份成功后才允许任何停服。任一SSH/身份失败不skip。
        for node in NODES:
            session = Session(node, evidence)
            sessions[node] = session
            snapshots[node] = session.rpc('prepare', {'run_id': run_id, 'expected_hashes': expected, 'payload_bytes': 4194304}, timeout=20)
            session.prepared = True
        facts['snapshots'] = snapshots
        (evidence / 'snapshots.json').write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + '\n')
        idle_gate(status())  # 最后一个停服前门禁，server state IDLE排除701无线事务。
        facts['status'] = 'maintenance'
        save('stop-clients-then-server')
        signal.alarm(360)  # 整段维护流程有界；finally另有独立恢复期限。
        for node in ALL_CLIENTS + ['fl00']:
            sessions[node].rpc('stop_original', timeout=30)
        save('explicit-channel-and-power')
        for node in NODES:
            sessions[node].rpc('align_channel', {'channel': args.channel, 'txpower_dbm': args.txpower}, timeout=10)
        save('start-standalone-from-actual-proc-argv')
        facts['standalone'] = {}
        for node in ['fl00'] + CLIENTS:
            facts['standalone'][node] = sessions[node].rpc('start_native', {'uplink_mcs': args.uplink_mcs if node in CLIENTS else None, 'active_clients': [4]}, timeout=12)
        for node in CLIENTS:
            sessions[node].rpc('receiver', timeout=8)
        save('single-downlink-4MiB-fl04')
        source = sessions['fl00'].rpc('downlink', timeout=50)
        facts['source'] = source
        facts['downlink'] = {}
        for node in CLIENTS:
            facts['downlink'][node] = sessions[node].rpc('verify_received', {'expected': source}, timeout=18)
        if set(facts['downlink']) != set(CLIENTS):
            raise RuntimeError('下行目标集合不完整')
        save('single-uplink-fl04')
        sessions['fl00'].rpc('http_receiver', timeout=8)
        facts['uplink'] = {}
        # 不同节点连接独立，可并发回传；一次请求，不重试/不skip。
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(sessions[node].rpc, 'upload', {'expected': source}, 170): node for node in CLIENTS}
            for future in as_completed(futures):
                node = futures[future]
                facts['uplink'][node] = future.result()
        if set(facts['uplink']) != set(CLIENTS):
            raise RuntimeError('上行目标集合不完整')
        facts['server_files'] = sessions['fl00'].rpc('verify_uploads', {'expected': source}, timeout=8)
        facts['status'] = 'transfer-passed-recovery-pending'
        save('single-transfer-finished')
    except BaseException as exc:
        facts['status'] = 'failed'
        facts['errors'].append(repr(exc))
        save('failure-no-retry')
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, original)
        # 所有节点先清自有资源，再server先恢复，最后client；失败也尽力全部恢复并单列错误。
        save('cleanup-owned-pids-only')
        for node, session in sessions.items():
            if session.prepared and not session.broken:
                try:
                    session.rpc('cleanup', timeout=22)
                except Exception as exc:
                    facts['recovery_errors'].append(node + ':cleanup:' + repr(exc))
        save('restore-original-server-then-clients')
        for node in ['fl00'] + ALL_CLIENTS:
            session = sessions.get(node)
            if session is None:
                continue
            if session.prepared and not session.broken:
                try:
                    report = session.rpc('collect', timeout=12)
                    (evidence / (node + '-before-restore.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
                    session.rpc('restore', timeout=30)
                    report = session.rpc('collect', timeout=12)
                    (evidence / (node + '-collected.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
                    session.rpc('finish', timeout=5)
                except Exception as exc:
                    facts['recovery_errors'].append(node + ':restore/collect:' + repr(exc))
            else:
                facts['recovery_errors'].append(node + ':connection failed/unprepared; remote EOF/alarm finally is fallback, not confirmed recovery')
        # 关闭stdin使各agent finally再次校验清理/恢复；逐个wait回收本脚本拥有的会话。
        for node, session in sessions.items():
            try:
                session.close()
            except Exception as exc:
                facts['recovery_errors'].append(node + ':session-close:' + repr(exc))
        samples = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                value = status()
                samples.append({'mono': time.monotonic(), 'state': value})
                if (value.get('server_state') == 'IDLE' and value.get('active_job') is None
                        and all(value.get('nodes', {}).get(str(n), {}).get('readiness') == 'READY' for n in [1, 2, 4])):
                    break
            except Exception as exc:
                samples.append({'mono': time.monotonic(), 'error': repr(exc)})
            time.sleep(2)
        (evidence / 'recovery-api-samples.json').write_text(json.dumps(samples, ensure_ascii=False, indent=2) + '\n')
        last = samples[-1].get('state', {}) if samples else {}
        facts['final_ready'] = bool(last.get('server_state') == 'IDLE' and last.get('active_job') is None
                                   and all(last.get('nodes', {}).get(str(n), {}).get('readiness') == 'READY' for n in [1, 2, 4]))
        if not facts['final_ready']:
            facts['recovery_errors'].append('API未在恢复观察期限内确认全员READY')
        if facts['status'] == 'transfer-passed-recovery-pending':
            facts['status'] = 'diagnostic-transfer-and-recovery-passed' if not facts['recovery_errors'] else 'transfer-passed-recovery-unconfirmed'
        save('finished')
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return 0 if facts['status'] == 'diagnostic-transfer-and-recovery-passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
