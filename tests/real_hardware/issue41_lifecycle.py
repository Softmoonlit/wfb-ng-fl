#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Issue #41 三角色服务 restart 与资源生命周期审计模块。

在三角色服务成功完成一轮作业并停止后，独立审计 stop、restart/no-overlap 和资源清理：
1. first_stop: 首次正常受控停止后，unit inactive、cgroup 无进程、TUN 消失、无孤儿进程。
2. restart: 启动新生命周期，重置/隔离旧未完成状态，验证服务达到 active 可运行状态且与旧进程无重叠 (no-overlap)。
3. second_stop: 再次受控停止，再次严密验证 unit inactive、cgroup 无进程、TUN 消失、无孤儿进程。
4. lifecycle summary: 输出独立的 lifecycle_summary.json 及原始证据文件，不从 Runtime 状态推导。
"""

import argparse
from dataclasses import dataclass, field
import json
import os
import sys
import time
from typing import Dict, List, Optional, Set, Tuple

from tests.real_hardware.issue41_envelope import RealExecutor


ROLES = ('server', 'client1', 'client2')
PROCS_TO_AUDIT = ('wfb-fl-server', 'wfb-fl-client', 'wfb_v6_uplink', 'uftp', 'uftpd')


@dataclass
class LifecycleConfig:
    roles: Tuple[str, ...] = ROLES
    server_unit: str = 'wfb-fl-server.service'
    client_unit: str = 'wfb-fl-client.service'
    server_tun: str = 'v8i41s0'
    client1_tun: str = 'v8i41c1'
    client2_tun: str = 'v8i41c2'
    server_work_dir: str = '/var/lib/wfb-ng/issue41/server'
    client_work_dir: str = '/var/lib/wfb-ng/issue41/client'
    timeout_seconds: float = 15.0
    poll_interval_seconds: float = 0.2
    client_ssh_map: Dict[str, str] = field(default_factory=lambda: {
        'client1': os.environ.get('ISSUE41_CLIENT1_SSH', 'vm1'),
        'client2': os.environ.get('ISSUE41_CLIENT2_SSH', 'vm2'),
    })

    def unit_for_role(self, role: str) -> str:
        return self.server_unit if role == 'server' else self.client_unit

    def tun_for_role(self, role: str) -> str:
        if role == 'server':
            return self.server_tun
        elif role == 'client1':
            return self.client1_tun
        elif role == 'client2':
            return self.client2_tun
        raise ValueError(f"未知角色: {role}")

    def work_dir_for_role(self, role: str) -> str:
        return self.server_work_dir if role == 'server' else self.client_work_dir


def _parse_show_props(out_show: str) -> Dict[str, str]:
    props = {}
    for line in out_show.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            props[k.strip()] = v.strip()
    return props


def audit_stopped_node(executor, role: str, config: LifecycleConfig) -> Dict:
    """审计单个节点的停止后状态（unit, cgroup, tun, 孤儿进程）。"""
    unit = config.unit_for_role(role)
    tun = config.tun_for_role(role)

    # 1. unit 状态
    rc_active, out_active, _ = executor.run(role, f"sudo systemctl is-active {unit} 2>&1")
    unit_status = out_active.strip() if out_active.strip() else ('active' if rc_active == 0 else 'inactive')

    # 获取详细属性
    rc_show, out_show, _ = executor.run(
        role,
        f"sudo systemctl show -p ActiveState,SubState,MainPID,ExecMainPID,TasksCurrent,ControlGroup {unit}"
    )
    show_props = _parse_show_props(out_show)

    active_state = show_props.get('ActiveState', unit_status)
    last_main_pid_str = show_props.get('MainPID') or show_props.get('ExecMainPID', '0')
    try:
        last_main_pid = int(last_main_pid_str)
    except ValueError:
        last_main_pid = 0

    # 2. cgroup 状态
    tasks_current_str = show_props.get('TasksCurrent', '')
    tasks_current = None
    if tasks_current_str and tasks_current_str.isdigit():
        tasks_current = int(tasks_current_str)

    cgroup_clean = True
    cgroup_procs_found = []
    if tasks_current is not None and tasks_current > 0:
        cgroup_clean = False

    cg_path = show_props.get('ControlGroup', '')
    if cg_path and cg_path != '/':
        rc_cg, out_cg, _ = executor.run(
            role,
            f"[ -d /sys/fs/cgroup{cg_path} ] && cat /sys/fs/cgroup{cg_path}/cgroup.procs 2>/dev/null || true"
        )
        procs = [p.strip() for p in out_cg.splitlines() if p.strip()]
        if procs:
            cgroup_clean = False
            cgroup_procs_found = procs

    # 3. TUN 设备检查
    rc_tun, out_tun, _ = executor.run(role, f"ip link show {tun} 2>&1")
    tun_exists = (rc_tun == 0 and ('mtu' in out_tun.lower() or f'{tun}:' in out_tun or f'{tun}@' in out_tun))

    # 4. 孤儿进程检查
    orphan_processes = []
    orphan_details = {}
    for proc in PROCS_TO_AUDIT:
        rc_p, out_p, _ = executor.run(role, f"pgrep -x {proc}")
        pids = [p.strip() for p in out_p.splitlines() if p.strip()]
        if rc_p == 0 and pids:
            orphan_processes.append(proc)
            orphan_details[proc] = pids

    is_unit_inactive = (active_state == 'inactive' or unit_status == 'inactive')
    is_clean = is_unit_inactive and cgroup_clean and (not tun_exists) and (not orphan_processes)

    return {
        'role': role,
        'unit': unit,
        'unit_status': active_state,
        'is_inactive': is_unit_inactive,
        'cgroup_clean': cgroup_clean,
        'tasks_current': tasks_current,
        'cgroup_procs': cgroup_procs_found,
        'tun': tun,
        'tun_exists': tun_exists,
        'orphan_processes': orphan_processes,
        'orphan_details': orphan_details,
        'last_main_pid': last_main_pid,
        'clean': is_clean,
        'raw_evidence': {
            'is_active': out_active.strip(),
            'show_props': show_props,
            'tun_out': out_tun.strip(),
        }
    }


def audit_restart_node(executor, role: str, old_main_pid: Optional[int],
                       old_cgroup_pids: Optional[Set[int]] = None,
                       config: Optional[LifecycleConfig] = None) -> Dict:
    """审计单个节点的重启后状态（unit active, 新 main_pid, no-overlap, tun UP, cgroup disjoint）。"""
    cfg = config or LifecycleConfig()
    unit = cfg.unit_for_role(role)
    tun = cfg.tun_for_role(role)

    # 1. unit 状态
    rc_active, out_active, _ = executor.run(role, f"sudo systemctl is-active {unit} 2>&1")
    unit_status = out_active.strip() if out_active.strip() else ('active' if rc_active == 0 else 'inactive')

    # 2. MainPID 与 cgroup
    rc_show, out_show, _ = executor.run(
        role,
        f"sudo systemctl show -p ActiveState,SubState,MainPID,TasksCurrent,ControlGroup {unit}"
    )
    show_props = _parse_show_props(out_show)

    active_state = show_props.get('ActiveState', unit_status)
    new_pid_str = show_props.get('MainPID', '0')
    try:
        new_main_pid = int(new_pid_str)
    except ValueError:
        new_main_pid = 0

    # 3. 读取新 cgroup 内所有进程 PID（含角色主进程与链路/传输子进程）
    new_cgroup_pids: Set[int] = set()
    cg_path = show_props.get('ControlGroup', '')
    if cg_path and cg_path != '/':
        rc_cg, out_cg, _ = executor.run(
            role,
            f"[ -d /sys/fs/cgroup{cg_path} ] && cat /sys/fs/cgroup{cg_path}/cgroup.procs 2>/dev/null || true"
        )
        for p in out_cg.splitlines():
            p = p.strip()
            if p.isdigit():
                new_cgroup_pids.add(int(p))
    if new_main_pid > 0:
        new_cgroup_pids.add(new_main_pid)

    # 4. no-overlap 检查：主进程与子进程均不得重叠
    pid_reused = False
    old_pid_still_running = False
    cgroup_disjoint = True
    overlapped_pids: List[int] = []

    if old_main_pid is not None and old_main_pid > 0:
        if new_main_pid == old_main_pid or old_main_pid in new_cgroup_pids:
            pid_reused = True
        rc_kill, _, _ = executor.run(role, f"sudo kill -0 {old_main_pid} 2>/dev/null")
        if rc_kill == 0:
            old_pid_still_running = True

    if old_cgroup_pids:
        intersection = new_cgroup_pids.intersection(old_cgroup_pids)
        if intersection:
            cgroup_disjoint = False
            overlapped_pids = sorted(list(intersection))
            pid_reused = True

    # 5. TUN 设备检查（必须存在且为 UP）
    rc_tun, out_tun, _ = executor.run(role, f"ip link show {tun} 2>&1")
    tun_exists = (rc_tun == 0 and ('mtu' in out_tun.lower() or f'{tun}:' in out_tun or f'{tun}@' in out_tun))
    tun_up = tun_exists and ('state UP' in out_tun or '<UP' in out_tun or ',UP,' in out_tun or ',UP>' in out_tun)

    # 6. 验证新 PID 存活
    new_pid_alive = False
    if new_main_pid > 0:
        rc_new_alive, _, _ = executor.run(role, f"sudo kill -0 {new_main_pid} 2>/dev/null")
        new_pid_alive = (rc_new_alive == 0)

    is_active = (active_state == 'active')
    ready = (is_active and (new_main_pid > 0) and new_pid_alive and
             (not pid_reused) and (not old_pid_still_running) and cgroup_disjoint and tun_up)

    return {
        'role': role,
        'unit': unit,
        'unit_status': active_state,
        'main_pid': new_main_pid,
        'old_main_pid': old_main_pid,
        'new_cgroup_pids': sorted(list(new_cgroup_pids)),
        'pid_reused': pid_reused,
        'old_pid_still_running': old_pid_still_running,
        'cgroup_disjoint': cgroup_disjoint,
        'overlapped_pids': overlapped_pids,
        'new_pid_alive': new_pid_alive,
        'tun': tun,
        'tun_exists': tun_exists,
        'tun_up': tun_up,
        'ready': ready,
        'raw_evidence': {
            'is_active': out_active.strip(),
            'show_props': show_props,
            'tun_out': out_tun.strip(),
        }
    }


def wait_nodes_cleanup(executor, config: LifecycleConfig, timeout: float = 15.0) -> Tuple[bool, Dict[str, Dict]]:
    """轮询等待各节点角色服务与资源完全释放。"""
    deadline = time.time() + timeout
    last_results = {}
    while time.time() < deadline:
        all_clean = True
        last_results = {}
        for role in config.roles:
            res = audit_stopped_node(executor, role, config)
            last_results[role] = res
            if not res['clean']:
                all_clean = False
        if all_clean:
            return True, last_results
        time.sleep(config.poll_interval_seconds)
    return False, last_results


def wait_nodes_restart_ready(executor, old_pids: Dict[str, Optional[int]],
                             old_cgroups: Dict[str, Set[int]],
                             config: LifecycleConfig, timeout: float = 15.0) -> Tuple[bool, Dict[str, Dict]]:
    """轮询等待各节点重启达到 active 且 TUN UP 状态。"""
    deadline = time.time() + timeout
    last_results = {}
    while time.time() < deadline:
        all_ready = True
        last_results = {}
        for role in config.roles:
            old_pid = old_pids.get(role)
            old_cg = old_cgroups.get(role, set())
            res = audit_restart_node(executor, role, old_pid, old_cg, config)
            last_results[role] = res
            if not res['ready']:
                all_ready = False
        if all_ready:
            return True, last_results
        time.sleep(config.poll_interval_seconds)
    return False, last_results


class LifecycleAuditor:
    """Issue #41 角色服务生命周期审计器。"""

    def __init__(self, executor, config: Optional[LifecycleConfig] = None):
        self.executor = executor
        self.config = config or LifecycleConfig()

    def _execute_stop_all(self):
        self.executor.run('server', f"sudo systemctl stop {self.config.server_unit} 2>/dev/null || true")
        for role in ('client1', 'client2'):
            self.executor.run(role, f"sudo systemctl stop {self.config.client_unit} 2>/dev/null || true")

    def _audit_stop_phase(self, phase_name: str, archive_dir: str) -> Tuple[bool, Dict[str, Dict], List[str], List[str]]:
        """通用停止阶段审计（first_stop 与 second_stop 复用）。"""
        self._execute_stop_all()
        clean, results = wait_nodes_cleanup(self.executor, self.config, timeout=self.config.timeout_seconds)

        evidence_files = []
        errors = []

        for role, res in results.items():
            txt_rel = os.path.join('lifecycle', f'{phase_name}_{role}.txt')
            txt_path = os.path.join(archive_dir, txt_rel)
            with open(txt_path, 'w', encoding='utf-8') as fh:
                fh.write(f"role: {role}\n")
                fh.write(f"unit_status: {res['unit_status']}\n")
                fh.write(f"cgroup_clean: {res['cgroup_clean']}\n")
                fh.write(f"tun_exists: {res['tun_exists']}\n")
                fh.write(f"orphan_processes: {json.dumps(res['orphan_processes'])}\n")
                fh.write(f"raw_show:\n{json.dumps(res['raw_evidence'].get('show_props', {}), indent=2)}\n")
                fh.write(f"tun_output:\n{res['raw_evidence'].get('tun_out', '')}\n")
            evidence_files.append(txt_rel)

        ev_json_rel = os.path.join('lifecycle', f'{phase_name}_evidence.json')
        with open(os.path.join(archive_dir, ev_json_rel), 'w', encoding='utf-8') as fh:
            json.dump(results, fh, indent=2, ensure_ascii=False)
        evidence_files.append(ev_json_rel)

        if not clean:
            for role, res in results.items():
                if not res['is_inactive']:
                    errors.append(f"{phase_name} 阶段 {role} unit 未处于 inactive 状态 (当前: {res['unit_status']})")
                if not res['cgroup_clean']:
                    errors.append(f"{phase_name} 阶段 {role} cgroup 中仍有进程残留: {res.get('cgroup_procs')}")
                if res['tun_exists']:
                    errors.append(f"{phase_name} 阶段 {role} TUN 设备 {res['tun']} 仍存在")
                if res['orphan_processes']:
                    errors.append(f"{phase_name} 阶段 {role} 存在孤儿进程: {res['orphan_processes']}")

        return clean, results, evidence_files, errors

    def run_lifecycle_audit(self, archive_dir: str,
                            initial_pids: Optional[Dict[str, int]] = None,
                            initial_cgroups: Optional[Dict[str, List[int]]] = None) -> Dict:
        """执行完整的三阶段生命周期审计，生成原始证据与 lifecycle_summary.json。"""
        lifecycle_dir = os.path.join(archive_dir, 'lifecycle')
        os.makedirs(lifecycle_dir, exist_ok=True)

        all_errors: List[str] = []
        all_evidence_files: List[str] = []

        # ==================== 阶段 1: 首次受控停止后审计 (first_stop) ====================
        first_clean, first_results, first_ev_files, first_errors = self._audit_stop_phase('first_stop', archive_dir)
        all_evidence_files.extend(first_ev_files)
        all_errors.extend(first_errors)

        # 确定各节点用于比对 no-overlap 的旧 MainPID 与旧 cgroup 进程集合
        old_pids: Dict[str, Optional[int]] = {}
        old_cgroups: Dict[str, Set[int]] = {}
        for role in self.config.roles:
            if initial_pids and role in initial_pids:
                old_pids[role] = initial_pids[role]
            else:
                old_pids[role] = first_results.get(role, {}).get('last_main_pid')

            if initial_cgroups and role in initial_cgroups:
                old_cgroups[role] = set(initial_cgroups[role])
            else:
                old_cgroups[role] = {old_pids[role]} if old_pids.get(role) else set()

            if not old_pids.get(role) or old_pids[role] <= 0:
                all_errors.append(f"缺少 {role} 首次运行的有效 MainPID，无法执行严格 no-overlap 验证")

        # ==================== 阶段 2: 重启与 no-overlap 审计 (restart) ====================
        # 清理旧工作区并验证干净隔离，确保绝不复用未完成轮次
        self.executor.run('server', f"sudo rm -rf {self.config.server_work_dir}")
        for role in ('client1', 'client2'):
            self.executor.run(role, f"sudo rm -rf {self.config.client_work_dir}")

        clean_state_verified = True
        for role in self.config.roles:
            wdir = self.config.work_dir_for_role(role)
            rc_w, out_w, _ = self.executor.run(role, f"[ ! -d {wdir} ] || [ -z \"$(ls -A {wdir} 2>/dev/null)\" ]")
            if rc_w != 0:
                clean_state_verified = False
                all_errors.append(f"restart 阶段 {role} 工作区 {wdir} 清理失败，存在复用未完成状态风险")

        # 重新启动服务
        for role in ('client1', 'client2'):
            self.executor.run(role, f"sudo systemctl restart {self.config.client_unit}")
        self.executor.run('server', f"sudo systemctl restart {self.config.server_unit}")

        restart_ready, restart_results = wait_nodes_restart_ready(
            self.executor, old_pids, old_cgroups, self.config, timeout=self.config.timeout_seconds)

        for role, res in restart_results.items():
            txt_rel = os.path.join('lifecycle', f'restart_{role}.txt')
            txt_path = os.path.join(archive_dir, txt_rel)
            with open(txt_path, 'w', encoding='utf-8') as fh:
                fh.write(f"role: {role}\n")
                fh.write(f"unit_status: {res['unit_status']}\n")
                fh.write(f"main_pid: {res['main_pid']}\n")
                fh.write(f"old_main_pid: {res['old_main_pid']}\n")
                fh.write(f"pid_reused: {res['pid_reused']}\n")
                fh.write(f"cgroup_disjoint: {res['cgroup_disjoint']}\n")
                fh.write(f"overlapped_pids: {json.dumps(res['overlapped_pids'])}\n")
                fh.write(f"old_pid_still_running: {res['old_pid_still_running']}\n")
                fh.write(f"tun_up: {res['tun_up']}\n")
                fh.write(f"raw_show:\n{json.dumps(res['raw_evidence'].get('show_props', {}), indent=2)}\n")
                fh.write(f"tun_output:\n{res['raw_evidence'].get('tun_out', '')}\n")
            all_evidence_files.append(txt_rel)

        restart_ev_rel = os.path.join('lifecycle', 'restart_evidence.json')
        with open(os.path.join(archive_dir, restart_ev_rel), 'w', encoding='utf-8') as fh:
            json.dump(restart_results, fh, indent=2, ensure_ascii=False)
        all_evidence_files.append(restart_ev_rel)

        if not restart_ready:
            for role, res in restart_results.items():
                if res['unit_status'] != 'active':
                    all_errors.append(f"restart 阶段 {role} unit 未进入 active 状态 (当前: {res['unit_status']})")
                if res['main_pid'] <= 0 or not res['new_pid_alive']:
                    all_errors.append(f"restart 阶段 {role} 新 MainPID 无效或未存活 (PID: {res['main_pid']})")
                if res['pid_reused']:
                    all_errors.append(f"restart 阶段 {role} 检测到进程 PID 重叠，复用了旧 PID {res['main_pid']}")
                if not res['cgroup_disjoint']:
                    all_errors.append(f"restart 阶段 {role} 子进程存在 PID 重叠: {res['overlapped_pids']}")
                if res['old_pid_still_running']:
                    all_errors.append(f"restart 阶段 {role} 旧进程 PID {res['old_main_pid']} 仍残留存活")
                if not res['tun_up']:
                    all_errors.append(f"restart 阶段 {role} TUN 设备 {res['tun']} 未就绪或未处于 UP 状态")

        # ==================== 阶段 3: 第二次受控停止后审计 (second_stop) ====================
        second_clean, second_results, second_ev_files, second_errors = self._audit_stop_phase('second_stop', archive_dir)
        all_evidence_files.extend(second_ev_files)
        all_errors.extend(second_errors)

        # ==================== 阶段 4: 汇总生成独立的 lifecycle_summary.json ====================
        overall_status = 'passed' if (first_clean and restart_ready and second_clean and clean_state_verified and not all_errors) else 'failed'
        summary_data = {
            'schema_version': 1,
            'status': overall_status,
            'reason': '; '.join(all_errors) if all_errors else '三角色服务 restart 与资源生命周期验证通过',
            'first_stop': {
                'status': 'passed' if first_clean else 'failed',
                'roles': {
                    role: {
                        'unit_status': first_results[role]['unit_status'],
                        'cgroup_clean': first_results[role]['cgroup_clean'],
                        'tun_exists': first_results[role]['tun_exists'],
                        'orphan_processes': first_results[role]['orphan_processes'],
                    } for role in self.config.roles if role in first_results
                }
            },
            'restart': {
                'status': 'passed' if (restart_ready and clean_state_verified) else 'failed',
                'clean_state_verified': clean_state_verified,
                'roles': {
                    role: {
                        'unit_status': restart_results[role]['unit_status'],
                        'main_pid': restart_results[role]['main_pid'],
                        'old_main_pid': restart_results[role]['old_main_pid'],
                        'pid_reused': restart_results[role]['pid_reused'],
                        'cgroup_disjoint': restart_results[role]['cgroup_disjoint'],
                        'tun_up': restart_results[role]['tun_up'],
                    } for role in self.config.roles if role in restart_results
                }
            },
            'second_stop': {
                'status': 'passed' if second_clean else 'failed',
                'roles': {
                    role: {
                        'unit_status': second_results[role]['unit_status'],
                        'cgroup_clean': second_results[role]['cgroup_clean'],
                        'tun_exists': second_results[role]['tun_exists'],
                        'orphan_processes': second_results[role]['orphan_processes'],
                    } for role in self.config.roles if role in second_results
                }
            },
            'errors': all_errors,
            'evidence_files': all_evidence_files,
        }

        summary_rel = os.path.join('lifecycle', 'lifecycle_summary.json')
        summary_path = os.path.join(archive_dir, summary_rel)
        with open(summary_path, 'w', encoding='utf-8') as fh:
            json.dump(summary_data, fh, indent=2, ensure_ascii=False)
            fh.write('\n')

        return summary_data


def _validate_stop_phase(phase_data: Dict, phase_name: str, errors: List[str]):
    """校验单个停止阶段（first_stop 与 second_stop 复用）。"""
    if phase_data.get('status') != 'passed':
        errors.append(f'lifecycle.{phase_name} 必须为 passed')
    roles_data = phase_data.get('roles', {})
    for role in ROLES:
        rdata = roles_data.get(role)
        if not isinstance(rdata, dict):
            errors.append(f'{phase_name} 缺少角色 {role} 证据')
            continue
        if rdata.get('unit_status') != 'inactive':
            errors.append(f'{phase_name} {role} unit_status 必须为 inactive (当前: {rdata.get("unit_status")})')
        if rdata.get('cgroup_clean') is not True:
            errors.append(f'{phase_name} {role} cgroup 必须清理干净')
        if rdata.get('tun_exists') is not False:
            errors.append(f'{phase_name} {role} TUN 设备必须已消失')
        if rdata.get('orphan_processes') != []:
            errors.append(f'{phase_name} {role} 不得残留孤儿进程: {rdata.get("orphan_processes")}')


def validate_lifecycle_summary(lifecycle: Dict, archive_dir: Optional[str] = None) -> List[str]:
    """校验 lifecycle 摘要与证据的合法性（供 issue41_validate_archive.py 调用）。"""
    errors: List[str] = []
    if not isinstance(lifecycle, dict):
        return ['lifecycle 分区必须是对象']

    status = lifecycle.get('status')
    if status not in ('passed', 'failed', 'skipped'):
        return ['lifecycle.status 无效']

    if status == 'failed':
        if not lifecycle.get('reason'):
            errors.append('lifecycle 标记为 failed 但缺少 reason 说明')
        return errors

    if status == 'skipped':
        return errors

    # 针对 passed 状态的严格断言
    for sec in ('first_stop', 'restart', 'second_stop'):
        if sec not in lifecycle or not isinstance(lifecycle[sec], dict):
            errors.append(f'lifecycle 缺少阶段: {sec}')

    if errors:
        return errors

    # 1. 校验 first_stop
    _validate_stop_phase(lifecycle['first_stop'], 'first_stop', errors)

    # 2. 校验 restart
    restart = lifecycle['restart']
    if restart.get('status') != 'passed':
        errors.append('lifecycle.restart 必须为 passed')
    if restart.get('clean_state_verified') is not True:
        errors.append('lifecycle.restart 必须证明工作区干净隔离、未复用旧未完成轮次')
    restart_roles = restart.get('roles', {})
    for role in ROLES:
        rdata = restart_roles.get(role)
        if not isinstance(rdata, dict):
            errors.append(f'restart 缺少角色 {role} 证据')
            continue
        if rdata.get('unit_status') != 'active':
            errors.append(f'restart {role} unit_status 必须为 active (当前: {rdata.get("unit_status")})')

        main_pid = rdata.get('main_pid')
        if not isinstance(main_pid, int) or main_pid <= 0:
            errors.append(f'restart {role} 必须拥有有效的新 MainPID')

        old_main_pid = rdata.get('old_main_pid')
        if not isinstance(old_main_pid, int) or old_main_pid <= 0:
            errors.append(f'restart {role} 必须记录有效的前序 MainPID 以保证 no-overlap 因果链')

        if isinstance(main_pid, int) and isinstance(old_main_pid, int) and main_pid == old_main_pid:
            errors.append(f'restart {role} 新旧 MainPID 相同，发生进程重叠')

        if rdata.get('pid_reused') is not False:
            errors.append(f'restart {role} 禁止复用旧进程 PID')

        if rdata.get('cgroup_disjoint') is not True:
            errors.append(f'restart {role} 子进程与旧进程集合重叠')

        if rdata.get('tun_up') is not True:
            errors.append(f'restart {role} TUN 设备必须已建立并处于 UP 状态')

    # 3. 校验 second_stop
    _validate_stop_phase(lifecycle['second_stop'], 'second_stop', errors)

    # 4. 证据文件校验
    evidence_files = lifecycle.get('evidence_files')
    if not isinstance(evidence_files, list) or len(evidence_files) == 0:
        errors.append('lifecycle 必须包含独立的原始证据文件列表 evidence_files')
    elif archive_dir:
        for req_f in ('lifecycle/first_stop_evidence.json', 'lifecycle/restart_evidence.json', 'lifecycle/second_stop_evidence.json'):
            full_p = os.path.join(archive_dir, req_f)
            if not os.path.isfile(full_p):
                errors.append(f'缺少 lifecycle 原始证据文件: {req_f}')

    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description='Issue #41 角色服务生命周期审计')
    subparsers = parser.add_subparsers(dest='subcmd', required=True)

    run_parser = subparsers.add_parser('run', help='执行完整的三阶段生命周期审计')
    run_parser.add_argument('archive_dir', help='归档目录路径')
    run_parser.add_argument('--initial-pids', help='可选的初始 MainPID JSON 字符串或路径 (如: {"server": 123, ...})')
    run_parser.add_argument('--initial-cgroups', help='可选的初始 cgroup PIDs JSON 字符串或路径')
    run_parser.add_argument('--server-tun', default='v8i41s0', help='server TUN 设备名')
    run_parser.add_argument('--client1-tun', default='v8i41c1', help='client1 TUN 设备名')
    run_parser.add_argument('--client2-tun', default='v8i41c2', help='client2 TUN 设备名')
    run_parser.add_argument('--client1-ssh', default=None, help='client1 SSH 主机')
    run_parser.add_argument('--client2-ssh', default=None, help='client2 SSH 主机')
    run_parser.add_argument('--timeout', type=float, default=15.0, help='超时时间（秒）')

    val_parser = subparsers.add_parser('validate', help='校验已生成的 lifecycle 结果')
    val_parser.add_argument('archive_dir', help='归档目录路径')

    args = parser.parse_args(argv)

    if args.subcmd == 'run':
        client_ssh_map = {}
        if args.client1_ssh:
            client_ssh_map['client1'] = args.client1_ssh
        if args.client2_ssh:
            client_ssh_map['client2'] = args.client2_ssh
        executor = RealExecutor(client_ssh_map=client_ssh_map or None)
        config = LifecycleConfig(
            server_tun=args.server_tun,
            client1_tun=args.client1_tun,
            client2_tun=args.client2_tun,
            timeout_seconds=args.timeout,
            client_ssh_map=executor.client_ssh_map,
        )
        initial_pids = None
        if args.initial_pids:
            if os.path.isfile(args.initial_pids):
                with open(args.initial_pids, 'r', encoding='utf-8') as fh:
                    initial_pids = json.load(fh)
            else:
                initial_pids = json.loads(args.initial_pids)

        initial_cgroups = None
        if args.initial_cgroups:
            if os.path.isfile(args.initial_cgroups):
                with open(args.initial_cgroups, 'r', encoding='utf-8') as fh:
                    initial_cgroups = json.load(fh)
            else:
                initial_cgroups = json.loads(args.initial_cgroups)

        auditor = LifecycleAuditor(executor, config)
        summary = auditor.run_lifecycle_audit(
            args.archive_dir, initial_pids=initial_pids, initial_cgroups=initial_cgroups)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        if summary.get('status') != 'passed':
            print(f"LIFECYCLE_AUDIT_FAILED: {summary.get('reason')}", file=sys.stderr)
            return 1
        return 0

    elif args.subcmd == 'validate':
        summary_path = os.path.join(args.archive_dir, 'lifecycle', 'lifecycle_summary.json')
        if not os.path.isfile(summary_path):
            print(f"FAIL: {summary_path} 不存在", file=sys.stderr)
            return 1
        with open(summary_path, 'r', encoding='utf-8') as fh:
            lifecycle = json.load(fh)
        errors = validate_lifecycle_summary(lifecycle, args.archive_dir)
        if errors:
            for err in errors:
                print(f"FAIL: {err}", file=sys.stderr)
            return 1
        print("OK: lifecycle summary validation passed")
        return 0

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
