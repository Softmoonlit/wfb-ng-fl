"""客户端测试的显式回环端点和无外部 UDP 边界。"""
import socket
import threading
import types

import pytest


def client_network_kwargs():
    ports = []
    for _ in range(3):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.bind(('127.0.0.1', 0))
            ports.append(peer.getsockname()[1])
    return dict(server_control_host='127.0.0.1', server_control_port=ports[0],
                broadcast_port=ports[1], radio_control_port=ports[2])


class FakeControlPeer:
    """仅记录控制 UDP，不创建 socket，不向任何主机发送。"""
    def __init__(self, *args, **kwargs):
        self.datagrams = []
        self.timeout = .01

    def setsockopt(self, *args): pass
    def bind(self, address): pass
    def setblocking(self, value): pass
    def settimeout(self, value): self.timeout = value
    def close(self): pass

    def sendto(self, data, address):
        self.datagrams.append((data, address))
        return len(data)

    def recvfrom(self, size):
        threading.Event().wait(min(self.timeout or .01, .02))
        raise socket.timeout()


@pytest.fixture(autouse=True)
def isolated_client_network(monkeypatch):
    from wfb_ng.fl import control
    real_socket = socket.socket

    class LoopbackSocket(real_socket):
        def _check_peer(self, address):
            if self.family in (socket.AF_INET, socket.AF_INET6):
                assert address[0].startswith('127.'), f'测试禁止外部网络端点: {address}'
                assert address[1] not in (9000, 9001, 9002, 9200), f'测试禁止实机控制端口: {address}'
                if self.getsockname()[1] == 0:
                    super().bind(('127.0.0.1', 0))

        def bind(self, address):
            if self.family in (socket.AF_INET, socket.AF_INET6):
                assert address[0].startswith('127.'), f'测试监听必须为显式回环地址: {address}'
                assert address[1] == 0 or address[1] not in (9000, 9001, 9002, 9200)
            return super().bind(address)

        def connect(self, address):
            self._check_peer(address)
            return super().connect(address)

        def sendto(self, data, *args):
            self._check_peer(args[-1])
            return super().sendto(data, *args)

    monkeypatch.setattr(socket, 'socket', LoopbackSocket)
    # 只替换 control 模块的 UDP 系统边界，service 的 UNIX socket 不受影响。
    proxy = types.SimpleNamespace(**{name: getattr(socket, name) for name in dir(socket)})
    proxy.socket = FakeControlPeer
    monkeypatch.setattr(control, 'socket', proxy)
    # 这些宿主检查直接分发广播；不启动任何真实广播监听 socket。
    monkeypatch.setattr(control.ControlPlaneClient, 'start_broadcast_listener', lambda self: None)
