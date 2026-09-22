#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import shutil
import tempfile
import unittest

from tests.real_hardware.issue41_envelope import RealExecutor
from tests.real_hardware.issue41_lifecycle import (
    LifecycleAuditor,
    LifecycleConfig,
    audit_restart_node,
    audit_stopped_node,
    main as lifecycle_main,
    validate_lifecycle_summary,
)
from tests.real_hardware.test_issue41_envelope import DummyExecutor


def make_stopped_node_executor(clean=True, unit_active=False, cgroup_dirty=False,
                               tun_exists=False, orphan_proc=None):
    executor = DummyExecutor()
    roles = [('server', 'v8i41s0')] + [(f'client{i}', f'v8i41c{i}') for i in range(1, 8)]
    for role, tun in roles:
        executor.set_response(role, 'is-active', 3, 'inactive\n', '')
        show_str = 'ActiveState=inactive\nSubState=dead\nMainPID=0\nExecMainPID=1000\nTasksCurrent=0\nControlGroup=/\n'
        executor.set_response(role, 'systemctl show', 0, show_str, '')
        executor.set_response(role, f'ip link show {tun}', 1, f'Device "{tun}" does not exist.\n', '')
        executor.set_response(role, 'pgrep -x', 1, '', '')
        executor.set_response(role, 'kill -0', 1, '', '')

    if unit_active:
        executor.set_response('server', 'is-active', 0, 'active\n', '')
        executor.set_response('server', 'systemctl show', 0, 'ActiveState=active\nTasksCurrent=2\nControlGroup=/\n', '')

    if cgroup_dirty:
        executor.set_response('client1', 'systemctl show', 0, 'ActiveState=inactive\nTasksCurrent=3\nControlGroup=/\n', '')

    if tun_exists:
        executor.set_response('client2', 'ip link show v8i41c2', 0, 'v8i41c2: <BROADCAST,MULTICAST> mtu 1500\n', '')

    if orphan_proc:
        executor.set_response('client1', f'pgrep -x {orphan_proc}', 0, '9999\n', '')

    return executor


def make_lifecycle_full_executor(first_clean=True, restart_clean=True, second_clean=True,
                                 pid_reused=False, old_pid_alive=False, tun_down=False,
                                 orphan_first=None, orphan_second=None):
    executor = DummyExecutor()
    pids_old = {'server': 1001, **{f'client{i}': 1001 + i for i in range(1, 8)}}
    pids_new = {'server': 2001, **{f'client{i}': 2001 + i for i in range(1, 8)}}
    if pid_reused:
        pids_new['server'] = pids_old['server']

    state = {'phase': 'first_stop'}

    # 动态处理器
    def handle_run(target, cmd, timeout=None):
        if 'stop' in cmd:
            if state['phase'] == 'restart':
                state['phase'] = 'second_stop'
        elif 'restart' in cmd:
            state['phase'] = 'restart'

        current_phase = state['phase']

        if 'is-active' in cmd:
            if current_phase in ('first_stop', 'second_stop'):
                return (3, 'inactive\n', '')
            else:
                return (0, 'active\n', '')

        if 'systemctl show' in cmd:
            if current_phase == 'first_stop':
                return (0, f'ActiveState=inactive\nSubState=dead\nMainPID=0\nExecMainPID={pids_old[target]}\nTasksCurrent=0\nControlGroup=/\n', '')
            elif current_phase == 'restart':
                pid = pids_new[target]
                return (0, f'ActiveState=active\nSubState=running\nMainPID={pid}\nTasksCurrent=4\nControlGroup=/system.slice/test\n', '')
            else:
                return (0, f'ActiveState=inactive\nSubState=dead\nMainPID=0\nExecMainPID={pids_new[target]}\nTasksCurrent=0\nControlGroup=/\n', '')

        if 'ip link show' in cmd:
            tun = 'v8i41s0' if target == 'server' else ('v8i41c1' if target == 'client1' else 'v8i41c2')
            if current_phase in ('first_stop', 'second_stop'):
                return (1, f'Device "{tun}" does not exist.\n', '')
            else:
                if tun_down and target == 'server':
                    return (0, f'{tun}: <BROADCAST,MULTICAST> mtu 1500 state DOWN\n', '')
                return (0, f'{tun}: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP\n', '')

        if 'pgrep -x' in cmd:
            if current_phase == 'first_stop' and orphan_first:
                if orphan_first in cmd:
                    return (0, '8888\n', '')
            if current_phase == 'second_stop' and orphan_second:
                if orphan_second in cmd:
                    return (0, '9999\n', '')
            return (1, '', '')

        if 'kill -0' in cmd:
            # 查新 PID 总是活的，查旧 PID 默认死了
            pid_str = cmd.split()[-2] if '2>/dev/null' in cmd else cmd.split()[-1]
            try:
                pid_num = int(pid_str)
            except ValueError:
                return (1, '', '')
            if pid_num in pids_new.values():
                return (0, '', '')
            if pid_num in pids_old.values():
                if old_pid_alive:
                    return (0, '', '')
                return (1, '', '')
            return (1, '', '')

        return (0, '', '')

    executor.run = handle_run
    return executor


class Issue41LifecycleTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix='issue41_test_lifecycle_')
        self.config = LifecycleConfig(
            roles=('server', 'client1', 'client2'),
            timeout_seconds=1.0,
            poll_interval_seconds=0.01,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_audit_stopped_node_clean(self):
        executor = make_stopped_node_executor()
        res = audit_stopped_node(executor, 'server', self.config)
        self.assertTrue(res['clean'])
        self.assertEqual('inactive', res['unit_status'])
        self.assertTrue(res['cgroup_clean'])
        self.assertFalse(res['tun_exists'])
        self.assertEqual([], res['orphan_processes'])

    def test_audit_stopped_node_detects_active_unit(self):
        executor = make_stopped_node_executor(unit_active=True)
        res = audit_stopped_node(executor, 'server', self.config)
        self.assertFalse(res['clean'])
        self.assertEqual('active', res['unit_status'])

    def test_audit_stopped_node_detects_cgroup_residual(self):
        executor = make_stopped_node_executor(cgroup_dirty=True)
        res = audit_stopped_node(executor, 'client1', self.config)
        self.assertFalse(res['clean'])
        self.assertFalse(res['cgroup_clean'])
        self.assertEqual(3, res['tasks_current'])

    def test_audit_stopped_node_detects_tun_residual(self):
        executor = make_stopped_node_executor(tun_exists=True)
        res = audit_stopped_node(executor, 'client2', self.config)
        self.assertFalse(res['clean'])
        self.assertTrue(res['tun_exists'])

    def test_audit_stopped_node_detects_orphan_processes(self):
        executor = make_stopped_node_executor(orphan_proc='uftp')
        res = audit_stopped_node(executor, 'client1', self.config)
        self.assertFalse(res['clean'])
        self.assertIn('uftp', res['orphan_processes'])

    def test_audit_restart_node_ready(self):
        executor = DummyExecutor()
        executor.set_response('server', 'is-active', 0, 'active\n', '')
        executor.set_response('server', 'systemctl show', 0, 'ActiveState=active\nMainPID=2001\nTasksCurrent=4\n', '')
        executor.set_response('server', 'ip link show v8i41s0', 0, 'v8i41s0: <BROADCAST,MULTICAST,UP> mtu 1500 state UP\n', '')
        executor.set_response('server', 'kill -0 2001', 0, '', '')
        executor.set_response('server', 'kill -0 1001', 1, '', '')

        res = audit_restart_node(executor, 'server', old_main_pid=1001, config=self.config)
        self.assertTrue(res['ready'])
        self.assertEqual('active', res['unit_status'])
        self.assertEqual(2001, res['main_pid'])
        self.assertFalse(res['pid_reused'])
        self.assertFalse(res['old_pid_still_running'])
        self.assertTrue(res['tun_up'])

    def test_audit_restart_node_detects_pid_reused(self):
        executor = DummyExecutor()
        executor.set_response('server', 'is-active', 0, 'active\n', '')
        executor.set_response('server', 'systemctl show', 0, 'ActiveState=active\nMainPID=1001\nTasksCurrent=4\n', '')
        executor.set_response('server', 'ip link show v8i41s0', 0, 'v8i41s0: <BROADCAST,MULTICAST,UP> mtu 1500 state UP\n', '')
        executor.set_response('server', 'kill -0 1001', 0, '', '')

        res = audit_restart_node(executor, 'server', old_main_pid=1001, config=self.config)
        self.assertFalse(res['ready'])
        self.assertTrue(res['pid_reused'])

    def test_audit_restart_node_detects_old_pid_still_running(self):
        executor = DummyExecutor()
        executor.set_response('server', 'is-active', 0, 'active\n', '')
        executor.set_response('server', 'systemctl show', 0, 'ActiveState=active\nMainPID=2001\nTasksCurrent=4\n', '')
        executor.set_response('server', 'ip link show v8i41s0', 0, 'v8i41s0: <BROADCAST,MULTICAST,UP> mtu 1500 state UP\n', '')
        executor.set_response('server', 'kill -0 2001', 0, '', '')
        executor.set_response('server', 'kill -0 1001', 0, '', '')  # 旧 PID 依然活着！

        res = audit_restart_node(executor, 'server', old_main_pid=1001, config=self.config)
        self.assertFalse(res['ready'])
        self.assertTrue(res['old_pid_still_running'])

    def test_audit_restart_node_detects_tun_not_up(self):
        executor = DummyExecutor()
        executor.set_response('server', 'is-active', 0, 'active\n', '')
        executor.set_response('server', 'systemctl show', 0, 'ActiveState=active\nMainPID=2001\nTasksCurrent=4\n', '')
        executor.set_response('server', 'ip link show v8i41s0', 0, 'v8i41s0: <BROADCAST,MULTICAST> mtu 1500 state DOWN\n', '')
        executor.set_response('server', 'kill -0 2001', 0, '', '')
        executor.set_response('server', 'kill -0 1001', 1, '', '')

        res = audit_restart_node(executor, 'server', old_main_pid=1001, config=self.config)
        self.assertFalse(res['ready'])
        self.assertFalse(res['tun_up'])

    def test_lifecycle_auditor_full_pass(self):
        executor = make_lifecycle_full_executor()
        auditor = LifecycleAuditor(executor, self.config)
        summary = auditor.run_lifecycle_audit(self.tmp_dir, initial_pids={'server': 1001, 'client1': 1002, 'client2': 1003})

        self.assertEqual('passed', summary['status'])
        self.assertEqual('passed', summary['first_stop']['status'])
        self.assertEqual('passed', summary['restart']['status'])
        self.assertEqual('passed', summary['second_stop']['status'])
        self.assertEqual([], summary['errors'])

        # 验证证据文件存在
        for ev_file in summary['evidence_files']:
            full_p = os.path.join(self.tmp_dir, ev_file)
            self.assertTrue(os.path.isfile(full_p), f"缺少证据文件: {full_p}")

        # 调用 validate_lifecycle_summary 验证无错误
        errors = validate_lifecycle_summary(summary, self.tmp_dir)
        self.assertEqual([], errors)

    def test_lifecycle_auditor_fails_on_first_stop_orphan(self):
        executor = make_lifecycle_full_executor(orphan_first='uftpd')
        auditor = LifecycleAuditor(executor, self.config)
        summary = auditor.run_lifecycle_audit(self.tmp_dir)

        self.assertEqual('failed', summary['status'])
        self.assertEqual('failed', summary['first_stop']['status'])
        self.assertTrue(any('孤儿进程' in err for err in summary['errors']))

        errors = validate_lifecycle_summary(summary, self.tmp_dir)
        # failed 状态且有 reason 时 validate_lifecycle_summary 返回 []（允许作为 failed 归档存在）
        self.assertEqual([], errors)

    def test_lifecycle_auditor_fails_on_restart_pid_reused(self):
        executor = make_lifecycle_full_executor(pid_reused=True)
        auditor = LifecycleAuditor(executor, self.config)
        summary = auditor.run_lifecycle_audit(self.tmp_dir, initial_pids={'server': 1001, 'client1': 1002, 'client2': 1003})

        self.assertEqual('failed', summary['status'])
        self.assertEqual('failed', summary['restart']['status'])
        self.assertTrue(any('PID 重叠' in err for err in summary['errors']))

    def test_lifecycle_auditor_fails_on_restart_tun_down(self):
        executor = make_lifecycle_full_executor(tun_down=True)
        auditor = LifecycleAuditor(executor, self.config)
        summary = auditor.run_lifecycle_audit(self.tmp_dir)

        self.assertEqual('failed', summary['status'])
        self.assertEqual('failed', summary['restart']['status'])
        self.assertTrue(any('TUN 设备' in err for err in summary['errors']))

    def test_lifecycle_auditor_fails_on_second_stop_orphan(self):
        executor = make_lifecycle_full_executor(orphan_second='wfb_v6_uplink')
        auditor = LifecycleAuditor(executor, self.config)
        summary = auditor.run_lifecycle_audit(self.tmp_dir)

        self.assertEqual('failed', summary['status'])
        self.assertEqual('failed', summary['second_stop']['status'])
        self.assertTrue(any('孤儿进程' in err for err in summary['errors']))

    def test_validate_lifecycle_summary_rejects_missing_sections(self):
        bad_summary = {'schema_version': 1, 'status': 'passed'}
        errors = validate_lifecycle_summary(bad_summary)
        self.assertTrue(any('缺少阶段' in err for err in errors))

    def test_validate_lifecycle_summary_rejects_dirty_first_stop(self):
        executor = make_lifecycle_full_executor()
        auditor = LifecycleAuditor(executor, self.config)
        summary = auditor.run_lifecycle_audit(self.tmp_dir, initial_pids={'server': 1001, 'client1': 1002, 'client2': 1003})
        # 人为篡改
        summary['first_stop']['roles']['client1']['tun_exists'] = True
        errors = validate_lifecycle_summary(summary, self.tmp_dir)
        self.assertTrue(any('TUN 设备必须已消失' in err for err in errors))

    def test_validate_lifecycle_summary_rejects_missing_evidence_file(self):
        executor = make_lifecycle_full_executor()
        auditor = LifecycleAuditor(executor, self.config)
        summary = auditor.run_lifecycle_audit(self.tmp_dir, initial_pids={'server': 1001, 'client1': 1002, 'client2': 1003})

        # 删除一个必需证据文件
        os.remove(os.path.join(self.tmp_dir, 'lifecycle', 'first_stop_evidence.json'))
        errors = validate_lifecycle_summary(summary, self.tmp_dir)
        self.assertTrue(any('缺少 lifecycle 原始证据文件' in err for err in errors))

    def test_lifecycle_auditor_fails_on_cgroup_child_process_overlap(self):
        executor = make_lifecycle_full_executor()
        auditor = LifecycleAuditor(executor, self.config)
        # 传入包含 2001（即 restart 后分配的新 PID）的旧 cgroup
        summary = auditor.run_lifecycle_audit(
            self.tmp_dir,
            initial_pids={'server': 1001, 'client1': 1002, 'client2': 1003},
            initial_cgroups={'server': [1001, 2001], 'client1': [1002], 'client2': [1003]}
        )
        self.assertEqual('failed', summary['status'])
        self.assertEqual('failed', summary['restart']['status'])
        self.assertTrue(any('子进程存在 PID 重叠' in err for err in summary['errors']))


if __name__ == '__main__':
    unittest.main()
