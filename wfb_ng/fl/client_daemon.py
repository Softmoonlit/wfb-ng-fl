#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
wfb_ng.fl.client_daemon: Client Persistent Daemon, Hardware Polling, and RoleService Sandbox.

Per ADR-0010, ADR-0012, ADR-0014:
- Persistent systemd daemon: wfb-fl-client-daemon.
- Identity: /etc/wfb-ng-fl/node.json (node_id 1..10, static tun_ip 10.80.0.{10+node_id}).
- Hardware Polling: 3s suspended retry if wlx* wireless interface not detected; auto hot takeover.
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
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from .errors import FLRuntimeError
from .radio import (
    ALLOWED_5GHZ_CHANNELS,
    DEFAULT_CHANNEL,
    DEFAULT_TXPOWER_DBM,
    FIXED_BANDWIDTH,
    FORBIDDEN_CHANNELS,
    RECOMMENDED_UPLINK_MCS,
    find_wlx_interfaces,
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


class DaemonState(str, Enum):
    POLLING_HARDWARE = "POLLING_HARDWARE"
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
    - tun_ip must be a valid 10.80.0.x IPv4 address, defaulting to 10.80.0.{10+node_id}/24.
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
            tun_ip_str = str(interface.ip)
            tun_cidr_str = str(interface)
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

    def setup_tun(self, tun_name: str, tun_cidr: str) -> None:
        raise NotImplementedError

    def teardown_tun(self, tun_name: str) -> None:
        raise NotImplementedError

    def is_tun_active(self, tun_name: str) -> bool:
        raise NotImplementedError


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
        return find_wlx_interfaces()

    def configure_wireless(
        self,
        iface: str,
        channel: int = DEFAULT_CHANNEL,
        channel_width: str = FIXED_BANDWIDTH,
        txpower_dbm: int = DEFAULT_TXPOWER_DBM,
    ) -> None:
        if channel in FORBIDDEN_CHANNELS:
            raise FLRuntimeError(
                "forbidden_channel",
                f"Channel {channel} is strictly forbidden due to driver kernel crash defect.",
            )
        if channel not in ALLOWED_5GHZ_CHANNELS:
            raise FLRuntimeError(
                "invalid_channel",
                f"Channel {channel} is not in legal pool {ALLOWED_5GHZ_CHANNELS}.",
            )
        if channel == 165:
            channel_width = "HT20"

        # 1. ip link set <iface> down
        self._run_cmd(["ip", "link", "set", iface, "down"])
        # 2. iw dev <iface> set type monitor
        self._run_cmd(["iw", "dev", iface, "set", "type", "monitor"])
        # 3. ip link set <iface> up
        self._run_cmd(["ip", "link", "set", iface, "up"])
        # 4. iw dev <iface> set channel <channel> <channel_width>
        self._run_cmd(["iw", "dev", iface, "set", "channel", str(channel), channel_width])

        # 5. TX power setting
        # Per memory #93: write to rtw_tx_pwr_idx_override and negative mBm in iw
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

        # iw dev <iface> set txpower fixed -<txpower_dbm * 100> (preserve negative sign!)
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


@dataclass(frozen=True)
class ClientDaemonConfig:
    node_id: int
    tun_ip: str
    tun_cidr: Optional[str] = None
    tun_name: Optional[str] = None
    channel: int = DEFAULT_CHANNEL
    channel_width: str = FIXED_BANDWIDTH
    radio_txpower_dbm: int = DEFAULT_TXPOWER_DBM
    uplink_mcs: int = RECOMMENDED_UPLINK_MCS
    work_dir: str = DEFAULT_WORK_DIR
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    interface: Optional[str] = None

    def __post_init__(self):
        if self.tun_cidr is None:
            object.__setattr__(self, "tun_cidr", f"{self.tun_ip}/24")
        if self.tun_name is None:
            object.__setattr__(self, "tun_name", f"{DEFAULT_TUN_PREFIX}{self.node_id}")


@dataclass(frozen=True)
class ClientJobConfig:
    job_id: str
    node_id: int
    tun_name: str
    tun_ip: str
    server_http_host: str = "10.80.0.1"
    server_http_port: int = 8080
    uftp_port: int = 9000
    uftp_bind_host: Optional[str] = None
    uftp_multicast_host: str = "224.0.0.1"
    uftp_private_multicast_host: str = "224.0.0.2"
    channel: int = DEFAULT_CHANNEL
    channel_width: str = FIXED_BANDWIDTH
    radio_txpower_dbm: int = DEFAULT_TXPOWER_DBM
    uplink_mcs: int = RECOMMENDED_UPLINK_MCS
    downlink_mcs: int = 3
    max_update_size_bytes: int = 1073741824
    io_timeout_seconds: int = 120
    algorithm: Optional[str] = None
    algorithm_config: Optional[Dict[str, Any]] = None
    live_observation: bool = False
    observation_path: Optional[str] = None

    def __post_init__(self):
        if self.uftp_bind_host is None:
            # Strip CIDR prefix if present
            clean_ip = self.tun_ip.split("/")[0]
            object.__setattr__(self, "uftp_bind_host", clean_ip)


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
        command_prefix: Optional[Sequence[str]] = None,
    ):
        self.work_dir = os.path.abspath(work_dir)
        self.network_adapter = network_adapter
        self.command_prefix = list(command_prefix) if command_prefix else None
        self._process: Optional[subprocess.Popen] = None
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
                "0",
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

            obs_path = job.observation_path
            if job.live_observation and not obs_path:
                obs_path = os.path.join(self._job_work_dir, "observation.jsonl")

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
            if self.command_prefix:
                cmd = list(self.command_prefix)
            else:
                cmd = [
                    sys.executable,
                    "-c",
                    "from wfb_ng.fl.service import client_main; client_main()",
                    "--config",
                    client_config_path,
                ]
                if job.algorithm:
                    cmd += ["--algorithm", job.algorithm]
                if algorithm_config_path:
                    cmd += ["--algorithm-config", algorithm_config_path]

            logger.info("派生 RoleService 子进程沙箱: %s", " ".join(cmd))
            try:
                log_path = os.path.join(self._job_work_dir, "role_service.log")
                self._log_file = open(log_path, "w", encoding="utf-8")
                # Use start_new_session=True to place child in a new process group
                # Redirect stdout/stderr to log file to avoid pipe buffer deadlock
                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=self._log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                self.state = DaemonState.RUNNING
                return self._process
            except Exception as exc:
                if self._log_file is not None:
                    try:
                        self._log_file.close()
                    except Exception:
                        pass
                    self._log_file = None
                if self._job_work_dir and os.path.exists(self._job_work_dir):
                    shutil.rmtree(self._job_work_dir, ignore_errors=True)
                self._job_work_dir = None
                self._active_job = None
                self._process = None
                self.state = DaemonState.IDLE
                raise FLRuntimeError(
                    "sandbox_start_failed", f"启动角色服务子进程失败: {exc}"
                ) from exc

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
            if proc is not None and proc.poll() is None:
                pid = proc.pid
                try:
                    pgid = os.getpgid(pid)
                    logger.info("终止子进程组 PGID=%d (PID=%d)", pgid, pid)
                    os.killpg(pgid, signal.SIGTERM)
                except OSError:
                    try:
                        proc.terminate()
                    except OSError:
                        pass

                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)

                if proc.poll() is None:
                    logger.warning("子进程未响应 SIGTERM，发送 SIGKILL (PGID=%d)", pgid)
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except OSError:
                        try:
                            proc.kill()
                        except OSError:
                            pass
                    proc.wait()

            self._finalize(proc.poll() if proc else 0)

    def _finalize(self, returncode: int) -> None:
        """Clean up process log file, TUN, temporary workspace, and reset state."""
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
        sandbox_command_prefix: Optional[Sequence[str]] = None,
    ):
        self.config = config
        self.network_adapter = network_adapter or LinuxNetworkAdapter()
        self.sandbox = JobSandbox(
            work_dir=config.work_dir,
            network_adapter=self.network_adapter,
            command_prefix=sandbox_command_prefix,
        )
        self.current_interface: Optional[str] = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

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
            if self.current_interface is not None:
                return DaemonState.IDLE
            return DaemonState.POLLING_HARDWARE

    def poll_hardware_once(self) -> Optional[str]:
        """
        Execute a single pass of hardware polling:
        - If no wlx* interface is found: returns None (state: POLLING_HARDWARE).
        - If wlx* interface is found: configures wireless and returns interface (state: IDLE).
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

    def _on_interface_lost(self) -> None:
        """Safely handle interface unplugging while running."""
        if self.sandbox.is_running:
            logger.warning("网卡丢失，紧急中止正在运行的作业沙箱...")
            self.sandbox.abort()
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
            return self.sandbox.start(job_config, air_interface=self.current_interface)

    def abort_job(self) -> None:
        with self._lock:
            self.sandbox.abort()
            self._restore_tun_if_idle()

    def wait_job(self, timeout: Optional[float] = None) -> int:
        ret = self.sandbox.wait(timeout=timeout)
        with self._lock:
            self._restore_tun_if_idle()
        return ret

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

            # Check if active job finished
            self.sandbox.poll()
            if self.current_interface is not None and not self.sandbox.is_running:
                self._restore_tun_if_idle()

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
            if self.sandbox.is_running:
                self.sandbox.abort()
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
        help="指定无线网卡接口 (默认: 动态探测 wlx*)",
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
        work_dir=args.work_dir,
        poll_interval_seconds=args.poll_interval,
        interface=args.interface,
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
