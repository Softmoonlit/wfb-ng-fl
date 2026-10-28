"""公开控制面接口的换频顺序、任务接管、主机名与停止竞态回归。"""

import json
import socket
import threading

import pytest

from wfb_ng.fl.control import ClientNodeState, ControlPlaneClient, ControlPlaneServer, NodeHeartbeat
from wfb_ng.fl.radio import RadioConfig
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
from wfb_ng.tests.test_fl_heartbeat_ack_identity import loopback_network


@pytest.mark.parametrize("failure", [None, "physical", "native"])
@pytest.mark.parametrize("patch", [{"bandwidth_mhz": 20, "downlink_mcs": 3, "uftp_rate_kbps": 1000}, {"uplink_mcs": 5}])
def test_commit_rearms_after_native_configuration(loopback_network, failure, patch):
    """带宽切换先设置物理网卡与 native，再重武装；同信道调制更新无需重武装。"""
    records = []
    entered = threading.Event()

    class Adapter(MockNetworkAdapter):
        """网卡系统边界记录物理应用顺序并注入硬件失败。"""

        def set_channel(self, *args, **kwargs):
            records.append("physical")
            entered.set()
            if failure == "physical":
                raise OSError("物理切换失败")
            return super().set_channel(*args, **kwargs)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink:
        sink.bind(("127.0.0.1", 0))
        client = ControlPlaneClient(
            4, "127.0.0.1", network_adapter=Adapter(["wlan-test"]),
            air_interface="wlan-test", server_host="127.0.0.1",
            server_port=sink.getsockname()[1], broadcast_port=sink.getsockname()[1],
        )

        def native(config):
            records.append("native")
            entered.set()
            if failure == "native":
                raise OSError("native 配置失败")

        client.on_radio_applied = native
        client.on_channel_applied = lambda: records.append("rearm")
        message = {"session_id": "review", "target_nodes": [4], "patch": patch,
                   "calibration": "bandwidth_mhz" in patch}
        try:
            client.handle_broadcast(dict(message, type="CONFIG_RADIO_PREPARE"))
            client.handle_broadcast(dict(message, type="CONFIG_RADIO_COMMIT", delay_ms=0))
            assert entered.wait(1), "必须实际进入物理或 native 配置边界"
            # 属性读取取得同一状态锁，等待本次应用真实完成。
            assert client.radio_switch_pending
            if "bandwidth_mhz" in patch:
                expected = {
                    None: ["physical", "native", "rearm"],
                    "physical": ["physical"],
                    "native": ["physical", "native"],
                }[failure]
            else:
                expected = ["native"]
            assert records == expected
        finally:
            client.stop()


@pytest.mark.parametrize("operation", ["hunt", "send"])
def test_localhost_resolves_to_matching_udp_endpoint(loopback_network, operation):
    """公开接口以 localhost 为目标时，数字来源地址仍应匹配同一固定端点。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(1)
        client = ControlPlaneClient(
            4, "127.0.0.1", server_host="localhost", server_port=server.getsockname()[1],
            broadcast_port=server.getsockname()[1], max_attempts_per_channel=1,
            attempt_timeout_seconds=0.1,
        )
        errors = []

        def reply():
            try:
                for _ in range(2 if operation == "hunt" else 1):
                    data, address = server.recvfrom(4096)
                    heartbeat = NodeHeartbeat.from_bytes(data)
                    server.sendto(json.dumps({
                        "ack": True, "heartbeat_timestamp_ms": heartbeat.timestamp_ms,
                        "channel": heartbeat.current_channel,
                    }).encode(), address)
            except BaseException as exc:
                errors.append(exc)

        peer = threading.Thread(target=reply, daemon=True)
        peer.start()
        try:
            if operation == "hunt":
                assert client.hunt_once(ladder=[157])
                assert client.state == ClientNodeState.IDLE
            else:
                assert client.send_heartbeat_once() is not None
        finally:
            client.stop()
            peer.join(2)
        assert not peer.is_alive()
        assert not errors, repr(errors)


@pytest.mark.parametrize("arrival", ["entry", "after-retune", "before-ack", "timeout"])
def test_task_takeover_stops_hunt_without_retuning_or_overwriting_state(loopback_network, arrival):
    """任务回调取得信道所有权后，旧寻频不能再换频、重试或覆盖 PREPARING。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(1)
        adapter = MockNetworkAdapter(["wlan-test"])
        client = ControlPlaneClient(
            4, "127.0.0.1", network_adapter=adapter, air_interface="wlan-test",
            server_host="127.0.0.1", server_port=server.getsockname()[1],
            broadcast_port=server.getsockname()[1], max_attempts_per_channel=2,
            attempt_timeout_seconds=0.05,
        )
        errors = []
        taken = threading.Event()

        def take_over(message):
            # 与宿主任务回调相同，在 native 所有权交接前发布公开状态。
            client.state = ClientNodeState.PREPARING
            taken.set()

        client.register_broadcast_handler("TASK_ANNOUNCE", take_over)

        def announce():
            client.handle_broadcast({"type": "TASK_ANNOUNCE"})

        def reply():
            try:
                data, address = server.recvfrom(4096)
                heartbeat = NodeHeartbeat.from_bytes(data)
                announce()
                if arrival == "before-ack":
                    server.sendto(json.dumps({
                        "ack": True, "heartbeat_timestamp_ms": heartbeat.timestamp_ms,
                        "channel": heartbeat.current_channel,
                    }).encode(), address)
            except BaseException as exc:
                errors.append(exc)

        peer = None
        if arrival == "entry":
            announce()
        elif arrival == "after-retune":
            client.on_channel_applied = announce
        else:
            peer = threading.Thread(target=reply, daemon=True)
            peer.start()
        try:
            assert client.hunt_once(ladder=[149, 157]) is False
            assert taken.is_set()
            assert client.state == ClientNodeState.PREPARING
            assert client.locked_channel is None
            assert adapter.channel_history == ([] if arrival == "entry" else [149])
            # 检查真实出站队列，任务接管后不能再有旧寻频心跳。
            server.setblocking(False)
            with pytest.raises(BlockingIOError):
                server.recvfrom(4096)
        finally:
            client.stop()
            if peer is not None:
                peer.join(2)
        assert peer is None or not peer.is_alive()
        assert not errors, repr(errors)


def test_stop_notification_before_liveness_wait_still_joins(loopback_network, monkeypatch):
    """门控线程在检查循环条件后暂停，先 stop 通知再入锁也必须真实退出。"""
    ready = threading.Event()
    release = threading.Event()
    notified = threading.Event()
    stopped = threading.Event()
    errors = []
    conditions = []
    original_enter = threading.Condition.__enter__
    original_notify = threading.Condition.notify_all

    def gated_enter(condition):
        current = threading._active.get(threading.get_ident())
        if current is not None and current.name == "wfb-server-job-liveness" and not conditions:
            conditions.append(condition)
            ready.set()
            assert release.wait(2), "测试必须有界释放入锁门控"
        return original_enter(condition)

    def observe_notify(condition):
        result = original_notify(condition)
        if conditions and condition is conditions[0]:
            notified.set()
        return result

    # 只在 threading 系统边界门控交错；生产 start/stop 与状态循环保持真实。
    monkeypatch.setattr(threading.Condition, "__enter__", gated_enter)
    monkeypatch.setattr(threading.Condition, "notify_all", observe_notify)
    server = ControlPlaneServer(RadioConfig(), bind_host="127.0.0.1", bind_port=0,
                                broadcast_addr="127.0.0.1", broadcast_port=0)
    server.start()
    assert ready.wait(1)

    def stop():
        try:
            server.stop()
            stopped.set()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=stop, daemon=True)
    worker.start()
    try:
        assert notified.wait(1), "必须先由真实 stop 设置停止事实并发出通知"
        release.set()
        assert stopped.wait(1), "stop 通知先于 wait 时仍须 join 到真实线程退出"
    finally:
        release.set()
        # RED 清理只在断言结果确定后唤醒线程，不能用于使行为断言通过。
        if not stopped.is_set() and conditions:
            with conditions[0]:
                conditions[0].notify_all()
        worker.join(2)
    assert not worker.is_alive()
    assert not errors, repr(errors)
