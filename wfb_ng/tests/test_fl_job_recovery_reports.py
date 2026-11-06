"""06 控制面恢复报告、屏障取消和作业级 liveness 接缝。"""

import json
import threading
import time

import pytest

from wfb_ng.fl.control import (
    ClientNodeState,
    ControlPlaneClient,
    ControlPlaneServer,
    MAX_CONTROL_DATAGRAM_BYTES,
    NodeHeartbeat,
)
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.radio import RadioConfig


def _server(*, offline_threshold_seconds=10.0):
    return ControlPlaneServer(
        active_radio_config=RadioConfig(
            channel=157,
            radio_txpower_dbm=12,
            uplink_mcs=6,
            downlink_mcs=3,
        ),
        bind_host="127.0.0.1",
        bind_port=0,
        offline_threshold_seconds=offline_threshold_seconds,
    )


def _reply(replies):
    assert replies and len(replies) == 1
    return json.loads(replies[0].decode("utf-8"))


def _recovery(
    *,
    job_id="job-recovery",
    node_id=1,
    report_id="recovery-1-1",
    epoch=1,
    state="ready",
    error_code=None,
    reason=None,
):
    report = {
        "recovery_epoch": epoch,
        "recovery_state": state,
    }
    if state == "blocked":
        report.update({"error_code": error_code or "CLEANUP_BLOCKED", "reason": reason or "清理未完成"})
    return {
        "type": "JOB_RECOVERY",
        "report_id": report_id,
        "job_id": job_id,
        "node_id": node_id,
        "report": report,
    }


def _heartbeat(node_id, state, timestamp_ms, session=None):
    return NodeHeartbeat(
        node_id=node_id,
        state=state,
        elapsed_ms=0,
        current_channel=157,
        txpower_dbm=12,
        uplink_mcs=6,
        timestamp_ms=timestamp_ms, boot_id="liveness-test", session=session,
    ).to_bytes()


def _establish_ready(server, node_id=1, timestamp=1000):
    address = (f"10.80.0.{10 + node_id}", 9001)
    challenge = _reply(server.handle_datagram(
        _heartbeat(node_id, ClientNodeState.HUNTING.value, timestamp), address
    ))
    session = challenge["session"]
    assert not challenge["confirmed"]
    ack = _reply(server.handle_datagram(
        _heartbeat(node_id, ClientNodeState.HUNTING.value, timestamp, session), address
    ))
    assert ack["confirmed"]
    server.handle_datagram(
        _heartbeat(node_id, ClientNodeState.IDLE.value, timestamp + 1, session), address
    )
    return address, session


def test_recovery_report_uses_envelope_and_dedicated_ack():
    server = _server()
    server.prepare_task_readiness("job-recovery", [1])
    server.cancel_job_barriers("job-recovery", "terminal")
    server.prepare_job_recovery("job-recovery", [1])

    payload = _recovery()
    ack = _reply(server.handle_datagram(json.dumps(payload).encode(), ("10.0.0.1", 1)))

    assert ack == {
        "type": "JOB_RECOVERY_ACK",
        "report_id": "recovery-1-1",
        "job_id": "job-recovery",
        "node_id": 1,
        "ok": True,
    }
    assert server.wait_for_job_recovery("job-recovery", 0) is True
    assert server.get_job_recovery_reports("job-recovery") == {
        1: {
            "report_id": "recovery-1-1",
            "job_id": "job-recovery",
            "node_id": 1,
            "report": {"recovery_epoch": 1, "recovery_state": "ready"},
            "source": ("10.0.0.1", 1),
        }
    }

    duplicate = _reply(server.handle_datagram(json.dumps(payload).encode(), ("10.0.0.1", 99)))
    assert duplicate["ok"] is True


def test_recovery_blocked_then_epoch_plus_one_ready_is_progress():
    server = _server()
    server.prepare_task_readiness("job-recovery", [1, 2])
    server.cancel_job_barriers("job-recovery", "terminal")
    server.prepare_job_recovery("job-recovery", [1, 2])

    blocked = _recovery(
        node_id=1,
        report_id="recovery-1-1",
        state="blocked",
        error_code="TASK_TUN_CLEANUP_FAILED",
        reason="任务 TUN 仍存在",
    )
    assert _reply(server.handle_datagram(json.dumps(blocked).encode(), ("10.0.0.1", 1)))["ok"] is True
    assert server.wait_for_job_recovery("job-recovery", 0) is False

    ready_two = _recovery(node_id=2, report_id="recovery-2-1")
    assert _reply(server.handle_datagram(json.dumps(ready_two).encode(), ("10.0.0.2", 1)))["ok"] is True
    assert server.wait_for_job_recovery("job-recovery", 0) is False

    ready_retry = _recovery(node_id=1, report_id="recovery-1-2", epoch=2)
    assert _reply(server.handle_datagram(json.dumps(ready_retry).encode(), ("10.0.0.1", 2)))["ok"] is True
    assert server.wait_for_job_recovery("job-recovery", 0) is True
    assert server.get_job_recovery_reports("job-recovery")[1]["report"] == {
        "recovery_epoch": 2,
        "recovery_state": "ready",
    }


def test_recovery_epoch_and_terminal_state_reject_overwrite():
    server = _server()
    server.prepare_task_readiness("job-recovery", [1])
    server.cancel_job_barriers("job-recovery")
    server.prepare_job_recovery("job-recovery", [1])

    blocked = _recovery(
        report_id="recovery-1-1",
        state="blocked",
        error_code="CLEANUP_BLOCKED",
        reason="仍在清理",
    )
    assert _reply(server.handle_datagram(json.dumps(blocked).encode(), ("10.0.0.1", 1)))["ok"] is True

    stale = _recovery(report_id="recovery-1-3", epoch=3)
    rejected = _reply(server.handle_datagram(json.dumps(stale).encode(), ("10.0.0.1", 1)))
    assert rejected["ok"] is False
    assert rejected["reason"] == "RECOVERY_EPOCH_REQUIRED"

    ready = _recovery(report_id="recovery-1-2", epoch=2)
    assert _reply(server.handle_datagram(json.dumps(ready).encode(), ("10.0.0.1", 1)))["ok"] is True

    regression = _recovery(
        report_id="recovery-1-3",
        epoch=3,
        state="blocked",
        error_code="LATE_FAILURE",
        reason="迟到失败",
    )
    rejected = _reply(server.handle_datagram(json.dumps(regression).encode(), ("10.0.0.1", 1)))
    assert rejected["ok"] is False
    assert rejected["reason"] == "RECOVERY_ALREADY_READY"


def test_clear_task_readiness_archives_recovery_receipt_without_early_clear():
    server = _server()
    server.prepare_task_readiness("job-recovery", [1])
    server.cancel_job_barriers("job-recovery")
    server.prepare_job_recovery("job-recovery", [1])
    payload = _recovery()
    assert _reply(server.handle_datagram(json.dumps(payload).encode(), ("10.0.0.1", 1)))["ok"] is True

    server.clear_task_readiness("job-recovery")
    duplicate = _reply(server.handle_datagram(json.dumps(payload).encode(), ("10.0.0.1", 2)))
    assert duplicate["ok"] is True

    old_new_identity = _recovery(report_id="recovery-1-new")
    rejected = _reply(server.handle_datagram(json.dumps(old_new_identity).encode(), ("10.0.0.1", 2)))
    assert rejected["ok"] is False
    assert rejected["reason"] == "REPORT_ID_CONFLICT"


def test_cancel_job_barriers_wakes_task_and_round_but_not_recovery():
    server = _server()
    server.prepare_task_readiness("job-task", [1])
    task_result = []
    task_done = threading.Event()

    def wait_task():
        task_result.append(server.wait_for_task_readiness(2))
        task_done.set()

    task_thread = threading.Thread(target=wait_task)
    task_thread.start()
    server.cancel_job_barriers("job-task", "terminal")
    assert task_done.wait(1)
    task_thread.join(1)
    assert task_result == [False]

    server.prepare_task_readiness("job-round", [1])
    ready = _recovery(job_id="job-round")
    task_ready = {
        "type": "TASK_READY",
        "report_id": "task-ready-1",
        "job_id": "job-round",
        "node_id": 1,
        "report": {"ready": True},
    }
    assert _reply(server.handle_datagram(json.dumps(task_ready).encode(), ("10.0.0.1", 1)))["ok"] is True
    assert server.wait_for_task_readiness(0) is True
    server.prepare_round_completion("job-round", 1)
    round_result = []
    round_done = threading.Event()

    def wait_round():
        round_result.append(server.wait_for_round_completion("job-round", 1, 2))
        round_done.set()

    round_thread = threading.Thread(target=wait_round)
    round_thread.start()
    server.cancel_job_barriers("job-round", "terminal")
    assert round_done.wait(1)
    round_thread.join(1)
    assert round_result == [False]

    server.prepare_job_recovery("job-round", [1])
    assert _reply(server.handle_datagram(json.dumps(ready).encode(), ("10.0.0.1", 1)))['ok'] is True
    assert server.wait_for_job_recovery("job-round", 0) is True


def test_daemon_stop_cancels_active_recovery_wait():
    server = _server()
    server.prepare_task_readiness("job-stop", [1])
    server.prepare_job_recovery("job-stop", [1])
    result = []
    done = threading.Event()

    def wait_recovery():
        result.append(server.wait_for_job_recovery("job-stop", 2))
        done.set()

    worker = threading.Thread(target=wait_recovery)
    worker.start()
    server.stop()
    assert done.wait(1)
    worker.join(1)
    assert result == [False]


def test_liveness_watcher_emits_edges_without_blocking_datagram_handler():
    server = _server(offline_threshold_seconds=0.05)
    address, session = _establish_ready(server)
    events = []
    offline = threading.Event()
    online = threading.Event()

    def on_change(event):
        events.append(event)
        if event["state"] == "offline":
            offline.set()
        if event["state"] == "online":
            online.set()

    server.start()
    try:
        server.watch_job_liveness("job-live", [1], on_change)
        assert offline.wait(1)
        assert events[0]["state"] == "offline"

        started = time.monotonic()
        server.handle_datagram(
            _heartbeat(1, ClientNodeState.IDLE.value, 1002, session), address
        )
        assert time.monotonic() - started < 0.5
        assert online.wait(1)
        assert [event["state"] for event in events] == ["offline", "online"]
    finally:
        server.unwatch_job_liveness("job-live")
        server.stop()


def test_liveness_lease_defers_offline_edge_until_end():
    server = _server(offline_threshold_seconds=0.05)
    _establish_ready(server)
    events = []
    offline = threading.Event()
    server.start()
    try:
        server.watch_job_liveness("job-live", [1], lambda event: (events.append(event), offline.set()))
        server.begin_job_liveness_lease("job-live", "radio-session", 0.2)
        assert not offline.wait(0.1)
        server.end_job_liveness_lease("job-live", "radio-session")
        assert offline.wait(1)
        assert events[-1]["state"] == "offline"
    finally:
        server.unwatch_job_liveness("job-live")
        server.stop()


def test_stop_waits_for_blocking_liveness_callback_before_returning():
    server = _server(offline_threshold_seconds=0.05)
    _establish_ready(server)
    callback_started = threading.Event()
    callback_release = threading.Event()
    callback_finished = threading.Event()
    stop_started = threading.Event()
    stop_finished = threading.Event()

    def on_change(event):
        assert event["state"] == "offline"
        callback_started.set()
        callback_release.wait(2)
        callback_finished.set()

    server.start()
    server.watch_job_liveness("job-stop", [1], on_change)
    assert callback_started.wait(1)

    def stop_server():
        stop_started.set()
        server.stop()
        stop_finished.set()

    stopper = threading.Thread(target=stop_server)
    stopper.start()
    assert stop_started.wait(1)
    assert not stop_finished.is_set()

    callback_release.set()
    assert stop_finished.wait(1)
    stopper.join(1)
    assert callback_finished.is_set()


def test_client_recovery_notification_uses_caller_report_id_across_rebuild(monkeypatch):
    first_client = ControlPlaneClient(node_id=1, tun_ip="10.80.0.11")
    rebuilt_client = ControlPlaneClient(node_id=1, tun_ip="10.80.0.11")
    first_sent = []
    rebuilt_sent = []

    monkeypatch.setattr(first_client, "_notify_report", lambda payload, *args: first_sent.append(payload))
    monkeypatch.setattr(rebuilt_client, "_notify_report", lambda payload, *args: rebuilt_sent.append(payload))
    first_client.notify_job_recovery(
        "job-client", 1, "blocked", report_id="persisted-epoch-1",
        error_code="CLEANUP", reason="还未完成",
    )
    rebuilt_client.notify_job_recovery(
        "job-client", 1, "blocked", report_id="persisted-epoch-1",
        error_code="CLEANUP", reason="还未完成",
    )
    first_client.notify_job_recovery(
        "job-client", 2, "ready", report_id="persisted-epoch-2",
    )

    assert first_sent[0]["report_id"] == "persisted-epoch-1"
    assert rebuilt_sent[0]["report_id"] == first_sent[0]["report_id"]
    assert first_sent[0]["report"] == rebuilt_sent[0]["report"]
    assert first_sent[1]["report_id"] == "persisted-epoch-2"
    with pytest.raises(TypeError):
        first_client.notify_job_recovery("job-client", 1, "ready")


def test_client_recovery_notification_real_udp_survives_control_rebuild():
    server = _server()
    server.start()
    try:
        port = server._server_sock.getsockname()[1]
        server.prepare_task_readiness("job-client", [1])
        server.cancel_job_barriers("job-client", "terminal")
        server.prepare_job_recovery("job-client", [1])

        first_client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            server_host="127.0.0.1",
            server_port=port,
        )
        first_client.notify_job_recovery(
            "job-client", 1, "blocked", report_id="persisted-udp-epoch-1",
            error_code="CLEANUP", reason="等待资源释放", timeout=1,
        )
        assert server.wait_for_job_recovery("job-client", 0) is False

        rebuilt_client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            server_host="127.0.0.1",
            server_port=port,
        )
        rebuilt_client.notify_job_recovery(
            "job-client", 2, "ready", report_id="persisted-udp-epoch-2", timeout=1,
        )
        assert server.wait_for_job_recovery("job-client", 1) is True
        assert server.get_job_recovery_reports("job-client")[1]["report_id"] == "persisted-udp-epoch-2"
    finally:
        server.stop()


def test_recovery_report_payload_size_is_bounded():
    client = ControlPlaneClient(node_id=1, tun_ip="10.80.0.11")
    with pytest.raises(FLRuntimeError) as caught:
        client.notify_job_recovery(
            "job-client", 1, "blocked", report_id="size-check",
            error_code="CLEANUP", reason="x" * 4096,
        )
    assert caught.value.error_code == "invalid_recovery_report"
    assert MAX_CONTROL_DATAGRAM_BYTES == 4096
