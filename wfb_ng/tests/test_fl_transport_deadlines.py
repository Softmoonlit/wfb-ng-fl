"""HTTP 绝对期限：受控时钟推进，不依赖固定 sleep。"""
import hashlib
import socket
from unittest.mock import patch

import pytest

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.transport import ClientTransport


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class PeerSocket:
    """每次 recv 都有进展，但所有阶段共用有限预算。"""
    def __init__(self, clock, response, step):
        self.clock = clock
        self.response = bytearray(response)
        self.step = step
        self.timeout = 100.0
        self.body_sent = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, address):
        self._advance()

    def sendall(self, data):
        self._advance()
        if not data.startswith(b'PUT '):
            self.body_sent = True

    def _advance(self):
        if self.step > self.timeout:
            self.clock.now += self.timeout
            raise socket.timeout('预算耗尽')
        self.clock.now += self.step

    def recv_into(self, buffer):
        self._advance()
        if not self.response:
            return 0
        buffer[0] = self.response.pop(0)
        return 1

    def close(self):
        pass


@pytest.mark.parametrize('step', [0.02, 0.2])
def test_client_slow_trickle_cannot_renew_budget(tmp_path, step):
    clock = Clock()
    peer = PeerSocket(clock, b'HTTP/1.1 100 Continue\r\n\r\n'
                      b'HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n', step)
    update = tmp_path / 'update.bin'
    update.write_bytes(b'x')
    client = ClientTransport(str(tmp_path), 1, 1044, ('127.0.0.1', 1), io_timeout=1)
    with patch('wfb_ng.fl.transport.time.monotonic', clock), patch(
            'wfb_ng.fl.transport.socket.socket', return_value=peer):
        with pytest.raises(FLRuntimeError) as error:
            client.submit_update('round', 1, str(update), 1, hashlib.sha256(b'x').hexdigest())
    assert error.value.error_code == 'update_submit_failed'
    assert clock.now <= 1.000001


def test_client_phases_share_one_budget(tmp_path):
    clock = Clock()
    peer = PeerSocket(clock, b'HTTP/1.1 100 Continue\r\n\r\n'
                      b'HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n', 0.01)
    update = tmp_path / 'update.bin'
    update.write_bytes(b'x')
    client = ClientTransport(str(tmp_path), 1, 1044, ('127.0.0.1', 1), io_timeout=0.5)
    with patch('wfb_ng.fl.transport.time.monotonic', clock), patch(
            'wfb_ng.fl.transport.socket.socket', return_value=peer):
        with pytest.raises(FLRuntimeError):
            client.submit_update('round', 1, str(update), 1, hashlib.sha256(b'x').hexdigest())
    assert peer.body_sent
    assert clock.now <= 0.500001


@pytest.fixture
def server(tmp_path):
    from wfb_ng.fl.transport import ServerTransport
    instance = ServerTransport((1,), 99, 1044, io_timeout=1)
    with patch('wfb_ng.fl.transport.shutil.which', return_value='/usr/bin/uftp'):
        instance.start()
    try:
        yield instance
    finally:
        instance.close()


def upload_headers(payload):
    import base64
    digest = base64.b64encode(hashlib.sha256(payload).digest()).decode('ascii')
    return (
        'PUT /v1/rounds/round/updates/1 HTTP/1.1\r\n'
        'Content-Length: %d\r\nContent-Digest: sha-256=:%s:\r\n'
        'Content-Type: application/octet-stream\r\n'
        'Expect: 100-continue\r\nConnection: close\r\n\r\n'
    ) % (len(payload), digest)


def test_server_slow_body_cannot_renew_accepted_budget(server, tmp_path):
    import threading
    clock = Clock()
    failures = []
    failed = threading.Event()

    def failure(*result):
        failures.append(result)
        failed.set()

    server.install_round('round', (1,), str(tmp_path), 100, failure)
    original_recv = socket.socket.recv_into
    receiving_body = threading.Event()

    def trickle(sock, buffer, *args):
        if sock.getsockname() == server.http_address and receiving_body.is_set():
            remaining = sock.gettimeout()
            if remaining is not None and remaining < 0.4:
                clock.now += remaining
                raise socket.timeout('预算耗尽')
            count = original_recv(sock, memoryview(buffer)[:1], *args)
            clock.now += 0.4
            return count
        return original_recv(sock, buffer, *args)

    with patch('wfb_ng.fl.transport.time.monotonic', clock), patch.object(
            socket.socket, 'recv_into', trickle):
        with socket.create_connection(server.http_address, timeout=2) as peer:
            peer.sendall(upload_headers(b'abcd').encode('ascii'))
            with peer.makefile('rb') as response:
                assert response.readline() == b'HTTP/1.1 100 Continue\r\n'
                assert response.readline() == b'\r\n'
                receiving_body.set()
                peer.sendall(b'abcd')
                response.read()
        assert failed.wait(2)
    assert failures[0][:3] == ('round', 1, 'upload_incomplete')
    assert not (tmp_path / 'updates/1/update.manifest.json').exists()
    assert not list((tmp_path / 'updates/1').glob('.update.bin.*'))


def test_server_close_shuts_down_socket_and_waits_for_failure_delivery(server, tmp_path):
    import threading
    callback_entered = threading.Event()
    release_callback = threading.Event()
    closed = threading.Event()
    server.io_timeout = 60

    def failure(*result):
        callback_entered.set()
        assert release_callback.wait(3)

    server.install_round('round', (1,), str(tmp_path), 100, failure)
    with socket.create_connection(server.http_address, timeout=2) as peer:
        peer.sendall(upload_headers(b'abcd').encode('ascii'))
        with peer.makefile('rb') as response:
            assert response.readline() == b'HTTP/1.1 100 Continue\r\n'
            assert response.readline() == b'\r\n'
            def close_server():
                server.close()
                closed.set()

            closer = threading.Thread(target=close_server)
            closer.start()
            try:
                assert callback_entered.wait(2), '关闭必须 shutdown 唤醒 body read'
                assert not closed.is_set(), '可靠失败交付结束前不能宣称关闭完成'
                assert not server.ready
            finally:
                release_callback.set()
                peer.shutdown(socket.SHUT_RDWR)
                closer.join(3)
            assert closed.is_set()


def test_server_commit_survives_expired_response_budget(server, tmp_path):
    from wfb_ng.fl.artifacts import write_json_atomic
    clock = Clock()
    failures = []
    server.install_round('round', (1,), str(tmp_path), 100, lambda *args: failures.append(args))

    def commit_then_expire(path, value):
        write_json_atomic(path, value)
        clock.now = 2.0

    with patch('wfb_ng.fl.transport.time.monotonic', clock), patch(
            'wfb_ng.fl.transport.write_json_atomic', commit_then_expire):
        with socket.create_connection(server.http_address, timeout=2) as peer:
            peer.sendall(upload_headers(b'abcd').encode('ascii'))
            with peer.makefile('rb') as response:
                assert response.readline() == b'HTTP/1.1 100 Continue\r\n'
                assert response.readline() == b'\r\n'
                peer.sendall(b'abcd')
                assert response.read() == b''
    assert failures == []
    assert (tmp_path / 'updates/1/update.bin').read_bytes() == b'abcd'
    import json
    manifest = json.loads((tmp_path / 'updates/1/update.manifest.json').read_text())
    assert manifest['sha256'] == hashlib.sha256(b'abcd').hexdigest()


def test_client_close_wakes_final_response_and_waits_for_operation(tmp_path):
    import threading
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    client = ClientTransport(str(tmp_path), 1, 1044, listener.getsockname(), io_timeout=60)
    update = tmp_path / 'update.bin'
    update.write_bytes(b'x')
    errors = []
    finished = threading.Event()

    def submit():
        try:
            client.submit_update('round', 1, str(update), 1, hashlib.sha256(b'x').hexdigest())
        except FLRuntimeError as error:
            errors.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=submit)
    worker.start()
    listener.settimeout(2)
    try:
        peer, _ = listener.accept()
        with peer, peer.makefile('rb') as request:
            while request.readline() != b'\r\n':
                pass
            peer.sendall(b'HTTP/1.1 100 Continue\r\n\r\n')
            assert request.read(1) == b'x'
            client.close()
            assert finished.wait(2)
            worker.join(2)
            assert not worker.is_alive()
            assert errors
            assert not client.ready
    finally:
        listener.close()
        client.close()
        worker.join(2)


def test_client_close_can_shutdown_connect_before_it_returns(tmp_path):
    import threading
    connecting = threading.Event()
    shutdown = threading.Event()
    closed = threading.Event()

    class ConnectingSocket:
        def settimeout(self, timeout):
            pass

        def connect(self, address):
            connecting.set()
            assert shutdown.wait(3)
            raise OSError('连接已取消')

        def shutdown(self, how):
            shutdown.set()

        def close(self):
            pass

    client = ClientTransport(str(tmp_path), 1, 1044, ('127.0.0.1', 1), io_timeout=60)
    errors = []

    def submit():
        try:
            client.submit_update('round', 1, str(tmp_path / 'unused'), 1, '00' * 32)
        except FLRuntimeError as error:
            errors.append(error)

    with patch('wfb_ng.fl.transport.socket.socket', return_value=ConnectingSocket()):
        worker = threading.Thread(target=submit)
        worker.start()
        assert connecting.wait(2)
        def close_client():
            client.close()
            closed.set()

        closer = threading.Thread(target=close_client)
        closer.start()
        try:
            assert shutdown.wait(1), 'connect 也必须注册可取消 socket'
            assert closed.wait(2)
        finally:
            shutdown.set()
            worker.join(3)
            closer.join(3)
    assert errors
    assert not worker.is_alive()
    assert not closer.is_alive()


def test_client_body_chunks_share_budget(tmp_path):
    clock = Clock()

    class ChunkPeer(PeerSocket):
        def recv_into(self, buffer):
            self._advance()
            data = b'HTTP/1.1 100 Continue\r\n\r\n'
            buffer[:len(data)] = data
            return len(data)

    peer = ChunkPeer(clock, b'', 0.2)
    update = tmp_path / 'update.bin'
    update.write_bytes(b'x' * (3 * 64 * 1024))
    client = ClientTransport(str(tmp_path), 1, 1044, ('127.0.0.1', 1), io_timeout=1)
    with patch('wfb_ng.fl.transport.time.monotonic', clock), patch(
            'wfb_ng.fl.transport.socket.socket', return_value=peer):
        with pytest.raises(FLRuntimeError) as error:
            client.submit_update('round', 1, str(update), update.stat().st_size,
                                 hashlib.sha256(update.read_bytes()).hexdigest())
    assert 'body' in str(error.value)
    assert clock.now <= 1.000001


def test_full_http_upload_succeeds_within_budget(server, tmp_path):
    failures = []
    server.install_round('round', (1,), str(tmp_path), 100, lambda *args: failures.append(args))
    update = tmp_path / 'client-update.bin'
    update.write_bytes(b'abcd')
    client = ClientTransport(str(tmp_path / 'client'), 1, 1044, server.http_address, io_timeout=2)
    try:
        client.submit_update('round', 1, str(update), 4, hashlib.sha256(b'abcd').hexdigest())
    finally:
        client.close()
    assert (tmp_path / 'updates/1/update.bin').read_bytes() == b'abcd'
    assert (tmp_path / 'updates/1/update.manifest.json').is_file()
    assert failures == []


def test_wait_for_model_is_not_limited_by_http_budget(tmp_path):
    from types import SimpleNamespace
    clock = Clock()
    client = ClientTransport(str(tmp_path), 1, 1044, ('127.0.0.1', 1), io_timeout=1)
    client.ready = True
    client._uftpd_process = SimpleNamespace(poll=lambda: None)
    candidate = tmp_path / 'inbox/next-round'

    def next_policy_completes(_interval):
        clock.now = 100.0
        candidate.mkdir(parents=True)
        (candidate / 'model.manifest.json').write_text('{}')

    with patch('wfb_ng.fl.transport.time.monotonic', clock), patch(
            'wfb_ng.fl.transport.time.sleep', next_policy_completes):
        assert client.wait_for_model_candidate() == str(candidate)


@pytest.mark.parametrize('body_cost, response_cost, committed', [
    (0.5, 0.0, False),
    (0.2, 0.3, True),
])
def test_loopback_headers_body_and_response_share_request_budget(
        server, tmp_path, body_cost, response_cost, committed):
    import threading
    clock = Clock()
    failures = []
    body_phase = threading.Event()
    headers = upload_headers(b'abcd').encode('ascii')
    original_recv = socket.socket.recv_into
    original_send = socket.socket.sendall

    def consume(sock, cost):
        remaining = sock.gettimeout()
        if remaining is not None and cost > remaining:
            clock.now += remaining
            raise socket.timeout('整个请求预算耗尽')
        clock.now += cost

    def receive(sock, buffer, *args):
        if sock.getsockname() != server.http_address:
            return original_recv(sock, buffer, *args)
        if body_phase.is_set():
            consume(sock, body_cost)
            return original_recv(sock, buffer, *args)
        # 真实 socket 逐字节读 header，累计消耗 0.6 秒受控预算。
        consume(sock, 0.6 / len(headers))
        return original_recv(sock, memoryview(buffer)[:1], *args)

    def send(sock, data, *args):
        if sock.getsockname() == server.http_address:
            if data.startswith(b'HTTP/1.1 100'):
                body_phase.set()
            elif data.startswith(b'HTTP/1.1 201'):
                consume(sock, response_cost)
        return original_send(sock, data, *args)

    server.install_round('round', (1,), str(tmp_path), 100, lambda *args: failures.append(args))
    with patch('wfb_ng.fl.transport.time.monotonic', clock), patch.object(
            socket.socket, 'recv_into', receive), patch.object(socket.socket, 'sendall', send):
        with socket.create_connection(server.http_address, timeout=2) as peer:
            peer.sendall(headers)
            with peer.makefile('rb') as response:
                assert response.readline() == b'HTTP/1.1 100 Continue\r\n'
                assert response.readline() == b'\r\n'
                peer.sendall(b'abcd')
                received = response.read()
    assert clock.now <= 1.000001
    assert (tmp_path / 'updates/1/update.manifest.json').is_file() is committed
    if committed:
        assert received == b''
        assert failures == []
        assert (tmp_path / 'updates/1/update.bin').read_bytes() == b'abcd'
    else:
        assert failures[0][:3] == ('round', 1, 'upload_incomplete')
        assert not list((tmp_path / 'updates/1').glob('.update.bin.*'))


@pytest.mark.parametrize('host', ['server.local', 'localhost', '::1', '', '127.0.0.999'])
def test_client_constructor_rejects_non_numeric_ipv4_server(tmp_path, host):
    with pytest.raises(FLRuntimeError) as error:
        ClientTransport(str(tmp_path), 1, 1044, (host, 8080))
    assert error.value.error_code == 'invalid_configuration'


def test_numeric_ipv4_submit_and_close_never_resolve_dns(server, tmp_path):
    failures = []
    server.install_round('round', (1,), str(tmp_path), 100, lambda *args: failures.append(args))
    update = tmp_path / 'numeric-update.bin'
    update.write_bytes(b'abcd')
    with patch('wfb_ng.fl.transport.socket.getaddrinfo', side_effect=AssertionError('禁止 DNS 解析')):
        client = ClientTransport(str(tmp_path / 'numeric'), 1, 1044, server.http_address)
        try:
            client.submit_update('round', 1, str(update), 4, hashlib.sha256(b'abcd').hexdigest())
        finally:
            client.close()
    assert failures == []
    assert (tmp_path / 'updates/1/update.manifest.json').is_file()
