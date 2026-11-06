"""通过真实回环 UDP 验证心跳回执身份与单次等待窗口。"""

from dataclasses import replace
import ipaddress
import json
import socket
import threading
import time

import pytest

from wfb_ng.fl.control import ClientNodeState, ControlPlaneClient, NodeHeartbeat, HeartbeatAck
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter


def receive_heartbeat(sock):
    """先通过公开协议完成启动挑战，再测试普通心跳 ACK。"""
    raw, address = sock.recvfrom(4096)
    if json.loads(raw).get("type") != "NODE_HEARTBEAT":
        return raw, address
    heartbeat = NodeHeartbeat.from_bytes(raw)
    if heartbeat.session is None:
        sock.sendto(HeartbeatAck(heartbeat.timestamp_ms, heartbeat.current_channel,
                    heartbeat.boot_id, "loopback-session", False).to_bytes(), address)
        raw, echoed_address = sock.recvfrom(4096)
        assert echoed_address == address
        echoed = NodeHeartbeat.from_bytes(raw)
        assert echoed == replace(heartbeat, session="loopback-session")
        return raw, address
    return raw, address


@pytest.fixture
def loopback_network(monkeypatch):
    """只允许真实 UDP 访问本测试动态分配的回环端口。"""
    real_socket = socket.socket
    dynamic_ports = set()

    class LoopbackSocket(real_socket):
        """保留真实 UDP，并限定通信双方的地址与端口。"""

        def bind(self, address):
            assert ipaddress.ip_address(address[0]).is_loopback
            assert address[1] == 0 or address[1] in dynamic_ports
            assert address[1] not in {9000, 9001, 9002, 9200}
            result = super().bind(address)
            assert self.getsockname()[1] not in {9000, 9001, 9002, 9200}
            dynamic_ports.add(self.getsockname()[1])
            return result

        def check_destination(self, address):
            assert address[0] == "localhost" or ipaddress.ip_address(address[0]).is_loopback
            assert address[1] in dynamic_ports
            assert address[1] not in {9000, 9001, 9002, 9200}
            if self.getsockname()[1] == 0:
                self.bind(("127.0.0.1", 0))

        def sendto(self, data, *args):
            self.check_destination(args[-1])
            return super().sendto(data, *args)

        def connect(self, address):
            self.check_destination(address)
            return super().connect(address)

    # 仅限制 socket 系统边界，协议与寻频逻辑保持真实执行。
    monkeypatch.setattr(socket, "socket", LoopbackSocket)


@pytest.mark.parametrize("port", [9000, 9001, 9002, 9200])
def test_dynamic_loopback_guard_blocks_production_ports(loopback_network, port):
    """回环地址也不得绕过生产端口保护，收发双方只使用动态端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(AssertionError):
            sock.bind(("127.0.0.1", port))
        with pytest.raises(AssertionError):
            sock.sendto(b"blocked", ("127.0.0.1", port))
        with pytest.raises(AssertionError):
            sock.connect(("127.0.0.1", port))


def test_late_149_ack_cannot_lock_157(loopback_network):
    """收到 157 寻频心跳后才释放 149 的回执，不能错误锁频。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(3.0)
        adapter = MockNetworkAdapter(["wlan-test"])
        client = ControlPlaneClient(
            node_id=4,
            tun_ip="127.0.0.1",
            network_adapter=adapter,
            air_interface="wlan-test",
            initial_channel=149,
            cached_channel=149,
            server_host="127.0.0.1",
            server_port=server.getsockname()[1],
            broadcast_port=server.getsockname()[1],
            max_attempts_per_channel=1,
        )
        received = []
        peer_errors = []
        ack_sent = threading.Event()

        def delayed_ack_peer():
            try:
                raw, first_address = receive_heartbeat(server)
                first = NodeHeartbeat.from_bytes(raw)
                received.append((first.current_channel, first.state))
                assert received == [(149, "HUNTING")]
                # 旧请求回执也包含完整身份，避免仅因缺少字段而虚假通过。
                stale_ack = json.dumps({
                    "ack": True,
                    "heartbeat_timestamp_ms": first.timestamp_ms,
                    "boot_id": first.boot_id, "session": first.session, "confirmed": True,
                    "channel": first.current_channel,
                }).encode("utf-8")
                # 不回复 149；收到 157 表示寻频已排空 socket，依收包序列释放回执。
                raw, second_address = receive_heartbeat(server)
                second = NodeHeartbeat.from_bytes(raw)
                received.append((second.current_channel, second.state))
                assert received == [(149, "HUNTING"), (157, "HUNTING")]
                assert second_address == first_address
                server.sendto(stale_ack, second_address)
                ack_sent.set()
                # 不给 157 回执，包括错误进入 IDLE 后的心跳。
            except BaseException as exc:
                peer_errors.append(exc)

        peer = threading.Thread(target=delayed_ack_peer, daemon=True)
        peer.start()
        try:
            found = client.hunt_once(ladder=[149, 157])
        finally:
            client.stop()
            peer.join(timeout=4.0)

        assert not peer.is_alive(), "回环服务线程超出截止时间"
        assert not peer_errors, repr(peer_errors)
        assert ack_sent.is_set(), "未在收到 157 探测后释放 149 回执"
        assert received == [(149, "HUNTING"), (157, "HUNTING")]
        assert adapter.channel_history == [149, 157]
        assert (found, client.locked_channel, client.state) == (
            False, None, ClientNodeState.HUNTING
        ), "迟到的 149 回执不能导致发现并锁定 157"


@pytest.mark.parametrize("operation", ["hunt", "send"])
def test_old_ack_then_current_ack_succeeds_in_same_window(loopback_network, operation):
    """同一等待窗口先收到完整的旧回执，随后仍应接受本次有效回执。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(2.0)
        client = ControlPlaneClient(
            node_id=4, tun_ip="127.0.0.1",
            network_adapter=MockNetworkAdapter(["wlan-test"]),
            air_interface="wlan-test", initial_channel=157,
            server_host="127.0.0.1", server_port=server.getsockname()[1],
            broadcast_port=server.getsockname()[1], max_attempts_per_channel=1,
        )
        heartbeats = []
        peer_errors = []

        def reply():
            try:
                raw, address = receive_heartbeat(server)
                heartbeat = NodeHeartbeat.from_bytes(raw)
                heartbeats.append(heartbeat)
                server.sendto(json.dumps({
                    "ack": True,
                    "heartbeat_timestamp_ms": heartbeat.timestamp_ms - 1,
                    "boot_id": heartbeat.boot_id, "session": heartbeat.session, "confirmed": True,
                    "channel": 157,
                }).encode(), address)
                # 同一窗口内夹入错信道、无关和非法消息，均不能提前结束等待。
                for invalid in (
                    json.dumps({"ack": True,
                                "heartbeat_timestamp_ms": heartbeat.timestamp_ms,
                                "boot_id": heartbeat.boot_id, "session": heartbeat.session, "confirmed": True,
                                "channel": 149}).encode(),
                    b'{"type":"UNRELATED"}', b'not-json', b'\xff', b'[]',
                    b'{"ack": true}',
                ):
                    server.sendto(invalid, address)
                server.sendto(json.dumps({
                    "ack": True,
                    "heartbeat_timestamp_ms": heartbeat.timestamp_ms,
                    "boot_id": heartbeat.boot_id, "session": heartbeat.session, "confirmed": True,
                    "channel": 157,
                }).encode(), address)
                if operation == "hunt":
                    # 寻频成功后，用公开协议确认客户端立即发出的 IDLE 心跳。
                    raw, address = receive_heartbeat(server)
                    idle = NodeHeartbeat.from_bytes(raw)
                    assert idle.state == "IDLE"
                    server.sendto(json.dumps({
                        "ack": True, "heartbeat_timestamp_ms": idle.timestamp_ms,
                        "boot_id": idle.boot_id, "session": idle.session, "confirmed": True,
                        "channel": 157,
                    }).encode(), address)
            except BaseException as exc:
                peer_errors.append(exc)

        peer = threading.Thread(target=reply, daemon=True)
        peer.start()
        try:
            if operation == "hunt":
                assert client.hunt_once(ladder=[157])
                assert client.locked_channel == 157
                assert client.state == ClientNodeState.IDLE
            else:
                ack = client.send_heartbeat_once()
                assert ack is not None
                assert ack.to_dict() == {
                    "ack": True,
                    "heartbeat_timestamp_ms": heartbeats[0].timestamp_ms,
                    "boot_id": heartbeats[0].boot_id, "session": heartbeats[0].session, "confirmed": True,
                    "channel": 157,
                }
        finally:
            client.stop()
            peer.join(timeout=3.0)
        assert not peer.is_alive(), "回环服务线程超出截止时间"
        assert not peer_errors, repr(peer_errors)


@pytest.mark.parametrize("operation", ["hunt", "send"])
@pytest.mark.parametrize("mismatch", ["request", "channel", "boot", "session", "port", "host", "old-protocol"])
def test_mismatched_ack_cannot_establish_contact(loopback_network, operation, mismatch):
    """即使其他字段正确，身份或来源任一不符也不能建立联系。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server, \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as impostor:
        server.bind(("127.0.0.1", 0))
        impostor.bind(("127.0.0.2", server.getsockname()[1]) if mismatch == "host"
                      else ("127.0.0.1", 0))
        server.settimeout(2.0)
        client = ControlPlaneClient(
            node_id=4, tun_ip="127.0.0.1",
            network_adapter=MockNetworkAdapter(["wlan-test"]), air_interface="wlan-test",
            initial_channel=157, server_host="127.0.0.1",
            server_port=server.getsockname()[1], broadcast_port=server.getsockname()[1],
            attempt_timeout_seconds=0.08, max_attempts_per_channel=1,
        )
        peer_errors = []

        def reply():
            try:
                raw, address = receive_heartbeat(server)
                heartbeat = NodeHeartbeat.from_bytes(raw)
                payload = {"ack": True,
                           "heartbeat_timestamp_ms": heartbeat.timestamp_ms,
                    "boot_id": heartbeat.boot_id, "session": heartbeat.session, "confirmed": True,
                           "channel": 157}
                if mismatch == "request":
                    payload["heartbeat_timestamp_ms"] -= 1
                elif mismatch == "channel":
                    payload["channel"] = 149
                elif mismatch == "boot":
                    payload["boot_id"] = "previous-boot"
                elif mismatch == "session":
                    payload["session"] = "previous-session"
                elif mismatch == "old-protocol":
                    payload = {"ack": True}
                sender = impostor if mismatch in ("port", "host") else server
                sender.sendto(json.dumps(payload).encode(), address)
            except BaseException as exc:
                peer_errors.append(exc)

        peer = threading.Thread(target=reply, daemon=True)
        peer.start()
        try:
            if operation == "hunt":
                assert client.hunt_once(ladder=[157]) is False
                assert client.locked_channel is None
            else:
                assert client.send_heartbeat_once() is None
                assert client.missed_acks == 0
            assert client.state == ClientNodeState.HUNTING
        finally:
            client.stop()
            peer.join(timeout=3.0)
        assert not peer.is_alive(), "回环服务线程超出截止时间"
        assert not peer_errors, repr(peer_errors)


@pytest.mark.parametrize("operation", ["hunt", "send"])
def test_invalid_stream_does_not_restart_single_deadline(loopback_network, operation):
    """持续无关消息不能刷新单次截止时间，也不能使等待提前结束。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(2.0)
        client = ControlPlaneClient(
            node_id=4, tun_ip="127.0.0.1",
            network_adapter=MockNetworkAdapter(["wlan-test"]), air_interface="wlan-test",
            initial_channel=157, server_host="127.0.0.1",
            server_port=server.getsockname()[1], broadcast_port=server.getsockname()[1],
            attempt_timeout_seconds=0.08, max_attempts_per_channel=1,
        )
        peer_errors = []
        finished = threading.Event()

        def reply():
            try:
                _, address = receive_heartbeat(server)
                deadline = time.monotonic() + 0.4
                while time.monotonic() < deadline:
                    server.sendto(b'{"type":"UNRELATED"}', address)
                    # 仅限流；释放条件始终是收到真实探测包，不使用延时触发竞态。
                    if finished.wait(0.01):
                        break
            except BaseException as exc:
                peer_errors.append(exc)

        peer = threading.Thread(target=reply, daemon=True)
        peer.start()
        try:
            start = time.monotonic()
            if operation == "hunt":
                assert client.hunt_once(ladder=[157]) is False
            else:
                assert client.send_heartbeat_once() is None
            elapsed = time.monotonic() - start
            assert 0.07 <= elapsed < 0.3
        finally:
            finished.set()
            peer.join(timeout=3.0)
            client.stop()
        assert not peer.is_alive(), "回环服务线程超出截止时间"
        assert not peer_errors, repr(peer_errors)
