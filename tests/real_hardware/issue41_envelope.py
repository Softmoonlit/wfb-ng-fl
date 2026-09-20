#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Issue 41 可审计三机运行包络 (Run Envelope)

负责三机硬件运行的生命周期管控：
- 唯一不可覆盖的 run ID 与归档目录
- 动态拓扑重新发现与网卡身份绑定
- 严格分层 preflight（8层 fail-closed 检查并产生失败分类）
- 受控停止与残留隔离
- 供后续阶段追加独立证据分区的单一接口
- 绑定 branch/commit/工作区状态/解析配置/运行模式
"""

import argparse
from datetime import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time


class FailureCategory:
    ENVIRONMENT = 'environment'
    TOOLING = 'tooling'
    IMPLEMENTATION = 'implementation'
    LINK_CAPABILITY = 'link_capability'


FAILURE_CATEGORIES = (
    FailureCategory.ENVIRONMENT,
    FailureCategory.TOOLING,
    FailureCategory.IMPLEMENTATION,
    FailureCategory.LINK_CAPABILITY,
)


class PreflightLayer:
    REPO_AND_VERSION = 'repo_and_version'
    SSH_AND_SUDO = 'ssh_and_sudo'
    DEPENDENCIES = 'dependencies'
    WIRELESS_USB = 'wireless_usb'
    RADIO_MONITOR_CHANNEL = 'radio_monitor_channel'
    NETWORK_PORTS_AND_TUN = 'network_ports_and_tun'
    RESIDUAL_PROCESSES = 'residual_processes'
    MANAGED_DIRECTORY_BOUNDARY = 'managed_directory_boundary'


PREFLIGHT_LAYERS = (
    PreflightLayer.REPO_AND_VERSION,
    PreflightLayer.SSH_AND_SUDO,
    PreflightLayer.DEPENDENCIES,
    PreflightLayer.WIRELESS_USB,
    PreflightLayer.RADIO_MONITOR_CHANNEL,
    PreflightLayer.NETWORK_PORTS_AND_TUN,
    PreflightLayer.RESIDUAL_PROCESSES,
    PreflightLayer.MANAGED_DIRECTORY_BOUNDARY,
)


def generate_run_id(prefix='v8_issue41_'):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    token = secrets.token_hex(3)
    return f"{prefix}{timestamp}_{token}"


class RealExecutor:
    """真实环境执行器，针对 server 本地执行，针对 client1/client2 通过 SSH 执行。"""

    def __init__(self, client_ssh_map=None):
        self.client_ssh_map = client_ssh_map or {
            'client1': os.environ.get('ISSUE41_CLIENT1_SSH', 'vm1'),
            'client2': os.environ.get('ISSUE41_CLIENT2_SSH', 'vm2'),
        }

    def run(self, target, cmd, timeout=30):
        try:
            if target == 'server':
                res = subprocess.run(
                    cmd, shell=True, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
            else:
                ssh_host = self.client_ssh_map.get(target, target)
                res = subprocess.run(
                    ['ssh', '-o', 'BatchMode=yes', ssh_host, cmd],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    timeout=timeout, check=False)
            return res.returncode, res.stdout, res.stderr
        except subprocess.TimeoutExpired as exc:
            return 124, '', f'Command timed out after {timeout}s'
        except Exception as exc:
            return 1, '', str(exc)


class RunEnvelope:
    def __init__(self, run_id=None, archive_root=None, archive_dir=None, branch=None, commit=None,
                 mode='formal', resolved_config=None, remote_repo=None, client_ssh_map=None):
        self.run_id = run_id or generate_run_id()
        if archive_dir:
            self.archive_dir = os.path.abspath(archive_dir)
            self.archive_root = os.path.dirname(self.archive_dir)
        else:
            self.archive_root = os.path.abspath(
                archive_root or os.environ.get('ISSUE41_ARCHIVE_ROOT',
                                               os.path.join(os.getcwd(), 'tests/logs')))
            self.archive_dir = os.path.join(self.archive_root, self.run_id)
        self.branch = branch
        self.commit = commit
        self.mode = mode
        self.resolved_config = resolved_config or {}
        self.remote_repo = remote_repo or os.environ.get('ISSUE41_REMOTE_REPO', '/home/virt/projects/wfb-ng-fl')
        self.client_ssh_map = client_ssh_map or {
            'client1': os.environ.get('ISSUE41_CLIENT1_SSH', 'vm1'),
            'client2': os.environ.get('ISSUE41_CLIENT2_SSH', 'vm2'),
        }
        self.topology = {}
        self.partitions = {}
        self.state = 'uninitialized'
        self.conclusion = None

    def initialize(self):
        """初始化运行包络目录和元数据文件，若归档目录已存在则 fail-closed。"""
        if os.path.exists(self.archive_dir):
            raise FileExistsError(f"归档目录已存在，不可覆盖：{self.archive_dir}")

        for subdir in ('orchestration', 'pre_runtime_smoke', 'formal_runtime_loop',
                       'lifecycle', 'raw'):
            os.makedirs(os.path.join(self.archive_dir, subdir), exist_ok=True)

        envelope_metadata = {
            'schema_version': 1,
            'run_id': self.run_id,
            'mode': self.mode,
            'branch': self.branch,
            'commit': self.commit,
            'archive_dir': self.archive_dir,
            'network_isolation': {
                'data_plane': '10.80.0.0/24',
                'management_network': [self.client_ssh_map.get('client1', 'vm1'),
                                       self.client_ssh_map.get('client2', 'vm2')],
                'prohibit_management_as_data_plane': True,
            },
            'resolved_config': self.resolved_config,
        }
        envelope_path = os.path.join(self.archive_dir, 'envelope.json')
        with open(envelope_path, 'w', encoding='utf-8') as fh:
            json.dump(envelope_metadata, fh, indent=2, ensure_ascii=False)
            fh.write('\n')

        self.state = 'initialized'
        return self.archive_dir

    def discover_topology(self, executor):
        """现场重新发现三机拓扑事实，并归档至 topology.json。"""
        if self.state == 'closed':
            raise RuntimeError("包络已关闭，无法执行拓扑发现")

        roles = ('server', 'client1', 'client2')
        topology = {}

        for role in roles:
            node_info = {'role': role}
            repo = os.getcwd() if role == 'server' else self.remote_repo

            # 1. 主机身份
            rc, out, _ = executor.run(role, 'hostname')
            node_info['hostname'] = out.strip() if rc == 0 else 'unknown'

            rc, out, _ = executor.run(role, 'cat /etc/machine-id 2>/dev/null || cat /var/lib/dbus/machine-id 2>/dev/null')
            node_info['machine_id'] = out.strip() if rc == 0 else 'unknown'

            # 2. 仓库位置与版本
            node_info['repo_root'] = repo
            rc, out, _ = executor.run(role, f"cd '{repo}' && git rev-parse HEAD")
            node_info['commit'] = out.strip() if rc == 0 else ''

            rc, out, _ = executor.run(role, f"cd '{repo}' && git rev-parse --abbrev-ref HEAD")
            node_info['branch'] = out.strip() if rc == 0 else ''

            rc, out, _ = executor.run(role, f"cd '{repo}' && git status --short")
            node_info['workspace_clean'] = (rc == 0 and out.strip() == '')

            # 3. 管理网 IP / 标识
            if role == 'server':
                node_info['management_target'] = 'localhost'
            else:
                node_info['management_target'] = self.client_ssh_map.get(role, role)

            # 4. 真实无线网卡接口 (匹配 wlx*)
            rc, out, _ = executor.run(role, "iw dev")
            interfaces = sorted(list(set(re.findall(r'\b(wlx[0-9a-zA-Z]+)\b', out))))
            if len(interfaces) != 1:
                raise RuntimeError(f"{role} 必须恰好发现一个 wlx* 网卡，实际发现：{interfaces}")
            wlx_iface = interfaces[0]
            node_info['wireless_interface'] = wlx_iface

            # 5. MAC 地址
            rc, out, _ = executor.run(role, f"cat /sys/class/net/{wlx_iface}/address")
            node_info['wireless_mac'] = out.strip() if rc == 0 else 'unknown'

            # 6. 网卡驱动
            rc, out, _ = executor.run(role, f"readlink -f /sys/class/net/{wlx_iface}/device/driver")
            node_info['driver'] = os.path.basename(out.strip()) if rc == 0 else 'unknown'

            # 7. USB 速率
            rc, out, _ = executor.run(role, f"cat /sys/class/net/{wlx_iface}/device/../speed 2>/dev/null || cat /sys/class/net/{wlx_iface}/device/speed 2>/dev/null || echo unknown")
            node_info['usb_speed'] = out.strip() if rc == 0 else 'unknown'

            topology[role] = node_info

        self.topology = topology
        topo_path = os.path.join(self.archive_dir, 'orchestration', 'topology.json')
        with open(topo_path, 'w', encoding='utf-8') as fh:
            json.dump(topology, fh, indent=2, ensure_ascii=False)
            fh.write('\n')

        return topology

    def run_preflight(self, executor, expected_branch=None, strict_usb=False,
                      min_usb_speed=480, reset_runtime_state=False):
        """执行 8 层严格 preflight 校验，任一失败立即产生 failed 归档并退出。"""
        if self.state == 'closed':
            raise RuntimeError("包络已关闭，无法执行 preflight")

        last_successful = None
        first_failing = None
        failure_cat = None
        failure_reason = None

        try:
            # Layer 1: repo_and_version
            l_name = PreflightLayer.REPO_AND_VERSION
            server_topo = self.topology.get('server', {})
            c1_topo = self.topology.get('client1', {})
            c2_topo = self.topology.get('client2', {})

            if expected_branch:
                for role, t in self.topology.items():
                    if t.get('branch') != expected_branch:
                        raise PreflightException(l_name, FailureCategory.IMPLEMENTATION,
                                                 f"{role} 分支为 {t.get('branch')}，非预期 {expected_branch}")

            server_commit = server_topo.get('commit')
            for role in ('client1', 'client2'):
                r_commit = self.topology.get(role, {}).get('commit')
                if not r_commit or r_commit != server_commit:
                    raise PreflightException(l_name, FailureCategory.IMPLEMENTATION,
                                             f"{role} commit 与本机不一致：{r_commit} != {server_commit}")

            for role, t in self.topology.items():
                if not t.get('workspace_clean'):
                    raise PreflightException(l_name, FailureCategory.IMPLEMENTATION,
                                             f"{role} 工作区不干净，存在未提交文件")
            last_successful = l_name

            # Layer 2: ssh_and_sudo
            l_name = PreflightLayer.SSH_AND_SUDO
            for role in ('client1', 'client2'):
                rc, _, err = executor.run(role, 'ssh_check echo ok')
                if rc != 0:
                    raise PreflightException(l_name, FailureCategory.ENVIRONMENT,
                                             f"SSH 到 {role} 失败：{err.strip()}")
            for role in ('server', 'client1', 'client2'):
                rc, _, err = executor.run(role, 'sudo -n true')
                if rc != 0:
                    raise PreflightException(l_name, FailureCategory.ENVIRONMENT,
                                             f"{role} 免密 sudo -n true 失败：{err.strip()}")
            last_successful = l_name

            # Layer 3: dependencies
            l_name = PreflightLayer.DEPENDENCIES
            required_cmds = ('ip', 'iw', 'systemctl', 'journalctl', 'make', 'python3', 'uftp', 'uftpd')
            for role in ('server', 'client1', 'client2'):
                for cmd in required_cmds:
                    rc, _, _ = executor.run(role, f"command -v {cmd}")
                    if rc != 0:
                        raise PreflightException(l_name, FailureCategory.TOOLING,
                                                 f"{role} 缺少必要命令依赖：{cmd}")

            # 4 MiB 输入模型和 update 模板检查
            initial_model = self.resolved_config.get('initial_model_path')
            if initial_model:
                rc, out, _ = executor.run('server', f"test -f '{initial_model}' && test -r '{initial_model}' && stat -c %s '{initial_model}'")
                if rc != 0 or out.strip() != str(4 * 1024 * 1024):
                    raise PreflightException(l_name, FailureCategory.TOOLING,
                                             f"server 初始模型必须是可读的 4 MiB 文件：{initial_model}")

            c1_tpl = self.resolved_config.get('client1_update_template_path')
            c2_tpl = self.resolved_config.get('client2_update_template_path')
            c1_sha, c2_sha = None, None
            if c1_tpl:
                rc, out, _ = executor.run('client1', f"sudo test -f '{c1_tpl}' && sudo test -r '{c1_tpl}' && sudo stat -c %s '{c1_tpl}'")
                if rc != 0 or out.strip() != str(4 * 1024 * 1024):
                    raise PreflightException(l_name, FailureCategory.TOOLING,
                                             f"client1 update 模板必须是可读的 4 MiB 文件：{c1_tpl}")
                rc, out, _ = executor.run('client1', f"sudo sha256sum '{c1_tpl}'")
                c1_sha = out.split()[0] if rc == 0 else ''

            if c2_tpl:
                rc, out, _ = executor.run('client2', f"sudo test -f '{c2_tpl}' && sudo test -r '{c2_tpl}' && sudo stat -c %s '{c2_tpl}'")
                if rc != 0 or out.strip() != str(4 * 1024 * 1024):
                    raise PreflightException(l_name, FailureCategory.TOOLING,
                                             f"client2 update 模板必须是可读的 4 MiB 文件：{c2_tpl}")
                rc, out, _ = executor.run('client2', f"sudo sha256sum '{c2_tpl}'")
                c2_sha = out.split()[0] if rc == 0 else ''

            if c1_sha and c2_sha and c1_sha == c2_sha:
                raise PreflightException(l_name, FailureCategory.TOOLING,
                                         "client1 与 client2 的 4 MiB update 模板 SHA-256 必须不同")
            last_successful = l_name

            # Layer 4: wireless_usb
            l_name = PreflightLayer.WIRELESS_USB
            for role in ('server', 'client1', 'client2'):
                w_iface = self.topology.get(role, {}).get('wireless_interface')
                if not w_iface:
                    raise PreflightException(l_name, FailureCategory.ENVIRONMENT,
                                             f"{role} 未发现有效无线网卡")
                speed_str = self.topology.get(role, {}).get('usb_speed', 'unknown')
                try:
                    speed_val = float(speed_str)
                    if speed_val < min_usb_speed and strict_usb:
                        raise PreflightException(l_name, FailureCategory.ENVIRONMENT,
                                                 f"{role} 网卡 USB speed={speed_val}Mbps 低于严格阈值 {min_usb_speed}Mbps")
                except ValueError:
                    if strict_usb:
                        raise PreflightException(l_name, FailureCategory.ENVIRONMENT,
                                                 f"{role} 无法确认无线网卡 USB 速度：{speed_str}")
            last_successful = l_name

            # Layer 5: radio_monitor_channel
            l_name = PreflightLayer.RADIO_MONITOR_CHANNEL
            for role in ('server', 'client1', 'client2'):
                w_iface = self.topology.get(role, {}).get('wireless_interface')
                rc, out, _ = executor.run(role, f"iw dev {w_iface} info")
                if rc != 0 or 'type monitor' not in out:
                    raise PreflightException(l_name, FailureCategory.ENVIRONMENT,
                                             f"{role} 空口网卡 {w_iface} 未进入 monitor 模式：{out.strip()}")
                rc, out, _ = executor.run(role, f"ip link show {w_iface}")
                if rc != 0 or '<' not in out or 'UP' not in out:
                    raise PreflightException(l_name, FailureCategory.ENVIRONMENT,
                                             f"{role} 空口网卡 {w_iface} 未处于 UP 状态：{out.strip()}")
            last_successful = l_name

            # Layer 6: network_ports_and_tun
            l_name = PreflightLayer.NETWORK_PORTS_AND_TUN
            ports_to_check = [
                self.resolved_config.get('uftp_port', 1044),
                self.resolved_config.get('http_port', 8080),
            ]
            for role in ('server', 'client1', 'client2'):
                rc, out, _ = executor.run(role, 'ss -tuln')
                if rc == 0:
                    for port in ports_to_check:
                        if re.search(rf':{port}\b', out):
                            raise PreflightException(l_name, FailureCategory.ENVIRONMENT,
                                                     f"{role} 关键端口 {port} 已被占用：{out.strip()}")

            # TUN 设备在 preflight 时不应存在
            tuns = {
                'server': self.resolved_config.get('server_tun', 'v8i41s0'),
                'client1': self.resolved_config.get('client1_tun', 'v8i41c1'),
                'client2': self.resolved_config.get('client2_tun', 'v8i41c2'),
            }
            for role, tun in tuns.items():
                rc, out, _ = executor.run(role, f"ip link show {tun}")
                if rc == 0 and ('mtu' in out or tun in out):
                    raise PreflightException(l_name, FailureCategory.ENVIRONMENT,
                                             f"{role} 上预期 TUN 设备 {tun} 已经存在")
            last_successful = l_name

            # Layer 7: residual_processes
            l_name = PreflightLayer.RESIDUAL_PROCESSES
            procs = ('wfb-fl-server', 'wfb-fl-client', 'wfb_v6_uplink', 'uftp', 'uftpd')
            for role in ('server', 'client1', 'client2'):
                for proc in procs:
                    rc, out, _ = executor.run(role, f"pgrep -x {proc}")
                    if rc == 0 and out.strip():
                        raise PreflightException(l_name, FailureCategory.ENVIRONMENT,
                                                 f"{role} 发现残留进程 {proc} (pid: {out.strip()})")
            last_successful = l_name

            # Layer 8: managed_directory_boundary
            l_name = PreflightLayer.MANAGED_DIRECTORY_BOUNDARY
            dirs_to_check = {
                'server': '/var/lib/wfb-ng/issue41/server',
                'client1': '/var/lib/wfb-ng/issue41/client',
                'client2': '/var/lib/wfb-ng/issue41/client',
            }
            for role, d in dirs_to_check.items():
                rc, out, _ = executor.run(role, f"check_work_dir_empty {d} || ([ -d '{d}' ] && [ -n \"$(ls -A '{d}' 2>/dev/null)\" ] && echo non-empty || echo empty)")
                if 'non-empty' in out:
                    if not reset_runtime_state:
                        raise PreflightException(l_name, FailureCategory.TOOLING,
                                                 f"{role} 受管工作目录 {d} 非空且未指定 reset_runtime_state")
            last_successful = l_name

        except PreflightException as exc:
            first_failing = exc.layer
            failure_cat = exc.category
            failure_reason = exc.reason

        if first_failing is not None:
            # Preflight 失败
            self.state = 'preflight_failed'
            preflight_result = {
                'status': 'failed',
                'last_successful_layer': last_successful,
                'first_failing_layer': first_failing,
                'failure_category': failure_cat,
                'failure_reason': failure_reason,
            }
            self.partitions['orchestration'] = preflight_result
            self._save_preflight_result(preflight_result)
            self._write_summary_and_result(conclusion_status='failed',
                                           reason=failure_reason,
                                           category=failure_cat,
                                           last_successful=last_successful,
                                           first_failing=first_failing)
            return False
        else:
            # Preflight 成功
            self.state = 'in_progress'
            preflight_result = {
                'status': 'passed',
                'last_successful_layer': last_successful,
                'first_failing_layer': None,
                'failure_category': None,
                'failure_reason': None,
            }
            self.partitions['orchestration'] = preflight_result
            self._save_preflight_result(preflight_result)
            self._write_summary_and_result(conclusion_status=None)
            return True

    def _save_preflight_result(self, result):
        path = os.path.join(self.archive_dir, 'orchestration', 'preflight_result.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
            fh.write('\n')

    def controlled_stop(self, executor, cleanup_timeout=5):
        """受控停止本次运行已经启动的所有资源，带超时轮询与残留审计。"""
        # 1. 停止角色服务
        executor.run('server', 'sudo systemctl stop wfb-fl-server.service 2>/dev/null || true')
        for role in ('client1', 'client2'):
            executor.run(role, 'sudo systemctl stop wfb-fl-client.service 2>/dev/null || true')

        # 2. 杀掉临时进程
        for role in ('server', 'client1', 'client2'):
            executor.run(role, 'sudo pkill -x wfb_v6_uplink 2>/dev/null || true')
            executor.run(role, 'sudo pkill -x uftp 2>/dev/null || true')
            executor.run(role, 'sudo pkill -x uftpd 2>/dev/null || true')
            executor.run(role, 'sudo pkill -f /var/tmp/wfb-ng-issue41-smoke 2>/dev/null || true')

        # 3. 轮询检查资源是否完全释放
        deadline = time.time() + cleanup_timeout
        residuals = []

        while time.time() < deadline:
            residuals = []
            for role in ('server', 'client1', 'client2'):
                for proc in ('wfb-fl-server', 'wfb-fl-client', 'wfb_v6_uplink', 'uftp', 'uftpd'):
                    rc, out, _ = executor.run(role, f"pgrep -x {proc}")
                    if rc == 0 and out.strip():
                        residuals.append(f"{role} 停止后仍有残留：进程 {proc} ({out.strip()})")

            tun_map = {
                'server': self.resolved_config.get('server_tun', 'v8i41s0'),
                'client1': self.resolved_config.get('client1_tun', 'v8i41c1'),
                'client2': self.resolved_config.get('client2_tun', 'v8i41c2'),
            }
            for role, tun in tun_map.items():
                rc, out, _ = executor.run(role, f"ip link show {tun}")
                if rc == 0 and ('mtu' in out or tun in out):
                    residuals.append(f"{role} 停止后仍有残留：TUN 设备 {tun}")

            if not residuals:
                break
            time.sleep(0.2)

        clean = (len(residuals) == 0)
        teardown_log = {
            'clean': clean,
            'timestamp': datetime.now().isoformat(),
            'residuals': residuals,
            'reason': f"停止后仍有残留：{'; '.join(residuals)}" if residuals else '受控停止完成，无残留资源',
        }
        teardown_path = os.path.join(self.archive_dir, 'orchestration', 'teardown_result.json')
        with open(teardown_path, 'w', encoding='utf-8') as fh:
            json.dump(teardown_log, fh, indent=2, ensure_ascii=False)
            fh.write('\n')

        return clean

    def append_partition(self, name, partition_data, artifacts=None):
        """供后续阶段追加独立证据分区的单一接口。"""
        if self.state == 'closed':
            raise RuntimeError("包络已关闭，拒绝修改")
        if self.state == 'preflight_failed':
            raise RuntimeError("preflight 失败，禁止追加后续数据面分区")

        if not isinstance(partition_data, dict):
            raise TypeError("分区数据必须为字典")

        # 防止混合 run ID
        data_run_id = partition_data.get('run_id')
        if data_run_id and data_run_id != self.run_id:
            raise ValueError(f"分区 run ID 不匹配：{data_run_id} != {self.run_id}")

        self.partitions[name] = partition_data

        if artifacts:
            for art_name, art_path in artifacts.items():
                dest = os.path.join(self.archive_dir, name, art_name)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if os.path.exists(art_path) and os.path.abspath(art_path) != os.path.abspath(dest):
                    shutil.copy2(art_path, dest)

        # 更新 summary
        self._write_summary_and_result()

    def close(self, reason='运行结束关闭'):
        """关闭并锁定运行包络。"""
        self.state = 'closed'
        close_meta = {
            'run_id': self.run_id,
            'closed_at': datetime.now().isoformat(),
            'reason': reason,
        }
        close_path = os.path.join(self.archive_dir, 'orchestration', 'envelope_closed.json')
        with open(close_path, 'w', encoding='utf-8') as fh:
            json.dump(close_meta, fh, indent=2, ensure_ascii=False)
            fh.write('\n')

    def _write_summary_and_result(self, conclusion_status=None, reason=None, category=None,
                                  last_successful=None, first_failing=None):
        orch = self.partitions.get('orchestration', {})
        orch_status = orch.get('status', 'skipped')

        smoke = self.partitions.get('pre_runtime_smoke', {})
        formal = self.partitions.get('formal_runtime_loop', {})
        lifecycle = self.partitions.get('lifecycle', {})

        if conclusion_status is None:
            # 自动推导
            all_parts = [orch]
            if 'pre_runtime_smoke' in self.partitions:
                all_parts.append(smoke.get('downlink_uftp', {}))
                all_parts.append(smoke.get('uplink_http_put', {}))
            if 'formal_runtime_loop' in self.partitions:
                all_parts.append(formal)
            if 'lifecycle' in self.partitions:
                all_parts.append(lifecycle)

            has_failed = any(p.get('status') == 'failed' for p in all_parts if isinstance(p, dict))
            all_passed = len(all_parts) >= 4 and all(p.get('status') == 'passed' for p in all_parts if isinstance(p, dict))

            if has_failed:
                conclusion_status = 'failed'
                reason = reason or '存在未通过的执行阶段'
            elif all_passed:
                conclusion_status = 'passed'
                reason = '所有阶段执行及验证通过'
            else:
                conclusion_status = 'in_progress' if self.state == 'in_progress' else 'failed'
                reason = reason or ('运行中' if conclusion_status == 'in_progress' else '运行未完全完成')

        conclusion_dict = {
            'status': conclusion_status,
            'reason': reason or ('通过' if conclusion_status == 'passed' else '失败'),
        }
        if category:
            conclusion_dict['category'] = category
        if last_successful:
            conclusion_dict['last_successful_layer'] = last_successful
        if first_failing:
            conclusion_dict['first_failing_layer'] = first_failing

        summary = {
            'schema_version': 1,
            'run_id': self.run_id,
            'orchestration': {
                'status': orch_status,
                'radio_health_dir': os.path.join(self.archive_dir, 'orchestration', 'radio-health'),
                'topology': self.topology,
                'last_successful_layer': orch.get('last_successful_layer'),
                'first_failing_layer': orch.get('first_failing_layer'),
            },
            'pre_runtime_smoke': smoke if smoke else {
                'downlink_uftp': {'status': 'skipped'},
                'uplink_http_put': {'status': 'skipped'},
            },
            'formal_runtime_loop': formal if formal else {
                'status': 'skipped',
            },
            'lifecycle': lifecycle if lifecycle else {'status': 'skipped'},
            'conclusion': conclusion_dict,
        }

        summary_path = os.path.join(self.archive_dir, 'issue41_summary.json')
        with open(summary_path, 'w', encoding='utf-8') as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False)
            fh.write('\n')

        result_md_path = os.path.join(self.archive_dir, 'result.md')
        with open(result_md_path, 'w', encoding='utf-8') as fh:
            fh.write(f"# issue41 运行结果：{self.run_id}\n\n")
            fh.write(f"- conclusion: {conclusion_status}\n")
            fh.write(f"- reason: {reason}\n")
            if category:
                fh.write(f"- failure_category: {category}\n")
            if first_failing:
                fh.write(f"- first_failing_layer: {first_failing}\n")
            if last_successful:
                fh.write(f"- last_successful_layer: {last_successful}\n")
            fh.write(f"- archive: {self.archive_dir}\n")


class PreflightException(Exception):
    def __init__(self, layer, category, reason):
        super().__init__(reason)
        self.layer = layer
        self.category = category
        self.reason = reason


def main(argv=None):
    parser = argparse.ArgumentParser(description='Issue 41 运行包络管理工具')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # init
    init_parser = subparsers.add_parser('init')
    init_parser.add_argument('--archive-dir', required=True)
    init_parser.add_argument('--run-id', required=True)
    init_parser.add_argument('--branch', default=None)
    init_parser.add_argument('--commit', default=None)
    init_parser.add_argument('--mode', choices=('formal', 'diagnostic'), default='formal')
    init_parser.add_argument('--config-json', default=None)

    # discover-topology
    topo_parser = subparsers.add_parser('discover-topology')
    topo_parser.add_argument('--archive-dir', required=True)

    # record-failure
    fail_parser = subparsers.add_parser('record-failure')
    fail_parser.add_argument('--archive-dir', required=True)
    fail_parser.add_argument('--layer', required=True)
    fail_parser.add_argument('--category', choices=FAILURE_CATEGORIES, required=True)
    fail_parser.add_argument('--reason', required=True)
    fail_parser.add_argument('--last-successful-layer', default=None)

    # append-partition
    part_parser = subparsers.add_parser('append-partition')
    part_parser.add_argument('--archive-dir', required=True)
    part_parser.add_argument('--name', required=True)
    part_parser.add_argument('--json-file', required=True)

    args = parser.parse_args(argv)

    if args.command == 'init':
        cfg = {}
        if args.config_json and os.path.isfile(args.config_json):
            with open(args.config_json, 'r', encoding='utf-8') as fh:
                cfg = json.load(fh)
        envelope = RunEnvelope(
            run_id=args.run_id,
            archive_dir=args.archive_dir,
            branch=args.branch,
            commit=args.commit,
            mode=args.mode,
            resolved_config=cfg,
        )
        archive_dir = envelope.initialize()
        print(f"ENVELOPE_INITIALIZED: {envelope.run_id} at {archive_dir}")
        return 0

    if args.command == 'discover-topology':
        archive_dir = os.path.abspath(args.archive_dir)
        env_path = os.path.join(archive_dir, 'envelope.json')
        if not os.path.isfile(env_path):
            print(f"错误：缺少 envelope.json：{env_path}", file=sys.stderr)
            return 1
        with open(env_path, 'r', encoding='utf-8') as fh:
            env_meta = json.load(fh)
        envelope = RunEnvelope(
            run_id=env_meta.get('run_id'),
            archive_dir=archive_dir,
            branch=env_meta.get('branch'),
            commit=env_meta.get('commit'),
            mode=env_meta.get('mode', 'formal'),
            resolved_config=env_meta.get('resolved_config', {}),
        )
        envelope.state = 'initialized'
        executor = RealExecutor()
        topo = envelope.discover_topology(executor)
        print(f"TOPOLOGY_DISCOVERED: {len(topo)} nodes")
        return 0

    if args.command == 'record-failure':
        archive_dir = os.path.abspath(args.archive_dir)
        env_path = os.path.join(archive_dir, 'envelope.json')
        env_meta = {}
        if os.path.isfile(env_path):
            with open(env_path, 'r', encoding='utf-8') as fh:
                env_meta = json.load(fh)
        envelope = RunEnvelope(
            run_id=env_meta.get('run_id', os.path.basename(archive_dir)),
            archive_dir=archive_dir,
            branch=env_meta.get('branch'),
            commit=env_meta.get('commit'),
            mode=env_meta.get('mode', 'formal'),
            resolved_config=env_meta.get('resolved_config', {}),
        )
        topo_path = os.path.join(archive_dir, 'orchestration', 'topology.json')
        if os.path.isfile(topo_path):
            with open(topo_path, 'r', encoding='utf-8') as fh:
                envelope.topology = json.load(fh)
        preflight_result = {
            'status': 'failed',
            'last_successful_layer': args.last_successful_layer,
            'first_failing_layer': args.layer,
            'failure_category': args.category,
            'failure_reason': args.reason,
        }
        envelope.partitions['orchestration'] = preflight_result
        envelope._save_preflight_result(preflight_result)
        envelope._write_summary_and_result(
            conclusion_status='failed',
            reason=args.reason,
            category=args.category,
            last_successful=args.last_successful_layer,
            first_failing=args.layer,
        )
        print(f"FAILURE_RECORDED: {args.layer} ({args.category}): {args.reason}")
        return 0

    if args.command == 'append-partition':
        archive_dir = os.path.abspath(args.archive_dir)
        env_path = os.path.join(archive_dir, 'envelope.json')
        env_meta = {}
        if os.path.isfile(env_path):
            with open(env_path, 'r', encoding='utf-8') as fh:
                env_meta = json.load(fh)
        envelope = RunEnvelope(
            run_id=env_meta.get('run_id', os.path.basename(archive_dir)),
            archive_dir=archive_dir,
            branch=env_meta.get('branch'),
            commit=env_meta.get('commit'),
            mode=env_meta.get('mode', 'formal'),
            resolved_config=env_meta.get('resolved_config', {}),
        )
        summary_path = os.path.join(archive_dir, 'issue41_summary.json')
        if os.path.isfile(summary_path):
            with open(summary_path, 'r', encoding='utf-8') as fh:
                existing_summary = json.load(fh)
                envelope.partitions = {
                    k: v for k, v in existing_summary.items()
                    if k in ('orchestration', 'pre_runtime_smoke', 'formal_runtime_loop', 'lifecycle')
                }
        with open(args.json_file, 'r', encoding='utf-8') as fh:
            part_data = json.load(fh)
        envelope.append_partition(args.name, part_data)
        print(f"PARTITION_APPENDED: {args.name}")
        return 0

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
