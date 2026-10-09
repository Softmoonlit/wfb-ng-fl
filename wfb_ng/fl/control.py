"""
Symmetric UDP control plane, three-tier hunting ladder, anti-desync state machine,
and NodeHorizon registry for WFB-ng Stage 2 (ADR-0012, ADR-0014, Ticket 03).
"""

import json
import logging
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
    validate_radio_config,
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

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "type": "NODE_HEARTBEAT",
            "node_id": self.node_id,
            "state": self.state,
            "elapsed_ms": self.elapsed_ms,
            "current_channel": self.current_channel,
            "txpower_dbm": self.txpower_dbm,
            "uplink_mcs": self.uplink_mcs,
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
        validate_radio_config({
            "channel": data["current_channel"],
            "radio_txpower_dbm": data["txpower_dbm"],
            "uplink_mcs": data["uplink_mcs"],
        })

        return cls(
            node_id=data["node_id"],
            state=state,
            elapsed_ms=data["elapsed_ms"],
            current_channel=data["current_channel"],
            txpower_dbm=data["txpower_dbm"],
            uplink_mcs=data["uplink_mcs"],
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
    """Uplink acknowledgement for CONFIG_RADIO_PREPARE sent by clients to 10.80.0.1:9001."""
    node_id: int
    session_id: str
    ok: bool = True
    error: Optional[str] = None
    timestamp_ms: Optional[int] = None

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
        return cls(
            node_id=data["node_id"],
            session_id=session_id,
            ok=data["ok"],
            error=data.get("error"),
            timestamp_ms=data.get("timestamp_ms"),
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

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "type": "COMMIT_SUCCESS",
            "node_id": self.node_id,
            "session_id": self.session_id,
            "current_channel": self.current_channel,
        }
        if self.timestamp_ms is not None:
            res["timestamp_ms"] = self.timestamp_ms
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
        return cls(
            node_id=data["node_id"],
            session_id=data["session_id"],
            current_channel=data["current_channel"],
            timestamp_ms=data.get("timestamp_ms"),
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

    def __bool__(self) -> bool:
        return self.success


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

            if align_patch:
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
        self._target_switch_channel: Optional[int] = None
        self._prepare_acks: Dict[int, PrepareAck] = {}
        self._commit_successes: Dict[int, CommitSuccess] = {}
        self._prepare_event = threading.Event()
        self._commit_event = threading.Event()
        self._expected_target_nodes: Set[int] = set()
        self._switch_phase: Optional[str] = None
        self._finalized_acks: Set[int] = set()
        self._confirmed_acks: Set[int] = set()
        self._barrier_event = threading.Event()

        # Job-scoped client RoleService readiness synchronization
        self._task_ready_job_id: Optional[str] = None
        self._task_ready_expected_nodes: Set[int] = set()
        self._task_ready_nodes: Set[int] = set()
        self._task_ready_event = threading.Event()

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

    def stop(self) -> None:
        """Stop background worker and close sockets."""
        self._stop_event.set()
        if self._recv_thread is not None and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=1.0)
            self._recv_thread = None

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

    def _apply_channel_switch(self, channel: int) -> None:
        """Switch server physical interface channel via network adapter. Fail closed on error."""
        if self.network_adapter is not None and self.air_interface is not None:
            width = "HT20" if channel == 165 else "HT40+"
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

    def prepare_task_readiness(self, job_id: str, target_nodes: Sequence[int]) -> None:
        with self._lock:
            self._task_ready_job_id = job_id
            self._task_ready_expected_nodes = set(target_nodes)
            self._task_ready_nodes.clear()
            self._task_ready_event.clear()

    def wait_for_task_readiness(self, timeout: float) -> bool:
        return self._task_ready_event.wait(timeout)

    def clear_task_readiness(self) -> None:
        with self._lock:
            self._task_ready_job_id = None
            self._task_ready_expected_nodes.clear()
            self._task_ready_nodes.clear()
            self._task_ready_event.clear()

    def broadcast_downlink(self, message: Dict[str, Any]) -> None:
        """Broadcast control message to 255.255.255.255:9000."""
        with self._lock:
            if self._broadcast_sock is None:
                raise RuntimeError("Server broadcast socket is not initialized")
            data = json.dumps(message).encode("utf-8")
            self._broadcast_sock.sendto(data, (self.broadcast_addr, self.broadcast_port))

    def handle_datagram(
        self, data: bytes, client_addr: Tuple[str, int]
    ) -> Optional[List[bytes]]:
        """
        Process incoming datagram and produce outbound replies.
        Handles PREPARE_ACK, COMMIT_SUCCESS, and standard NODE_HEARTBEAT.
        """
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception as exc:
            logger.debug("无法解析 JSON 数据报: %s", exc)
            return None

        if not isinstance(payload, dict):
            return None
        msg_type = payload.get("type")
        if msg_type in ("RADIO_SWITCH_FINALIZED_ACK", "RADIO_SWITCH_CONFIRMED_ACK"):
            phase = "FINALIZED" if msg_type == "RADIO_SWITCH_FINALIZED_ACK" else "CONFIRMED"
            node_id = payload.get("node_id")
            with self._lock:
                if (
                    self._active_switch_session is not None
                    and payload.get("session_id") == self._active_switch_session
                    and self._switch_phase == phase
                    and type(node_id) is int
                    and node_id in self._expected_target_nodes
                    and type(payload.get("channel")) is int
                    and payload["channel"] == self._target_switch_channel
                ):
                    acks = self._finalized_acks if phase == "FINALIZED" else self._confirmed_acks
                    acks.add(node_id)
                    if self._expected_target_nodes.issubset(acks):
                        self._barrier_event.set()
            return None

        if msg_type == "TASK_READY":
            job_id = payload.get("job_id")
            node_id = payload.get("node_id")
            if not isinstance(job_id, str) or type(node_id) is not int:
                return None
            with self._lock:
                if (
                    job_id == self._task_ready_job_id
                    and node_id in self._task_ready_expected_nodes
                ):
                    self._task_ready_nodes.add(node_id)
                    if self._task_ready_expected_nodes.issubset(self._task_ready_nodes):
                        self._task_ready_event.set()
            return [HeartbeatAck(ack=True).to_bytes()]

        if msg_type == "PREPARE_ACK":
            ack = PrepareAck.from_dict(payload)
            with self._lock:
                if (self._active_switch_session == ack.session_id
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
                    and self._switch_phase == "COMMIT"
                    and cs.node_id in self._expected_target_nodes
                    and self._target_switch_channel is not None
                    and cs.current_channel == self._target_switch_channel
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
            if self.radio_error or (
                self._active_switch_session is not None and hb.node_id in self._expected_target_nodes
            ):
                align_patch = None

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
            }
            replies.append(json.dumps(align_msg).encode("utf-8"))
        return replies

    def reconfigure_radio(
        self,
        patch: Dict[str, Any],
        target_nodes: Optional[Sequence[int]] = None,
        prepare_timeout_seconds: float = PREPARE_TIMEOUT_DEFAULT_SECONDS,
        commit_delay_seconds: float = COMMIT_DELAY_DEFAULT_SECONDS,
        commit_timeout_seconds: float = COMMIT_TIMEOUT_DEFAULT_SECONDS,
        lease_timeout_seconds: float = LEASE_TIMEOUT_DEFAULT_SECONDS,
        ping_interval_seconds: float = NEW_CHANNEL_PING_INTERVAL_SECONDS,
    ) -> RadioSwitchResult:
        """Run the lease-protected transaction and reliable final confirmation barriers.

        FINALIZED acknowledgements precede the irreversible commit. After that
        point a failed CONFIRMED barrier keeps the new config and fails closed.
        Each barrier uses commit_timeout_seconds; clients renew leases only while
        the transaction remains reversible.
        """
        with self._switch_lock:
            if self.radio_error:
                raise FLRuntimeError("radio_error", "射频状态不可确认，禁止后续射频重配")
            norm_patch = normalize_radio_patch(patch)
            previous = self.active_radio_config
            updated = validate_radio_patch(norm_patch, base=previous)
            targets = set(target_nodes or [
                nid for nid, record in self.registry.get_all_nodes().items()
                if record.readiness in (NodeReadiness.READY, NodeReadiness.ACTIVE)
            ])
            if not targets:
                return RadioSwitchResult(
                    success=False, status="rolled_back", session_id="",
                    target_channel=updated.channel, applied_patch=norm_patch,
                    effective_config=previous, failed_phase="PREPARE",
                    error_message="无可用在线目标节点参与射频重配",
                )
            session_id = f"switch_{uuid.uuid4().hex}"
            committed = False
            commit_sent = False
            phase = "PREPARE"
            missing = sorted(targets)
            with self._lock:
                self._active_switch_session = session_id
                self._target_switch_channel = updated.channel
                self._expected_target_nodes = targets
                self._prepare_acks.clear()
                self._commit_successes.clear()
                self._finalized_acks.clear()
                self._confirmed_acks.clear()
                self._prepare_event.clear()
                self._commit_event.clear()
                self._barrier_event.clear()
                self._switch_phase = phase

            def message(kind: str, **fields: Any) -> Dict[str, Any]:
                return dict(type=kind, session_id=session_id,
                            target_nodes=sorted(targets), **fields)

            def wait_barrier(kind: str, acks: Set[int]) -> bool:
                nonlocal missing
                deadline = time.monotonic() + commit_timeout_seconds
                while time.monotonic() < deadline:
                    # FINALIZED is still reversible: keep all clients' leases alive.
                    if not committed:
                        self.broadcast_downlink(message("NEW_CHANNEL_PING", channel=updated.channel))
                    self.broadcast_downlink(message(kind, channel=updated.channel, patch=norm_patch))
                    with self._lock:
                        missing = sorted(targets - acks)
                        if not missing:
                            return True
                        self._barrier_event.clear()
                    self._barrier_event.wait(max(0, min(ping_interval_seconds, deadline - time.monotonic())))
                return False

            try:
                logger.info("射频重配 PREPARE session=%s targets=%s patch=%s", session_id, sorted(targets), norm_patch)
                self.broadcast_downlink(message("CONFIG_RADIO_PREPARE", patch=norm_patch,
                                                timeout_seconds=prepare_timeout_seconds))
                self._prepare_event.wait(prepare_timeout_seconds)
                with self._lock:
                    missing = sorted(targets - {nid for nid, ack in self._prepare_acks.items() if ack.ok})
                if missing:
                    raise TimeoutError(f"节点缺失或准备失败: {missing}")

                phase = "COMMIT"
                with self._lock:
                    self._switch_phase = phase
                # Set before sending: a send may partly succeed before raising.
                commit_sent = True
                self.broadcast_downlink(message("CONFIG_RADIO_COMMIT", patch=norm_patch,
                    delay_ms=int(commit_delay_seconds * 1000), lease_timeout_seconds=lease_timeout_seconds))
                if commit_delay_seconds > 0:
                    time.sleep(commit_delay_seconds)
                if updated.channel != previous.channel:
                    self._apply_channel_switch(updated.channel)
                if updated.radio_txpower_dbm != previous.radio_txpower_dbm:
                    self._apply_txpower_switch(updated.radio_txpower_dbm)
                if self.on_radio_applied is not None:
                    self.on_radio_applied(updated)
                self.update_radio_config(updated)
                deadline = time.monotonic() + commit_timeout_seconds
                while True:
                    self.broadcast_downlink(message("NEW_CHANNEL_PING", channel=updated.channel))
                    with self._lock:
                        missing = sorted(targets - self._commit_successes.keys())
                    if not missing:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"新信道上线超时: {missing}")
                    self._commit_event.wait(min(ping_interval_seconds, remaining))

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
                return RadioSwitchResult(
                    success=True, status="finalized", session_id=session_id,
                    target_channel=updated.channel, applied_patch=norm_patch,
                    effective_config=updated,
                )
            except Exception as exc:
                logger.warning("射频重配 %s 失败 session=%s: %s", phase, session_id, exc)
                error = str(exc)
                effective: Optional[RadioConfig] = updated if committed else previous
                status: Literal["rolled_back", "radio_error"] = "radio_error" if committed else "rolled_back"
                if not committed:
                    fallback = validate_radio_patch({"channel": DEFAULT_BENCHMARK_CHANNEL}, base=previous)
                    if not commit_sent:
                        # Prepared clients on a nonbenchmark channel have no
                        # lease yet. Notify them before leaving that channel.
                        try:
                            self.broadcast_downlink(message("CONFIG_RADIO_ABORT",
                                channel=previous.channel, reason=error))
                        except Exception as abort_exc:
                            logger.warning("射频 prepare abort 发送失败 session=%s: %s", session_id, abort_exc)
                    # Attempt both physical restores even when one fails; do not
                    # infer hardware state from the last successfully cached config.
                    rollback_errors = []
                    for restore in (
                        lambda: self._apply_channel_switch(DEFAULT_BENCHMARK_CHANNEL),
                        lambda: self._apply_txpower_switch(previous.radio_txpower_dbm),
                        lambda: self.on_radio_applied(fallback) if self.on_radio_applied is not None else None,
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
                        effective = fallback
                        self.update_radio_config(fallback)
                        logger.warning("射频重配回退至 Channel %d session=%s", DEFAULT_BENCHMARK_CHANNEL, session_id)
                    # All reversible failures converge on the benchmark.
                    for _ in range(2):
                        try:
                            self.broadcast_downlink(message("CONFIG_RADIO_ABORT",
                                channel=DEFAULT_BENCHMARK_CHANNEL, reason=error))
                        except Exception as abort_exc:
                            logger.warning("射频 abort 发送失败 session=%s: %s", session_id, abort_exc)
                with self._lock:
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
                return RadioSwitchResult(
                    success=False, status=status, session_id=session_id,
                    target_channel=updated.channel, applied_patch=norm_patch,
                    effective_config=effective, error_message=error,
                    failed_phase=phase, unresponsive_nodes=missing,
                )
            finally:
                with self._lock:
                    self._active_switch_session = None
                    self._target_switch_channel = None
                    self._switch_phase = None
                    self._expected_target_nodes = set()
                    self._prepare_acks.clear()
                    self._commit_successes.clear()
                    self._finalized_acks.clear()
                    self._confirmed_acks.clear()
                    self._prepare_event.clear()
                    self._commit_event.clear()
                    self._barrier_event.clear()

    def _recv_loop(self) -> None:
        """Receive incoming UDP heartbeats and reply with ACK or alignment."""
        while not self._stop_event.is_set():
            if self._server_sock is None:
                break
            try:
                data, client_addr = self._server_sock.recvfrom(4096)
            except (socket.timeout, BlockingIOError):
                continue
            except OSError:
                break

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
    ):
        self.node_id = node_id
        self.tun_ip = tun_ip
        self.network_adapter = network_adapter
        self.air_interface = air_interface
        self.current_channel = initial_channel
        self.cached_channel = cached_channel or initial_channel
        self.locked_channel: Optional[int] = None
        self.txpower_dbm = txpower_dbm
        self.uplink_mcs = uplink_mcs
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

        # 15s Lease Watchdog & radio switch tracking (ADR-0012, ADR-0014, Ticket 04)
        self.lease_watchdog = LeaseWatchdog(
            timeout_seconds=LEASE_TIMEOUT_DEFAULT_SECONDS,
            on_expired=self._on_lease_watchdog_expired,
            name=f"wfb-client-{node_id}-watchdog",
        )
        self.on_radio_applied: Optional[Callable[[RadioConfig], None]] = None
        self.on_radio_finalized: Optional[Callable[[RadioConfig], None]] = None
        self._pending_switch_session_id: Optional[str] = None
        self._pending_switch_patch: Optional[Dict[str, Any]] = None
        self._pre_switch_config: Optional[RadioConfig] = None
        self._switch_applied = False
        self._finalized_proposal_session: Optional[str] = None
        self._confirmed_switch_session: Optional[str] = None
        self._confirmed_switch_channel: Optional[int] = None

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

    def _apply_channel_switch(self, channel: int) -> None:
        """Switch physical interface channel via network adapter. Fail closed on error."""
        if self.network_adapter is not None and self.air_interface is not None:
            width = "HT20" if channel == 165 else "HT40+"
            self.network_adapter.set_channel(
                self.air_interface, channel=channel, channel_width=width
            )
        self.current_channel = channel

    def _apply_txpower_switch(self, txpower_dbm: int) -> None:
        """Switch physical interface txpower via network adapter. Fail closed on error."""
        if self.network_adapter is not None and self.air_interface is not None:
            self.network_adapter.set_txpower(self.air_interface, txpower_dbm=txpower_dbm)
        self.txpower_dbm = txpower_dbm

    def apply_alignment(self, patch: Dict[str, Any]) -> None:
        """
        Apply incremental alignment directives for txpower and MCS.
        Channel hopping is prohibited here per project rule #89.
        """
        with self._lock:
            if self._pre_switch_config is not None or not isinstance(patch, dict) or not patch:
                return

            base = RadioConfig(
                channel=self.current_channel,
                radio_txpower_dbm=self.txpower_dbm,
                uplink_mcs=self.uplink_mcs,
            )
            # Exclude channel if mistakenly present
            safe_patch = {k: v for k, v in patch.items() if k != "channel"}
            if not safe_patch:
                return
            updated = validate_radio_patch(safe_patch, base=base)

            try:
                if updated.radio_txpower_dbm != self.txpower_dbm:
                    self._apply_txpower_switch(updated.radio_txpower_dbm)
                self.uplink_mcs = updated.uplink_mcs
                self._notify_radio_applied()
            except Exception:
                self._apply_txpower_switch(base.radio_txpower_dbm)
                self.uplink_mcs = base.uplink_mcs
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
                self.apply_alignment(msg["patch"])
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

    def notify_task_ready(self, job_id: str, timeout: float = 8.0) -> None:
        message = json.dumps({
            "type": "TASK_READY",
            "job_id": job_id,
            "node_id": self.node_id,
            "timestamp_ms": self._next_timestamp_ms(),
        }).encode("utf-8")
        deadline = time.monotonic() + timeout
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(min(0.25, timeout))
        try:
            while time.monotonic() < deadline:
                try:
                    sock.sendto(message, (self.server_host, self.server_port))
                    data, _ = sock.recvfrom(4096)
                    if HeartbeatAck.from_bytes(data).ack:
                        return
                except (socket.timeout, BlockingIOError, ValueError, OSError):
                    continue
        finally:
            sock.close()
        raise FLRuntimeError(
            "task_ready_timeout",
            f"作业 {job_id} 的 TASK_READY 未在 {timeout:g} 秒内获得服务端确认",
        )

    def _send_unicast_datagram(self, data: bytes) -> None:
        """Helper to send unicast control datagram to server."""
        sock = self._get_uplink_sock()
        try:
            sock.sendto(data, (self.server_host, self.server_port))
        except Exception as exc:
            logger.debug("发送单播控制报文失败: %s", exc)

    def _matches_pending_switch(self, msg: Dict[str, Any]) -> bool:
        """Call with the client lock held; absent sessions never match."""
        return (
            self._pending_switch_session_id is not None
            and msg.get("session_id") == self._pending_switch_session_id
            and self.node_id in msg.get("target_nodes", [self.node_id])
        )

    def _clear_pending_switch(self) -> None:
        self._pending_switch_session_id = None
        self._pending_switch_patch = None
        self._pre_switch_config = None
        self._switch_applied = False
        self._finalized_proposal_session = None

    def _handle_radio_prepare(self, msg: Dict[str, Any]) -> None:
        session_id = msg.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return
        if self.node_id not in msg.get("target_nodes", [self.node_id]):
            return
        with self._lock:
            # An active lease cannot be superseded by a delayed/new PREPARE.
            if self._pre_switch_config is not None:
                return
            try:
                norm_patch = normalize_radio_patch(msg.get("patch", {}))
                validate_radio_patch(norm_patch, base=RadioConfig(
                    channel=self.current_channel, radio_txpower_dbm=self.txpower_dbm,
                    uplink_mcs=self.uplink_mcs,
                ))
                self._pending_switch_session_id = session_id
                self._pending_switch_patch = norm_patch
                self._finalized_proposal_session = None
                # A new transaction invalidates the previous re-ACK receipt.
                self._confirmed_switch_session = None
                self._confirmed_switch_channel = None
                ack = PrepareAck(self.node_id, session_id, ok=True,
                                 timestamp_ms=self._next_timestamp_ms())
            except Exception as exc:
                ack = PrepareAck(self.node_id, session_id, ok=False, error=str(exc),
                                 timestamp_ms=self._next_timestamp_ms())
            self._send_unicast_datagram(ack.to_bytes())

    def _handle_radio_commit(self, msg: Dict[str, Any]) -> None:
        with self._lock:
            if not self._matches_pending_switch(msg) or self._pre_switch_config is not None:
                return
            session_id = self._pending_switch_session_id
            norm_patch = normalize_radio_patch(msg.get("patch") or self._pending_switch_patch or {})
            if norm_patch != self._pending_switch_patch:
                return
            self._pre_switch_config = RadioConfig(
                channel=self.current_channel, radio_txpower_dbm=self.txpower_dbm,
                uplink_mcs=self.uplink_mcs,
            )
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
                if session_id != self._pending_switch_session_id or not self.lease_watchdog.is_armed:
                    return
                try:
                    if "channel" in norm_patch:
                        self._apply_channel_switch(norm_patch["channel"])
                    if "radio_txpower_dbm" in norm_patch:
                        self._apply_txpower_switch(norm_patch["radio_txpower_dbm"])
                    if "uplink_mcs" in norm_patch:
                        self.uplink_mcs = norm_patch["uplink_mcs"]
                    self._notify_radio_applied()
                    self._switch_applied = True
                    logger.info("节点 %d COMMIT session=%s channel=%d", self.node_id, session_id, self.current_channel)
                except Exception as exc:
                    # Keep the lease armed so an incomplete physical apply heals.
                    logger.error("节点 %d COMMIT 硬件应用失败: %s", self.node_id, exc)
                    self._pending_switch_patch = None

        threading.Thread(target=delayed_switch, name=f"wfb-client-{self.node_id}-switch", daemon=True).start()

    def _handle_new_channel_ping(self, msg: Dict[str, Any]) -> None:
        with self._lock:
            if (not self._matches_pending_switch(msg)
                or msg.get("channel") != self.current_channel
                or self._pending_switch_patch is None
                or not self._switch_applied
                or not self.lease_watchdog.refresh()):
                return
            success = CommitSuccess(
                node_id=self.node_id, session_id=self._pending_switch_session_id or "",
                current_channel=self.current_channel, timestamp_ms=self._next_timestamp_ms(),
            )
            self._send_unicast_datagram(success.to_bytes())

    def _send_final_barrier_ack(self, kind: str, session_id: str) -> None:
        self._send_unicast_datagram(json.dumps({
            "type": kind, "session_id": session_id, "node_id": self.node_id,
            "channel": self.current_channel,
        }).encode("utf-8"))

    def _handle_radio_switch_finalized(self, msg: Dict[str, Any]) -> None:
        with self._lock:
            if (not self._matches_pending_switch(msg)
                or msg.get("channel") != self.current_channel
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
            if (self.node_id not in msg.get("target_nodes", [self.node_id])
                or msg.get("channel") != self.current_channel):
                return
            if (session_id == self._confirmed_switch_session
                and self.current_channel == self._confirmed_switch_channel):
                self._send_final_barrier_ack("RADIO_SWITCH_CONFIRMED_ACK", session_id)
                return
            if (not self._matches_pending_switch(msg)
                or self._finalized_proposal_session != session_id
                or not self.lease_watchdog.disarm_if_alive()):
                return
            self.locked_channel = self.current_channel
            self.cached_channel = self.current_channel
            self._confirmed_switch_session = session_id
            self._confirmed_switch_channel = self.current_channel
            self._clear_pending_switch()
            self.state = ClientNodeState.IDLE
            self._state_enter_time = time.monotonic()
            new_config = RadioConfig(
                channel=self.current_channel, radio_txpower_dbm=self.txpower_dbm,
                uplink_mcs=self.uplink_mcs,
            )
            self._send_final_barrier_ack("RADIO_SWITCH_CONFIRMED_ACK", session_id)
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

    def _notify_radio_applied(self) -> None:
        """Apply process parameters synchronously, before acknowledging success.

        Invoked with the state lock held. An owning daemon must share this lock
        so callbacks and task handoffs cannot race or invert the lock order.
        Exceptions leave the saved configuration available for lease recovery.
        """
        if self.on_radio_applied is not None:
            self.on_radio_applied(RadioConfig(
                channel=self.current_channel, radio_txpower_dbm=self.txpower_dbm,
                uplink_mcs=self.uplink_mcs,
            ))

    def _rollback_switch(self) -> None:
        """Restore the benchmark and all pre-switch client parameters."""
        # An interrupted restore cannot acknowledge the abandoned target config.
        self._switch_applied = False
        self._finalized_proposal_session = None
        self._apply_channel_switch(DEFAULT_BENCHMARK_CHANNEL)
        if self._pre_switch_config is not None:
            self._apply_txpower_switch(self._pre_switch_config.radio_txpower_dbm)
            self.uplink_mcs = self._pre_switch_config.uplink_mcs
        self._notify_radio_applied()
        self._clear_pending_switch()
        self.locked_channel = None
        self.state = ClientNodeState.HUNTING
        self._state_enter_time = time.monotonic()
        self._wake_event.set()

    def _handle_radio_abort(self, msg: Dict[str, Any]) -> None:
        with self._lock:
            if not self._matches_pending_switch(msg):
                return
            logger.warning("节点 %d 射频 ABORT session=%s", self.node_id, self._pending_switch_session_id)
            # Physical-channel isolation also applies to direct protocol dispatch.
            if msg.get("channel", self.current_channel) != self.current_channel:
                return
            # Restore before disarming: a failed restore retains the watchdog.
            self._rollback_switch()
            self.lease_watchdog.disarm()

    def _on_lease_watchdog_expired(self) -> None:
        with self._lock:
            # A queued expiration callback cannot undo a newer lease or a
            # confirmation that won the atomic watchdog deadline check.
            if not self.lease_watchdog.is_expired or self._pending_switch_session_id is None:
                return
            logger.warning("节点 %d: 射频租约耗尽 session=%s，回退至 Channel %d", self.node_id,
                           self._pending_switch_session_id, DEFAULT_BENCHMARK_CHANNEL)
            # Wake the existing supervisor even if this first restore raises.
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
                data, _ = self._broadcast_listener_sock.recvfrom(4096)
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
                    # Expiration callbacks may fail on transient adapter errors.
                    # Preserve the saved config until every restore succeeds and
                    # retry through the existing supervisory loop while hunting
                    # remains suspended.
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
