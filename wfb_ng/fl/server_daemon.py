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
  1. Target nodes must be READY/IDLE under the registry heartbeat facts; the configured preparation budget covers TASK_READY.
  2. Initial model file exists and validated (size matches).
  3. Engine is currently IDLE with no concurrent job conflict.
"""

import argparse
from copy import deepcopy
import hashlib
import ipaddress
import json
import logging
import math
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
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qs, urlparse

from .artifacts import archive_file, write_json_atomic
from .algorithm import (AlgorithmContext, freeze_algorithm_config, load_algorithm,
                        validate_algorithm_config, validate_evaluation_result)
from .client_daemon import (
    LinuxNetworkAdapter,
    NetworkAdapter,
)
from .link_radio import DEFAULT_RADIO_CONTROL_PORT, apply_link_radio
from .coordinator import FLCoordinator, JobConfig, VALID_FL_MODES
from .transport import (
    DEFAULT_UFTP_DATA_PORT,
    DEFAULT_UFTP_MULTICAST_HOST,
    DEFAULT_UFTP_PRIVATE_MULTICAST_HOST,
)
from .control import (
    CLIENT_UPLINK_DEFAULT_ADDR,
    CLIENT_UPLINK_DEFAULT_PORT,
    SERVER_CONTROL_BROADCAST_ADDR,
    SERVER_CONTROL_BROADCAST_PORT,
    ClientNodeState,
    ControlPlaneServer,
    NodeHeartbeat,
    NodeReadiness,
    NodeRecord,
)
from .console import ConsoleApplicationService
from .console_http import ManagementWebListener, validate_management_address
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
    validate_radio_calibration_patch,
    validate_radio_patch,
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
DEFAULT_WEB_PORT = 8080
DEFAULT_TUN_NAME = "fl-s"
DEFAULT_TUN_IP = "10.80.0.1"
DEFAULT_TUN_CIDR = "10.80.0.1/24"
DEFAULT_TUN_TXQUEUELEN = 5000
DEFAULT_LINK_ID = 7669206
DEFAULT_DOWNLINK_MCS = 3
DEFAULT_UPLINK_MCS = 6
ALL_KNOWN_CLIENT_IDS = tuple(range(1, 11))
VALID_FL_MODES = ("sync",)


class ServerState(str, Enum):
    INITIALIZING = "INITIALIZING"
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    ABORTING = "ABORTING"
    SWITCHING_RADIO = "SWITCHING_RADIO"
    SURVEYING = "SURVEYING"
    RADIO_ERROR = "RADIO_ERROR"
    STOPPED = "STOPPED"


def calculate_file_sha256(path: str, cancel_event: Optional[threading.Event] = None) -> str:
    """Compute deterministic SHA-256 checksum for a local file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise FLRuntimeError("job_preparation_cancelled", "作业准备被停止")
            chunk = f.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass(frozen=True)
class ServerDaemonConfig:
    ipc_host: str = DEFAULT_IPC_HOST
    startup_config_path: str = DEFAULT_SERVER_CONFIG_PATH
    ipc_port: int = DEFAULT_IPC_PORT
    web_host: Optional[str] = None
    web_port: int = DEFAULT_WEB_PORT
    channel: int = DEFAULT_CHANNEL
    radio_txpower_dbm: int = DEFAULT_TXPOWER_DBM
    downlink_mcs: int = DEFAULT_DOWNLINK_MCS
    uplink_mcs: int = DEFAULT_UPLINK_MCS
    bandwidth_mhz: int = 40
    radio_control_port: int = DEFAULT_RADIO_CONTROL_PORT
    uftp_rate_kbps: Optional[int] = None
    tun_name: str = DEFAULT_TUN_NAME
    tun_ip: str = DEFAULT_TUN_IP
    tun_cidr: str = DEFAULT_TUN_CIDR
    tun_txqueuelen: int = DEFAULT_TUN_TXQUEUELEN
    control_bind_host: str = DEFAULT_TUN_IP
    control_bind_port: int = CLIENT_UPLINK_DEFAULT_PORT
    control_broadcast_addr: str = SERVER_CONTROL_BROADCAST_ADDR
    control_broadcast_port: int = SERVER_CONTROL_BROADCAST_PORT
    air_interface: Optional[str] = None
    link_id: int = DEFAULT_LINK_ID
    known_clients: Tuple[int, ...] = ALL_KNOWN_CLIENT_IDS
    work_dir: str = "/tmp/wfb-ng-fl/server"
    model_library_dir: str = "/var/lib/wfb-ng-fl/models"
    enable_link_process: bool = True
    enable_control_plane: bool = True
    preparation_timeout_seconds: float = 30.0
    round_timeout_seconds: float = 900.0
    io_timeout_seconds: float = 120.0
    mnist_data_dir: Optional[str] = None
    client_entry: str = 'wfb_ng.fl.mnist:client_entry'
    aggregate_entry: str = 'wfb_ng.fl.mnist:aggregate_entry'
    evaluate_entry: str = 'wfb_ng.fl.mnist:evaluate_entry'
    algorithm_config: Dict[str, Any] = field(default_factory=lambda: {'data_seed': 2026})

    def __post_init__(self) -> None:
        object.__setattr__(self, 'algorithm_config', validate_algorithm_config(self.algorithm_config))
        for entry in (self.client_entry, self.aggregate_entry, self.evaluate_entry):
            if not isinstance(entry, str) or entry.count(':') != 1 or not all(entry.split(':')):
                raise FLRuntimeError('invalid_configuration', '部署算法入口格式须为 module:callable')
        if self.mnist_data_dir is not None and (not isinstance(self.mnist_data_dir, str)
                                                or not os.path.isabs(self.mnist_data_dir)):
            raise FLRuntimeError('invalid_configuration', 'mnist_data_dir须为本机绝对目录')
        # Validate radio parameters fail-closed per ADR-0014
        try:
            radio = validate_radio_config({
                "channel": self.channel,
                "bandwidth_mhz": self.bandwidth_mhz,
                "radio_txpower_dbm": self.radio_txpower_dbm,
                "downlink_mcs": self.downlink_mcs,
                "uplink_mcs": self.uplink_mcs,
                **({"uftp_rate_kbps": self.uftp_rate_kbps} if self.uftp_rate_kbps is not None else {}),
            })
            object.__setattr__(self, "uftp_rate_kbps", radio.uftp_rate_kbps)
        except Exception as exc:
            raise FLRuntimeError("invalid_radio_configuration", str(exc)) from exc

        if type(self.radio_control_port) is not int or not 1 <= self.radio_control_port <= 65535:
            raise FLRuntimeError("invalid_radio_control_port", "本机射频端口必须为 1..65535 的整数")

        # Validate IP addresses
        try:
            ipaddress.IPv4Address(self.tun_ip)
            ipaddress.IPv4Interface(self.tun_cidr)
        except Exception as exc:
            raise FLRuntimeError("invalid_tun_ip", f"TUN 地址配置非法: {exc}") from exc

        # Web is deliberately opt-in: it must use an explicit management address.
        if self.web_host is not None:
            try:
                web_address = ipaddress.IPv4Address(self.web_host)
            except Exception as exc:
                raise FLRuntimeError("invalid_web_host", f"管理 Web 地址非法: {exc}") from exc
            if web_address.is_loopback or web_address.is_unspecified or web_address == ipaddress.IPv4Address(self.tun_ip):
                raise FLRuntimeError("invalid_web_host", "管理 Web 地址不得为回环、通配或 TUN 地址")
        if self.web_port != DEFAULT_WEB_PORT:
            raise FLRuntimeError("invalid_web_port", "管理 Web 端口固定为 8080")

        for name in ('preparation_timeout_seconds', 'round_timeout_seconds', 'io_timeout_seconds'):
            raw = getattr(self, name)
            code = 'invalid_' + name.removesuffix('_seconds')
            try:
                if isinstance(raw, bool):
                    raise ValueError('布尔值不是期限')
                value = float(raw)
            except (TypeError, ValueError, OverflowError) as exc:
                raise FLRuntimeError(code, f'{name} 必须为有限正数') from exc
            if not math.isfinite(value) or value <= 0:
                raise FLRuntimeError(code, f'{name} 必须为有限正数')
            object.__setattr__(self, name, value)

        # Validate known_clients
        if self.known_clients != ALL_KNOWN_CLIENT_IDS:
            raise FLRuntimeError(
                "invalid_known_clients",
                "必须全量预置平台支持的 1~10 号节点白名单槽位，严禁缩减槽位池",
            )


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
        startup_config_path=path,
        ipc_host=data.get("ipc_host", DEFAULT_IPC_HOST),
        ipc_port=int(data.get("ipc_port", DEFAULT_IPC_PORT)),
        web_host=data.get("web_host"),
        web_port=int(data.get("web_port", DEFAULT_WEB_PORT)),
        channel=int(data.get("channel", DEFAULT_CHANNEL)),
        radio_txpower_dbm=int(data.get("radio_txpower_dbm", DEFAULT_TXPOWER_DBM)),
        downlink_mcs=int(data.get("downlink_mcs", DEFAULT_DOWNLINK_MCS)),
        uplink_mcs=int(data.get("uplink_mcs", DEFAULT_UPLINK_MCS)),
        radio_control_port=data.get("radio_control_port", DEFAULT_RADIO_CONTROL_PORT),
        bandwidth_mhz=data.get("bandwidth_mhz", 40),
        uftp_rate_kbps=data.get("uftp_rate_kbps"),
        tun_name=data.get("tun_name", DEFAULT_TUN_NAME),
        tun_ip=data.get("tun_ip", DEFAULT_TUN_IP),
        tun_cidr=data.get("tun_cidr", DEFAULT_TUN_CIDR),
        tun_txqueuelen=int(data.get("tun_txqueuelen", DEFAULT_TUN_TXQUEUELEN)),
        control_bind_host=data.get("control_bind_host", DEFAULT_TUN_IP),
        control_bind_port=int(data.get("control_bind_port", CLIENT_UPLINK_DEFAULT_PORT)),
        control_broadcast_addr=data.get("control_broadcast_addr", SERVER_CONTROL_BROADCAST_ADDR),
        control_broadcast_port=int(data.get("control_broadcast_port", SERVER_CONTROL_BROADCAST_PORT)),
        air_interface=data.get("air_interface"),
        link_id=int(data.get("link_id", DEFAULT_LINK_ID)),
        known_clients=ALL_KNOWN_CLIENT_IDS,
        work_dir=data.get("work_dir", "/tmp/wfb-ng-fl/server"),
        model_library_dir=data.get("model_library_dir", "/var/lib/wfb-ng-fl/models"),
        enable_link_process=bool(data.get("enable_link_process", True)),
        enable_control_plane=bool(data.get("enable_control_plane", True)),
        preparation_timeout_seconds=data.get("preparation_timeout_seconds", 30.0),
        round_timeout_seconds=data.get("round_timeout_seconds", 900.0),
        io_timeout_seconds=data.get("io_timeout_seconds", 120.0),
        mnist_data_dir=data.get('mnist_data_dir'),
        client_entry=data.get('client_entry', 'wfb_ng.fl.mnist:client_entry'),
        aggregate_entry=data.get('aggregate_entry', 'wfb_ng.fl.mnist:aggregate_entry'),
        evaluate_entry=data.get('evaluate_entry', 'wfb_ng.fl.mnist:evaluate_entry'),
        algorithm_config=data.get('algorithm_config', {'data_seed': 2026}),
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
    bandwidth_mhz: int = 40,
    radio_control_port: int = DEFAULT_RADIO_CONTROL_PORT,
) -> List[str]:
    """
    Construct command line arguments for wfb_v6_uplink running as server.
    Per ADR-0012, ADR-0014, and Ticket 05:
    - Pre-allocates all 1..10 known clients (--known-clients 1,2,3,4,5,6,7,8,9,10)
    - Pre-allocates all client-target mappings (--client-target nid:10.80.0.{10+nid}:127.0.0.1:1)
    - OMIT --grant-duration-ms and --guard-interval-ms to rely on built-in 120ms/10ms defaults.
    """
    RadioConfig(channel=channel, bandwidth_mhz=bandwidth_mhz).channel_width
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
        "--radio-bandwidth", str(bandwidth_mhz),
        "--radio-control-port", str(radio_control_port),
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

        if path == "/api/v1/state":
            self._send_json_response(200, daemon.console.snapshot())
        elif path == "/api/v1/status":
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

        if path in ("/api/v1/radio/reconfigure", "/api/v1/radio/calibrate"):
            self._handle_radio_reconfigure(daemon, body, calibration=path.endswith("/calibrate"))
        elif path == "/api/v1/radio/save":
            try:
                self._send_json_response(200, daemon.save_radio_configuration(body))
            except FLRuntimeError as exc:
                self._send_json_response(409 if exc.error_code == "engine_busy" else 400,
                                         {"error": exc.error_code, "message": exc.error_message})
        elif path == "/api/v1/survey":
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

    def _handle_radio_reconfigure(self, daemon: "ServerDaemon", body: Any, *, calibration: bool = False) -> None:
        try:
            result = daemon.calibrate_radio(body) if calibration else daemon.reconfigure_radio(body)
        except FLRuntimeError as exc:
            code = 409 if exc.error_code in ("engine_busy", "radio_target_nodes_not_ready", "radio_target_set_changed") else 400
            self._send_json_response(code, {
                "error": exc.error_code, "message": exc.error_message,
                **(exc.details or {}),
            })
        except Exception as exc:
            logger.exception("射频重配执行异常")
            self._send_json_response(500, {"error": "radio_reconfigure_failed", "message": str(exc)})
        else:
            self._send_json_response(200, result)

    def _handle_survey(self, daemon: "ServerDaemon", body: Dict[str, Any]) -> None:
        if daemon.server_state != ServerState.IDLE:
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
        except FLRuntimeError as exc:
            self._send_json_response(
                409 if exc.error_code == "engine_busy" else 500,
                {"error": exc.error_code, "message": exc.error_message},
            )
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


class _JobPreparation:
    """在准备阶段管理可取消资源并等待其线程退出。"""

    def __init__(self) -> None:
        self._cancel_event = threading.Event()
        self._done_event = threading.Event()
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._resource: Optional[Any] = None
        self._result: Optional[Tuple[Any, Optional[Any]]] = None
        self._error: Optional[BaseException] = None

    @property
    def cancellation_event(self) -> threading.Event:
        return self._cancel_event

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def start(self, work: Callable[[], Tuple[Any, Optional[Any]]]) -> None:
        self._thread = threading.Thread(target=self._run, args=(work,),
                                        name="wfb-job-preparation", daemon=True)
        self._thread.start()

    def _run(self, work: Callable[[], Tuple[Any, Optional[Any]]]) -> None:
        try:
            self._result = work()
        except BaseException as exc:
            self._error = exc
        finally:
            self._done_event.set()

    def register_resource(self, resource: Any) -> None:
        with self._lock:
            cancelled = self.cancelled
            if not cancelled:
                self._resource = resource
        if cancelled:
            resource.close()

    def wait(self, timeout: float) -> bool:
        return self._done_event.wait(timeout)

    def result(self) -> Tuple[Any, Optional[Any]]:
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise RuntimeError("作业准备线程未产生结果")
        return self._result

    def close_registered_resource(self, resource: Any) -> None:
        if resource is None:
            return
        with self._lock:
            if self._resource is resource:
                self._resource = None
        try:
            resource.close()
        except Exception:
            logger.exception("关闭准备资源失败")

    def cancel_and_wait(self) -> None:
        self._cancel_event.set()
        with self._lock:
            resource = self._resource
            self._resource = None
            thread = self._thread
        if resource is not None:
            try:
                resource.close()
            except Exception:
                logger.exception("取消作业准备资源失败")
        if thread is not None:
            thread.join()


class ServerDaemon:
    """Persistent cluster owner with separate local IPC and console adapters."""

    @property
    def server_state(self) -> ServerState:
        return self._server_state

    @server_state.setter
    def server_state(self, state: ServerState) -> None:
        if not hasattr(self, "console"):
            self._server_state = state
            return
        with self._lock:
            self._server_state = state
            self._best_effort_console_refresh()

    def _best_effort_console_refresh(self) -> None:
        """Optional Web observation must never interrupt a core mutation."""
        try:
            self.console.snapshot()
        except Exception:
            logger.exception("控制台观察刷新失败，核心操作继续")

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
        self._recent_job: Optional[Dict[str, Any]] = None
        self.coordinator: Optional[Any] = None
        self.server_role: Optional[Any] = None
        self._job_runtime: Optional[Any] = None
        self._job_training_by_round: Dict[int, List[Dict[str, Any]]] = {}
        self._preparation: Optional[_JobPreparation] = None

        self.event_bus = ServerEventBus()
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        # Link Process management
        self._link_process: Optional[subprocess.Popen] = None
        self._link_log_file: Optional[Any] = None
        self._link_radio_config: Optional[RadioConfig] = None
        self._link_thread: Optional[threading.Thread] = None

        # Control Plane Engine
        self.radio_config = validate_radio_config({
            "channel": self.config.channel,
            "bandwidth_mhz": self.config.bandwidth_mhz,
            "uftp_rate_kbps": self.config.uftp_rate_kbps,
            "radio_txpower_dbm": self.config.radio_txpower_dbm,
            "downlink_mcs": self.config.downlink_mcs,
            "uplink_mcs": self.config.uplink_mcs,
        })
        self._radio_config_confirmed = True
        self.control_plane = ControlPlaneServer(
            active_radio_config=self.radio_config,
            network_adapter=self.adapter,
            air_interface=self.current_interface,
            bind_host=self.config.control_bind_host,
            bind_port=self.config.control_bind_port,
            broadcast_addr=self.config.control_broadcast_addr,
            broadcast_port=self.config.control_broadcast_port,
            on_heartbeat_received=self._on_node_heartbeat,
            on_radio_applied=self._apply_radio_runtime,
        )

        self.control_plane.register_task_error_handler(self._on_task_report_error)
        self._terminal_done = threading.Event()
        self._terminal_done.set()
        self._job_monitor_stop = threading.Event()
        self._job_monitor_thread: Optional[threading.Thread] = None

        # HTTP REST IPC Server
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None
        self.actual_ipc_port: int = self.config.ipc_port
        self._web_state = ("management_web_unavailable", "WEB_HOST_NOT_CONFIGURED" if self.config.web_host is None else None)
        self.console = ConsoleApplicationService(self.get_status_report, self._lock,
                                                 Path(self.config.model_library_dir), self._apply_console_radio,
                                                 self._start_console_job, self._abort_console_job)
        self._web = ManagementWebListener(
            self.config.web_host, self.config.web_port, self.console,
            lambda host: validate_management_address(host, self.config.tun_name, self.current_interface),
            self._on_web_status,
        )

    def _start_console_job(self, request: Dict[str, Any], idempotency_key: str) -> Dict[str, Any]:
        """将Web窄接口映射到已部署的真实算法，不接受浏览器算法路径。"""
        with self._lock:
            payload = dict(request)
            payload.update({
                'run_id': f'web-{uuid.uuid4().hex}',
                'job_id': f'web-{uuid.uuid4().hex}',
                'mode': 'sync',
                'live_observation': False,
            })
        result = self.start_job(payload)
        result['idempotency_key'] = idempotency_key
        return result

    def _abort_console_job(self, job_id: str, reason: str) -> Dict[str, Any]:
        with self._lock:
            if self.active_job is None or self.active_job.get('job_id') != job_id:
                raise FLRuntimeError('JOB_NOT_ACTIVE', '目标作业不处于运行中')
        self.publish_event({'type': 'JOB_ABORT_REQUESTED', 'job_id': job_id, 'reason': reason})

        def run_abort() -> None:
            try:
                result = self._finalize_job(outcome='aborted', job_id=job_id, reason=reason)
                if result.get('status') == 'ignored':
                    self.publish_event({'type': 'JOB_ABORT_SUPERSEDED', 'job_id': job_id,
                                        'reason': 'job_already_terminal'})
            except Exception:
                logger.exception('Web 急停执行失败 (job=%s)', job_id)

        threading.Thread(target=run_abort, name='wfb-console-abort', daemon=True).start()
        return {'status': 'accepted', 'job_id': job_id, 'execution_result': 'pending'}

    def _on_web_status(self, status: str, error: Optional[str]) -> None:
        # Listener status cannot wait behind a potentially slow snapshot reader.
        self._web_state = (status, error)
        self.console.record_web_status(status, error)

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
        """Keep diagnostic stream and console operator history separate."""
        if event.get('type') == 'NODE_HEARTBEAT':
            # The UDP receiver must keep processing TASK_READY while start_job
            # holds the daemon lock at the readiness barrier. Heartbeats are
            # diagnostic only; their authoritative facts are already in registry.
            self.event_bus.publish(event)
            if self._lock.acquire(blocking=False):
                try:
                    self._best_effort_console_refresh()
                finally:
                    self._lock.release()
            return

        event_for_history = deepcopy(event)
        with self._lock:
            if (self.active_job is not None and
                    event.get('job_id', self.active_job['job_id']) == self.active_job['job_id']):
                event_type = event.get('type')
                event_for_history = self._on_training_round_event(event)
                if event_type == 'ROUND_STARTED':
                    self.active_job['current_round'] = event.get('round_index')
                    self.active_job['server_phase'] = 'preparing'
                    self.active_job['round_started_at'] = time.time()
                    self.active_job['phase_started_at'] = time.time()
                elif event_type in ('MODEL_PUBLISH_START', 'MODEL_PUBLISH_STARTED'):
                    self.active_job['server_phase'] = 'publishing_model'
                    self.active_job['phase_started_at'] = time.time()
                elif event_type == 'WAIT_FOR_UPDATES_START':
                    self.active_job['server_phase'] = 'waiting_updates'
                    self.active_job['phase_started_at'] = time.time()
                elif event_type == 'EVALUATION_COMPLETED':
                    if event.get('round_index') == self.active_job['rounds']:
                        self.active_job['effect_result'] = {
                            key: deepcopy(event.get(key)) for key in ('round_index', 'metrics', 'verdict', 'reason')}
                elif event_type == 'ROUND_COMPLETED':
                    self.active_job['rounds_completed'] = event.get('round_index', 0)
                    self.active_job.setdefault('round_results', []).append(deepcopy(event_for_history.get('summary', {})))
                    self.active_job['server_phase'] = 'preparing'
                    self.active_job['phase_started_at'] = time.time()
                elif event_type in ('JOB_COMPLETED', 'JOB_FAILED') and isinstance(event.get('summary'), dict):
                    self.active_job['summary'] = deepcopy(event['summary'])
            self.console.record_event(event_for_history)
            self._best_effort_console_refresh()
        self.event_bus.publish(event)

    def _selection_blockers(self, node_id: int, record: Optional[NodeRecord]) -> List[Dict[str, str]]:
        """复用注册表的实时就绪状态及其心跳超时判定。"""
        blockers = []
        if ((record is not None and record.tun_ip == self.config.tun_ip)
                or (record is None and f"10.80.0.{10 + node_id}" == self.config.tun_ip)):
            blockers.append({"code": "SERVER_NODE", "message": "Server 自身不能作为客户端目标"})
        if record is None or record.readiness == NodeReadiness.OFFLINE:
            blockers.append({"code": "NODE_OFFLINE", "message": "节点未上线或心跳已超时"})
        else:
            if record.readiness != NodeReadiness.READY:
                blockers.append({"code": "NODE_NOT_READY", "message": f"基础未就绪: {record.readiness.value}"})
            if record.reported_state != ClientNodeState.IDLE.value:
                blockers.append({"code": "NODE_NOT_IDLE", "message": f"节点尚未待命: {record.reported_state}"})
        if record is not None and record.error_code:
            blockers.append({"code": "NODE_ERROR", "message": record.error_code})
        return blockers

    def _unready_target_nodes(self, target_nodes: Sequence[int]) -> Dict[str, Any]:
        unready_nodes: Dict[str, Any] = {}
        now = time.monotonic()
        for nid in target_nodes:
            record = self.control_plane.registry.get_node(nid)
            blockers = self._selection_blockers(nid, record)
            if blockers:
                unready_nodes[str(nid)] = {
                    "readiness": record.readiness.value if record else "OFFLINE",
                    "reported_state": record.reported_state if record else None,
                    "last_heartbeat_ago_seconds": (
                        round(max(0.0, now - record.last_heartbeat_time), 2) if record else None),
                    "selection_blockers": blockers,
                    "reason": "; ".join(blocker["message"] for blocker in blockers),
                }
        return unready_nodes

    def get_status_report(self) -> Dict[str, Any]:
        """Generate structured status snapshot for GET /api/v1/status."""
        with self._lock:
            self._refresh_job_recovery()
            all_nodes = self.control_plane.registry.get_all_nodes() if self.control_plane is not None else {}
            now = time.monotonic()
            nodes_dict: Dict[str, Any] = {}
            for nid in self.config.known_clients:
                expected_ip = f"10.80.0.{10 + nid}"
                record = all_nodes.get(nid)
                if self.config.tun_ip == (record.tun_ip if record is not None else expected_ip):
                    continue
                r = record
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
                        "tun_ip": expected_ip,
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

                selection_blockers = self._selection_blockers(nid, r)
                nodes_dict[str(nid)]["can_select"] = not selection_blockers
                nodes_dict[str(nid)]["selection_blockers"] = selection_blockers

            web_status, web_error = self._web_state
            is_link_running = False
            link_pid = None
            if self._link_process is not None and self._link_process.poll() is None:
                is_link_running = True
                link_pid = self._link_process.pid

            return {
                "server_state": self.server_state.value,
                "management_web": {
                    "status": web_status,
                    "host": self.config.web_host,
                    "port": self.config.web_port,
                    "error": web_error,
                },
                "link_required": self.config.enable_link_process,
                "recent_job": deepcopy(self._recent_job),
                "active_job": deepcopy(self.active_job),
                "radio": self.radio_config.to_dict() if self._radio_config_confirmed else None,
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

    def _apply_console_radio(self, token: str, confirm_risk: bool) -> Dict[str, Any]:
        with self._lock:
            payload = self.console.consume_radio_confirmation(token, confirm_risk)
            self._reserve_radio_reconfiguration(payload)
        return self._execute_radio_reconfiguration(payload['patch'], payload['target_nodes'])

    def reconfigure_radio(self, payload: Any) -> Dict[str, Any]:
        """接受人工确认请求；协议执行期间释放锁以便状态查询和冲突响应。"""
        self._reserve_radio_reconfiguration(payload)
        return self._execute_radio_reconfiguration(payload['patch'], payload['target_nodes'])

    def save_radio_configuration(self, payload: Any) -> Dict[str, Any]:
        """操作者显式保存已确认的正常配置；在线调整本身不写启动文件。"""
        if not isinstance(payload, dict) or payload != {"confirmed": True} or payload["confirmed"] is not True:
            raise FLRuntimeError("invalid_radio_request", "保存只接受 confirmed=true")
        with self._lock:
            if self.server_state != ServerState.IDLE or self.active_job is not None:
                raise FLRuntimeError("engine_busy", "仅待命状态允许保存启动配置")
            if not self._radio_config_confirmed or self.control_plane.calibration:
                raise FLRuntimeError("invalid_radio_request", "不能保存未确认或标定配置")
            radio = validate_radio_config(self.radio_config.to_dict())
            path = Path(self.config.startup_config_path)
            try:
                data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                if not isinstance(data, dict):
                    raise ValueError("启动配置必须为 JSON object")
                data.update({key: value for key, value in radio.to_dict().items()
                             if key in {"channel", "bandwidth_mhz", "radio_txpower_dbm", "downlink_mcs", "uplink_mcs", "uftp_rate_kbps"}})
                write_json_atomic(str(path), data)
            except (OSError, ValueError) as exc:
                raise FLRuntimeError("radio_config_save_failed", f"保存启动配置失败: {exc}") from exc
            return {"saved_config": radio.to_dict()}

    def calibrate_radio(self, payload: Any) -> Dict[str, Any]:
        """仅本机受控标定入口；普通作业不能使用未标定的速率。"""
        self._reserve_radio_reconfiguration(payload, calibration=True)
        return self._execute_radio_reconfiguration(payload['patch'], payload['target_nodes'], calibration=True)

    def _reserve_radio_reconfiguration(self, payload: Any, *, calibration: bool = False) -> None:
        if not isinstance(payload, dict):
            raise FLRuntimeError("invalid_radio_request", "请求体必须为 JSON object")
        if set(payload) != {"confirmed", "patch", "target_nodes"}:
            raise FLRuntimeError("invalid_radio_request", "仅允许 confirmed、patch、target_nodes 字段")
        if payload["confirmed"] is not True:
            raise FLRuntimeError("invalid_radio_request", "必须由操作者明确确认 confirmed=true")
        patch = payload["patch"]
        targets = payload["target_nodes"]
        if not isinstance(patch, dict) or not patch:
            raise FLRuntimeError("invalid_radio_request", "patch 必须为非空 object")
        if (
            not isinstance(targets, list) or not targets
            or any(type(nid) is not int or nid not in self.config.known_clients for nid in targets)
            or len(set(targets)) != len(targets)
        ):
            raise FLRuntimeError("invalid_radio_request", "target_nodes 必须为不重复的 1~10 节点列表")

        with self._lock, self.control_plane.registry.admission_window():
            try:
                validator = validate_radio_calibration_patch if calibration else validate_radio_patch
                validator(patch, base=self.radio_config)
            except (TypeError, ValueError) as exc:
                raise FLRuntimeError("invalid_radio_request", str(exc)) from exc
            if self.server_state != ServerState.IDLE or self.active_job is not None:
                raise FLRuntimeError("engine_busy", f"当前状态 {self.server_state.value} 无法执行射频重配")
            unready = []
            for nid in targets:
                record = self.control_plane.registry.get_node(nid)
                if (
                    record is None or record.readiness != NodeReadiness.READY
                    or record.reported_state != ClientNodeState.IDLE.value
                ):
                    unready.append(nid)
            if unready:
                raise FLRuntimeError(
                    "radio_target_nodes_not_ready", "目标节点必须全部 IDLE/READY",
                    details={"unready_nodes": sorted(unready)},
                )
            online = sorted(
                nid for nid, record in self.control_plane.registry.get_all_nodes().items()
                if record.readiness != NodeReadiness.OFFLINE
            )
            if online != sorted(targets):
                raise FLRuntimeError(
                    "radio_target_set_changed", "必须包含受理时全部在线客户端",
                    details={"expected_nodes": online},
                )
            self.server_state = ServerState.SWITCHING_RADIO

    def _execute_radio_reconfiguration(self, patch: Dict[str, Any], targets: List[int], *, calibration: bool = False) -> Dict[str, Any]:
        try:
            operation = self.control_plane.calibrate_radio if calibration else self.control_plane.reconfigure_radio
            result = operation(patch, target_nodes=targets)
        except Exception:
            with self._lock:
                self.radio_config = self.control_plane.active_radio_config
                self._radio_config_confirmed = False
                self.control_plane.radio_error = True
                if self.server_state != ServerState.STOPPED:
                    self.server_state = ServerState.RADIO_ERROR
                self.console.record_event({"type": "RADIO_APPLY_FAILED", "message": "射频应用失败"})
            raise
        with self._lock:
            self.radio_config = self.control_plane.active_radio_config
            self._radio_config_confirmed = result.effective_config is not None
            if self.server_state != ServerState.STOPPED:
                self.server_state = ServerState.RADIO_ERROR if result.status == "radio_error" else ServerState.IDLE
            self.console.record_event({"type": "RADIO_APPLY_RESULT", "message": result.status})
            return {
                "calibration": self.control_plane.calibration,
                "session_id": result.session_id,
                "status": result.status,
                "target_channel": result.target_channel,
                "applied_patch": result.applied_patch,
                "failed_phase": result.failed_phase,
                "unresponsive_nodes": result.unresponsive_nodes,
                "error_message": result.error_message,
                "effective_config": self.radio_config.to_dict() if self._radio_config_confirmed else None,
            }

    def run_spectrum_survey(
        self, duration_ms: float = 300.0, congestion_threshold_fps: float = 50.0
    ) -> SpectrumSurveyReport:
        """与作业和射频重配共享准入锁，预留扫频状态直到硬件操作结束。"""
        with self._lock:
            if self.server_state != ServerState.IDLE:
                raise FLRuntimeError("engine_busy", "当前状态无法执行扫频")
            self.server_state = ServerState.SURVEYING
        try:
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
        finally:
            with self._lock:
                if self.server_state == ServerState.SURVEYING:
                    self.server_state = ServerState.IDLE

    def _on_task_report_error(self, report: Dict[str, Any]) -> None:
        """可靠错误 ACK 不等待宿主锁、计算或资源回收。"""
        frozen = deepcopy(report)

        def fail() -> None:
            with self._lock:
                if self.active_job is None or self.active_job['job_id'] != frozen['job_id']:
                    if self._recent_job is not None and self._recent_job['job_id'] == frozen['job_id']:
                        self._recent_job['client_error'] = frozen
                    return
                self.active_job['client_error'] = frozen
            self._finalize_job(outcome='failed', job_id=frozen['job_id'],
                               reason=frozen['error_code'], error=frozen['reason'])

        threading.Thread(target=fail, name=f"fl-client-error-{frozen['job_id']}", daemon=True).start()

    def _on_job_liveness(self, event: Dict[str, Any]) -> None:
        """控制面负责心跳期限及无线租约；这里只处理固定目标的失联事实。"""
        if event['state'] != 'offline':
            return

        def fail() -> None:
            with self._lock:
                if self.active_job is None or self.active_job['job_id'] != event['job_id']:
                    return
                self.active_job.setdefault('liveness_events', []).append(deepcopy(event))
            self._finalize_job(outcome='failed', job_id=event['job_id'], reason='participant_offline',
                               error=f"固定参与者 {event['node_id']} 心跳失联")

        threading.Thread(target=fail, name=f"fl-offline-{event['job_id']}", daemon=True).start()

    def _watch_job_role(self, job_id: str, role: Any) -> None:
        stop = threading.Event()
        self._job_monitor_stop = stop
        self._job_monitor_thread = None
        if role is None:
            return

        def monitor() -> None:
            while not stop.wait(0.05):
                try:
                    failure = role.poll_failure()
                except Exception as exc:
                    failure = exc
                if failure is not None:
                    self._on_job_failed(job_id, failure)
                    return

        self._job_monitor_thread = threading.Thread(target=monitor, name=f'fl-role-{job_id}', daemon=True)
        self._job_monitor_thread.start()

    def start_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """冻结已部署算法，先完成初始评估与全员准备，再启动真实轮次。"""
        if any(key in payload for key in ('algorithm', 'algorithm_config', 'client_entry',
                                          'aggregate_entry', 'evaluate_entry')):
            raise FLRuntimeError('invalid_job_config', '算法入口和参数只从服务器部署配置选择')
        payload = {**payload,
                   "preparation_timeout_seconds": self.config.preparation_timeout_seconds,
                   "round_timeout_seconds": self.config.round_timeout_seconds,
                   "io_timeout_seconds": self.config.io_timeout_seconds}
        job = JobConfig.from_dict(payload)
        algorithm_config = validate_algorithm_config(self.config.algorithm_config)
        algorithm_context: Optional[AlgorithmContext] = None
        aggregate_fn: Optional[Callable[..., Any]] = None
        evaluate_fn: Optional[Callable[..., Any]] = None
        initial_result: Dict[str, Any] = {}
        preparation_started_at = time.monotonic()

        with self._lock:
            if self.control_plane.calibration:
                raise FLRuntimeError("radio_calibration_active", "受控标定配置仅用于测量，请先恢复正常无线配置")
            preparation_timeout_seconds = self.config.preparation_timeout_seconds
            # Check 3: Current engine is idle, no concurrent job conflict
            if self.server_state != ServerState.IDLE or self.active_job is not None:
                conflict_id = self.active_job.get("job_id") if self.active_job else "unknown"
                raise FLRuntimeError(
                    "preflight_engine_conflict",
                    f"当前协同引擎正处于 {self.server_state.value} 状态，存在并发作业冲突 (作业: {conflict_id})",
                )
            self._refresh_job_recovery()
            if self._recent_job is not None and self._recent_job.get("recovery_state") != "ready":
                raise FLRuntimeError(
                    "preflight_engine_conflict",
                    "上一项作业资源尚未恢复，不能受理新作业",
                )

            # 按 Web 候选使用的同一组事实整体校验提交目标。
            unready_nodes = self._unready_target_nodes(job.target_nodes)

            if unready_nodes:
                raise FLRuntimeError(
                    "preflight_target_nodes_not_ready",
                    f"目标客户端不可用: {list(unready_nodes.keys())}",
                    details={"unready_nodes": unready_nodes},
                )

            uftp_port = int(payload.get("uftp_port", DEFAULT_UFTP_DATA_PORT))
            if uftp_port in (self.config.control_broadcast_port, self.config.control_bind_port):
                raise FLRuntimeError(
                    "preflight_port_conflict",
                    "UFTP 数据端口不得与 UDP 控制面端口冲突",
                )

            # 模型读取和摘要也属于准备预算，交给可取消的准备句柄。
            self.server_state = ServerState.PREPARING
            model_preparation = _JobPreparation()
            self._preparation = model_preparation

            def prepare_model() -> Tuple[Any, Optional[Any]]:
                nonlocal aggregate_fn, evaluate_fn, algorithm_context, initial_result, job
                if model_preparation.cancelled:
                    raise FLRuntimeError("job_preparation_cancelled", "作业准备被停止")
                if not os.path.isfile(job.model_path):
                    raise FLRuntimeError(
                        "preflight_model_file_not_found",
                        f"初始模型文件不存在: {job.model_path}",
                    )
                actual_size = os.path.getsize(job.model_path)
                if actual_size <= 0:
                    raise FLRuntimeError(
                        "preflight_model_empty",
                        f"初始模型文件为空 (0 字节): {job.model_path}",
                    )
                if actual_size != job.model_size_bytes:
                    raise FLRuntimeError(
                        "preflight_model_validation_failed",
                        f"模型大小不匹配: 期望 {job.model_size_bytes} 字节，实际 {actual_size} 字节",
                    )
                digest = calculate_file_sha256(job.model_path, model_preparation.cancellation_event)
                if job.model_sha256 is not None and digest != job.model_sha256:
                    raise FLRuntimeError(
                        "preflight_model_checksum_failed",
                        f"模型 SHA-256 校验不一致: 期望 {job.model_sha256}，实际 {digest}",
                    )
                frozen_model = os.path.join(self.config.work_dir, f'job_{job.job_id}', 'artifacts', 'initial.pt')
                if model_preparation.cancelled:
                    raise FLRuntimeError('job_preparation_cancelled', '作业准备被停止')
                frozen_size, frozen_digest = archive_file(job.model_path, frozen_model, actual_size)
                if frozen_size != actual_size or frozen_digest != digest:
                    raise FLRuntimeError('preflight_model_checksum_failed', '初始模型冻结期间内容发生变化')
                job = replace(job, model_path=frozen_model, model_sha256=digest)
                if model_preparation.cancelled:
                    raise FLRuntimeError('job_preparation_cancelled', '作业准备被停止')
                load_algorithm(self.config.client_entry, arity=3)
                aggregate_fn = load_algorithm(self.config.aggregate_entry, arity=5)
                evaluate_fn = load_algorithm(self.config.evaluate_entry, arity=3)
                algorithm_context = AlgorithmContext(
                    job_id=job.job_id, node_id=255, target_nodes=tuple(job.target_nodes),
                    rounds=job.rounds,
                    artifact_dir=os.path.join(self.config.work_dir, f'job_{job.job_id}', 'artifacts'),
                    local_config={'mnist_data_dir': self.config.mnist_data_dir},
                    stop_event=model_preparation.cancellation_event,
                    phase='preparing', round_index=0)
                initial_result = validate_evaluation_result(
                    evaluate_fn(job.model_path, freeze_algorithm_config(algorithm_config), algorithm_context))
                if (not initial_result['preparation'] or initial_result['verdict'] is not None
                        or initial_result['reason'] is not None):
                    raise FLRuntimeError('invalid_initial_metrics', '初始评估须返回非空准备事实且不判定最终效果')
                if self.config.evaluate_entry == 'wfb_ng.fl.mnist:evaluate_entry':
                    from .mnist import validate_preparation_report
                    validate_preparation_report(initial_result['preparation'], expected_samples=10000,
                                                require_gpu=False, expected_seed=algorithm_config.get('data_seed'))
                algorithm_context.check_stop()
                os.makedirs(algorithm_context.artifact_dir, exist_ok=True)
                write_json_atomic(Path(algorithm_context.artifact_dir) / 'initial_evaluation.json', initial_result)
                if calculate_file_sha256(job.model_path, model_preparation.cancellation_event) != digest:
                    raise FLRuntimeError('preflight_model_checksum_failed', '初始评估期间模型被修改')
                return actual_size, digest

            model_preparation.start(prepare_model)
            model_error: Optional[BaseException] = None
            remaining_model_timeout = max(
                0.0, preparation_timeout_seconds - (time.monotonic() - preparation_started_at))
            self._lock.release()
            try:
                if not model_preparation.wait(remaining_model_timeout):
                    model_preparation.cancel_and_wait()
                    model_error = FLRuntimeError(
                        "preflight_preparation_timeout", "作业准备超过配置期限")
                elif model_preparation.cancelled:
                    model_preparation.cancel_and_wait()
                    model_error = FLRuntimeError(
                        "job_preparation_cancelled", "作业准备被停止")
                else:
                    actual_file_size, computed_sha256 = model_preparation.result()
            except BaseException as exc:
                model_preparation.cancel_and_wait()
                model_error = exc
            finally:
                self._lock.acquire()

            self._preparation = None
            if model_error is not None:
                if self.server_state == ServerState.PREPARING:
                    self.server_state = ServerState.IDLE
                raise model_error
            if self.server_state != ServerState.PREPARING or self._stop_event.is_set():
                raise FLRuntimeError("job_preparation_cancelled", "作业准备被停止")

            # All 3 preflight checks passed!
            job_dict = job.to_dict()
            job_dict["model_sha256"] = computed_sha256
            job_dict["started_at"] = time.time()
            job_dict["preparation_timeout_seconds"] = preparation_timeout_seconds

            assert algorithm_context is not None and aggregate_fn is not None and evaluate_fn is not None
            job_dict['algorithm'] = {'client_entry': self.config.client_entry,
                                     'aggregate_entry': self.config.aggregate_entry,
                                     'evaluate_entry': self.config.evaluate_entry,
                                     'config': deepcopy(algorithm_config)}
            job_dict['initial_metrics'] = deepcopy(initial_result['metrics'])
            job_dict['server_preparation'] = deepcopy(initial_result['preparation'])

            # Instantiate runtime and coordinator
            runtime = None
            server_role = None
            self.server_state = ServerState.PREPARING
            preparation = _JobPreparation()
            self._preparation = preparation

            def prepare_resources() -> Tuple[Any, Optional[Any]]:
                if preparation.cancelled:
                    raise FLRuntimeError("job_preparation_cancelled", "作业准备被停止")
                if self.runtime_factory is not None:
                    prepared_runtime = self.runtime_factory(job, payload)
                    if prepared_runtime is None:
                        raise FLRuntimeError('runtime_unavailable', 'Runtime工厂没有返回可用任务上下文')
                    preparation.register_resource(prepared_runtime)
                    if preparation.cancelled:
                        raise FLRuntimeError("job_preparation_cancelled", "作业准备被停止")
                    if preparation.cancelled:
                        preparation.close_registered_resource(prepared_runtime)
                        raise FLRuntimeError("job_preparation_cancelled", "作业准备被停止")
                    return prepared_runtime, None
                if not self.config.enable_link_process:
                    raise FLRuntimeError('runtime_unavailable', '未启用任务Runtime且未提供宿主Runtime工厂')

                from .role import ServerRole
                role_work_dir = os.path.join(self.config.work_dir, f"job_{job.job_id}_role")
                obs_path = os.path.join(role_work_dir, "observation.jsonl") if job.live_observation else None
                prepared_role = ServerRole(
                    work_dir=role_work_dir,
                    participant_node_id=tuple(job.target_nodes),
                    participant_uftp_uid=tuple(job.target_nodes),
                    server_uftp_uid=100,
                    uftp_port=uftp_port,
                    http_port=int(payload.get("server_http_port", 8080)),
                    max_update_size_bytes=int(payload.get("max_update_size_bytes", actual_file_size * 2)),
                    http_host=self.config.tun_ip,
                    uftp_bind_host=self.config.tun_ip,
                    uftp_multicast_host=str(payload.get("uftp_multicast_host", DEFAULT_UFTP_MULTICAST_HOST)),
                    uftp_private_multicast_host=str(payload.get("uftp_private_multicast_host", DEFAULT_UFTP_PRIVATE_MULTICAST_HOST)),
                    live_observation=job.live_observation,
                    observation_path=obs_path,
                    io_timeout=job.io_timeout_seconds,
                    uftp_rate_kbps=self.radio_config.uftp_rate_kbps,
                )
                preparation.register_resource(prepared_role)
                if preparation.cancelled:
                    raise FLRuntimeError("job_preparation_cancelled", "作业准备被停止")
                prepared_role.start()
                prepared_runtime = prepared_role.runtime
                if preparation.cancelled:
                    preparation.close_registered_resource(prepared_role)
                    raise FLRuntimeError("job_preparation_cancelled", "作业准备被停止")
                return prepared_runtime, prepared_role

            preparation.start(prepare_resources)
            preparation_error: Optional[BaseException] = None
            remaining_preparation_timeout = max(
                0.0, preparation_timeout_seconds - (time.monotonic() - preparation_started_at))
            self._lock.release()
            try:
                completed = preparation.wait(remaining_preparation_timeout)
                if not completed:
                    preparation.cancel_and_wait()
                    preparation_error = FLRuntimeError(
                        "preflight_preparation_timeout", "作业准备超过配置期限")
                elif preparation.cancelled:
                    preparation.cancel_and_wait()
                    preparation_error = FLRuntimeError(
                        "job_preparation_cancelled", "作业准备被停止")
                else:
                    runtime, server_role = preparation.result()
            except BaseException as exc:
                preparation.cancel_and_wait()
                preparation_error = exc
            finally:
                self._lock.acquire()

            if preparation_error is not None:
                self._preparation = None
                if self.server_state == ServerState.PREPARING:
                    self.server_state = ServerState.IDLE
                raise preparation_error
            if self.server_state != ServerState.PREPARING or self._stop_event.is_set():
                preparation.cancel_and_wait()
                self._preparation = None
                raise FLRuntimeError("job_preparation_cancelled", "作业准备被停止")
            if time.monotonic() - preparation_started_at > preparation_timeout_seconds:
                preparation.cancel_and_wait()
                self._preparation = None
                self.server_state = ServerState.IDLE
                raise FLRuntimeError("preflight_preparation_timeout", "作业准备超过配置期限")

            # 仅在最终写入 active_job 的瞬间锁住注册表，避免目标在受理边界竞态失效。
            admission_error: Optional[FLRuntimeError] = None
            with self.control_plane.registry.admission_window():
                unready_nodes = self._unready_target_nodes(job.target_nodes)
                if unready_nodes:
                    admission_error = FLRuntimeError(
                        "preflight_target_nodes_not_ready",
                        f"目标客户端不可用: {list(unready_nodes.keys())}",
                        details={"unready_nodes": unready_nodes},
                    )
                else:
                    self._recent_job = None
                    job_dict['current_round'] = 0
                    job_dict['rounds_completed'] = 0
                    job_dict['server_phase'] = 'preparing'
                    job_dict['phase_started_at'] = time.time()
                    self.active_job = job_dict
                    self._job_training_by_round = {}
                    self.server_state = ServerState.RUNNING
                    self.server_role = server_role
                    self._job_runtime = runtime
                    self._preparation = None

            if admission_error is not None:
                self.server_state = ServerState.IDLE
                preparation.cancel_and_wait()
                self._preparation = None
                raise admission_error

            accepted = {
                "status": "accepted",
                "run_id": job.run_id,
                "job_id": job.job_id,
                "mode": job.mode,
                "rounds": job.rounds,
                "target_nodes": list(job.target_nodes),
                "model_size_bytes": actual_file_size,
                "model_sha256": computed_sha256,
                "preparation_timeout_seconds": preparation_timeout_seconds,
                "round_timeout_seconds": job.round_timeout_seconds,
                "io_timeout_seconds": job.io_timeout_seconds,
            }

            def finalize_accepted_failure(reason: str, error: str) -> Dict[str, Any]:
                """把已写入 active_job 的准备错误收口为最近作业事实。"""
                self._lock.release()
                try:
                    self._finalize_job(
                        outcome="failed",
                        job_id=job.job_id,
                        reason=reason,
                        error=error,
                    )
                except Exception:
                    # 作业已受理；恢复错误保留在 recent_job，不能变成表单拒绝。
                    logger.exception("已受理作业准备失败后的资源恢复异常 (job=%s)", job.job_id)
                finally:
                    self._lock.acquire()
                return accepted

            try:
                # 广播 TASK_ANNOUNCE 下行并等待全部目标完成作业级就绪屏障。
                announce_msg = {
                    "type": "TASK_ANNOUNCE",
                    "run_id": job.run_id,
                    "job_id": job.job_id,
                    "mode": job.mode,
                    "rounds": job.rounds,
                    "target_nodes": list(job.target_nodes),
                    "round_timeout_seconds": job.round_timeout_seconds,
                    "io_timeout_seconds": job.io_timeout_seconds,
                    "live_observation": job.live_observation,
                    "model_size_bytes": actual_file_size,
                    "model_sha256": computed_sha256,
                    "preparation_timeout_seconds": preparation_timeout_seconds,
                    "server_http_host": self.config.tun_ip,
                    "server_http_port": int(payload.get("server_http_port", 8080)),
                    "uftp_port": uftp_port,
                    "link_id": self.config.link_id,
                    "algorithm": self.config.client_entry,
                    "algorithm_config": deepcopy(algorithm_config),

                    "timestamp_ms": int(time.time() * 1000),
                }
                preparation_reference: Dict[str, Any] = {}

                def validate_task_report(message: Dict[str, Any], source: Tuple[str, int]) -> None:
                    if self.config.client_entry == 'wfb_ng.fl.mnist:client_entry':
                        from .mnist import validate_preparation_report
                        report = message['report']
                        validate_preparation_report(report, expected_samples=60000, require_gpu=True,
                                                    expected_seed=algorithm_config.get('data_seed'))
                        comparable = {key: report[key] for key in
                                      ('model_format', 'sample_count', 'data_sha256', 'data_seed')}
                        if preparation_reference and comparable != preparation_reference:
                            raise FLRuntimeError('client_preparation_mismatch', '客户端训练数据或算法准备事实不一致')
                        preparation_reference.update(deepcopy(comparable))

                self.control_plane.prepare_task_readiness(job.job_id, job.target_nodes, validate_task_report)
                self.control_plane.watch_job_liveness(job.job_id, job.target_nodes, self._on_job_liveness)
                self._watch_job_role(job.job_id, server_role)
                self.control_plane.broadcast_downlink(announce_msg)
                remaining_preparation_timeout = max(
                    0.0, preparation_timeout_seconds - (time.monotonic() - preparation_started_at))
                self._lock.release()
                try:
                    task_ready = self.control_plane.wait_for_task_readiness(remaining_preparation_timeout)
                finally:
                    self._lock.acquire()
                if self.active_job is None or self.active_job.get('job_id') != job.job_id:
                    return accepted
                if not task_ready:
                    return finalize_accepted_failure(
                        'client_startup_timeout', '目标客户端未在准备期限内提交有效训练准备报告')
                reports = {node: entry['report'] for node, entry in
                           self.control_plane.get_task_preparation_reports(job.job_id).items()}
                self.active_job['client_preparation_reports'] = deepcopy(reports)
                aggregate_fn = self._wrap_mnist_aggregate(
                    aggregate_fn, job, reports)
                algorithm_context = replace(
                    algorithm_context,
                    local_config={**algorithm_context.local_config, 'client_preparation_reports': reports},
                    initial_metrics=initial_result['metrics'])
                def prepare_round(index: int) -> None:
                    self.control_plane.prepare_round_completion(job.job_id, index)

                def complete_round(index: int) -> None:
                    deadline = coordinator.current_round_deadline
                    if deadline is None:
                        raise FLRuntimeError('round_not_started', '轮次完成屏障缺少 Coordinator 期限')
                    remaining = max(0.0, deadline - time.monotonic())
                    if not self.control_plane.wait_for_round_completion(job.job_id, index, remaining):
                        raise FLRuntimeError('round_report_timeout', '未在本轮期限内收齐提交完成报告')

                # Broadcast SSE event
                self.publish_event({
                    "type": "JOB_STARTED",
                    "job": job_dict,
                    "timestamp": time.time(),
                })

                if runtime is not None:
                    coordinator = FLCoordinator(
                        job=job,
                        runtime=runtime,
                        work_dir=os.path.join(self.config.work_dir, f"job_{job.job_id}"),
                        on_event=self.publish_event,
                        on_completed=lambda summary: self._on_job_completed(job.job_id, summary),
                        on_failed=lambda exc: self._on_job_failed(job.job_id, exc),
                        aggregate_fn=aggregate_fn, evaluate_fn=evaluate_fn,
                        algorithm_config=algorithm_config, context=algorithm_context,
                        initial_metrics=initial_result['metrics'],
                        prepare_round=prepare_round, round_completion=complete_round,
                        cancel_callback=lambda cause: self._on_coordinator_cancel(job.job_id, cause),
                    )
                    self.coordinator = coordinator
                    coordinator.start()

                logger.info(
                    "作业 %s 成功通过前置门禁并启动 (模式: %s, 轮次: %d, 节点: %s, 模型大小: %d B)",
                    job.job_id,
                    job.mode,
                    job.rounds,
                    list(job.target_nodes),
                    actual_file_size,
                )
                return accepted
            except Exception as exc:
                return finalize_accepted_failure(getattr(exc, 'error_code', 'job_preparation_failed'), str(exc))

    def _refresh_job_recovery(self) -> None:
        """恢复报告可迟到；它们只更新资源事实，不能改写首次执行／效果终态。"""
        recent = self._recent_job
        if recent is None or 'server_recovery_complete' not in recent:
            return
        reports = self.control_plane.get_job_recovery_reports(recent['job_id'])
        recent['recovery_reports'] = deepcopy(reports)
        for node, envelope in reports.items():
            report = envelope.get('report', {}) if isinstance(envelope, dict) else {}
            if not isinstance(report, dict) or 'recovery_epoch' not in report:
                continue
            history = recent.setdefault('recovery_report_history', {}).setdefault(node, [])
            epoch = report['recovery_epoch']
            if not any(entry.get('report', {}).get('recovery_epoch') == epoch for entry in history):
                history.append(deepcopy(envelope))
        missing = sorted(node for node in recent['target_nodes']
                         if node not in reports or not isinstance(reports[node], dict)
                         or reports[node].get('report', {}).get('recovery_state') != 'ready')
        recent['recovery_missing_nodes'] = missing
        if not recent.get('recovery_wait_finished'):
            return
        blocked = any(isinstance(entry, dict) and entry.get('report', {}).get('recovery_state') == 'blocked'
                      for entry in reports.values())
        state = ('blocked' if recent.get('recovery_error') or blocked
                 or not recent.get('server_recovery_complete') else
                 'unconfirmed' if missing else 'ready')
        recent['recovery_state'] = state
        recent['server_phase'] = 'ready' if state == 'ready' else 'recovery_' + state

    def _training_facts_for_summary(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """复制 Coordinator 摘要后附加宿主读取的有限训练事实。"""
        result = deepcopy(summary)
        rounds = result.get('rounds')
        if not isinstance(rounds, list):
            return result
        for row in rounds:
            if not isinstance(row, dict) or type(row.get('round_index')) is not int:
                continue
            facts = self._job_training_by_round.get(row['round_index'])
            if facts:
                row['training'] = deepcopy(facts)
        return result

    def _on_training_round_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """把训练事实合入宿主快照；输入事件和 Coordinator 内部对象不被修改。"""
        copied = deepcopy(event)
        index = copied.get('round_index')
        facts = self._job_training_by_round.get(index) if type(index) is int else None
        if facts and copied.get('type') == 'ROUND_COMPLETED':
            copied['summary'] = deepcopy(copied.get('summary', {}))
            copied['summary']['training'] = deepcopy(facts)
        return copied

    def _wrap_mnist_aggregate(
        self,
        aggregate_fn: Callable[..., Any],
        job: JobConfig,
        preparation_reports: Dict[int, Any],
    ) -> Callable[..., Any]:
        """部署 MNIST 聚合成功后读取固定 update 的有限 metadata；generic 不解析。"""
        if self.config.aggregate_entry != 'wfb_ng.fl.mnist:aggregate_entry':
            return aggregate_fn

        def aggregate_with_training_facts(current: str, updates: Any, output: str,
                                          config: Any, context: Any) -> Any:
            result = aggregate_fn(current, updates, output, config, context)
            from .mnist import read_update_metadata
            base_sha = calculate_file_sha256(current)
            expected = {
                node: report.get('sample_count') if isinstance(report, dict) else None
                for node, report in preparation_reports.items()
            }
            facts: List[Dict[str, Any]] = []
            self._job_training_by_round[context.round_index] = facts
            for node in sorted(job.target_nodes):
                expected_samples = expected.get(node)
                if type(expected_samples) is not int:
                    raise FLRuntimeError('invalid_mnist_preparation', '聚合缺少节点样本准备事实')
                facts.append(read_update_metadata(
                    updates[node], node_id=node, round_index=context.round_index,
                    base_model_sha256=base_sha, expected_sample_count=expected_samples))
            self._job_training_by_round[context.round_index] = deepcopy(facts)
            return result

        return aggregate_with_training_facts

    def _finalize_job(
        self,
        outcome: str,
        job_id: Optional[str] = None,
        reason: str = "completed",
        summary: Optional[Dict[str, Any]] = None,
        error: str = "",
    ) -> Dict[str, Any]:
        """首次终态冻结结果；取消屏障、等待实际计算退出后才回收角色并确认恢复。"""
        reason = reason or outcome
        with self._lock:
            if self.active_job is None or (job_id is not None and self.active_job['job_id'] != job_id):
                if self._recent_job is not None and (job_id is None or self._recent_job['job_id'] == job_id):
                    if error and (reason != self._recent_job.get('reason') or error != self._recent_job.get('error')):
                        diagnostic = {'reason': reason, 'error': error}
                        diagnostics = self._recent_job.setdefault('additional_errors', [])
                        if diagnostic not in diagnostics:
                            diagnostics.append(diagnostic)
                    return deepcopy(self._recent_job['terminal_result'])
                return {'status': 'ignored', 'job_id': job_id}
            if self.server_state == ServerState.STOPPED:
                return {'status': 'ignored', 'job_id': job_id}
            job_id = self.active_job['job_id']
            first_failure = self.active_job.get('first_failure')
            if first_failure is not None:
                outcome, reason, error = 'failed', first_failure['reason'], first_failure['error']
            terminal_summary = summary if summary is not None else self.active_job.get('summary', {})
            effect_failed = terminal_summary.get('error_code') == 'effect_criterion_not_met'
            if outcome == 'completed' or (outcome == 'failed' and effect_failed):
                if terminal_summary.get('execution_result') != 'succeeded':
                    raise FLRuntimeError('invalid_coordinator_summary', '完整执行终态缺少明确的执行成功事实')
                execution = 'succeeded'
            elif outcome in ('failed', 'aborted'):
                execution = outcome
            else:
                raise FLRuntimeError('invalid_job_outcome', '未知作业终态')
            result = {'status': outcome, 'job_id': job_id, 'reason': reason}
            recent = deepcopy(self.active_job)
            recent.update(execution_result=execution, recovery_state='recovering',
                          server_phase='recovering', phase_started_at=time.time(), reason=reason,
                          error=error, terminal_result=result, recovery_wait_finished=False,
                          server_recovery_complete=False,
                          recovery_missing_nodes=list(recent['target_nodes']))
            if terminal_summary:
                recent['summary'] = self._training_facts_for_summary(terminal_summary)
                recent['rounds_completed'] = terminal_summary.get('rounds_completed', recent['rounds_completed'])
                if 'effect_result' in terminal_summary:
                    recent['effect_result'] = deepcopy(terminal_summary['effect_result'])
            self._recent_job = recent
            coord, role, runtime = self.coordinator, self.server_role, self._job_runtime
            self.active_job = None
            self.server_state = ServerState.ABORTING
            self._terminal_done.clear()
            self._job_monitor_stop.set()
            self.control_plane.unwatch_job_liveness(job_id)
            self.console.record_event({'type': 'RECOVERY_STARTED', 'job_id': job_id})

        terminal_type = 'JOB_COMPLETED' if outcome == 'completed' else 'JOB_ABORT'
        message = {'type': terminal_type, 'job_id': job_id, 'timestamp_ms': int(time.time() * 1000)}
        if outcome != 'completed':
            message['reason'] = reason or error
        recovery_deadline = time.monotonic() + recent['io_timeout_seconds']
        delivery_thread: Optional[threading.Thread] = None

        def deliver_terminal() -> None:
            # 与本地退出并行；单次发送失败或部分 blocked 都不能漏停其他目标。
            while not self._stop_event.is_set() and time.monotonic() < recovery_deadline:
                retry_at = min(recovery_deadline, time.monotonic() + 0.25)
                try:
                    self.control_plane.broadcast_downlink(message)
                    with self._lock:
                        recent['terminal_delivery_succeeded'] = True
                except Exception as exc:
                    with self._lock:
                        recent.setdefault('terminal_delivery_errors', []).append({
                            'error_code': getattr(exc, 'error_code', 'TERMINAL_BROADCAST_FAILED'),
                            'reason': str(exc),
                        })
                    logger.warning('作业终态发送失败，预算内重试 (job=%s): %s', job_id, exc)
                try:
                    ready = self.control_plane.wait_for_job_recovery(
                        job_id, max(0.0, retry_at - time.monotonic()))
                    with self._lock:
                        self._refresh_job_recovery()
                    if ready:
                        return
                except Exception as exc:
                    with self._lock:
                        recent.setdefault('terminal_delivery_errors', []).append({
                            'error_code': getattr(exc, 'error_code', 'RECOVERY_WAIT_FAILED'),
                            'reason': str(exc),
                        })
                # blocked epoch 可以提前唤醒控制面等待；仍等到同一重试时点，避免忙循环。
                self._stop_event.wait(max(0.0, retry_at - time.monotonic()))

        try:
            # 先为客户端恢复事实留门；投递线程不以某次 send 成功为建立条件。
            self.control_plane.prepare_job_recovery(job_id, recent['target_nodes'])
        except Exception as exc:
            with self._lock:
                recent['recovery_error'] = getattr(exc, 'error_code', 'RECOVERY_PREPARATION_FAILED')
                recent['recovery_reason'] = str(exc)
            logger.exception('作业恢复屏障建立失败 (job=%s)', job_id)
        delivery_thread = threading.Thread(target=deliver_terminal,
                                           name=f'fl-terminal-delivery-{job_id}', daemon=True)
        delivery_thread.start()

        try:
            if coord is not None and outcome in ('aborted', 'failed'):
                coord.abort(reason=reason)
            self.control_plane.cancel_job_barriers(job_id, reason)
            if coord is not None:
                # 无限 join 只表示等待真实退出，不能用有限等待伪造退出证明。
                coord.wait(timeout=None)
                coordinator_summary = coord.get_summary()
                with self._lock:
                    if coordinator_summary:
                        recent['summary'] = self._training_facts_for_summary(coordinator_summary)
                        recent['rounds_completed'] = coordinator_summary.get('rounds_completed', recent['rounds_completed'])
            monitor = self._job_monitor_thread
            if monitor is not None and monitor is not threading.current_thread():
                monitor.join()
            with self._lock:
                self.coordinator = None
            if role is not None:
                role.close()
            elif runtime is not None:
                runtime.close()
            with self._lock:
                self.server_role = None
                self._job_runtime = None
                recent['server_recovery_complete'] = True
        except Exception as exc:
            logger.exception('作业终态资源恢复失败 (job=%s)', job_id)
            with self._lock:
                recent['recovery_error'] = getattr(exc, 'error_code', 'RESOURCE_RECOVERY_FAILED')
                recent['recovery_reason'] = str(exc)
        finally:
            if delivery_thread is not None:
                delivery_thread.join()
            with self._lock:
                recent['recovery_wait_finished'] = True
                if (recent.get('terminal_delivery_errors')
                        and not recent.get('terminal_delivery_succeeded')):
                    recent.setdefault('recovery_error', 'TERMINAL_BROADCAST_FAILED')
                if self._stop_event.is_set():
                    recent['recovery_error'] = 'DAEMON_STOPPED'
                self._refresh_job_recovery()
                if not self._stop_event.is_set():
                    self.server_state = ServerState.IDLE
                self.console.record_event({'type': 'RECOVERY_COMPLETED' if recent['recovery_state'] == 'ready'
                                           else 'RECOVERY_BLOCKED', 'job_id': job_id})
            self._terminal_done.set()

        self.publish_event({'type': {'completed': 'JOB_COMPLETED', 'failed': 'JOB_FAILED',
                                     'aborted': 'JOB_ABORTED'}[outcome],
                            'job_id': job_id, 'summary': deepcopy(terminal_summary),
                            'reason': reason, 'error': error})
        return deepcopy(result)

    def _schedule_job_terminal(self, **kwargs: Any) -> None:
        # UDP 接收和 Coordinator 回调只调度；关闭角色前必须从别的线程 join 计算。
        threading.Thread(target=self._finalize_job, kwargs=kwargs,
                         name=f"fl-terminal-{kwargs['job_id']}", daemon=True).start()

    def _on_coordinator_cancel(self, job_id: str, cause: str) -> None:
        """Coordinator 首因立即冻结，取消屏障后异步等待计算；不能在回调中 join。"""
        self.control_plane.cancel_job_barriers(job_id, cause)
        with self._lock:
            if self.active_job is None or self.active_job['job_id'] != job_id:
                return
            self.active_job.setdefault('first_failure', {'reason': cause, 'error': cause})
        self._schedule_job_terminal(outcome='failed', job_id=job_id, reason=cause, error=cause)

    def _on_job_completed(self, job_id: str, summary: Dict[str, Any]) -> None:
        if isinstance(summary, dict):
            summary = self._training_facts_for_summary(summary)
        self._schedule_job_terminal(outcome="completed", job_id=job_id, summary=summary)

    def _on_job_failed(self, job_id: str, exc: Exception) -> None:
        self._schedule_job_terminal(
            outcome="failed",
            job_id=job_id,
            error=str(exc),
            reason=getattr(exc, 'error_code', 'job_failed'),
        )

    def abort_job(self, reason: str = "user_requested") -> Dict[str, Any]:
        """停止准备或活动作业，保留首次终态与独立恢复事实。"""
        with self._lock:
            preparation = self._preparation if self.active_job is None else None
        if preparation is not None:
            preparation.cancel_and_wait()
            return {'status': 'aborted', 'job_id': None, 'reason': reason}
        return self._finalize_job(outcome="aborted", reason=reason)

    def _apply_radio_runtime(self, config: RadioConfig) -> None:
        """在线应用完整发射配置，保留链路进程、TUN 和调度会话。"""
        with self._lock:
            if self.server_state == ServerState.STOPPED:
                raise FLRuntimeError('daemon_stopped', 'Server 已停止，无法应用射频')
            if self.config.enable_link_process:
                if self._link_process is None or self._link_process.poll() is not None:
                    raise FLRuntimeError('link_not_running', '链路进程未运行，不能确认射频应用')
                apply_link_radio(self.config.radio_control_port, config.bandwidth_mhz, config.downlink_mcs)
                self._link_radio_config = config

    def _start_link_process(self, radio: Optional[RadioConfig] = None) -> None:
        """Launch wfb_v6_uplink server background process with pre-allocated slots."""
        radio = radio or self.radio_config
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
            channel=radio.channel,
            downlink_mcs=radio.downlink_mcs,
            link_id=self.config.link_id,
            known_clients=self.config.known_clients,
            bandwidth_mhz=radio.bandwidth_mhz,
            radio_control_port=self.config.radio_control_port,
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

        try:
            # Wait for link-up, configured address and broadcast routing, not just TUNSETIFF.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                rc = self._link_process.poll()
                if rc is not None:
                    raise FLRuntimeError("link_process_start_failed", f"wfb_v6_uplink 启动期异常退出，退出码: {rc}")
                if self.adapter.is_tun_ready(
                        self.config.tun_name, self.config.tun_cidr, self.control_plane.broadcast_addr):
                    break
                time.sleep(0.05)

            else:
                raise FLRuntimeError(
                    "link_tun_failed",
                    f"wfb_v6_uplink TUN 地址或控制路由未就绪: {self.config.tun_name}",
                )
        except Exception:
            self._stop_link_process()
            raise

        self._link_radio_config = radio
        try:
            self.adapter.set_tun_txqueuelen(self.config.tun_name, self.config.tun_txqueuelen)
            logger.info("已设置 TUN %s txqueuelen=%d", self.config.tun_name, self.config.tun_txqueuelen)
        except Exception as exc:
            logger.warning("设置 TUN txqueuelen 失败: %s", exc)

    def _stop_link_process(self) -> None:
        """Safely terminate wfb_v6_uplink process, close log file, and clean up TUN."""
        self._link_radio_config = None
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
                width = self.radio_config.channel_width
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
            self._web.start()

    def stop(self) -> None:
        """Stop all background workers, sockets, and processes."""
        with self._lock:
            if self._stop_event.is_set():
                return

            self._stop_event.set()
            terminal_in_progress = not self._terminal_done.is_set()
            self._job_monitor_stop.set()
            stopping_job = self.active_job or self._recent_job
            if stopping_job is not None:
                self.control_plane.cancel_job_barriers(stopping_job['job_id'], 'daemon_stopped')
                self.control_plane.unwatch_job_liveness(stopping_job['job_id'])
            if self.active_job is not None:
                self._recent_job = deepcopy(self.active_job)
                first_failure = self.active_job.get('first_failure')
                execution = 'failed' if first_failure else 'aborted'
                reason = first_failure['reason'] if first_failure else 'daemon_stopped'
                self._recent_job.update(execution_result=execution, recovery_state="blocked",
                                        reason=reason, recovery_error="DAEMON_STOPPED",
                                        terminal_result={'status': execution, 'job_id': self.active_job['job_id'],
                                                         'reason': reason})
            elif self._recent_job and self._recent_job["recovery_state"] == "recovering":
                self._recent_job.update(recovery_state="blocked", recovery_error="DAEMON_STOPPED")
            self.server_state = ServerState.STOPPED

            httpd = self._httpd
            http_thread = self._http_thread
            coord = None if terminal_in_progress else self.coordinator
            role_to_close = None if terminal_in_progress else self.server_role
            runtime_to_close = None if terminal_in_progress else self._job_runtime
            preparation_to_cancel = self._preparation

            self._httpd = None
            self._http_thread = None
            self.active_job = None
            self._preparation = None
            self.control_plane.cancel_task_readiness()
            self._best_effort_console_refresh()

        if preparation_to_cancel is not None:
            preparation_to_cancel.cancel_and_wait()

        # Stop HTTP REST Server outside lock
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

        if http_thread is not None:
            http_thread.join(timeout=1.0)

        try:
            self._web.stop()
        except Exception:
            logger.exception("管理 Web 停止失败，继续清理核心资源")

        # Stop Control Plane outside lock
        self.control_plane.stop()
        self._terminal_done.wait()

        # Stop active coordinator and server role outside lock
        computation_exited = True
        if coord is not None:
            try:
                coord.abort(reason="daemon_stopped")
                coord.wait(timeout=None)
                with self._lock:
                    self.coordinator = None
                    if self._recent_job is not None and not self._recent_job.get('summary'):
                        self._recent_job['summary'] = coord.get_summary()
            except Exception as exc:
                computation_exited = False
                logger.warning("停止 coordinator 失败，保留任务资源: %s", exc)

        monitor = self._job_monitor_thread
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join()
        if computation_exited and (role_to_close is not None or runtime_to_close is not None):
            try:
                if role_to_close is not None:
                    role_to_close.close()
                elif runtime_to_close is not None:
                    runtime_to_close.close()
                with self._lock:
                    self.server_role = None
                    self._job_runtime = None
            except Exception as exc:
                logger.warning("关闭 server_role 失败: %s", exc)

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
    parser.add_argument("--bandwidth-mhz", type=int, choices=(20, 40), default=None)
    parser.add_argument("--uftp-rate-kbps", type=int, default=None)
    parser.add_argument("--radio-control-port", type=int, default=None)
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
    if args.bandwidth_mhz is not None:
        overrides["bandwidth_mhz"] = args.bandwidth_mhz
    if args.uftp_rate_kbps is not None:
        overrides["uftp_rate_kbps"] = args.uftp_rate_kbps
    if args.radio_control_port is not None:
        overrides["radio_control_port"] = args.radio_control_port
    if args.interface is not None:
        overrides["air_interface"] = args.interface
    if args.tun_name is not None:
        overrides["tun_name"] = args.tun_name

    if overrides:
        radio_keys = {"channel", "bandwidth_mhz", "radio_txpower_dbm", "downlink_mcs", "uplink_mcs", "uftp_rate_kbps"}
        base_radio = validate_radio_config({key: value for key, value in config.__dict__.items() if key in radio_keys})
        target_radio = validate_radio_patch({key: value for key, value in overrides.items() if key in radio_keys}, base_radio)
        merged = {**config.__dict__, **overrides,
                  **{key: value for key, value in target_radio.to_dict().items() if key in radio_keys}}
        config = ServerDaemonConfig(**merged)

    daemon = ServerDaemon(config=config)
    daemon.run()


if __name__ == "__main__":
    main()
