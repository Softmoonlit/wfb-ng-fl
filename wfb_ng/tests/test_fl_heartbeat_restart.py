"""通过编解码、注册表和真实回环客户端接缝验证跨重启心跳契约。"""
from dataclasses import replace
import json
import socket
import threading
from types import SimpleNamespace
import time

import pytest

from wfb_ng.fl import control
from wfb_ng.fl.control import (ControlPlaneClient, HeartbeatAck, NodeHeartbeat,
                               NodeHorizonRegistry, NodeReadiness)
from wfb_ng.fl.radio import RadioConfig
from wfb_ng.tests.test_fl_heartbeat_ack_identity import loopback_network

ADDR = ("127.0.0.1", 12345)
RADIO = RadioConfig()


def heartbeat(boot, timestamp, state="HUNTING", session=None):
    return NodeHeartbeat(1, state, 0, 157, 12, 6, timestamp_ms=timestamp,
                         boot_id=boot, session=session)


def establish(registry, boot, timestamp):
    request = heartbeat(boot, timestamp)
    challenge, _ = registry.update_heartbeat(request, ADDR, RADIO)
    assert not challenge.confirmed
    request = replace(request, session=challenge.session)
    ack, _ = registry.update_heartbeat(request, ADDR, RADIO)
    assert ack.confirmed
    return request


@pytest.mark.parametrize("new_timestamp", [62406, 9038346])
def test_reboot_and_same_os_daemon_restart_require_new_hunting_idle(new_timestamp):
    registry = NodeHorizonRegistry()
    old = establish(registry, "old", 9038343)
    registry.update_heartbeat(replace(old, state="IDLE", timestamp_ms=9038344), ADDR, RADIO)
    assert registry.get_node(1).readiness == NodeReadiness.READY
    before = registry.get_node(1).to_dict()
    request = heartbeat("new", new_timestamp)
    challenge, _ = registry.update_heartbeat(request, ADDR, RADIO)
    assert not challenge.confirmed
    assert registry.get_node(1).to_dict() == before
    new = replace(request, session=challenge.session)
    registry.update_heartbeat(new, ADDR, RADIO)
    assert registry.get_node(1).timestamp_ms == new_timestamp
    assert registry.get_node(1).readiness == NodeReadiness.CONNECTING
    # 旧代即使携带更大的序列，也不能覆盖新进程的记录。
    registry.update_heartbeat(replace(old, state="IDLE", timestamp_ms=9999999), ADDR, RADIO)
    assert registry.get_node(1).timestamp_ms == new_timestamp
    registry.update_heartbeat(replace(new, state="IDLE", timestamp_ms=new_timestamp + 1), ADDR, RADIO)
    assert registry.get_node(1).readiness == NodeReadiness.READY
    before = registry.get_node(1).to_dict()
    registry.update_heartbeat(new, ADDR, RADIO)
    assert registry.get_node(1).to_dict() == before


def test_old_unseen_generation_and_old_challenge_cannot_resurrect():
    registry = NodeHorizonRegistry()
    old = establish(registry, "old", 9038344)
    unseen = heartbeat("delayed-unseen", 9000000)
    challenge, _ = registry.update_heartbeat(unseen, ADDR, RADIO)
    new = establish(registry, "new", 62406)
    before = registry.get_node(1).to_dict()
    for delayed in (old, unseen, replace(unseen, session=challenge.session)):
        ack, _ = registry.update_heartbeat(delayed, ADDR, RADIO)
        assert not ack.confirmed
        assert registry.get_node(1).to_dict() == before
    registry.update_heartbeat(replace(new, state="IDLE", timestamp_ms=62407), ADDR, RADIO)
    assert registry.get_node(1).readiness == NodeReadiness.READY


@pytest.mark.parametrize("mismatch", ["source", "channel", "request", "boot"])
def test_challenge_confirmation_is_bound_to_request_and_endpoint(mismatch):
    registry = NodeHorizonRegistry()
    request = heartbeat("new", 62406)
    challenge, _ = registry.update_heartbeat(request, ADDR, RADIO)
    confirmation = replace(request, session=challenge.session)
    addr = ADDR
    if mismatch == "source":
        addr = ("127.0.0.2", 12345)
    elif mismatch == "channel":
        confirmation = replace(confirmation, current_channel=149)
    elif mismatch == "request":
        confirmation = replace(confirmation, timestamp_ms=62407)
    else:
        confirmation = replace(confirmation, boot_id="other")
    ack, _ = registry.update_heartbeat(confirmation, addr, RADIO)
    assert not ack.confirmed
    assert registry.get_node(1) is None


def test_new_protocol_codec_roundtrip_and_old_protocol_rejected():
    request = heartbeat("boot", 62406)
    assert NodeHeartbeat.from_bytes(request.to_bytes()) == request
    ack = HeartbeatAck(62406, 157, "boot", "challenge", False)
    assert HeartbeatAck.from_bytes(ack.to_bytes()) == ack
    for field in ("boot_id", "session"):
        payload = request.to_dict()
        del payload[field]
        with pytest.raises(ValueError):
            NodeHeartbeat.from_bytes(json.dumps(payload).encode())
    for field in ("boot_id", "session", "confirmed"):
        payload = ack.to_dict()
        del payload[field]
        with pytest.raises(ValueError):
            HeartbeatAck.from_bytes(json.dumps(payload).encode())


@pytest.mark.parametrize("reset_clock", [True, False])
def test_real_client_restart_reconnects_with_challenge(loopback_network, monkeypatch, reset_clock):
    clock = SimpleNamespace(now=9038.344)
    monkeypatch.setattr(control, "time", SimpleNamespace(
        monotonic=time.monotonic, monotonic_ns=lambda: int(clock.now * 1e9),
        time=time.time, sleep=time.sleep))
    registry = NodeHorizonRegistry()
    errors = []
    received = []
    done = threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(.1)

        def peer():
            try:
                while not done.is_set():
                    try:
                        raw, addr = server.recvfrom(4096)
                    except socket.timeout:
                        continue
                    hb = NodeHeartbeat.from_bytes(raw)
                    received.append(hb)
                    ack, _ = registry.update_heartbeat(hb, addr, RADIO)
                    server.sendto(ack.to_bytes(), addr)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=peer, daemon=True)
        worker.start()
        clients = []
        try:
            for index in range(2):
                if index:
                    clock.now = 62.406 if reset_clock else 9040
                client = ControlPlaneClient(1, "127.0.0.1", server_host="127.0.0.1",
                    server_port=server.getsockname()[1], broadcast_port=server.getsockname()[1],
                    max_attempts_per_channel=1)
                clients.append(client)
                assert client.hunt_once([157])
                assert registry.get_node(1).readiness == NodeReadiness.READY
            assert clients[0].boot_id != clients[1].boot_id
            assert received[0].timestamp_ms == 9038344
            new = [hb for hb in received if hb.boot_id == clients[1].boot_id]
            assert new[0].timestamp_ms == (62406 if reset_clock else 9040000)
            assert [hb.state for hb in new] == ["HUNTING", "HUNTING", "IDLE"]
            assert new[0].session is None and new[1].session
            assert new[2].timestamp_ms > new[1].timestamp_ms
        finally:
            done.set()
            worker.join(timeout=2)
            for client in clients:
                client.stop()
        assert not worker.is_alive()
        assert not errors


@pytest.mark.parametrize("operation", ["hunt", "send"])
def test_challenge_without_confirmation_cannot_establish_contact(loopback_network, operation):
    errors = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(1)
        client = ControlPlaneClient(1, "127.0.0.1", server_host="127.0.0.1",
            server_port=server.getsockname()[1], broadcast_port=server.getsockname()[1],
            max_attempts_per_channel=1, attempt_timeout_seconds=.08)

        def peer():
            try:
                raw, addr = server.recvfrom(4096)
                hb = NodeHeartbeat.from_bytes(raw)
                server.sendto(HeartbeatAck(hb.timestamp_ms, 157, hb.boot_id,
                                          "challenge-only", False).to_bytes(), addr)
                raw, echoed_addr = server.recvfrom(4096)
                assert echoed_addr == addr
                assert NodeHeartbeat.from_bytes(raw) == replace(hb, session="challenge-only")
                # 故意丢弃确认 ACK。
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=peer, daemon=True)
        worker.start()
        try:
            if operation == "hunt":
                assert not client.hunt_once([157])
            else:
                assert client.send_heartbeat_once() is None
            assert client.state == control.ClientNodeState.HUNTING
            assert client.locked_channel is None
        finally:
            client.stop()
            worker.join(timeout=2)
        assert not worker.is_alive()
        assert not errors


def test_unconfirmed_heartbeat_does_not_call_business_handler():
    calls = []
    server = control.ControlPlaneServer(RADIO, bind_host="127.0.0.1", bind_port=0,
        broadcast_addr="127.0.0.1", broadcast_port=0,
        on_heartbeat_received=lambda *args: calls.append(args))
    old = heartbeat("old", 9038344)
    challenge = HeartbeatAck.from_bytes(server.handle_datagram(old.to_bytes(), ADDR)[0])
    old = replace(old, session=challenge.session)
    server.handle_datagram(old.to_bytes(), ADDR)
    assert len(calls) == 1
    before = server.registry.get_node(1).to_dict()
    reply = server.handle_datagram(heartbeat("new", 62406).to_bytes(), ADDR)
    assert len(reply) == 1
    assert not HeartbeatAck.from_bytes(reply[0]).confirmed
    assert len(calls) == 1
    assert server.registry.get_node(1).to_dict() == before


def test_duplicate_null_request_reuses_challenge_cookie():
    registry = NodeHorizonRegistry()
    request = heartbeat("new", 62406)
    first, _ = registry.update_heartbeat(request, ADDR, RADIO)
    duplicate, _ = registry.update_heartbeat(request, ADDR, RADIO)
    assert duplicate == first
    assert registry.get_node(1) is None
    ack, _ = registry.update_heartbeat(replace(request, session=first.session), ADDR, RADIO)
    assert ack.confirmed
    assert registry.get_node(1).readiness == NodeReadiness.CONNECTING


def test_old_confirmation_cannot_invalidate_current_challenge_cookie():
    registry = NodeHorizonRegistry()
    old_request = heartbeat("old-pending", 9038344)
    old_challenge, _ = registry.update_heartbeat(old_request, ADDR, RADIO)
    establish(registry, "admitted", 100)
    request = heartbeat("new", 62406)
    challenge, _ = registry.update_heartbeat(request, ADDR, RADIO)
    stale, _ = registry.update_heartbeat(
        replace(old_request, session=old_challenge.session), ADDR, RADIO)
    assert not stale.confirmed
    assert registry.get_node(1).timestamp_ms == 100
    duplicate, _ = registry.update_heartbeat(request, ADDR, RADIO)
    assert duplicate == challenge
    ack, _ = registry.update_heartbeat(replace(request, session=challenge.session), ADDR, RADIO)
    assert ack.confirmed
    assert registry.get_node(1).timestamp_ms == 62406


@pytest.mark.parametrize("operation", ["send", "hunt"])
def test_live_client_recovers_after_server_restart(loopback_network, operation):
    errors = []
    received = []
    done = threading.Event()

    def new_server():
        return control.ControlPlaneServer(RADIO, bind_host="127.0.0.1", bind_port=0,
            broadcast_addr="127.0.0.1", broadcast_port=0)

    current = [new_server()]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(.1)
        client = ControlPlaneClient(1, "127.0.0.1", server_host="127.0.0.1",
            server_port=server.getsockname()[1], broadcast_port=server.getsockname()[1],
            max_attempts_per_channel=1)

        def peer():
            try:
                while not done.is_set():
                    try:
                        raw, addr = server.recvfrom(4096)
                    except socket.timeout:
                        continue
                    received.append(NodeHeartbeat.from_bytes(raw))
                    for reply in current[0].handle_datagram(raw, addr):
                        server.sendto(reply, addr)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=peer, daemon=True)
        worker.start()
        try:
            assert client.hunt_once([157])
            old_session = received[-1].session
            assert old_session
            current[0] = new_server()
            start = len(received)
            if operation == "hunt":
                # 在重启后交错另一代际的请求，客户端仍须能恢复。
                current[0].registry.update_heartbeat(heartbeat("other", 1), ADDR, RADIO)
            # 公开状态通知会先用客户端保留的旧 session 上报 HUNTING。
            client.notify_state_change(control.ClientNodeState.HUNTING)
            assert received[start].session == old_session
            if operation == "hunt":
                assert client.hunt_once([157])
                assert any(hb.session is None for hb in received[start + 1:])
            else:
                assert current[0].registry.get_node(1).readiness == NodeReadiness.CONNECTING
                client.notify_state_change(control.ClientNodeState.IDLE)
            assert current[0].registry.get_node(1).readiness == NodeReadiness.READY
            assert received[-1].boot_id == client.boot_id
            assert received[-1].session != old_session
        finally:
            done.set()
            worker.join(timeout=2)
            client.stop()
        assert not worker.is_alive()
        assert not errors


def test_loopback_duplicate_challenges_and_late_confirmation_still_connect(loopback_network):
    registry = NodeHorizonRegistry()
    errors = []
    done = threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(.1)
        client = ControlPlaneClient(1, "127.0.0.1", server_host="127.0.0.1",
            server_port=server.getsockname()[1], broadcast_port=server.getsockname()[1],
            max_attempts_per_channel=1)

        def peer():
            try:
                raw, addr = server.recvfrom(4096)
                request = NodeHeartbeat.from_bytes(raw)
                old_request = replace(request, boot_id="old-pending")
                # 模拟重启前签发、重启后迟到的旧挑战确认。
                old_challenge, _ = NodeHorizonRegistry().update_heartbeat(old_request, addr, RADIO)
                challenge, _ = registry.update_heartbeat(request, addr, RADIO)
                duplicate, _ = registry.update_heartbeat(request, addr, RADIO)
                assert duplicate == challenge
                rejected, _ = registry.update_heartbeat(
                    replace(old_request, session=old_challenge.session), addr, RADIO)
                assert not rejected.confirmed
                # 按确定顺序注入旧 ACK 和两份当前挑战，不通过延时制造竞态。
                for ack in (old_challenge, duplicate, challenge):
                    server.sendto(ack.to_bytes(), addr)
                while not done.is_set():
                    try:
                        raw, addr = server.recvfrom(4096)
                    except socket.timeout:
                        continue
                    hb = NodeHeartbeat.from_bytes(raw)
                    ack, _ = registry.update_heartbeat(hb, addr, RADIO)
                    server.sendto(ack.to_bytes(), addr)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=peer, daemon=True)
        worker.start()
        try:
            assert client.hunt_once([157])
            assert registry.get_node(1).readiness == NodeReadiness.READY
        finally:
            done.set()
            worker.join(timeout=2)
            client.stop()
        assert not worker.is_alive()
        assert not errors


def test_old_null_duplicate_cannot_invalidate_new_generation_confirmation():
    registry = NodeHorizonRegistry()
    old = heartbeat("unseen-old", 9038344)
    old_challenge, _ = registry.update_heartbeat(old, ADDR, RADIO)
    new = heartbeat("new", 62406)
    new_challenge, _ = registry.update_heartbeat(new, ADDR, RADIO)
    registry.update_heartbeat(old, ADDR, RADIO)
    ack, _ = registry.update_heartbeat(replace(new, session=new_challenge.session), ADDR, RADIO)
    assert ack.confirmed
    assert registry.get_node(1).timestamp_ms == 62406
    stale, _ = registry.update_heartbeat(replace(old, session=old_challenge.session), ADDR, RADIO)
    assert not stale.confirmed
    assert registry.get_node(1).timestamp_ms == 62406


@pytest.mark.parametrize("session", ["未知会话", "\ud800"])
def test_unicode_session_is_rejected_without_breaking_cookie_validation(session):
    registry = NodeHorizonRegistry()
    request = heartbeat("new", 62406)
    challenge, _ = registry.update_heartbeat(request, ADDR, RADIO)
    rejected, _ = registry.update_heartbeat(replace(request, session=session), ADDR, RADIO)
    assert not rejected.confirmed
    assert rejected.session == challenge.session
    accepted, _ = registry.update_heartbeat(replace(request, session=challenge.session), ADDR, RADIO)
    assert accepted.confirmed
