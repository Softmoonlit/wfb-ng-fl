"""
Symmetric UDP control plane, three-tier hunting ladder, anti-desync state machine,
and NodeHorizon registry for WFB-ng Stage 2 (ADR-0012, ADR-0014, Ticket 03).
"""

from contextlib import contextmanager
from copy import deepcopy
import json
import logging
import math
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional, Sequence, Set, Tuple

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.radio import (
    ALLOWED_5GHZ_CHANNELS,
    DEFAULT_CHANNEL,
    DEFAULT_TXPOWER_DBM,
    FORBIDDEN_CHANNELS,
    RECOMMENDED_UPLINK_MCS,
    RadioConfig,
    normalize_radio_patch,
    validate_radio_calibration_patch,
    validate_radio_patch,
)

if TYPE_CHECKING:
    from wfb_ng.fl.client_daemon import NetworkAdapter

logger = logging.getLogger(__name__)

# Protocol Network Endpoints
SERVER_CONTROL_BROADCAST_ADDR: str = "255.255.255.255"
SERVER_CONTROL_BROADCAST_PORT: int = 9000
CLIENT_UPLINK_DEFAULT_ADDR: str = "10.80.0.1"
CLIENT_UPLINK_DEFAULT_PORT: int = 9001

# Channel Ladder Parameters
DEFAULT_BENCHMARK_CHANNEL: int = 157
CANDIDATE_CHANNELS: Tuple[int, ...] = (149, 153, 165)

# Timing Parameters
HUNTING_ATTEMPT_TIMEOUT_SECONDS: float = 0.23
HUNTING_RETRIES_PER_CHANNEL: int = 2
IDLE_HEARTBEAT_INTERVAL_SECONDS: float = 5.0
IDLE_MISSED_ACK_THRESHOLD: int = 2
ACTIVE_HEARTBEAT_INTERVAL_SECONDS: float = 2.0
NODE_OFFLINE_THRESHOLD_SECONDS: float = 10.0

# Two-Phase Radio Switch Parameters (ADR-0012, ADR-0014, Ticket 04)
PREPARE_TIMEOUT_DEFAULT_SECONDS: float = 5.0
COMMIT_DELAY_DEFAULT_SECONDS: float = 2.0
COMMIT_TIMEOUT_DEFAULT_SECONDS: float = 10.0
LEASE_TIMEOUT_DEFAULT_SECONDS: float = 15.0
NEW_CHANNEL_PING_INTERVAL_SECONDS: float = 1.0

# 任务报告传输限制。报告使用调用方总截止时间，不采用固定尝试次数，
# 由重试间隔限制 UDP 重试频率。
MAX_CONTROL_DATAGRAM_BYTES: int = 4096
REPORT_RETRY_INTERVAL_SECONDS: float = 0.25


class ClientNodeState(str, Enum):
    """Client-side execution and hunting states."""
    HUNTING = "HUNTING"
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    ABORTING = "ABORTING"


class NodeReadiness(str, Enum):
    """Server-side perceived readiness and status for connected nodes."""
    OFFLINE = "OFFLINE"        # 🔴 离线 (>10s silent)
    CONNECTING = "CONNECTING"  # 🟡 连接中 (HUNTING sent, awaiting IDLE closed-loop confirmation)
    READY = "READY"            # 🟢 就绪 (confirmed IDLE)
    ACTIVE = "ACTIVE"          # 🔵 运行中 (PREPARING, RUNNING)
    ABORTING = "ABORTING"      # 中止中


def build_hunting_ladder(cached_channel: Optional[int] = None) -> List[int]:
    """
    Construct the three-tier hunting ladder:
    1. Local cached channel (if valid and allowed)
    2. Benchmark channel 157
    3. Candidate pool [149, 153, 165]
    Preserves order and deduplicates. Channel 161 is strictly excluded.
    Always returns exactly 4 allowed channels.
    """
    ladder: List[int] = []
    if (
        cached_channel is not None
        and cached_channel in ALLOWED_5GHZ_CHANNELS
        and cached_channel not in FORBIDDEN_CHANNELS
    ):
        ladder.append(cached_channel)

    if DEFAULT_BENCHMARK_CHANNEL not in ladder:
        ladder.append(DEFAULT_BENCHMARK_CHANNEL)

    for ch in CANDIDATE_CHANNELS:
        if ch not in ladder and ch not in FORBIDDEN_CHANNELS:
            ladder.append(ch)

    return ladder


@dataclass(frozen=True)
class NodeHeartbeat:
    """Uplink heartbeat datagram payload sent by clients to 10.80.0.1:9001."""
    node_id: int
    state: str
    elapsed_ms: int
    current_channel: int
    txpower_dbm: int
    uplink_mcs: int
    error_code: Optional[str] = None
    timestamp_ms: Optional[int] = None
    bandwidth_mhz: int = 40
    downlink_mcs: int = 3
    uftp_rate_kbps: int = 15000
    calibration: bool = False

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "type": "NODE_HEARTBEAT",
            "node_id": self.node_id,
            "state": self.state,
            "elapsed_ms": self.elapsed_ms,
            "current_channel": self.current_channel,
            "txpower_dbm": self.txpower_dbm,
            "uplink_mcs": self.uplink_mcs,
            "bandwidth_mhz": self.bandwidth_mhz,
            "downlink_mcs": self.downlink_mcs,
            "uftp_rate_kbps": self.uftp_rate_kbps,
            "calibration": self.calibration,
        }
        if self.error_code is not None:
            res["error_code"] = self.error_code
        if self.timestamp_ms is not None:
            res["timestamp_ms"] = self.timestamp_ms
        return res

    def to_bytes(self) -> bytes:
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodeHeartbeat":
        if not isinstance(data, dict):
            raise TypeError(f"Payload must be a dict, got {type(data).__name__}")

        if "node_id" not in data or type(data["node_id"]) is not int or not (1 <= data["node_id"] <= 10):
            raise ValueError(f"Invalid or missing node_id: {data.get('node_id')}")

        if "state" not in data or not isinstance(data["state"], str):
            raise ValueError(f"Invalid or missing state: {data.get('state')}")
        try:
            state = ClientNodeState(data["state"]).value
        except ValueError:
            raise ValueError(f"Unknown state: {data['state']}")

        if "elapsed_ms" not in data or type(data["elapsed_ms"]) is not int or data["elapsed_ms"] < 0:
            raise ValueError(f"Invalid or missing elapsed_ms: {data.get('elapsed_ms')}")

        if "current_channel" not in data or type(data["current_channel"]) is not int:
            raise ValueError(f"Invalid or missing current_channel: {data.get('current_channel')}")

        if "txpower_dbm" not in data or type(data["txpower_dbm"]) is not int:
            raise ValueError(f"Invalid or missing txpower_dbm: {data.get('txpower_dbm')}")

        if "uplink_mcs" not in data or type(data["uplink_mcs"]) is not int:
            raise ValueError(f"Invalid or missing uplink_mcs: {data.get('uplink_mcs')}")

        if type(data.get("timestamp_ms")) is not int or data["timestamp_ms"] <= 0:
            raise ValueError(f"Invalid or missing timestamp_ms: {data.get('timestamp_ms')}")

        # Strict validation of RF parameters using existing canonical validator
        if type(data.get("calibration")) is not bool:
            raise ValueError("calibration must be a boolean")
        validator = validate_radio_calibration_patch if data["calibration"] else validate_radio_patch
        validator({
            "channel": data["current_channel"],
            "radio_txpower_dbm": data["txpower_dbm"],
            "uplink_mcs": data["uplink_mcs"],
            "bandwidth_mhz": data["bandwidth_mhz"],
            "downlink_mcs": data["downlink_mcs"],
            "uftp_rate_kbps": data["uftp_rate_kbps"],
        }, base=RadioConfig())

        return cls(
            node_id=data["node_id"],
            state=state,
            elapsed_ms=data["elapsed_ms"],
            current_channel=data["current_channel"],
            txpower_dbm=data["txpower_dbm"],
            uplink_mcs=data["uplink_mcs"],
            bandwidth_mhz=data["bandwidth_mhz"],
            downlink_mcs=data["downlink_mcs"],
            uftp_rate_kbps=data["uftp_rate_kbps"],
            calibration=data["calibration"],
            error_code=data.get("error_code"),
            timestamp_ms=data["timestamp_ms"],
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "NodeHeartbeat":
        return cls.from_dict(json.loads(raw.decode("utf-8")))


@dataclass(frozen=True)
class HeartbeatAck:
    """
    Downlink ACK datagram payload sent by server to client.
    Strictly minimal acknowledgement per ADR-0012: exact b'{"ack": true}' (13 bytes, < 20 bytes).
    """
    ack: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"ack": self.ack}

    def to_bytes(self) -> bytes:
        return b'{"ack": true}'

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HeartbeatAck":
        if not isinstance(data, dict):
            raise TypeError(f"Payload must be a dict, got {type(data).__name__}")
        if "ack" not in data or type(data["ack"]) is not bool:
            raise ValueError("Missing or invalid boolean 'ack' field")
        return cls(ack=data["ack"])

    @classmethod
    def from_bytes(cls, raw: bytes) -> "HeartbeatAck":
        return cls.from_dict(json.loads(raw.decode("utf-8")))


@dataclass(frozen=True)
class PrepareAck:
    """Uplink acknowledgement for CONFIG_RADIO_PREPARE sent by clients."""
    node_id: int
    session_id: str
    ok: bool = True
    error: Optional[str] = None
    timestamp_ms: Optional[int] = None
    job_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "type": "PREPARE_ACK",
            "node_id": self.node_id,
            "session_id": self.session_id,
            "ok": self.ok,
        }
        if self.error is not None:
            res["error"] = self.error
        if self.timestamp_ms is not None:
            res["timestamp_ms"] = self.timestamp_ms
        if self.job_id is not None:
            res["job_id"] = self.job_id
        return res

    def to_bytes(self) -> bytes:
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PrepareAck":
        if not isinstance(data, dict):
            raise TypeError(f"Payload must be a dict, got {type(data).__name__}")
        if "node_id" not in data or type(data["node_id"]) is not int or not (1 <= data["node_id"] <= 10):
            raise ValueError(f"Invalid or missing node_id: {data.get('node_id')}")
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Invalid or missing session_id in PREPARE_ACK")
        if type(data.get("ok")) is not bool:
            raise ValueError("Invalid or missing boolean ok in PREPARE_ACK")
        job_id = data.get("job_id")
        if job_id is not None and (not isinstance(job_id, str) or not job_id):
            raise ValueError("Invalid job_id in PREPARE_ACK")
        return cls(
            node_id=data["node_id"],
            session_id=session_id,
            ok=data["ok"],
            error=data.get("error"),
            timestamp_ms=data.get("timestamp_ms"),
            job_id=job_id,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "PrepareAck":
        return cls.from_dict(json.loads(raw.decode("utf-8")))


@dataclass(frozen=True)
class CommitSuccess:
    """Uplink check-in sent by clients on the new channel upon receiving NEW_CHANNEL_PING."""
    node_id: int
    session_id: str
    current_channel: int
    timestamp_ms: Optional[int] = None
    config: Optional[Dict[str, Any]] = None
    calibration: bool = False
    job_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "type": "COMMIT_SUCCESS",
            "node_id": self.node_id,
            "session_id": self.session_id,
            "current_channel": self.current_channel,
            "config": self.config,
            "calibration": self.calibration,
        }
        if self.timestamp_ms is not None:
            res["timestamp_ms"] = self.timestamp_ms
        if self.job_id is not None:
            res["job_id"] = self.job_id
        return res

    def to_bytes(self) -> bytes:
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CommitSuccess":
        if not isinstance(data, dict):
            raise TypeError(f"Payload must be a dict, got {type(data).__name__}")
        if "node_id" not in data or type(data["node_id"]) is not int or not (1 <= data["node_id"] <= 10):
            raise ValueError(f"Invalid or missing node_id: {data.get('node_id')}")
        if "session_id" not in data or not isinstance(data["session_id"], str):
            raise ValueError(f"Invalid or missing session_id: {data.get('session_id')}")
        if "current_channel" not in data or type(data["current_channel"]) is not int:
            raise ValueError(f"Invalid or missing current_channel: {data.get('current_channel')}")
        job_id = data.get("job_id")
        if job_id is not None and (not isinstance(job_id, str) or not job_id):
            raise ValueError("Invalid job_id in COMMIT_SUCCESS")
        return cls(
            node_id=data["node_id"],
            session_id=data["session_id"],
            current_channel=data["current_channel"],
            timestamp_ms=data.get("timestamp_ms"),
            config=data.get("config"),
            calibration=data.get("calibration", False),
            job_id=job_id,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "CommitSuccess":
        return cls.from_dict(json.loads(raw.decode("utf-8")))


@dataclass(frozen=True)
class RadioSwitchResult:
    """Outcome report of a two-phase radio reconfiguration execution."""
    success: bool
    session_id: str
    target_channel: int
    applied_patch: Dict[str, Any]
    status: Literal["finalized", "rolled_back", "radio_error"]
    effective_config: Optional[RadioConfig] = None
    error_message: Optional[str] = None
    failed_phase: Optional[str] = None
    unresponsive_nodes: List[int] = field(default_factory=list)
    unconfirmed_nodes: List[int] = field(default_factory=list)
    job_id: Optional[str] = None
    before_config: Optional[RadioConfig] = None
    target_nodes: List[int] = field(default_factory=list)
    confirmed_nodes: List[int] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.success


class _RadioSwitchCancelled(Exception):
    """Internal cancellation marker for the reversible transaction path."""


class LeaseWatchdog:
    """
    15-second Lease Watchdog (ADR-0012, ADR-0014, Ticket 04).
    Prevents channel split-brain by arming a countdown when radio reconfiguration commit starts.
    If server heartbeats/pings cease before finalization, watchdog expires and executes fallback:
    forces physical wireless adapter back to benchmark Channel 157 and resumes hunting.
    """

    def __init__(
        self,
        timeout_seconds: float = LEASE_TIMEOUT_DEFAULT_SECONDS,
        on_expired: Optional[Callable[[], None]] = None,
        name: str = "wfb-lease-watchdog",
    ):
        self.timeout_seconds = timeout_seconds
        self.on_expired = on_expired
        self.name = name
        self._generation = 0
        self._deadline: float = 0.0
        self._armed: bool = False
        self._expired: bool = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

    @property
    def is_armed(self) -> bool:
        with self._lock:
            return self._armed and not self._expired and time.monotonic() < self._deadline

    @property
    def is_expired(self) -> bool:
        with self._lock:
            return self._expired

    def arm(self, timeout_seconds: Optional[float] = None) -> None:
        """Arm or restart the countdown."""
        with self._lock:
            if timeout_seconds is not None:
                self.timeout_seconds = timeout_seconds
            self._deadline = time.monotonic() + self.timeout_seconds
            self._armed = True
            self._expired = False
            self._generation += 1
            self._condition.notify_all()
            self._thread = threading.Thread(
                target=self._run, args=(self._generation,), name=self.name, daemon=True
            )
            self._thread.start()

    def refresh(self, timeout_seconds: Optional[float] = None) -> bool:
        """
        Refresh the watchdog countdown if armed.
        Returns True if successfully refreshed, False if not armed or already expired.
        """
        with self._lock:
            if not self._armed or self._expired or time.monotonic() >= self._deadline:
                return False
            if timeout_seconds is not None:
                self.timeout_seconds = timeout_seconds
            self._deadline = time.monotonic() + self.timeout_seconds
            self._condition.notify_all()
            return True

    def disarm(self) -> None:
        """Disarm and cancel the watchdog cleanly upon switch finalization."""
        with self._lock:
            self._armed = False
            self._condition.notify_all()

    def disarm_if_alive(self) -> bool:
        """Atomically accept confirmation only before lease expiration."""
        with self._lock:
            if not self.is_armed:
                return False
            self.disarm()
            return True

    def _run(self, generation: int) -> None:
        with self._condition:
            while generation == self._generation and self._armed:
                remaining = self._deadline - time.monotonic()
                if remaining > 0:
                    # Condition.wait atomically releases the deadline lock. A
                    # concurrent refresh/rearm cannot lose its wake notification.
                    self._condition.wait(timeout=remaining)
                    continue
                self._armed = False
                self._expired = True
                break
            else:
                return
        logger.warning(
            "射频重配租约看门狗耗尽 (超时 %.2fs)！触发防脑裂自愈回退！",
            self.timeout_seconds,
        )
        if self.on_expired is not None:
            try:
                self.on_expired()
            except Exception as exc:
                logger.error("租约看门狗超时自愈回调执行异常: %s", exc)


@dataclass
class NodeRecord:
    """Server-side tracked node status in NodeHorizonRegistry."""
    node_id: int
    tun_ip: str
    reported_state: str
    readiness: NodeReadiness
    elapsed_ms: int
    current_channel: int
    txpower_dbm: int
    uplink_mcs: int
    error_code: Optional[str] = None
    timestamp_ms: Optional[int] = None
    last_heartbeat_time: float = field(default_factory=time.monotonic)
    last_heartbeat_wall_time: float = field(default_factory=time.time)
    addr: Optional[Tuple[str, int]] = None
    bandwidth_mhz: int = 40
    downlink_mcs: int = 3
    uftp_rate_kbps: int = 15000
    calibration: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "tun_ip": self.tun_ip,
            "reported_state": self.reported_state,
            "readiness": self.readiness.value,
            "elapsed_ms": self.elapsed_ms,
            "current_channel": self.current_channel,
            "txpower_dbm": self.txpower_dbm,
            "uplink_mcs": self.uplink_mcs,
            "bandwidth_mhz": self.bandwidth_mhz,
            "downlink_mcs": self.downlink_mcs,
            "uftp_rate_kbps": self.uftp_rate_kbps,
            "calibration": self.calibration,
            "error_code": self.error_code,
            "timestamp_ms": self.timestamp_ms,
            "last_heartbeat_time": self.last_heartbeat_time,
            "last_heartbeat_wall_time": self.last_heartbeat_wall_time,
            "addr": list(self.addr) if self.addr else None,
        }


class NodeHorizonRegistry:
    """
    In-memory registry maintaining the live topology horizon of connected nodes.
    Implements the two-army anti-desync gate:
    Nodes in HUNTING stay in CONNECTING (🟡 连接中) until confirmed IDLE (🟢 就绪).
    Direct unsolicited IDLE from unverified nodes is held in CONNECTING.
    Replayed and out-of-order heartbeats are dropped to guarantee timing monotonicity.
    """

    def __init__(self, offline_threshold_seconds: float = NODE_OFFLINE_THRESHOLD_SECONDS):
        self.offline_threshold_seconds = offline_threshold_seconds
        self._nodes: Dict[int, NodeRecord] = {}
        # Successful HUNTING -> IDLE identity survives OFFLINE liveness views.
        self._verified_nodes: Set[int] = set()
        self._awaiting_idle_confirmation: Dict[int, bool] = {}
        self._hunting_timestamps: Dict[int, int] = {}
        self._lock = threading.RLock()

    def update_heartbeat(
        self,
        heartbeat: NodeHeartbeat,
        client_addr: Tuple[str, int],
        server_radio: RadioConfig,
    ) -> Tuple[HeartbeatAck, Optional[Dict[str, Any]]]:
        """
        Record received heartbeat and generate appropriate ACK and alignment patch.
        Returns:
            Tuple of (HeartbeatAck, optional alignment patch).
        """
        with self._lock:
            now = time.monotonic()
            node_id = heartbeat.node_id
            tun_ip = f"10.80.0.{10 + node_id}"

            existing_node = self.get_node(node_id)
            # Replay and disorder protection: ignore stale or duplicate heartbeats
            if (
                existing_node is not None
                and heartbeat.timestamp_ms is not None
                and existing_node.timestamp_ms is not None
                and heartbeat.timestamp_ms <= existing_node.timestamp_ms
            ):
                logger.debug(
                    "忽略来自节点 %d 的旧乱序或重复心跳 (ts=%d <= existing_ts=%d)",
                    node_id,
                    heartbeat.timestamp_ms,
                    existing_node.timestamp_ms,
                )
                return HeartbeatAck(ack=True), None

            # Two-Army Gate:
            # HUNTING opens confirmation; only its IDLE closure verifies identity.
            # Silence changes liveness, never this daemon-lifetime identity.
            if heartbeat.state == ClientNodeState.HUNTING.value:
                readiness = NodeReadiness.CONNECTING
                self._awaiting_idle_confirmation[node_id] = True
                if heartbeat.timestamp_ms is not None:
                    self._hunting_timestamps[node_id] = heartbeat.timestamp_ms
            elif heartbeat.state == ClientNodeState.IDLE.value:
                hunting_ts = self._hunting_timestamps.get(node_id)
                ts_valid = True
                if hunting_ts is not None and heartbeat.timestamp_ms is not None:
                    ts_valid = heartbeat.timestamp_ms >= hunting_ts

                if (self._awaiting_idle_confirmation.get(node_id, False) and ts_valid) or node_id in self._verified_nodes:
                    readiness = NodeReadiness.READY
                    self._verified_nodes.add(node_id)
                    self._awaiting_idle_confirmation[node_id] = False
                else:
                    # Unsolicited IDLE without prior HUNTING handshake stays in CONNECTING
                    readiness = NodeReadiness.CONNECTING
            elif heartbeat.state in (
                ClientNodeState.PREPARING.value,
                ClientNodeState.RUNNING.value,
            ):
                readiness = NodeReadiness.ACTIVE
            elif heartbeat.state == ClientNodeState.ABORTING.value:
                readiness = NodeReadiness.ABORTING
            else:
                readiness = NodeReadiness.CONNECTING

            self._nodes[node_id] = NodeRecord(
                node_id=node_id,
                tun_ip=tun_ip,
                reported_state=heartbeat.state,
                readiness=readiness,
                elapsed_ms=heartbeat.elapsed_ms,
                current_channel=heartbeat.current_channel,
                txpower_dbm=heartbeat.txpower_dbm,
                uplink_mcs=heartbeat.uplink_mcs,
                bandwidth_mhz=heartbeat.bandwidth_mhz,
                downlink_mcs=heartbeat.downlink_mcs,
                uftp_rate_kbps=heartbeat.uftp_rate_kbps,
                calibration=heartbeat.calibration,
                error_code=heartbeat.error_code,
                timestamp_ms=heartbeat.timestamp_ms,
                last_heartbeat_time=now,
                last_heartbeat_wall_time=time.time(),
                addr=client_addr,
            )

            # Check radio parameters alignment (txpower & uplink_mcs; rule #89 forbids silent channel hop)
            align_patch: Dict[str, Any] = {}
            if heartbeat.txpower_dbm != server_radio.radio_txpower_dbm:
                align_patch["radio_txpower_dbm"] = server_radio.radio_txpower_dbm
            if heartbeat.uplink_mcs != server_radio.uplink_mcs:
                align_patch["uplink_mcs"] = server_radio.uplink_mcs

            for key in ("bandwidth_mhz", "downlink_mcs", "uftp_rate_kbps"):
                if getattr(heartbeat, key) != getattr(server_radio, key):
                    align_patch[key] = getattr(server_radio, key)
            if align_patch:
                # 对齐传播完整逻辑配置，信道仍由操作者和寻频流程控制。
                align_patch = {k: v for k, v in server_radio.to_dict().items()
                               if k in ("radio_txpower_dbm", "uplink_mcs", "downlink_mcs",
                                        "bandwidth_mhz", "uftp_rate_kbps")}
                return HeartbeatAck(ack=True), align_patch
            return HeartbeatAck(ack=True), None

    def get_node(self, node_id: int) -> Optional[NodeRecord]:
        """Retrieve a node record, marking it OFFLINE if silent for > offline_threshold."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                return None
            if time.monotonic() - node.last_heartbeat_time > self.offline_threshold_seconds:
                return replace(node, readiness=NodeReadiness.OFFLINE)
            return node

    def get_all_nodes(self) -> Dict[int, NodeRecord]:
        """Return all tracked nodes with up-to-date readiness."""
        with self._lock:
            res: Dict[int, NodeRecord] = {}
            for nid in list(self._nodes.keys()):
                record = self.get_node(nid)
                if record is not None:
                    res[nid] = record
            return res

    @contextmanager
    def admission_window(self):
        """在目标终态提交的瞬间阻止心跳改写注册表事实。"""
        with self._lock:
            yield

    def is_node_ready(self, node_id: int) -> bool:
        """True if the node has confirmed IDLE state within offline threshold."""
        node = self.get_node(node_id)
        if node is None:
            return False
        return (
            node.readiness == NodeReadiness.READY
            and (time.monotonic() - node.last_heartbeat_time <= self.offline_threshold_seconds)
        )


class ControlPlaneServer:
    """
    Server-side symmetric UDP control plane engine.
    - Listens on 10.80.0.1:9001 for client unicast heartbeats.
    - Responds with unicast ACK (b'{"ack": true}', strictly < 20 bytes).
    - If radio mismatch detected, dispatches alignment directive.
    - Broadcasts global downlink directives to 255.255.255.255:9000.
    """

    def __init__(
        self,
        active_radio_config: RadioConfig,
        network_adapter: Optional[Any] = None,
        air_interface: Optional[str] = None,
        bind_host: str = "0.0.0.0",
        bind_port: int = CLIENT_UPLINK_DEFAULT_PORT,
        broadcast_addr: str = SERVER_CONTROL_BROADCAST_ADDR,
        broadcast_port: int = SERVER_CONTROL_BROADCAST_PORT,
        offline_threshold_seconds: float = NODE_OFFLINE_THRESHOLD_SECONDS,
        on_heartbeat_received: Optional[Callable[[NodeHeartbeat, NodeRecord], None]] = None,
        on_radio_applied: Optional[Callable[[RadioConfig], None]] = None,
    ):
        self.active_radio_config = active_radio_config
        self.calibration = False
        # Latched fail-closed state, also set by the daemon on unexpected escape.
        self.radio_error = False
        self.network_adapter = network_adapter
        self.air_interface = air_interface
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.broadcast_addr = broadcast_addr
        self.broadcast_port = broadcast_port
        self.offline_threshold_seconds = offline_threshold_seconds
        self.on_heartbeat_received = on_heartbeat_received
        self.on_radio_applied = on_radio_applied
        self.registry = NodeHorizonRegistry(offline_threshold_seconds=offline_threshold_seconds)

        self._stop_event = threading.Event()
        self._recv_thread: Optional[threading.Thread] = None
        self._server_sock: Optional[socket.socket] = None
        self._broadcast_sock: Optional[socket.socket] = None
        self._lock = threading.RLock()

        # Two-phase radio switch synchronization
        self._switch_lock = threading.Lock()
        self._active_switch_session: Optional[str] = None
        self._target_switch_job_id: Optional[str] = None
        self._target_switch_channel: Optional[int] = None
        self._target_switch_config: Optional[Dict[str, Any]] = None
        self._target_switch_calibration = False
        self._prepare_acks: Dict[int, PrepareAck] = {}
        self._commit_successes: Dict[int, CommitSuccess] = {}
        self._prepare_event = threading.Event()
        self._commit_event = threading.Event()
        self._expected_target_nodes: Set[int] = set()
        self._switch_phase: Optional[str] = None
        self._finalized_acks: Set[int] = set()
        self._confirmed_acks: Set[int] = set()
        self._rollback_acks: Set[int] = set()
        self._barrier_event = threading.Event()

        # 客户端角色服务作业就绪同步。任务报告与私有 systemd READY
        # 通知相互独立。
        self._task_ready_job_id: Optional[str] = None
        self._task_ready_expected_nodes: Set[int] = set()
        self._task_ready_nodes: Set[int] = set()
        self._task_ready_reports: Dict[int, Dict[str, Any]] = {}
        self._task_ready_seen: Dict[int, Dict[str, Any]] = {}
        self._task_ready_report_validator: Optional[Callable[
            [Dict[str, Any], Tuple[str, int]], Any]] = None
        self._task_ready_completed = False
        self._task_ready_active = False
        self._task_ready_cancelled = False
        self._task_ready_cancel_reason: Optional[str] = None
        self._task_ready_error: Optional[Dict[str, Any]] = None
        self._task_ready_event = threading.Event()

        # 轮次完成屏障。参与节点取自任务准备屏障，因此不需要新增 Runtime 接口。
        self._round_job_id: Optional[str] = None
        self._round_index: Optional[int] = None
        self._round_expected_nodes: Set[int] = set()
        self._round_reports: Dict[int, Dict[str, Any]] = {}
        self._round_seen: Dict[int, Dict[str, Any]] = {}
        self._round_error: Optional[Dict[str, Any]] = None
        self._round_active = False
        self._round_cancelled = False
        self._round_cancel_reason: Optional[str] = None
        self._round_event = threading.Event()
        self._task_error_handlers: List[Callable[[Dict[str, Any]], None]] = []

        # 终态恢复屏障。恢复报告按节点保存最新 envelope，按 epoch 保存幂等 receipt。
        self._recovery_job_id: Optional[str] = None
        self._recovery_expected_nodes: Set[int] = set()
        self._recovery_reports: Dict[int, Dict[str, Any]] = {}
        self._recovery_seen: Dict[int, Dict[int, Dict[str, Any]]] = {}
        self._recovery_last_epochs: Dict[int, int] = {}
        self._recovery_wait_observed_epochs: Dict[int, int] = {}
        self._recovery_active = False
        self._recovery_cancelled = False
        self._recovery_event = threading.Event()

        # 只保留最近结束任务的有限报告 receipt，不形成历史数据库。
        self._recent_report_job_id: Optional[str] = None
        self._recent_report_receipts: Dict[str, List[Dict[str, Any]]] = {
            "preparation": [],
            "round": [],
            "error": [],
            "recovery": [],
        }

        # 作业级 liveness 观察依赖单调时钟；监视线程不持控制锁调用外部回调。
        self._liveness_condition = threading.Condition(self._lock)
        self._liveness_watches: Dict[str, Dict[str, Any]] = {}
        self._liveness_leases: Dict[Tuple[str, str], float] = {}
        self._liveness_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Bind sockets and start receiving client heartbeats."""
        with self._lock:
            if self._recv_thread is not None and self._recv_thread.is_alive():
                return

            self._stop_event.clear()

            # Heartbeat listener socket
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind((self.bind_host, self.bind_port))
            self._server_sock.settimeout(0.1)

            # Broadcast sender socket
            self._broadcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._broadcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._broadcast_sock.bind((self.bind_host, 0))

            self._recv_thread = threading.Thread(
                target=self._recv_loop,
                name="wfb-server-control-recv",
                daemon=True,
            )
            self._recv_thread.start()
            self._start_liveness_monitor_locked()

    def stop(self) -> None:
        """设置停止事实并等待控制面工作线程真实退出后关闭套接字。"""
        self._stop_event.set()
        with self._liveness_condition:
            self._cancel_barriers_locked(cancel_recovery=True)
            self._liveness_condition.notify_all()

        current_thread = threading.current_thread()
        recv_thread = self._recv_thread
        if recv_thread is not None and recv_thread is not current_thread:
            recv_thread.join()
            with self._lock:
                if self._recv_thread is recv_thread:
                    self._recv_thread = None

        liveness_thread = self._liveness_thread
        if liveness_thread is not None and liveness_thread is not current_thread:
            liveness_thread.join()
            with self._lock:
                if self._liveness_thread is liveness_thread:
                    self._liveness_thread = None

        with self._lock:
            if self._server_sock is not None:
                try:
                    self._server_sock.close()
                except Exception:
                    pass
                self._server_sock = None

            if self._broadcast_sock is not None:
                try:
                    self._broadcast_sock.close()
                except Exception:
                    pass
                self._broadcast_sock = None

    def _apply_channel_switch(self, channel: int, bandwidth_mhz: Optional[int] = None) -> None:
        """Switch server physical interface channel via network adapter. Fail closed on error."""
        if self.network_adapter is not None and self.air_interface is not None:
            width = RadioConfig(channel=channel, bandwidth_mhz=(
                self.active_radio_config.bandwidth_mhz if bandwidth_mhz is None else bandwidth_mhz
            )).channel_width
            self.network_adapter.set_channel(
                self.air_interface, channel=channel, channel_width=width
            )

    def _apply_txpower_switch(self, txpower_dbm: int) -> None:
        """Switch server physical interface txpower via network adapter. Fail closed on error."""
        if self.network_adapter is not None and self.air_interface is not None:
            self.network_adapter.set_txpower(self.air_interface, txpower_dbm=txpower_dbm)

    def update_radio_config(self, config: RadioConfig) -> None:
        """Update server active radio config for alignment comparisons."""
        with self._lock:
            self.active_radio_config = config

    def prepare_task_readiness(
        self,
        job_id: str,
        target_nodes: Sequence[int],
        report_validator: Optional[Callable[[Dict[str, Any], Tuple[str, int]], Any]] = None,
    ) -> None:
        """为已校验的客户端报告开启任务准备屏障。"""
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError('invalid_task_readiness', 'job_id 无效')
        try:
            expected_nodes = set(target_nodes)
        except TypeError as exc:
            raise FLRuntimeError('invalid_task_readiness', 'target_nodes 无效') from exc
        if (not expected_nodes or
                any(type(node_id) is not int or not 1 <= node_id <= 10
                    for node_id in expected_nodes)):
            raise FLRuntimeError('invalid_task_readiness', 'target_nodes 无效')
        if report_validator is not None and not callable(report_validator):
            raise FLRuntimeError('invalid_task_readiness', 'report_validator 不可调用')

        with self._lock:
            # 新任务替换最近结束任务的 receipt，避免旧作业报告进入新屏障。
            self._recent_report_job_id = None
            self._recent_report_receipts = {
                "preparation": [],
                "round": [],
                "error": [],
                "recovery": [],
            }
            self._task_ready_job_id = job_id
            self._task_ready_expected_nodes = expected_nodes
            self._task_ready_nodes.clear()
            self._task_ready_reports.clear()
            self._task_ready_seen.clear()
            self._task_ready_report_validator = report_validator
            self._task_ready_completed = False
            self._task_ready_active = True
            self._task_ready_cancelled = False
            self._task_ready_cancel_reason = None
            self._task_ready_error = None
            self._task_ready_event.clear()
            self._round_job_id = None
            self._round_index = None
            self._round_expected_nodes.clear()
            self._round_reports.clear()
            self._round_seen.clear()
            self._round_error = None
            self._round_active = False
            self._round_cancelled = False
            self._round_cancel_reason = None
            self._round_event.clear()
            self._recovery_job_id = None
            self._recovery_expected_nodes.clear()
            self._recovery_reports.clear()
            self._recovery_seen.clear()
            self._recovery_last_epochs.clear()
            self._recovery_wait_observed_epochs.clear()
            self._recovery_active = False
            self._recovery_cancelled = False
            self._recovery_event.clear()

    def wait_for_task_readiness(self, timeout: float) -> bool:
        """等待全部准备报告；收到客户端错误时抛出异常。"""
        deadline = self._wait_deadline(timeout)
        while True:
            with self._lock:
                if self._task_ready_error is not None:
                    raise self._report_error(self._task_ready_error)
                if (self._task_ready_active and
                        self._task_ready_expected_nodes.issubset(self._task_ready_nodes)):
                    self._task_ready_completed = True
                    return True
                if self._task_ready_cancelled or not self._task_ready_active:
                    return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._task_ready_event.wait(remaining)

    def get_task_preparation_reports(self, job_id: str) -> Dict[int, Dict[str, Any]]:
        """返回已接受准备报告的独立 envelope 快照，实际报告位于 entry['report']。"""
        with self._lock:
            if job_id != self._task_ready_job_id:
                return {}
            return deepcopy(self._task_ready_reports)

    def prepare_round_completion(self, job_id: str, round_index: int) -> None:
        """使用任务参与者快照开启轮次完成屏障。"""
        if type(round_index) is not int or round_index <= 0:
            raise FLRuntimeError('invalid_round_report', 'round_index 无效')
        with self._lock:
            if job_id != self._task_ready_job_id:
                raise FLRuntimeError('round_not_prepared', 'job_id 没有对应的任务准备上下文')
            if not self._task_ready_completed:
                raise FLRuntimeError('task_not_ready', '任务准备屏障尚未完成')
            if self._task_ready_cancelled:
                raise FLRuntimeError('task_cancelled', '任务准备屏障已取消')
            if (self._round_active and self._round_job_id == job_id and
                    self._round_index != round_index and
                    self._round_error is None and
                    not self._round_expected_nodes.issubset(self._round_reports)):
                raise FLRuntimeError('round_in_progress', '上一轮完成屏障仍在等待')
            self._round_job_id = job_id
            self._round_index = round_index
            self._round_expected_nodes = set(self._task_ready_expected_nodes)
            self._round_reports.clear()
            self._round_seen.clear()
            self._round_error = None
            self._round_active = True
            self._round_cancelled = False
            self._round_event.clear()

    def wait_for_round_completion(
        self, job_id: str, round_index: int, timeout: float
    ) -> bool:
        """等待全部轮次报告；收到客户端错误时抛出异常。"""
        if type(round_index) is not int or round_index <= 0:
            raise FLRuntimeError('invalid_round_report', 'round_index 无效')
        deadline = self._wait_deadline(timeout)
        while True:
            with self._lock:
                if (not self._round_active or self._round_job_id != job_id or
                        self._round_index != round_index):
                    raise FLRuntimeError('round_not_prepared', '轮次完成屏障未准备')
                if self._round_error is not None:
                    raise self._report_error(self._round_error)
                if self._round_cancelled:
                    return False
                if self._round_expected_nodes.issubset(self._round_reports):
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._round_event.wait(remaining)

    def _cancel_barriers_locked(self, *, cancel_recovery: bool = False) -> None:
        """Wake task/round waiters; recovery is cancelled only during daemon stop."""
        self._task_ready_cancelled = True
        self._round_cancelled = True
        self._task_ready_event.set()
        self._round_event.set()
        if cancel_recovery and self._recovery_active:
            self._recovery_cancelled = True
            self._recovery_event.set()

    def cancel_task_readiness(self) -> None:
        """唤醒准备和轮次等待；不取消恢复屏障。"""
        with self._lock:
            self._cancel_barriers_locked()

    def cancel_job_barriers(self, job_id: str, reason: str = "job_cancelled") -> None:
        """按作业取消准备/轮次屏障，不触碰已启动的恢复屏障。"""
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError("invalid_job_id", "job_id 无效")
        if not isinstance(reason, str) or not reason:
            raise FLRuntimeError("invalid_cancel_reason", "取消原因无效")
        with self._lock:
            if job_id != self._task_ready_job_id and job_id != self._recovery_job_id:
                return
            self._task_ready_cancel_reason = reason
            self._round_cancel_reason = reason
            self._task_ready_cancelled = True
            self._round_cancelled = True
            self._task_ready_event.set()
            self._round_event.set()

    def prepare_job_recovery(self, job_id: str, target_nodes: Sequence[int]) -> None:
        """只初始化恢复子状态；不得清除准备/轮次 receipt 或屏障。"""
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError("invalid_recovery", "job_id 无效")
        try:
            expected_nodes = set(target_nodes)
        except TypeError as exc:
            raise FLRuntimeError("invalid_recovery", "target_nodes 无效") from exc
        if (not expected_nodes or
                any(type(node_id) is not int or not 1 <= node_id <= 10
                    for node_id in expected_nodes)):
            raise FLRuntimeError("invalid_recovery", "target_nodes 无效")
        with self._lock:
            if job_id != self._task_ready_job_id:
                raise FLRuntimeError("recovery_not_prepared", "job_id 没有对应的任务上下文")
            if expected_nodes != self._task_ready_expected_nodes:
                raise FLRuntimeError("recovery_target_mismatch", "恢复目标集合与任务参与集合不一致")
            if self._recovery_active and not self._recovery_cancelled:
                raise FLRuntimeError("recovery_in_progress", "恢复屏障已经开启")
            self._recovery_job_id = job_id
            self._recovery_expected_nodes = expected_nodes
            self._recovery_reports.clear()
            self._recovery_seen.clear()
            self._recovery_last_epochs.clear()
            self._recovery_wait_observed_epochs.clear()
            self._recovery_active = True
            self._recovery_cancelled = False
            self._recovery_event.clear()

    def wait_for_job_recovery(self, job_id: str, timeout: float) -> bool:
        """等待全部目标 ready；新的 blocked epoch 返回 False 但保留恢复屏障。"""
        deadline = self._wait_deadline(timeout)
        while True:
            with self._lock:
                if not self._recovery_active or self._recovery_job_id != job_id:
                    raise FLRuntimeError("recovery_not_prepared", "恢复屏障未准备")
                if self._recovery_cancelled:
                    return False
                if (self._recovery_expected_nodes and
                        all(
                            self._recovery_reports.get(node_id, {}).get("report", {}).get(
                                "recovery_state") == "ready"
                            for node_id in self._recovery_expected_nodes
                        )):
                    return True
                for node_id, envelope in self._recovery_reports.items():
                    report = envelope.get("report", {})
                    epoch = report.get("recovery_epoch")
                    if (report.get("recovery_state") == "blocked"
                            and type(epoch) is int
                            and epoch > self._recovery_wait_observed_epochs.get(node_id, 0)):
                        self._recovery_wait_observed_epochs[node_id] = epoch
                        self._recovery_event.clear()
                        return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._recovery_event.wait(remaining)

    def get_job_recovery_reports(self, job_id: str) -> Dict[int, Dict[str, Any]]:
        """返回恢复报告 envelope；实际事实位于 entry['report']。"""
        with self._lock:
            if job_id != self._recovery_job_id:
                return {}
            return deepcopy(self._recovery_reports)

    def clear_task_readiness(self, job_id: Optional[str] = None) -> None:
        """终态后清除 task/round/recovery 状态，并归档所有有限 receipt。"""
        with self._lock:
            current_job_id = self._task_ready_job_id or self._recovery_job_id
            if job_id is not None and job_id != current_job_id:
                return
            self._archive_report_receipts_locked()
            self._task_ready_active = False
            self._task_ready_cancelled = False
            self._task_ready_cancel_reason = None
            self._task_ready_nodes.clear()
            self._task_ready_reports.clear()
            self._task_ready_seen.clear()
            self._task_ready_report_validator = None
            self._task_ready_error = None
            self._task_ready_event.clear()
            self._round_job_id = None
            self._round_index = None
            self._round_expected_nodes.clear()
            self._round_reports.clear()
            self._round_seen.clear()
            self._round_error = None
            self._round_active = False
            self._round_cancelled = False
            self._round_cancel_reason = None
            self._round_event.clear()
            self._recovery_job_id = None
            self._recovery_expected_nodes.clear()
            self._recovery_reports.clear()
            self._recovery_seen.clear()
            self._recovery_last_epochs.clear()
            self._recovery_wait_observed_epochs.clear()
            self._recovery_active = False
            self._recovery_cancelled = False
            self._recovery_event.clear()

    def watch_job_liveness(
        self,
        job_id: str,
        target_nodes: Sequence[int],
        on_change: Callable[[Dict[str, Any]], None],
    ) -> None:
        """注册 job-scoped liveness watcher，不阻塞 UDP 接收线程。"""
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError("invalid_liveness_watch", "job_id 无效")
        if not callable(on_change):
            raise FLRuntimeError("invalid_liveness_watch", "on_change 不可调用")
        try:
            expected_nodes = set(target_nodes)
        except TypeError as exc:
            raise FLRuntimeError("invalid_liveness_watch", "target_nodes 无效") from exc
        if (not expected_nodes or
                any(type(node_id) is not int or not 1 <= node_id <= 10
                    for node_id in expected_nodes)):
            raise FLRuntimeError("invalid_liveness_watch", "target_nodes 无效")
        with self._liveness_condition:
            if self._stop_event.is_set():
                raise FLRuntimeError("control_plane_stopped", "控制面已经停止")
            if job_id in self._liveness_watches:
                raise FLRuntimeError("liveness_watch_in_progress", "作业 liveness watcher 已存在")
            states = {
                node_id: self._node_online_locked(node_id)
                for node_id in expected_nodes
            }
            self._liveness_watches[job_id] = {
                "target_nodes": expected_nodes,
                "on_change": on_change,
                "states": states,
            }
            self._start_liveness_monitor_locked()
            self._liveness_condition.notify_all()

    def unwatch_job_liveness(self, job_id: str) -> None:
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError("invalid_liveness_watch", "job_id 无效")
        with self._liveness_condition:
            self._liveness_watches.pop(job_id, None)
            for key in list(self._liveness_leases):
                if key[0] == job_id:
                    self._liveness_leases.pop(key, None)
            self._liveness_condition.notify_all()

    def begin_job_liveness_lease(
        self, job_id: str, transaction_id: str, timeout_seconds: float
    ) -> None:
        if (not isinstance(job_id, str) or not job_id or
                not isinstance(transaction_id, str) or not transaction_id):
            raise FLRuntimeError("invalid_liveness_lease", "job_id 或 transaction_id 无效")
        if (isinstance(timeout_seconds, bool) or
                not isinstance(timeout_seconds, (int, float)) or
                not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0):
            raise FLRuntimeError("invalid_liveness_lease", "liveness lease 期限无效")
        with self._liveness_condition:
            if job_id not in self._liveness_watches:
                raise FLRuntimeError("liveness_watch_not_prepared", "作业 liveness watcher 未准备")
            self._liveness_leases[(job_id, transaction_id)] = (
                time.monotonic() + float(timeout_seconds)
            )
            self._liveness_condition.notify_all()

    def end_job_liveness_lease(self, job_id: str, transaction_id: str) -> None:
        if (not isinstance(job_id, str) or not job_id or
                not isinstance(transaction_id, str) or not transaction_id):
            raise FLRuntimeError("invalid_liveness_lease", "job_id 或 transaction_id 无效")
        with self._liveness_condition:
            self._liveness_leases.pop((job_id, transaction_id), None)
            self._liveness_condition.notify_all()

    def _start_liveness_monitor_locked(self) -> None:
        if self._liveness_thread is not None and self._liveness_thread.is_alive():
            return
        self._liveness_thread = threading.Thread(
            target=self._liveness_loop,
            name="wfb-server-job-liveness",
            daemon=True,
        )
        self._liveness_thread.start()

    def _node_online_locked(self, node_id: int) -> bool:
        record = self.registry.get_node(node_id)
        return record is not None and record.readiness != NodeReadiness.OFFLINE

    def _active_liveness_lease_deadline_locked(
        self, job_id: str, now: float
    ) -> Optional[float]:
        deadlines: List[float] = []
        for key, deadline in list(self._liveness_leases.items()):
            if deadline <= now:
                self._liveness_leases.pop(key, None)
            elif key[0] == job_id:
                deadlines.append(deadline)
        return min(deadlines) if deadlines else None

    def _liveness_loop(self) -> None:
        while not self._stop_event.is_set():
            callbacks: List[Tuple[Callable[[Dict[str, Any]], None], Dict[str, Any]]] = []
            with self._liveness_condition:
                now = time.monotonic()
                next_deadline: Optional[float] = None
                for job_id, watch in list(self._liveness_watches.items()):
                    target_nodes = watch["target_nodes"]
                    states = watch["states"]
                    lease_deadline = self._active_liveness_lease_deadline_locked(job_id, now)
                    if lease_deadline is not None:
                        next_deadline = (
                            lease_deadline if next_deadline is None
                            else min(next_deadline, lease_deadline)
                        )
                    for node_id in target_nodes:
                        record = self.registry.get_node(node_id)
                        online = record is not None and record.readiness != NodeReadiness.OFFLINE
                        previous = states.get(node_id)
                        if online and previous is False:
                            states[node_id] = True
                            callbacks.append((watch["on_change"], {
                                "job_id": job_id,
                                "node_id": node_id,
                                "state": "online",
                                "reason": "heartbeat_received",
                                "observed_at": now,
                                "last_heartbeat_at": record.last_heartbeat_time if record else None,
                            }))
                        elif not online and previous is True and lease_deadline is None:
                            states[node_id] = False
                            callbacks.append((watch["on_change"], {
                                "job_id": job_id,
                                "node_id": node_id,
                                "state": "offline",
                                "reason": "heartbeat_timeout",
                                "observed_at": now,
                                "last_heartbeat_at": record.last_heartbeat_time if record else None,
                            }))
                        if online and lease_deadline is None and record is not None:
                            heartbeat_deadline = (
                                record.last_heartbeat_time + self.offline_threshold_seconds
                            )
                            next_deadline = (
                                heartbeat_deadline if next_deadline is None
                                else min(next_deadline, heartbeat_deadline)
                            )
                if callbacks:
                    wait_timeout = 0.0
                elif next_deadline is None:
                    wait_timeout = None
                else:
                    wait_timeout = max(0.0, next_deadline - time.monotonic())
                if not callbacks:
                    self._liveness_condition.wait(wait_timeout)
            for callback, event in callbacks:
                try:
                    callback(event)
                except Exception as exc:
                    logger.warning("作业 liveness watcher 回调异常: %s", exc)

    @staticmethod
    def _wait_deadline(timeout: float) -> float:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
            raise FLRuntimeError('invalid_wait_timeout', '等待超时参数无效')
        return time.monotonic() + float(timeout)

    @staticmethod
    def _report_error(report: Dict[str, Any]) -> FLRuntimeError:
        return FLRuntimeError(
            report.get('error_code', 'task_error'),
            report.get('reason', '客户端报告作业错误'),
            node_id=report.get('node_id'),
            details={
                'job_id': report.get('job_id'),
                'phase': report.get('phase'),
                'round_index': report.get('round_index'),
                'report_id': report.get('report_id'),
            },
        )

    def register_task_error_handler(
        self, handler: Callable[[Dict[str, Any]], None]
    ) -> None:
        """注册轻量错误观察器；异步投递和处理由守护进程负责。"""
        if not callable(handler):
            raise TypeError('task error handler must be callable')
        with self._lock:
            self._task_error_handlers.append(handler)

    def broadcast_downlink(self, message: Dict[str, Any]) -> None:
        """向 255.255.255.255:9000 广播控制消息，并校验 UTF-8 字节上限。"""
        with self._lock:
            if self._broadcast_sock is None:
                raise RuntimeError("Server broadcast socket is not initialized")
            try:
                data = json.dumps(
                    message, ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise FLRuntimeError(
                    "invalid_control_message", "控制广播消息不可编码"
                ) from exc
            if len(data) > MAX_CONTROL_DATAGRAM_BYTES:
                raise FLRuntimeError(
                    "control_message_too_large", "控制广播消息超过 4096 字节"
                )
            self._broadcast_sock.sendto(data, (self.broadcast_addr, self.broadcast_port))

    @staticmethod
    def _report_fingerprint(payload: Dict[str, Any]) -> str:
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )

    @staticmethod
    def _report_source_host(client_addr: Tuple[str, int]) -> Optional[str]:
        return client_addr[0] if isinstance(client_addr, tuple) and client_addr else None

    @staticmethod
    def _receipt_category(report_type: Any) -> Optional[str]:
        if report_type == "TASK_READY":
            return "preparation"
        if report_type == "ROUND_READY":
            return "round"
        if report_type == "TASK_ERROR":
            return "error"
        if report_type == "JOB_RECOVERY":
            return "recovery"
        return None

    def _recent_report_status_locked(
        self,
        payload: Dict[str, Any],
        report_type: str,
        client_addr: Tuple[str, int],
    ) -> Optional[str]:
        """检查最近结束任务的 receipt；调用时必须持有控制锁。"""
        if self._recent_report_job_id != payload.get("job_id"):
            return None
        category = self._receipt_category(report_type)
        if category is None:
            return None
        for receipt in self._recent_report_receipts[category]:
            stored = receipt.get("payload")
            if (not isinstance(stored, dict)
                    or stored.get("type") != report_type
                    or stored.get("node_id") != payload.get("node_id")):
                continue
            if receipt.get("report_id") != payload.get("report_id"):
                return "conflict"
            if (receipt.get("fingerprint") == self._report_fingerprint(payload)
                    and receipt.get("source_host") == self._report_source_host(client_addr)):
                return "idempotent"
            return "conflict"
        return None

    def _archive_report_receipts_locked(self) -> None:
        """只保存最近任务的每节点准备、末轮报告及首个错误，大小受冻结参与集合约束。"""
        job_id = self._task_ready_job_id
        if job_id is None:
            return
        receipts: Dict[str, List[Dict[str, Any]]] = {
            "preparation": [],
            "round": [],
            "error": [],
            "recovery": [],
        }

        def append_seen(seen: Dict[str, Any]) -> None:
            payload = seen.get("payload")
            if not isinstance(payload, dict) or payload.get("job_id") != job_id:
                return
            category = self._receipt_category(payload.get("type"))
            if category is None:
                return
            if category == "error" and receipts[category]:
                return
            receipts[category].append({
                "report_id": seen.get("report_id"),
                "fingerprint": seen.get("fingerprint"),
                "source_host": seen.get("source_host"),
                "payload": deepcopy(payload),
            })

        for seen in self._task_ready_seen.values():
            append_seen(seen)
        for seen in self._round_seen.values():
            append_seen(seen)
        for node_seen in self._recovery_seen.values():
            for seen in node_seen.values():
                append_seen(seen)
        self._recent_report_job_id = job_id
        self._recent_report_receipts = receipts

    @classmethod
    def _report_ack(
        cls,
        report_type: str,
        payload: Dict[str, Any],
        ok: bool,
        reason: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> List[bytes]:
        ack: Dict[str, Any] = {
            "type": f"{report_type}_ACK",
            "report_id": payload.get("report_id"),
            "job_id": payload.get("job_id"),
            "node_id": payload.get("node_id"),
            "ok": ok,
        }
        if reason is not None:
            # 拒绝 ACK 也必须有界，避免校验器抛出超长错误信息。
            ack["reason"] = str(reason)[:512]
        if error_code is not None:
            ack["error_code"] = str(error_code)[:128]
        data = json.dumps(ack, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_CONTROL_DATAGRAM_BYTES:
            ack["reason"] = "REPORT_REJECTED"
            ack.pop("error_code", None)
            data = json.dumps(ack, separators=(",", ":")).encode("utf-8")
        return [data]

    @staticmethod
    def _report_envelope_error(
        payload: Any, report_type: str
    ) -> Optional[str]:
        if not isinstance(payload, dict):
            return "INVALID_REPORT"
        if payload.get("type") != report_type:
            return "INVALID_REPORT_TYPE"
        report_id = payload.get("report_id")
        if not isinstance(report_id, str) or not report_id:
            return "INVALID_REPORT_ID"
        job_id = payload.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return "INVALID_JOB_ID"
        node_id = payload.get("node_id")
        if type(node_id) is not int or not 1 <= node_id <= 10:
            return "INVALID_NODE_ID"
        if report_type == "TASK_READY":
            report = payload.get("report")
            if not isinstance(report, dict) or not report:
                return "TASK_READY_REPORT_REQUIRED"
        elif report_type == "ROUND_READY":
            round_index = payload.get("round_index")
            if type(round_index) is not int or round_index <= 0:
                return "INVALID_ROUND_INDEX"
        elif report_type == "TASK_ERROR":
            phase = payload.get("phase")
            if phase not in ("prepare", "round"):
                return "INVALID_PHASE"
            error_code = payload.get("error_code")
            reason = payload.get("reason")
            if not isinstance(error_code, str) or not error_code:
                return "INVALID_ERROR_CODE"
            if not isinstance(reason, str) or not reason:
                return "INVALID_ERROR_REASON"
            round_index = payload.get("round_index")
            if phase == "prepare":
                if round_index is not None:
                    return "PREPARE_ROUND_INDEX_FORBIDDEN"
            elif type(round_index) is not int or round_index <= 0:
                return "ROUND_INDEX_REQUIRED"
        elif report_type == "JOB_RECOVERY":
            report = payload.get("report")
            if not isinstance(report, dict) or not report:
                return "RECOVERY_REPORT_REQUIRED"
            epoch = report.get("recovery_epoch")
            state = report.get("recovery_state")
            if type(epoch) is not int or epoch <= 0:
                return "INVALID_RECOVERY_EPOCH"
            if state not in ("ready", "blocked"):
                return "INVALID_RECOVERY_STATE"
            if state == "ready":
                if set(report) != {"recovery_epoch", "recovery_state"}:
                    return "READY_REPORT_FIELDS_INVALID"
            else:
                if set(report) != {"recovery_epoch", "recovery_state", "error_code", "reason"}:
                    return "BLOCKED_REPORT_FIELDS_INVALID"
                if (not isinstance(report.get("error_code"), str)
                        or not report["error_code"]
                        or len(report["error_code"].encode("utf-8")) > 128):
                    return "INVALID_RECOVERY_ERROR_CODE"
                if (not isinstance(report.get("reason"), str)
                        or not report["reason"]
                        or len(report["reason"].encode("utf-8")) > 512):
                    return "INVALID_RECOVERY_REASON"
        return None

    @classmethod
    def _seen_status(
        cls,
        seen: Optional[Dict[str, Any]],
        payload: Dict[str, Any],
        client_addr: Tuple[str, int],
    ) -> Optional[str]:
        if seen is None:
            return None
        same_content = (
            seen.get("report_id") == payload.get("report_id")
            and seen.get("fingerprint") == cls._report_fingerprint(payload)
            and seen.get("source_host") == cls._report_source_host(client_addr)
        )
        return "idempotent" if same_content else "conflict"

    @staticmethod
    def _validator_failure(exc: BaseException) -> Tuple[str, Optional[str]]:
        if isinstance(exc, FLRuntimeError):
            return exc.error_message or exc.error_code, exc.error_code
        return str(exc) or "报告校验失败", None

    def _validate_task_report(
        self, payload: Dict[str, Any], client_addr: Tuple[str, int]
    ) -> Optional[List[bytes]]:
        envelope_error = self._report_envelope_error(payload, "TASK_READY")
        if envelope_error is not None:
            return self._report_ack("TASK_READY", payload, False, envelope_error)

        with self._lock:
            recent_status = self._recent_report_status_locked(
                payload, "TASK_READY", client_addr
            )
            if recent_status == "idempotent":
                return self._report_ack("TASK_READY", payload, True)
            if recent_status == "conflict":
                return self._report_ack("TASK_READY", payload, False, "REPORT_ID_CONFLICT")
            if not self._task_ready_active or payload["job_id"] != self._task_ready_job_id:
                return self._report_ack("TASK_READY", payload, False, "TASK_NOT_PREPARED")
            if payload["node_id"] not in self._task_ready_expected_nodes:
                return self._report_ack("TASK_READY", payload, False, "REPORT_NOT_PARTICIPANT")
            seen = self._task_ready_seen.get(payload["node_id"])
            status = self._seen_status(seen, payload, client_addr)
            if status == "idempotent":
                return self._report_ack("TASK_READY", payload, True)
            if status == "conflict":
                return self._report_ack("TASK_READY", payload, False, "REPORT_ID_CONFLICT")
            if self._task_ready_cancelled:
                return self._report_ack("TASK_READY", payload, False, "BARRIER_CANCELLED")
            if self._task_ready_error is not None:
                return self._report_ack("TASK_READY", payload, False, "BARRIER_FAILED")
            validator = self._task_ready_report_validator

        if validator is not None:
            try:
                result = validator(deepcopy(payload), client_addr)
                if result is False:
                    return self._report_ack("TASK_READY", payload, False, "REPORT_VALIDATION_FAILED")
            except Exception as exc:
                reason, error_code = self._validator_failure(exc)
                return self._report_ack("TASK_READY", payload, False, reason, error_code)

        with self._lock:
            # 即使校验器执行在锁外，也要在提交前重新检查屏障状态。
            if not self._task_ready_active or payload["job_id"] != self._task_ready_job_id:
                return self._report_ack("TASK_READY", payload, False, "TASK_NOT_PREPARED")
            if payload["node_id"] not in self._task_ready_expected_nodes:
                return self._report_ack("TASK_READY", payload, False, "REPORT_NOT_PARTICIPANT")
            seen = self._task_ready_seen.get(payload["node_id"])
            status = self._seen_status(seen, payload, client_addr)
            if status == "idempotent":
                return self._report_ack("TASK_READY", payload, True)
            if status == "conflict":
                return self._report_ack("TASK_READY", payload, False, "REPORT_ID_CONFLICT")
            if self._task_ready_cancelled:
                return self._report_ack("TASK_READY", payload, False, "BARRIER_CANCELLED")
            if self._task_ready_error is not None:
                return self._report_ack("TASK_READY", payload, False, "BARRIER_FAILED")
            record = {
                "report_id": payload["report_id"],
                "job_id": payload["job_id"],
                "node_id": payload["node_id"],
                "report": deepcopy(payload["report"]),
                "source": client_addr,
            }
            self._task_ready_seen[payload["node_id"]] = {
                "type": "TASK_READY",
                "payload": deepcopy(payload),
                "report_id": payload["report_id"],
                "fingerprint": self._report_fingerprint(payload),
                "source_host": self._report_source_host(client_addr),
            }
            self._task_ready_reports[payload["node_id"]] = record
            self._task_ready_nodes.add(payload["node_id"])
            if self._task_ready_expected_nodes.issubset(self._task_ready_nodes):
                self._task_ready_completed = True
                self._task_ready_event.set()
        return self._report_ack("TASK_READY", payload, True)

    def _validate_round_report(
        self, payload: Dict[str, Any], client_addr: Tuple[str, int]
    ) -> Optional[List[bytes]]:
        envelope_error = self._report_envelope_error(payload, "ROUND_READY")
        if envelope_error is not None:
            return self._report_ack("ROUND_READY", payload, False, envelope_error)
        with self._lock:
            recent_status = self._recent_report_status_locked(
                payload, "ROUND_READY", client_addr
            )
            if recent_status == "idempotent":
                return self._report_ack("ROUND_READY", payload, True)
            if recent_status == "conflict":
                return self._report_ack("ROUND_READY", payload, False, "REPORT_ID_CONFLICT")
            if (not self._round_active or payload["job_id"] != self._round_job_id or
                    payload["round_index"] != self._round_index):
                return self._report_ack("ROUND_READY", payload, False, "ROUND_NOT_PREPARED")
            if payload["node_id"] not in self._round_expected_nodes:
                return self._report_ack("ROUND_READY", payload, False, "REPORT_NOT_PARTICIPANT")
            seen = self._round_seen.get(payload["node_id"])
            status = self._seen_status(seen, payload, client_addr)
            if status == "idempotent":
                return self._report_ack("ROUND_READY", payload, True)
            if status == "conflict":
                return self._report_ack("ROUND_READY", payload, False, "REPORT_ID_CONFLICT")
            if self._round_cancelled:
                return self._report_ack("ROUND_READY", payload, False, "BARRIER_CANCELLED")
            if self._round_error is not None:
                return self._report_ack("ROUND_READY", payload, False, "BARRIER_FAILED")
            if self._round_expected_nodes.issubset(self._round_reports):
                return self._report_ack("ROUND_READY", payload, False, "BARRIER_COMPLETE")
            self._round_seen[payload["node_id"]] = {
                "type": "ROUND_READY",
                "payload": deepcopy(payload),
                "report_id": payload["report_id"],
                "fingerprint": self._report_fingerprint(payload),
                "source_host": self._report_source_host(client_addr),
            }
            self._round_reports[payload["node_id"]] = {
                "report_id": payload["report_id"],
                "job_id": payload["job_id"],
                "node_id": payload["node_id"],
                "round_index": payload["round_index"],
                "source": client_addr,
            }
            if self._round_expected_nodes.issubset(self._round_reports):
                self._round_event.set()
        return self._report_ack("ROUND_READY", payload, True)

    def _validate_recovery_report(
        self, payload: Dict[str, Any], client_addr: Tuple[str, int]
    ) -> Optional[List[bytes]]:
        envelope_error = self._report_envelope_error(payload, "JOB_RECOVERY")
        if envelope_error is not None:
            return self._report_ack("JOB_RECOVERY", payload, False, envelope_error)

        with self._lock:
            recent_status = self._recent_report_status_locked(
                payload, "JOB_RECOVERY", client_addr
            )
            if recent_status == "idempotent":
                return self._report_ack("JOB_RECOVERY", payload, True)
            if recent_status == "conflict":
                return self._report_ack("JOB_RECOVERY", payload, False, "REPORT_ID_CONFLICT")
            if (not self._recovery_active or
                    payload["job_id"] != self._recovery_job_id):
                return self._report_ack("JOB_RECOVERY", payload, False, "RECOVERY_NOT_PREPARED")
            if payload["node_id"] not in self._recovery_expected_nodes:
                return self._report_ack("JOB_RECOVERY", payload, False, "REPORT_NOT_PARTICIPANT")
            if self._recovery_cancelled:
                return self._report_ack("JOB_RECOVERY", payload, False, "RECOVERY_CANCELLED")

            node_id = payload["node_id"]
            report = payload["report"]
            epoch = report["recovery_epoch"]
            state = report["recovery_state"]
            seen_by_epoch = self._recovery_seen.setdefault(node_id, {})
            seen = seen_by_epoch.get(epoch)
            status = self._seen_status(seen, payload, client_addr)
            if status == "idempotent":
                return self._report_ack("JOB_RECOVERY", payload, True)
            if status == "conflict":
                return self._report_ack("JOB_RECOVERY", payload, False, "REPORT_ID_CONFLICT")

            current = self._recovery_reports.get(node_id)
            current_report = current.get("report", {}) if current else {}
            if current_report.get("recovery_state") == "ready":
                return self._report_ack("JOB_RECOVERY", payload, False, "RECOVERY_ALREADY_READY")
            last_epoch = self._recovery_last_epochs.get(node_id, 0)
            if epoch != last_epoch + 1:
                return self._report_ack("JOB_RECOVERY", payload, False, "RECOVERY_EPOCH_REQUIRED")

            record = {
                "report_id": payload["report_id"],
                "job_id": payload["job_id"],
                "node_id": node_id,
                "report": deepcopy(report),
                "source": client_addr,
            }
            seen_by_epoch[epoch] = {
                "type": "JOB_RECOVERY",
                "payload": deepcopy(payload),
                "report_id": payload["report_id"],
                "fingerprint": self._report_fingerprint(payload),
                "source_host": self._report_source_host(client_addr),
            }
            self._recovery_last_epochs[node_id] = epoch
            self._recovery_reports[node_id] = record
            self._recovery_event.set()
        return self._report_ack("JOB_RECOVERY", payload, True)

    def _validate_error_report(
        self, payload: Dict[str, Any], client_addr: Tuple[str, int]
    ) -> Optional[List[bytes]]:
        envelope_error = self._report_envelope_error(payload, "TASK_ERROR")
        if envelope_error is not None:
            return self._report_ack("TASK_ERROR", payload, False, envelope_error)

        handlers: List[Callable[[Dict[str, Any]], None]] = []
        with self._lock:
            recent_status = self._recent_report_status_locked(
                payload, "TASK_ERROR", client_addr
            )
            if recent_status == "idempotent":
                return self._report_ack("TASK_ERROR", payload, True)
            if recent_status == "conflict":
                return self._report_ack("TASK_ERROR", payload, False, "REPORT_ID_CONFLICT")
            phase = payload["phase"]
            if phase == "prepare":
                active = (
                    self._task_ready_active and
                    payload["job_id"] == self._task_ready_job_id
                )
                expected_nodes = self._task_ready_expected_nodes
                seen_map = self._task_ready_seen
                current_error = self._task_ready_error
            else:
                active = (
                    self._round_active and
                    payload["job_id"] == self._round_job_id and
                    payload["round_index"] == self._round_index
                )
                expected_nodes = self._round_expected_nodes
                seen_map = self._round_seen
                current_error = self._round_error
            if not active:
                return self._report_ack("TASK_ERROR", payload, False, "PHASE_NOT_ACTIVE")
            if payload["node_id"] not in expected_nodes:
                return self._report_ack("TASK_ERROR", payload, False, "REPORT_NOT_PARTICIPANT")
            seen = seen_map.get(payload["node_id"])
            status = self._seen_status(seen, payload, client_addr)
            if status == "idempotent":
                return self._report_ack("TASK_ERROR", payload, True)
            if status == "conflict":
                return self._report_ack("TASK_ERROR", payload, False, "REPORT_ID_CONFLICT")
            if phase == "prepare" and self._task_ready_cancelled:
                return self._report_ack("TASK_ERROR", payload, False, "BARRIER_CANCELLED")
            if phase == "round" and self._round_cancelled:
                return self._report_ack("TASK_ERROR", payload, False, "BARRIER_CANCELLED")
            if current_error is not None:
                return self._report_ack("TASK_ERROR", payload, False, "BARRIER_FAILED")
            record = {
                "report_id": payload["report_id"],
                "job_id": payload["job_id"],
                "node_id": payload["node_id"],
                "phase": payload["phase"],
                "round_index": payload.get("round_index"),
                "error_code": payload["error_code"],
                "reason": payload["reason"],
                "source": client_addr,
            }
            seen_map[payload["node_id"]] = {
                "type": "TASK_ERROR",
                "payload": deepcopy(payload),
                "report_id": payload["report_id"],
                "fingerprint": self._report_fingerprint(payload),
                "source_host": self._report_source_host(client_addr),
            }
            if phase == "prepare":
                self._task_ready_error = record
                self._task_ready_event.set()
            else:
                self._round_error = record
                self._round_event.set()
            handlers = list(self._task_error_handlers)

        # 守护进程错误处理器负责排队；控制面不会在自身锁内调用它。
        for handler in handlers:
            try:
                handler(deepcopy(record))
            except Exception as exc:
                logger.warning("任务错误 handler 执行异常: %s", exc)
        return self._report_ack("TASK_ERROR", payload, True)

    def handle_datagram(
        self, data: bytes, client_addr: Tuple[str, int]
    ) -> Optional[List[bytes]]:
        """
        处理控制面数据报并生成响应。
        处理任务报告、射频确认和普通心跳。
        """
        if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_CONTROL_DATAGRAM_BYTES:
            return None
        try:
            payload = json.loads(bytes(data).decode("utf-8"))
        except Exception as exc:
            logger.debug("无法解析 JSON 数据报: %s", exc)
            return None

        if not isinstance(payload, dict):
            return None
        msg_type = payload.get("type")
        if msg_type == "TASK_READY":
            return self._validate_task_report(payload, client_addr)
        if msg_type == "ROUND_READY":
            return self._validate_round_report(payload, client_addr)
        if msg_type == "TASK_ERROR":
            return self._validate_error_report(payload, client_addr)
        if msg_type == "JOB_RECOVERY":
            return self._validate_recovery_report(payload, client_addr)

        if msg_type in ("RADIO_SWITCH_FINALIZED_ACK", "RADIO_SWITCH_CONFIRMED_ACK",
                        "RADIO_SWITCH_ROLLBACK_ACK"):
            phase = {"RADIO_SWITCH_FINALIZED_ACK": "FINALIZED",
                     "RADIO_SWITCH_CONFIRMED_ACK": "CONFIRMED",
                     "RADIO_SWITCH_ROLLBACK_ACK": "ROLLBACK"}[msg_type]
            node_id = payload.get("node_id")
            with self._lock:
                if (
                    self._active_switch_session is not None
                    and payload.get("session_id") == self._active_switch_session
                    and payload.get("job_id") == self._target_switch_job_id
                    and self._switch_phase == phase
                    and type(node_id) is int
                    and node_id in self._expected_target_nodes
                    and type(payload.get("channel")) is int
                    and payload["channel"] == self._target_switch_channel
                    and payload.get("config") == self._target_switch_config
                    and payload.get("calibration") is self._target_switch_calibration
                ):
                    acks = {"FINALIZED": self._finalized_acks,
                            "CONFIRMED": self._confirmed_acks,
                            "ROLLBACK": self._rollback_acks}[phase]
                    acks.add(node_id)
                    if self._expected_target_nodes.issubset(acks):
                        self._barrier_event.set()
            return None

        if msg_type == "PREPARE_ACK":
            ack = PrepareAck.from_dict(payload)
            with self._lock:
                if (self._active_switch_session == ack.session_id
                    and ack.job_id == self._target_switch_job_id
                    and self._switch_phase == "PREPARE"
                    and ack.node_id in self._expected_target_nodes):
                    self._prepare_acks[ack.node_id] = ack
                    if self._expected_target_nodes and self._expected_target_nodes.issubset(
                        self._prepare_acks.keys()
                    ):
                        self._prepare_event.set()
            return [HeartbeatAck(ack=True).to_bytes()]

        if msg_type == "COMMIT_SUCCESS":
            cs = CommitSuccess.from_dict(payload)
            with self._lock:
                if (
                    self._active_switch_session == cs.session_id
                    and cs.job_id == self._target_switch_job_id
                    and self._switch_phase == "COMMIT"
                    and cs.node_id in self._expected_target_nodes
                    and self._target_switch_channel is not None
                    and cs.current_channel == self._target_switch_channel
                    and cs.config == self._target_switch_config
                    and cs.calibration is self._target_switch_calibration
                ):
                    self._commit_successes[cs.node_id] = cs
                    if self._expected_target_nodes and self._expected_target_nodes.issubset(
                        self._commit_successes.keys()
                    ):
                        self._commit_event.set()
            return [HeartbeatAck(ack=True).to_bytes()]

        # Standard NodeHeartbeat
        hb = NodeHeartbeat.from_dict(payload)
        with self._lock:
            ack_res, align_patch = self.registry.update_heartbeat(
                heartbeat=hb, client_addr=client_addr,
                server_radio=self.active_radio_config,
            )
            self._liveness_condition.notify_all()
            if self.radio_error or (
                self._active_switch_session is not None and hb.node_id in self._expected_target_nodes
            ):
                align_patch = None

            if (not self.radio_error and self._active_switch_session is None
                and hb.calibration != self.calibration):
                align_patch = {k: v for k, v in self.active_radio_config.to_dict().items()
                               if k in ("radio_txpower_dbm", "uplink_mcs", "downlink_mcs",
                                        "bandwidth_mhz", "uftp_rate_kbps")}

        record = self.registry.get_node(hb.node_id)
        if self.on_heartbeat_received is not None and record is not None:
            try:
                self.on_heartbeat_received(hb, record)
            except Exception as exc:
                logger.warning("心跳处理回调执行异常: %s", exc)

        replies = [ack_res.to_bytes()]
        if align_patch:
            align_msg = {
                "type": "CONFIG_RADIO_ALIGN",
                "patch": align_patch,
                "calibration": self.calibration,
            }
            replies.append(json.dumps(align_msg).encode("utf-8"))
        return replies

    def reconfigure_radio(
        self,
        patch: Dict[str, Any],
        target_nodes: Sequence[int],
        prepare_timeout_seconds: float = PREPARE_TIMEOUT_DEFAULT_SECONDS,
        commit_delay_seconds: float = COMMIT_DELAY_DEFAULT_SECONDS,
        commit_timeout_seconds: float = COMMIT_TIMEOUT_DEFAULT_SECONDS,
        lease_timeout_seconds: float = LEASE_TIMEOUT_DEFAULT_SECONDS,
        ping_interval_seconds: float = NEW_CHANNEL_PING_INTERVAL_SECONDS,
        *,
        job_id: Optional[str] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> RadioSwitchResult:
        """通过 host 冻结的目标集合执行正常射频事务。"""
        return self._reconfigure_radio(
            patch, target_nodes, prepare_timeout_seconds,
            commit_delay_seconds, commit_timeout_seconds, lease_timeout_seconds,
            ping_interval_seconds, calibration=False, job_id=job_id,
            stop_event=stop_event,
        )

    def calibrate_radio(
        self,
        patch: Dict[str, Any],
        target_nodes: Sequence[int],
        prepare_timeout_seconds: float = PREPARE_TIMEOUT_DEFAULT_SECONDS,
        commit_delay_seconds: float = COMMIT_DELAY_DEFAULT_SECONDS,
        commit_timeout_seconds: float = COMMIT_TIMEOUT_DEFAULT_SECONDS,
        lease_timeout_seconds: float = LEASE_TIMEOUT_DEFAULT_SECONDS,
        ping_interval_seconds: float = NEW_CHANNEL_PING_INTERVAL_SECONDS,
    ) -> RadioSwitchResult:
        """专用受控 HT20 标定入口；只允许 idle 事务，不绑定作业。"""
        return self._reconfigure_radio(
            patch, target_nodes, prepare_timeout_seconds,
            commit_delay_seconds, commit_timeout_seconds, lease_timeout_seconds,
            ping_interval_seconds, calibration=True,
        )

    def _reconfigure_radio(
        self,
        patch: Dict[str, Any],
        target_nodes: Sequence[int],
        prepare_timeout_seconds: float = PREPARE_TIMEOUT_DEFAULT_SECONDS,
        commit_delay_seconds: float = COMMIT_DELAY_DEFAULT_SECONDS,
        commit_timeout_seconds: float = COMMIT_TIMEOUT_DEFAULT_SECONDS,
        lease_timeout_seconds: float = LEASE_TIMEOUT_DEFAULT_SECONDS,
        ping_interval_seconds: float = NEW_CHANNEL_PING_INTERVAL_SECONDS,
        *,
        calibration: bool = False,
        job_id: Optional[str] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> RadioSwitchResult:
        """Run a job-bound or idle radio transaction with complete rollback facts.

        ``target_nodes`` is frozen by the host. A job transaction can be
        cancelled before the irreversible commit; after that point it fails
        closed and keeps the newly applied local configuration.
        """
        with self._switch_lock:
            if not isinstance(job_id, (str, type(None))) or (job_id is not None and not job_id):
                raise FLRuntimeError("invalid_radio_job", "job_id 无效")
            if stop_event is not None and not callable(getattr(stop_event, "is_set", None)):
                raise FLRuntimeError("invalid_radio_cancel", "stop_event 必须提供 is_set()")
            if (not isinstance(target_nodes, (list, tuple, set)) or not target_nodes
                    or any(type(node_id) is not int or not 1 <= node_id <= 10
                           for node_id in target_nodes)
                    or len(set(target_nodes)) != len(target_nodes)):
                raise FLRuntimeError("invalid_radio_targets", "target_nodes 必须为 host 冻结的不重复节点集合")
            targets = set(target_nodes)
            if stop_event is not None and stop_event.is_set():
                raise FLRuntimeError("radio_reconfigure_cancelled", "射频事务在启动前被取消")
            if self.radio_error:
                raise FLRuntimeError("radio_error", "射频状态不可确认，禁止后续射频重配")
            norm_patch = normalize_radio_patch(patch)
            previous = self.active_radio_config
            previous_calibration = self.calibration
            validator = validate_radio_calibration_patch if calibration else validate_radio_patch
            updated = validator(norm_patch, base=previous)
            transaction_patch = {k: v for k, v in updated.to_dict().items()
                                 if k in ("channel", "radio_txpower_dbm", "downlink_mcs",
                                          "uplink_mcs", "bandwidth_mhz", "uftp_rate_kbps")}
            if job_id is not None and calibration:
                raise FLRuntimeError("invalid_radio_job", "标定事务不能绑定作业")
            if not targets:
                return RadioSwitchResult(
                    success=False, status="rolled_back", session_id="",
                    target_channel=updated.channel, applied_patch=norm_patch,
                    effective_config=previous, failed_phase="PREPARE",
                    error_message="目标节点集合不能为空", job_id=job_id,
                    before_config=previous, target_nodes=sorted(targets),
                )
            session_id = f"switch_{uuid.uuid4().hex}"
            liveness_lease_started = False
            if job_id is not None:
                with self._lock:
                    watch_exists = job_id in self._liveness_watches
                if watch_exists:
                    # 覆盖准备、三段可逆/不可逆屏障以及最坏回退等待。
                    transaction_budget = (
                        prepare_timeout_seconds
                        + 3 * commit_timeout_seconds
                        + lease_timeout_seconds
                        + 2 * commit_delay_seconds
                    )
                    self.begin_job_liveness_lease(
                        job_id, session_id, transaction_budget)
                    liveness_lease_started = True
            committed = False
            commit_sent = False
            phase = "PREPARE"
            missing = sorted(targets)
            rollback_targets: Set[int] = set()
            recovery_unresponsive: Optional[List[int]] = None
            with self._lock:
                self._active_switch_session = session_id
                self._target_switch_job_id = job_id
                self._target_switch_channel = updated.channel
                self._target_switch_config = updated.to_dict()
                self._target_switch_calibration = calibration
                self._expected_target_nodes = targets
                self._prepare_acks.clear()
                self._commit_successes.clear()
                self._finalized_acks.clear()
                self._confirmed_acks.clear()
                self._rollback_acks.clear()
                self._prepare_event.clear()
                self._commit_event.clear()
                self._barrier_event.clear()
                self._switch_phase = phase

            def message(kind: str, **fields: Any) -> Dict[str, Any]:
                fields.setdefault("config", updated.to_dict())
                fields.setdefault("calibration", calibration)
                return dict(type=kind, session_id=session_id,
                            job_id=job_id, target_nodes=sorted(targets), **fields)

            def check_cancelled() -> None:
                if stop_event is not None and stop_event.is_set():
                    raise _RadioSwitchCancelled()

            def wait_control_event(event: threading.Event, timeout: float) -> bool:
                deadline = time.monotonic() + timeout
                while True:
                    check_cancelled()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return event.is_set()
                    if event.wait(min(ping_interval_seconds, remaining)):
                        return True

            def wait_barrier(kind: str, acks: Set[int]) -> bool:
                nonlocal missing
                deadline = time.monotonic() + commit_timeout_seconds
                while time.monotonic() < deadline:
                    check_cancelled()
                    # FINALIZED 仍可回退；期间保持客户端租约。
                    if not committed:
                        self.broadcast_downlink(message("NEW_CHANNEL_PING", channel=updated.channel))
                    self.broadcast_downlink(message(kind, channel=updated.channel, patch=norm_patch))
                    with self._lock:
                        missing = sorted(targets - acks)
                        if not missing:
                            return True
                        self._barrier_event.clear()
                    wait_control_event(
                        self._barrier_event,
                        min(ping_interval_seconds, max(0.0, deadline - time.monotonic())),
                    )
                return False

            try:
                logger.info("射频重配 PREPARE session=%s targets=%s patch=%s", session_id, sorted(targets), norm_patch)
                self.broadcast_downlink(message("CONFIG_RADIO_PREPARE", patch=transaction_patch,
                                                timeout_seconds=prepare_timeout_seconds))
                wait_control_event(self._prepare_event, prepare_timeout_seconds)
                with self._lock:
                    rollback_targets = {
                        nid for nid, ack in self._prepare_acks.items() if ack.ok
                    }
                    missing = sorted(targets - rollback_targets)
                if missing:
                    raise TimeoutError(f"节点缺失或准备失败: {missing}")

                phase = "COMMIT"
                with self._lock:
                    self._switch_phase = phase
                # Set before sending: a send may partly succeed before raising.
                commit_sent = True
                self.broadcast_downlink(message("CONFIG_RADIO_COMMIT", patch=transaction_patch,
                    delay_ms=int(commit_delay_seconds * 1000), lease_timeout_seconds=lease_timeout_seconds))
                if commit_delay_seconds > 0:
                    if stop_event is not None:
                        if stop_event.wait(commit_delay_seconds):
                            raise _RadioSwitchCancelled()
                    else:
                        time.sleep(commit_delay_seconds)
                check_cancelled()
                if (updated.channel, updated.bandwidth_mhz) != (previous.channel, previous.bandwidth_mhz):
                    self._apply_channel_switch(updated.channel, updated.bandwidth_mhz)
                if updated.radio_txpower_dbm != previous.radio_txpower_dbm:
                    self._apply_txpower_switch(updated.radio_txpower_dbm)
                self.calibration = calibration
                if self.on_radio_applied is not None:
                    self.on_radio_applied(updated)
                self.update_radio_config(updated)
                deadline = time.monotonic() + commit_timeout_seconds
                while True:
                    check_cancelled()
                    self.broadcast_downlink(message("NEW_CHANNEL_PING", channel=updated.channel))
                    with self._lock:
                        missing = sorted(targets - self._commit_successes.keys())
                    if not missing:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"新信道上线超时: {missing}")
                    wait_control_event(
                        self._commit_event,
                        min(ping_interval_seconds, remaining),
                    )

                phase = "FINALIZED"
                with self._lock:
                    self._switch_phase = phase
                    self._barrier_event.clear()
                if not wait_barrier("RADIO_SWITCH_FINALIZED", self._finalized_acks):
                    raise TimeoutError(f"FINALIZED ACK 超时: {missing}")
                with self._lock:
                    committed = True
                    phase = "CONFIRMED"
                    self._switch_phase = phase
                    self._barrier_event.clear()
                logger.info("射频重配不可逆 commit session=%s channel=%d", session_id, updated.channel)
                if not wait_barrier("RADIO_SWITCH_CONFIRMED", self._confirmed_acks):
                    raise TimeoutError(f"CONFIRMED ACK 超时: {missing}")
                check_cancelled()
                return RadioSwitchResult(
                    success=True, status="finalized", session_id=session_id,
                    target_channel=updated.channel, applied_patch=norm_patch,
                    effective_config=updated, job_id=job_id,
                    before_config=previous, target_nodes=sorted(targets),
                    confirmed_nodes=sorted(targets),
                )
            except Exception as exc:
                logger.warning("射频重配 %s 失败 session=%s: %s", phase, session_id, exc)
                cancelled = isinstance(exc, _RadioSwitchCancelled)
                error = (
                    "射频事务被取消"
                    if cancelled else str(exc)
                )
                effective: Optional[RadioConfig] = None if committed else previous
                status: Literal["rolled_back", "radio_error"] = "radio_error" if committed else "rolled_back"
                if not committed:
                    # 提交前恢复进入事务时的完整 previous 配置，不改写为基准信道。
                    fallback = previous
                    rollback_targets = set(rollback_targets)
                    recovery_required = bool(rollback_targets)
                    with self._lock:
                        self._switch_phase = "ROLLBACK"
                        self._target_switch_channel = fallback.channel
                        self._target_switch_config = fallback.to_dict()
                        self._target_switch_calibration = previous_calibration
                        self._rollback_acks.clear()
                        self._barrier_event.clear()
                    if not commit_sent:
                        # PREPARE 已确认的节点尚未持有租约，仍需取消并等待回退确认。
                        try:
                            self.broadcast_downlink(message("CONFIG_RADIO_ABORT",
                                channel=previous.channel, config=previous.to_dict(),
                                calibration=previous_calibration, reason=error))
                        except Exception as abort_exc:
                            logger.warning("射频 prepare abort 发送失败 session=%s: %s", session_id, abort_exc)
                    # 即使某一项恢复失败，也尝试恢复其余完整 previous 字段。
                    self.calibration = previous_calibration
                    rollback_errors = []
                    for restore in (
                        lambda: self._apply_channel_switch(previous.channel, previous.bandwidth_mhz),
                        lambda: self._apply_txpower_switch(previous.radio_txpower_dbm),
                        lambda: self.on_radio_applied(previous) if self.on_radio_applied is not None else None,
                    ):
                        try:
                            restore()
                        except Exception as rollback_exc:
                            rollback_errors.append(str(rollback_exc))
                    if rollback_errors:
                        status = "radio_error"
                        effective = None
                        error += "; 硬件回退无法确认: " + "; ".join(rollback_errors)
                    else:
                        effective = previous
                        self.calibration = previous_calibration
                        self.update_radio_config(previous)
                        logger.warning("射频重配回退至 previous channel %d session=%s", previous.channel, session_id)
                    # 本机恢复不等于全网恢复；等待已进入本事务的节点回退 ACK。
                    recovery_deadline = time.monotonic() + commit_timeout_seconds
                    if commit_sent:
                        recovery_deadline += lease_timeout_seconds + commit_delay_seconds
                    recovery_missing = sorted(rollback_targets)
                    while True:
                        try:
                            self.broadcast_downlink(message("CONFIG_RADIO_ABORT",
                                channel=previous.channel, config=previous.to_dict(),
                                calibration=previous_calibration, reason=error))
                        except Exception as abort_exc:
                            logger.warning("射频 abort 发送失败 session=%s: %s", session_id, abort_exc)
                        with self._lock:
                            recovery_missing = sorted(rollback_targets - self._rollback_acks)
                            self._barrier_event.clear()
                        if not recovery_required or not recovery_missing or rollback_errors:
                            break
                        remaining = recovery_deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._barrier_event.wait(min(ping_interval_seconds, remaining))
                    if recovery_required and recovery_missing:
                        status = "radio_error"
                        effective = None
                        recovery_unresponsive = recovery_missing
                        error += f"; 全网恢复确认缺失: {recovery_missing}"
                with self._lock:
                    if committed:
                        # 不可逆提交后的全网有效配置未知，保留本机新配置并锁定错误。
                        status = "radio_error"
                        effective = None
                    if status == "radio_error":
                        self.radio_error = True
                    if phase == "PREPARE":
                        missing = sorted(targets - {
                            nid for nid, ack in self._prepare_acks.items() if ack.ok
                        })
                    elif phase == "COMMIT":
                        missing = sorted(targets - self._commit_successes.keys())
                    elif phase == "FINALIZED":
                        missing = sorted(targets - self._finalized_acks)
                    elif phase == "CONFIRMED":
                        missing = sorted(targets - self._confirmed_acks)
                    if recovery_unresponsive is not None:
                        missing = recovery_unresponsive
                return RadioSwitchResult(
                    success=False, status=status, session_id=session_id,
                    target_channel=updated.channel, applied_patch=norm_patch,
                    effective_config=effective, error_message=error,
                    failed_phase=phase, unresponsive_nodes=missing,
                    unconfirmed_nodes=missing,
                    job_id=job_id, before_config=previous,
                    target_nodes=sorted(targets),
                    confirmed_nodes=sorted(set(targets) - set(missing)),
                )
            finally:
                if liveness_lease_started and job_id is not None:
                    self.end_job_liveness_lease(job_id, session_id)
                with self._lock:
                    self._active_switch_session = None
                    self._target_switch_job_id = None
                    self._target_switch_channel = None
                    self._target_switch_config = None
                    self._target_switch_calibration = False
                    self._switch_phase = None
                    self._expected_target_nodes = set()
                    self._prepare_acks.clear()
                    self._commit_successes.clear()
                    self._finalized_acks.clear()
                    self._confirmed_acks.clear()
                    self._rollback_acks.clear()
                    self._prepare_event.clear()
                    self._commit_event.clear()
                    self._barrier_event.clear()

    def _recv_loop(self) -> None:
        """Receive incoming UDP heartbeats and reply with ACK or alignment."""
        while not self._stop_event.is_set():
            if self._server_sock is None:
                break
            try:
                data, client_addr = self._server_sock.recvfrom(MAX_CONTROL_DATAGRAM_BYTES + 1)
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                break
            if len(data) > MAX_CONTROL_DATAGRAM_BYTES:
                continue

            try:
                replies = self.handle_datagram(data, client_addr)
            except Exception as exc:
                logger.debug("处理来自 %s 的数据报异常: %s", client_addr, exc)
                continue

            if replies and self._server_sock is not None:
                for rep in replies:
                    try:
                        self._server_sock.sendto(rep, client_addr)
                    except Exception as exc:
                        logger.warning("回复信令至 %s 失败: %s", client_addr, exc)


class ControlPlaneClient:
    """
    Client-side symmetric UDP control plane agent.
    - Implements three-tier hunting ladder: cached -> 157 -> [149, 153, 165].
    - Monotonically sweeps channels, retrying 2 times (250ms each) per channel (< 2s total).
    - Transitions from HUNTING to IDLE upon ACK receipt and immediately fires IDLE heartbeat.
    - Adaptive frequencies: 500ms hunting, 5s idle, 2s active.
    - 0-delay instant trigger on state transitions.
    """

    def __init__(
        self,
        node_id: int,
        tun_ip: str,
        network_adapter: Optional[Any] = None,
        air_interface: Optional[str] = None,
        initial_channel: int = DEFAULT_BENCHMARK_CHANNEL,
        cached_channel: Optional[int] = None,
        txpower_dbm: int = DEFAULT_TXPOWER_DBM,
        uplink_mcs: int = RECOMMENDED_UPLINK_MCS,
        server_host: str = CLIENT_UPLINK_DEFAULT_ADDR,
        server_port: int = CLIENT_UPLINK_DEFAULT_PORT,
        broadcast_port: int = SERVER_CONTROL_BROADCAST_PORT,
        attempt_timeout_seconds: float = HUNTING_ATTEMPT_TIMEOUT_SECONDS,
        max_attempts_per_channel: int = HUNTING_RETRIES_PER_CHANNEL,
        idle_interval_seconds: float = IDLE_HEARTBEAT_INTERVAL_SECONDS,
        active_interval_seconds: float = ACTIVE_HEARTBEAT_INTERVAL_SECONDS,
        state_lock: Optional[Any] = None,
        bandwidth_mhz: int = 40,
        downlink_mcs: int = 3,
        uftp_rate_kbps: int = 15000,
        initial_radio_config: Optional[RadioConfig] = None,
        calibration: bool = False,
    ):
        if type(calibration) is not bool:
            raise ValueError("calibration must be a boolean")
        if calibration and initial_radio_config is None:
            raise ValueError("标定状态恢复必须提供完整 initial_radio_config")
        if initial_radio_config is not None:
            if not isinstance(initial_radio_config, RadioConfig):
                raise TypeError("initial_radio_config must be a RadioConfig")
            # 重建仅恢复调用方保存的完整状态；普通初始化仍经过正常组合门禁。
            initial_patch = {k: v for k, v in initial_radio_config.to_dict().items()
                             if k in ("channel", "radio_txpower_dbm", "downlink_mcs",
                                      "uplink_mcs", "bandwidth_mhz", "uftp_rate_kbps")}
            validator = validate_radio_calibration_patch if calibration else validate_radio_patch
            initial_radio_config = validator(initial_patch, base=RadioConfig())
            initial_channel = initial_radio_config.channel
            txpower_dbm = initial_radio_config.radio_txpower_dbm
            uplink_mcs = initial_radio_config.uplink_mcs
            bandwidth_mhz = initial_radio_config.bandwidth_mhz
            downlink_mcs = initial_radio_config.downlink_mcs
            uftp_rate_kbps = initial_radio_config.uftp_rate_kbps
        self.node_id = node_id
        self.tun_ip = tun_ip
        self.network_adapter = network_adapter
        self.air_interface = air_interface
        self.current_channel = initial_channel
        self.cached_channel = cached_channel or initial_channel
        self.locked_channel: Optional[int] = None
        self.txpower_dbm = txpower_dbm
        self.uplink_mcs = uplink_mcs
        self.bandwidth_mhz = bandwidth_mhz
        self.downlink_mcs = downlink_mcs
        self.uftp_rate_kbps = uftp_rate_kbps
        self.calibration = calibration
        self._pre_switch_calibration = False
        self._pending_switch_calibration = False
        self.server_host = server_host
        self.server_port = server_port
        self.broadcast_port = broadcast_port
        self.attempt_timeout_seconds = attempt_timeout_seconds
        self.max_attempts_per_channel = max_attempts_per_channel
        self.idle_interval_seconds = idle_interval_seconds
        self.active_interval_seconds = active_interval_seconds

        self.state = ClientNodeState.HUNTING
        self.missed_acks: int = 0
        self._state_enter_time: float = time.monotonic()
        self._last_sent_ts_ms: int = 0

        self._uplink_sock: Optional[socket.socket] = None
        self._broadcast_listener_sock: Optional[socket.socket] = None
        self._broadcast_thread: Optional[threading.Thread] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        # The daemon calls control while holding its state lock. Sharing that
        # RLock permits synchronous radio callbacks without reversing lock order.
        self._lock = state_lock if state_lock is not None else threading.RLock()
        self._broadcast_handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._bound_job_id: Optional[str] = None

        # 15s Lease Watchdog & radio switch tracking (ADR-0012, ADR-0014, Ticket 04)
        self.lease_watchdog = LeaseWatchdog(
            timeout_seconds=LEASE_TIMEOUT_DEFAULT_SECONDS,
            on_expired=self._on_lease_watchdog_expired,
            name=f"wfb-client-{node_id}-watchdog",
        )
        self.on_radio_prepare: Optional[Callable[[Optional[str], RadioConfig], None]] = None
        self.on_radio_applied: Optional[Callable[[RadioConfig], None]] = None
        self.on_radio_finalized: Optional[Callable[[RadioConfig], None]] = None
        self._pending_switch_session_id: Optional[str] = None
        self._pending_switch_job_id: Optional[str] = None
        self._pending_switch_target_nodes: Tuple[int, ...] = ()
        self._pending_switch_state: Optional[ClientNodeState] = None
        self._pending_switch_locked_channel: Optional[int] = None
        self._pending_switch_patch: Optional[Dict[str, Any]] = None
        self._pre_switch_config: Optional[RadioConfig] = None
        self._switch_applied = False
        self._finalized_proposal_session: Optional[str] = None
        self._confirmed_switch_session: Optional[str] = None
        self._confirmed_switch_channel: Optional[int] = None
        self._confirmed_switch_job_id: Optional[str] = None
        self._confirmed_switch_target_nodes: Tuple[int, ...] = ()
        self._rolled_back_switch_session: Optional[str] = None
        self._rolled_back_switch_job_id: Optional[str] = None
        self._rolled_back_switch_target_nodes: Tuple[int, ...] = ()

        # Register radio switch broadcast handlers
        self.register_broadcast_handler(
            "CONFIG_RADIO_PREPARE", self._handle_radio_prepare
        )
        self.register_broadcast_handler(
            "CONFIG_RADIO_COMMIT", self._handle_radio_commit
        )
        self.register_broadcast_handler(
            "CONFIG_RADIO_ABORT", self._handle_radio_abort
        )
        self.register_broadcast_handler(
            "NEW_CHANNEL_PING", self._handle_new_channel_ping
        )
        self.register_broadcast_handler(
            "RADIO_SWITCH_FINALIZED", self._handle_radio_switch_finalized
        )
        self.register_broadcast_handler(
            "RADIO_SWITCH_CONFIRMED", self._handle_radio_switch_confirmed
        )

    def bind_job_context(self, job_id: str) -> None:
        """绑定当前任务身份，供 job-bound 射频事务做窄身份校验。"""
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError("invalid_radio_job", "job_id 无效")
        with self._lock:
            if (self._pending_switch_session_id is not None and
                    self._pending_switch_job_id not in (None, job_id)):
                raise FLRuntimeError("radio_job_mismatch", "已有不同任务的射频事务")
            self._bound_job_id = job_id

    def clear_job_context(self, job_id: str) -> None:
        """清除当前任务身份；不引入独立 window token。"""
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError("invalid_radio_job", "job_id 无效")
        with self._lock:
            if self._bound_job_id != job_id:
                raise FLRuntimeError("radio_job_mismatch", "清除的 job_id 不是当前任务")
            if (self._pending_switch_session_id is not None and
                    self._pending_switch_job_id == job_id):
                raise FLRuntimeError("radio_switch_pending", "射频事务尚未结束")
            self._bound_job_id = None

    def _accepts_job_transaction(self, job_id: Optional[str]) -> bool:
        """未绑定节点可接受 job-bound 事务；已绑定节点只接受同一任务。"""
        if self._bound_job_id is None:
            return True
        return job_id == self._bound_job_id

    @property
    def is_running(self) -> bool:
        return self._loop_thread is not None and self._loop_thread.is_alive()

    def get_elapsed_ms(self) -> int:
        return int((time.monotonic() - self._state_enter_time) * 1000)

    def _next_timestamp_ms(self) -> int:
        """Generate strictly monotonically increasing integer timestamps."""
        with self._lock:
            now_ms = int(time.monotonic_ns() // 1_000_000)
            if now_ms <= self._last_sent_ts_ms:
                now_ms = self._last_sent_ts_ms + 1
            self._last_sent_ts_ms = now_ms
            return now_ms

    def _get_uplink_sock(self) -> socket.socket:
        with self._lock:
            if self._uplink_sock is None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(self.attempt_timeout_seconds)
                self._uplink_sock = sock
            return self._uplink_sock

    def _drain_sock(self, sock: socket.socket) -> None:
        sock.setblocking(False)
        try:
            while True:
                sock.recvfrom(4096)
        except (BlockingIOError, socket.error):
            pass
        finally:
            sock.setblocking(True)

    def _apply_channel_switch(self, channel: int, bandwidth_mhz: Optional[int] = None) -> None:
        """Switch physical interface channel via network adapter. Fail closed on error."""
        if self.network_adapter is not None and self.air_interface is not None:
            width = replace(self._current_radio_config(), channel=channel, bandwidth_mhz=(
                self.bandwidth_mhz if bandwidth_mhz is None else bandwidth_mhz
            )).channel_width
            self.network_adapter.set_channel(
                self.air_interface, channel=channel, channel_width=width
            )
        self.current_channel = channel
        if bandwidth_mhz is not None:
            self.bandwidth_mhz = bandwidth_mhz

    def _apply_txpower_switch(self, txpower_dbm: int) -> None:
        """Switch physical interface txpower via network adapter. Fail closed on error."""
        if self.network_adapter is not None and self.air_interface is not None:
            self.network_adapter.set_txpower(self.air_interface, txpower_dbm=txpower_dbm)
        self.txpower_dbm = txpower_dbm

    def apply_alignment(self, patch: Dict[str, Any], *, calibration: bool = False) -> None:
        """
        Apply incremental alignment directives for txpower and MCS.
        Channel hopping is prohibited here per project rule #89.
        """
        with self._lock:
            if self._pre_switch_config is not None or not isinstance(patch, dict) or not patch:
                return

            base = self._current_radio_config()
            # Exclude channel if mistakenly present
            safe_patch = {k: v for k, v in patch.items() if k != "channel"}
            if not safe_patch:
                return
            if type(calibration) is not bool:
                raise ValueError("calibration must be a boolean")
            validator = validate_radio_calibration_patch if calibration else validate_radio_patch
            updated = validator(safe_patch, base=base)
            previous_calibration = self.calibration

            try:
                if updated.radio_txpower_dbm != self.txpower_dbm:
                    self._apply_txpower_switch(updated.radio_txpower_dbm)
                if updated.bandwidth_mhz != self.bandwidth_mhz:
                    self._apply_channel_switch(updated.channel, updated.bandwidth_mhz)
                self._set_radio_fields(updated)
                self.calibration = calibration
                self._notify_radio_applied()
            except Exception:
                self._apply_txpower_switch(base.radio_txpower_dbm)
                self._apply_channel_switch(base.channel, base.bandwidth_mhz)
                self._set_radio_fields(base)
                self.calibration = previous_calibration
                self._notify_radio_applied()
                raise

    def hunt_once(self, ladder: Optional[List[int]] = None) -> bool:
        """
        Execute one full pass through the three-tier hunting ladder:
        Returns True if server was discovered and locked, False otherwise.
        """
        with self._lock:
            if self._pre_switch_config is not None:
                return False
        channels = ladder if ladder is not None else build_hunting_ladder(self.cached_channel)
        sock = self._get_uplink_sock()

        for ch in channels:
            if self._stop_event.is_set():
                return False

            with self._lock:
                if self._pre_switch_config is not None:
                    return False
                try:
                    replace(self._current_radio_config(), channel=ch).channel_width
                except ValueError:
                    continue
                self._apply_channel_switch(ch)

            for attempt in range(1, self.max_attempts_per_channel + 1):
                if self._stop_event.is_set():
                    return False

                self._drain_sock(sock)
                hb = NodeHeartbeat(
                    node_id=self.node_id,
                    state=ClientNodeState.HUNTING.value,
                    elapsed_ms=self.get_elapsed_ms(),
                    current_channel=ch,
                    txpower_dbm=self.txpower_dbm,
                    uplink_mcs=self.uplink_mcs,
                    bandwidth_mhz=self.bandwidth_mhz,
                    downlink_mcs=self.downlink_mcs,
                    uftp_rate_kbps=self.uftp_rate_kbps,
                    calibration=self.calibration,
                    timestamp_ms=self._next_timestamp_ms(),
                )

                try:
                    sock.sendto(hb.to_bytes(), (self.server_host, self.server_port))
                except Exception as exc:
                    logger.debug("寻频发送异常 (信道=%d): %s", ch, exc)
                    continue

                try:
                    sock.settimeout(self.attempt_timeout_seconds)
                    resp_data, _ = sock.recvfrom(4096)
                    ack = HeartbeatAck.from_bytes(resp_data)
                    if ack.ack:
                        with self._lock:
                            if self._pre_switch_config is not None:
                                return False
                            # Success! Lock channel and cache it
                            self.locked_channel = ch
                            self.cached_channel = ch
                            self.missed_acks = 0

                            # Also drain/check for subsequent CONFIG_RADIO_ALIGN packet
                            self._check_and_apply_alignment_packet(sock)

                            # Transition to IDLE
                            self.state = ClientNodeState.IDLE
                            self._state_enter_time = time.monotonic()

                            # 即刻抢跑 (0 delay) send IDLE heartbeat to close two-army loop
                            self._send_heartbeat(ClientNodeState.IDLE.value, elapsed_ms=0)
                            return True
                except (socket.timeout, BlockingIOError):
                    continue
                except Exception as exc:
                    logger.debug("寻频接收异常 (信道=%d): %s", ch, exc)
                    continue

        return False

    def _check_and_apply_alignment_packet(self, sock: socket.socket) -> None:
        """Check if an alignment directive was sent as a separate UDP packet."""
        try:
            sock.settimeout(0.02)
            extra_data, _ = sock.recvfrom(4096)
        except (socket.timeout, BlockingIOError):
            return
        except OSError:
            return

        try:
            msg = json.loads(extra_data.decode("utf-8"))
            if msg.get("type") == "CONFIG_RADIO_ALIGN" and "patch" in msg:
                self.apply_alignment(msg["patch"], calibration=msg.get("calibration", False))
        except Exception as exc:
            logger.warning("解析或应用对齐指令失败: %s", exc)

    def _send_heartbeat(self, state: str, elapsed_ms: int) -> Optional[HeartbeatAck]:
        """Internal helper to send a heartbeat and optionally wait for ACK."""
        sock = self._get_uplink_sock()
        self._drain_sock(sock)

        hb = NodeHeartbeat(
            node_id=self.node_id,
            state=state,
            elapsed_ms=elapsed_ms,
            current_channel=self.current_channel,
            txpower_dbm=self.txpower_dbm,
            uplink_mcs=self.uplink_mcs,
            bandwidth_mhz=self.bandwidth_mhz,
            downlink_mcs=self.downlink_mcs,
            uftp_rate_kbps=self.uftp_rate_kbps,
            calibration=self.calibration,
            timestamp_ms=self._next_timestamp_ms(),
        )

        try:
            sock.sendto(hb.to_bytes(), (self.server_host, self.server_port))
        except Exception as exc:
            logger.debug("发送心跳失败: %s", exc)
            return None

        try:
            sock.settimeout(self.attempt_timeout_seconds)
            resp_data, _ = sock.recvfrom(4096)
            ack = HeartbeatAck.from_bytes(resp_data)
            if ack.ack:
                self.missed_acks = 0
                self._check_and_apply_alignment_packet(sock)
                return ack
        except (socket.timeout, BlockingIOError):
            pass
        except Exception as exc:
            logger.debug("接收心跳 ACK 失败: %s", exc)

        return None

    def send_heartbeat_once(self, timeout: Optional[float] = None) -> Optional[HeartbeatAck]:
        """Send a single heartbeat in the current state and process the response."""
        with self._lock:
            old_timeout = self.attempt_timeout_seconds
            if timeout is not None:
                self.attempt_timeout_seconds = timeout

            try:
                ack = self._send_heartbeat(
                    state=self.state.value,
                    elapsed_ms=self.get_elapsed_ms(),
                )
                if ack is None:
                    if self.state == ClientNodeState.IDLE and self._pre_switch_config is None:
                        self.missed_acks += 1
                        if self.missed_acks >= IDLE_MISSED_ACK_THRESHOLD:
                            logger.warning(
                                "连续 %d 次未收到 ACK，判定失联，退回寻频自愈！",
                                self.missed_acks,
                            )
                            self.state = ClientNodeState.HUNTING
                            self._state_enter_time = time.monotonic()
                return ack
            finally:
                self.attempt_timeout_seconds = old_timeout

    def notify_state_change(self, new_state: ClientNodeState) -> None:
        """
        Transition client state machine and execute 0-delay instant heartbeat dispatch.
        """
        with self._lock:
            if self.state == new_state:
                return
            logger.info("客户端状态跃迁: %s -> %s (0 延迟即刻抢跑)", self.state.value, new_state.value)
            self.state = new_state
            self._state_enter_time = time.monotonic()

            # 0-delay instant send
            self._send_heartbeat(new_state.value, elapsed_ms=0)
            self._wake_event.set()

    def _notify_report(
        self,
        payload: Dict[str, Any],
        ack_type: str,
        timeout: float,
        timeout_code: str,
        timeout_message: str,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise FLRuntimeError('invalid_report_timeout', '报告 ACK 超时参数无效')
        try:
            message = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise FLRuntimeError('invalid_task_report', '报告内容不可序列化') from exc
        if len(message) > MAX_CONTROL_DATAGRAM_BYTES:
            raise FLRuntimeError('control_report_too_large', '控制报告超过 4096 字节')

        deadline = time.monotonic() + float(timeout)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    sock.sendto(message, (self.server_host, self.server_port))
                except OSError:
                    time.sleep(min(REPORT_RETRY_INTERVAL_SECONDS, remaining))
                    continue

                # 一次重试间隔同时作为单次 ACK 等待窗口。无关的心跳 ACK
                # 会被忽略，不能确认当前报告。
                attempt_deadline = min(
                    deadline, time.monotonic() + REPORT_RETRY_INTERVAL_SECONDS
                )
                while time.monotonic() < attempt_deadline:
                    try:
                        sock.settimeout(max(0.001, attempt_deadline - time.monotonic()))
                        data, _ = sock.recvfrom(MAX_CONTROL_DATAGRAM_BYTES + 1)
                    except (socket.timeout, BlockingIOError, OSError):
                        break
                    if len(data) > MAX_CONTROL_DATAGRAM_BYTES:
                        continue
                    try:
                        ack = json.loads(data.decode("utf-8"))
                    except (UnicodeError, ValueError):
                        continue
                    if not isinstance(ack, dict):
                        continue
                    if (ack.get("type") != ack_type
                            or ack.get("report_id") != payload["report_id"]
                            or ack.get("job_id") != payload["job_id"]
                            or ack.get("node_id") != self.node_id
                            or type(ack.get("ok")) is not bool):
                        continue
                    if not ack["ok"]:
                        raise FLRuntimeError(
                            "task_report_rejected",
                            str(ack.get("reason") or "服务端拒绝任务报告"),
                            node_id=self.node_id,
                            details={"report_id": payload["report_id"], "ack": ack},
                        )
                    logger.info(
                        "%s report_id=%s job_id=%s node_id=%s server=%s:%s",
                        ack_type, payload["report_id"], payload["job_id"],
                        self.node_id, self.server_host, self.server_port,
                    )
                    return
        finally:
            sock.close()
        raise FLRuntimeError(timeout_code, timeout_message, node_id=self.node_id)

    def notify_task_ready(
        self, job_id: str, report: Dict[str, Any], timeout: float = 8.0
    ) -> None:
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError('invalid_task_report', 'job_id 无效')
        if not isinstance(report, dict) or not report:
            raise FLRuntimeError('invalid_task_report', 'TASK_READY.report 必须为非空 object')
        payload = {
            "type": "TASK_READY",
            "report_id": uuid.uuid4().hex,
            "job_id": job_id,
            "node_id": self.node_id,
            "report": deepcopy(report),
        }
        self._notify_report(
            payload, "TASK_READY_ACK", timeout, "task_ready_timeout",
            f"作业 {job_id} 的 TASK_READY 未在 {timeout:g} 秒内获得服务端确认",
        )

    def notify_round_complete(
        self, job_id: str, round_index: int, timeout: float = 8.0
    ) -> None:
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError('invalid_round_report', 'job_id 无效')
        if type(round_index) is not int or round_index <= 0:
            raise FLRuntimeError('invalid_round_report', 'round_index 无效')
        payload = {
            "type": "ROUND_READY",
            "report_id": uuid.uuid4().hex,
            "job_id": job_id,
            "node_id": self.node_id,
            "round_index": round_index,
        }
        self._notify_report(
            payload, "ROUND_READY_ACK", timeout, "round_ready_timeout",
            f"作业 {job_id} 第 {round_index} 轮未在 {timeout:g} 秒内获得服务端确认",
        )

    def notify_task_error(
        self,
        job_id: str,
        phase: str,
        error_code: str,
        reason: str,
        round_index: Optional[int] = None,
        timeout: float = 8.0,
    ) -> None:
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError('invalid_task_report', 'job_id 无效')
        if phase not in ("prepare", "round"):
            raise FLRuntimeError('invalid_task_report', 'phase 无效')
        if not isinstance(error_code, str) or not error_code:
            raise FLRuntimeError('invalid_task_report', 'error_code 无效')
        if not isinstance(reason, str) or not reason:
            raise FLRuntimeError('invalid_task_report', 'reason 无效')
        if phase == "prepare" and round_index is not None:
            raise FLRuntimeError('invalid_task_report', 'prepare 错误不能带 round_index')
        if phase == "round" and (type(round_index) is not int or round_index <= 0):
            raise FLRuntimeError('invalid_task_report', 'round 错误必须带有效 round_index')
        payload: Dict[str, Any] = {
            "type": "TASK_ERROR",
            "report_id": uuid.uuid4().hex,
            "job_id": job_id,
            "node_id": self.node_id,
            "phase": phase,
            "error_code": error_code,
            "reason": reason,
        }
        if round_index is not None:
            payload["round_index"] = round_index
        self._notify_report(
            payload, "TASK_ERROR_ACK", timeout, "task_error_timeout",
            f"作业 {job_id} 的 TASK_ERROR 未在 {timeout:g} 秒内获得服务端确认",
        )

    def notify_job_recovery(
        self,
        job_id: str,
        recovery_epoch: int,
        recovery_state: str,
        *,
        report_id: str,
        error_code: Optional[str] = None,
        reason: Optional[str] = None,
        timeout: float = 8.0,
    ) -> None:
        """发送调用方持久化的恢复事实；ACK 丢失时重用同一 report_id。"""
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError("invalid_recovery_report", "job_id 无效")
        if type(recovery_epoch) is not int or recovery_epoch <= 0:
            raise FLRuntimeError("invalid_recovery_report", "recovery_epoch 无效")
        if not isinstance(report_id, str) or not report_id:
            raise FLRuntimeError("invalid_recovery_report", "report_id 无效")
        if recovery_state not in ("ready", "blocked"):
            raise FLRuntimeError("invalid_recovery_report", "recovery_state 无效")
        if recovery_state == "ready":
            if error_code is not None or reason is not None:
                raise FLRuntimeError("invalid_recovery_report", "ready 报告不能包含错误字段")
            report = {
                "recovery_epoch": recovery_epoch,
                "recovery_state": recovery_state,
            }
        else:
            if (not isinstance(error_code, str) or not error_code
                    or len(error_code.encode("utf-8")) > 128
                    or not isinstance(reason, str) or not reason
                    or len(reason.encode("utf-8")) > 512):
                raise FLRuntimeError("invalid_recovery_report", "blocked 报告错误字段无效")
            report = {
                "recovery_epoch": recovery_epoch,
                "recovery_state": recovery_state,
                "error_code": error_code,
                "reason": reason,
            }

        payload = {
            "type": "JOB_RECOVERY",
            "report_id": report_id,
            "job_id": job_id,
            "node_id": self.node_id,
            "report": report,
        }
        self._notify_report(
            payload,
            "JOB_RECOVERY_ACK",
            timeout,
            "job_recovery_timeout",
            f"作业 {job_id} 恢复报告未在 {timeout:g} 秒内获得服务端确认",
        )

    def _send_unicast_datagram(self, data: bytes) -> None:
        """Helper to send unicast control datagram to server."""
        sock = self._get_uplink_sock()
        try:
            sock.sendto(data, (self.server_host, self.server_port))
        except Exception as exc:
            logger.debug("发送单播控制报文失败: %s", exc)

    @staticmethod
    def _canonical_target_nodes(value: Any) -> Tuple[int, ...]:
        if (not isinstance(value, (list, tuple)) or not value or
                any(type(node_id) is not int or not 1 <= node_id <= 10
                    for node_id in value)):
            raise ValueError("target_nodes 无效")
        if len(set(value)) != len(value):
            raise ValueError("target_nodes 不能重复")
        return tuple(sorted(value))

    def _matches_pending_switch(self, msg: Dict[str, Any]) -> bool:
        """当前事务必须同时匹配 session、job 身份和冻结目标节点。"""
        try:
            target_nodes = self._canonical_target_nodes(
                msg.get("target_nodes", [self.node_id]))
        except ValueError:
            return False
        return (
            self._pending_switch_session_id is not None
            and msg.get("session_id") == self._pending_switch_session_id
            and msg.get("job_id") == self._pending_switch_job_id
            and target_nodes == self._pending_switch_target_nodes
            and self.node_id in target_nodes
        )

    def _clear_pending_switch(self) -> None:
        self._pending_switch_session_id = None
        self._pending_switch_job_id = None
        self._pending_switch_target_nodes = ()
        self._pending_switch_state = None
        self._pending_switch_locked_channel = None
        self._pending_switch_patch = None
        self._pre_switch_config = None
        self._switch_applied = False
        self._finalized_proposal_session = None

    def _handle_radio_prepare(self, msg: Dict[str, Any]) -> None:
        session_id = msg.get("session_id")
        job_id = msg.get("job_id")
        raw_targets = msg.get("target_nodes", [self.node_id])
        if not isinstance(session_id, str) or not session_id:
            return
        if job_id is not None and (not isinstance(job_id, str) or not job_id):
            return
        try:
            target_nodes = self._canonical_target_nodes(
                msg.get("target_nodes", [self.node_id]))
        except ValueError:
            return
        if self.node_id not in target_nodes:
            return
        with self._lock:
            # PREPARE itself reserves the owner. Only the exact same proposal
            # may be acknowledged again before COMMIT; another owner is ignored.
            if self._pending_switch_session_id is not None:
                try:
                    norm_patch = normalize_radio_patch(msg.get("patch", {}))
                    calibration = msg.get("calibration", False)
                    same_proposal = (
                        session_id == self._pending_switch_session_id
                        and job_id == self._pending_switch_job_id
                        and target_nodes == self._pending_switch_target_nodes
                        and norm_patch == self._pending_switch_patch
                        and calibration is self._pending_switch_calibration
                    )
                except (TypeError, ValueError):
                    same_proposal = False
                if same_proposal:
                    self._send_unicast_datagram(PrepareAck(
                        self.node_id, session_id, ok=True, job_id=job_id,
                        timestamp_ms=self._next_timestamp_ms()).to_bytes())
                return
            try:
                norm_patch = normalize_radio_patch(msg.get("patch", {}))
                calibration = msg.get("calibration", False)
                if type(calibration) is not bool:
                    raise ValueError("calibration must be a boolean")
                validator = validate_radio_calibration_patch if calibration else validate_radio_patch
                updated = validator(norm_patch, base=self._current_radio_config())
                if self.on_radio_prepare is not None:
                    self.on_radio_prepare(job_id, updated)
                self._rolled_back_switch_session = None
                self._rolled_back_switch_target_nodes = ()
                self._pending_switch_session_id = session_id
                self._pending_switch_job_id = job_id
                self._pending_switch_target_nodes = target_nodes
                self._pending_switch_state = self.state
                self._pending_switch_locked_channel = self.locked_channel
                self._pending_switch_patch = norm_patch
                self._pending_switch_calibration = calibration
                self._finalized_proposal_session = None
                # 新事务使上一事务的重复确认收据失效。
                self._confirmed_switch_session = None
                self._confirmed_switch_channel = None
                self._confirmed_switch_job_id = None
                self._confirmed_switch_target_nodes = ()
                ack = PrepareAck(
                    self.node_id, session_id, ok=True,
                    job_id=job_id, timestamp_ms=self._next_timestamp_ms())
            except Exception as exc:
                ack = PrepareAck(
                    self.node_id, session_id, ok=False, error=str(exc),
                    job_id=job_id, timestamp_ms=self._next_timestamp_ms())
            self._send_unicast_datagram(ack.to_bytes())

    def _handle_radio_commit(self, msg: Dict[str, Any]) -> None:
        with self._lock:
            if not self._matches_pending_switch(msg) or self._pre_switch_config is not None:
                return
            session_id = self._pending_switch_session_id
            norm_patch = normalize_radio_patch(msg.get("patch") or self._pending_switch_patch or {})
            if (norm_patch != self._pending_switch_patch
                or msg.get("calibration", False) is not self._pending_switch_calibration):
                return
            self._pre_switch_config = self._current_radio_config()
            self._pre_switch_calibration = self.calibration
            delay_sec = max(0.0, int(msg.get("delay_ms", 2000)) / 1000.0)
            self._switch_applied = False
            lease_seconds = float(msg.get("lease_timeout_seconds", LEASE_TIMEOUT_DEFAULT_SECONDS))
            self.lease_watchdog.arm(lease_seconds)
            logger.info("节点 %d LEASE_ARM session=%s lease_seconds=%.3f", self.node_id, session_id, lease_seconds)
            self._wake_event.set()

        def delayed_switch() -> None:
            if delay_sec > 0:
                time.sleep(delay_sec)
            with self._lock:
                previous = self._pre_switch_config
                if (session_id != self._pending_switch_session_id
                    or not self.lease_watchdog.is_armed or previous is None):
                    return
                try:
                    validator = (validate_radio_calibration_patch if self._pending_switch_calibration
                                 else validate_radio_patch)
                    updated = validator(norm_patch, base=previous)
                    if (updated.channel, updated.bandwidth_mhz) != (self.current_channel, self.bandwidth_mhz):
                        self._apply_channel_switch(updated.channel, updated.bandwidth_mhz)
                    if "radio_txpower_dbm" in norm_patch:
                        self._apply_txpower_switch(norm_patch["radio_txpower_dbm"])
                    self._set_radio_fields(updated)
                    self.calibration = self._pending_switch_calibration
                    self._notify_radio_applied()
                    self._switch_applied = True
                    logger.info("节点 %d COMMIT session=%s channel=%d", self.node_id, session_id, self.current_channel)
                except Exception as exc:
                    # 物理应用未完成时保留租约，以便自动恢复。
                    logger.error("节点 %d COMMIT 硬件应用失败: %s", self.node_id, exc)
                    self._pending_switch_patch = None

        threading.Thread(target=delayed_switch, name=f"wfb-client-{self.node_id}-switch", daemon=True).start()

    def _handle_new_channel_ping(self, msg: Dict[str, Any]) -> None:
        with self._lock:
            if (not self._matches_pending_switch(msg)
                or not self._matches_current_radio(msg)
                or self._pending_switch_patch is None
                or not self._switch_applied
                or not self.lease_watchdog.refresh()):
                return
            success = CommitSuccess(
                node_id=self.node_id, session_id=self._pending_switch_session_id or "",
                current_channel=self.current_channel, timestamp_ms=self._next_timestamp_ms(),
                config=self._current_radio_config().to_dict(), calibration=self.calibration,
                job_id=self._pending_switch_job_id,
            )
            self._send_unicast_datagram(success.to_bytes())

    def _matches_current_radio(self, msg: Dict[str, Any]) -> bool:
        """完整配置一致才允许续租或确认，防止同信道错配被接受。"""
        return (msg.get("channel") == self.current_channel
                and msg.get("config") == self._current_radio_config().to_dict()
                and msg.get("calibration") is self.calibration)

    def _send_final_barrier_ack(
        self, kind: str, session_id: str, job_id: Optional[str] = None
    ) -> None:
        if job_id is None:
            job_id = self._pending_switch_job_id
        self._send_unicast_datagram(json.dumps({
            "type": kind, "session_id": session_id, "job_id": job_id,
            "node_id": self.node_id,
            "channel": self.current_channel,
            "config": self._current_radio_config().to_dict(),
            "calibration": self.calibration,
        }).encode("utf-8"))

    def _handle_radio_switch_finalized(self, msg: Dict[str, Any]) -> None:
        with self._lock:
            if (not self._matches_pending_switch(msg)
                or not self._matches_current_radio(msg)
                or self._pending_switch_patch is None
                or not self._switch_applied
                or not self.lease_watchdog.refresh()):
                return
            session_id = self._pending_switch_session_id or ""
            self._finalized_proposal_session = session_id
            self._send_final_barrier_ack("RADIO_SWITCH_FINALIZED_ACK", session_id)

    def _handle_radio_switch_confirmed(self, msg: Dict[str, Any]) -> None:
        with self._lock:
            session_id = msg.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                return
            try:
                target_nodes = self._canonical_target_nodes(
                    msg.get("target_nodes", [self.node_id]))
            except ValueError:
                return
            if (self.node_id not in target_nodes
                or not self._matches_current_radio(msg)):
                return
            if (session_id == self._confirmed_switch_session
                and msg.get("job_id") == self._confirmed_switch_job_id
                and target_nodes == self._confirmed_switch_target_nodes
                and self.current_channel == self._confirmed_switch_channel):
                self._send_final_barrier_ack(
                    "RADIO_SWITCH_CONFIRMED_ACK", session_id,
                    self._confirmed_switch_job_id)
                return
            if (not self._matches_pending_switch(msg)
                or self._finalized_proposal_session != session_id
                or not self.lease_watchdog.disarm_if_alive()):
                return
            self.locked_channel = self.current_channel
            self.cached_channel = self.current_channel
            confirmed_job_id = self._pending_switch_job_id
            confirmed_target_nodes = self._pending_switch_target_nodes
            task_owned = (
                confirmed_job_id is not None
                and self._bound_job_id == confirmed_job_id
            )
            previous_state = self._pending_switch_state or ClientNodeState.IDLE
            previous_locked_channel = self._pending_switch_locked_channel
            self._confirmed_switch_session = session_id
            self._confirmed_switch_channel = self.current_channel
            self._confirmed_switch_job_id = confirmed_job_id
            self._confirmed_switch_target_nodes = confirmed_target_nodes
            self._clear_pending_switch()
            if task_owned:
                self.state = previous_state
                self.locked_channel = (
                    previous_locked_channel
                    if previous_locked_channel is not None
                    else (self.current_channel if previous_state == ClientNodeState.IDLE else None)
                )
            else:
                self.state = ClientNodeState.IDLE
                self.locked_channel = self.current_channel
            self._state_enter_time = time.monotonic()
            new_config = self._current_radio_config()
            self._send_final_barrier_ack(
                "RADIO_SWITCH_CONFIRMED_ACK", session_id, confirmed_job_id)
            self._wake_event.set()
            logger.info("节点 %d CONFIRMED session=%s channel=%d", self.node_id, session_id, self.current_channel)
        if self.on_radio_finalized is not None:
            try:
                self.on_radio_finalized(new_config)
            except Exception as exc:
                logger.warning("on_radio_finalized 执行失败: %s", exc)

    @property
    def radio_switch_pending(self) -> bool:
        with self._lock:
            return self._pending_switch_session_id is not None

    def _current_radio_config(self) -> RadioConfig:
        """读取当前完整逻辑配置；标定状态单独保存在控制协议中。"""
        return RadioConfig(channel=self.current_channel, radio_txpower_dbm=self.txpower_dbm,
                           downlink_mcs=self.downlink_mcs, uplink_mcs=self.uplink_mcs,
                           bandwidth_mhz=self.bandwidth_mhz, uftp_rate_kbps=self.uftp_rate_kbps)

    def _set_radio_fields(self, config: RadioConfig) -> None:
        """同步完整逻辑配置，避免回调和租约恢复丢失带宽或下行参数。"""
        self.bandwidth_mhz = config.bandwidth_mhz
        self.downlink_mcs = config.downlink_mcs
        self.uplink_mcs = config.uplink_mcs
        self.uftp_rate_kbps = config.uftp_rate_kbps

    def _notify_radio_applied(self) -> None:
        """确认成功前同步应用进程参数。

        调用时持有状态锁，所属守护进程须共享此锁，避免回调与任务切换竞争或锁序反转。
        应用异常时保留旧配置快照，供租约恢复使用。
        """
        if self.on_radio_applied is not None:
            self.on_radio_applied(self._current_radio_config())

    def _rollback_switch(self) -> None:
        """恢复事务开始时的完整客户端配置，不隐式回退到基准信道。"""
        # 恢复尚未完成时，不得确认已放弃的目标配置。
        self._switch_applied = False
        self._finalized_proposal_session = None
        session_id = self._pending_switch_session_id
        rollback_job_id = self._pending_switch_job_id
        rollback_target_nodes = self._pending_switch_target_nodes
        task_owned = (
            rollback_job_id is not None
            and self._bound_job_id == rollback_job_id
        )
        previous_state = self._pending_switch_state or ClientNodeState.HUNTING
        previous_locked_channel = self._pending_switch_locked_channel
        previous = self._pre_switch_config or self._current_radio_config()
        previous_calibration = (
            self._pre_switch_calibration if self._pre_switch_config is not None
            else self.calibration
        )
        self._apply_channel_switch(previous.channel, previous.bandwidth_mhz)
        self._apply_txpower_switch(previous.radio_txpower_dbm)
        self._set_radio_fields(previous)
        self.calibration = previous_calibration
        self._notify_radio_applied()
        self._clear_pending_switch()
        if task_owned:
            self.locked_channel = (
                previous_locked_channel
                if previous_locked_channel is not None
                else (self.current_channel if previous_state == ClientNodeState.IDLE else None)
            )
            self.state = previous_state
        else:
            self.locked_channel = None
            self.state = ClientNodeState.HUNTING
        self._state_enter_time = time.monotonic()
        self._wake_event.set()
        self._rolled_back_switch_session = session_id
        self._rolled_back_switch_job_id = rollback_job_id
        self._rolled_back_switch_target_nodes = rollback_target_nodes
        if session_id is not None:
            self._send_final_barrier_ack(
                "RADIO_SWITCH_ROLLBACK_ACK", session_id, rollback_job_id)

    def _handle_radio_abort(self, msg: Dict[str, Any]) -> None:
        with self._lock:
            try:
                target_nodes = self._canonical_target_nodes(
                    msg.get("target_nodes", [self.node_id]))
            except ValueError:
                return
            if (self._rolled_back_switch_session is not None and
                    target_nodes != self._rolled_back_switch_target_nodes):
                return
            if (self._rolled_back_switch_session is not None
                and msg.get("session_id") == self._rolled_back_switch_session
                and msg.get("job_id") == self._rolled_back_switch_job_id
                and self._matches_current_radio(msg)):
                self._send_final_barrier_ack(
                    "RADIO_SWITCH_ROLLBACK_ACK", self._rolled_back_switch_session,
                    self._rolled_back_switch_job_id)
                return
            if not self._matches_pending_switch(msg):
                return
            logger.warning("节点 %d 射频 ABORT session=%s", self.node_id, self._pending_switch_session_id)
            # 直接分发协议消息时，同样遵守物理信道隔离。
            if msg.get("channel", self.current_channel) != self.current_channel:
                return
            # 先恢复再解除租约，恢复失败时保留看门狗。
            self._rollback_switch()
            self.lease_watchdog.disarm()

    def _on_lease_watchdog_expired(self) -> None:
        with self._lock:
            # 排队中的到期回调不得撤销新租约或已通过原子截止时间检查的确认。
            if not self.lease_watchdog.is_expired or self._pending_switch_session_id is None:
                return
            previous_channel = (
                self._pre_switch_config.channel
                if self._pre_switch_config is not None
                else self.current_channel
            )
            logger.warning("节点 %d: 射频租约耗尽 session=%s，回退至 Channel %d", self.node_id,
                           self._pending_switch_session_id, previous_channel)
            # 即使首次恢复抛出异常，也唤醒现有监督循环以便重试。
            self._wake_event.set()
            self._rollback_switch()

    def register_broadcast_handler(
        self, msg_type: str, handler: Callable[[Dict[str, Any]], None]
    ) -> None:
        with self._lock:
            if msg_type not in self._broadcast_handlers:
                self._broadcast_handlers[msg_type] = []
            self._broadcast_handlers[msg_type].append(handler)

    def start_broadcast_listener(self) -> None:
        """Start background receiver for server broadcasts on port 9000."""
        with self._lock:
            if self._broadcast_thread is not None and self._broadcast_thread.is_alive():
                return

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except (AttributeError, OSError):
                    pass
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", self.broadcast_port))
            sock.settimeout(0.2)
            self._broadcast_listener_sock = sock

            self._broadcast_thread = threading.Thread(
                target=self._broadcast_loop,
                name="wfb-client-broadcast-recv",
                daemon=True,
            )
            self._broadcast_thread.start()

    def handle_broadcast(self, message: Dict[str, Any]) -> None:
        """Dispatch a downlink datagram using the physical channel filter."""
        if not isinstance(message, dict):
            return
        with self._lock:
            channel = message.get("channel")
            if channel is not None and channel != self.current_channel:
                return
            handlers = list(self._broadcast_handlers.get(message.get("type", ""), []))
        # Daemon callbacks acquire their own lock and may call back into control.
        # Only snapshot dispatch under this lock to avoid reversing lock order.
        for handler in handlers:
            try:
                handler(message)
            except Exception as exc:
                logger.error("处理广播指令 %s 失败: %s", message.get("type"), exc)

    def _broadcast_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._broadcast_listener_sock is None:
                break
            try:
                data, _ = self._broadcast_listener_sock.recvfrom(MAX_CONTROL_DATAGRAM_BYTES + 1)
                if len(data) > MAX_CONTROL_DATAGRAM_BYTES:
                    continue
                msg = json.loads(data.decode("utf-8"))
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                break
            except Exception as exc:
                logger.debug("解析广播信令异常: %s", exc)
                continue
            self.handle_broadcast(msg)

    def start(self) -> None:
        """Start the supervisory hunting and adaptive heartbeat loop."""
        with self._lock:
            if self._loop_thread is not None and self._loop_thread.is_alive():
                return

            self._stop_event.clear()
            self.start_broadcast_listener()

            self._loop_thread = threading.Thread(
                target=self._supervisory_loop,
                name="wfb-client-control-loop",
                daemon=True,
            )
            self._loop_thread.start()

    def stop(self) -> None:
        """Stop client control plane and release all sockets."""
        self.lease_watchdog.disarm()
        self._stop_event.set()
        self._wake_event.set()

        if self._loop_thread is not None and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=1.0)
            self._loop_thread = None

        if self._broadcast_thread is not None and self._broadcast_thread.is_alive():
            self._broadcast_thread.join(timeout=1.0)
            self._broadcast_thread = None

        with self._lock:
            if self._uplink_sock is not None:
                try:
                    self._uplink_sock.close()
                except Exception:
                    pass
                self._uplink_sock = None

            if self._broadcast_listener_sock is not None:
                try:
                    self._broadcast_listener_sock.close()
                except Exception:
                    pass
                self._broadcast_listener_sock = None

    def _supervisory_loop(self) -> None:
        """Adaptive loop handling HUNTING, IDLE, and ACTIVE heartbeat schedules."""
        while not self._stop_event.is_set():
            with self._lock:
                switching = self._pre_switch_config is not None
                if switching and self.lease_watchdog.is_expired:
                    # 到期回调可能遭遇短暂网卡故障；全部恢复成功前保留旧配置快照，
                    # 暂停寻频，并通过现有监督循环重试。
                    try:
                        self._rollback_switch()
                        switching = False
                    except Exception as exc:
                        logger.error("节点 %d 租约回退重试失败: %s", self.node_id, exc)
            if switching:
                self._wake_event.wait(timeout=0.1)
                self._wake_event.clear()
                continue
            if self.state == ClientNodeState.HUNTING:
                found = self.hunt_once()
                if not found:
                    # Brief backoff before next full hunting sweep
                    self._stop_event.wait(0.1)
                continue

            # State is IDLE or ACTIVE
            interval = (
                self.idle_interval_seconds
                if self.state == ClientNodeState.IDLE
                else self.active_interval_seconds
            )

            # Wait for interval or wake event on state change
            self._wake_event.clear()
            awakened = self._wake_event.wait(timeout=interval)

            if self._stop_event.is_set():
                break

            if awakened:
                # State change was already dispatched with 0 delay by notify_state_change
                continue

            # Periodic keepalive/progress heartbeat
            self.send_heartbeat_once()
