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
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from wfb_ng.fl.radio import (
    ALLOWED_5GHZ_CHANNELS,
    DEFAULT_CHANNEL,
    DEFAULT_TXPOWER_DBM,
    FORBIDDEN_CHANNELS,
    RECOMMENDED_UPLINK_MCS,
    RadioConfig,
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
            timestamp_ms=data.get("timestamp_ms"),
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

            is_currently_active_or_ready = (
                existing_node is not None
                and existing_node.readiness in (NodeReadiness.READY, NodeReadiness.ACTIVE)
            )

            # Two-Army Gate:
            # 1. HUNTING -> CONNECTING, marks awaiting_idle_confirmation = True
            # 2. IDLE -> READY only if preceding HUNTING was acknowledged or node was already authenticated!
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

                if (self._awaiting_idle_confirmation.get(node_id, False) and ts_valid) or is_currently_active_or_ready:
                    readiness = NodeReadiness.READY
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
                return NodeRecord(
                    node_id=node.node_id,
                    tun_ip=node.tun_ip,
                    reported_state=node.reported_state,
                    readiness=NodeReadiness.OFFLINE,
                    elapsed_ms=node.elapsed_ms,
                    current_channel=node.current_channel,
                    txpower_dbm=node.txpower_dbm,
                    uplink_mcs=node.uplink_mcs,
                    error_code=node.error_code,
                    last_heartbeat_time=node.last_heartbeat_time,
                    last_heartbeat_wall_time=node.last_heartbeat_wall_time,
                    addr=node.addr,
                )
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
        bind_host: str = "0.0.0.0",
        bind_port: int = CLIENT_UPLINK_DEFAULT_PORT,
        broadcast_addr: str = SERVER_CONTROL_BROADCAST_ADDR,
        broadcast_port: int = SERVER_CONTROL_BROADCAST_PORT,
        offline_threshold_seconds: float = NODE_OFFLINE_THRESHOLD_SECONDS,
    ):
        self.active_radio_config = active_radio_config
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.broadcast_addr = broadcast_addr
        self.broadcast_port = broadcast_port
        self.registry = NodeHorizonRegistry(offline_threshold_seconds=offline_threshold_seconds)

        self._stop_event = threading.Event()
        self._recv_thread: Optional[threading.Thread] = None
        self._server_sock: Optional[socket.socket] = None
        self._broadcast_sock: Optional[socket.socket] = None
        self._lock = threading.RLock()

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

    def update_radio_config(self, config: RadioConfig) -> None:
        """Update server active radio config for alignment comparisons."""
        with self._lock:
            self.active_radio_config = config

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
        Designed as a clean seam for testing and socket operations.
        """
        hb = NodeHeartbeat.from_bytes(data)
        ack, align_patch = self.registry.update_heartbeat(
            heartbeat=hb,
            client_addr=client_addr,
            server_radio=self.active_radio_config,
        )

        replies = [ack.to_bytes()]
        if align_patch:
            align_msg = {
                "type": "CONFIG_RADIO_ALIGN",
                "patch": align_patch,
            }
            replies.append(json.dumps(align_msg).encode("utf-8"))
        return replies

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
        self._lock = threading.RLock()
        self._broadcast_handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

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
            if not isinstance(patch, dict) or not patch:
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

            if updated.radio_txpower_dbm != self.txpower_dbm:
                self._apply_txpower_switch(updated.radio_txpower_dbm)

            if updated.uplink_mcs != self.uplink_mcs:
                self.uplink_mcs = updated.uplink_mcs

    def hunt_once(self, ladder: Optional[List[int]] = None) -> bool:
        """
        Execute one full pass through the three-tier hunting ladder:
        Returns True if server was discovered and locked, False otherwise.
        """
        channels = ladder if ladder is not None else build_hunting_ladder(self.cached_channel)
        sock = self._get_uplink_sock()

        for ch in channels:
            if self._stop_event.is_set():
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
            current_channel=self.locked_channel or self.current_channel,
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
                    if self.state == ClientNodeState.IDLE:
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

            msg_type = msg.get("type")
            if msg_type and msg_type in self._broadcast_handlers:
                for handler in self._broadcast_handlers[msg_type]:
                    try:
                        handler(msg)
                    except Exception as exc:
                        logger.error("处理广播指令 %s 失败: %s", msg_type, exc)

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
