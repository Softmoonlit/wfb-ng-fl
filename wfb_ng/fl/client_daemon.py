#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
wfb_ng.fl.client_daemon: Client Persistent Daemon, Hardware Polling, and RoleService Sandbox.

Per ADR-0010, ADR-0012, ADR-0014:
- Persistent systemd daemon: wfb-fl-client-daemon.
- Identity: /etc/wfb-ng-fl/node.json (node_id 1..10, static tun_ip 10.80.0.{10+node_id}).
- Hardware Polling: 3s suspended retry if wl* wireless interface not detected; auto hot takeover.
- Hardware Configuration: Monitor mode, Channel 157 (HT40+), TX power (12 dBm), TUN setup/cleanup.
- Sandbox: Short-lifecycle RoleService(role='client') spawned via subprocess.Popen in isolated process group.
- Resource Audit: Strict cleanup of child processes (SIGTERM/SIGKILL pgid) and TUN network devices upon completion/abort.
"""

import argparse
import ipaddress
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence

from .artifacts import validate_path_safe_identifier
from .errors import FLRuntimeError
from .transport import (
    DEFAULT_UFTP_DATA_PORT,
    DEFAULT_UFTP_MULTICAST_HOST,
    DEFAULT_UFTP_PRIVATE_MULTICAST_HOST,
)
from .control import (
    CLIENT_UPLINK_DEFAULT_ADDR,
    CLIENT_UPLINK_DEFAULT_PORT,
    SERVER_CONTROL_BROADCAST_PORT,
    ClientNodeState,
    ControlPlaneClient,
)
from .radio import (
    ALLOWED_5GHZ_CHANNELS,
    DEFAULT_CHANNEL,
    DEFAULT_TXPOWER_DBM,
    FIXED_BANDWIDTH,
    FORBIDDEN_CHANNELS,
    RECOMMENDED_UPLINK_MCS,
    find_wl_interfaces,
    validate_radio_config,
)

logger = logging.getLogger("wfb_fl_client_daemon")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [client-daemon] %(message)s",
    )

DEFAULT_NODE_CONFIG_PATH = "/etc/wfb-ng-fl/node.json"
DEFAULT_POLL_INTERVAL_SECONDS = 3.0
DEFAULT_TUN_PREFIX = "fl-c"
DEFAULT_WORK_DIR = "/tmp/wfb-ng-fl/client"
DEFAULT_LINK_ID = 7669206


def build_v6_uplink_client_command(
    executable_path: str,
    node_id: int,
    tun_name: str,
    tun_addr: str,
    air_interface: str,
    uplink_mcs: int,
    link_id: int,
    channel_width: str = FIXED_BANDWIDTH,
) -> List[str]:
    """Build the persistent client link command used by the control plane."""
    return [
        executable_path,
        "--role", "client",
        "--tun-name", tun_name,
        "--tun-addr", tun_addr,
        "--node-id", str(node_id),
        "--link-id", str(link_id),
        "--uplink-stream", "1",
        "--downlink-stream", "2",
        "--fec-k", "8",
        "--fec-n", "14",
        "--radio-bandwidth", "40" if "40" in channel_width else "20",
        "--radio-mcs-index", str(uplink_mcs),
        "--radio-short-gi",
        "--air-interface", air_interface,
        "--uplink-pause-threshold-bytes", "131072",
        "--uplink-resume-threshold-bytes", "65536",
        "--uplink-queue-packets-limit", "64",
    ]


class DaemonState(str, Enum):
    POLLING_HARDWARE = "POLLING_HARDWARE"
    HUNTING = "HUNTING"
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    ABORTING = "ABORTING"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class NodeIdentity:
    node_id: int
    tun_ip: str
    tun_cidr: str


def load_node_identity(path: str = DEFAULT_NODE_CONFIG_PATH) -> NodeIdentity:
    """
    Load and strictly validate client node identity from /etc/wfb-ng-fl/node.json.
    - node_id must be an integer in range [1, 10].
    - tun_ip is required and must strictly match the static schema 10.80.0.{10+node_id}/24.
    Fails closed with FLRuntimeError('invalid_node_identity', ...) on any defect.
    """
    if not os.path.exists(path):
        raise FLRuntimeError(
            "invalid_node_identity", f"节点身份配置文件不存在: {path}"
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        raise FLRuntimeError(
            "invalid_node_identity", f"无法解析节点身份配置文件 {path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise FLRuntimeError(
            "invalid_node_identity", f"节点身份配置结构无效，必须为 JSON object: {path}"
        )

    raw_node_id = data.get("node_id")
    if type(raw_node_id) is not int:
        raise FLRuntimeError(
            "invalid_node_identity",
            f"node_id 必须为整数，实际为: {raw_node_id!r}",
        )
    if not (1 <= raw_node_id <= 10):
        raise FLRuntimeError(
            "invalid_node_identity",
            f"node_id 超出系统支持范围 [1, 10]，实际为: {raw_node_id}",
        )

    raw_tun_ip = data.get("tun_ip")
    if raw_tun_ip is None:
        raise FLRuntimeError(
            "invalid_node_identity",
            f"节点身份配置缺少必填字段 'tun_ip': {path}",
        )
    if not isinstance(raw_tun_ip, str):
        raise FLRuntimeError(
            "invalid_node_identity",
            f"tun_ip 必须为字符串，实际为: {raw_tun_ip!r}",
        )
    try:
        if "/" in raw_tun_ip:
            interface = ipaddress.IPv4Interface(raw_tun_ip)
            if interface.network.prefixlen != 24:
                raise FLRuntimeError(
                    "invalid_node_identity",
                    f"tun_ip 子网掩码必须为 /24，实际为: /{interface.network.prefixlen}",
                )
            tun_ip_str = str(interface.ip)
            tun_cidr_str = f"{tun_ip_str}/24"
        else:
            addr = ipaddress.IPv4Address(raw_tun_ip)
            tun_ip_str = str(addr)
            tun_cidr_str = f"{tun_ip_str}/24"
    except ValueError as exc:
        raise FLRuntimeError(
            "invalid_node_identity", f"tun_ip 地址格式非法: {raw_tun_ip}"
        ) from exc

    # Enforce static mapping contract: 10.80.0.{10+node_id} (Memory #66)
    expected_ip = f"10.80.0.{10 + raw_node_id}"
    if tun_ip_str != expected_ip:
        raise FLRuntimeError(
            "invalid_node_identity",
            f"节点 {raw_node_id} 的 tun_ip ({tun_ip_str}) 不符合静态分配契约 (必须为 {expected_ip})",
        )

    return NodeIdentity(
        node_id=raw_node_id, tun_ip=tun_ip_str, tun_cidr=tun_cidr_str
    )


class NetworkAdapter:
    """Interface for system wireless and TUN network operations."""

    def find_interfaces(self) -> List[str]:
        raise NotImplementedError

    def configure_wireless(
        self,
        iface: str,
        channel: int = DEFAULT_CHANNEL,
        channel_width: str = FIXED_BANDWIDTH,
        txpower_dbm: int = DEFAULT_TXPOWER_DBM,
    ) -> None:
        raise NotImplementedError

    def set_channel(
        self,
        iface: str,
        channel: int,
        channel_width: str = FIXED_BANDWIDTH,
    ) -> None:
        raise NotImplementedError

    def set_txpower(self, iface: str, txpower_dbm: int) -> None:
        raise NotImplementedError

    def setup_tun(self, tun_name: str, tun_cidr: str) -> None:
        raise NotImplementedError

    def teardown_tun(self, tun_name: str) -> None:
        raise NotImplementedError

    def is_tun_active(self, tun_name: str) -> bool:
        raise NotImplementedError

    def set_tun_txqueuelen(self, tun_name: str, txqueuelen: int = 5000) -> None:
        pass


class LinuxNetworkAdapter(NetworkAdapter):
    """Production Linux network adapter using system netlink, iw, and sysfs."""

    def __init__(self, use_sudo: bool = True):
        self.use_sudo = use_sudo and (os.geteuid() != 0)

    def _run_cmd(self, cmd: Sequence[str]) -> subprocess.CompletedProcess:
        full_cmd = list(cmd)
        if self.use_sudo:
            full_cmd = ["sudo"] + full_cmd
        res = subprocess.run(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise FLRuntimeError(
                "hardware_configuration_failed",
                f"执行命令 {' '.join(full_cmd)} 失败: {res.stderr.strip()}",
            )
        return res

    def find_interfaces(self) -> List[str]:
        return find_wl_interfaces()

    def configure_wireless(
        self,
        iface: str,
        channel: int = DEFAULT_CHANNEL,
        channel_width: str = FIXED_BANDWIDTH,
        txpower_dbm: int = DEFAULT_TXPOWER_DBM,
    ) -> None:
        validate_radio_config({
            "channel": channel,
            "radio_txpower_dbm": txpower_dbm,
        })
        if channel == 165:
            channel_width = "HT20"

        # 1. ip link set <iface> down
        self._run_cmd(["ip", "link", "set", iface, "down"])
        # 2. iw dev <iface> set type monitor
        self._run_cmd(["iw", "dev", iface, "set", "type", "monitor"])
        # 3. ip link set <iface> up
        self._run_cmd(["ip", "link", "set", iface, "up"])
        # 4. iw dev <iface> set channel <channel> <channel_width>
        self.set_channel(iface, channel, channel_width)

        # 5. TX power setting
        self.set_txpower(iface, txpower_dbm)

    def set_channel(
        self,
        iface: str,
        channel: int,
        channel_width: str = FIXED_BANDWIDTH,
    ) -> None:
        validate_radio_config({"channel": channel})
        if channel == 165:
            channel_width = "HT20"
        self._run_cmd(["iw", "dev", iface, "set", "channel", str(channel), channel_width])

    def set_txpower(self, iface: str, txpower_dbm: int) -> None:
        validate_radio_config({"radio_txpower_dbm": txpower_dbm})
        override_path = "/sys/module/88XXau_wfb/parameters/rtw_tx_pwr_idx_override"
        if os.path.exists(override_path):
            try:
                cmd = ["tee", override_path]
                if self.use_sudo:
                    cmd = ["sudo"] + cmd
                subprocess.run(
                    cmd,
                    input=f"{txpower_dbm}\n",
                    text=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception as exc:
                logger.warning("写入 rtw_tx_pwr_idx_override 失败: %s", exc)

        negative_mbm = -(txpower_dbm * 100)
        self._run_cmd(["iw", "dev", iface, "set", "txpower", "fixed", str(negative_mbm)])

    def setup_tun(self, tun_name: str, tun_cidr: str) -> None:
        self.teardown_tun(tun_name)
        self._run_cmd(["ip", "tuntap", "add", "dev", tun_name, "mode", "tun"])
        self._run_cmd(["ip", "addr", "add", tun_cidr, "dev", tun_name])
        self._run_cmd(["ip", "link", "set", tun_name, "up"])

    def teardown_tun(self, tun_name: str) -> None:
        if self.is_tun_active(tun_name):
            try:
                self._run_cmd(["ip", "link", "del", tun_name])
            except Exception as exc:
                logger.warning("清理 TUN 接口 %s 失败: %s", tun_name, exc)

    def is_tun_active(self, tun_name: str) -> bool:
        return os.path.exists(f"/sys/class/net/{tun_name}")

    def set_tun_txqueuelen(self, tun_name: str, txqueuelen: int = 5000) -> None:
        self._run_cmd(["ip", "link", "set", "dev", tun_name, "txqueuelen", str(txqueuelen)])


@dataclass(frozen=True)
class ClientDaemonConfig:
    node_id: int
    tun_ip: str
    tun_cidr: str = ""
    tun_name: str = ""
    channel: int = DEFAULT_CHANNEL
    channel_width: str = FIXED_BANDWIDTH
    radio_txpower_dbm: int = DEFAULT_TXPOWER_DBM
    uplink_mcs: int = RECOMMENDED_UPLINK_MCS
    work_dir: str = DEFAULT_WORK_DIR
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    interface: Optional[str] = None
    server_control_host: str = CLIENT_UPLINK_DEFAULT_ADDR
    server_control_port: int = CLIENT_UPLINK_DEFAULT_PORT
    broadcast_port: int = SERVER_CONTROL_BROADCAST_PORT
    link_id: int = DEFAULT_LINK_ID
    enable_control_plane: bool = True
    enable_link_process: bool = True

    def __post_init__(self):
        if not self.tun_cidr:
            object.__setattr__(self, "tun_cidr", f"{self.tun_ip}/24")
        if not self.tun_name:
            object.__setattr__(self, "tun_name", f"{DEFAULT_TUN_PREFIX}{self.node_id}")
        validate_radio_config({
            "channel": self.channel,
            "radio_txpower_dbm": self.radio_txpower_dbm,
            "uplink_mcs": self.uplink_mcs,
        })
        if type(self.link_id) is not int or self.link_id <= 0:
            raise ValueError("link_id 必须为正整数")


@dataclass(frozen=True)
class ClientJobConfig:
    job_id: str
    run_id: str
    node_id: int
    tun_name: str
    tun_ip: str
    io_timeout_seconds: int
    live_observation: bool
    server_http_host: str = "10.80.0.1"
    server_http_port: int = 8080
    uftp_port: int = DEFAULT_UFTP_DATA_PORT
    link_id: int = DEFAULT_LINK_ID
    uftp_bind_host: Optional[str] = None
    uftp_multicast_host: str = DEFAULT_UFTP_MULTICAST_HOST
    uftp_private_multicast_host: str = DEFAULT_UFTP_PRIVATE_MULTICAST_HOST
    channel: int = DEFAULT_CHANNEL
    channel_width: str = FIXED_BANDWIDTH
    radio_txpower_dbm: int = DEFAULT_TXPOWER_DBM
    uplink_mcs: int = RECOMMENDED_UPLINK_MCS
    max_update_size_bytes: int = 1073741824
    algorithm: Optional[str] = None
    algorithm_config: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        validate_path_safe_identifier(self.run_id, "run_id")
        validate_path_safe_identifier(self.job_id, "job_id")
        if type(self.io_timeout_seconds) is not int or self.io_timeout_seconds <= 0:
            raise FLRuntimeError("invalid_io_timeout", "io_timeout_seconds 必须为正整数")
        if type(self.live_observation) is not bool:
            raise FLRuntimeError("invalid_live_observation", "live_observation 必须为布尔值")
        if self.uftp_bind_host is None:
            # Strip CIDR prefix if present
            clean_ip = self.tun_ip.split("/")[0]
            object.__setattr__(self, "uftp_bind_host", clean_ip)
        validate_radio_config({
            "channel": self.channel,
            "radio_txpower_dbm": self.radio_txpower_dbm,
            "uplink_mcs": self.uplink_mcs,
        })
        if type(self.link_id) is not int or self.link_id <= 0:
            raise ValueError("link_id 必须为正整数")


class JobSandbox:
    """
    Subprocess sandbox for RoleService(role='client').
    Manages transient execution lifecycle:
    - Generates isolated role configuration JSON in a fresh sandbox work directory.
    - Spawns child process in its own session / process group (start_new_session=True).
    - Monitored polling and waiting.
    - Guaranteed cleanup: SIGTERM -> SIGKILL to process group, TUN deletion, and workspace cleanup.
    """

    def __init__(
        self,
        work_dir: str,
        network_adapter: NetworkAdapter,
        _command_prefix: Optional[Sequence[str]] = None,
        _require_ready_notification: bool = True,
    ):
        self.work_dir = os.path.abspath(work_dir)
        self.network_adapter = network_adapter
        self._command_prefix = list(_command_prefix) if _command_prefix else None
        self._require_ready_notification = _require_ready_notification
        self._process: Optional[subprocess.Popen] = None
        self._pgid: Optional[int] = None
        self._log_file: Optional[Any] = None
        self._active_job: Optional[ClientJobConfig] = None
        self._job_work_dir: Optional[str] = None
        self._lock = threading.RLock()
        self.state = DaemonState.IDLE

    @property
    def is_running(self) -> bool:
        with self._lock:
            if self._process is None:
                return False
            ret = self._process.poll()
            if ret is not None:
                self._finalize(ret)
                return False
            return True

    def start(self, job: ClientJobConfig, air_interface: str) -> subprocess.Popen:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise FLRuntimeError(
                    "sandbox_already_running",
                    f"已有作业正在运行 (PID={self._process.pid})，拒绝并发执行",
                )

            self.state = DaemonState.PREPARING
            self._active_job = job
            self._job_work_dir = os.path.join(self.work_dir, f"job_{job.job_id}")

            try:
                os.makedirs(self._job_work_dir, exist_ok=True)

                # Ensure any leftover TUN from a previous crashed run is cleaned up
                self.network_adapter.teardown_tun(job.tun_name)

                # Generate role configuration JSON per wfb_ng.fl.service schema
                tun_addr = f"{job.tun_ip}/24" if "/" not in job.tun_ip else job.tun_ip
                client_config_path = os.path.join(self._job_work_dir, "client_role.json")

                link_args = [
                    "--tun-name",
                    job.tun_name,
                    "--tun-addr",
                    tun_addr,
                    "--link-id",
                    str(job.link_id),
                    "--uplink-stream",
                    "1",
                    "--downlink-stream",
                    "2",
                    "--fec-k",
                    "8",
                    "--fec-n",
                    "14",
                    "--radio-bandwidth",
                    "40" if "40" in job.channel_width else "20",
                    "--radio-mcs-index",
                    str(job.uplink_mcs),
                    "--radio-short-gi",
                    "--air-interface",
                    air_interface,
                ]

                obs_path = os.path.join(self._job_work_dir, "observation.jsonl") if job.live_observation else None

                role_dict: Dict[str, Any] = {
                    "schema_version": 1,
                    "role": "client",
                    "work_dir": self._job_work_dir,
                    "node_id": job.node_id,
                    "uftp_uid": job.node_id,
                    "uftp_port": job.uftp_port,
                    "server_http_host": job.server_http_host,
                    "server_http_port": job.server_http_port,
                    "uftp_bind_host": job.uftp_bind_host,
                    "uftp_multicast_host": job.uftp_multicast_host,
                    "uftp_private_multicast_host": job.uftp_private_multicast_host,
                    "channel": job.channel,
                    "channel_width": job.channel_width,
                    "radio_txpower_dbm": job.radio_txpower_dbm,
                    "max_update_size_bytes": job.max_update_size_bytes,
                    "live_observation": job.live_observation,
                    "observation_path": obs_path,
                    "io_timeout_seconds": job.io_timeout_seconds,
                    "link_args": link_args,
                }

                with open(client_config_path, "w", encoding="utf-8") as f:
                    json.dump(role_dict, f, indent=2)

                algorithm_config_path = None
                if job.algorithm_config is not None:
                    algorithm_config_path = os.path.join(
                        self._job_work_dir, "algorithm_config.json"
                    )
                    with open(algorithm_config_path, "w", encoding="utf-8") as f:
                        json.dump(job.algorithm_config, f, indent=2)

                # Build command line
                if self._command_prefix:
                    cmd = list(self._command_prefix)
                else:
                    cmd = [
                        sys.executable,
                        "-m",
                        "wfb_ng.fl.service",
                        "--config",
                        client_config_path,
                    ]
                    if job.algorithm:
                        cmd += ["--algorithm", job.algorithm]
                    if algorithm_config_path:
                        cmd += ["--algorithm-config", algorithm_config_path]

                logger.info("派生 RoleService 子进程沙箱: %s", " ".join(cmd))
                log_path = os.path.join(self._job_work_dir, "role_service.log")
                self._log_file = open(log_path, "w", encoding="utf-8")
                notify_socket = None
                notify_path = os.path.join(self._job_work_dir, "notify.sock")
                child_env = None
                if self._command_prefix is None and self._require_ready_notification:
                    notify_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                    notify_socket.bind(notify_path)
                    notify_socket.settimeout(0.1)
                    child_env = os.environ.copy()
                    child_env["NOTIFY_SOCKET"] = notify_path
                # Use start_new_session=True to place child in a new process group
                # Redirect stdout/stderr to log file to avoid pipe buffer deadlock
                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=self._log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=child_env,
                )
                try:
                    self._pgid = os.getpgid(self._process.pid)
                except OSError:
                    self._pgid = None
                if notify_socket is not None:
                    try:
                        deadline = time.monotonic() + 5.0
                        while time.monotonic() < deadline:
                            returncode = self._process.poll()
                            if returncode is not None:
                                raise FLRuntimeError(
                                    "sandbox_start_failed",
                                    f"角色服务就绪前退出，退出码: {returncode}",
                                )
                            try:
                                notification = notify_socket.recv(1024)
                            except socket.timeout:
                                continue
                            if b"READY=1" in notification.splitlines():
                                break
                        else:
                            raise FLRuntimeError(
                                "sandbox_start_timeout",
                                "角色服务未在 5 秒内报告就绪",
                            )
                    finally:
                        notify_socket.close()
                        try:
                            os.unlink(notify_path)
                        except FileNotFoundError:
                            pass
                self.state = DaemonState.RUNNING
                return self._process
            except Exception as exc:
                self._cleanup_failed_start()
                raise FLRuntimeError(
                    "sandbox_start_failed", f"启动角色服务子进程失败: {exc}"
                ) from exc

    def _cleanup_failed_start(self) -> None:
        if self._pgid is not None:
            try:
                os.killpg(self._pgid, signal.SIGKILL)
            except OSError:
                pass
            self._pgid = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
        if self._job_work_dir and os.path.exists(self._job_work_dir):
            try:
                shutil.rmtree(self._job_work_dir, ignore_errors=True)
            except Exception:
                pass
        self._job_work_dir = None
        self._active_job = None
        self._process = None
        self.state = DaemonState.IDLE

    def poll(self) -> Optional[int]:
        with self._lock:
            if self._process is None:
                return None
            ret = self._process.poll()
            if ret is not None:
                self._finalize(ret)
            return ret

    def wait(self, timeout: Optional[float] = None) -> int:
        proc = None
        with self._lock:
            proc = self._process
        if proc is None:
            return 0
        try:
            ret = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.abort()
            raise
        with self._lock:
            self._finalize(ret)
        return ret

    def abort(self, timeout: float = 3.0) -> None:
        """
        Forcefully abort and terminate the sandbox process group, ensuring no orphans remain.
        """
        with self._lock:
            self.state = DaemonState.ABORTING
            proc = self._process
            if proc is not None:
                pid = proc.pid
                pgid = None
                try:
                    pgid = os.getpgid(pid)
                except OSError:
                    pass

                # 1. Send SIGTERM to process group or direct child
                if pgid is not None:
                    logger.info("终止子进程组 PGID=%d (PID=%d)", pgid, pid)
                    try:
                        os.killpg(pgid, signal.SIGTERM)
                    except OSError:
                        pass
                else:
                    try:
                        proc.terminate()
                    except OSError:
                        pass

                # 2. Wait for process group / child to exit within timeout
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    pg_alive = False
                    if pgid is not None:
                        try:
                            os.killpg(pgid, 0)
                            pg_alive = True
                        except OSError:
                            pg_alive = False
                    elif proc.poll() is None:
                        pg_alive = True

                    if not pg_alive:
                        break
                    time.sleep(0.05)

                # 3. Always send SIGKILL to process group if any descendants survive
                if pgid is not None:
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except OSError:
                        pass
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    pass

            rc = proc.poll() if proc else 0
            self._finalize(rc if rc is not None else 0)

    def _finalize(self, returncode: int) -> None:
        """Clean up process log file, process group, TUN, temporary workspace, and reset state."""
        # Always terminate any lingering descendant processes in the process group
        pgid = self._pgid
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            self._pgid = None

        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

        job = self._active_job
        if job:
            logger.info("回收作业 %s 资源 (exit_code=%d)", job.job_id, returncode)
            self.network_adapter.teardown_tun(job.tun_name)

        if self._job_work_dir and os.path.exists(self._job_work_dir):
            try:
                shutil.rmtree(self._job_work_dir, ignore_errors=True)
            except Exception as exc:
                logger.warning("清理临时工作区失败: %s", exc)

        self._process = None
        self._active_job = None
        self._job_work_dir = None
        self.state = DaemonState.IDLE


class ClientDaemon:
    """
    Main Client Daemon supervising hardware polling, radio configuration,
    and task sandbox execution.
    """

    def __init__(
        self,
        config: ClientDaemonConfig,
        network_adapter: Optional[NetworkAdapter] = None,
        _link_process_factory: Optional[Callable[..., subprocess.Popen]] = None,
    ):
        self.config = config
        self.network_adapter = network_adapter or LinuxNetworkAdapter()
        self.sandbox = JobSandbox(
            work_dir=config.work_dir,
            network_adapter=self.network_adapter,
        )
        self.current_interface: Optional[str] = None
        self._last_locked_channel: int = self._load_cached_channel()
        self.control_plane: Optional[ControlPlaneClient] = None
        self._link_process_factory = _link_process_factory or subprocess.Popen
        self._link_process: Optional[subprocess.Popen] = None
        self._link_log_file: Optional[Any] = None
        self._current_job_id: Optional[str] = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

    def _load_cached_channel(self) -> int:
        """Load persisted cached channel from work_dir if valid."""
        cache_path = os.path.join(self.config.work_dir, "channel_cache.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    ch = data.get("channel")
                    if (
                        isinstance(ch, int)
                        and ch in ALLOWED_5GHZ_CHANNELS
                        and ch not in FORBIDDEN_CHANNELS
                    ):
                        return ch
            except Exception:
                pass
        return self.config.channel

    def _save_cached_channel(self, channel: int) -> None:
        """Persist locked channel to work_dir across daemon restarts."""
        try:
            os.makedirs(self.config.work_dir, exist_ok=True)
            cache_path = os.path.join(self.config.work_dir, "channel_cache.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"channel": channel}, f)
        except Exception as exc:
            logger.warning("持久化信道缓存失败: %s", exc)

    @property
    def state(self) -> DaemonState:
        """Derive authoritative daemon state from active sub-components."""
        with self._lock:
            if self._stop_event.is_set():
                return DaemonState.STOPPED
            if self.sandbox.state in (
                DaemonState.PREPARING,
                DaemonState.RUNNING,
                DaemonState.ABORTING,
            ):
                return self.sandbox.state
            if self.control_plane is not None and self.control_plane.is_running:
                if self.control_plane.state == ClientNodeState.HUNTING:
                    return DaemonState.HUNTING
                return DaemonState.IDLE
            if self.current_interface is not None:
                return DaemonState.IDLE
            return DaemonState.POLLING_HARDWARE

    def poll_hardware_once(self) -> Optional[str]:
        """
        Execute a single pass of hardware polling:
        - If no wl* interface is found: returns None (state: POLLING_HARDWARE).
        - If wl* interface is found: configures wireless and returns interface (state: IDLE).
        - If known interface disappeared: triggers hardware unplug recovery.
        """
        with self._lock:
            # If explicit interface was specified in config, use it
            if self.config.interface:
                candidates = [self.config.interface]
            else:
                candidates = self.network_adapter.find_interfaces()

            if not candidates:
                if self.current_interface is not None:
                    logger.warning("检测到无线网卡 %s 被拔出！进行安全自愈重置...", self.current_interface)
                    self._on_interface_lost()
                return None

            target_iface = candidates[0]
            if target_iface != self.current_interface:
                logger.info("检测到无线网卡 %s，开始接管配置...", target_iface)
                try:
                    self.network_adapter.configure_wireless(
                        iface=target_iface,
                        channel=self.config.channel,
                        channel_width=self.config.channel_width,
                        txpower_dbm=self.config.radio_txpower_dbm,
                    )
                    self.network_adapter.setup_tun(
                        self.config.tun_name, self.config.tun_cidr
                    )
                    self.current_interface = target_iface
                    logger.info(
                        "网卡 %s 接管就绪 (Channel=%d, TXPower=%d dBm, TUN=%s)",
                        target_iface,
                        self.config.channel,
                        self.config.radio_txpower_dbm,
                        self.config.tun_name,
                    )
                except Exception as exc:
                    logger.error("网卡 %s 配置失败: %s", target_iface, exc)
                    self.current_interface = None
                    return None

            return self.current_interface

    @property
    def is_idle_link_running(self) -> bool:
        with self._lock:
            return self._link_process is not None and self._link_process.poll() is None

    def start_idle_link(self) -> None:
        """Start the client link that carries control-plane traffic while idle."""
        with self._lock:
            if (
                not self.config.enable_link_process
                or self.current_interface is None
                or self.sandbox.is_running
            ):
                return
            if self.is_idle_link_running:
                return
            if self._link_process is not None:
                self._stop_idle_link()

            executable = shutil.which("wfb_v6_uplink")
            if not executable:
                local_bin = os.path.abspath("wfb_v6_uplink")
                if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
                    executable = local_bin
            if not executable:
                raise FLRuntimeError(
                    "link_executable_missing",
                    "未找到 wfb_v6_uplink 可执行程序，客户端控制链路无法启动",
                )

            self.network_adapter.teardown_tun(self.config.tun_name)
            cmd = build_v6_uplink_client_command(
                executable_path=executable,
                node_id=self.config.node_id,
                tun_name=self.config.tun_name,
                tun_addr=self.config.tun_cidr,
                air_interface=self.current_interface,
                uplink_mcs=self.config.uplink_mcs,
                link_id=self.config.link_id,
                channel_width=self.config.channel_width,
            )
            os.makedirs(self.config.work_dir, exist_ok=True)
            log_path = os.path.join(self.config.work_dir, "wfb_uplink.log")
            self._link_log_file = open(log_path, "a", encoding="utf-8")
            logger.info("启动客户端空闲链路底座: %s", " ".join(cmd))
            try:
                self._link_process = self._link_process_factory(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=self._link_log_file,
                    stderr=self._link_log_file,
                )
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    returncode = self._link_process.poll()
                    if returncode is not None:
                        raise FLRuntimeError(
                            "link_process_start_failed",
                            f"客户端 wfb_v6_uplink 启动期异常退出，退出码: {returncode}",
                        )
                    if self.network_adapter.is_tun_active(self.config.tun_name):
                        return
                    time.sleep(0.05)
                raise FLRuntimeError(
                    "link_tun_failed",
                    f"客户端链路未创建 TUN: {self.config.tun_name}",
                )
            except Exception:
                self._stop_idle_link()
                raise

    def _stop_idle_link(self) -> None:
        """Stop the idle client link and release its TUN."""
        with self._lock:
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
            if self._link_log_file is not None:
                try:
                    self._link_log_file.close()
                except Exception:
                    pass
                self._link_log_file = None
            self.network_adapter.teardown_tun(self.config.tun_name)

    def _ensure_idle_transport(self) -> None:
        if self.current_interface is None or self.sandbox.is_running:
            return
        if self.config.enable_link_process and self.config.enable_control_plane:
            self.start_idle_link()
        else:
            self._restore_tun_if_idle()

    def _on_interface_lost(self) -> None:
        """Safely handle interface unplugging while running."""
        if self.sandbox.is_running:
            logger.warning("网卡丢失，紧急中止正在运行的作业沙箱...")
            self.sandbox.abort()
        if self.control_plane is not None:
            if self.control_plane.locked_channel is not None:
                self._last_locked_channel = self.control_plane.locked_channel
            self.control_plane.stop()
            self.control_plane = None
        self._stop_idle_link()
        if self.current_interface is not None:
            self.network_adapter.teardown_tun(self.config.tun_name)
        self.current_interface = None

    def _restore_tun_if_idle(self) -> None:
        """Ensure persistent TUN interface is active when daemon is idle with interface."""
        if self.current_interface is not None and not self.sandbox.is_running:
            if not self.network_adapter.is_tun_active(self.config.tun_name):
                logger.info(
                    "恢复常驻 TUN 接口 %s (%s)",
                    self.config.tun_name,
                    self.config.tun_cidr,
                )
                self.network_adapter.setup_tun(
                    self.config.tun_name, self.config.tun_cidr
                )

    def trigger_job(self, job_config: ClientJobConfig) -> subprocess.Popen:
        """Start a job in the sandbox using the active wireless interface."""
        with self._lock:
            if self.current_interface is None:
                raise FLRuntimeError(
                    "hardware_unavailable", "无线网卡未就绪，无法启动算法作业"
                )
            if job_config.node_id != self.config.node_id:
                raise FLRuntimeError(
                    "invalid_job_config",
                    f"作业节点 ID ({job_config.node_id}) 与守护进程节点 ID ({self.config.node_id}) 不符",
                )
            if job_config.tun_name != self.config.tun_name:
                raise FLRuntimeError(
                    "invalid_job_config",
                    f"作业 TUN 名称 ({job_config.tun_name}) 与守护进程 TUN 名称 ({self.config.tun_name}) 不符",
                )
            if job_config.tun_ip != self.config.tun_ip:
                raise FLRuntimeError(
                    "invalid_job_config",
                    f"作业 TUN IP ({job_config.tun_ip}) 与守护进程 TUN IP ({self.config.tun_ip}) 不符",
                )
            if job_config.link_id != self.config.link_id:
                raise FLRuntimeError(
                    "invalid_job_config",
                    f"作业 link_id ({job_config.link_id}) 与守护进程 link_id ({self.config.link_id}) 不符",
                )
            self._stop_idle_link()
            try:
                proc = self.sandbox.start(job_config, air_interface=self.current_interface)
                self._current_job_id = job_config.job_id
                if self.control_plane is not None:
                    self.control_plane.notify_task_ready(job_config.job_id)
                    self.control_plane.notify_state_change(ClientNodeState.RUNNING)
                return proc
            except Exception:
                self._current_job_id = None
                self._ensure_idle_transport()
                raise

    def _handle_job_terminal(self, msg: Dict[str, Any], aborted: bool) -> None:
        job_id = msg.get("job_id")
        with self._lock:
            if not isinstance(job_id, str) or job_id != self._current_job_id:
                return
            self.sandbox.abort()
            self._stop_idle_link()
            self.start_idle_link()
            self._current_job_id = None
            if self.control_plane is not None:
                if aborted:
                    self.control_plane.notify_state_change(ClientNodeState.ABORTING)
                self.control_plane.notify_state_change(ClientNodeState.IDLE)

    def _terminate_job(self, intermediate_state: Optional[ClientNodeState] = None) -> None:
        with self._lock:
            self.sandbox.abort()
            self._ensure_idle_transport()
            if self.control_plane is not None:
                if intermediate_state is not None:
                    self.control_plane.notify_state_change(intermediate_state)
                self.control_plane.notify_state_change(ClientNodeState.IDLE)

    def finish_job(self) -> None:
        self._terminate_job()

    def abort_job(self) -> None:
        self._terminate_job(intermediate_state=ClientNodeState.ABORTING)

    def wait_job(self, timeout: Optional[float] = None) -> int:
        ret = self.sandbox.wait(timeout=timeout)
        with self._lock:
            self._ensure_idle_transport()
            if self.control_plane is not None and not self.sandbox.is_running:
                self.control_plane.notify_state_change(ClientNodeState.IDLE)
        return ret

    def _handle_task_announce(self, msg: Dict[str, Any]) -> None:
        """Handle incoming TASK_ANNOUNCE broadcast from server coordinator."""
        with self._lock:
            target_nodes = msg.get("target_nodes")
            if target_nodes and self.config.node_id not in target_nodes:
                return
            if self.state != DaemonState.IDLE:
                logger.warning("收到 TASK_ANNOUNCE 但节点非 IDLE (当前: %s)，忽略", self.state)
                return
            if self.control_plane is not None:
                self.control_plane.notify_state_change(ClientNodeState.PREPARING)

            def _reject(reason: str) -> None:
                logger.error(reason)
                if self.control_plane is not None:
                    self.control_plane.notify_state_change(ClientNodeState.IDLE)

            try:
                run_id = validate_path_safe_identifier(msg.get("run_id"), "run_id")
                job_id = validate_path_safe_identifier(msg.get("job_id"), "job_id")
            except FLRuntimeError as exc:
                return _reject(f"TASK_ANNOUNCE 标识非法: {exc}")

            raw_io_timeout = msg.get("io_timeout_seconds")
            if raw_io_timeout is None or type(raw_io_timeout) is not int or raw_io_timeout <= 0:
                return _reject(f"TASK_ANNOUNCE 缺失或无效的 io_timeout_seconds 字段: {raw_io_timeout!r}")
            io_timeout_seconds = raw_io_timeout

            raw_live_obs = msg.get("live_observation")
            if raw_live_obs is None or type(raw_live_obs) is not bool:
                return _reject(f"TASK_ANNOUNCE 缺失或无效的 live_observation 字段: {raw_live_obs!r}")
            live_observation = raw_live_obs

            algorithm = msg.get("algorithm")
            if not algorithm:
                return _reject("TASK_ANNOUNCE 缺失 algorithm 字段，拒绝启动")

            raw_algo_config = msg.get("algorithm_config")
            if not isinstance(raw_algo_config, dict):
                return _reject("TASK_ANNOUNCE 缺失有效的 algorithm_config，拒绝启动")

            if "rounds" not in msg:
                return _reject("TASK_ANNOUNCE 缺失 rounds 字段，拒绝启动")

            rounds = int(msg["rounds"])
            algo_config = dict(raw_algo_config)
            algo_config["rounds"] = rounds
            algo_config["node_id"] = self.config.node_id
            model_size = msg.get("model_size_bytes")
            if model_size is not None:
                algo_config["required_artifact_size_bytes"] = model_size

            tpl = algo_config.get("update_template_path")
            if tpl and "{node_id}" in tpl:
                algo_config["update_template_path"] = tpl.format(node_id=self.config.node_id)
            elif not tpl:
                algo_config["update_template_path"] = os.path.join(
                    self.config.work_dir,
                    f"update-client{self.config.node_id}-template.bin",
                )

            if "server_http_host" not in msg or "server_http_port" not in msg or "uftp_port" not in msg or "link_id" not in msg:
                return _reject("TASK_ANNOUNCE 缺失网络配置字段，拒绝启动")

            server_http_host = str(msg["server_http_host"])
            server_http_port = int(msg["server_http_port"])
            uftp_port = int(msg["uftp_port"])
            if uftp_port in (self.config.broadcast_port, self.config.server_control_port):
                return _reject("TASK_ANNOUNCE 的 UFTP 数据端口与控制面端口冲突")
            link_id = int(msg["link_id"])

            job_config = ClientJobConfig(
                run_id=run_id,
                job_id=job_id,
                node_id=self.config.node_id,
                tun_name=self.config.tun_name,
                tun_ip=self.config.tun_ip,
                server_http_host=server_http_host,
                server_http_port=server_http_port,
                uftp_port=uftp_port,
                link_id=link_id,
                algorithm=algorithm,
                algorithm_config=algo_config,
                io_timeout_seconds=io_timeout_seconds,
                live_observation=live_observation,
            )
            self.trigger_job(job_config)

    def start_control_plane(self) -> None:
        """Start client UDP control plane if network interface is available."""
        with self._lock:
            if self.current_interface is None:
                return
            if self.control_plane is None or not self.control_plane.is_running:
                self.control_plane = ControlPlaneClient(
                    node_id=self.config.node_id,
                    tun_ip=self.config.tun_ip,
                    network_adapter=self.network_adapter,
                    air_interface=self.current_interface,
                    initial_channel=self.config.channel,
                    cached_channel=self._last_locked_channel or self.config.channel,
                    txpower_dbm=self.config.radio_txpower_dbm,
                    uplink_mcs=self.config.uplink_mcs,
                    server_host=self.config.server_control_host,
                    server_port=self.config.server_control_port,
                    broadcast_port=self.config.broadcast_port,
                )
                self.control_plane.on_radio_finalized = self._on_radio_finalized
                self.control_plane.register_broadcast_handler(
                    "TASK_ANNOUNCE", self._handle_task_announce
                )
                self.control_plane.register_broadcast_handler(
                    "JOB_ABORT", lambda msg: self._handle_job_terminal(msg, aborted=True)
                )
                self.control_plane.register_broadcast_handler(
                    "JOB_COMPLETED", lambda msg: self._handle_job_terminal(msg, aborted=False)
                )
                self.control_plane.start()

    def _on_radio_finalized(self, new_config: Any) -> None:
        """Handle finalized radio reconfiguration by persisting channel cache."""
        with self._lock:
            channel = getattr(new_config, "channel", None)
            if isinstance(channel, int) and channel != self._last_locked_channel:
                self._last_locked_channel = channel
                self._save_cached_channel(channel)

    def run(self) -> None:
        """Run the main daemon supervisory loop until stopped."""
        logger.info(
            "wfb-fl-client-daemon 启动 (Node ID=%d, TUN IP=%s)",
            self.config.node_id,
            self.config.tun_ip,
        )

        while not self._stop_event.is_set():
            if self.current_interface is None:
                iface = self.poll_hardware_once()
                if iface is None:
                    # Wait for poll interval, but interruptible
                    self._stop_event.wait(self.config.poll_interval_seconds)
                    continue

            if (
                self.config.enable_control_plane
                and self.current_interface is not None
                and not self.sandbox.is_running
            ):
                try:
                    self._ensure_idle_transport()
                except Exception as exc:
                    logger.error("客户端空闲链路启动失败: %s", exc)
                    self._stop_event.wait(self.config.poll_interval_seconds)
                    continue

            if (
                self.config.enable_control_plane
                and self.current_interface is not None
                and (self.control_plane is None or not self.control_plane.is_running)
            ):
                self.start_control_plane()

            # Check if active job finished and reconcile control-plane state
            self.sandbox.poll()
            if (
                self.control_plane is not None
                and not self.sandbox.is_running
                and self.control_plane.state in (ClientNodeState.RUNNING, ClientNodeState.PREPARING)
            ):
                self.control_plane.notify_state_change(ClientNodeState.IDLE)

            if self.control_plane is not None and self.control_plane.locked_channel is not None:
                if self.control_plane.locked_channel != self._last_locked_channel:
                    self._last_locked_channel = self.control_plane.locked_channel
                    self._save_cached_channel(self.control_plane.locked_channel)

            if self.current_interface is not None and not self.sandbox.is_running:
                self._ensure_idle_transport()

            # Periodic hardware check (ensure interface hasn't vanished)
            if self.current_interface is not None:
                candidates = self.network_adapter.find_interfaces()
                if self.current_interface not in candidates:
                    self._on_interface_lost()

            self._stop_event.wait(min(self.config.poll_interval_seconds, 0.05))

        self._cleanup()

    def stop(self) -> None:
        """Stop the daemon and release all resources."""
        self._stop_event.set()
        self._cleanup()

    def _cleanup(self) -> None:
        with self._lock:
            if self.control_plane is not None:
                self.control_plane.stop()
                self.control_plane = None
            if self.sandbox.is_running:
                self.sandbox.abort()
            self._stop_idle_link()
            if self.current_interface is not None:
                self.network_adapter.teardown_tun(self.config.tun_name)
            logger.info("wfb-fl-client-daemon 已安全停止")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="WFB-ng FL 客户端常驻守护进程")
    parser.add_argument(
        "--config",
        default=DEFAULT_NODE_CONFIG_PATH,
        help="节点身份配置文件路径 (默认: /etc/wfb-ng-fl/node.json)",
    )
    parser.add_argument(
        "--interface",
        help="指定无线网卡接口 (默认: 动态探测 wl*)",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=DEFAULT_CHANNEL,
        help=f"默认射频信道 (默认: {DEFAULT_CHANNEL})",
    )
    parser.add_argument(
        "--radio-txpower-dbm",
        type=int,
        default=DEFAULT_TXPOWER_DBM,
        help=f"发射功率 (dBm) (默认: {DEFAULT_TXPOWER_DBM})",
    )
    parser.add_argument(
        "--uplink-mcs",
        type=int,
        default=RECOMMENDED_UPLINK_MCS,
        help=f"上行 MCS 调制索引 (默认: {RECOMMENDED_UPLINK_MCS})",
    )
    parser.add_argument(
        "--link-id",
        type=int,
        default=DEFAULT_LINK_ID,
        help=f"WFB 链路 ID (默认: {DEFAULT_LINK_ID})",
    )
    parser.add_argument(
        "--work-dir",
        default=DEFAULT_WORK_DIR,
        help=f"沙箱作业临时工作目录 (默认: {DEFAULT_WORK_DIR})",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"网卡挂起轮询周期 (秒) (默认: {DEFAULT_POLL_INTERVAL_SECONDS})",
    )

    parser.add_argument(
        "--server-host",
        default=CLIENT_UPLINK_DEFAULT_ADDR,
        help=f"服务端 UDP 控制面上行地址 (默认: {CLIENT_UPLINK_DEFAULT_ADDR})",
    )
    parser.add_argument(
        "--server-port",
        type=int,
        default=CLIENT_UPLINK_DEFAULT_PORT,
        help=f"服务端 UDP 控制面上行端口 (默认: {CLIENT_UPLINK_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--broadcast-port",
        type=int,
        default=SERVER_CONTROL_BROADCAST_PORT,
        help=f"服务端 UDP 控制面广播监听端口 (默认: {SERVER_CONTROL_BROADCAST_PORT})",
    )

    args = parser.parse_args(argv)

    try:
        identity = load_node_identity(args.config)
    except FLRuntimeError as exc:
        logger.error("节点身份加载失败: %s (%s)", exc.error_message, exc.error_code)
        return 1

    daemon_config = ClientDaemonConfig(
        node_id=identity.node_id,
        tun_ip=identity.tun_ip,
        tun_cidr=identity.tun_cidr,
        channel=args.channel,
        radio_txpower_dbm=args.radio_txpower_dbm,
        uplink_mcs=args.uplink_mcs,
        link_id=args.link_id,
        work_dir=args.work_dir,
        poll_interval_seconds=args.poll_interval,
        interface=args.interface,
        server_control_host=args.server_host,
        server_control_port=args.server_port,
        broadcast_port=args.broadcast_port,
    )

    daemon = ClientDaemon(config=daemon_config)

    def handle_signal(signum, frame):
        logger.info("收到退出信号 (%d)，正在优雅退出...", signum)
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        daemon.run()
    except Exception as exc:
        logger.exception("守护进程异常退出: %s", exc)
        daemon.stop()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
