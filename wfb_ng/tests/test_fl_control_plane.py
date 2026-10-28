"""验证双向 UDP 控制面、三级寻频与防失步状态机。"""

import ipaddress
import json
import socket
import threading
import time
import tempfile
import unittest
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

from wfb_ng.fl.control import (
    CANDIDATE_CHANNELS,
    DEFAULT_BENCHMARK_CHANNEL,
    FORBIDDEN_CHANNELS,
    HUNTING_ATTEMPT_TIMEOUT_SECONDS,
    HUNTING_RETRIES_PER_CHANNEL,
    IDLE_HEARTBEAT_INTERVAL_SECONDS,
    IDLE_MISSED_ACK_THRESHOLD,
    ACTIVE_HEARTBEAT_INTERVAL_SECONDS,
    NODE_OFFLINE_THRESHOLD_SECONDS,
    SERVER_CONTROL_BROADCAST_ADDR,
    SERVER_CONTROL_BROADCAST_PORT,
    CLIENT_UPLINK_DEFAULT_ADDR,
    CLIENT_UPLINK_DEFAULT_PORT,
    ClientNodeState,
    NodeReadiness,
    NodeHeartbeat,
    HeartbeatAck,
    NodeRecord,
    PrepareAck,
    CommitSuccess,
    NodeHorizonRegistry,
    ControlPlaneServer,
    ControlPlaneClient,
    build_hunting_ladder,
)
from wfb_ng.fl.radio import RadioConfig, validate_radio_config
from wfb_ng.fl.client_daemon import (
    ClientDaemon,
    ClientDaemonConfig,
    DaemonState,
    NetworkAdapter,
)
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter


_REAL_SOCKET = socket.socket
_PRODUCTION_UDP_PORTS = {9000, 9001, 9002, 9200}


class _LoopbackOnlyDatagramSocket(_REAL_SOCKET):
    """真实 UDP socket；测试发送不得离开 loopback 或命中生产端口。"""

    def check_peer(self, address):
        if self.family == socket.AF_INET and self.type & socket.SOCK_DGRAM:
            host, port = address[:2]
            target = ipaddress.ip_address(host)
            if not target.is_loopback:
                raise AssertionError(f"测试 UDP 禁止发送到非 loopback 地址: {host}")
            if port in _PRODUCTION_UDP_PORTS:
                raise AssertionError(f"测试 UDP 禁止发送到生产端口: {port}")
    def sendto(self, data, address):
        self.check_peer(address)
        return super().sendto(data, address)

    def connect(self, address):
        self.check_peer(address)
        return super().connect(address)


class FaultInjectingControlPlaneServer(ControlPlaneServer):
    """在公开数据报边界模拟控制面丢包。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.drop_downlink_acks = False
        self.packet_filter = None

    def handle_datagram(self, data: bytes, client_addr: Tuple[str, int]) -> Optional[List[bytes]]:
        if self.drop_downlink_acks:
            # 仍更新节点注册表，但抑制下行回执。
            super().handle_datagram(data, client_addr)
            return None
        if self.packet_filter is not None:
            hb = NodeHeartbeat.from_bytes(data)
            if not self.packet_filter(hb):
                return None
        return super().handle_datagram(data, client_addr)


class TestHuntingLadder(unittest.TestCase):
    """验证三级寻频顺序、去重与信道约束。"""

    def test_default_ladder_without_cache(self):
        ladder = build_hunting_ladder(None)
        self.assertEqual(ladder, [157, 149, 153, 165])
        self.assertEqual(len(ladder), 4)

    def test_ladder_with_cached_157(self):
        ladder = build_hunting_ladder(157)
        self.assertEqual(ladder, [157, 149, 153, 165])
        self.assertEqual(len(ladder), 4)

    def test_ladder_with_cached_149(self):
        ladder = build_hunting_ladder(149)
        self.assertEqual(ladder, [149, 157, 153, 165])
        self.assertEqual(len(ladder), 4)

    def test_ladder_with_cached_153(self):
        ladder = build_hunting_ladder(153)
        self.assertEqual(ladder, [153, 157, 149, 165])
        self.assertEqual(len(ladder), 4)

    def test_ladder_with_cached_165(self):
        ladder = build_hunting_ladder(165)
        self.assertEqual(ladder, [165, 157, 149, 153])
        self.assertEqual(len(ladder), 4)

    def test_forbidden_161_channel_strictly_excluded(self):
        # 信道 161 存在已知驱动崩溃缺陷，必须排除。
        ladder = build_hunting_ladder(161)
        self.assertEqual(ladder, [157, 149, 153, 165])
        self.assertNotIn(161, ladder)

    def test_invalid_channel_falls_back_to_benchmark(self):
        ladder = build_hunting_ladder(999)
        self.assertEqual(ladder, [157, 149, 153, 165])


class TestControlMessagesSerialization(unittest.TestCase):
    """验证控制消息的严格身份契约与序列化。"""

    def test_socket_guard_blocks_native_and_control_production_endpoints(self):
        """全局 socket 边界同时阻断控制报文和 native 模块的生产目标。"""
        from wfb_ng.fl.link_radio import apply_link_radio, rearm_link_uplink

        with patch("socket.socket", _LoopbackOnlyDatagramSocket):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                for address in (("127.0.0.1", 9200), ("10.80.0.1", 12345)):
                    with self.subTest(address=address):
                        with self.assertRaises(AssertionError):
                            sock.connect(address)
                        with self.assertRaises(AssertionError):
                            sock.sendto(b"blocked", address)
            with self.assertRaises(AssertionError):
                apply_link_radio(9200, 40, 3)
            with self.assertRaises(AssertionError):
                rearm_link_uplink(9200, 1)

    def test_heartbeat_ack_identity_roundtrip(self):
        """回执序列化必须完整保留请求身份与服务端信道。"""
        ack = HeartbeatAck(heartbeat_timestamp_ms=1000, channel=157)
        self.assertEqual(json.loads(ack.to_bytes()), {
            "ack": True, "heartbeat_timestamp_ms": 1000, "channel": 157,
        })
        self.assertEqual(HeartbeatAck.from_bytes(ack.to_bytes()), ack)

    def test_protocol_fail_closed_validation(self):
        """协议字段非法时必须拒绝消息。"""
        with self.assertRaises(ValueError):
            HeartbeatAck.from_dict({})
        with self.assertRaises(ValueError):
            HeartbeatAck.from_dict({"ack": "not_a_bool"})

        with self.assertRaises(ValueError):
            NodeHeartbeat.from_dict({})
        with self.assertRaises(ValueError):
            NodeHeartbeat.from_dict({
                "node_id": 99,
                "state": "IDLE",
                "elapsed_ms": 0,
                "current_channel": 157,
                "txpower_dbm": 12,
                "uplink_mcs": 6,
            })
        with self.assertRaises(ValueError):
            NodeHeartbeat.from_dict({
                "node_id": 1,
                "state": "INVALID_STATE",
                "elapsed_ms": 0,
                "current_channel": 157,
                "txpower_dbm": 12,
                "uplink_mcs": 6,
            })
        # 拒绝禁用信道 161。
        with self.assertRaises(ValueError):
            NodeHeartbeat.from_dict({
                "node_id": 1,
                "state": "IDLE",
                "elapsed_ms": 0,
                "current_channel": 161,
                "txpower_dbm": 12,
                "uplink_mcs": 6,
            })
        # 拒绝非法发射功率。
        with self.assertRaises(ValueError):
            NodeHeartbeat.from_dict({
                "node_id": 1,
                "state": "IDLE",
                "elapsed_ms": 0,
                "current_channel": 157,
                "txpower_dbm": 99,
                "uplink_mcs": 6,
            })

    def test_heartbeat_ack_requires_strict_complete_identity(self):
        """拒绝旧协议、缺失字段、假值、布尔整数及非正数。"""
        valid = {"ack": True, "heartbeat_timestamp_ms": 1000, "channel": 157}
        for field in valid:
            incomplete = dict(valid)
            del incomplete[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                HeartbeatAck.from_dict(incomplete)
        for field, values in (
            ("ack", (False, 1, "true", None)),
            ("heartbeat_timestamp_ms", (True, False, 0, -1, 1.5, "1000", None)),
            ("channel", (True, False, 0, -1, 157.0, "157", None)),
        ):
            for value in values:
                invalid = dict(valid, **{field: value})
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    HeartbeatAck.from_dict(invalid)
                with self.subTest(constructor=field, value=value), self.assertRaises(ValueError):
                    HeartbeatAck(**invalid)
        with self.assertRaises(ValueError):
            HeartbeatAck.from_bytes(b'{"ack": true}')
        for payload in (None, [], True, 1, "ack"):
            with self.subTest(payload=payload), self.assertRaises(TypeError):
                HeartbeatAck.from_bytes(json.dumps(payload).encode())

    def test_node_heartbeat_serialization_roundtrip(self):
        hb = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=150,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            error_code=None,
            timestamp_ms=1000,
        )
        raw_bytes = hb.to_bytes()
        parsed = NodeHeartbeat.from_bytes(raw_bytes)
        self.assertEqual(parsed.node_id, 1)
        self.assertEqual(parsed.state, "HUNTING")
        self.assertEqual(parsed.elapsed_ms, 150)
        self.assertEqual(parsed.current_channel, 157)
        self.assertEqual(parsed.txpower_dbm, 12)
        self.assertEqual(parsed.uplink_mcs, 6)


class TestNodeHorizonRegistryAndAntiDesyncGate(unittest.TestCase):
    """验证节点注册、防失步就绪门禁与射频对齐。"""

    def setUp(self):
        self.registry = NodeHorizonRegistry()
        self.server_radio = RadioConfig(
            channel=157,
            radio_txpower_dbm=12,
            downlink_mcs=3,
            uplink_mcs=6,
        )

    def test_hunting_state_keeps_node_in_connecting(self):
        hb = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=100,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=211,
        )
        ack, patch = self.registry.update_heartbeat(
            hb, client_addr=("10.80.0.11", 50000), server_radio=self.server_radio
        )
        self.assertEqual(ack.to_dict(), {
            "ack": True, "heartbeat_timestamp_ms": 211, "channel": 157,
        })
        self.assertIsNone(patch)

        node = self.registry.get_node(1)
        self.assertIsNotNone(node)
        self.assertEqual(node.reported_state, "HUNTING")
        # 防失步门禁：未经确认必须保持 CONNECTING。
        self.assertEqual(node.readiness, NodeReadiness.CONNECTING)
        self.assertFalse(self.registry.is_node_ready(1))

    def test_idle_state_transitions_node_to_ready(self):
        # 客户端先发送寻频心跳。
        hb_hunting = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=100,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=234,
        )
        self.registry.update_heartbeat(
            hb_hunting, client_addr=("10.80.0.11", 50000), server_radio=self.server_radio
        )
        self.assertEqual(self.registry.get_node(1).readiness, NodeReadiness.CONNECTING)

        # 客户端收到回执转为 IDLE 后立即上报待命状态。
        hb_idle = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=248,
        )
        self.registry.update_heartbeat(
            hb_idle, client_addr=("10.80.0.11", 50000), server_radio=self.server_radio
        )
        node = self.registry.get_node(1)
        self.assertEqual(node.reported_state, "IDLE")
        # 防失步门禁：只有此时才授予就绪状态。
        self.assertEqual(node.readiness, NodeReadiness.READY)
        self.assertTrue(self.registry.is_node_ready(1))

    def test_unsolicited_idle_held_in_connecting(self):
        """未知节点未经过寻频确认时，上报 IDLE 仍须保持 CONNECTING。"""
        hb_unsolicited_idle = NodeHeartbeat(
            node_id=3,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=271,
        )
        self.registry.update_heartbeat(
            hb_unsolicited_idle,
            client_addr=("10.80.0.13", 50000),
            server_radio=self.server_radio,
        )
        node = self.registry.get_node(3)
        self.assertIsNotNone(node)
        self.assertEqual(node.readiness, NodeReadiness.CONNECTING)
        self.assertFalse(self.registry.is_node_ready(3))

    def test_radio_mismatch_triggers_alignment_directive(self):
        # 客户端上报功率 20 dBm、上行调制阶数 3，与服务端不匹配。
        hb_mismatch = NodeHeartbeat(
            node_id=2,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=50,
            current_channel=157,
            txpower_dbm=20,
            uplink_mcs=3,
            timestamp_ms=291,
        )
        ack, patch = self.registry.update_heartbeat(
            hb_mismatch, client_addr=("10.80.0.12", 50000), server_radio=self.server_radio
        )
        self.assertEqual(ack.to_dict(), {
            "ack": True, "heartbeat_timestamp_ms": 291, "channel": 157,
        })
        self.assertIsNotNone(patch)
        # 服务端自动计算射频对齐补丁。
        self.assertEqual(patch.get("radio_txpower_dbm"), 12)
        self.assertEqual(patch.get("uplink_mcs"), 6)
        # 按规则 89，对齐补丁不包含信道。
        self.assertNotIn("channel", patch)

    def test_node_offline_timeout(self):
        # 通过合法的寻频到待命确认建立节点身份。
        hb_hunt = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=312,
        )
        self.registry.update_heartbeat(
            hb_hunt, client_addr=("10.80.0.11", 50000), server_radio=self.server_radio
        )
        hb_idle = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=323,
        )
        self.registry.update_heartbeat(
            hb_idle, client_addr=("10.80.0.11", 50000), server_radio=self.server_radio
        )
        self.assertTrue(self.registry.is_node_ready(1))

        # 将心跳时间推至十秒离线阈值之前。
        node = self.registry.get_node(1)
        node.last_heartbeat_time -= 11.0

        self.assertFalse(self.registry.is_node_ready(1))
        self.assertEqual(self.registry.get_node(1).readiness, NodeReadiness.OFFLINE)

    def test_reordered_and_replayed_heartbeats_idempotency_and_monotonicity(self):
        """忽略乱序和重复心跳，旧 IDLE 不能错误授予 READY 状态。"""
        # 节点在时间戳 1000 发送寻频心跳。
        hb_hunt = NodeHeartbeat(
            node_id=4,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=1000,
        )
        self.registry.update_heartbeat(
            hb_hunt, client_addr=("10.80.0.14", 50000), server_radio=self.server_radio
        )
        self.assertEqual(self.registry.get_node(4).readiness, NodeReadiness.CONNECTING)

        # 重放旧会话时间戳 500 的待命心跳，早于本次寻频。
        hb_stale_idle = NodeHeartbeat(
            node_id=4,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=500,
        )
        ack, _ = self.registry.update_heartbeat(
            hb_stale_idle, client_addr=("10.80.0.14", 50000), server_radio=self.server_radio
        )
        self.assertEqual(ack.to_dict(), {
            "ack": True, "heartbeat_timestamp_ms": 500, "channel": 157,
        })
        # 此时仍须保持连接中，不能就绪。
        self.assertEqual(self.registry.get_node(4).readiness, NodeReadiness.CONNECTING)
        self.assertFalse(self.registry.is_node_ready(4))

        # 时间戳 1050 的待命心跳晚于本次寻频，可完成确认。
        hb_valid_idle = NodeHeartbeat(
            node_id=4,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=50,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=1050,
        )
        self.registry.update_heartbeat(
            hb_valid_idle, client_addr=("10.80.0.14", 50000), server_radio=self.server_radio
        )
        # 现在才允许进入就绪状态。
        self.assertEqual(self.registry.get_node(4).readiness, NodeReadiness.READY)
        self.assertTrue(self.registry.is_node_ready(4))

        # 重放时间戳 900 的旧寻频心跳，不能将就绪状态降为连接中。
        hb_old_hunt = NodeHeartbeat(
            node_id=4,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=900,
        )
        self.registry.update_heartbeat(
            hb_old_hunt, client_addr=("10.80.0.14", 50000), server_radio=self.server_radio
        )
        self.assertEqual(self.registry.get_node(4).readiness, NodeReadiness.READY)


class TestControlPlaneServerReceipts(unittest.TestCase):
    """通过公开数据报接口验证服务端回执，无需启动后台监听线程。"""

    def setUp(self):
        self.server = ControlPlaneServer(
            active_radio_config=RadioConfig(channel=153),
            bind_host="127.0.0.1", bind_port=0,
            broadcast_addr="127.0.0.1", broadcast_port=0,
        )

    def test_server_ack_reports_effective_channel_not_heartbeat_channel(self):
        """服务端回显本次时间戳，信道取服务端生效配置而非客户端报告。"""
        heartbeat = NodeHeartbeat(
            node_id=4, state="HUNTING", elapsed_ms=0, current_channel=149,
            txpower_dbm=12, uplink_mcs=6, timestamp_ms=1000,
        )
        replies = self.server.handle_datagram(heartbeat.to_bytes(), ("127.0.0.1", 1))
        self.assertIsNotNone(replies)
        self.assertEqual(json.loads(replies[0]), {
            "ack": True, "heartbeat_timestamp_ms": 1000, "channel": 153,
        })

    def test_radio_reports_do_not_generate_heartbeat_receipts(self):
        """射频准备和提交报告不应生成没有心跳身份的通用回执。"""
        for report in (
            PrepareAck(node_id=4, session_id="session"),
            CommitSuccess(node_id=4, session_id="session", current_channel=153),
        ):
            with self.subTest(report=report):
                self.assertIsNone(self.server.handle_datagram(
                    report.to_bytes(), ("127.0.0.1", 1),
                ))


class TestControlPlaneNetworkLoop(unittest.TestCase):
    """通过真实 UDP 验证控制面寻频与防失步行为。"""

    def setUp(self):
        self.socket_guard = patch("socket.socket", _LoopbackOnlyDatagramSocket)
        self.socket_guard.start()
        self.addCleanup(self.socket_guard.stop)
        # 使用 loopback 和动态端口，socket guard 防止测试流量逃逸。
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s1:
            s1.bind(("127.0.0.1", 0))
            self.server_port = s1.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s2:
            s2.bind(("127.0.0.1", 0))
            self.client_broadcast_port = s2.getsockname()[1]

        self.server_radio = RadioConfig(
            channel=153,  # 服务端位于候选信道 153。
            radio_txpower_dbm=12,
            downlink_mcs=3,
            uplink_mcs=6,
        )
        self.server = FaultInjectingControlPlaneServer(
            active_radio_config=self.server_radio,
            bind_host="127.0.0.1",
            bind_port=self.server_port,
            broadcast_addr="127.255.255.255",
            broadcast_port=self.client_broadcast_port,
        )
        self.server.start()

    def tearDown(self):
        self.server.stop()

    def test_task_ready_barrier_is_job_scoped_and_requires_all_targets(self):
        self.server.prepare_task_readiness("job-ready", [1, 2])

        wrong_job = json.dumps({
            "type": "TASK_READY", "report_id": "wrong-job-1",
            "job_id": "other", "node_id": 1,
            "report": {"format": "reference"},
        }).encode()
        self.assertIsNotNone(self.server.handle_datagram(wrong_job, ("127.0.0.1", 1)))
        self.assertFalse(self.server.wait_for_task_readiness(0))

        node1 = json.dumps({
            "type": "TASK_READY", "report_id": "ready-1",
            "job_id": "job-ready", "node_id": 1,
            "report": {"format": "reference"},
        }).encode()
        self.assertIsNotNone(self.server.handle_datagram(node1, ("127.0.0.1", 1)))
        self.assertFalse(self.server.wait_for_task_readiness(0))

        node2 = json.dumps({
            "type": "TASK_READY", "report_id": "ready-2",
            "job_id": "job-ready", "node_id": 2,
            "report": {"format": "reference"},
        }).encode()
        self.assertIsNotNone(self.server.handle_datagram(node2, ("127.0.0.1", 2)))
        self.assertTrue(self.server.wait_for_task_readiness(0))

    def test_unidirectional_drop_two_army_simulation(self):
        """模拟上行正常、下行丢包；仅在回执恢复并确认 IDLE 后授予 READY。"""
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            attempt_timeout_seconds=0.1,
            max_attempts_per_channel=2,
        )

        # 在服务端启用下行丢包。
        self.server.drop_downlink_acks = True

        # 在 153 执行一次寻频。
        found = client.hunt_once(ladder=[153])
        self.assertFalse(found)
        self.assertEqual(client.state, ClientNodeState.HUNTING)

        # 服务端收到寻频心跳，但回执被丢弃。
        node = self.server.registry.get_node(1)
        self.assertIsNotNone(node)
        self.assertEqual(node.reported_state, "HUNTING")
        self.assertEqual(node.readiness, NodeReadiness.CONNECTING)
        self.assertFalse(self.server.registry.is_node_ready(1))

        # 恢复下行回执。
        self.server.drop_downlink_acks = False

        # 再次执行寻频。
        found = client.hunt_once(ladder=[153])
        self.assertTrue(found)
        self.assertEqual(client.state, ClientNodeState.IDLE)
        self.assertEqual(client.locked_channel, 153)

        # 等待服务端处理立即发出的待命心跳。
        time.sleep(0.05)

        node = self.server.registry.get_node(1)
        self.assertEqual(node.reported_state, "IDLE")
        self.assertEqual(node.readiness, NodeReadiness.READY)
        self.assertTrue(self.server.registry.is_node_ready(1))

        client.stop()

    def test_three_tier_hunting_sweep_progression_and_timing(self):
        """服务端位于 153，客户端依次探测 157、149、153，发现后不再探测 165。"""
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=2,
            tun_ip="10.80.0.12",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=157,
            cached_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            attempt_timeout_seconds=0.08,
            max_attempts_per_channel=2,
        )

        # 信道不是 153 时丢弃回执，以模拟射频信道不匹配。
        def channel_filter(hb: NodeHeartbeat) -> bool:
            return hb.current_channel == 153

        self.server.packet_filter = channel_filter

        t0 = time.monotonic()
        found = client.hunt_once()
        elapsed = time.monotonic() - t0

        self.assertTrue(found)
        self.assertEqual(client.locked_channel, 153)
        self.assertEqual(client.state, ClientNodeState.IDLE)

        # 验证信道按指定顺序推进。
        self.assertEqual(adapter.channel_history, [157, 149, 153])
        # 总耗时包含前两个信道的超时与第三个信道的快速成功。
        self.assertLess(elapsed, 1.0)

        client.stop()

    def test_compatible_channel_pool_sweep_under_two_seconds(self):
        """未找到服务器时，两秒内扫完当前HT40允许的信道，跳过不兼容的165。"""
        from wfb_ng.fl.control import HUNTING_ATTEMPT_TIMEOUT_SECONDS
        self.assertEqual(HUNTING_ATTEMPT_TIMEOUT_SECONDS, 0.23)

        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=3,
            tun_ip="10.80.0.13",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=157,
            cached_channel=None,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
        )

        # 丢弃全部回执，模拟服务端不可达。
        self.server.drop_downlink_acks = True

        t0 = time.monotonic()
        found = client.hunt_once()
        elapsed = time.monotonic() - t0

        self.assertFalse(found)
        self.assertEqual(adapter.channel_history, [157, 149, 153])
        # 当前带宽不能在165工作，只检查实际可用集合的有界寻频。
        self.assertLess(elapsed, 2.0)
        self.assertGreaterEqual(elapsed, 3 * HUNTING_ATTEMPT_TIMEOUT_SECONDS)

        client.stop()

    def test_radio_alignment_applied_by_client(self):
        """客户端应用服务端的功率与调制阶数对齐指令，并在后续心跳确认。"""
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            txpower_dbm=20,  # 与服务端不匹配。
            uplink_mcs=3,   # 与服务端不匹配。
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            attempt_timeout_seconds=0.1,
            max_attempts_per_channel=2,
        )

        found = client.hunt_once(ladder=[153])
        self.assertTrue(found)

        # 验证客户端已应用射频对齐。
        self.assertEqual(client.txpower_dbm, 12)
        self.assertEqual(client.uplink_mcs, 6)
        self.assertEqual(adapter.wireless_states["wlan0"]["txpower_dbm"], 12)

        # 等待立即发出的待命心跳到达服务端。
        time.sleep(0.05)
        node = self.server.registry.get_node(1)
        self.assertEqual(node.txpower_dbm, 12)
        self.assertEqual(node.uplink_mcs, 6)
        self.assertEqual(node.readiness, NodeReadiness.READY)

        client.stop()

    def test_instant_heartbeat_on_state_transition(self):
        """客户端状态变化时立即发送心跳。"""
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            attempt_timeout_seconds=0.1,
            idle_interval_seconds=5.0,
            active_interval_seconds=2.0,
        )

        client.hunt_once(ladder=[153])
        time.sleep(0.05)
        self.assertEqual(self.server.registry.get_node(1).reported_state, "IDLE")

        # 立即切换状态。
        t0 = time.monotonic()
        client.notify_state_change(ClientNodeState.RUNNING)
        time.sleep(0.05)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 0.2)
        node = self.server.registry.get_node(1)
        self.assertEqual(node.reported_state, "RUNNING")
        self.assertEqual(node.readiness, NodeReadiness.ACTIVE)

        client.stop()

    def test_missed_acks_in_idle_triggers_fallback_to_hunting(self):
        """空闲时连续两次缺失回执，客户端应返回寻频状态。"""
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            attempt_timeout_seconds=0.05,
            idle_interval_seconds=0.05,  # 使用较短测试周期。
        )

        client.hunt_once(ladder=[153])
        self.assertEqual(client.state, ClientNodeState.IDLE)

        # 丢弃回执。
        self.server.drop_downlink_acks = True

        # 第一次心跳缺失回执。
        client.send_heartbeat_once(timeout=0.05)
        self.assertEqual(client.state, ClientNodeState.IDLE)
        self.assertEqual(client.missed_acks, 1)

        # 第二次心跳缺失回执，达到失联阈值。
        client.send_heartbeat_once(timeout=0.05)
        self.assertEqual(client.state, ClientNodeState.HUNTING)

        client.stop()

    def test_server_broadcast_downlink_received_by_client(self):
        """客户端后台监听器通过动态回环端口接收服务端广播并调用处理器。"""
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
        )
        client.start_broadcast_listener()

        received_events = []
        client.register_broadcast_handler(
            "TASK_ANNOUNCE", lambda msg: received_events.append(msg)
        )

        self.server.broadcast_downlink({
            "type": "TASK_ANNOUNCE",
            "task_id": "job-123",
            "rounds": 2,
        })

        time.sleep(0.1)
        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0]["task_id"], "job-123")

        client.stop()

    def test_multi_client_broadcast_reception(self):
        """多个客户端通过端口复用同时接收服务端回环广播。"""
        adapter1 = MockNetworkAdapter(["wlan0"])
        client1 = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter1,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
        )
        adapter2 = MockNetworkAdapter(["wlan1"])
        client2 = ControlPlaneClient(
            node_id=2,
            tun_ip="10.80.0.12",
            network_adapter=adapter2,
            air_interface="wlan1",
            initial_channel=153,
            cached_channel=153,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
        )

        client1.start_broadcast_listener()
        client2.start_broadcast_listener()

        c1_events = []
        c2_events = []
        client1.register_broadcast_handler("TASK_ANNOUNCE", lambda m: c1_events.append(m))
        client2.register_broadcast_handler("TASK_ANNOUNCE", lambda m: c2_events.append(m))

        self.server.broadcast_downlink({
            "type": "TASK_ANNOUNCE",
            "task_id": "broadcast_multi_client",
        })

        time.sleep(0.15)
        self.assertEqual(len(c1_events), 1)
        self.assertEqual(len(c2_events), 1)
        self.assertEqual(c1_events[0]["task_id"], "broadcast_multi_client")
        self.assertEqual(c2_events[0]["task_id"], "broadcast_multi_client")

        client1.stop()
        client2.stop()

    def test_client_daemon_control_plane_integration(self):
        """客户端守护进程启动控制面并寻频，确认待命后服务端授予 READY。"""
        adapter = MockNetworkAdapter(["wlan0"])
        config = ClientDaemonConfig(
            node_id=1,
            tun_ip="10.80.0.11",
            channel=153,
            server_control_host="127.0.0.1",
            server_control_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            enable_control_plane=True,
            enable_link_process=False,
            poll_interval_seconds=0.05,
        )
        daemon = ClientDaemon(config, network_adapter=adapter)

        # 在后台线程启动守护监管循环。
        daemon_thread = threading.Thread(target=daemon.run, daemon=True)
        daemon_thread.start()

        # 等待控制面完成寻频并锁定信道。
        max_wait = 2.0
        start_t = time.monotonic()
        while time.monotonic() - start_t < max_wait:
            if daemon.state == DaemonState.IDLE and self.server.registry.is_node_ready(1):
                break
            time.sleep(0.05)

        self.assertEqual(daemon.state, DaemonState.IDLE)
        self.assertTrue(self.server.registry.is_node_ready(1))

        node = self.server.registry.get_node(1)
        self.assertEqual(node.reported_state, "IDLE")
        self.assertEqual(node.readiness, NodeReadiness.READY)

        daemon.stop()
        daemon_thread.join(timeout=1.0)

    def test_client_daemon_task_announce_and_abort_handling(self):
        """客户端守护进程接收任务宣告、启动任务，并在中止后恢复待命。"""
        import sys
        import subprocess
        from unittest.mock import patch
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        adapter = MockNetworkAdapter(["wlan0"])
        config = ClientDaemonConfig(
            work_dir=work.name,
            node_id=1,
            tun_ip="10.80.0.11",
            channel=153,
            server_control_host="127.0.0.1",
            server_control_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            enable_control_plane=True,
            enable_link_process=False,
            poll_interval_seconds=0.05,
        )
        daemon = ClientDaemon(config, network_adapter=adapter)
        daemon.sandbox.evidence_dir = work.name + '/evidence'
        stub_script = (
            "import os, threading\n"
            "from wfb_ng.fl.service import AlgorithmNotifications\n"
            "AlgorithmNotifications(os.environ['WFB_FL_NOTIFY_SOCKET'], 'job_fl_001', "
            "threading.Event(), timeout=3).prepared({'sample_count': 60000})\n"
            "threading.Event().wait(10)\n"
        )
        real_popen = subprocess.Popen
        process_patch = patch('wfb_ng.fl.client_daemon.subprocess.Popen',
                              side_effect=lambda cmd, **kwargs: real_popen(
                                  [sys.executable, '-c', stub_script], **kwargs))
        process_patch.start()
        self.addCleanup(process_patch.stop)
        self.addCleanup(daemon.stop)

        daemon_thread = threading.Thread(target=daemon.run, daemon=True)
        daemon_thread.start()

        # 等待守护进程进入待命状态。
        start_t = time.monotonic()
        while time.monotonic() - start_t < 2.0:
            if daemon.state == DaemonState.IDLE and self.server.registry.is_node_ready(1):
                break
            time.sleep(0.05)

        self.assertEqual(daemon.state, DaemonState.IDLE)

        # 使用真实 prepared 报告及相关确认，心跳回执不能代替准备确认。
        self.server.prepare_task_readiness("job_fl_001", [1])
        # 向节点 1 广播任务宣告。
        self.server.broadcast_downlink({
            "type": "TASK_ANNOUNCE",
            "run_id": "run_fl_001",
            "job_id": "job_fl_001",
            "target_nodes": [1],
            "rounds": 1,
            "preparation_timeout_seconds": 3,
            "algorithm": "wfb_ng.fl.mnist:client_entry",
            "algorithm_config": {"data_seed": 2026},
            "server_http_host": "10.80.0.1",
            "server_http_port": 8080,
            "uftp_port": 1044,
            "link_id": 7669206,
            "io_timeout_seconds": 120,
            "live_observation": True,
        })

        self.assertTrue(self.server.wait_for_task_readiness(2))
        self.assertEqual(self.server.get_task_preparation_reports("job_fl_001")[1]["report"],
                         {"sample_count": 60000})

        # 等待任务开始。
        start_t = time.monotonic()
        while time.monotonic() - start_t < 2.0:
            if daemon.state == DaemonState.RUNNING:
                break
            time.sleep(0.05)

        self.assertEqual(daemon.state, DaemonState.RUNNING)

        # 终态广播前必须先开启恢复屏障；客户端恢复 ACK 不能进入未准备的 barrier。
        self.server.prepare_job_recovery("job_fl_001", [1])
        self.server.broadcast_downlink({"type": "JOB_ABORT", "job_id": "job_fl_001"})

        # 等待任务中止并恢复待命。
        start_t = time.monotonic()
        while time.monotonic() - start_t < 2.0:
            if daemon.state == DaemonState.IDLE:
                break
            time.sleep(0.05)

        self.assertEqual(daemon.state, DaemonState.IDLE)
        self.assertTrue(self.server.wait_for_job_recovery("job_fl_001", 2))

        daemon.stop()
        daemon_thread.join(timeout=1.0)

    def test_hardware_reconfiguration_failure_fails_closed(self):
        """硬件重配失败时传播错误，不能错误记录为配置已更新。"""
        class BrokenAdapter(MockNetworkAdapter):
            def set_channel(self, iface, channel, channel_width="HT40+"):
                raise OSError("Physical device busy or removed")

            def set_txpower(self, iface, txpower_dbm):
                raise OSError("Device driver error")

        adapter = BrokenAdapter(["wlan0"])
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=157,
            txpower_dbm=12,
        )

        with self.assertRaises(OSError):
            client._apply_channel_switch(149)
        self.assertEqual(client.current_channel, 157)  # 失败后保持原值。

        with self.assertRaises(OSError):
            client._apply_txpower_switch(20)
        self.assertEqual(client.txpower_dbm, 12)  # 失败后保持原值。

    def test_client_daemon_persists_locked_channel_across_hotplug(self):
        """客户端守护进程在网卡热插拔后保留已发现的信道。"""
        adapter = MockNetworkAdapter(["wlan0"])
        config = ClientDaemonConfig(
            node_id=1,
            tun_ip="10.80.0.11",
            channel=157,  # 初始信道为 157。
            server_control_host="127.0.0.1",
            server_control_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            enable_control_plane=True,
            enable_link_process=False,
            poll_interval_seconds=0.05,
        )
        # 模拟射频条件：仅在客户端切至 153 时接收心跳。
        self.server.packet_filter = lambda hb: hb.current_channel == 153

        daemon = ClientDaemon(config, network_adapter=adapter)
        self.addCleanup(daemon.stop)
        daemon.poll_hardware_once()
        daemon.start_control_plane()

        # 等待控制面后台寻频并锁定 153。
        start_t = time.monotonic()
        while time.monotonic() - start_t < 2.0:
            if daemon.control_plane.locked_channel == 153:
                break
            time.sleep(0.05)
        self.assertEqual(daemon.control_plane.locked_channel, 153)

        # 拔出网卡。
        daemon._on_interface_lost()
        self.assertIsNone(daemon.current_interface)
        self.assertIsNone(daemon.control_plane)
        # 验证守护进程保留已锁定的信道。
        self.assertEqual(daemon._last_locked_channel, 153)

        # 重新插入网卡。
        adapter.set_interfaces(["wlan0"])
        daemon.poll_hardware_once()
        daemon.start_control_plane()
        # 验证重建的控制面优先使用缓存信道 153。
        self.assertEqual(daemon.control_plane.cached_channel, 153)

        daemon.stop()


if __name__ == "__main__":
    unittest.main()
