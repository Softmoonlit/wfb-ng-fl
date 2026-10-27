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
import errno
import ipaddress
import json
import logging
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence

from pyroute2 import IPRoute, NetlinkError

from .algorithm import validate_algorithm_config
from .artifacts import validate_path_safe_identifier, write_json_atomic
from .evidence import DEFAULT_EVIDENCE_DIR, archive_client_evidence
from .errors import FLRuntimeError
from .link_radio import DEFAULT_RADIO_CONTROL_PORT, apply_link_radio
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
    ALLOWED_MCS_VALUES,
    DEFAULT_BANDWIDTH_MHZ,
    TXPOWER_MIN_DBM,
    TXPOWER_MAX_DBM,
    DEFAULT_CHANNEL,
    DEFAULT_TXPOWER_DBM,
    FIXED_BANDWIDTH,
    FORBIDDEN_CHANNELS,
    RECOMMENDED_UPLINK_MCS,
    RadioConfig,
    find_wl_interfaces,
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


def _hardware_radio_config(channel: int, bandwidth_mhz: int, txpower_dbm: int,
                           uplink_mcs: int = RECOMMENDED_UPLINK_MCS) -> RadioConfig:
    # 硬件设置只校验物理参数，不承担普通作业的 UFTP 实测速率门禁。
    if type(channel) is not int or channel not in ALLOWED_5GHZ_CHANNELS:
        raise ValueError("射频信道不在允许列表中")
    if type(txpower_dbm) is not int or not TXPOWER_MIN_DBM <= txpower_dbm <= TXPOWER_MAX_DBM:
        raise ValueError("射频功率必须为 10..20 dBm 的整数")
    if type(uplink_mcs) is not int or uplink_mcs not in ALLOWED_MCS_VALUES:
        raise ValueError("上行 MCS 必须为 3、4、5 或 6")
    radio = RadioConfig(channel=channel, bandwidth_mhz=bandwidth_mhz,
                        radio_txpower_dbm=txpower_dbm, uplink_mcs=uplink_mcs)
    radio.channel_width  # 派生时拒绝不支持的信道与带宽组合。
    return radio


def _validate_hardware_width(channel: int, channel_width: str, txpower_dbm: int) -> None:
    widths = {"HT20": 20, "HT40+": 40, "HT40-": 40}
    if channel_width not in widths:
        raise ValueError("网卡带宽必须为 HT20、HT40+ 或 HT40-")
    radio = _hardware_radio_config(channel, widths[channel_width], txpower_dbm)
    if radio.channel_width != channel_width:
        raise ValueError("网卡 HT40 方向与固定信道不符")


def build_v6_uplink_client_command(
    executable_path: str,
    node_id: int,
    tun_name: str,
    tun_addr: str,
    air_interface: str,
    uplink_mcs: int,
    link_id: int,
    bandwidth_mhz: int = DEFAULT_BANDWIDTH_MHZ,
    radio_control_port: int = DEFAULT_RADIO_CONTROL_PORT,
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
        "--radio-bandwidth", str(bandwidth_mhz),
        "--radio-control-port", str(radio_control_port),
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

    def is_tun_ready(self, tun_name: str, tun_cidr: str, destination: str) -> bool:
        """Whether the configured UP/address/control-route contract is satisfied."""
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
        _validate_hardware_width(channel, channel_width, txpower_dbm)

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
        _validate_hardware_width(channel, channel_width, DEFAULT_TXPOWER_DBM)
        self._run_cmd(["iw", "dev", iface, "set", "channel", str(channel), channel_width])

    def set_txpower(self, iface: str, txpower_dbm: int) -> None:
        _hardware_radio_config(DEFAULT_CHANNEL, DEFAULT_BANDWIDTH_MHZ, txpower_dbm)
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

    def is_tun_ready(self, tun_name: str, tun_cidr: str, destination: str) -> bool:
        """TUNSETIFF exposes the name before link-up and address/route creation."""
        address = ipaddress.IPv4Interface(tun_cidr)
        with IPRoute() as ip:
            ip.get_timeout = 1.0
            ip.get_timeout_exception = TimeoutError
            ip.settimeout(1.0)
            indexes = ip.link_lookup(ifname=tun_name)
            if not indexes:
                return False
            index = indexes[0]
            links = ip.get_links(index)
            if not links or not links[0]['flags'] & 1:  # IFF_UP
                return False
            if not any(a.get_attr('IFA_LOCAL') == str(address.ip)
                       and a['prefixlen'] == address.network.prefixlen
                       for a in ip.get_addr(index=index, family=socket.AF_INET)):
                return False
            try:
                routes = ip.route('get', dst=destination, src=str(address.ip))
            except NetlinkError as exc:
                if exc.code in (3, 19, 99, 101):  # removed device/address or no route
                    return False
                raise
            return bool(routes) and all(
                route.get_attr('RTA_OIF') == index for route in routes)

    def set_tun_txqueuelen(self, tun_name: str, txqueuelen: int = 5000) -> None:
        self._run_cmd(["ip", "link", "set", "dev", tun_name, "txqueuelen", str(txqueuelen)])


@dataclass(frozen=True)
class ClientDaemonConfig:
    node_id: int
    tun_ip: str
    tun_cidr: str = ""
    tun_name: str = ""
    channel: int = DEFAULT_CHANNEL
    bandwidth_mhz: int = DEFAULT_BANDWIDTH_MHZ
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
    radio_control_port: int = DEFAULT_RADIO_CONTROL_PORT
    mnist_data_dir: Optional[str] = None

    def __post_init__(self):
        if not self.tun_cidr:
            object.__setattr__(self, "tun_cidr", f"{self.tun_ip}/24")
        if not self.tun_name:
            object.__setattr__(self, "tun_name", f"{DEFAULT_TUN_PREFIX}{self.node_id}")
        _hardware_radio_config(self.channel, self.bandwidth_mhz,
                               self.radio_txpower_dbm, self.uplink_mcs)
        if type(self.radio_control_port) is not int or not 1 <= self.radio_control_port <= 65535:
            raise ValueError("radio_control_port 必须为 1..65535 的整数")
        if type(self.link_id) is not int or self.link_id <= 0:
            raise ValueError("link_id 必须为正整数")

    @property
    def channel_width(self) -> str:
        return RadioConfig(channel=self.channel, bandwidth_mhz=self.bandwidth_mhz).channel_width


@dataclass(frozen=True)
class ClientJobConfig:
    job_id: str
    run_id: str
    node_id: int
    tun_name: str
    tun_ip: str
    io_timeout_seconds: float
    live_observation: bool
    server_http_host: str = "10.80.0.1"
    server_http_port: int = 8080
    uftp_port: int = DEFAULT_UFTP_DATA_PORT
    link_id: int = DEFAULT_LINK_ID
    uftp_bind_host: Optional[str] = None
    uftp_multicast_host: str = DEFAULT_UFTP_MULTICAST_HOST
    uftp_private_multicast_host: str = DEFAULT_UFTP_PRIVATE_MULTICAST_HOST
    channel: int = DEFAULT_CHANNEL
    bandwidth_mhz: int = DEFAULT_BANDWIDTH_MHZ
    radio_txpower_dbm: int = DEFAULT_TXPOWER_DBM
    uplink_mcs: int = RECOMMENDED_UPLINK_MCS
    max_update_size_bytes: int = 1073741824
    algorithm: Optional[str] = None
    algorithm_config: Optional[Dict[str, Any]] = None
    target_nodes: Sequence[int] = ()
    rounds: int = 1
    preparation_timeout_seconds: float = 30.0

    def __post_init__(self):
        if (type(self.rounds) is not int or self.rounds <= 0 or
                isinstance(self.preparation_timeout_seconds, bool) or
                not isinstance(self.preparation_timeout_seconds, (int, float)) or
                not math.isfinite(self.preparation_timeout_seconds) or self.preparation_timeout_seconds <= 0):
            raise FLRuntimeError('invalid_job_config', '轮数与准备期限必须为正数')
        if self.target_nodes and (self.node_id not in self.target_nodes or
                any(type(node) is not int or not 1 <= node <= 255 for node in self.target_nodes) or
                len(set(self.target_nodes)) != len(self.target_nodes)):
            raise FLRuntimeError('invalid_job_config', '参与节点无效')
        validate_path_safe_identifier(self.run_id, "run_id")
        validate_path_safe_identifier(self.job_id, "job_id")
        if (isinstance(self.io_timeout_seconds, bool) or
                not isinstance(self.io_timeout_seconds, (int, float)) or
                not math.isfinite(self.io_timeout_seconds) or self.io_timeout_seconds <= 0):
            raise FLRuntimeError("invalid_io_timeout", "io_timeout_seconds 必须为有限正数")
        if type(self.live_observation) is not bool:
            raise FLRuntimeError("invalid_live_observation", "live_observation 必须为布尔值")
        if self.uftp_bind_host is None:
            # Strip CIDR prefix if present
            clean_ip = self.tun_ip.split("/")[0]
            object.__setattr__(self, "uftp_bind_host", clean_ip)
        _hardware_radio_config(self.channel, self.bandwidth_mhz,
                               self.radio_txpower_dbm, self.uplink_mcs)
        if type(self.link_id) is not int or self.link_id <= 0:
            raise ValueError("link_id 必须为正整数")

    @property
    def channel_width(self) -> str:
        return RadioConfig(channel=self.channel, bandwidth_mhz=self.bandwidth_mhz).channel_width


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
        evidence_dir: Optional[str] = None,
        local_config: Optional[Dict[str, Any]] = None,
    ):
        self.local_config = dict(local_config or {})
        self._notify_socket: Optional[socket.socket] = None
        self._notify_path: Optional[str] = None
        self._preparation_deadline: Optional[float] = None
        self.supervised = False
        self._prepared = False
        self._completed_round = 0
        self.evidence_dir = evidence_dir if evidence_dir is not None else DEFAULT_EVIDENCE_DIR
        self.last_evidence_error: Optional[str] = None
        self.retained_sandbox_dir: Optional[str] = None
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
                if self.supervised:
                    return True  # 监管线程先核对完成事实，再释放资源。
                self._finalize(ret)
                return False
            return True

    def start(self, job: ClientJobConfig, air_interface: str) -> subprocess.Popen:
        with self._lock:
            if self.state == DaemonState.STOPPED:
                raise FLRuntimeError('sandbox_stopped', '任务资源清理失败，拒绝启动新作业')
            if self._process is not None:
                returncode = self._process.poll()
                if returncode is None:
                    raise FLRuntimeError(
                        "sandbox_already_running",
                        f"已有作业正在运行 (PID={self._process.pid})，拒绝并发执行",
                    )
                self._finalize(returncode)

            self.state = DaemonState.PREPARING
            self._preparation_deadline = time.monotonic() + job.preparation_timeout_seconds
            self.last_evidence_error = None
            self._active_job = job
            job_work_dir = os.path.join(self.work_dir, f"job_{job.job_id}")
            self._job_work_dir = None

            try:
                os.makedirs(job_work_dir, mode=0o700, exist_ok=False)
                self._job_work_dir = job_work_dir

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
                    str(job.bandwidth_mhz),
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

                context_path = os.path.join(self._job_work_dir, "job_context.json")
                with open(context_path, "w", encoding="utf-8") as f:
                    json.dump(dict(job_id=job.job_id, node_id=job.node_id,
                                   target_nodes=list(job.target_nodes or (job.node_id,)),
                                   rounds=job.rounds, artifact_dir=os.path.join(self._job_work_dir, 'algorithm'),
                                   local_config=self.local_config,
                                   preparation_timeout_seconds=job.preparation_timeout_seconds), f)

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
                        "--job-context", context_path,
                    ]
                    if job.algorithm:
                        cmd += ["--algorithm", job.algorithm]
                    if algorithm_config_path:
                        cmd += ["--algorithm-config", algorithm_config_path]

                logger.info("派生 RoleService 子进程沙箱: %s", " ".join(cmd))
                log_path = os.path.join(self._job_work_dir, "role_service.log")
                self._log_file = open(log_path, "w", encoding="utf-8")
                notify_socket = None
                self._prepared = False
                self._completed_round = 0
                notify_path = os.path.join(self._job_work_dir, "notify.sock")
                child_env = None
                if self._command_prefix is None and self._require_ready_notification:
                    notify_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                    notify_socket.bind(notify_path)
                    notify_socket.settimeout(0.1)
                    child_env = os.environ.copy()
                    child_env["WFB_FL_NOTIFY_SOCKET"] = notify_path
                    self._notify_socket = notify_socket
                    self._notify_path = notify_path
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
                if notify_socket is None:
                    self.state = DaemonState.RUNNING
                return self._process
            except Exception as exc:
                self._cleanup_failed_start()
                raise FLRuntimeError(
                    "sandbox_start_failed", f"启动角色服务子进程失败: {exc}"
                ) from exc

    def monitor(self, on_event, stop_event):
        """监管准备期限、有限算法报告与角色退出。"""
        with self._lock:
            job, proc, notify = self._active_job, self._process, self._notify_socket
            preparation_deadline = self._preparation_deadline
        if job is None or proc is None:
            return
        if preparation_deadline is None:
            raise FLRuntimeError('sandbox_start_failed', '任务沙箱缺少准备期限')
        while not stop_event.is_set():
            # 先处理排队报告，再检查退出，避免遗漏最后的错误事实。
            if notify is not None:
                try:
                    payload, sender = notify.recvfrom(4097)
                except socket.timeout:
                    payload = None
                except OSError:
                    return
                if payload is not None:
                    accepted = False
                    try:
                        if len(payload) > 4096:
                            raise FLRuntimeError('control_message_too_large', '本机算法报告超过 4096 字节')
                        event = json.loads(payload)
                        if not isinstance(event, dict) or event.get('job_id') != job.job_id:
                            raise FLRuntimeError('invalid_algorithm_report', '算法报告作业身份不匹配')
                        kind = event.get('event')
                        if kind == 'prepared':
                            if self._prepared or set(event) != {'job_id', 'event', 'report'} or not isinstance(event['report'], dict):
                                raise FLRuntimeError('invalid_algorithm_report', '准备报告无效')
                        elif kind == 'round_complete':
                            index = event.get('round_index')
                            if (set(event) != {'job_id', 'event', 'round_index'} or not self._prepared or
                                    type(index) is not int or index != self._completed_round + 1 or index > job.rounds):
                                raise FLRuntimeError('invalid_algorithm_report', '算法完成轮序无效')
                        elif kind == 'error':
                            if (set(event) != {'job_id', 'event', 'phase', 'code', 'reason', 'round_index'} or
                                    any(not isinstance(event.get(key), str) or not event[key] for key in ('phase', 'code', 'reason')) or
                                    (event['round_index'] is not None and (type(event['round_index']) is not int or event['round_index'] < 1))):
                                raise FLRuntimeError('invalid_algorithm_report', '算法错误报告无效')
                        else:
                            raise FLRuntimeError('invalid_algorithm_report', '未知算法报告')
                        on_event(event)
                        with self._lock:
                            if stop_event.is_set() or self._process is not proc:
                                return
                            if kind == 'prepared':
                                self._prepared = True
                                self.state = DaemonState.RUNNING
                            elif kind == 'round_complete':
                                self._completed_round = event['round_index']
                            accepted = True
                    except Exception as exc:
                        logger.warning('算法报告未获确认: %s', exc)
                    try:
                        notify.sendto(json.dumps({'accepted': accepted}).encode('utf-8'), sender)
                    except OSError:
                        return
                    if not accepted:
                        raise FLRuntimeError('algorithm_report_rejected', '本机算法报告无效或控制面未确认')
                    if event['event'] == 'error':
                        return
            elif stop_event.wait(0.05):
                return
            if not self._prepared and time.monotonic() >= preparation_deadline:
                raise FLRuntimeError('preparation_timeout', '客户端训练准备期限已到达')
            returncode = proc.poll()
            if returncode is not None:
                if returncode != 0 or not self._prepared or self._completed_round != job.rounds:
                    raise FLRuntimeError('algorithm_incomplete', '角色退出或算法完成轮数不足')
                return

    def _cleanup_failed_start(self) -> None:
        proc = self._process
        returncode = -1
        if proc is not None:
            try:
                if self._pgid is not None:
                    os.killpg(self._pgid, signal.SIGKILL)
            except OSError:
                pass
            try:
                if proc.poll() is None:
                    proc.kill()
                returncode = proc.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                logger.exception('启动失败后回收角色服务失败')
        self._finalize(returncode, outcome='start_failed')

    def poll(self) -> Optional[int]:
        with self._lock:
            if self._process is None:
                return None
            ret = self._process.poll()
            if ret is not None and not self.supervised:
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

    def abort(self, timeout: float = 3.0, *, outcome: str = 'aborted') -> None:
        """
        Forcefully abort and terminate the sandbox process group, ensuring no orphans remain.
        """
        with self._lock:
            self.state = DaemonState.ABORTING
            proc = self._process
            if proc is not None:
                completed_returncode = proc.poll()
                if completed_returncode is not None:
                    self._finalize(completed_returncode, outcome=outcome)
                    return
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
                    proc.poll()  # 先回收直接子进程，再判断是否仍有存活后代。
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
            self._finalize(rc if rc is not None else -1, outcome=outcome)

    def _finalize(self, returncode: int, outcome: Optional[str] = None) -> None:
        """Clean up process log file, process group, TUN, temporary workspace, and reset state."""
        if self._notify_socket is not None:
            self._notify_socket.close()
            self._notify_socket = None
        if self._notify_path is not None:
            try:
                os.unlink(self._notify_path)
            except FileNotFoundError:
                pass
            self._notify_path = None
        # 复用现有组终止路径；信号送达后必须确认退出，未确认保留身份。
        pgid = self._pgid
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    self.state = DaemonState.STOPPED
                    raise FLRuntimeError('task_process_group_not_exited', '任务进程组无法确认退出') from exc
            deadline = time.monotonic() + 2.0
            poll_wait = threading.Event()
            while True:
                if self._process is not None:
                    self._process.poll()
                try:
                    os.killpg(pgid, 0)
                except OSError as exc:
                    if exc.errno == errno.ESRCH:
                        break
                    self.state = DaemonState.STOPPED
                    raise FLRuntimeError('task_process_group_not_exited', '任务进程组无法确认退出') from exc
                if time.monotonic() >= deadline:
                    self.state = DaemonState.STOPPED
                    raise FLRuntimeError('task_process_group_not_exited', '任务进程组仍未退出，停止接单')
                poll_wait.wait(min(0.05, max(0, deadline - time.monotonic())))
            self._pgid = None
        if self._process is not None and self._process.poll() is None:
            self.state = DaemonState.STOPPED
            raise FLRuntimeError('task_process_not_exited', '任务角色仍未退出，停止接单')

        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

        job = self._active_job
        archive_succeeded = False
        if job and self._job_work_dir:
            try:
                archive_client_evidence(
                    self._job_work_dir, self.evidence_dir,
                    run_id=job.run_id, job_id=job.job_id, node_id=job.node_id,
                    lifecycle_outcome=outcome or ('succeeded' if returncode == 0 else 'failed'),
                    returncode=returncode,
                )
                archive_succeeded = True
            except Exception as exc:
                self.last_evidence_error = str(exc)
                logger.exception('作业 %s evidence 归档失败，保留沙箱 %s', job.job_id, self._job_work_dir)
                try:
                    write_json_atomic(os.path.join(self._job_work_dir, 'evidence_error.json'), {
                        'schema_version': 1, 'run_id': job.run_id, 'job_id': job.job_id,
                        'error': str(exc),
                    })
                except Exception:
                    logger.exception('无法写入 evidence 错误记录，错误已保留在 daemon journal')
        cleanup_error = None
        if job:
            logger.info("回收作业 %s 资源 (exit_code=%d)", job.job_id, returncode)
            try:
                self.network_adapter.teardown_tun(job.tun_name)
                if self.network_adapter.is_tun_active(job.tun_name):
                    raise FLRuntimeError('task_tun_cleanup_failed', '任务 TUN 仍存在')
            except Exception as exc:
                cleanup_error = exc
                logger.exception('释放任务 TUN 失败: %s', job.tun_name)

        if archive_succeeded and self._job_work_dir and os.path.exists(self._job_work_dir):
            try:
                shutil.rmtree(self._job_work_dir)
            except Exception:
                logger.exception("清理临时工作区失败，保留路径: %s", self._job_work_dir)
        if self._job_work_dir and os.path.lexists(self._job_work_dir):
            self.retained_sandbox_dir = self._job_work_dir

        self.supervised = False
        if cleanup_error is not None:
            self.state = DaemonState.STOPPED
            raise FLRuntimeError('task_tun_cleanup_failed', '任务 TUN 清理失败，停止接单') from cleanup_error
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
        self._effective_radio = RadioConfig(
            channel=config.channel, radio_txpower_dbm=config.radio_txpower_dbm,
            uplink_mcs=config.uplink_mcs, bandwidth_mhz=config.bandwidth_mhz,
        )
        self._effective_radio_calibration = False
        self.network_adapter = network_adapter or LinuxNetworkAdapter()
        self.sandbox = JobSandbox(
            work_dir=config.work_dir,
            network_adapter=self.network_adapter,
            local_config={"mnist_data_dir": config.mnist_data_dir},
        )
        self.current_interface: Optional[str] = None
        self._last_locked_channel: int = self._load_cached_channel()
        self.control_plane: Optional[ControlPlaneClient] = None
        self._link_process_factory = _link_process_factory or subprocess.Popen
        self._link_process: Optional[subprocess.Popen] = None
        self._link_log_file: Optional[Any] = None
        self._current_job_id: Optional[str] = None
        # 仅保留当前或最近一次受理事实，拦截该作业迟到的启动重试。
        self._last_accepted_job_id: Optional[str] = None
        self._stop_event = threading.Event()
        self._job_stop_event = threading.Event()
        self._startup_thread: Optional[threading.Thread] = None
        self._recovery_thread: Optional[threading.Thread] = None
        self._recovery_pending: Optional[str] = None
        self._terminal_job_id: Optional[str] = None
        self._terminal_aborted = False
        # 单一回收所有者；daemon 锁只保护身份，不覆盖退出或 ACK 等待。
        self._recovery_lock = threading.Lock()
        self._transport_lock = threading.RLock()
        self._recovery_fact: Optional[tuple[str, int, str, Optional[str], Optional[str], str]] = None
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
            if (self._stop_event.is_set() or self.sandbox.state == DaemonState.STOPPED or
                    (self.current_interface is not None and
                     self._recovery_fact is not None and
                     self._recovery_fact[0] == self._current_job_id and
                     self._recovery_fact[2] == 'blocked')):
                return DaemonState.STOPPED
            if self.sandbox.state in (
                DaemonState.PREPARING,
                DaemonState.RUNNING,
                DaemonState.ABORTING,
            ):
                return self.sandbox.state
            if (self._current_job_id is not None and self.current_interface is not None and
                    (self._terminal_job_id == self._current_job_id or
                     (self._recovery_fact is not None and
                      self._recovery_fact[0] == self._current_job_id))):
                return DaemonState.ABORTING
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
        candidates = ([self.config.interface] if self.config.interface
                      else self.network_adapter.find_interfaces())
        if not candidates:
            if self.current_interface is not None:
                logger.warning("检测到无线网卡 %s 被拔出", self.current_interface)
                self._on_interface_lost()
            return None
        with self._lock:
            target_iface = candidates[0]
            if target_iface != self.current_interface:
                logger.info("检测到无线网卡 %s，开始接管配置...", target_iface)
                try:
                    self.network_adapter.configure_wireless(
                        iface=target_iface,
                        channel=self._effective_radio.channel,
                        channel_width=self._effective_radio.channel_width,
                        txpower_dbm=self._effective_radio.radio_txpower_dbm,
                    )
                    self.network_adapter.setup_tun(
                        self.config.tun_name, self.config.tun_cidr
                    )
                    self.current_interface = target_iface
                    logger.info(
                        "网卡 %s 接管就绪 (Channel=%d, TXPower=%d dBm, TUN=%s)",
                        target_iface,
                        self._effective_radio.channel,
                        self._effective_radio.radio_txpower_dbm,
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
        with self._transport_lock:
            if (
                not self.config.enable_link_process
                or self.current_interface is None
                or self.sandbox.state == DaemonState.STOPPED
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
                uplink_mcs=self._effective_radio.uplink_mcs,
                link_id=self.config.link_id,
                bandwidth_mhz=self._effective_radio.bandwidth_mhz,
                radio_control_port=self.config.radio_control_port,
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
                    if self.network_adapter.is_tun_ready(
                            self.config.tun_name, self.config.tun_cidr, self.config.server_control_host):
                        return
                    time.sleep(0.05)
                raise FLRuntimeError(
                    "link_tun_failed",
                    f"客户端链路 TUN 地址或控制路由未就绪: {self.config.tun_name}",
                )
            except Exception:
                self._stop_idle_link()
                raise

    def _stop_idle_link(self) -> None:
        """Stop the idle client link and release its TUN."""
        with self._transport_lock:
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
                if self._link_process.poll() is None:
                    raise FLRuntimeError('idle_link_not_exited', '旧待命链路尚未退出，拒绝任务接管')
                self._link_process = None
            if self._link_log_file is not None:
                try:
                    self._link_log_file.close()
                except Exception:
                    pass
                self._link_log_file = None
            self.network_adapter.teardown_tun(self.config.tun_name)

    def _ensure_idle_transport(self) -> None:
        if self.current_interface is None or self.sandbox.state in (DaemonState.STOPPED, DaemonState.PREPARING) or self.sandbox.is_running:
            return
        if self.config.enable_link_process and self.config.enable_control_plane:
            self.start_idle_link()
        else:
            self._restore_tun_if_idle()

    def _on_interface_lost(self) -> None:
        """硬件丢失仍通过同一回收所有者处理，锁外等待工作线程。"""
        self._job_stop_event.set()
        with self._lock:
            job_id, worker = self._current_job_id, self._startup_thread
            control = self.control_plane
            self.control_plane = None
            self.current_interface = None
            if control is not None and control.locked_channel is not None:
                self._last_locked_channel = control.locked_channel
        if worker is not None and worker is not threading.current_thread():
            worker.join()
        if job_id is not None:
            self._recover_job_resources(job_id, 'aborted')
        else:
            with self._recovery_lock:
                self.sandbox.abort()
        if control is not None:
            control.stop()
        self._stop_idle_link()

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
        with self._recovery_lock:
            with self._lock:
                if (self._current_job_id is not None and
                        threading.current_thread() is not self._startup_thread):
                    raise FLRuntimeError('job_recovery_pending', '前一作业恢复尚未获得服务器确认，拒绝接单')
                if threading.current_thread() is self._startup_thread and (self._job_stop_event.is_set() or self._stop_event.is_set()):
                    raise FLRuntimeError('job_stopped', '启动前作业已停止')
                if self.control_plane is not None and self.control_plane.radio_switch_pending:
                    raise FLRuntimeError('radio_switch_pending', '射频切换未完成，拒绝启动作业')
                if self.state == DaemonState.STOPPED:
                    raise FLRuntimeError('sandbox_stopped', '任务资源清理失败，拒绝启动新作业')
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
                channel = (self.control_plane.current_channel if self.control_plane is not None
                           else self._effective_radio.channel)
                job_config = replace(
                    job_config,
                    channel=channel,
                    bandwidth_mhz=self._effective_radio.bandwidth_mhz,
                    radio_txpower_dbm=self._effective_radio.radio_txpower_dbm,
                    uplink_mcs=self._effective_radio.uplink_mcs,
                )
                self._current_job_id = job_config.job_id
                self._last_accepted_job_id = job_config.job_id
            self._stop_idle_link()
            try:
                proc = self.sandbox.start(job_config, air_interface=self.current_interface)
                return proc
            except Exception:
                self._ensure_idle_transport()
                raise

    def _handle_job_terminal(self, msg: Dict[str, Any], aborted: bool) -> None:
        """下行线程只登记终态，退出确认与报告交工作线程。"""
        job_id = msg.get('job_id')
        with self._lock:
            fact = self._recovery_fact
            if (not isinstance(job_id, str) or
                    (job_id != self._current_job_id and
                     (fact is None or job_id != fact[0] or
                      job_id != self._last_accepted_job_id))):
                return
            if self._terminal_job_id != job_id:
                self._terminal_job_id = job_id
                self._terminal_aborted = aborted
            if self._current_job_id == job_id:
                self._job_stop_event.set()
            self._recovery_pending = job_id
            if self._recovery_thread is None:
                self._recovery_thread = threading.Thread(
                    target=self._report_job_recovery,
                    name='fl-client-recovery-' + job_id, daemon=True)
                self._recovery_thread.start()

    def _recover_job_resources(self, job_id: str, outcome: str, *, retry: bool = False) -> None:
        """串行回收任务资源，只缓存实际验证后的恢复事实。"""
        with self._recovery_lock:
            with self._lock:
                fact = self._recovery_fact
                if fact is not None and fact[0] == job_id and (not retry or fact[2] == 'ready'):
                    return
                if self._current_job_id != job_id:
                    return
                epoch = fact[1] + 1 if fact is not None and fact[0] == job_id else 1
            code = reason = None
            state = 'ready'
            try:
                self.sandbox.supervised = False
                self.sandbox.abort(outcome=outcome)
                if self._stop_event.is_set():
                    raise FLRuntimeError('client_stopped', '客户端正在退出，待命链路未恢复')
                if self.current_interface is None:
                    raise FLRuntimeError('hardware_unavailable', '无线网卡不可用，待命链路未恢复')
                self._ensure_idle_transport()
                if self.config.enable_link_process and self.config.enable_control_plane:
                    if not self.is_idle_link_running:
                        raise FLRuntimeError('idle_link_recovery_failed', '待命链路进程未运行')
                if not self.network_adapter.is_tun_ready(
                        self.config.tun_name, self.config.tun_cidr,
                        self.config.server_control_host):
                    raise FLRuntimeError('idle_link_recovery_failed', '待命 TUN 地址或控制路由未就绪')
            except Exception as exc:
                state = 'blocked'
                code = str(getattr(exc, 'error_code', None) or 'client_recovery_failed').encode('utf-8')[:128].decode('utf-8', 'ignore')
                reason = str(getattr(exc, 'error_message', None) or '客户端资源恢复失败').encode('utf-8')[:512].decode('utf-8', 'ignore')
                logger.exception('作业 %s 资源未恢复', job_id)
            with self._lock:
                self._recovery_fact = (job_id, epoch, state, code, reason, uuid.uuid4().hex)
                if self.control_plane is not None:
                    self.control_plane.notify_state_change(
                        ClientNodeState.ABORTING)

    def _report_job_recovery(self) -> None:
        """终态重试只重报缓存事实，不再次复位任务或待命链路。"""
        while True:
            with self._lock:
                job_id = self._recovery_pending
                self._recovery_pending = None
                if job_id is None:
                    self._recovery_thread = None
                    return
                worker = self._startup_thread if self._current_job_id == job_id else None
                aborted = self._terminal_aborted
            if worker is not None and worker is not threading.current_thread():
                worker.join()
            self._recover_job_resources(job_id, 'aborted' if aborted else 'succeeded')
            with self._lock:
                fact, control = self._recovery_fact, self.control_plane
            if fact is not None and fact[0] == job_id and control is not None:
                try:
                    control.notify_job_recovery(
                        fact[0], fact[1], fact[2], error_code=fact[3], reason=fact[4],
                        report_id=fact[5])
                    with self._lock:
                        if (self.control_plane is control and self._recovery_fact == fact and
                                self._current_job_id == job_id and fact[2] == 'ready'):
                            self._current_job_id = None
                            control.notify_state_change(ClientNodeState.IDLE)
                except Exception:
                    logger.exception('作业 %s 恢复报告未获确认，保留事实供重报', job_id)
            with self._lock:
                if self._recovery_pending is None:
                    self._recovery_thread = None
                    return

    def _terminate_job(self, intermediate_state: Optional[ClientNodeState] = None) -> None:
        """本地显式清理可在 blocked 后重新验证，重复广播不会触发此路径。"""
        with self._lock:
            self._job_stop_event.set()
            job_id, worker = self._current_job_id, self._startup_thread
        if worker is not None and worker is not threading.current_thread():
            worker.join()
        if job_id is not None:
            self._recover_job_resources(
                job_id, 'aborted' if intermediate_state is not None else 'succeeded', retry=True)
            if self._terminal_job_id == job_id:
                self._handle_job_terminal({'job_id': job_id}, self._terminal_aborted)

    def finish_job(self) -> None:
        self._terminate_job()

    def abort_job(self) -> None:
        self._terminate_job(intermediate_state=ClientNodeState.ABORTING)

    def wait_job(self, timeout: Optional[float] = None) -> int:
        ret = self.sandbox.wait(timeout=timeout)
        with self._lock:
            self._ensure_idle_transport()
            if (self.control_plane is not None and not self.sandbox.is_running and
                    self._terminal_job_id != self._current_job_id):
                self.control_plane.notify_state_change(ClientNodeState.IDLE)
        return ret

    def _handle_task_announce(self, msg: Dict[str, Any]) -> None:
        """Handle incoming TASK_ANNOUNCE broadcast from server coordinator."""
        with self._lock:
            announced_job_id = msg.get('job_id')
            if isinstance(announced_job_id, str) and announced_job_id == self._last_accepted_job_id:
                return
            target_nodes = msg.get("target_nodes")
            if (not isinstance(target_nodes, (list, tuple)) or not target_nodes or
                    any(type(node) is not int or not 1 <= node <= 255 for node in target_nodes) or
                    len(set(target_nodes)) != len(target_nodes) or self.config.node_id not in target_nodes):
                return
            if self.control_plane is not None and self.control_plane.radio_switch_pending:
                logger.warning("射频切换未完成，忽略 TASK_ANNOUNCE")
                return
            if (self._current_job_id is not None or
                    self.state != DaemonState.IDLE or
                    (self._startup_thread is not None and self._startup_thread.is_alive())):
                logger.warning("收到 TASK_ANNOUNCE 但节点非 IDLE (当前: %s)，忽略", self.state)
                return
            if self.control_plane is not None:
                self.control_plane.notify_state_change(ClientNodeState.PREPARING)

            def _reject(reason: str) -> None:
                logger.error(reason)
                control = self.control_plane
                if control is None:
                    return
                try:
                    rejected_job_id = validate_path_safe_identifier(msg.get('job_id'), 'job_id')
                except FLRuntimeError:
                    control.notify_state_change(ClientNodeState.IDLE)
                    return

                # 合法身份的拒绝也保留绑定，服务器终态才能取得恢复事实。
                self._current_job_id = rejected_job_id
                self._last_accepted_job_id = rejected_job_id
                self._terminal_job_id = None
                self._job_stop_event = threading.Event()

                def report_rejection():
                    try:
                        control.notify_task_error(rejected_job_id, 'prepare', 'invalid_job_config',
                                                  reason, timeout=8.0)
                    except Exception:
                        logger.exception('配置拒绝报告未获确认')
                    finally:
                        with self._lock:
                            if self.control_plane is control:
                                control.notify_state_change(ClientNodeState.ABORTING)

                self._startup_thread = threading.Thread(target=report_rejection,
                                                        name='fl-client-rejection', daemon=True)
                self._startup_thread.start()

            try:
                run_id = validate_path_safe_identifier(msg.get("run_id"), "run_id")
                job_id = validate_path_safe_identifier(msg.get("job_id"), "job_id")
            except FLRuntimeError as exc:
                return _reject(f"TASK_ANNOUNCE 标识非法: {exc}")

            raw_io_timeout = msg.get("io_timeout_seconds")
            if (isinstance(raw_io_timeout, bool) or
                    not isinstance(raw_io_timeout, (int, float)) or
                    not math.isfinite(raw_io_timeout) or raw_io_timeout <= 0):
                return _reject(f"TASK_ANNOUNCE 缺失或无效的 io_timeout_seconds 字段: {raw_io_timeout!r}")
            io_timeout_seconds = raw_io_timeout

            raw_live_obs = msg.get("live_observation")
            if raw_live_obs is None or type(raw_live_obs) is not bool:
                return _reject(f"TASK_ANNOUNCE 缺失或无效的 live_observation 字段: {raw_live_obs!r}")
            live_observation = raw_live_obs

            algorithm = msg.get("algorithm")
            if not isinstance(algorithm, str) or not algorithm:
                return _reject("TASK_ANNOUNCE 缺失 algorithm 字段，拒绝启动")

            raw_algo_config = msg.get("algorithm_config")
            if not isinstance(raw_algo_config, dict):
                return _reject("TASK_ANNOUNCE 缺失有效的 algorithm_config，拒绝启动")

            if "rounds" not in msg:
                return _reject("TASK_ANNOUNCE 缺失 rounds 字段，拒绝启动")

            rounds = msg['rounds']
            preparation_timeout = msg.get('preparation_timeout_seconds')
            if (type(rounds) is not int or rounds <= 0 or
                    isinstance(preparation_timeout, bool) or
                    not isinstance(preparation_timeout, (int, float)) or
                    not math.isfinite(preparation_timeout) or preparation_timeout <= 0):
                return _reject('TASK_ANNOUNCE 轮数或准备期限无效')
            if any(key in msg for key in ('mnist_data_dir', 'local_config')):
                return _reject('TASK_ANNOUNCE 不得提供本机数据路径')
            try:
                algo_config = validate_algorithm_config(raw_algo_config)
            except FLRuntimeError as exc:
                return _reject(exc.error_message)

            if "server_http_host" not in msg or "server_http_port" not in msg or "uftp_port" not in msg or "link_id" not in msg:
                return _reject("TASK_ANNOUNCE 缺失网络配置字段，拒绝启动")

            if (not isinstance(msg['server_http_host'], str) or not msg['server_http_host'] or
                    any(type(msg[key]) is not int or not 1 <= msg[key] <= 65535
                        for key in ('server_http_port', 'uftp_port')) or
                    type(msg['link_id']) is not int or msg['link_id'] <= 0):
                return _reject('TASK_ANNOUNCE 网络配置无效')
            server_http_host = msg['server_http_host']
            server_http_port = msg['server_http_port']
            uftp_port = msg['uftp_port']
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
                target_nodes=tuple(target_nodes or ()),
                rounds=rounds,
                preparation_timeout_seconds=preparation_timeout,
                io_timeout_seconds=io_timeout_seconds,
                live_observation=live_observation,
            )
            self._current_job_id = job_id
            self._last_accepted_job_id = job_id
            self._terminal_job_id = None
            self._job_stop_event = threading.Event()
            self.sandbox.state = DaemonState.PREPARING
            self.sandbox.supervised = True
            self._startup_thread = threading.Thread(
                target=self._run_job, args=(job_config, self._job_stop_event),
                name='fl-client-job-' + job_id, daemon=True)
            self._startup_thread.start()

    def _run_job(self, job, stop_event):
        outcome = 'failed'
        try:
            if stop_event.is_set() or self._stop_event.is_set():
                return
            self.trigger_job(job)
            if stop_event.is_set() or self._stop_event.is_set():
                return

            preparation_deadline = self.sandbox._preparation_deadline
            if preparation_deadline is None:
                raise FLRuntimeError('sandbox_start_failed', '任务沙箱缺少准备期限')

            def forward(event):
                control = self.control_plane
                if control is None or stop_event.is_set():
                    raise FLRuntimeError('job_stopped', '作业停止或控制面不可用')
                timeout = min(8.0, max(0.001, preparation_deadline - time.monotonic())
                              if event['event'] == 'prepared' else job.io_timeout_seconds)
                if event['event'] == 'prepared':
                    control.notify_task_ready(job.job_id, event['report'], timeout=timeout)
                    with self._lock:
                        if not stop_event.is_set() and self.control_plane is control:
                            control.notify_state_change(ClientNodeState.RUNNING)
                elif event['event'] == 'round_complete':
                    control.notify_round_complete(job.job_id, event['round_index'], timeout=timeout)
                else:
                    control.notify_task_error(job.job_id, event['phase'], event['code'], event['reason'],
                                              round_index=event.get('round_index'), timeout=timeout)

            self.sandbox.monitor(forward, stop_event)
            outcome = 'succeeded' if self.sandbox._completed_round == job.rounds else 'failed'
        except Exception as exc:
            logger.exception('客户端作业 %s 失败', job.job_id)
            if not stop_event.is_set() and self.control_plane is not None:
                try:
                    self.control_plane.notify_task_error(
                        job.job_id, 'prepare' if not self.sandbox._prepared else 'round',
                        getattr(exc, 'error_code', 'client_job_failed'),
                        getattr(exc, 'error_message', '客户端作业执行失败'),
                        round_index=self.sandbox._completed_round + 1 if self.sandbox._prepared else None,
                        timeout=min(job.io_timeout_seconds, 8.0))
                except Exception:
                    logger.exception('客户端作业失败报告未获确认')
        finally:
            self._recover_job_resources(
                job.job_id, 'aborted' if stop_event.is_set() else outcome)

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
                    initial_radio_config=self._effective_radio,
                    calibration=self._effective_radio_calibration,
                    cached_channel=self._last_locked_channel or self._effective_radio.channel,
                    server_host=self.config.server_control_host,
                    server_port=self.config.server_control_port,
                    broadcast_port=self.config.broadcast_port,
                    state_lock=self._lock,
                )
                self.control_plane.on_radio_applied = self._on_radio_applied
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

    def _on_radio_applied(self, new_config: RadioConfig) -> None:
        """本机在线更新成功后才发布生效配置，失败交由原事务租约恢复。"""
        with self._lock:
            if self._current_job_id is not None or self.sandbox.is_running:
                raise FLRuntimeError('radio_job_active', '作业运行时拒绝更新空闲射频链路')
            if self.is_idle_link_running:
                # 每次都发送完整目标，包括失败后的回退，不能用缓存推断发射器状态。
                apply_link_radio(self.config.radio_control_port,
                                 new_config.bandwidth_mhz, new_config.uplink_mcs)
                self._effective_radio = new_config
            else:
                previous = self._effective_radio
                self._effective_radio = new_config
                try:
                    self._ensure_idle_transport()
                except Exception:
                    self._effective_radio = previous
                    raise
            # 与成功应用的完整快照一起保留模式，供网卡重入和控制面重建使用。
            self._effective_radio_calibration = (
                self.control_plane.calibration if self.control_plane is not None else False
            )

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
                and self._current_job_id is None
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
            if self._current_job_id is None or not self.sandbox.supervised:
                self.sandbox.poll()
            if (
                self.control_plane is not None
                and self._current_job_id is None
                and not self.sandbox.is_running
                and self.control_plane.state in (ClientNodeState.RUNNING, ClientNodeState.PREPARING)
            ):
                self.control_plane.notify_state_change(ClientNodeState.IDLE)

            if self.control_plane is not None and self.control_plane.locked_channel is not None:
                if self.control_plane.locked_channel != self._last_locked_channel:
                    self._last_locked_channel = self.control_plane.locked_channel
                    self._save_cached_channel(self.control_plane.locked_channel)

            if (self.current_interface is not None and self._current_job_id is None
                    and not self.sandbox.is_running):
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
        self._job_stop_event.set()
        self._cleanup()

    def _cleanup(self) -> None:
        self._job_stop_event.set()
        for worker in (self._startup_thread, self._recovery_thread):
            if worker is not None and worker is not threading.current_thread():
                worker.join()
        try:
            with self._recovery_lock:
                try:
                    self.sandbox.abort()
                finally:
                    self._stop_idle_link()
        finally:
            with self._lock:
                control = self.control_plane
                self.control_plane = None
            if control is not None:
                control.stop()
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
    parser.add_argument("--bandwidth-mhz", type=int, choices=(20, 40), default=DEFAULT_BANDWIDTH_MHZ)
    parser.add_argument("--radio-control-port", type=int, default=DEFAULT_RADIO_CONTROL_PORT)
    parser.add_argument("--mnist-data-dir", help="本机预置 MNIST IDX 数据目录")
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
        bandwidth_mhz=args.bandwidth_mhz,
        radio_control_port=args.radio_control_port,
        mnist_data_dir=args.mnist_data_dir,
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
