"""工单 04：任务准备、轮次完成和错误报告控制面契约。"""

import json
import socket
import threading
import time

import pytest

from wfb_ng.fl.control import (
    ControlPlaneClient,
    ControlPlaneServer,
    ClientNodeState,
    NodeHeartbeat,
    MAX_CONTROL_DATAGRAM_BYTES,
)
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.radio import RadioConfig
from wfb_ng.tests.test_fl_training_host import isolated_socket_boundary


def _server():
    return ControlPlaneServer(
        active_radio_config=RadioConfig(
            channel=157,
            radio_txpower_dbm=12,
            uplink_mcs=6,
            downlink_mcs=3,
        ),
        bind_host="127.0.0.1",
        bind_port=0,
        broadcast_addr="127.0.0.1",
        broadcast_port=0,
    )


def _decode_reply(reply):
    assert reply and len(reply) == 1
    return json.loads(reply[0].decode("utf-8"))


def _task_ready(job_id="job-1", node_id=1, report_id="ready-1", report=None):
    return {
        "type": "TASK_READY",
        "report_id": report_id,
        "job_id": job_id,
        "node_id": node_id,
        "report": report if report is not None else {"format": "reference"},
    }


def _round_ready(job_id="job-1", node_id=1, round_index=1, report_id="round-1"):
    return {
        "type": "ROUND_READY",
        "report_id": report_id,
        "job_id": job_id,
        "node_id": node_id,
        "round_index": round_index,
    }


def _task_error(job_id="job-1", node_id=1, phase="prepare", report_id="error-1"):
    payload = {
        "type": "TASK_ERROR",
        "report_id": report_id,
        "job_id": job_id,
        "node_id": node_id,
        "phase": phase,
        "error_code": "reference_invalid",
        "reason": "参考模型校验失败",
    }
    if phase == "round":
        payload["round_index"] = 1
    return payload


class _CaptureBroadcastSocket:
    """捕获广播字节，便于验证 UTF-8 编码和边界。"""

    def __init__(self):
        self.sent = []

    def sendto(self, data, address):
        self.sent.append((data, address))
        return len(data)


class _DropFirstRoundAckSocket:
    """在服务端首次发送末轮 ACK 时模拟网络丢包并触发终态清理。"""

    def __init__(self, sock, on_drop):
        self._sock = sock
        self._on_drop = on_drop
        self.dropped = False

    def recvfrom(self, size):
        return self._sock.recvfrom(size)

    def sendto(self, data, address):
        try:
            message = json.loads(data.decode("utf-8"))
        except (UnicodeError, ValueError):
            message = None
        if (not self.dropped and isinstance(message, dict)
                and message.get("type") == "ROUND_READY_ACK"):
            self.dropped = True
            self._on_drop()
            return len(data)
        return self._sock.sendto(data, address)

    def close(self):
        return self._sock.close()


def test_task_ready_requires_report_and_validates_before_ack():
    server = _server()
    calls = []

    def validate(payload, source):
        calls.append((payload, source))
        if payload["report"].get("format") != "reference":
            raise FLRuntimeError("bad_reference", "参考模型格式无效")

    server.prepare_task_readiness("job-1", [1, 2], report_validator=validate)

    bare = {
        "type": "TASK_READY",
        "report_id": "bare-1",
        "job_id": "job-1",
        "node_id": 1,
    }
    rejected = _decode_reply(server.handle_datagram(json.dumps(bare).encode(), ("10.0.0.1", 1)))
    assert rejected["type"] == "TASK_READY_ACK"
    assert rejected["ok"] is False
    assert rejected["reason"] == "TASK_READY_REPORT_REQUIRED"
    assert calls == []

    valid = _task_ready()
    accepted = _decode_reply(server.handle_datagram(json.dumps(valid).encode(), ("10.0.0.1", 2)))
    assert accepted == {
        "type": "TASK_READY_ACK",
        "report_id": "ready-1",
        "job_id": "job-1",
        "node_id": 1,
        "ok": True,
    }
    assert calls == [(valid, ("10.0.0.1", 2))]
    assert server.wait_for_task_readiness(0) is False
    assert server.get_task_preparation_reports("job-1")[1]["report"] == {"format": "reference"}

    invalid = _task_ready(node_id=2, report_id="bad-1", report={"format": "other"})
    rejected = _decode_reply(server.handle_datagram(json.dumps(invalid).encode(), ("10.0.0.2", 3)))
    assert rejected["ok"] is False
    assert rejected["reason"] == "参考模型格式无效"
    assert calls[-1] == (invalid, ("10.0.0.2", 3))

    valid_two = _task_ready(node_id=2, report_id="ready-2")
    accepted = _decode_reply(
        server.handle_datagram(json.dumps(valid_two).encode(), ("10.0.0.2", 4))
    )
    assert accepted["ok"] is True
    assert server.wait_for_task_readiness(0) is True


def test_task_ready_duplicate_is_idempotent_but_conflict_is_rejected():
    server = _server()
    server.prepare_task_readiness("job-1", [1])
    payload = _task_ready()
    first = _decode_reply(server.handle_datagram(json.dumps(payload).encode(), ("10.0.0.1", 1)))
    duplicate = _decode_reply(server.handle_datagram(json.dumps(payload).encode(), ("10.0.0.1", 9)))
    assert first["ok"] is True
    assert duplicate["ok"] is True
    assert server.get_task_preparation_reports("job-1")[1]["report"] == {"format": "reference"}

    conflict = dict(payload, report={"format": "changed"})
    rejected = _decode_reply(server.handle_datagram(json.dumps(conflict).encode(), ("10.0.0.1", 1)))
    assert rejected["ok"] is False
    assert rejected["reason"] == "REPORT_ID_CONFLICT"

    another_identity = dict(payload, report_id="ready-2")
    rejected = _decode_reply(
        server.handle_datagram(json.dumps(another_identity).encode(), ("10.0.0.1", 1))
    )
    assert rejected["ok"] is False
    assert rejected["reason"] == "REPORT_ID_CONFLICT"


def test_round_barrier_does_not_accept_heartbeat_ack():
    server = _server()
    server.prepare_task_readiness("job-1", [1, 2])
    for node_id in (1, 2):
        payload = _task_ready(node_id=node_id, report_id=f"ready-{node_id}")
        assert _decode_reply(
            server.handle_datagram(json.dumps(payload).encode(), ("10.0.0.%d" % node_id, 1))
        )["ok"] is True
    assert server.wait_for_task_readiness(0) is True
    server.prepare_round_completion("job-1", 1)

    heartbeat = NodeHeartbeat(
        node_id=1,
        state=ClientNodeState.RUNNING.value,
        elapsed_ms=0,
        current_channel=157,
        txpower_dbm=12,
        uplink_mcs=6,
        timestamp_ms=1,
    )
    heartbeat_ack = _decode_reply(server.handle_datagram(heartbeat.to_bytes(), ("10.0.0.1", 1)))
    assert heartbeat_ack == {
        "ack": True, "heartbeat_timestamp_ms": 1, "channel": 157,
        "boot_id": heartbeat.boot_id, "session": heartbeat_ack["session"], "confirmed": False,
    }
    assert server.wait_for_round_completion("job-1", 1, 0) is False

    first = _round_ready(node_id=1)
    assert _decode_reply(
        server.handle_datagram(json.dumps(first).encode(), ("10.0.0.1", 2))
    )["ok"] is True
    assert server.wait_for_round_completion("job-1", 1, 0) is False

    second = _round_ready(node_id=2, report_id="round-2")
    assert _decode_reply(
        server.handle_datagram(json.dumps(second).encode(), ("10.0.0.2", 2))
    )["ok"] is True
    assert server.wait_for_round_completion("job-1", 1, 0) is True


def test_task_and_round_errors_wake_waiters_and_notify_handler():
    server = _server()
    observed = []
    server.register_task_error_handler(observed.append)
    server.prepare_task_readiness("job-1", [1])

    error = _task_error()
    ack = _decode_reply(server.handle_datagram(json.dumps(error).encode(), ("10.0.0.1", 1)))
    assert ack["type"] == "TASK_ERROR_ACK"
    assert ack["ok"] is True
    assert observed[0]["error_code"] == "reference_invalid"
    with pytest.raises(FLRuntimeError) as caught:
        server.wait_for_task_readiness(1)
    assert caught.value.error_code == "reference_invalid"
    assert caught.value.details["phase"] == "prepare"

    # 新开一项任务验证轮次错误，错误报告不应被准备阶段消费。
    server.prepare_task_readiness("job-2", [1])
    ready = _task_ready(job_id="job-2", report_id="ready-2")
    server.handle_datagram(json.dumps(ready).encode(), ("10.0.0.1", 1))
    assert server.wait_for_task_readiness(0) is True
    server.prepare_round_completion("job-2", 1)
    round_error = _task_error(job_id="job-2", phase="round", report_id="error-2")
    server.handle_datagram(json.dumps(round_error).encode(), ("10.0.0.1", 1))
    with pytest.raises(FLRuntimeError) as caught:
        server.wait_for_round_completion("job-2", 1, 1)
    assert caught.value.error_code == "reference_invalid"
    assert caught.value.details["round_index"] == 1


def test_reports_reject_invalid_phase_and_nonparticipant():
    server = _server()
    server.prepare_task_readiness("job-1", [1])
    nonparticipant = _task_ready(node_id=2, report_id="other-node")
    rejected = _decode_reply(
        server.handle_datagram(json.dumps(nonparticipant).encode(), ("10.0.0.2", 1))
    )
    assert rejected["ok"] is False
    assert rejected["reason"] == "REPORT_NOT_PARTICIPANT"

    invalid_error = _task_error(report_id="bad-phase")
    invalid_error["phase"] = "training"
    rejected = _decode_reply(
        server.handle_datagram(json.dumps(invalid_error).encode(), ("10.0.0.1", 1))
    )
    assert rejected["ok"] is False
    assert rejected["reason"] == "INVALID_PHASE"


def test_control_report_payload_is_capped_at_4096_bytes():
    server = _server()
    server.prepare_task_readiness("job-1", [1])
    oversized = json.dumps(_task_ready(report={"blob": "x" * 5000})).encode()
    assert len(oversized) > MAX_CONTROL_DATAGRAM_BYTES
    assert server.handle_datagram(oversized, ("10.0.0.1", 1)) is None

    client = ControlPlaneClient(node_id=1, tun_ip="10.80.0.11")
    with pytest.raises(FLRuntimeError) as caught:
        client.notify_task_ready("job-1", {"blob": "x" * 5000}, timeout=1)
    assert caught.value.error_code == "control_report_too_large"


def test_broadcast_uses_utf8_and_rejects_byte_overflow():
    server = _server()
    capture = _CaptureBroadcastSocket()
    server._broadcast_sock = capture

    unicode_task = {
        "type": "TASK_ANNOUNCE",
        "algorithm_config": {"数据集": "本地中文参数"},
    }
    server.broadcast_downlink(unicode_task)
    raw = capture.sent[-1][0]
    assert "本地中文参数".encode("utf-8") in raw
    assert json.loads(raw.decode("utf-8")) == unicode_task

    def message_of_size(size):
        empty = json.dumps(
            {"x": ""}, ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        return {"x": "a" * (size - len(empty))}

    exact = message_of_size(MAX_CONTROL_DATAGRAM_BYTES)
    server.broadcast_downlink(exact)
    assert len(capture.sent[-1][0]) == MAX_CONTROL_DATAGRAM_BYTES

    with pytest.raises(FLRuntimeError) as too_large:
        server.broadcast_downlink(message_of_size(MAX_CONTROL_DATAGRAM_BYTES + 1))
    assert too_large.value.error_code == "control_message_too_large"

    full_task = {
        "type": "TASK_ANNOUNCE",
        "job_id": "job-1",
        "algorithm_config": {"说明": "中文参数" * 2000},
    }
    with pytest.raises(FLRuntimeError) as full_task_error:
        server.broadcast_downlink(full_task)
    assert full_task_error.value.error_code == "control_message_too_large"

    with pytest.raises(FLRuntimeError) as nan_error:
        server.broadcast_downlink({"type": "TASK_ANNOUNCE", "value": float("nan")})
    assert nan_error.value.error_code == "invalid_control_message"


def test_lost_final_round_ack_retries_from_receipt_after_clear():
    server = _server()
    server.start()
    try:
        real_socket = server._server_sock
        port = real_socket.getsockname()[1]
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            server_host="127.0.0.1",
            server_port=port,
        )
        server.prepare_task_readiness("job-1", [1])
        dropper = _DropFirstRoundAckSocket(
            real_socket, lambda: server.clear_task_readiness("job-1")
        )
        server._server_sock = dropper

        client.notify_task_ready("job-1", {"format": "reference"}, timeout=1)
        assert server.wait_for_task_readiness(1) is True
        server.prepare_round_completion("job-1", 1)

        # 首个末轮 ACK 被丢弃时，回调已完成终态清理；客户端随后用同一
        # report_id 重试，最近 receipt 必须返回幂等成功 ACK。
        client.notify_round_complete("job-1", 1, timeout=1)
        assert dropper.dropped is True

        new_old_report = _round_ready(
            job_id="job-1", node_id=1, round_index=1, report_id="old-new-report"
        )
        rejected = _decode_reply(
            server.handle_datagram(
                json.dumps(new_old_report).encode("utf-8"), ("127.0.0.1", 2)
            )
        )
        assert rejected["ok"] is False
        assert rejected["reason"] == "REPORT_ID_CONFLICT"

        # 新任务启动后，旧作业的新报告也不能进入新屏障。
        server.prepare_task_readiness("job-2", [1])
        old_task_report = _task_ready(
            job_id="job-1", node_id=1, report_id="old-task-new"
        )
        rejected = _decode_reply(
            server.handle_datagram(
                json.dumps(old_task_report).encode("utf-8"), ("127.0.0.1", 3)
            )
        )
        assert rejected["ok"] is False
        assert rejected["reason"] == "TASK_NOT_PREPARED"
    finally:
        server.stop()


def test_client_round_and_error_reports_use_dedicated_ack_types():
    server = _server()
    server.start()
    try:
        port = server._server_sock.getsockname()[1]
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            server_host="127.0.0.1",
            server_port=port,
        )
        server.prepare_task_readiness("job-1", [1])
        client.notify_task_ready("job-1", {"format": "reference"}, timeout=1)
        assert server.wait_for_task_readiness(1) is True

        server.prepare_round_completion("job-1", 1)
        client.notify_round_complete("job-1", 1, timeout=1)
        assert server.wait_for_round_completion("job-1", 1, 1) is True

        server.prepare_task_readiness("job-2", [1])
        client.notify_task_error(
            "job-2", "prepare", "reference_invalid", "参考模型格式无效", timeout=1
        )
        with pytest.raises(FLRuntimeError) as caught:
            server.wait_for_task_readiness(1)
        assert caught.value.error_code == "reference_invalid"
    finally:
        server.stop()


def test_client_retries_same_report_id_until_matching_dedicated_ack():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(2)
    port = receiver.getsockname()[1]
    received = []
    done = threading.Event()

    def serve():
        try:
            while True:
                data, source = receiver.recvfrom(MAX_CONTROL_DATAGRAM_BYTES + 1)
                payload = json.loads(data.decode())
                received.append(payload)
                if len(received) == 1:
                    # 普通心跳 ACK 不能确认 TASK_READY。
                    receiver.sendto(json.dumps({
                        "ack": True, "heartbeat_timestamp_ms": 1, "channel": 157,
                        "boot_id": "unrelated", "session": "unrelated", "confirmed": True,
                    }).encode(), source)
                    continue
                receiver.sendto(json.dumps({
                    "type": "TASK_READY_ACK",
                    "report_id": payload["report_id"],
                    "job_id": payload["job_id"],
                    "node_id": payload["node_id"],
                    "ok": True,
                }).encode(), source)
                return
        finally:
            done.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            server_host="127.0.0.1",
            server_port=port,
        )
        client.notify_task_ready("job-1", {"format": "reference"}, timeout=1)
        assert len(received) == 2
        assert received[0]["report_id"] == received[1]["report_id"]
    finally:
        done.wait(1)
        receiver.close()
        thread.join(1)
