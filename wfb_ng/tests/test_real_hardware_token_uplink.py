#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap

from twisted.trial import unittest


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, 'tests', 'real_hardware', 'test_token_gated_uplink.sh')


class DualLongRunScriptTestCase(unittest.TestCase):
    def write_executable(self, directory, name, content):
        path = os.path.join(directory, name)
        with open(path, 'w') as fh:
            fh.write(content)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def make_scheduler_script(self, directory, emit_rejoin):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import signal
            import sys
            import time

            EMIT_REJOIN = __EMIT_REJOIN__

            def _stop(signum, frame):
                raise SystemExit(0)

            signal.signal(signal.SIGTERM, _stop)
            signal.signal(signal.SIGINT, _stop)

            print("join/rejoin node_id=1 active_queue=[1] cursor=0 cursor_node_id=1", flush=True)
            print("join/rejoin node_id=2 active_queue=[1,2] cursor=0 cursor_node_id=1", flush=True)

            seq = 0
            rejoin_emitted = False
            while True:
                node_id = 1 if seq % 2 == 0 else 2
                print(
                    "grant seq={seq} node_id={node_id} duration_ms=1000 guard_interval_ms=100 window_end_offset_ms=1000 active_queue=[1,2] cursor=0 cursor_node_id=1".format(
                        seq=seq,
                        node_id=node_id,
                    ),
                    flush=True,
                )
                print(
                    "guard seq={seq} guard_interval_ms=100 next_seq={next_seq}".format(
                        seq=seq,
                        next_seq=seq + 1,
                    ),
                    flush=True,
                )
                if EMIT_REJOIN and not rejoin_emitted and seq >= 3:
                    print(
                        "remove node_id=1 silence_ms=1200 consecutive_silent_grants=2 active_queue=[2] cursor=0 cursor_node_id=2",
                        flush=True,
                    )
                    print(
                        "join/rejoin node_id=1 active_queue=[2,1] cursor=0 cursor_node_id=2",
                        flush=True,
                    )
                    rejoin_emitted = True
                seq += 1
                time.sleep(0.2)
            """
        ).replace('__EMIT_REJOIN__', 'True' if emit_rejoin else 'False')
        return self.write_executable(directory, 'emit_scheduler.py', script)

    def make_client_script(self, directory, name):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import signal
            import time

            def _stop(signum, frame):
                raise SystemExit(0)

            signal.signal(signal.SIGTERM, _stop)
            signal.signal(signal.SIGINT, _stop)

            counter = 0
            while True:
                counter += 1
                ts = int(time.time() * 1000)
                print("{ts}\tTOKEN_FILTER\t{counter}:{counter}:0:0:0:0".format(ts=ts, counter=counter), flush=True)
                print("{ts}\tTOKEN_AUTH\t{counter}:0:{counter}:0".format(ts=ts, counter=counter), flush=True)
                time.sleep(0.2)
            """
        )
        return self.write_executable(directory, name, script)

    def make_server_script(self, directory):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import signal
            import time

            def _stop(signum, frame):
                raise SystemExit(0)

            signal.signal(signal.SIGTERM, _stop)
            signal.signal(signal.SIGINT, _stop)

            counter = 0
            while True:
                counter += 1
                ts = int(time.time() * 1000)
                print("{ts}\tPKT\t0:0:0:0:{counter}".format(ts=ts, counter=counter), flush=True)
                time.sleep(0.2)
            """
        )
        return self.write_executable(directory, 'emit_server.py', script)

    def run_dual_long_run(self, emit_rejoin):
        temp_dir = tempfile.mkdtemp(prefix='wfb-dual-long-run-', dir='/tmp')
        log_dir = os.path.join(temp_dir, 'logs')
        os.makedirs(log_dir)

        scheduler_script = self.make_scheduler_script(temp_dir, emit_rejoin)
        client1_script = self.make_client_script(temp_dir, 'emit_client1.py')
        client2_script = self.make_client_script(temp_dir, 'emit_client2.py')
        server_script = self.make_server_script(temp_dir)

        env = os.environ.copy()
        env.update({
            'LOG_DIR': log_dir,
            'TOKEN_SERVER_START_CMD': '{python} {script}'.format(python=sys.executable, script=server_script),
            'TOKEN_CLIENT1_START_CMD': '{python} {script}'.format(python=sys.executable, script=client1_script),
            'TOKEN_CLIENT2_START_CMD': '{python} {script}'.format(python=sys.executable, script=client2_script),
            'TOKEN_SCHEDULER_CMD': '{python} {script}'.format(python=sys.executable, script=scheduler_script),
            'TOKEN_CLIENT1_PROBE_CMD': 'true',
            'TOKEN_CLIENT2_PROBE_CMD': 'true',
            'TOKEN_SCHEDULER_WARMUP': '1',
            'TOKEN_LONGRUN_DURATION_SEC': '3',
            'TOKEN_LONGRUN_PROBE_INTERVAL_SEC': '1',
            'TOKEN_LONGRUN_SAMPLE_INTERVAL_SEC': '1',
            'TOKEN_LONGRUN_MIN_GRANTS': '6',
            'TOKEN_LONGRUN_MIN_AUTHORIZED_SENDS': '6',
            'TOKEN_LONGRUN_MAX_IDLE_SEC': '2',
            'TOKEN_LONGRUN_MAX_REMOVE_COUNT': '0',
            'TOKEN_LONGRUN_MAX_EVICT_COUNT': '0',
            'TOKEN_LONGRUN_MAX_REJOIN_COUNT': '0',
        })

        try:
            result = subprocess.run(
                ['bash', SCRIPT_PATH, '--scenario', 'dual-long-run'],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            return temp_dir, log_dir, result
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def test_dual_long_run_success_generates_summary(self):
        temp_dir, log_dir, result = self.run_dual_long_run(emit_rejoin=False)
        try:
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

            result_md = os.path.join(log_dir, 'token_results.md')
            context_file = os.path.join(log_dir, 'token_context.txt')
            samples_file = os.path.join(log_dir, 'dual_long_run_samples.tsv')

            self.assertTrue(os.path.exists(result_md))
            self.assertTrue(os.path.exists(context_file))
            self.assertTrue(os.path.exists(samples_file))

            with open(result_md, 'r') as fh:
                result_text = fh.read()
            with open(context_file, 'r') as fh:
                context_text = fh.read()
            with open(samples_file, 'r') as fh:
                samples_text = fh.read()

            self.assertIn('| dual-long-run | PASS |', result_text)
            self.assertIn('## dual-long-run 摘要', result_text)
            self.assertIn('- 目标时长: 3 秒', result_text)
            self.assertIn('- 采样文件: {path}'.format(path=samples_file), result_text)
            self.assertIn('## 正式归档要求', result_text)
            self.assertIn('tracking issue 未按固定模板回填正式结论前不得关闭', result_text)
            self.assertIn('dual_long_run_total_grants=', context_text)
            self.assertIn('dual_long_run_result=双客户端长稳场景满足固定口径', context_text)
            self.assertIn('elapsed_sec\tgrants_total', samples_text)
            self.assertIn('\tYES', samples_text)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_dual_long_run_rejoin_churn_fails(self):
        temp_dir, log_dir, result = self.run_dual_long_run(emit_rejoin=True)
        try:
            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)

            result_md = os.path.join(log_dir, 'token_results.md')
            context_file = os.path.join(log_dir, 'token_context.txt')

            self.assertTrue(os.path.exists(result_md))
            self.assertTrue(os.path.exists(context_file))

            with open(result_md, 'r') as fh:
                result_text = fh.read()
            with open(context_file, 'r') as fh:
                context_text = fh.read()

            self.assertIn('| dual-long-run | FAIL |', result_text)
            self.assertIn('rejoin 抖动', result_text)
            self.assertIn('dual_long_run_client1_rejoins=1', context_text)
            self.assertIn('dual_long_run_result=出现超门槛 rejoin 抖动', context_text)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
