"""通过公开客户端接口和真实回环 UDP 验证空闲心跳监督。"""

import json
import queue
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from wfb_ng.fl import control
from wfb_ng.fl.control import ClientNodeState, ControlPlaneClient, NodeHeartbeat
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
from wfb_ng.tests.test_fl_heartbeat_ack_identity import loopback_network, receive_heartbeat


@pytest.fixture
def heartbeat_peer(loopback_network, monkeypatch):
    """仅替换本机时钟边界，回执通过真实 UDP 与动态端口传递。"""
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(control, "time", SimpleNamespace(
        monotonic=lambda: clock.now,
        monotonic_ns=lambda: int(clock.now * 1_000_000_000),
        time=time.time, sleep=time.sleep,
    ))
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(0.1)
    impostor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    impostor.bind(("127.0.0.1", 0))
    client = ControlPlaneClient(
        node_id=4, tun_ip="127.0.0.1",
        network_adapter=MockNetworkAdapter(["wlan-test"]), air_interface="wlan-test",
        initial_channel=149, server_host="127.0.0.1",
        server_port=server.getsockname()[1], broadcast_port=server.getsockname()[1],
        max_attempts_per_channel=1,
    )
    actions = queue.Queue()
    errors = []
    finished = threading.Event()
    received = []
    reports = queue.Queue()

    def reply():
        try:
            while not finished.is_set():
                try:
                    raw, address = receive_heartbeat(server)
                except socket.timeout:
                    continue
                message = json.loads(raw)
                if message.get("type") != "NODE_HEARTBEAT":
                    reports.put(message)
                    continue
                heartbeat = NodeHeartbeat.from_bytes(raw)
                received.append(heartbeat)
                mode, arrival = actions.get(timeout=1)
                if arrival is not None:
                    clock.now = arrival
                payload = {"ack": True, "heartbeat_timestamp_ms": heartbeat.timestamp_ms,
                           "channel": heartbeat.current_channel, "boot_id": heartbeat.boot_id,
                           "session": heartbeat.session, "confirmed": True}
                sender = server
                if mode == "drop":
                    # 无关数据报只唤醒接收，时钟到达本次截止时间后结束探测。
                    clock.now += 0.25
                    payload = {"type": "UNRELATED"}
                elif mode == "old-ts":
                    payload["heartbeat_timestamp_ms"] -= 1
                elif mode == "boot":
                    payload["boot_id"] = "previous-boot"
                elif mode == "channel":
                    payload["channel"] = 157
                elif mode == "source":
                    sender = impostor
                sender.sendto(json.dumps(payload).encode(), address)
        except BaseException as exc:
            errors.append(exc)

    peer = threading.Thread(target=reply, daemon=True)
    peer.start()
    # 通过真实 HUNTING 往返建立新协议会话；下列场景均在已确认会话中验证活性。
    actions.put(("valid", None))
    assert client.send_heartbeat_once() is not None
    received.clear()

    def exchange(at, mode="drop", arrival=None, operation=None):
        clock.now = at
        actions.put((mode, arrival))
        return (operation or client.send_heartbeat_once)()

    try:
        yield SimpleNamespace(client=client, clock=clock, exchange=exchange,
                              actions=actions, received=received, reports=reports)
    finally:
        finished.set()
        peer.join(timeout=2)
        client.stop()
        server.close()
        impostor.close()
        assert not peer.is_alive(), "回环服务线程超出截止时间"
        assert not errors, repr(errors)
        assert actions.empty(), "预定公开心跳未发出"


def test_idle_packet_loss_waits_for_ten_second_liveness_window(heartbeat_peer):
    """一两次丢包不触发寻频，持续失联仅在探测结束达到十秒时触发。"""
    p = heartbeat_peer
    p.exchange(100, "valid", operation=lambda: p.client.notify_state_change(ClientNodeState.IDLE))
    for missed, at in enumerate((102, 104, 106, 108, 109.7), start=1):
        assert p.exchange(at) is None
        assert p.client.state == ClientNodeState.IDLE
        assert p.client.missed_acks == missed
    assert p.exchange(109.75) is None
    assert p.client.missed_acks == 6
    assert p.client.state == ClientNodeState.HUNTING


def test_valid_ack_renews_from_request_send_time(heartbeat_peer):
    """有效回执恢复确认，但迟到的接收时刻不额外延长十秒窗口。"""
    p = heartbeat_peer
    p.exchange(100, "valid", operation=lambda: p.client.notify_state_change(ClientNodeState.IDLE))
    p.exchange(102)
    p.exchange(104)
    assert p.exchange(106, "valid", arrival=106.2) is not None
    assert p.client.missed_acks == 0
    assert p.exchange(115.7) is None
    assert p.client.state == ClientNodeState.IDLE
    assert p.exchange(115.75) is None
    assert p.client.state == ClientNodeState.HUNTING


@pytest.mark.parametrize("mode", ["old-ts", "channel", "source", "boot"])
def test_invalid_ack_cannot_renew_liveness(heartbeat_peer, mode):
    """旧请求、错信道及错来源不能续期，即使其他身份均有效。"""
    p = heartbeat_peer
    p.exchange(100, "valid", operation=lambda: p.client.notify_state_change(ClientNodeState.IDLE))
    assert p.exchange(106, mode, arrival=106.1) is None
    assert p.client.state == ClientNodeState.IDLE
    assert p.exchange(108) is None
    assert p.client.state == ClientNodeState.IDLE
    assert p.exchange(109.75) is None
    assert p.client.state == ClientNodeState.HUNTING


def test_unacknowledged_idle_entry_has_finite_nonrenewable_window(heartbeat_peer):
    """首次待命有有限起点，活动后返回待命不能续期既有确认。"""
    p = heartbeat_peer
    p.exchange(100, operation=lambda: p.client.notify_state_change(ClientNodeState.IDLE))
    assert p.exchange(109.7) is None
    assert p.client.state == ClientNodeState.IDLE
    p.client.notify_state_change(ClientNodeState.IDLE)
    assert p.exchange(109.75) is None
    assert p.client.state == ClientNodeState.HUNTING
    p.exchange(120, "valid", operation=lambda: p.client.notify_state_change(ClientNodeState.RUNNING))
    p.exchange(130, operation=lambda: p.client.notify_state_change(ClientNodeState.IDLE))
    assert p.exchange(130.5) is None
    assert p.client.state == ClientNodeState.HUNTING


def test_hunt_ack_seeds_liveness_and_recovers_ack_updates(heartbeat_peer):
    """寻频回执建立起点，即刻待命回执丢失也受监督，重新寻频后仍可续期。"""
    p = heartbeat_peer
    p.actions.put(("valid", None))
    assert p.exchange(100, operation=lambda: p.client.hunt_once(ladder=[149]))
    assert p.client.locked_channel == 149
    assert p.exchange(109.7) is None
    assert p.client.state == ClientNodeState.IDLE
    assert p.exchange(109.75) is None
    assert p.client.state == ClientNodeState.HUNTING
    p.actions.put(("valid", None))
    assert p.exchange(120, "valid", operation=lambda: p.client.hunt_once(ladder=[149]))
    assert p.exchange(126, "valid", arrival=126.2) is not None
    assert p.exchange(135.7) is None
    assert p.client.state == ClientNodeState.IDLE
    assert p.exchange(135.75) is None
    assert p.client.state == ClientNodeState.HUNTING


def test_long_custom_probe_checks_liveness_even_after_valid_ack(heartbeat_peer):
    """自定义长等待收到有效回执后，仍按发送锚检查探测结束时的失活。"""
    p = heartbeat_peer
    p.exchange(100, "valid", operation=lambda: p.client.notify_state_change(ClientNodeState.IDLE))
    assert p.exchange(102, "valid", arrival=112,
                      operation=lambda: p.client.send_heartbeat_once(timeout=20)) is not None
    assert p.client.missed_acks == 0
    assert p.client.state == ClientNodeState.HUNTING
    assert p.client.attempt_timeout_seconds == 0.23


def test_active_state_does_not_apply_idle_liveness(heartbeat_peer):
    """活动作业不会因空闲监督阈值转入寻频。"""
    p = heartbeat_peer
    assert p.client.idle_interval_seconds == 2.0
    p.exchange(100, "valid", operation=lambda: p.client.notify_state_change(ClientNodeState.RUNNING))
    assert p.exchange(120) is None
    assert p.client.state == ClientNodeState.RUNNING
    assert p.client.missed_acks == 0


def test_prepare_suppresses_hunting_without_renewing_liveness(heartbeat_peer):
    """准备事务只保留禁止寻频行为，取消后仍使用事务前的活性起点。"""
    p = heartbeat_peer
    p.exchange(100, "valid", operation=lambda: p.client.notify_state_change(ClientNodeState.IDLE))
    p.client.bind_job_context("job")
    proposal = {"session_id": "prepare", "job_id": "job", "target_nodes": [4],
                "channel": 149, "patch": {"radio_txpower_dbm": 13}}
    p.clock.now = 109
    p.client.handle_broadcast(dict(proposal, type="CONFIG_RADIO_PREPARE"))
    assert p.reports.get(timeout=1)["type"] == "PREPARE_ACK"
    assert p.client.radio_switch_pending
    assert p.exchange(110) is None
    assert p.client.state == ClientNodeState.IDLE
    p.client.handle_broadcast(dict(proposal, type="CONFIG_RADIO_ABORT"))
    assert p.reports.get(timeout=1)["type"] == "RADIO_SWITCH_ROLLBACK_ACK"
    assert not p.client.radio_switch_pending
    assert p.exchange(111) is None
    assert p.client.state == ClientNodeState.HUNTING


def test_state_changes_without_any_ack_do_not_extend_initial_window(heartbeat_peer):
    """从未确认时也只初始化一次，反复状态迁移不能获得永久租约。"""
    p = heartbeat_peer
    p.exchange(100, operation=lambda: p.client.notify_state_change(ClientNodeState.IDLE))
    p.exchange(104, operation=lambda: p.client.notify_state_change(ClientNodeState.RUNNING))
    p.exchange(108, operation=lambda: p.client.notify_state_change(ClientNodeState.IDLE))
    assert p.exchange(109.7) is None
    assert p.client.state == ClientNodeState.IDLE
    assert p.exchange(109.75) is None
    assert p.client.state == ClientNodeState.HUNTING
