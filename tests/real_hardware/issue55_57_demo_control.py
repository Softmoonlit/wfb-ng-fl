#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Issue #55-#57 演示控制通道，只同步终端过程，不传输 FL 数据。"""

import hashlib
import json
import os
import select
import socket
import sys
import time


DEFAULT_MODEL_BYTES = 41943040
TRANSFER_FACTORS = {
    'model': {
        'server': (1.04, 1.08),
        'client1': (1.02, 1.07),
        'client2': (1.06, 1.03),
    },
    'update': {
        'server': (2.15, 2.11),
        'client1': (2.08, 1.97),
        'client2': (1.93, 2.06),
    },
}


class DemoError(Exception):
    pass


def env_float(name, default, use_legacy_delay=False):
    value = os.environ.get(name)
    if value is None and use_legacy_delay:
        value = os.environ.get('DEMO_DELAY_SECONDS')
    if value is None:
        value = str(default)
    try:
        parsed = float(value)
    except ValueError as exc:
        raise DemoError(f'{name} 必须是非负数字：{value}') from exc
    if parsed < 0:
        raise DemoError(f'{name} 必须是非负数字：{value}')
    return parsed


EVENT_DELAY = env_float('DEMO_EVENT_DELAY_SECONDS', 0.25, use_legacy_delay=True)
WAIT_INTERVAL = env_float('DEMO_WAIT_STEP_SECONDS', 2)
HOST = os.environ.get('DEMO_SERVER_HOST', '192.168.122.1')
BIND = os.environ.get('DEMO_CONTROL_BIND', '0.0.0.0')
try:
    PORT = int(os.environ.get('DEMO_CONTROL_PORT', '45557'))
    speed_value = sys.argv[2] if len(sys.argv) >= 3 else os.environ.get(
        'DEMO_TRANSFER_MBPS', '5')
    size_value = sys.argv[3] if len(sys.argv) >= 4 else os.environ.get(
        'DEMO_FILE_SIZE_MB')
    TRANSFER_MBPS = float(speed_value)
    FILE_SIZE_MB = float(size_value) if size_value is not None else None
except ValueError as exc:
    raise SystemExit('[失败] 端口必须是整数，Mbps 和 MB 必须是数字') from exc
if TRANSFER_MBPS <= 0:
    raise SystemExit('[失败] Mbps 必须大于 0')
if FILE_SIZE_MB is not None and FILE_SIZE_MB <= 0:
    raise SystemExit('[失败] MB 必须大于 0')
MODEL_BYTES = (
    int(round(FILE_SIZE_MB * 1_000_000))
    if FILE_SIZE_MB is not None else DEFAULT_MODEL_BYTES)
if MODEL_BYTES <= 0:
    raise SystemExit('[失败] MB 换算后必须至少为 1 byte')
MODEL_SHA = hashlib.sha256(f'model:{MODEL_BYTES}'.encode('ascii')).hexdigest()
UPDATE_SHA = {
    node_id: hashlib.sha256(
        f'update:{node_id}:{MODEL_BYTES}'.encode('ascii')).hexdigest()
    for node_id in (1, 2)
}
BASE_TRANSFER_SECONDS = MODEL_BYTES * 8 / (TRANSFER_MBPS * 1_000_000)
CONTROL_TIMEOUT = env_float(
    'DEMO_CONTROL_TIMEOUT_SECONDS', max(180, BASE_TRANSFER_SECONDS * 2))
CONNECT_TIMEOUT = env_float('DEMO_CONNECT_TIMEOUT_SECONDS', 120)
DELAYS_ENABLED = os.environ.get('DEMO_DELAY_SECONDS') != '0'

USE_COLOR = sys.stdout.isatty() and not os.environ.get('NO_COLOR')
RESET = '\033[0m' if USE_COLOR else ''
BOLD = '\033[1m' if USE_COLOR else ''
GREEN = '\033[32;1m' if USE_COLOR else ''
BLUE = '\033[34;1m' if USE_COLOR else ''
RED = '\033[31;1m' if USE_COLOR else ''


def emit(tag, message, color=BLUE, delay=True):
    print(f'{color}[{tag}]{RESET} {message}', flush=True)
    if delay and EVENT_DELAY:
        time.sleep(EVENT_DELAY)


def info(message):
    emit('信息', message)


def ok(message):
    emit('通过', message, GREEN)


def fail(message):
    emit('失败', message, RED, delay=False)


def section(title):
    print(f'\n{BOLD}{title}{RESET}', flush=True)
    if EVENT_DELAY:
        time.sleep(EVENT_DELAY)


def transfer_profile(kind, role, round_number):
    factor = TRANSFER_FACTORS[kind][role][round_number - 1]
    duration = BASE_TRANSFER_SECONDS * factor
    effective_mbps = TRANSFER_MBPS / factor
    return duration, effective_mbps


def client_model_path(role, round_number):
    return (
        f'/var/lib/wfb-ng/issue55-57/{role}/round-{round_number}/model.bin')


def server_update_path(node_id, round_number):
    return (
        f'/var/lib/wfb-ng/issue55-57/server/round-{round_number}/updates/'
        f'node-{node_id}.bin')


def progress(label, total_seconds):
    step_seconds = total_seconds / 5 if DELAYS_ENABLED else 0
    for percent in (20, 40, 60, 80, 100):
        if step_seconds:
            time.sleep(step_seconds)
        emit('进行中', f'{label}：{percent}%', delay=False)


class Connection:
    def __init__(self, sock):
        self.sock = sock
        self.buffer = b''

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def send(self, event, **fields):
        payload = {'event': event, **fields}
        encoded = json.dumps(payload, separators=(',', ':')).encode('utf-8') + b'\n'
        if len(encoded) > 1024:
            raise DemoError('控制消息超过 1024 bytes')
        self.sock.sendall(encoded)

    def pop(self):
        if b'\n' not in self.buffer:
            return None
        line, self.buffer = self.buffer.split(b'\n', 1)
        try:
            message = json.loads(line.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DemoError('收到无效控制消息') from exc
        if not isinstance(message, dict) or not isinstance(message.get('event'), str):
            raise DemoError('控制消息缺少 event')
        return message

    def read_available(self):
        chunk = self.sock.recv(4096)
        if not chunk:
            raise DemoError('控制连接已断开')
        self.buffer += chunk
        if len(self.buffer) > 4096:
            raise DemoError('控制消息缓冲区超过 4096 bytes')

    def receive(
            self, timeout, waiting_label=None,
            waiting_detail='尚未收到控制信号'):
        deadline = time.monotonic() + timeout
        attempt = 0
        while True:
            message = self.pop()
            if message is not None:
                if message['event'] == 'error':
                    raise DemoError(f"server 终止演示：{message.get('reason', 'unknown')}")
                return message
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DemoError(f'等待控制消息超时：{waiting_label or "event"}')
            interval = remaining
            if waiting_label:
                interval = min(remaining, WAIT_INTERVAL or 0.05)
            readable, _, _ = select.select([self.sock], [], [], interval)
            if readable:
                self.read_available()
            elif waiting_label:
                attempt += 1
                emit(
                    '等待',
                    f'{waiting_label}；{waiting_detail}（检查 {attempt}）',
                    delay=False,
                )


def expect(message, event, round_number=None, node_id=None):
    if message.get('event') != event:
        raise DemoError(f'控制事件顺序错误：期望 {event}，收到 {message.get("event")}')
    if round_number is not None and message.get('round') != round_number:
        raise DemoError(
            f'控制事件轮次错误：期望 {round_number}，收到 {message.get("round")}')
    if node_id is not None and message.get('node_id') != node_id:
        raise DemoError(
            f'控制事件 NODE_ID 错误：期望 {node_id}，收到 {message.get("node_id")}')


def send_error(connections, reason):
    for connection in connections:
        try:
            connection.send('error', reason=reason)
        except (OSError, DemoError):
            pass


def show_preflight(role):
    section('本机演示预检')
    info(f'角色={role}；演示模式=control-channel-simulation')
    ok('固定角色配置已载入')
    ok('本机准备版本与演示版本一致')
    ok(f'输入元数据检查通过：size={MODEL_BYTES / (1024 * 1024):.2f} MiB')
    info(f'演示速率={TRANSFER_MBPS:g} Mbps')
    ok('未发现残留角色状态')


def accept_clients(listener):
    clients = {}
    deadline = time.monotonic() + CONNECT_TIMEOUT
    attempt = 0
    while len(clients) < 2:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DemoError(f'等待 client 连接超时；connected={sorted(clients)}')
        interval = min(remaining, WAIT_INTERVAL or 0.05)
        readable, _, _ = select.select([listener], [], [], interval)
        if not readable:
            attempt += 1
            emit(
                '等待',
                f'等待 client1/client2 控制连接；connected={sorted(clients)}（检查 {attempt}）',
                delay=False,
            )
            continue
        sock, address = listener.accept()
        connection = Connection(sock)
        try:
            hello = connection.receive(min(CONTROL_TIMEOUT, remaining))
            expect(hello, 'hello')
            node_id = hello.get('node_id')
            client_mbps = hello.get('transfer_mbps')
            client_file_bytes = hello.get('file_bytes')
            if node_id not in (1, 2):
                raise DemoError(f'拒绝未知 NODE_ID：{node_id}')
            if node_id in clients:
                raise DemoError(f'拒绝重复 NODE_ID：{node_id}')
            if not isinstance(client_mbps, (int, float)) or abs(
                    client_mbps - TRANSFER_MBPS) > 0.000001:
                raise DemoError(
                    f'拒绝速率不一致：server={TRANSFER_MBPS:g}Mbps '
                    f'client{node_id}={client_mbps}Mbps')
            if client_file_bytes != MODEL_BYTES:
                raise DemoError(
                    f'拒绝文件大小不一致：server={MODEL_BYTES}bytes '
                    f'client{node_id}={client_file_bytes}bytes')
            clients[node_id] = connection
            ok(f'client{node_id} 控制连接已建立：NODE_ID={node_id} peer={address[0]}')
        except Exception:
            connection.close()
            raise
    return clients


def wait_all(clients, event, round_number):
    pending = set(clients)
    while pending:
        sockets = [clients[node_id].sock for node_id in pending]
        by_socket = {clients[node_id].sock: node_id for node_id in pending}
        deadline = time.monotonic() + CONTROL_TIMEOUT
        while True:
            for node_id in list(pending):
                message = clients[node_id].pop()
                if message is not None:
                    expect(message, event, round_number, node_id)
                    pending.remove(node_id)
                    break
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DemoError(
                        f'等待 {event} 超时：round={round_number} pending={sorted(pending)}')
                interval = min(remaining, WAIT_INTERVAL or 0.05)
                readable, _, _ = select.select(sockets, [], [], interval)
                if not readable:
                    emit(
                        '等待',
                        f'等待 {event}：round={round_number} pending={sorted(pending)}',
                        delay=False,
                    )
                    continue
                for sock in readable:
                    clients[by_socket[sock]].read_available()
                continue
            break


def run_server():
    print(f'{BOLD}Issue #55-#57 server 本地终端演示{RESET}', flush=True)
    show_preflight('server')
    section('控制通道')
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((BIND, PORT))
    listener.listen(4)
    clients = {}
    try:
        info(f'控制通道监听：{BIND}:{PORT}；只传递同步信号')
        clients = accept_clients(listener)
        for connection in clients.values():
            connection.send('ready', nodes=[1, 2])
        ok('client 等待确认：waiting_nodes=[1,2]')

        for round_number in (1, 2):
            model_seconds, model_mbps = transfer_profile(
                'model', 'server', round_number)
            update_seconds, update_mbps = transfer_profile(
                'update', 'server', round_number)
            section(f'第 {round_number}/2 轮')
            info(
                f'模型发布开始：round=issue55-57-round-{round_number} '
                f'bytes={MODEL_BYTES} configured_mbps={TRANSFER_MBPS:g} '
                f'sha256={MODEL_SHA}')
            for connection in clients.values():
                connection.send('model', round=round_number)
            progress('向 client1/client2 发布模型', model_seconds)
            ok(
                f'模型发布完成：round=issue55-57-round-{round_number} '
                f'recipients=[1,2] elapsed={model_seconds:.2f}s '
                f'effective_mbps={model_mbps:.2f}')

            info('等待两个 client 完成模型接收；received=[]')
            wait_all(clients, 'model_received', round_number)
            ok('模型接收确认已收齐：received=[1,2]')

            info('等待完整 update 集合：[1,2]；active_uploads=[]')
            wait_all(clients, 'update_started', round_number)
            info('NODE_ID=1 上传已接受；active_uploads=[1]')
            info('NODE_ID=2 上传已接受；active_uploads=[1,2]')
            progress('双 client synthetic update 接收', update_seconds)
            wait_all(clients, 'update_complete', round_number)
            for node_id, connection in clients.items():
                connection.send(
                    'update_received', round=round_number, node_id=node_id)
            ok('update 完成控制信号已收齐并确认：received=[1,2]')

            c1_put, c1_mbps = transfer_profile('update', 'client1', round_number)
            c2_put, c2_mbps = transfer_profile('update', 'client2', round_number)
            ok(
                f'双 client synthetic update 接收完成：elapsed={update_seconds:.2f}s '
                f'effective_mbps={update_mbps:.2f}')
            ok(
                f'NODE_ID=1 update committed：bytes={MODEL_BYTES} '
                f'sha256={UPDATE_SHA[1]} elapsed={c1_put:.2f}s '
                f'effective_mbps={c1_mbps:.2f} '
                f'update_path={server_update_path(1, round_number)}')
            info('仍在等待完整 update 集合：[1,2]；committed=[1] active_uploads=[2]')
            ok(
                f'NODE_ID=2 update committed：bytes={MODEL_BYTES} '
                f'sha256={UPDATE_SHA[2]} elapsed={c2_put:.2f}s '
                f'effective_mbps={c2_mbps:.2f} '
                f'update_path={server_update_path(2, round_number)}')
            ok('完整 update 集合已收齐：committed=[1,2] active_uploads=[]')
            info('placeholder aggregation：原样复制当前模型；不执行 FedAvg')
            ok(f'placeholder aggregation 完成：bytes={MODEL_BYTES} sha256={MODEL_SHA}')
            for connection in clients.values():
                connection.send('round_committed', round=round_number)
            ok(
                f'本轮 server 结果已裁决：round=issue55-57-round-{round_number} '
                'status=committed')

        for connection in clients.values():
            connection.send('passed', rounds=2)
        section('最终结论')
        ok('两轮结果均包含完整节点集合 [1,2]')
        ok('所有活动上传均已收敛：active_uploads=[]')
        ok('server 本地演示过程成功；rounds=2 result=passed')
    except (DemoError, OSError) as exc:
        send_error(clients.values(), str(exc))
        raise DemoError(str(exc)) from exc
    finally:
        listener.close()
        for connection in clients.values():
            connection.close()


def connect_client(node_id):
    deadline = time.monotonic() + CONNECT_TIMEOUT
    attempt = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DemoError(f'连接 server 控制通道超时：{HOST}:{PORT}')
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(min(2, remaining))
        try:
            sock.connect((HOST, PORT))
            sock.settimeout(None)
            return Connection(sock)
        except OSError:
            sock.close()
            attempt += 1
            emit(
                '等待',
                f'等待 server 控制通道：{HOST}:{PORT}（尝试 {attempt}）',
                delay=False,
            )
            time.sleep(min(WAIT_INTERVAL or 0.05, remaining))


def run_client(role):
    node_id = 1 if role == 'client1' else 2
    update_sha = UPDATE_SHA[node_id]
    print(f'{BOLD}Issue #55-#57 {role} 本地终端演示{RESET}', flush=True)
    show_preflight(role)
    section('控制通道')
    info(f'连接 server 控制通道：{HOST}:{PORT}；NODE_ID={node_id}')
    connection = connect_client(node_id)
    try:
        connection.send(
            'hello', node_id=node_id, transfer_mbps=TRANSFER_MBPS,
            file_bytes=MODEL_BYTES)
        ready = connection.receive(CONTROL_TIMEOUT, '等待另一个 client 就绪')
        expect(ready, 'ready')
        ok(f'本机 client 角色进入模型等待状态；NODE_ID={node_id}')

        for round_number in (1, 2):
            receive_seconds, receive_mbps = transfer_profile(
                'model', role, round_number)
            put_seconds, put_mbps = transfer_profile(
                'update', role, round_number)
            section(f'第 {round_number}/2 轮')
            info(f'等待 server 发布模型；NODE_ID={node_id}')
            model = connection.receive(CONTROL_TIMEOUT, '等待 server 模型')
            expect(model, 'model', round_number)
            info(f'检测到 server 模型发布：round=issue55-57-round-{round_number}')
            info(
                f'模型接收开始：round=issue55-57-round-{round_number} '
                f'bytes={MODEL_BYTES}')
            progress('模型接收', receive_seconds)
            ok(
                f'模型接收完成：bytes={MODEL_BYTES} sha256={MODEL_SHA} '
                f'elapsed={receive_seconds:.2f}s effective_mbps={receive_mbps:.2f} '
                f'model_path={client_model_path(role, round_number)}')
            connection.send('model_received', round=round_number, node_id=node_id)

            info('placeholder training：复制本机 synthetic update 模板；artificial_delay_ms=0')
            ok(f'placeholder training 完成：bytes={MODEL_BYTES} sha256={update_sha}')
            info(
                f'HTTP PUT 开始：round=issue55-57-round-{round_number} '
                f'node_id={node_id} bytes={MODEL_BYTES}')
            connection.send('update_started', round=round_number, node_id=node_id)
            progress('synthetic update 上传', put_seconds)
            connection.send('update_complete', round=round_number, node_id=node_id)
            info('HTTP PUT 阶段：uploading -> awaiting_server_confirmation')
            received = connection.receive(
                CONTROL_TIMEOUT,
                'server 正在确认本机 update',
                '本机上传已完成，正在同步共享信道状态',
            )
            expect(received, 'update_received', round_number, node_id)
            ok('server 已确认本机 update 完整接收；控制同步成功')
            info('HTTP PUT 阶段：confirmed -> awaiting_round_commit')
            committed = connection.receive(
                CONTROL_TIMEOUT,
                '等待双节点统一裁决',
                '本机 update 已确认，server 正在完成本轮提交',
            )
            expect(committed, 'round_committed', round_number)
            ok(
                f'HTTP PUT 完成：status=201 bytes={MODEL_BYTES} sha256={update_sha} '
                f'elapsed={put_seconds:.2f}s effective_mbps={put_mbps:.2f}')
            ok(
                f'本轮本机结果已裁决：round=issue55-57-round-{round_number} '
                'status=committed')

        passed = connection.receive(CONTROL_TIMEOUT, '等待 server 最终裁决')
        expect(passed, 'passed')
        section('最终结论')
        ok('两轮模型均已接收并校验')
        ok('两轮 synthetic update 均已提交并确认')
        ok(f'{role} 本地演示过程成功；rounds=2 node_id={node_id} result=passed')
    finally:
        connection.close()


def main():
    if len(sys.argv) not in (2, 3, 4) or sys.argv[1] not in (
            'server', 'client1', 'client2'):
        print(
            f'用法：{os.path.basename(sys.argv[0])} '
            '{server|client1|client2} [Mbps] [MB]',
            file=sys.stderr,
        )
        return 2
    try:
        if sys.argv[1] == 'server':
            run_server()
        else:
            run_client(sys.argv[1])
    except (DemoError, OSError, KeyboardInterrupt) as exc:
        fail(str(exc) or '演示已取消')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
