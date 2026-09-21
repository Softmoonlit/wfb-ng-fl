#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import shutil
import tempfile
import unittest

from tests.real_hardware.issue41_envelope import (
    FAILURE_CATEGORIES,
    PREFLIGHT_LAYERS,
    FailureCategory,
    PreflightLayer,
    RunEnvelope,
    generate_run_id,
)


class DummyExecutor:
    """可配置的执行器，用于在测试中模拟本地和远端命令执行。"""

    def __init__(self):
        self.commands = {}
        self.executed = []

    def set_response(self, target, cmd_prefix, returncode=0, stdout='', stderr=''):
        self.commands[(target, cmd_prefix)] = (returncode, stdout, stderr)

    def run(self, target, cmd, timeout=None):
        self.executed.append((target, cmd))
        # 优先匹配最长/最具体的前缀或子串
        matching = [
            (prefix, resp) for (t, prefix), resp in self.commands.items()
            if t == target and prefix in cmd
        ]
        if matching:
            matching.sort(key=lambda item: len(item[0]), reverse=True)
            return matching[0][1]
        # 默认成功返回
        return (0, '', '')


def make_mock_topology_executor(server_wlx='wlxfc221c300cbc',
                                client1_wlx='wlxfc221c300cbb',
                                client2_wlx='wlxfc221c500a88',
                                commit='0123456789abcdef0123456789abcdef01234567',
                                branch='feat/41-real-hardware-fl-runtime-redo',
                                clean=True):
    executor = DummyExecutor()
    status_str = '' if clean else ' M modified_file.py'

    # server 本地查询
    executor.set_response('server', 'hostname', 0, 'server-vm0\n', '')
    executor.set_response('server', 'cat /etc/machine-id', 0, 'srv-mid-123\n', '')
    executor.set_response('server', 'git rev-parse HEAD', 0, f'{commit}\n', '')
    executor.set_response('server', 'git rev-parse --abbrev-ref HEAD', 0, f'{branch}\n', '')
    executor.set_response('server', 'git status --short', 0, f'{status_str}\n', '')
    executor.set_response('server', 'iw dev', 0, f'Interface {server_wlx}\n  type monitor\n', '')
    executor.set_response('server', f'cat /sys/class/net/{server_wlx}/address', 0, 'fc:22:1c:30:0c:bc\n', '')
    executor.set_response('server', f'readlink -f /sys/class/net/{server_wlx}/device/driver', 0, '/sys/bus/usb/drivers/rtl88xxau_wfb\n', '')
    executor.set_response('server', f'cat /sys/class/net/{server_wlx}/device/../speed', 0, '480\n', '')
    executor.set_response('server', 'sudo -n true', 0, '', '')
    executor.set_response('server', 'stat -c %s', 0, '4194304\n', '')
    executor.set_response('server', 'ss -tuln', 0, 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n', '')
    executor.set_response('server', 'ip link show v8i41', 1, 'Device "v8i41s0" does not exist.\n', '')
    executor.set_response('server', 'pgrep', 1, '', '')
    executor.set_response('server', 'check_work_dir_empty', 0, 'empty\n', '')
    executor.set_response('server', f'ip link show {server_wlx}', 0, f'{server_wlx}: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n', '')

    # client1 远端查询
    executor.set_response('client1', 'hostname', 0, 'client1-vm1\n', '')
    executor.set_response('client1', 'cat /etc/machine-id', 0, 'c1-mid-123\n', '')
    executor.set_response('client1', 'git rev-parse HEAD', 0, f'{commit}\n', '')
    executor.set_response('client1', 'git rev-parse --abbrev-ref HEAD', 0, f'{branch}\n', '')
    executor.set_response('client1', 'git status --short', 0, f'{status_str}\n', '')
    executor.set_response('client1', 'iw dev', 0, f'Interface {client1_wlx}\n  type monitor\n', '')
    executor.set_response('client1', f'cat /sys/class/net/{client1_wlx}/address', 0, 'fc:22:1c:30:0c:bb\n', '')
    executor.set_response('client1', f'readlink -f /sys/class/net/{client1_wlx}/device/driver', 0, '/sys/bus/usb/drivers/rtl88xxau_wfb\n', '')
    executor.set_response('client1', f'cat /sys/class/net/{client1_wlx}/device/../speed', 0, '480\n', '')
    executor.set_response('client1', 'sudo -n true', 0, '', '')
    executor.set_response('client1', 'stat -c %s', 0, '4194304\n', '')
    executor.set_response('client1', 'sha256sum', 0, '1111111111111111111111111111111111111111111111111111111111111111  tpl\n', '')
    executor.set_response('client1', 'ss -tuln', 0, 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n', '')
    executor.set_response('client1', 'ip link show v8i41', 1, 'Device "v8i41c1" does not exist.\n', '')
    executor.set_response('client1', 'pgrep', 1, '', '')
    executor.set_response('client1', 'check_work_dir_empty', 0, 'empty\n', '')
    executor.set_response('client1', f'ip link show {client1_wlx}', 0, f'{client1_wlx}: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n', '')

    # client2 远端查询
    executor.set_response('client2', 'hostname', 0, 'client2-vm2\n', '')
    executor.set_response('client2', 'cat /etc/machine-id', 0, 'c2-mid-123\n', '')
    executor.set_response('client2', 'git rev-parse HEAD', 0, f'{commit}\n', '')
    executor.set_response('client2', 'git rev-parse --abbrev-ref HEAD', 0, f'{branch}\n', '')
    executor.set_response('client2', 'git status --short', 0, f'{status_str}\n', '')
    executor.set_response('client2', 'iw dev', 0, f'Interface {client2_wlx}\n  type monitor\n', '')
    executor.set_response('client2', f'cat /sys/class/net/{client2_wlx}/address', 0, 'fc:22:1c:50:0a:88\n', '')
    executor.set_response('client2', f'readlink -f /sys/class/net/{client2_wlx}/device/driver', 0, '/sys/bus/usb/drivers/rtl88xxau_wfb\n', '')
    executor.set_response('client2', f'cat /sys/class/net/{client2_wlx}/device/../speed', 0, '480\n', '')
    executor.set_response('client2', 'sudo -n true', 0, '', '')
    executor.set_response('client2', 'stat -c %s', 0, '4194304\n', '')
    executor.set_response('client2', 'sha256sum', 0, '2222222222222222222222222222222222222222222222222222222222222222  tpl\n', '')
    executor.set_response('client2', 'ss -tuln', 0, 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n', '')
    executor.set_response('client2', 'ip link show v8i41', 1, 'Device "v8i41c2" does not exist.\n', '')
    executor.set_response('client2', 'pgrep', 1, '', '')
    executor.set_response('client2', 'check_work_dir_empty', 0, 'empty\n', '')
    executor.set_response('client2', f'ip link show {client2_wlx}', 0, f'{client2_wlx}: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n', '')

    return executor


class Issue41EnvelopeTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix='wfb-envelope-test-')
        self.archive_root = os.path.join(self.temp_dir, 'logs')
        os.makedirs(self.archive_root, exist_ok=True)
        self.default_config = {
            'channel': '157',
            'channel_width': 'HT40+',
            'link_id': '406',
            'uplink_stream': '32',
            'downlink_stream': '33',
            'fec_k': '8',
            'fec_n': '14',
            'radio_bandwidth': '40',
            'radio_mcs_index': '3',
            'server_tun': 'v8i41s0',
            'client1_tun': 'v8i41c1',
            'client2_tun': 'v8i41c2',
            'server_tun_addr': '10.80.0.1/24',
            'client1_tun_addr': '10.80.0.11/24',
            'client2_tun_addr': '10.80.0.12/24',
            'uftp_group': '239.80.41.1',
            'uftp_private_group': '239.80.41.2',
            'uftp_port': 1044,
            'http_host': '10.80.0.1',
            'http_port': 8080,
            'io_timeout_seconds': 120,
            'rounds': 1,
            'initial_model_path': os.path.join(self.temp_dir, 'model-4mib.bin'),
            'client1_update_template_path': os.path.join(self.temp_dir, 'update-client1.bin'),
            'client2_update_template_path': os.path.join(self.temp_dir, 'update-client2.bin'),
            'artifact_size_bytes': 4 * 1024 * 1024,
        }
        # 创建 4 MiB 虚拟输入文件
        self._write_file(self.default_config['initial_model_path'], b'm' * (4 * 1024 * 1024))
        self._write_file(self.default_config['client1_update_template_path'], b'1' * (4 * 1024 * 1024))
        self._write_file(self.default_config['client2_update_template_path'], b'2' * (4 * 1024 * 1024))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_file(self, path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(content)

    def test_generate_unique_run_id(self):
        id1 = generate_run_id()
        id2 = generate_run_id()
        self.assertNotEqual(id1, id2)
        self.assertTrue(id1.startswith('v8_issue41_'))
        self.assertTrue(id2.startswith('v8_issue41_'))

    def test_archive_cannot_be_overwritten(self):
        run_id = 'v8_issue41_test_fixed'
        archive_dir = os.path.join(self.archive_root, run_id)
        os.makedirs(archive_dir)  # 已存在

        envelope = RunEnvelope(
            run_id=run_id,
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        with self.assertRaises(FileExistsError) as ctx:
            envelope.initialize()
        self.assertIn('不可覆盖', str(ctx.exception))

    def test_envelope_json_binds_metadata_and_network_isolation(self):
        run_id = 'v8_issue41_test_init'
        envelope = RunEnvelope(
            run_id=run_id,
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            mode='formal',
            resolved_config=self.default_config,
        )
        envelope.initialize()

        envelope_path = os.path.join(envelope.archive_dir, 'envelope.json')
        self.assertTrue(os.path.isfile(envelope_path))
        with open(envelope_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)

        self.assertEqual(run_id, data['run_id'])
        self.assertEqual('formal', data['mode'])
        self.assertEqual('feat/41-real-hardware-fl-runtime-redo', data['branch'])
        self.assertEqual('0123456789abcdef0123456789abcdef01234567', data['commit'])
        self.assertEqual(self.default_config, data['resolved_config'])
        self.assertEqual('10.80.0.0/24', data['network_isolation']['data_plane'])
        self.assertTrue(data['network_isolation']['prohibit_management_as_data_plane'])

    def test_topology_discovery_records_three_nodes_and_actual_interfaces(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_topo_ok',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()

        topo = envelope.discover_topology(executor)

        self.assertIn('server', topo)
        self.assertIn('client1', topo)
        self.assertIn('client2', topo)
        self.assertEqual('wlxfc221c300cbc', topo['server']['wireless_interface'])
        self.assertEqual('wlxfc221c300cbb', topo['client1']['wireless_interface'])
        self.assertEqual('wlxfc221c500a88', topo['client2']['wireless_interface'])

        topo_file = os.path.join(envelope.archive_dir, 'orchestration', 'topology.json')
        self.assertTrue(os.path.isfile(topo_file))
        with open(topo_file, 'r', encoding='utf-8') as fh:
            saved_topo = json.load(fh)
        self.assertEqual(topo, saved_topo)

    def test_topology_discovery_fails_when_wireless_interface_missing(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_topo_fail',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        # 模拟 client2 未找到 wlx* 网卡
        executor.set_response('client2', 'iw dev', 0, 'Interface wlan0\n  type managed\n', '')

        with self.assertRaises(RuntimeError) as ctx:
            envelope.discover_topology(executor)
        self.assertIn('client2 必须恰好发现一个 wlx* 网卡', str(ctx.exception))

    def test_preflight_all_layers_pass(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_preflight_pass',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        envelope.discover_topology(executor)

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')

        self.assertTrue(passed)
        self.assertEqual('passed', envelope.partitions['orchestration']['status'])
        self.assertEqual(PreflightLayer.MANAGED_DIRECTORY_BOUNDARY,
                         envelope.partitions['orchestration']['last_successful_layer'])
        self.assertIsNone(envelope.partitions['orchestration']['first_failing_layer'])

    def test_preflight_passes_for_40mib_scenario(self):
        cfg_40m = dict(self.default_config)
        cfg_40m['rounds'] = 2
        cfg_40m['artifact_size_bytes'] = 40 * 1024 * 1024
        cfg_40m['initial_model_path'] = os.path.join(self.temp_dir, 'model-40mib.bin')
        cfg_40m['client1_update_template_path'] = os.path.join(self.temp_dir, 'update-c1-40mib.bin')
        cfg_40m['client2_update_template_path'] = os.path.join(self.temp_dir, 'update-c2-40mib.bin')
        self._write_file(cfg_40m['initial_model_path'], b'm' * (40 * 1024 * 1024))
        self._write_file(cfg_40m['client1_update_template_path'], b'1' * (40 * 1024 * 1024))
        self._write_file(cfg_40m['client2_update_template_path'], b'2' * (40 * 1024 * 1024))

        envelope = RunEnvelope(
            run_id='v8_issue41_preflight_40m_pass',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=cfg_40m,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        executor.set_response('server', 'stat -c %s', 0, f'{40 * 1024 * 1024}\n', '')
        executor.set_response('client1', 'stat -c %s', 0, f'{40 * 1024 * 1024}\n', '')
        executor.set_response('client2', 'stat -c %s', 0, f'{40 * 1024 * 1024}\n', '')
        envelope.discover_topology(executor)

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')
        self.assertTrue(passed)
        self.assertEqual('passed', envelope.partitions['orchestration']['status'])

    def test_preflight_fails_on_40mib_size_mismatch(self):
        cfg_40m = dict(self.default_config)
        cfg_40m['rounds'] = 2
        cfg_40m['artifact_size_bytes'] = 40 * 1024 * 1024
        cfg_40m['initial_model_path'] = os.path.join(self.temp_dir, 'model-40mib-corrupt.bin')
        cfg_40m['client1_update_template_path'] = os.path.join(self.temp_dir, 'update-c1-40mib.bin')
        cfg_40m['client2_update_template_path'] = os.path.join(self.temp_dir, 'update-c2-40mib.bin')
        # 初始模型大小仅为 100 字节，不满足 40 MiB
        self._write_file(cfg_40m['initial_model_path'], b'short')
        self._write_file(cfg_40m['client1_update_template_path'], b'1' * (40 * 1024 * 1024))
        self._write_file(cfg_40m['client2_update_template_path'], b'2' * (40 * 1024 * 1024))

        envelope = RunEnvelope(
            run_id='v8_issue41_preflight_40m_mismatch',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=cfg_40m,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        envelope.discover_topology(executor)

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')
        self.assertFalse(passed)
        self.assertEqual('failed', envelope.partitions['orchestration']['status'])
        self.assertEqual(PreflightLayer.DEPENDENCIES,
                         envelope.partitions['orchestration']['first_failing_layer'])
        self.assertIn('40 MiB', envelope.partitions['orchestration']['failure_reason'])

    def test_preflight_fails_on_commit_mismatch(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_commit_mismatch',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        # 模拟 client1 的 commit 不一致
        executor.set_response('client1', 'git rev-parse HEAD', 0, 'fedcba9876543210fedcba9876543210fedcba98\n', '')
        envelope.discover_topology(executor)

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')

        self.assertFalse(passed)
        orch = envelope.partitions['orchestration']
        self.assertEqual('failed', orch['status'])
        self.assertEqual(PreflightLayer.REPO_AND_VERSION, orch['first_failing_layer'])
        self.assertEqual(FailureCategory.IMPLEMENTATION, orch['failure_category'])
        self.assertIn('commit 与本机不一致', orch['failure_reason'])

        # 验证写入了 failed 的 issue41_summary.json 和 result.md
        summary_path = os.path.join(envelope.archive_dir, 'issue41_summary.json')
        self.assertTrue(os.path.isfile(summary_path))
        with open(summary_path, 'r', encoding='utf-8') as fh:
            summary = json.load(fh)
        self.assertEqual('failed', summary['conclusion']['status'])
        self.assertEqual(FailureCategory.IMPLEMENTATION, summary['conclusion']['category'])

    def test_preflight_fails_on_dirty_workspace(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_dirty_ws',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor(clean=False)
        envelope.discover_topology(executor)

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')

        self.assertFalse(passed)
        orch = envelope.partitions['orchestration']
        self.assertEqual('failed', orch['status'])
        self.assertEqual(PreflightLayer.REPO_AND_VERSION, orch['first_failing_layer'])
        self.assertEqual(FailureCategory.IMPLEMENTATION, orch['failure_category'])
        self.assertIn('工作区不干净', orch['failure_reason'])

    def test_preflight_fails_on_ssh_failure(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_ssh_fail',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        envelope.discover_topology(executor)
        # 模拟 SSH 到 client2 失败
        executor.set_response('client2', 'ssh_check', 255, '', 'Permission denied (publickey)')

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')

        self.assertFalse(passed)
        orch = envelope.partitions['orchestration']
        self.assertEqual('failed', orch['status'])
        self.assertEqual(PreflightLayer.SSH_AND_SUDO, orch['first_failing_layer'])
        self.assertEqual(PreflightLayer.REPO_AND_VERSION, orch['last_successful_layer'])
        self.assertEqual(FailureCategory.ENVIRONMENT, orch['failure_category'])

    def test_preflight_fails_on_sudo_failure(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_sudo_fail',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        envelope.discover_topology(executor)
        # 模拟 client1 免密 sudo 失败
        executor.set_response('client1', 'sudo -n true', 1, '', 'sudo: a password is required')

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')

        self.assertFalse(passed)
        orch = envelope.partitions['orchestration']
        self.assertEqual(PreflightLayer.SSH_AND_SUDO, orch['first_failing_layer'])
        self.assertEqual(FailureCategory.ENVIRONMENT, orch['failure_category'])

    def test_preflight_fails_on_missing_dependency(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_missing_dep',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        envelope.discover_topology(executor)
        # 模拟 server 缺少 uftp
        executor.set_response('server', 'command -v uftp', 1, '', '')

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')

        self.assertFalse(passed)
        orch = envelope.partitions['orchestration']
        self.assertEqual(PreflightLayer.DEPENDENCIES, orch['first_failing_layer'])
        self.assertEqual(FailureCategory.TOOLING, orch['failure_category'])

    def test_preflight_fails_on_wireless_usb_speed_strict(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_usb_speed_strict',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        # 模拟 client2 usb speed 仅为 12 Mbps
        executor.set_response('client2', 'cat /sys/class/net/wlxfc221c500a88/device/../speed', 0, '12\n', '')
        envelope.discover_topology(executor)

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo',
                                       strict_usb=True)

        self.assertFalse(passed)
        orch = envelope.partitions['orchestration']
        self.assertEqual(PreflightLayer.WIRELESS_USB, orch['first_failing_layer'])
        self.assertEqual(FailureCategory.ENVIRONMENT, orch['failure_category'])

    def test_preflight_fails_on_radio_monitor_failure(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_monitor_fail',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        envelope.discover_topology(executor)
        # 模拟 server 设置 monitor 模式失败
        executor.set_response('server', 'iw dev wlxfc221c300cbc info', 0, 'Interface wlxfc221c300cbc\n  type managed\n', '')

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')

        self.assertFalse(passed)
        orch = envelope.partitions['orchestration']
        self.assertEqual(PreflightLayer.RADIO_MONITOR_CHANNEL, orch['first_failing_layer'])
        self.assertEqual(FailureCategory.ENVIRONMENT, orch['failure_category'])

    def test_preflight_fails_on_port_conflict(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_port_conflict',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        envelope.discover_topology(executor)
        # 模拟 server 8080 端口已被占用
        executor.set_response('server', 'ss -tuln', 0, 'tcp LISTEN 0 128 0.0.0.0:8080 0.0.0.0:*\n', '')

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')

        self.assertFalse(passed)
        orch = envelope.partitions['orchestration']
        self.assertEqual(PreflightLayer.NETWORK_PORTS_AND_TUN, orch['first_failing_layer'])
        self.assertEqual(FailureCategory.ENVIRONMENT, orch['failure_category'])

    def test_preflight_fails_on_tun_exists(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_tun_exists',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        envelope.discover_topology(executor)
        # 模拟 server 存在旧 TUN 设备 v8i41s0
        executor.set_response('server', 'ip link show v8i41s0', 0, 'v8i41s0: <POINTOPOINT,MULTICAST,NOARP> mtu 1500\n', '')

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')

        self.assertFalse(passed)
        orch = envelope.partitions['orchestration']
        self.assertEqual(PreflightLayer.NETWORK_PORTS_AND_TUN, orch['first_failing_layer'])
        self.assertEqual(FailureCategory.ENVIRONMENT, orch['failure_category'])

    def test_preflight_fails_on_residual_process(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_residual_proc',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        envelope.discover_topology(executor)
        # 模拟 client1 发现残留的 uftpd 进程
        executor.set_response('client1', 'pgrep -x uftpd', 0, '12345\n', '')

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')

        self.assertFalse(passed)
        orch = envelope.partitions['orchestration']
        self.assertEqual(PreflightLayer.RESIDUAL_PROCESSES, orch['first_failing_layer'])
        self.assertEqual(FailureCategory.ENVIRONMENT, orch['failure_category'])

    def test_stale_work_dir_pollution_rejected(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_stale_dir',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        envelope.discover_topology(executor)
        # 模拟 server 上次运行残留工作目录，且未显式 reset
        executor.set_response('server', 'check_work_dir_empty /var/lib/wfb-ng/issue41/server', 1, 'non-empty\n', '')

        passed = envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo',
                                       reset_runtime_state=False)

        self.assertFalse(passed)
        orch = envelope.partitions['orchestration']
        self.assertEqual(PreflightLayer.MANAGED_DIRECTORY_BOUNDARY, orch['first_failing_layer'])
        self.assertEqual(FailureCategory.TOOLING, orch['failure_category'])

    def test_controlled_stop_kills_processes_and_waits_for_tuns(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_ctrl_stop',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = DummyExecutor()
        # 模拟 stop-all 指令均成功，进程均不存在
        executor.set_response('server', 'pgrep', 1, '', '')
        executor.set_response('client1', 'pgrep', 1, '', '')
        executor.set_response('client2', 'pgrep', 1, '', '')
        executor.set_response('server', 'ip link show', 1, 'Device "v8i41s0" does not exist.\n', '')
        executor.set_response('client1', 'ip link show', 1, 'Device "v8i41c1" does not exist.\n', '')
        executor.set_response('client2', 'ip link show', 1, 'Device "v8i41c2" does not exist.\n', '')

        clean = envelope.controlled_stop(executor, cleanup_timeout=2)
        self.assertTrue(clean)

    def test_controlled_stop_records_teardown_failure_when_residual_remains(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_stop_fail',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = DummyExecutor()
        # 模拟 client2 上 wfb_v6_uplink 怎么杀都杀不掉
        executor.set_response('client2', 'pgrep -x wfb_v6_uplink', 0, '9999\n', '')

        clean = envelope.controlled_stop(executor, cleanup_timeout=1)
        self.assertFalse(clean)

        stop_log = os.path.join(envelope.archive_dir, 'orchestration', 'teardown_result.json')
        self.assertTrue(os.path.isfile(stop_log))
        with open(stop_log, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        self.assertFalse(data['clean'])
        self.assertIn('client2 停止后仍有残留', data['reason'])

    def test_append_partition_interface_and_summary_generation(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_append_part',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        executor = make_mock_topology_executor()
        envelope.discover_topology(executor)
        envelope.run_preflight(executor, expected_branch='feat/41-real-hardware-fl-runtime-redo')

        # 验证运行包络自身不预判后续状态
        self.assertNotIn('pre_runtime_smoke', envelope.partitions)
        self.assertNotIn('formal_runtime_loop', envelope.partitions)

        # 追加 pre_runtime_smoke 分区 (数据面 Gate)
        smoke_data = {
            'run_id': envelope.run_id,
            'status': 'passed',
            'gate_type': 'three_cycle_bidirectional',
            'cycle_count': 3,
        }
        envelope.append_partition('pre_runtime_smoke', smoke_data)

        self.assertEqual(smoke_data, envelope.partitions['pre_runtime_smoke'])
        # 检查持久化 summary 包含已追加的分区
        summary_path = os.path.join(envelope.archive_dir, 'issue41_summary.json')
        with open(summary_path, 'r', encoding='utf-8') as fh:
            summary = json.load(fh)
        self.assertIn('pre_runtime_smoke', summary)
        self.assertEqual('passed', summary['pre_runtime_smoke']['status'])

    def test_record_stage_failure_classifies_and_updates_summary(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_stage_fail',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        gate_failure_data = {
            'run_id': envelope.run_id,
            'status': 'failed',
            'gate_type': 'three_cycle_bidirectional',
        }
        envelope.record_stage_failure(
            partition_name='pre_runtime_smoke',
            partition_data=gate_failure_data,
            category='link_capability',
            last_successful='tun_and_route',
            first_failing='uftp_feedback_and_status',
            reason='UFTP 注册超时未命中',
        )
        summary_path = os.path.join(envelope.archive_dir, 'issue41_summary.json')
        with open(summary_path, 'r', encoding='utf-8') as fh:
            summary = json.load(fh)
        self.assertEqual('failed', summary['conclusion']['status'])
        self.assertEqual('link_capability', summary['conclusion']['category'])
        self.assertEqual('tun_and_route', summary['conclusion']['last_successful_layer'])
        self.assertEqual('uftp_feedback_and_status', summary['conclusion']['first_failing_layer'])
        self.assertEqual('failed', summary['pre_runtime_smoke']['status'])

    def test_append_partition_rejects_mixed_run_id(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_run_A',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()

        bad_data = {
            'run_id': 'v8_issue41_run_B',  # 跨 run ID
            'status': 'passed',
        }
        with self.assertRaises(ValueError) as ctx:
            envelope.append_partition('pre_runtime_smoke', bad_data)
        self.assertIn('run ID 不匹配', str(ctx.exception))

    def test_cannot_modify_config_or_resume_closed_envelope(self):
        envelope = RunEnvelope(
            run_id='v8_issue41_closed',
            archive_root=self.archive_root,
            branch='feat/41-real-hardware-fl-runtime-redo',
            commit='0123456789abcdef0123456789abcdef01234567',
            resolved_config=self.default_config,
        )
        envelope.initialize()
        envelope.close(reason='测试正常结束')

        with self.assertRaises(RuntimeError) as ctx:
            envelope.append_partition('pre_runtime_smoke', {'status': 'passed'})
        self.assertIn('包络已关闭', str(ctx.exception))

    def test_cli_init_and_fail_on_existing_dir(self):
        from tests.real_hardware.issue41_envelope import main
        run_id = 'v8_issue41_cli_test'
        archive_dir = os.path.join(self.archive_root, run_id)
        # 第一次 init 成功
        rc = main(['init', '--archive-dir', archive_dir, '--run-id', run_id])
        self.assertEqual(0, rc)
        self.assertTrue(os.path.isfile(os.path.join(archive_dir, 'envelope.json')))

        # 第二次 init 同一路径应抛出 FileExistsError
        with self.assertRaises(FileExistsError):
            main(['init', '--archive-dir', archive_dir, '--run-id', run_id])

    def test_cli_record_failure_creates_summary_and_result(self):
        from tests.real_hardware.issue41_envelope import main
        run_id = 'v8_issue41_cli_fail'
        archive_dir = os.path.join(self.archive_root, run_id)
        main(['init', '--archive-dir', archive_dir, '--run-id', run_id])

        rc = main([
            'record-failure',
            '--archive-dir', archive_dir,
            '--layer', PreflightLayer.SSH_AND_SUDO,
            '--category', FailureCategory.ENVIRONMENT,
            '--reason', 'SSH 连通超时',
            '--last-successful-layer', PreflightLayer.REPO_AND_VERSION,
        ])
        self.assertEqual(0, rc)

        summary_path = os.path.join(archive_dir, 'issue41_summary.json')
        self.assertTrue(os.path.isfile(summary_path))
        with open(summary_path, 'r', encoding='utf-8') as fh:
            summary = json.load(fh)
        self.assertEqual('failed', summary['conclusion']['status'])
        self.assertEqual(FailureCategory.ENVIRONMENT, summary['conclusion']['category'])
        self.assertEqual(PreflightLayer.SSH_AND_SUDO, summary['conclusion']['first_failing_layer'])
        self.assertEqual(PreflightLayer.REPO_AND_VERSION, summary['conclusion']['last_successful_layer'])

        result_path = os.path.join(archive_dir, 'result.md')
        self.assertTrue(os.path.isfile(result_path))
        with open(result_path, 'r', encoding='utf-8') as fh:
            content = fh.read()
        self.assertIn('failure_category: environment', content)
        self.assertIn('first_failing_layer: ssh_and_sudo', content)

    def test_cli_append_partition_updates_summary(self):
        from tests.real_hardware.issue41_envelope import main
        run_id = 'v8_issue41_cli_part'
        archive_dir = os.path.join(self.archive_root, run_id)
        main(['init', '--archive-dir', archive_dir, '--run-id', run_id])

        part_file = os.path.join(self.temp_dir, 'part.json')
        with open(part_file, 'w', encoding='utf-8') as fh:
            json.dump({'run_id': run_id, 'status': 'passed', 'data': 'sample'}, fh)

        rc = main([
            'append-partition',
            '--archive-dir', archive_dir,
            '--name', 'lifecycle',
            '--json-file', part_file,
        ])
        self.assertEqual(0, rc)

        summary_path = os.path.join(archive_dir, 'issue41_summary.json')
        with open(summary_path, 'r', encoding='utf-8') as fh:
            summary = json.load(fh)
        self.assertEqual('passed', summary['lifecycle']['status'])

    def test_cli_record_stage_failure_preserves_completed_partitions(self):
        from tests.real_hardware.issue41_envelope import main
        run_id = 'v8_issue41_cli_stage_fail'
        archive_dir = os.path.join(self.archive_root, run_id)
        main(['init', '--archive-dir', archive_dir, '--run-id', run_id])

        # 先追加一个 passed 的 pre_runtime_smoke
        smoke_file = os.path.join(self.temp_dir, 'smoke.json')
        with open(smoke_file, 'w', encoding='utf-8') as fh:
            json.dump({'run_id': run_id, 'status': 'passed', 'data': 'gate_ok'}, fh)
        main(['append-partition', '--archive-dir', archive_dir, '--name', 'pre_runtime_smoke', '--json-file', smoke_file])

        # 记录 formal_runtime_loop 失败
        runtime_fail_file = os.path.join(self.temp_dir, 'runtime_fail.json')
        with open(runtime_fail_file, 'w', encoding='utf-8') as fh:
            json.dump({'run_id': run_id, 'status': 'failed', 'reason': 'client2 timeout'}, fh)

        rc = main([
            'record-stage-failure',
            '--archive-dir', archive_dir,
            '--partition-name', 'formal_runtime_loop',
            '--json-file', runtime_fail_file,
            '--category', FailureCategory.IMPLEMENTATION,
            '--reason', 'client2 timeout',
            '--last-successful-layer', 'pre_runtime_smoke',
            '--first-failing-layer', 'formal_runtime_loop',
        ])
        self.assertEqual(0, rc)

        summary_path = os.path.join(archive_dir, 'issue41_summary.json')
        with open(summary_path, 'r', encoding='utf-8') as fh:
            summary = json.load(fh)
        self.assertEqual('failed', summary['conclusion']['status'])
        self.assertEqual('client2 timeout', summary['conclusion']['reason'])
        self.assertEqual('passed', summary['pre_runtime_smoke']['status'])
        self.assertEqual('failed', summary['formal_runtime_loop']['status'])

    def test_initialize_rejects_candidate_feedback_in_resolved_config(self):
        bad_cfg = dict(self.default_config)
        bad_cfg['feedback_window_start_immediately'] = True
        envelope = RunEnvelope(
            run_id='v8_issue41_bad_feedback',
            archive_root=self.archive_root,
            resolved_config=bad_cfg,
        )
        with self.assertRaises(ValueError) as ctx:
            envelope.initialize()
        self.assertIn('严禁使用 --feedback-window-start-immediately', str(ctx.exception))




if __name__ == '__main__':
    unittest.main()
