#!/usr/bin/env python3
"""九客户端通信：模型制品或显式 1MiB 合成冒烟；默认仅生成本地计划。"""
import argparse
import concurrent.futures as futures
import hashlib
import json
import inspect
import re
from pathlib import Path
import queue
import shlex
import signal
import subprocess
import threading
import time

NODES = [1, 2, 3, 5, 6, 7, 8, 9, 10]
HOSTS = ['fl00'] + [f'fl{node:02d}' for node in NODES]
DIRECTORY = Path(__file__).resolve().parent
RELEASE_COMMIT = 'cccac233291c5a79ca7a66bb2ebb210497ab4071'
RELEASE_ROOT = '/srv/wfb-ng-fl/releases/heartbeat-cccac233291c-20261109T183910Z'
# 所有跨层期限在此定义；节点只消费 prepare 下发的值。
DIRECTION_TIMEOUT = 600
ORCHESTRATION_TIMEOUT = 2 * DIRECTION_TIMEOUT + 90
TIMING = dict(io_timeout=DIRECTION_TIMEOUT, orchestration_timeout=ORCHESTRATION_TIMEOUT,
              node_timeout=ORCHESTRATION_TIMEOUT + 15,
              recovery_budget=60, cleanup_timeout=10, unit_stop_timeout=8, close_restore_reserve=40)
PATCH_DEFAULT = dict(channel=157, bandwidth_mhz=40, downlink_mcs=3, uplink_mcs=4,
                     radio_txpower_dbm=18, uftp_rate_kbps=12000)


def payload_info(path):
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError('payload 必须是本机普通文件')
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(chunk)
            if size > 64 * 1024 * 1024:
                raise ValueError('payload 大小必须为 1..64MiB 字节')
            digest.update(chunk)
    if not 1 <= size <= 64 * 1024 * 1024:
        raise ValueError('payload 大小必须为 1..64MiB 字节')
    return dict(path=str(path), size_bytes=size, sha256=digest.hexdigest())


def model_manifest_info(path, payload):
    path = path.resolve(strict=True)
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get('model_format') != 'cifar10-resnet18-fp32-v1':
        raise ValueError('model manifest format mismatch')
    if type(value.get('size_bytes')) is not int or value['size_bytes'] != payload['size_bytes']:
        raise ValueError('model manifest size mismatch')
    if value.get('sha256') != payload['sha256']:
        raise ValueError('model manifest SHA mismatch')
    initialization = value.get('initialization')
    if not isinstance(initialization, dict):
        raise ValueError('model manifest initialization required')
    seed, source = initialization.get('seed'), initialization.get('source')
    if type(seed) is not int or not 0 <= seed <= 2**63 - 1:
        raise ValueError('model manifest seed invalid')
    if not isinstance(source, str) or not source.strip():
        raise ValueError('model manifest source invalid')
    return dict(path=str(path), content=value)


def audit_final(sessions, record, deadline):
    """十端审计必须全部按时通过；射频 HTTP 成功不能替代最终状态证据。"""
    if set(sessions) != set(HOSTS):
        return False
    pending = {h: sessions[h].call('audit_final') for h in HOSTS}
    ok = True
    for host, future in pending.items():
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('final audit deadline')
            result = future.result(timeout=remaining)
            if time.monotonic() >= deadline or result.get('ok') is not True:
                raise RuntimeError('final audit failed/late')
            record(dict(phase='audit_final', host=host, ok=True, result=result))
        except Exception as exc:
            ok = False
            record(dict(phase='audit_final', host=host, ok=False, error=repr(exc)))
    return ok


def bundle_sha():
    digest = hashlib.sha256()
    for name in ['runner.py', 'node_agent.py', 'recover.py']:
        digest.update(name.encode() + b'\0' + (DIRECTORY / name).read_bytes())
    return digest.hexdigest()


class Session:
    """每个节点独立串行 RPC 工作线程，读取响应受整体期限约束。"""
    def __init__(self, host, code, root, deadline):
        self.host, self.deadline = host, deadline
        self.worker = futures.ThreadPoolExecutor(max_workers=1)
        self.responses = queue.Queue()
        self.serial = 0
        self.stderr = (root / (host + '.stderr')).open('wb')
        unit = 'comm-smoke-' + root.name + '-' + host
        # 临时 unit 只拥有本代理及其后代；退出或期限到达时回收剩余子进程。
        command = ['systemd-run', '--quiet', '--pipe', '--wait', '--collect', '--service-type=exec',
                   '--unit=' + unit, '--property=KillMode=control-group',
                   '--property=RuntimeMaxSec=' + str(TIMING['node_timeout']),
                   '--property=TimeoutStopSec=' + str(TIMING['unit_stop_timeout']),
                   '/usr/bin/python3', '-I', '-B', '-u', '-c', code]
        argv = ['sudo', '-n', *command] if host == 'fl00' else [
            'ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=5', host,
            'sudo -n ' + shlex.join(command)]
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.stderr, text=True, bufsize=1)
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            for line in self.process.stdout:
                self.responses.put(json.loads(line))
        except Exception as exc:
            self.responses.put(exc)
        finally:
            self.responses.put(EOFError('SSH EOF: ' + self.host))

    def call(self, action, **params):
        def rpc():
            self.serial += 1
            ident = self.serial
            self.process.stdin.write(json.dumps(dict(id=ident, action=action, params=params)) + '\n')
            self.process.stdin.flush()
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('overall deadline')
            response = self.responses.get(timeout=remaining)
            if isinstance(response, Exception):
                raise response
            if response.get('id') != ident or not response.get('ok'):
                raise RuntimeError(str(response))
            return response['result']
        return self.worker.submit(rpc)

    def dispose(self):
        # EOF 触发节点 finally；这里只终止自有会话，临时 unit 负责后代清理。
        self.responses.put(EOFError('session disposed: ' + self.host))
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.process.stdin.close()
        try:
            self.process.wait(timeout=max(.1, min(5, self.deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.stderr.close()


def scan_upload_progress(run_root):
    """只读本轮固定层级；缺失文件不代表已接收零字节。"""
    from pathlib import Path
    import stat

    def directories(parent):
        try:
            return [path for path in parent.iterdir()
                    if not path.is_symlink() and path.is_dir()]
        except FileNotFoundError:
            return []

    root = Path(run_root)
    role, rounds = root / 'role', root / 'role' / 'rounds'
    if any(path.is_symlink() for path in (root, role, rounds)):
        raise ValueError('symlink run directory')
    records = []
    for round_dir in directories(rounds):
        updates = round_dir / 'updates'
        if updates.is_symlink():
            continue
        observed = {path.name: path for path in directories(updates)}
        for node in (1, 2, 3, 5, 6, 7, 8, 9, 10):
            partial, committed_bytes = [], None
            node_dir = observed.get(str(node))
            if node_dir is not None:
                try:
                    for path in node_dir.iterdir():
                        if path.name != 'update.bin' and not path.name.startswith('.update.bin.'):
                            continue
                        try:
                            info = path.lstat()
                        except FileNotFoundError:
                            continue
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        if path.name == 'update.bin':
                            committed_bytes = info.st_size
                        else:
                            partial.append(info.st_size)
                except FileNotFoundError:
                    pass
            sizes = partial + ([committed_bytes] if committed_bytes is not None else [])
            records.append(dict(round=round_dir.name, node=node,
                bytes=max(sizes) if sizes else None, partial_bytes=sum(partial) if partial else None,
                committed=committed_bytes is not None, observed=bool(sizes)))
    return dict(nodes=records, observation='filesystem_snapshot',
                note='Missing temporary files are unobservable, not zero bytes; cleanup may remove them. '
                     'Committed means update.bin is present, not independently verified.')


def sample_upload_progress(run_id, record, timeout=3):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id):
        raise ValueError('invalid run id')
    code = inspect.getsource(scan_upload_progress) + (
        '\nimport json, sys\nprint(json.dumps(scan_upload_progress(sys.argv[1])))\n')
    try:
        result = subprocess.run(['sudo', '-n', '/usr/bin/python3', '-I', '-B', '-c', code,
            '/srv/wfb-ng-fl/comm-' + run_id], capture_output=True, text=True,
            check=True, timeout=timeout)
        record(dict(phase='upload_progress', host='fl00', ok=True, result=json.loads(result.stdout)))
    except Exception as exc:
        record(dict(phase='upload_progress', host='fl00', ok=False, error=repr(exc)))


def wait_submit(pending, deadline, run_id, record):
    remaining = set(pending)
    next_sample = time.monotonic() + 5
    while remaining:
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError('submit deadline')
        done, remaining = futures.wait(remaining, timeout=max(0, min(next_sample, deadline) - now),
                                       return_when=futures.FIRST_COMPLETED)
        # 先处理失败，避免额外采样延误清理。
        for future in sorted(done, key=lambda item: item.exception() is None):
            yield future
        now = time.monotonic()
        if remaining and now >= next_sample and now < deadline:
            sample_upload_progress(run_id, record, timeout=min(3, deadline - now))
            next_sample = now + 5


def orchestrate(sessions, record, deadline, run_id, payload, radio_patch=None, expected_native=None, release_root=RELEASE_ROOT, release_commit=RELEASE_COMMIT):
    if radio_patch is None:
        radio_patch = dict(PATCH_DEFAULT)
    if set(sessions) != set(HOSTS):
        raise RuntimeError("固定十节点会话不齐")
    def phase(action, hosts=HOSTS, **params):
        dispatch_start = time.monotonic()
        record(dict(phase=action, event='dispatch_start', dispatch_start=dispatch_start, hosts=list(hosts)))
        pending = {sessions[host].call(action, **params): host for host in hosts}
        completed = (wait_submit(pending, deadline, run_id, record) if action == 'submit' else
                     futures.as_completed(pending, timeout=max(.01, deadline - time.monotonic())))
        for future in completed:
            host = pending[future]
            try:
                result = future.result()
                record(dict(host=host, phase=action, ok=True, result=result))
            except Exception as exc:
                record(dict(host=host, phase=action, ok=False, error=repr(exc)))
                for other in pending:
                    other.cancel()
                raise RuntimeError(action + ' failed: ' + host) from exc
    # 全十目标准备成功后，才允许重配事务及角色启动。
    # 客户端不获知本机源路径，更不会经管理网获取文件内容。
    dispatch_start = time.monotonic()
    record(dict(phase='prepare', event='dispatch_start', dispatch_start=dispatch_start, hosts=HOSTS))
    pending = {}
    for host in HOSTS:
        metadata = payload if host == 'fl00' else {k: payload[k] for k in ('size_bytes', 'sha256')}
        pending[host] = sessions[host].call('prepare', run_id=run_id,
                                         payload=metadata, timing=TIMING, patch=radio_patch,
                                         expected_native=expected_native, release_root=release_root, release_commit=release_commit)
    by_future = {future: host for host, future in pending.items()}
    for future in futures.as_completed(by_future, timeout=max(.01, deadline - time.monotonic())):
        host = by_future[future]
        try:
            result = future.result()
            record(dict(host=host, phase='prepare', ok=True, result=result))
        except Exception as exc:
            record(dict(host=host, phase='prepare', ok=False, error=repr(exc)))
            for other in by_future:
                other.cancel()
            raise RuntimeError('prepare failed: ' + host) from exc
    phase('acquire_radio', hosts=['fl00'])
    phase('verify_radio')
    phase('start_receive')
    # 服务端发布与全部客户端阻塞接收必须并行。
    phase('publish')
    # 全客户端接收及独立哈希校验通过后才统一提交。
    phase('submit')
    phase('verify', hosts=['fl00'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--payload', type=Path, help='本机已存在的模型制品（1..64MiB 字节）')
    mode.add_argument('--smoke', action='store_true', help='显式生成 1MiB 合成字节文件，仅作通信冒烟')
    parser.add_argument('--model-manifest', type=Path, help='model 模式必填：模型 CLI 输出的 JSON manifest')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--reviewed-sha', help='必须匹配 runner、node_agent、recover 三文件冻结摘要')
    parser.add_argument('--channel', type=int, choices=(149, 157), default=157)
    parser.add_argument('--bandwidth-mhz', type=int, choices=(20, 40), default=40)
    parser.add_argument('--native-sha', required=True, help='本批次显式声明的 native 制品 SHA-256')
    parser.add_argument('--release-root', default=RELEASE_ROOT)
    parser.add_argument('--release-commit', default=RELEASE_COMMIT)
    parser.add_argument('--fec-n', type=int, choices=(12, 14), default=14)
    parser.add_argument('--grant-duration-ms', type=int, default=15)
    parser.add_argument('--guard-interval-ms', type=int, default=5)
    parser.add_argument('--downlink-mcs', type=int, choices=(3, 4), default=3)
    parser.add_argument('--uplink-mcs', type=int, choices=(4, 6), default=4)
    parser.add_argument('--uftp-rate-kbps', type=int, default=12000,
                        choices=range(1, 26001), metavar='KBIT/S',
                        help='下行注入速率；HT20使用受控标定候选')
    parser.add_argument('--run-id', default=time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    parser.add_argument('--output', type=Path, default=DIRECTORY / 'runs')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{64}', args.native_sha):
        parser.error('--native-sha must be 64 lowercase hex characters')
    if args.grant_duration_ms <= 0 or args.guard_interval_ms < 0:
        parser.error('grant duration must be positive and guard interval nonnegative')
    if not re.fullmatch(r'[0-9a-f]{40}', args.release_commit):
        parser.error('--release-commit must be a full lowercase commit SHA')
    release = Path(args.release_root)
    if (not release.is_absolute() or release.parent != Path('/srv/wfb-ng-fl/releases')
            or not re.fullmatch('heartbeat-' + args.release_commit[:12] + r'-[0-9]{8}T[0-9]{6}Z', release.name)):
        parser.error('--release-root must match --release-commit under /srv/wfb-ng-fl/releases')
    expected_native = dict(native_sha=args.native_sha, fec_k=8, fec_n=args.fec_n, grant_duration_ms=args.grant_duration_ms,
                           guard_interval_ms=args.guard_interval_ms)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.run_id):
        parser.error('invalid run id')
    if args.smoke and args.model_manifest:
        parser.error('--smoke 不接受模型 manifest')
    if not args.smoke and args.model_manifest is None:
        parser.error('model 模式要求 --model-manifest')
    try:
        if args.smoke:
            args.output.mkdir(parents=True, exist_ok=True)
            smoke_path = args.output / ('smoke-' + args.run_id + '.bin')
            generated = bytes(1024 * 1024)
            try:
                with smoke_path.open('xb') as stream:
                    stream.write(generated)
            except FileExistsError:
                pass
            payload = payload_info(smoke_path)
            if payload['size_bytes'] != len(generated) or payload['sha256'] != hashlib.sha256(generated).hexdigest():
                raise ValueError('existing smoke file mismatch')
            manifest = None
        else:
            payload = payload_info(args.payload)
            manifest = model_manifest_info(args.model_manifest, payload)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    radio_patch = dict(channel=args.channel, bandwidth_mhz=args.bandwidth_mhz, downlink_mcs=args.downlink_mcs,
                       uplink_mcs=args.uplink_mcs, radio_txpower_dbm=18,
                       uftp_rate_kbps=args.uftp_rate_kbps)
    plan = dict(kind='synthetic-smoke-communication' if args.smoke else 'model-equivalent-communication',
        payload_mode='smoke' if args.smoke else 'model', generated_payload=args.smoke, hosts=HOSTS, nodes=NODES,
        payload=payload, bytes=payload['size_bytes'], rounds=1, radio_patch=radio_patch, expected_native=expected_native, release_root=args.release_root, release_commit=args.release_commit, model_manifest=manifest,
        model_manifest_matched=not args.smoke, model_semantics_validated_by_runner=False, uftp_port=1144,
        http_port=18080, multicast='239.80.41.1', private_multicast='239.80.42.1',
        **TIMING, deadline=TIMING['orchestration_timeout'], bundle_sha256=bundle_sha(), execute=args.execute,
        boundary='1MiB合成字节通信冒烟，不是模型，无训练、聚合' if args.smoke else '校验manifest与字节身份，不解析张量，不承诺正式FL生命周期或update等量；公共load由调用方另验；无训练、聚合')
    print(json.dumps(plan), flush=True)
    if not args.execute:
        return 0
    if args.reviewed_sha != plan['bundle_sha256']:
        parser.error('--execute 要求 --reviewed-sha 与当前 bundle SHA 完全匹配')
    root = args.output / args.run_id
    root.mkdir(parents=True, exist_ok=False)
    deadline = time.monotonic() + TIMING['orchestration_timeout']
    recovery_deadline = deadline + TIMING['recovery_budget']
    sessions = {}
    transfer_ok = False
    cleanup_ok = True
    restore_ok = False
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, interrupted)
    with (root / 'progress.jsonl').open('x', buffering=1) as progress:
        def record(value):
            value = dict(wall=time.time(), monotonic=time.monotonic(), **value)
            progress.write(json.dumps(value) + '\n')
            print(json.dumps(value), flush=True)
        record(dict(phase='plan', result=plan))
        try:
            code = (DIRECTORY / 'node_agent.py').read_text()
            for host in HOSTS:
                sessions[host] = Session(host, code, root, deadline)
            orchestrate(sessions, record, deadline - TIMING['close_restore_reserve'], args.run_id, payload, radio_patch, expected_native, args.release_root, args.release_commit)
            transfer_ok = True
        except (Exception, KeyboardInterrupt) as exc:
            record(dict(phase='run', ok=False, error=repr(exc)))
        finally:
            # 失败时 RPC 可能仍阻塞；不排队 close，直接独立回收本轮 unit。
            closing = {session.call('close'): host for host, session in sessions.items()} if transfer_ok else {}
            roles_closed = transfer_ok and set(sessions) == set(HOSTS)
            for future, host in closing.items():
                try:
                    result = future.result(timeout=max(.01, deadline - time.monotonic() - 5))
                    cleanup_ok = cleanup_ok and result['ok']
                    roles_closed = roles_closed and result['role_close_returned']
                    record(dict(phase='cleanup', host=host, result=result))
                except Exception as exc:
                    cleanup_ok = False
                    roles_closed = False
                    record(dict(phase='cleanup', host=host, ok=False, error=repr(exc)))
            if roles_closed and 'fl00' in sessions:
                try:
                    restored = sessions['fl00'].call('release_radio').result(
                        timeout=max(.01, deadline - time.monotonic() - 5))
                    record(dict(phase='restore', result=restored))
                    if restored['ok']:
                        restore_ok = audit_final(sessions, record, deadline - 5)
                except Exception as exc:
                    record(dict(phase='restore', ok=False, error=repr(exc)))
            else:
                record(dict(phase='restore', ok=False, error='全角色 close 未确认；先独立回收再恢复'))
            disposal = futures.ThreadPoolExecutor(max_workers=len(HOSTS))
            try:
                disposing = {disposal.submit(s.dispose): h for h, s in sessions.items()}
                for future, host in disposing.items():
                    try:
                        future.result(timeout=7)
                    except Exception as exc:
                        cleanup_ok = False
                        record(dict(phase='dispose', host=host, ok=False, error=repr(exc)))
            finally:
                disposal.shutdown(wait=False, cancel_futures=True)
            # 独立回收不依赖原 RPC 或 role.close，可清理被强制结束的自有 unit 和路由。
            code = (DIRECTORY / 'recover.py').read_text()
            def recover(host):
                command = ['sudo', '-n', '/usr/bin/python3', '-I', '-B', '-c', code, args.run_id]
                argv = command if host == 'fl00' else [
                    'ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=4', host, shlex.join(command)]
                result = subprocess.run(argv, capture_output=True, text=True, timeout=max(.01, min(18, recovery_deadline - time.monotonic())))
                if result.returncode:
                    raise RuntimeError(result.stdout + result.stderr)
                return json.loads(result.stdout)
            all_stopped = True
            recovery = futures.ThreadPoolExecutor(max_workers=len(HOSTS))
            try:
                pending = {recovery.submit(recover, h): h for h in sessions}
                for future, host in pending.items():
                    try:
                        result = future.result(timeout=max(.01, recovery_deadline - time.monotonic()))
                        cleanup_ok = cleanup_ok and result['ok'] is True
                        all_stopped &= result['ok'] is True
                        record(dict(phase='independent_cleanup', host=host, result=result))
                    except Exception as exc:
                        cleanup_ok = False
                        all_stopped = False
                        record(dict(phase='independent_cleanup', host=host, ok=False, error=repr(exc)))
            finally:
                recovery.shutdown(wait=False, cancel_futures=True)
            if not restore_ok and all_stopped and set(sessions) == set(HOSTS):
                # 全测试 unit 已结束后，才允许独立恢复射频，避免在传输中切频。
                try:
                    command = ['sudo', '-n', '/usr/bin/python3', '-I', '-B', '-c', code, args.run_id, '--restore']
                    result = subprocess.run(command, capture_output=True, text=True, timeout=max(.01, min(32, recovery_deadline - time.monotonic())))
                    restored = json.loads(result.stdout)
                    rf_response_ok = restored.get('rf_response_ok', result.returncode == 0 and restored['ok'])
                    record(dict(phase='independent_restore', result=restored,
                                rf_response_ok=rf_response_ok, audited=False))
                    # 原代理已退出，独立恢复不具备十端最终审计，保守保持失败。
                    restore_ok = False
                except Exception as exc:
                    record(dict(phase='independent_restore', ok=False, error=repr(exc)))
        record(dict(phase='result', transfer_verified=transfer_ok, cleanup_ok=cleanup_ok, restore_ok=restore_ok))
    return 0 if transfer_ok and cleanup_ok and restore_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
