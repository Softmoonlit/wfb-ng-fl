#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
wfb_ng.fl.server_daemon: Server Persistent Daemon, Pre-allocated Slot Pool,
Local REST IPC, and Deterministic Job Preflight Gates.

Per ADR-0010, ADR-0012, ADR-0014, Ticket 05:
- Persistent systemd daemon: wfb-fl-server-daemon.
- Pre-allocated Node Slot Pool: Launches wfb_v6_uplink with --known-clients 1..10 and
  static 10.80.0.{10+n} client targets, relying on built-in 120ms/10ms scheduling defaults.
- Hardware Management: Takes over wl* physical wireless adapter, configures Monitor mode,
  txpower (12 dBm), Channel 157 (HT40+), and TUN interface with txqueuelen=5000.
- Control Plane: Symmetric UDP control plane on 9001 (client uplink) and 9000 (server broadcast),
  maintaining in-memory NodeHorizonRegistry with two-army anti-desync gate.
- Local REST IPC: Exposes http://127.0.0.1:9090 (/status, /survey, /jobs/start, /jobs/abort, /logs/stream).
- 3 Deterministic Preflight Checks on /jobs/start:
  1. Target nodes confirmed in IDLE within 10 seconds.
  2. Initial model file exists and validated (size matches).
  3. Engine is currently IDLE with no concurrent job conflict.
"""

import argparse
import hashlib
import ipaddress
import json
import logging
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qs, urlparse

from .client_daemon import (
    LinuxNetworkAdapter,
    NetworkAdapter,
)
from .coordinator import FLCoordinator, JobConfig, VALID_FL_MODES
from .transport import DEFAULT_UFTP_DATA_PORT
from .control import (
    CLIENT_UPLINK_DEFAULT_ADDR,
    CLIENT_UPLINK_DEFAULT_PORT,
    NODE_OFFLINE_THRESHOLD_SECONDS,
    SERVER_CONTROL_BROADCAST_ADDR,
    SERVER_CONTROL_BROADCAST_PORT,
    ClientNodeState,
    ControlPlaneServer,
    NodeHeartbeat,
    NodeReadiness,
    NodeRecord,
)
from .errors import FLRuntimeError
from .radio import (
    ALLOWED_5GHZ_CHANNELS,
    DEFAULT_CHANNEL,
    DEFAULT_TXPOWER_DBM,
    FIXED_BANDWIDTH,
    FORBIDDEN_CHANNELS,
    RECOMMENDED_UPLINK_MCS,
    LiveRadioSurveyBackend,
    RadioConfig,
    SpectrumSurveyReport,
    SurveyBackend,
    find_wl_interfaces,
    survey_spectrum,
    validate_radio_config,
)

logger = logging.getLogger("wfb_fl_server_daemon")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [server-daemon] %(message)s",
    )

DEFAULT_SERVER_CONFIG_PATH = "/etc/wfb-ng-fl/server.json"
DEFAULT_IPC_HOST = "127.0.0.1"
DEFAULT_IPC_PORT = 9090
DEFAULT_TUN_NAME = "fl-s"
DEFAULT_TUN_IP = "10.80.0.1"
DEFAULT_TUN_CIDR = "10.80.0.1/24"
DEFAULT_TUN_TXQUEUELEN = 5000
DEFAULT_LINK_ID = 7669206
DEFAULT_DOWNLINK_MCS = 3
DEFAULT_UPLINK_MCS = 6
ALL_KNOWN_CLIENT_IDS = tuple(range(1, 11))
VALID_FL_MODES = ("sync", "semi_async", "async")


class ServerState(str, Enum):
    INITIALIZING = "INITIALIZING"
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    ABORTING = "ABORTING"
    SWITCHING_RADIO = "SWITCHING_RADIO"
    STOPPED = "STOPPED"


def calculate_file_sha256(path: str) -> str:
    """Compute deterministic SHA-256 checksum for a local file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass(frozen=True)
class ServerDaemonConfig:
    ipc_host: str = DEFAULT_IPC_HOST
    ipc_port: int = DEFAULT_IPC_PORT
    channel: int = DEFAULT_CHANNEL
    radio_txpower_dbm: int = DEFAULT_TXPOWER_DBM
    downlink_mcs: int = DEFAULT_DOWNLINK_MCS
    uplink_mcs: int = DEFAULT_UPLINK_MCS
    tun_name: str = DEFAULT_TUN_NAME
    tun_ip: str = DEFAULT_TUN_IP
    tun_cidr: str = DEFAULT_TUN_CIDR
    tun_txqueuelen: int = DEFAULT_TUN_TXQUEUELEN
    control_bind_host: str = "0.0.0.0"
    control_bind_port: int = CLIENT_UPLINK_DEFAULT_PORT
    control_broadcast_addr: str = SERVER_CONTROL_BROADCAST_ADDR
    control_broadcast_port: int = SERVER_CONTROL_BROADCAST_PORT
    air_interface: Optional[str] = None
    link_id: int = DEFAULT_LINK_ID
    known_clients: Tuple[int, ...] = ALL_KNOWN_CLIENT_IDS
    work_dir: str = "/tmp/wfb-ng-fl/server"
    enable_link_process: bool = True
    enable_control_plane: bool = True

    def __post_init__(self) -> None:
        # Validate radio parameters fail-closed per ADR-0014
        try:
            validate_radio_config({
                "channel": self.channel,
                "radio_txpower_dbm": self.radio_txpower_dbm,
                "downlink_mcs": self.downlink_mcs,
                "uplink_mcs": self.uplink_mcs,
            })
        except Exception as exc:
            raise FLRuntimeError("invalid_radio_configuration", str(exc)) from exc

        # Validate known_clients
        if self.known_clients != ALL_KNOWN_CLIENT_IDS:
            raise FLRuntimeError(
                "invalid_known_clients",
                "必须全量预置平台支持的 1~10 号节点白名单槽位，严禁缩减槽位池",
            )

        # Validate IP addresses
        try:
            ipaddress.IPv4Address(self.tun_ip)
            ipaddress.IPv4Interface(self.tun_cidr)
        except Exception as exc:
            raise FLRuntimeError("invalid_tun_ip", f"TUN 地址配置非法: {exc}") from exc


def load_server_config(path: str = DEFAULT_SERVER_CONFIG_PATH) -> ServerDaemonConfig:
    """
    Load server daemon configuration from JSON file.
    Fails closed if custom path does not exist or JSON is invalid.
    """
    if not os.path.exists(path):
        if path != DEFAULT_SERVER_CONFIG_PATH:
            raise FLRuntimeError("config_not_found", f"指定的配置文件不存在: {path}")
        logger.info("服务端默认配置文件 %s 不存在，使用标准基准配置", path)
        return ServerDaemonConfig()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        raise FLRuntimeError(
            "invalid_server_config", f"无法解析服务端配置文件 {path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise FLRuntimeError(
            "invalid_server_config", f"服务端配置必须为 JSON object: {path}"
        )

    return ServerDaemonConfig(
        ipc_host=data.get("ipc_host", DEFAULT_IPC_HOST),
        ipc_port=int(data.get("ipc_port", DEFAULT_IPC_PORT)),
        channel=int(data.get("channel", DEFAULT_CHANNEL)),
        radio_txpower_dbm=int(data.get("radio_txpower_dbm", DEFAULT_TXPOWER_DBM)),
        downlink_mcs=int(data.get("downlink_mcs", DEFAULT_DOWNLINK_MCS)),
        uplink_mcs=int(data.get("uplink_mcs", DEFAULT_UPLINK_MCS)),
        tun_name=data.get("tun_name", DEFAULT_TUN_NAME),
        tun_ip=data.get("tun_ip", DEFAULT_TUN_IP),
        tun_cidr=data.get("tun_cidr", DEFAULT_TUN_CIDR),
        tun_txqueuelen=int(data.get("tun_txqueuelen", DEFAULT_TUN_TXQUEUELEN)),
        control_bind_host=data.get("control_bind_host", "0.0.0.0"),
        control_bind_port=int(data.get("control_bind_port", CLIENT_UPLINK_DEFAULT_PORT)),
        control_broadcast_addr=data.get("control_broadcast_addr", SERVER_CONTROL_BROADCAST_ADDR),
        control_broadcast_port=int(data.get("control_broadcast_port", SERVER_CONTROL_BROADCAST_PORT)),
        air_interface=data.get("air_interface"),
        link_id=int(data.get("link_id", DEFAULT_LINK_ID)),
        known_clients=ALL_KNOWN_CLIENT_IDS,
        work_dir=data.get("work_dir", "/tmp/wfb-ng-fl/server"),
        enable_link_process=bool(data.get("enable_link_process", True)),
        enable_control_plane=bool(data.get("enable_control_plane", True)),
    )


def build_v6_uplink_server_command(
    executable_path: str,
    tun_name: str,
    tun_addr: str,
    air_interface: str,
    channel: int = DEFAULT_CHANNEL,
    downlink_mcs: int = DEFAULT_DOWNLINK_MCS,
    link_id: int = DEFAULT_LINK_ID,
    known_clients: Sequence[int] = ALL_KNOWN_CLIENT_IDS,
    epoch: Optional[int] = None,
) -> List[str]:
    """
    Construct command line arguments for wfb_v6_uplink running as server.
    Per ADR-0012, ADR-0014, and Ticket 05:
    - Pre-allocates all 1..10 known clients (--known-clients 1,2,3,4,5,6,7,8,9,10)
    - Pre-allocates all client-target mappings (--client-target nid:10.80.0.{10+nid}:127.0.0.1:1)
    - OMIT --grant-duration-ms and --guard-interval-ms to rely on built-in 120ms/10ms defaults.
    """
    bandwidth = "20" if channel == 165 else "40"
    cmd = [
        executable_path,
        "--role", "server",
        "--tun-name", tun_name,
        "--tun-addr", tun_addr,
        "--node-id", "255",
        "--link-id", str(link_id),
        "--uplink-stream", "1",
        "--downlink-stream", "2",
        "--fec-k", "8",
        "--fec-n", "14",
        "--radio-bandwidth", bandwidth,
        "--radio-mcs-index", str(downlink_mcs),
        "--radio-short-gi",
        "--air-interface", air_interface,
        "--known-clients", ",".join(str(n) for n in known_clients),
    ]

    for nid in known_clients:
        client_tun_ip = f"10.80.0.{10 + nid}"
        cmd.extend(["--client-target", f"{nid}:{client_tun_ip}:127.0.0.1:1"])

    cmd.extend([
        "--downlink-pause-threshold-bytes", "131072",
        "--downlink-resume-threshold-bytes", "65536",
        "--downlink-queue-packets-limit", "64",
    ])

    if epoch is not None:
        cmd.extend(["--epoch", str(epoch)])

    return cmd


class ServerEventBus:
    """Thread-safe publish/subscribe event bus for Server-Sent Events (SSE)."""

    def __init__(self, maxsize: int = 100):
        self._subscribers: List[queue.Queue[Dict[str, Any]]] = []
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def subscribe(self) -> queue.Queue[Dict[str, Any]]:
        q: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[Dict[str, Any]]) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event: Dict[str, Any]) -> None:
        with self._lock:
            for q in list(self._subscribers):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    # Drop oldest to avoid blocking fast stream producers
                    try:
                        q.get_nowait()
                        q.put_nowait(event)
                    except (queue.Empty, queue.Full):
                        pass


class ServerIPCRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for local loopback REST IPC (127.0.0.1:9090)."""

    # Quiet default BaseHTTPRequestHandler access log to keep test output clean
    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("[REST] %s - - [%s] %s", self.client_address[0], self.log_date_time_string(), format % args)

    def _send_json_response(self, status_code: int, data: Dict[str, Any]) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        daemon: ServerDaemon = self.server.daemon  # type: ignore

        if path == "/api/v1/status":
            self._handle_status(daemon)
        elif path == "/api/v1/logs/stream":
            self._handle_logs_stream(daemon)
        else:
            self._send_json_response(404, {"error": "not_found", "message": f"未知路径: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        daemon: ServerDaemon = self.server.daemon  # type: ignore

        content_length = int(self.headers.get("Content-Length", 0))
        body: Dict[str, Any] = {}
        if content_length > 0:
            raw_body = self.rfile.read(content_length)
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except Exception as exc:
                self._send_json_response(
                    400, {"error": "invalid_json", "message": f"无法解析请求体 JSON: {exc}"}
                )
                return

        if path == "/api/v1/survey":
            self._handle_survey(daemon, body)
        elif path == "/api/v1/jobs/start":
            self._handle_jobs_start(daemon, body)
        elif path == "/api/v1/jobs/abort":
            self._handle_jobs_abort(daemon, body)
        else:
            self._send_json_response(404, {"error": "not_found", "message": f"未知路径: {path}"})

    def _handle_status(self, daemon: "ServerDaemon") -> None:
        status_data = daemon.get_status_report()
        self._send_json_response(200, status_data)

    def _handle_survey(self, daemon: "ServerDaemon", body: Dict[str, Any]) -> None:
        if daemon.server_state in (ServerState.RUNNING, ServerState.PREPARING):
            self._send_json_response(
                409,
                {
                    "error": "engine_busy",
                    "message": f"当前协同引擎正处于 {daemon.server_state.value} 状态，无法执行扫频探路",
                },
            )
            return

        duration_ms = float(body.get("duration_ms", 300.0))
        threshold_fps = float(body.get("congestion_threshold_fps", 50.0))

        try:
            report = daemon.run_spectrum_survey(
                duration_ms=duration_ms, congestion_threshold_fps=threshold_fps
            )
            rep_dict = report.to_dict()
            rep_dict["ranking"] = [r["channel"] for r in rep_dict.get("results", [])]
            self._send_json_response(200, rep_dict)
        except Exception as exc:
            logger.error("扫频执行异常: %s", exc)
            self._send_json_response(
                500, {"error": "survey_failed", "message": f"扫频执行失败: {exc}"}
            )

    def _handle_jobs_start(self, daemon: "ServerDaemon", body: Dict[str, Any]) -> None:
        # Preflight checks
        try:
            result = daemon.start_job(body)
            self._send_json_response(200, result)
        except FLRuntimeError as exc:
            # Map preflight failures to proper HTTP status codes
            error_code = getattr(exc, "error_code", "unknown_error")
            error_message = getattr(exc, "error_message", str(exc))
            status_code = 400
            if error_code == "preflight_engine_conflict":
                status_code = 409
            elif error_code == "preflight_target_nodes_not_ready":
                status_code = 400

            resp: Dict[str, Any] = {
                "error": error_code,
                "message": error_message,
            }
            if hasattr(exc, "details") and exc.details:
                resp.update(exc.details)
            self._send_json_response(status_code, resp)
        except Exception as exc:
            logger.error("启动作业异常: %s", exc)
            self._send_json_response(
                500, {"error": "job_start_failed", "message": str(exc)}
            )

    def _handle_jobs_abort(self, daemon: "ServerDaemon", body: Dict[str, Any]) -> None:
        reason = body.get("reason", "user_requested")
        result = daemon.abort_job(reason=reason)
        self._send_json_response(200, result)

    def _handle_logs_stream(self, daemon: "ServerDaemon") -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        event_queue = daemon.event_bus.subscribe()
        try:
            # Initial handshake
            init_msg = json.dumps({
                "status": "connected",
                "server_state": daemon.server_state.value,
                "timestamp": time.time(),
            })
            self.wfile.write(f"event: connected\ndata: {init_msg}\n\n".encode("utf-8"))
            self.wfile.flush()

            while not daemon._stop_event.is_set():
                try:
                    event = event_queue.get(timeout=0.5)
                    data_str = json.dumps(event)
                    self.wfile.write(f"event: message\ndata: {data_str}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Keepalive ping
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
        finally:
            daemon.event_bus.unsubscribe(event_queue)


class ServerDaemon:
    """
    Production WFB-ng Stage 2 Server Persistent Daemon.
    """

    def __init__(
        self,
        config: Optional[ServerDaemonConfig] = None,
        network_adapter: Optional[NetworkAdapter] = None,
        survey_backend: Optional[SurveyBackend] = None,
        runtime_factory: Optional[Callable[[JobConfig, Dict[str, Any]], Any]] = None,
    ):
        self.config = config or ServerDaemonConfig()
        self.adapter = network_adapter or LinuxNetworkAdapter()
        self.survey_backend = survey_backend
        self.runtime_factory = runtime_factory
        self.current_interface: Optional[str] = self.config.air_interface

        self.server_state = ServerState.INITIALIZING
        self.active_job: Optional[Dict[str, Any]] = None
        self.coordinator: Optional[Any] = None
        self.server_role: Optional[Any] = None

        self.event_bus = ServerEventBus()
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        # Link Process management
        self._link_process: Optional[subprocess.Popen] = None
        self._link_log_file: Optional[Any] = None
        self._link_thread: Optional[threading.Thread] = None

        # Control Plane Engine
        self.radio_config = RadioConfig(
            channel=self.config.channel,
            radio_txpower_dbm=self.config.radio_txpower_dbm,
            downlink_mcs=self.config.downlink_mcs,
            uplink_mcs=self.config.uplink_mcs,
        )
        self.control_plane = ControlPlaneServer(
            active_radio_config=self.radio_config,
            network_adapter=self.adapter,
            air_interface=self.current_interface,
            bind_host=self.config.control_bind_host,
            bind_port=self.config.control_bind_port,
            broadcast_addr=self.config.control_broadcast_addr,
            broadcast_port=self.config.control_broadcast_port,
            on_heartbeat_received=self._on_node_heartbeat,
        )

        # HTTP REST IPC Server
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None
        self.actual_ipc_port: int = self.config.ipc_port

    def _on_node_heartbeat(self, hb: NodeHeartbeat, record: NodeRecord) -> None:
        """Forward heartbeat notifications to SSE event stream."""
        self.publish_event({
            "type": "NODE_HEARTBEAT",
            "node_id": hb.node_id,
            "state": hb.state,
            "readiness": record.readiness.value,
            "elapsed_ms": hb.elapsed_ms,
            "current_channel": hb.current_channel,
            "txpower_dbm": hb.txpower_dbm,
            "uplink_mcs": hb.uplink_mcs,
            "timestamp": time.time(),
        })

    def publish_event(self, event: Dict[str, Any]) -> None:
        """Publish an event to all connected SSE clients."""
        self.event_bus.publish(event)

    def get_status_report(self) -> Dict[str, Any]:
        """Generate structured status snapshot for GET /api/v1/status."""
        with self._lock:
            all_nodes = self.control_plane.registry.get_all_nodes()
            now = time.monotonic()
            nodes_dict: Dict[str, Any] = {}
            for nid in self.config.known_clients:
                r = all_nodes.get(nid)
                if r is not None:
                    ago = max(0.0, now - r.last_heartbeat_time)
                    ts_ms = (
                        int(r.last_heartbeat_wall_time * 1000)
                        if r.last_heartbeat_wall_time is not None
                        else r.timestamp_ms
                    )
                    nodes_dict[str(nid)] = {
                        "node_id": r.node_id,
                        "tun_ip": r.tun_ip,
                        "reported_state": r.reported_state,
                        "readiness": r.readiness.value,
                        "elapsed_ms": r.elapsed_ms,
                        "current_channel": r.current_channel,
                        "txpower_dbm": r.txpower_dbm,
                        "uplink_mcs": r.uplink_mcs,
                        "last_heartbeat_time": r.last_heartbeat_time,
                        "last_heartbeat_timestamp_ms": ts_ms,
                        "last_heartbeat_ago_seconds": round(ago, 2),
                        "error_code": r.error_code,
                    }
                else:
                    nodes_dict[str(nid)] = {
                        "node_id": nid,
                        "tun_ip": f"10.80.0.{10 + nid}",
                        "reported_state": None,
                        "readiness": NodeReadiness.OFFLINE.value,
                        "elapsed_ms": 0,
                        "current_channel": None,
                        "txpower_dbm": None,
                        "uplink_mcs": None,
                        "last_heartbeat_time": None,
                        "last_heartbeat_timestamp_ms": None,
                        "last_heartbeat_ago_seconds": None,
                        "error_code": None,
                    }

            is_link_running = False
            link_pid = None
            if self._link_process is not None and self._link_process.poll() is None:
                is_link_running = True
                link_pid = self._link_process.pid

            return {
                "server_state": self.server_state.value,
                "active_job": self.active_job,
                "radio": self.radio_config.to_dict(),
                "tun": {
                    "name": self.config.tun_name,
                    "ip": self.config.tun_ip,
                    "cidr": self.config.tun_cidr,
                    "txqueuelen": self.config.tun_txqueuelen,
                    "is_active": self.adapter.is_tun_active(self.config.tun_name),
                },
                "air_interface": self.current_interface,
                "link_process": {
                    "running": is_link_running,
                    "pid": link_pid,
                },
                "nodes": nodes_dict,
            }

    def run_spectrum_survey(
        self, duration_ms: float = 300.0, congestion_threshold_fps: float = 50.0
    ) -> SpectrumSurveyReport:
        """Trigger 5 GHz channel survey using underlying wireless adapter."""
        with self._lock:
            if self.server_state in (ServerState.RUNNING, ServerState.PREPARING):
                raise FLRuntimeError("engine_busy", "作业执行期间无法执行扫频")

        iface = self.current_interface or "wlan0"
        report = survey_spectrum(
            interface=iface,
            duration_ms=duration_ms,
            backend=self.survey_backend,
            congestion_threshold_fps=congestion_threshold_fps,
        )
        self.publish_event({
            "type": "SPECTRUM_SURVEY_COMPLETED",
            "report": report.to_dict(),
            "timestamp": time.time(),
        })
        return report

    def start_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute 3 deterministic preflight checks and transition to active job execution.
        """
        job = JobConfig.from_dict(payload)

        with self._lock:
            # Check 3: Current engine is idle, no concurrent job conflict
            if self.server_state != ServerState.IDLE or self.active_job is not None:
                conflict_id = self.active_job.get("job_id") if self.active_job else "unknown"
                raise FLRuntimeError(
                    "preflight_engine_conflict",
                    f"当前协同引擎正处于 {self.server_state.value} 状态，存在并发作业冲突 (作业: {conflict_id})",
                )

            # Check 1: Target nodes check (within 10s confirmed IDLE)
            unready_nodes: Dict[str, Any] = {}
            now = time.monotonic()
            for nid in job.target_nodes:
                record = self.control_plane.registry.get_node(nid)
                if record is None:
                    unready_nodes[str(nid)] = {
                        "readiness": "OFFLINE",
                        "reported_state": None,
                        "last_heartbeat_ago_seconds": None,
                        "reason": "节点未上线或无任何心跳记录",
                    }
                elif record.readiness != NodeReadiness.READY:
                    ago = round(now - record.last_heartbeat_time, 2)
                    unready_nodes[str(nid)] = {
                        "readiness": record.readiness.value,
                        "reported_state": record.reported_state,
                        "last_heartbeat_ago_seconds": ago,
                        "reason": f"节点未就绪 (当前状态: {record.readiness.value})",
                    }
                elif (now - record.last_heartbeat_time) > NODE_OFFLINE_THRESHOLD_SECONDS:
                    ago = round(now - record.last_heartbeat_time, 2)
                    unready_nodes[str(nid)] = {
                        "readiness": "OFFLINE",
                        "reported_state": record.reported_state,
                        "last_heartbeat_ago_seconds": ago,
                        "reason": f"节点心跳超时 ({ago}s > 10s)",
                    }

            if unready_nodes:
                raise FLRuntimeError(
                    "preflight_target_nodes_not_ready",
                    f"目标客户端未在 10 秒内确认处于 IDLE 状态: {list(unready_nodes.keys())}",
                    details={"unready_nodes": unready_nodes},
                )

            # Check 2: Initial model file exists, is non-empty, and strictly matches size
            if not os.path.isfile(job.model_path):
                raise FLRuntimeError(
                    "preflight_model_file_not_found",
                    f"初始模型文件不存在: {job.model_path}",
                )

            actual_file_size = os.path.getsize(job.model_path)
            if actual_file_size <= 0:
                raise FLRuntimeError(
                    "preflight_model_empty",
                    f"初始模型文件为空 (0 字节): {job.model_path}",
                )

            if actual_file_size != job.model_size_bytes:
                raise FLRuntimeError(
                    "preflight_model_validation_failed",
                    f"模型大小不匹配: 期望 {job.model_size_bytes} 字节，实际 {actual_file_size} 字节",
                )

            # Check integrity sha256 if provided, or compute it
            computed_sha256 = calculate_file_sha256(job.model_path)
            if job.model_sha256 is not None and computed_sha256 != job.model_sha256:
                raise FLRuntimeError(
                    "preflight_model_checksum_failed",
                    f"模型 SHA-256 校验不一致: 期望 {job.model_sha256}，实际 {computed_sha256}",
                )

            # All 3 preflight checks passed!
            job_dict = job.to_dict()
            job_dict["model_sha256"] = computed_sha256
            job_dict["started_at"] = time.time()

            # Instantiate runtime and coordinator
            uftp_port = int(payload.get("uftp_port", DEFAULT_UFTP_DATA_PORT))
            if uftp_port in (self.config.control_broadcast_port, self.config.control_bind_port):
                raise FLRuntimeError(
                    "preflight_port_conflict",
                    "UFTP 数据端口不得与 UDP 控制面端口冲突",
                )
            runtime = None
            server_role = None
            if self.runtime_factory is not None:
                runtime = self.runtime_factory(job, payload)
            elif self.config.enable_link_process:
                from .role import ServerRole
                server_role = ServerRole(
                    work_dir=os.path.join(self.config.work_dir, f"job_{job.job_id}_role"),
                    participant_node_id=tuple(job.target_nodes),
                    participant_uftp_uid=tuple(job.target_nodes),
                    server_uftp_uid=100,
                    uftp_port=uftp_port,
                    http_port=int(payload.get("server_http_port", 8080)),
                    max_update_size_bytes=int(payload.get("max_update_size_bytes", actual_file_size * 2)),
                    http_host=self.config.tun_ip,
                    uftp_bind_host=self.config.tun_ip,
                    uftp_multicast_host=str(payload.get("uftp_multicast_host", "224.0.0.1")),
                    uftp_private_multicast_host=str(payload.get("uftp_private_multicast_host", "224.0.0.2")),
                )
                try:
                    server_role.start()
                    runtime = server_role.runtime
                except Exception:
                    server_role.close()
                    raise

            # Only transition state and commit active_job after runtime is ready
            self.active_job = job_dict
            self.server_state = ServerState.RUNNING
            self.server_role = server_role

            # Broadcast TASK_ANNOUNCE downlink to all clients (per spec line 108)
            announce_msg = {
                "type": "TASK_ANNOUNCE",
                "job_id": job.job_id,
                "mode": job.mode,
                "rounds": job.rounds,
                "target_nodes": list(job.target_nodes),
                "min_updates": job.min_updates,
                "max_staleness": job.max_staleness,
                "round_timeout_seconds": job.round_timeout_seconds,
                "model_size_bytes": actual_file_size,
                "model_sha256": computed_sha256,
                "server_http_host": self.config.tun_ip,
                "server_http_port": int(payload.get("server_http_port", 8080)),
                "uftp_port": uftp_port,
                "link_id": self.config.link_id,
                "algorithm": payload.get("algorithm", "wfb_ng.fl.issue41_algorithm:client_main"),
                "algorithm_config": payload.get("algorithm_config", {}),
                "timestamp_ms": int(time.time() * 1000),
            }
            self.control_plane.broadcast_downlink(announce_msg)

            # Broadcast SSE event
            self.publish_event({
                "type": "JOB_STARTED",
                "job": job_dict,
                "timestamp": time.time(),
            })

            if runtime is not None:
                self.coordinator = FLCoordinator(
                    job=job,
                    runtime=runtime,
                    work_dir=os.path.join(self.config.work_dir, f"job_{job.job_id}"),
                    on_event=self.publish_event,
                    on_completed=lambda summary: self._on_job_completed(job.job_id, summary),
                    on_failed=lambda exc: self._on_job_failed(job.job_id, exc),
                )
                self.coordinator.start()

            logger.info(
                "作业 %s 成功通过前置门禁并启动 (模式: %s, 轮次: %d, 节点: %s, 模型大小: %d B)",
                job.job_id,
                job.mode,
                job.rounds,
                list(job.target_nodes),
                actual_file_size,
            )

            return {
                "status": "accepted",
                "job_id": job.job_id,
                "mode": job.mode,
                "rounds": job.rounds,
                "target_nodes": list(job.target_nodes),
                "min_updates": job.min_updates,
                "model_size_bytes": actual_file_size,
                "model_sha256": computed_sha256,
            }

    def _cleanup_terminal_job(self, job_id: str) -> None:
        role_to_close = None
        with self._lock:
            if self.active_job and self.active_job.get("job_id") == job_id:
                self.server_state = ServerState.IDLE
                self.active_job = None
                role_to_close = self.server_role
                self.server_role = None
        if role_to_close is not None:
            try:
                role_to_close.close()
            except Exception as exc:
                logger.warning("关闭 server_role 失败: %s", exc)

    def _on_job_completed(self, job_id: str, summary: Dict[str, Any]) -> None:
        self._cleanup_terminal_job(job_id)
        if self.control_plane is not None:
            self.control_plane.broadcast_downlink({
                "type": "JOB_COMPLETED",
                "job_id": job_id,
                "timestamp_ms": int(time.time() * 1000),
            })
        self.publish_event({
            "type": "JOB_COMPLETED",
            "job_id": job_id,
            "summary": summary,
        })

    def _on_job_failed(self, job_id: str, exc: Exception) -> None:
        self._cleanup_terminal_job(job_id)
        if self.control_plane is not None:
            self.control_plane.broadcast_downlink({
                "type": "JOB_ABORT",
                "job_id": job_id,
                "reason": f"job_failed: {exc}",
                "timestamp_ms": int(time.time() * 1000),
            })
        self.publish_event({
            "type": "JOB_FAILED",
            "job_id": job_id,
            "error": str(exc),
        })

    def _stop_coordinator_and_role(self, reason: str = "stopped") -> None:
        coord = None
        s_role = None
        with self._lock:
            coord = self.coordinator
            s_role = self.server_role
            self.coordinator = None
            self.server_role = None

        if coord is not None:
            try:
                coord.abort(reason=reason)
                coord.wait(timeout=3.0)
            except Exception as exc:
                logger.warning("中止 coordinator 失败: %s", exc)

        if s_role is not None:
            try:
                s_role.close()
            except Exception as exc:
                logger.warning("关闭 server_role 失败: %s", exc)

    def abort_job(self, reason: str = "user_requested") -> Dict[str, Any]:
        """Forcefully abort active job and reset server state to IDLE."""
        with self._lock:
            aborted_job_id = self.active_job.get("job_id") if self.active_job else None
            self.server_state = ServerState.ABORTING

        self._stop_coordinator_and_role(reason=reason)

        # Broadcast JOB_ABORT to all clients
        abort_msg = {
            "type": "JOB_ABORT",
            "job_id": aborted_job_id,
            "reason": reason,
            "timestamp_ms": int(time.time() * 1000),
        }
        if self.control_plane is not None:
            self.control_plane.broadcast_downlink(abort_msg)

        with self._lock:
            self.active_job = None
            self.server_state = ServerState.IDLE

        self.publish_event({
            "type": "JOB_ABORTED",
            "job_id": aborted_job_id,
            "reason": reason,
            "timestamp": time.time(),
        })

        logger.info("作业已中止，协同引擎重置为 IDLE (原因: %s)", reason)
        return {"status": "aborted", "job_id": aborted_job_id, "reason": reason}

    def _start_link_process(self) -> None:
        """Launch wfb_v6_uplink server background process with pre-allocated slots."""
        executable = shutil.which("wfb_v6_uplink")
        if not executable:
            # Check local build directory
            local_bin = os.path.abspath("wfb_v6_uplink")
            if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
                executable = local_bin

        if not executable:
            raise FLRuntimeError("link_executable_missing", "未找到 wfb_v6_uplink 可执行程序，链路底座无法启动")

        iface = self.current_interface or "wlan0"
        cmd = build_v6_uplink_server_command(
            executable_path=executable,
            tun_name=self.config.tun_name,
            tun_addr=self.config.tun_cidr,
            air_interface=iface,
            channel=self.config.channel,
            downlink_mcs=self.config.downlink_mcs,
            link_id=self.config.link_id,
            known_clients=self.config.known_clients,
        )

        logger.info("启动 wfb_v6_uplink 链路底座: %s", " ".join(cmd))
        os.makedirs(self.config.work_dir, exist_ok=True)
        log_path = os.path.join(self.config.work_dir, "wfb_uplink.log")
        self._link_log_file = open(log_path, "a", encoding="utf-8")

        try:
            self._link_process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=self._link_log_file,
                stderr=self._link_log_file,
            )
        except Exception as exc:
            if hasattr(self, "_link_log_file") and self._link_log_file is not None:
                self._link_log_file.close()
                self._link_log_file = None
            raise FLRuntimeError("link_process_start_failed", f"拉起 wfb_v6_uplink 进程失败: {exc}") from exc

        # Wait briefly for TUN interface creation and configure txqueuelen
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            rc = self._link_process.poll()
            if rc is not None:
                raise FLRuntimeError("link_process_start_failed", f"wfb_v6_uplink 启动期异常退出，退出码: {rc}")
            if self.adapter.is_tun_active(self.config.tun_name):
                break
            time.sleep(0.05)

        if not self.adapter.is_tun_active(self.config.tun_name):
            self._stop_link_process()
            raise FLRuntimeError(
                "link_tun_failed",
                f"wfb_v6_uplink 未能在超时时间内创建 TUN 接口: {self.config.tun_name}",
            )

        try:
            self.adapter.set_tun_txqueuelen(self.config.tun_name, self.config.tun_txqueuelen)
            logger.info("已设置 TUN %s txqueuelen=%d", self.config.tun_name, self.config.tun_txqueuelen)
        except Exception as exc:
            logger.warning("设置 TUN txqueuelen 失败: %s", exc)

    def _stop_link_process(self) -> None:
        """Safely terminate wfb_v6_uplink process, close log file, and clean up TUN."""
        if self._link_process is not None:
            try:
                self._link_process.terminate()
                self._link_process.wait(timeout=2.0)
            except Exception:
                try:
                    self._link_process.kill()
                    self._link_process.wait(timeout=1.0)
                except Exception:
                    pass
            self._link_process = None

        if hasattr(self, "_link_log_file") and self._link_log_file is not None:
            try:
                self._link_log_file.close()
            except Exception:
                pass
            self._link_log_file = None

        try:
            self.adapter.teardown_tun(self.config.tun_name)
        except Exception:
            pass

    def start(self) -> None:
        """Start server hardware takeover, control plane, link process, and REST IPC."""
        with self._lock:
            if self.server_state != ServerState.INITIALIZING:
                return

            self._stop_event.clear()

            # 1. Hardware interface discovery and configuration
            if self.current_interface is None:
                interfaces = self.adapter.find_interfaces()
                if interfaces:
                    self.current_interface = interfaces[0]
                    self.control_plane.air_interface = self.current_interface
                    logger.info("自动纳管无线网卡接口: %s", self.current_interface)
                else:
                    logger.warning("未检测到物理无线网卡，将以无网卡模式运行")

            if self.current_interface:
                width = "HT20" if self.config.channel == 165 else FIXED_BANDWIDTH
                self.adapter.configure_wireless(
                    iface=self.current_interface,
                    channel=self.config.channel,
                    channel_width=width,
                    txpower_dbm=self.config.radio_txpower_dbm,
                )
                logger.info(
                    "网卡 %s 配置就绪 (信道=%d, 发射功率=%d dBm)",
                    self.current_interface,
                    self.config.channel,
                    self.config.radio_txpower_dbm,
                )

            # 2. Start Link Process if enabled
            if self.config.enable_link_process:
                self._start_link_process()

            # 3. Start Control Plane Engine if enabled
            if self.config.enable_control_plane:
                self.control_plane.start()
                logger.info("对称 UDP 控制平面已监听 (端口 9001, 广播 9000)")

            # 4. Start HTTP REST IPC Server
            self._httpd = ThreadingHTTPServer(
                (self.config.ipc_host, self.config.ipc_port),
                ServerIPCRequestHandler,
            )
            self._httpd.daemon = self  # type: ignore
            self.actual_ipc_port = self._httpd.server_port

            self._http_thread = threading.Thread(
                target=self._httpd.serve_forever,
                name="wfb-server-ipc-http",
                daemon=True,
            )
            self._http_thread.start()
            logger.info(
                "本地专用 REST IPC 运行中: http://%s:%d",
                self.config.ipc_host,
                self.actual_ipc_port,
            )

            self.server_state = ServerState.IDLE

    def stop(self) -> None:
        """Stop all background workers, sockets, and processes."""
        with self._lock:
            if self.server_state == ServerState.STOPPED:
                return

            self._stop_event.set()
            self.server_state = ServerState.STOPPED

            # Stop HTTP REST Server
            if self._httpd is not None:
                self._httpd.shutdown()
                self._httpd.server_close()
                self._httpd = None

            if self._http_thread is not None:
                self._http_thread.join(timeout=1.0)
                self._http_thread = None

            # Stop Control Plane
            if self.config.enable_control_plane:
                self.control_plane.stop()

            # Stop active coordinator and server role
            self._stop_coordinator_and_role(reason="daemon_stopped")

            # Stop Link Process and teardown TUN
            if self.config.enable_link_process:
                self._stop_link_process()

            logger.info("wfb-fl-server-daemon 已安全停止")

    def run(self) -> None:
        """Blocking supervisory runner responding to OS signals."""
        self.start()

        def _signal_handler(signum: int, frame: Any) -> None:
            logger.info("收到系统信号 %d，正在关闭服务...", signum)
            self.stop()

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        while not self._stop_event.is_set():
            time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WFB-ng Stage 2 Server Persistent Daemon & Local REST IPC"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=DEFAULT_SERVER_CONFIG_PATH,
        help="Path to server.json configuration file",
    )
    parser.add_argument("--ipc-host", type=str, default=None, help="REST IPC bind host")
    parser.add_argument("--ipc-port", type=int, default=None, help="REST IPC bind port")
    parser.add_argument("--channel", type=int, default=None, help="5 GHz radio channel")
    parser.add_argument("--txpower", type=int, default=None, help="Radio TX power in dBm")
    parser.add_argument("--downlink-mcs", type=int, default=None, help="Downlink MCS index (3..6)")
    parser.add_argument("--uplink-mcs", type=int, default=None, help="Uplink MCS index (3..6)")
    parser.add_argument("--interface", type=str, default=None, help="Air wireless interface name")
    parser.add_argument("--tun-name", type=str, default=None, help="Server TUN device name")

    args = parser.parse_args()

    # Load base config from file or defaults
    config = load_server_config(args.config)

    # CLI parameter overrides
    overrides: Dict[str, Any] = {}
    if args.ipc_host is not None:
        overrides["ipc_host"] = args.ipc_host
    if args.ipc_port is not None:
        overrides["ipc_port"] = args.ipc_port
    if args.channel is not None:
        overrides["channel"] = args.channel
    if args.txpower is not None:
        overrides["radio_txpower_dbm"] = args.txpower
    if args.downlink_mcs is not None:
        overrides["downlink_mcs"] = args.downlink_mcs
    if args.uplink_mcs is not None:
        overrides["uplink_mcs"] = args.uplink_mcs
    if args.interface is not None:
        overrides["air_interface"] = args.interface
    if args.tun_name is not None:
        overrides["tun_name"] = args.tun_name

    if overrides:
        merged = {**config.__dict__, **overrides}
        config = ServerDaemonConfig(**merged)

    daemon = ServerDaemon(config=config)
    daemon.run()


if __name__ == "__main__":
    main()
