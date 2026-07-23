#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ast
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest


HERE = os.path.dirname(__file__)
SCRIPT = os.path.join(HERE, 'issue55_57_terminal_demo.sh')
CONTROL = os.path.join(HERE, 'issue55_57_demo_control.py')
WRAPPERS = {
    'server': os.path.join(HERE, 'issue57_server_demo.sh'),
    'client1': os.path.join(HERE, 'issue56_client1_demo.sh'),
    'client2': os.path.join(HERE, 'issue56_client2_demo.sh'),
}


class Issue55To57TerminalDemoTestCase(unittest.TestCase):
    def free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            return sock.getsockname()[1]

    def demo_env(self, port, **overrides):
        env = os.environ.copy()
        env.update({
            'DEMO_DELAY_SECONDS': '0',
            'DEMO_SERVER_HOST': '127.0.0.1',
            'DEMO_CONTROL_BIND': '127.0.0.1',
            'DEMO_CONTROL_PORT': str(port),
            'DEMO_CONTROL_TIMEOUT_SECONDS': '3',
            'DEMO_CONNECT_TIMEOUT_SECONDS': '3',
            'DEMO_WAIT_STEP_SECONDS': '0.01',
            'NO_COLOR': '1',
        })
        env.update(overrides)
        return env

    def start_role(self, role, env, cwd=None, speed=None):
        command = ['/bin/bash', WRAPPERS[role]]
        if speed is not None:
            command.append(str(speed))
        return subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def run_trio(self, cwd=None, speed=5, env_overrides=None):
        port = self.free_port()
        env = self.demo_env(port)
        if env_overrides:
            env.update(env_overrides)
        server = self.start_role('server', env, cwd, speed)
        client1 = self.start_role('client1', env, cwd, speed)
        client2 = self.start_role('client2', env, cwd, speed)
        processes = {'server': server, 'client1': client1, 'client2': client2}
        results = {}
        try:
            for role, process in processes.items():
                stdout, stderr = process.communicate(timeout=10)
                results[role] = (process.returncode, stdout, stderr)
        finally:
            for process in processes.values():
                if process.poll() is None:
                    process.kill()
                    process.wait()
        return results

    def test_scripts_have_valid_syntax(self):
        with open(CONTROL, 'r', encoding='utf-8') as fh:
            ast.parse(fh.read(), CONTROL)
        for path in (SCRIPT, *WRAPPERS.values()):
            result = subprocess.run(
                ['/bin/bash', '-n', path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual('', result.stderr, path)
            self.assertEqual(0, result.returncode, path)

    def test_three_roles_synchronize_two_rounds_and_pass(self):
        results = self.run_trio()

        for role, (returncode, _, stderr) in results.items():
            with self.subTest(role=role):
                self.assertEqual('', stderr)
                self.assertEqual(0, returncode)

        server_output = results['server'][1]
        self.assertIn('演示速率=5 Mbps', server_output)
        self.assertNotIn('基准传输时间', server_output)
        self.assertIn('client1 控制连接已建立', server_output)
        self.assertIn('client2 控制连接已建立', server_output)
        self.assertEqual(2, server_output.count('模型接收确认已收齐'))
        self.assertEqual(
            2, server_output.count('update 完成控制信号已收齐并确认'))
        self.assertEqual(2, server_output.count('完整 update 集合已收齐'))
        for round_number in (1, 2):
            for node_id in (1, 2):
                self.assertIn(
                    f'update_path=/var/lib/wfb-ng/issue55-57/server/'
                    f'round-{round_number}/updates/node-{node_id}.bin',
                    server_output,
                )
        self.assertIn('server 本地演示过程成功；rounds=2 result=passed', server_output)

        for role, node_id in (('client1', 1), ('client2', 2)):
            output = results[role][1]
            with self.subTest(role=role):
                self.assertEqual(2, output.count('检测到 server 模型发布'))
                self.assertEqual(10, output.count('模型接收：'))
                self.assertEqual(10, output.count('synthetic update 上传：'))
                self.assertEqual(2, output.count('HTTP PUT 完成：status=201'))
                for round_number in (1, 2):
                    self.assertIn(
                        f'model_path=/var/lib/wfb-ng/issue55-57/{role}/'
                        f'round-{round_number}/model.bin',
                        output,
                    )
                self.assertIn(
                    f'{role} 本地演示过程成功；rounds=2 '
                    f'node_id={node_id} result=passed',
                    output,
                )
                elapsed = re.findall(
                    r'(?:模型接收完成|HTTP PUT 完成).*?elapsed=([0-9.]+)s',
                    output,
                )
                self.assertEqual(4, len(elapsed))
                self.assertEqual(4, len(set(elapsed)))

    def test_five_mbps_uses_expected_varied_durations(self):
        results = self.run_trio(speed=5)

        self.assertIn('elapsed=69.79s effective_mbps=4.81', results['server'][1])
        self.assertIn('elapsed=72.48s effective_mbps=4.63', results['server'][1])
        self.assertIn('elapsed=68.45s effective_mbps=4.90', results['client1'][1])
        self.assertIn('elapsed=139.59s effective_mbps=2.40', results['client1'][1])
        self.assertIn('elapsed=71.14s effective_mbps=4.72', results['client2'][1])
        self.assertIn('elapsed=129.52s effective_mbps=2.59', results['client2'][1])
        self.assertIn('elapsed=144.28s effective_mbps=2.33', results['server'][1])

        for role, (_, output, _) in results.items():
            effective_values = re.findall(r'effective_mbps=([0-9.]+)', output)
            self.assertTrue(effective_values, role)
            for effective in effective_values:
                self.assertLess(float(effective), 5.0, (role, effective))

        for role in ('client1', 'client2'):
            update_times = re.findall(
                r'HTTP PUT 完成.*?elapsed=([0-9]+\.[0-9]{2})s',
                results[role][1],
            )
            self.assertEqual(2, len(update_times))
            for elapsed in update_times:
                self.assertGreater(float(elapsed), 67.11 * 1.9)
                self.assertLess(float(elapsed), 67.11 * 2.2)

    def test_commit_barrier_confirms_control_sync_without_false_failure_text(self):
        results = self.run_trio(
            speed=5000,
            env_overrides={
                'DEMO_DELAY_SECONDS': '1',
                'DEMO_EVENT_DELAY_SECONDS': '0',
                'DEMO_WAIT_STEP_SECONDS': '0.002',
            },
        )

        for role in ('client1', 'client2'):
            output = results[role][1]
            self.assertNotIn(
                '等待 server commit；尚未收到控制信号', output)
            self.assertEqual(
                2, output.count('server 已确认本机 update 完整接收；控制同步成功'))

    def test_server_does_not_publish_without_both_clients(self):
        port = self.free_port()
        env = self.demo_env(
            port,
            DEMO_CONNECT_TIMEOUT_SECONDS='0.25',
            DEMO_CONTROL_TIMEOUT_SECONDS='0.25',
        )
        server = self.start_role('server', env)
        client1 = self.start_role('client1', env)
        try:
            server_stdout, _ = server.communicate(timeout=3)
            client1_stdout, _ = client1.communicate(timeout=3)
        finally:
            for process in (server, client1):
                if process.poll() is None:
                    process.kill()
                    process.wait()

        self.assertNotEqual(0, server.returncode)
        self.assertNotEqual(0, client1.returncode)
        self.assertNotIn('模型发布开始', server_stdout)
        self.assertNotIn('模型接收开始', client1_stdout)
        self.assertIn('等待 client 连接超时', server_stdout)

    def test_duplicate_node_id_fails_closed(self):
        port = self.free_port()
        env = self.demo_env(
            port,
            DEMO_CONNECT_TIMEOUT_SECONDS='1',
            DEMO_CONTROL_TIMEOUT_SECONDS='1',
        )
        server = self.start_role('server', env)
        first = self.start_role('client1', env)
        second = self.start_role('client1', env)
        processes = (server, first, second)
        try:
            outputs = [process.communicate(timeout=4)[0] for process in processes]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()

        self.assertNotEqual(0, server.returncode)
        self.assertIn('拒绝重复 NODE_ID：1', outputs[0])
        self.assertNotIn('模型发布开始', outputs[0])

    def test_mismatched_transfer_speed_fails_closed(self):
        port = self.free_port()
        env = self.demo_env(port)
        server = self.start_role('server', env, speed=5)
        client1 = self.start_role('client1', env, speed=4)
        client2 = self.start_role('client2', env, speed=5)
        processes = (server, client1, client2)
        try:
            outputs = [process.communicate(timeout=4)[0] for process in processes]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()

        self.assertNotEqual(0, server.returncode)
        self.assertIn('拒绝速率不一致', outputs[0])
        self.assertNotIn('模型发布开始', outputs[0])

    def test_client_does_not_continue_without_server(self):
        port = self.free_port()
        env = self.demo_env(port, DEMO_CONNECT_TIMEOUT_SECONDS='0.2')
        client = self.start_role('client1', env)
        stdout, _ = client.communicate(timeout=3)

        self.assertNotEqual(0, client.returncode)
        self.assertIn('连接 server 控制通道超时', stdout)
        self.assertNotIn('模型接收开始', stdout)
        self.assertNotIn('result=passed', stdout)

    def test_run_has_no_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory() as work_dir:
            before = os.listdir(work_dir)
            results = self.run_trio(cwd=work_dir)
            after = os.listdir(work_dir)

        self.assertTrue(all(result[0] == 0 for result in results.values()))
        self.assertEqual(before, after)

    def test_control_channel_contains_no_fl_execution_paths(self):
        with open(SCRIPT, 'r', encoding='utf-8') as fh:
            shell_script = fh.read()
        with open(CONTROL, 'r', encoding='utf-8') as fh:
            controller = fh.read()
        combined = shell_script + controller

        for forbidden in (
            'run-all', 'issue41_fl_runtime_loop.sh', 'systemctl ',
            'sudo ', 'ssh ', 'scp ', 'curl ', 'wget ', '/dev/tcp',
            'wfb_v6_uplink ', 'uftp ', 'uftpd ', 'ip link ', 'iw dev ',
            'sendfile(', "b'0' * MODEL_BYTES", "b'\\0' * MODEL_BYTES",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)
        self.assertIn("if len(encoded) > 1024", controller)

    def test_unknown_role_fails_with_usage(self):
        result = subprocess.run(
            [sys.executable, CONTROL, 'unknown'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        self.assertEqual(2, result.returncode)
        self.assertEqual('', result.stdout)
        self.assertIn('用法：', result.stderr)


if __name__ == '__main__':
    unittest.main()
