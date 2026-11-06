#!/usr/bin/env python3
"""真实模型制品等量通信；默认仅打印本地计划，不连接任何节点。"""
import argparse
import concurrent.futures as futures
import hashlib
import json
from pathlib import Path
import queue
import shlex
import signal
import subprocess
import threading
import time

HOSTS = ['fl00', 'fl01', 'fl02', 'fl04', 'fl05']
DIRECTORY = Path(__file__).resolve().parent
# 所有跨层期限在此定义；节点只消费 prepare 下发的值。
TIMING = dict(io_timeout=300, orchestration_timeout=690, node_timeout=705,
              recovery_budget=60, cleanup_timeout=10, unit_stop_timeout=8, close_restore_reserve=40)


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
    """五端审计必须全部按时通过；射频 HTTP 成功不能替代最终状态证据。"""
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
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.stderr.close()


def orchestrate(sessions, record, deadline, run_id, payload):
    if set(sessions) != set(HOSTS):
        raise RuntimeError("固定五节点会话不齐")
    def phase(action, hosts=HOSTS, **params):
        pending = {sessions[host].call(action, **params): host for host in hosts}
        failures = []
        for future in futures.as_completed(pending, timeout=max(.01, deadline - time.monotonic())):
            host = pending[future]
            try:
                result = future.result()
                record(dict(host=host, phase=action, ok=True, result=result))
            except Exception as exc:
                record(dict(host=host, phase=action, ok=False, error=repr(exc)))
                failures.append(host)
        if failures:
            raise RuntimeError(action + ' failed: ' + ','.join(failures))
    # 全五目标准备成功后，才允许重配事务及角色启动。
    # 客户端不获知本机源路径，更不会经管理网获取文件内容。
    pending = {}
    for host in HOSTS:
        metadata = payload if host == 'fl00' else {k: payload[k] for k in ('size_bytes', 'sha256')}
        pending[host] = sessions[host].call('prepare', run_id=run_id,
                                         payload=metadata, timing=TIMING)
    failures = []
    for host, future in pending.items():
        try:
            result = future.result(timeout=max(.01, deadline - time.monotonic()))
            record(dict(host=host, phase='prepare', ok=True, result=result))
        except Exception as exc:
            record(dict(host=host, phase='prepare', ok=False, error=repr(exc)))
            failures.append(host)
    if failures:
        raise RuntimeError('prepare failed: ' + ','.join(failures))
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
    parser.add_argument('--payload', required=True, type=Path, help='本机已存在的模型制品（1..64MiB 字节）')
    parser.add_argument('--model-manifest', required=True, type=Path, help='模型 CLI 输出的 JSON manifest')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--reviewed-sha', help='必须匹配 runner、node_agent、recover 三文件冻结摘要')
    parser.add_argument('--run-id', default=time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    parser.add_argument('--output', type=Path, default=DIRECTORY / 'runs')
    args = parser.parse_args()
    try:
        payload = payload_info(args.payload)
        manifest = model_manifest_info(args.model_manifest, payload)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    plan = dict(kind='model-equivalent-communication', hosts=HOSTS, nodes=[1, 2, 4, 5],
        payload=payload, bytes=payload['size_bytes'], rounds=1, model_manifest=manifest,
        model_manifest_matched=True, model_semantics_validated_by_runner=False, uftp_port=1144,
        http_port=18080, multicast='239.80.41.1', private_multicast='239.80.42.1',
        **TIMING, deadline=TIMING['orchestration_timeout'], bundle_sha256=bundle_sha(), execute=args.execute,
        boundary='校验manifest与字节身份，不解析张量，不承诺正式FL生命周期或update等量；公共load由调用方另验；无训练、聚合')
    print(json.dumps(plan), flush=True)
    if not args.execute:
        return 0
    if args.reviewed_sha != plan['bundle_sha256']:
        parser.error('--execute 要求 --reviewed-sha 与当前 bundle SHA 完全匹配')
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.run_id):
        parser.error('invalid run id')
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
            orchestrate(sessions, record, deadline - TIMING['close_restore_reserve'], args.run_id, payload)
            transfer_ok = True
        except (Exception, KeyboardInterrupt) as exc:
            record(dict(phase='run', ok=False, error=repr(exc)))
        finally:
            closing = {session.call('close'): host for host, session in sessions.items()}
            roles_closed = len(sessions) == 5
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
            with futures.ThreadPoolExecutor(max_workers=5) as disposal:
                disposing = {disposal.submit(s.dispose): h for h, s in sessions.items()}
                for future, host in disposing.items():
                    try:
                        future.result(timeout=7)
                    except Exception as exc:
                        cleanup_ok = False
                        record(dict(phase='dispose', host=host, ok=False, error=repr(exc)))
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
            with futures.ThreadPoolExecutor(max_workers=5) as recovery:
                pending = {recovery.submit(recover, h): h for h in sessions}
                for future, host in pending.items():
                    try:
                        result = future.result(timeout=max(.01, recovery_deadline - time.monotonic()))
                        cleanup_ok = cleanup_ok and result['ok']
                        record(dict(phase='independent_cleanup', host=host, result=result))
                    except Exception as exc:
                        cleanup_ok = False
                        all_stopped = False
                        record(dict(phase='independent_cleanup', host=host, ok=False, error=repr(exc)))
            if not restore_ok and all_stopped and len(sessions) == 5:
                # 全测试 unit 已结束后，才允许独立恢复射频，避免在传输中切频。
                try:
                    command = ['sudo', '-n', '/usr/bin/python3', '-I', '-B', '-c', code, args.run_id, '--restore']
                    result = subprocess.run(command, capture_output=True, text=True, timeout=max(.01, min(32, recovery_deadline - time.monotonic())))
                    restored = json.loads(result.stdout)
                    rf_response_ok = restored.get('rf_response_ok', result.returncode == 0 and restored['ok'])
                    record(dict(phase='independent_restore', result=restored,
                                rf_response_ok=rf_response_ok, audited=False))
                    # 原代理已退出，独立恢复不具备五端最终审计，保守保持失败。
                    restore_ok = False
                except Exception as exc:
                    record(dict(phase='independent_restore', ok=False, error=repr(exc)))
        record(dict(phase='result', transfer_verified=transfer_ok, cleanup_ok=cleanup_ok, restore_ok=restore_ok))
    return 0 if transfer_ok and cleanup_ok and restore_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
