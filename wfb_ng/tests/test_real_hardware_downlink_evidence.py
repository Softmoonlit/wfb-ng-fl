#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap

from twisted.trial import unittest


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, 'tests', 'real_hardware', 'test_full_transfer.sh')


class DownlinkEvidenceScriptTestCase(unittest.TestCase):
    def write_executable(self, directory, name, content):
        path = os.path.join(directory, name)
        with open(path, 'w') as fh:
            fh.write(content)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def make_receiver_script(self, directory, name):
        script = textwrap.dedent(
            """
            #!/usr/bin/env python3
            import os
            import time

            deadline = time.time() + 10
            watched = [
                os.environ.get('DOWNLINK_CLIENT1_RECEIVED_FILE', ''),
                os.environ.get('DOWNLINK_CLIENT2_RECEIVED_FILE', ''),
            ]
            while time.time() < deadline:
                if any(path and os.path.exists(path) for path in watched):
                    time.sleep(1)
                    break
                time.sleep(0.2)
            """
        )
        return self.write_executable(directory, name, script)

    def make_sender_script(self, directory, mode):
        script = textwrap.dedent(
            """
            #!/usr/bin/env python3
            import os
            import time

            mode = os.environ['DOWNLINK_TEST_MODE']
            source = os.environ['DOWNLINK_PAYLOAD_FILE']
            client1 = os.environ['DOWNLINK_CLIENT1_RECEIVED_FILE']
            client2 = os.environ.get('DOWNLINK_CLIENT2_RECEIVED_FILE', '')

            with open(source, 'rb') as fh:
                payload = fh.read()

            def ensure_parent(path):
                if path:
                    os.makedirs(os.path.dirname(path), exist_ok=True)

            def write_partial(path, size):
                ensure_parent(path)
                with open(path, 'wb') as fh:
                    fh.write(payload[:size])
                    fh.flush()
                    os.fsync(fh.fileno())

            def finish_logs(retries, duration, warning=None):
                if warning:
                    print(warning, flush=True)
                print(f'retries: {retries}', flush=True)
                print(f'Transfer complete, duration: {duration}s', flush=True)

            half = len(payload) // 2
            quarter = max(1, len(payload) // 4)

            if mode == 'single-success':
                write_partial(client1, half)
                print('progress client1 half', flush=True)
                time.sleep(1)
                write_partial(client1, len(payload))
                finish_logs(2, 2.0, warning='warning: retransmit window expanded once')
            elif mode == 'shared-success':
                write_partial(client1, half)
                write_partial(client2, quarter)
                print('progress both receivers step1', flush=True)
                time.sleep(1)
                write_partial(client2, half)
                print('progress client2 step2', flush=True)
                time.sleep(1)
                write_partial(client1, len(payload))
                write_partial(client2, len(payload))
                finish_logs(4, 3.0, warning='warning: shared downlink observed retry burst')
            elif mode == 'stall-fail':
                write_partial(client1, quarter)
                print('progress client1 quarter', flush=True)
                time.sleep(4)
                write_partial(client1, len(payload))
                finish_logs(7, 5.0, warning='warning: prolonged stall before recovery')
            else:
                raise RuntimeError(f'unknown mode: {mode}')
            """
        )
        return self.write_executable(directory, 'emit_sender.py', script)

    def file_sha256(self, path):
        digest = hashlib.sha256()
        with open(path, 'rb') as fh:
            for chunk in iter(lambda: fh.read(65536), b''):
                digest.update(chunk)
        return digest.hexdigest()

    def run_downlink_script(self, scenario, mode, max_idle='2'):
        temp_dir = tempfile.mkdtemp(prefix='wfb-downlink-', dir='/tmp')
        log_dir = os.path.join(temp_dir, 'logs')
        os.makedirs(log_dir)

        receiver1 = self.make_receiver_script(temp_dir, 'receiver1.py')
        receiver2 = self.make_receiver_script(temp_dir, 'receiver2.py')
        sender = self.make_sender_script(temp_dir, mode)

        env = os.environ.copy()
        env.update({
            'LOG_DIR': log_dir,
            'DOWNLINK_PAYLOAD_SIZE': '1024',
            'DOWNLINK_TRANSFER_TIMEOUT_SEC': '8',
            'DOWNLINK_SAMPLE_INTERVAL_SEC': '1',
            'DOWNLINK_MAX_IDLE_SEC': max_idle,
            'DOWNLINK_CLIENT1_RECEIVE_CMD': '{python} {script}'.format(python=sys.executable, script=receiver1),
            'DOWNLINK_CLIENT2_RECEIVE_CMD': '{python} {script}'.format(python=sys.executable, script=receiver2),
            'DOWNLINK_UFTP_SEND_CMD': '{python} {script}'.format(python=sys.executable, script=sender),
            'DOWNLINK_TEST_MODE': mode,
        })

        if scenario == 'shared':
            env['DOWNLINK_RECEIVER_COUNT'] = '2'

        try:
            result = subprocess.run(
                ['bash', SCRIPT_PATH, '--scenario', scenario],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            return temp_dir, log_dir, result
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def test_single_downlink_evidence_generates_summary_chain(self):
        temp_dir, log_dir, result = self.run_downlink_script('single', 'single-success')
        try:
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

            result_md = os.path.join(log_dir, 'downlink_results.md')
            context_file = os.path.join(log_dir, 'downlink_context.txt')
            samples_file = os.path.join(log_dir, 'downlink_samples.tsv')
            metrics_file = os.path.join(log_dir, 'metrics.json')
            summary_file = os.path.join(log_dir, 'summary.txt')
            received_file = os.path.join(log_dir, 'client1', 'received', 'downlink_payload.bin')

            for path in (result_md, context_file, samples_file, metrics_file, summary_file, received_file):
                self.assertTrue(os.path.exists(path), msg='missing {}'.format(path))

            with open(result_md, 'r') as fh:
                result_text = fh.read()
            with open(context_file, 'r') as fh:
                context_text = fh.read()
            with open(samples_file, 'r') as fh:
                samples_text = fh.read()
            with open(metrics_file, 'r') as fh:
                metrics_text = fh.read()
            with open(summary_file, 'r') as fh:
                summary_text = fh.read()

            self.assertIn('- 结果: PASS', result_text)
            self.assertIn('- 场景: single', result_text)
            self.assertIn('双客户端 Token-gated 上行 long-run 仍是 real-hardware 主场景', result_text)
            self.assertIn('## 人工判读关注点', result_text)

            self.assertIn('scenario=single', context_text)
            self.assertIn('receiver_count=1', context_text)
            self.assertIn('shared_distribution_confirmed=not_applicable', context_text)
            self.assertIn('result_status=PASS', context_text)

            self.assertIn('elapsed_sec\tclient1_bytes\tclient2_bytes\tprogressed\tidle_sec', samples_text)
            self.assertIn('\tYES\t', samples_text)

            self.assertIn('"receiver_count": 1', metrics_text)
            self.assertIn('"shared_distribution_confirmed": false', metrics_text)
            self.assertIn('"stall_events": 0', metrics_text)
            self.assertIn('"retries": 2', metrics_text)

            self.assertIn('--- 自动摘要 ---', summary_text)
            self.assertIn('共享下行/分发证据: N/A（single 场景）', summary_text)
            self.assertIn('--- 需要人工判读 ---', summary_text)
            self.assertIn('downlink_samples.tsv', summary_text)
            self.assertIn('--- Issue 回填最小字段 ---', summary_text)
            self.assertIn('是否可作为正式 v5 基线证据', summary_text)
            self.assertIn('## 正式归档要求', result_text)
            self.assertIn('tracking issue 未按固定模板回填正式结论前不得关闭', result_text)

            payload_file = os.path.join(log_dir, 'downlink_payload.bin')
            self.assertEqual(self.file_sha256(payload_file), self.file_sha256(received_file))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_shared_downlink_evidence_confirms_distribution(self):
        temp_dir, log_dir, result = self.run_downlink_script('shared', 'shared-success')
        try:
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

            result_md = os.path.join(log_dir, 'downlink_results.md')
            context_file = os.path.join(log_dir, 'downlink_context.txt')
            summary_file = os.path.join(log_dir, 'summary.txt')
            client1_file = os.path.join(log_dir, 'client1', 'received', 'downlink_payload.bin')
            client2_file = os.path.join(log_dir, 'client2', 'received', 'downlink_payload.bin')

            for path in (result_md, context_file, summary_file, client1_file, client2_file):
                self.assertTrue(os.path.exists(path), msg='missing {}'.format(path))

            with open(result_md, 'r') as fh:
                result_text = fh.read()
            with open(context_file, 'r') as fh:
                context_text = fh.read()
            with open(summary_file, 'r') as fh:
                summary_text = fh.read()

            self.assertIn('- 场景: shared', result_text)
            self.assertIn('- 共享下行/分发证据: PASS', result_text)
            self.assertIn('receiver_count=2', context_text)
            self.assertIn('shared_distribution_confirmed=yes', context_text)
            self.assertIn('共享下行/分发证据: PASS', summary_text)
            self.assertIn('--- Issue 回填最小字段 ---', summary_text)

            payload_file = os.path.join(log_dir, 'downlink_payload.bin')
            expected_sha = self.file_sha256(payload_file)
            self.assertEqual(expected_sha, self.file_sha256(client1_file))
            self.assertEqual(expected_sha, self.file_sha256(client2_file))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_downlink_stall_failure_still_emits_evidence_artifacts(self):
        temp_dir, log_dir, result = self.run_downlink_script('single', 'stall-fail', max_idle='1')
        try:
            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)

            result_md = os.path.join(log_dir, 'downlink_results.md')
            context_file = os.path.join(log_dir, 'downlink_context.txt')
            metrics_file = os.path.join(log_dir, 'metrics.json')
            summary_file = os.path.join(log_dir, 'summary.txt')

            for path in (result_md, context_file, metrics_file, summary_file):
                self.assertTrue(os.path.exists(path), msg='missing {}'.format(path))

            with open(result_md, 'r') as fh:
                result_text = fh.read()
            with open(context_file, 'r') as fh:
                context_text = fh.read()
            with open(metrics_file, 'r') as fh:
                metrics_text = fh.read()
            with open(summary_file, 'r') as fh:
                summary_text = fh.read()

            self.assertIn('- 结果: FAIL', result_text)
            self.assertIn('连续无推进窗口超过 1 秒', result_text)
            self.assertIn('result_status=FAIL', context_text)
            self.assertIn('result_reason=连续无推进窗口超过 1 秒', context_text)
            self.assertIn('"stall_events": 1', metrics_text)
            self.assertIn('自动结论: FAIL', summary_text)
            self.assertIn('--- Issue 回填最小字段 ---', summary_text)
            self.assertIn('## 正式归档要求', result_text)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
