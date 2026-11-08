"""射频可靠交付：真实动态端口 loopback，按消息类型定向丢包。"""
import json
import threading
import time

import pytest

from wfb_ng.tests import test_fl_radio_switch as network


@pytest.fixture
def radio_network():
    case = network.TestTwoPhaseRadioSwitchNetworkLoop()
    case.setUp()
    try:
        case._bring_clients_online()
        yield case
    finally:
        case.tearDown()
        case.doCleanups()


@pytest.mark.parametrize("lost", ["CONFIG_RADIO_PREPARE", "PREPARE_ACK", "CONFIG_RADIO_COMMIT"])
def test_first_packet_loss_recovers_without_moving_deadline(radio_network, monkeypatch, lost):
    case = radio_network
    client = case.client1
    seen = []
    commits = []
    prepared = []
    applied = []
    lease_arms = []
    original_arm = client.lease_watchdog.arm

    def arm(timeout_seconds=None):
        lease_arms.append(timeout_seconds)
        return original_arm(timeout_seconds)

    monkeypatch.setattr(client.lease_watchdog, "arm", arm)
    client.on_radio_prepare = lambda *args: prepared.append(args)
    client.on_radio_applied = lambda config: applied.append(time.monotonic())
    original_handle = client.handle_broadcast
    original_send = client._send_unicast_datagram
    original_broadcast = case.server.broadcast_downlink

    def handle(message):
        kind = message.get("type")
        if kind == lost:
            seen.append(dict(message))
            if len(seen) == 1:
                return
        return original_handle(message)

    def send(data):
        message = json.loads(data)
        if message.get("type") == lost:
            seen.append(message)
            if len(seen) == 1:
                return
        return original_send(data)

    def broadcast(message):
        if message["type"] == "CONFIG_RADIO_COMMIT":
            commits.append((time.monotonic(), dict(message)))
        return original_broadcast(message)

    monkeypatch.setattr(client, "handle_broadcast", handle)
    monkeypatch.setattr(client, "_send_unicast_datagram", send)
    monkeypatch.setattr(case.server, "broadcast_downlink", broadcast)
    result = case.server.reconfigure_radio(
        {"channel": 149}, [1, 2], job_id="reliable-radio",
        prepare_timeout_seconds=.35, commit_delay_seconds=.20,
        commit_timeout_seconds=.35, lease_timeout_seconds=.6,
        ping_interval_seconds=.025)
    assert result.success, result.error_message
    assert len(seen) >= 2
    assert len(prepared) == 1, "重复 PREPARE 不得重新取得 owner"
    assert len(applied) == 1, "重复 COMMIT 不得重新应用"
    assert len(lease_arms) == 1, "重复 COMMIT 不得重新启动租约"
    assert len(commits) >= 3
    first_time, first = commits[0]
    for sent, message in commits[1:]:
        assert message["delay_ms"] < first["delay_ms"]
        assert abs(sent + message["delay_ms"] / 1000 -
                   (first_time + first["delay_ms"] / 1000)) < .015
        assert {k: v for k, v in message.items() if k != "delay_ms"} == {
            k: v for k, v in first.items() if k != "delay_ms"}
    assert applied[0] < first_time + .29, "重复 COMMIT 不得推迟原切换期限"
    if lost == "CONFIG_RADIO_PREPARE":
        assert seen[0] == seen[1], "重发必须保持同一 proposal"


@pytest.mark.parametrize("cancel", [False, True])
def test_missing_prepare_has_hard_deadline_and_cancellable_wait(radio_network, monkeypatch, cancel):
    case = radio_network
    stop = threading.Event()
    sent = []
    original = case.server.broadcast_downlink
    first_prepare = threading.Event()

    def broadcast(message):
        sent.append((time.monotonic(), message["type"]))
        if message["type"] == "CONFIG_RADIO_PREPARE":
            first_prepare.set()
            return  # 所有 PREPARE 丢失：缺端绝不能确认。
        return original(message)

    monkeypatch.setattr(case.server, "broadcast_downlink", broadcast)
    results = []
    worker = threading.Thread(target=lambda: results.append(case.server.reconfigure_radio(
        {"channel": 149}, [1, 2], prepare_timeout_seconds=.18 if not cancel else 3,
        commit_timeout_seconds=.1, ping_interval_seconds=.025 if not cancel else .8,
        stop_event=stop)))
    started = time.monotonic()
    worker.start()
    try:
        assert first_prepare.wait(.5)
        if cancel:
            stop.set()
        worker.join(.4 if cancel else .6)
        assert not worker.is_alive(), "取消必须唤醒等待，硬截止不得因重发续期"
        assert not results[0].success
        assert results[0].failed_phase == "PREPARE"
        assert not any(kind in ("CONFIG_RADIO_COMMIT", "RADIO_SWITCH_CONFIRMED") for _, kind in sent)
        if not cancel:
            prepares = [t for t, kind in sent if kind == "CONFIG_RADIO_PREPARE"]
            assert len(prepares) >= 3
            assert prepares[-1] - started < .20
            assert time.monotonic() - started < .35
    finally:
        stop.set()
        worker.join(4)


def test_commit_wait_cancellation_wakes_before_ping_interval(radio_network, monkeypatch):
    case = radio_network
    stop = threading.Event()
    committing = threading.Event()
    original = case.server.broadcast_downlink
    messages = []

    def broadcast(message):
        messages.append(message["type"])
        result = original(message)
        if message["type"] == "CONFIG_RADIO_COMMIT":
            committing.set()
        return result

    monkeypatch.setattr(case.server, "broadcast_downlink", broadcast)
    results = []
    worker = threading.Thread(target=lambda: results.append(case.server.reconfigure_radio(
        {"channel": 149}, [1, 2], prepare_timeout_seconds=.5,
        commit_delay_seconds=3, commit_timeout_seconds=.2,
        lease_timeout_seconds=.6, ping_interval_seconds=.8, stop_event=stop)))
    worker.start()
    try:
        assert committing.wait(1)
        stopped = time.monotonic()
        stop.set()
        worker.join(.4)
        assert not worker.is_alive(), "COMMIT 延迟等待必须由 stop_event 直接唤醒"
        assert time.monotonic() - stopped < .4
        assert not results[0].success
        assert results[0].failed_phase == "COMMIT"
        assert "RADIO_SWITCH_CONFIRMED" not in messages
        assert case.server.active_radio_config.channel == 157
    finally:
        stop.set()
        worker.join(4)


@pytest.mark.parametrize("name", [
    "ping_interval_seconds", "prepare_timeout_seconds", "commit_timeout_seconds",
    "lease_timeout_seconds", "commit_delay_seconds",
])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), -float("inf"),
                                   True, False, "1", None])
def test_invalid_timing_rejected_before_reservation_or_broadcast(monkeypatch, name, value):
    from unittest.mock import Mock
    from wfb_ng.fl.control import ControlPlaneServer
    from wfb_ng.fl.errors import FLRuntimeError
    from wfb_ng.fl.radio import RadioConfig

    server = ControlPlaneServer(RadioConfig(channel=157))
    broadcast = Mock()
    reserve = Mock()
    monkeypatch.setattr(server, "broadcast_downlink", broadcast)
    monkeypatch.setattr(server, "begin_job_liveness_lease", reserve)
    # 存在作业监视时有效事务会取得 liveness lease，校验失败必须在此前退出。
    server._liveness_watches["timing-review"] = object()
    with pytest.raises(FLRuntimeError) as error:
        server.reconfigure_radio({"channel": 149}, [1], job_id="timing-review",
                                 **{name: value})
    assert error.value.error_code == "invalid_radio_timing"
    assert name in str(error.value)
    broadcast.assert_not_called()
    reserve.assert_not_called()
    assert server._active_switch_session is None
    assert server.active_radio_config.channel == 157


@pytest.mark.parametrize("name", [
    "ping_interval_seconds", "prepare_timeout_seconds", "commit_timeout_seconds",
    "lease_timeout_seconds",
])
def test_zero_timeout_rejected_before_broadcast(monkeypatch, name):
    test_invalid_timing_rejected_before_reservation_or_broadcast(monkeypatch, name, 0)


def test_zero_commit_delay_sends_once_and_completes(radio_network, monkeypatch):
    case = radio_network
    commits = []
    original = case.server.broadcast_downlink

    def broadcast(message):
        if message["type"] == "CONFIG_RADIO_COMMIT":
            commits.append(dict(message))
        return original(message)

    monkeypatch.setattr(case.server, "broadcast_downlink", broadcast)
    result = case.server.reconfigure_radio(
        {"channel": 149}, [1, 2], prepare_timeout_seconds=.35,
        commit_delay_seconds=0, commit_timeout_seconds=.35,
        lease_timeout_seconds=.6, ping_interval_seconds=.025)
    assert result.success, result.error_message
    assert len(commits) == 1
    assert commits[0]["delay_ms"] == 0
