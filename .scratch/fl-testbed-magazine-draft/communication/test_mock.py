"""本地编排与回收测试；不导入生产模块，不连接 socket 或 SSH。"""
import io
import json
import tempfile
from pathlib import Path
import concurrent.futures as futures
import threading
import time
import unittest
from unittest.mock import patch
import runner
import node_agent


class MockTests(unittest.TestCase):
    def exercise(self, fail_prepare=False):
        events = []
        lock = threading.Lock()
        publish = threading.Barrier(5, timeout=2)
        prepared = set()
        received = set()
        pool = futures.ThreadPoolExecutor(max_workers=5)
        class Session:
            def __init__(self, host):
                self.host = host
            def call(self, action, **params):
                def invoke():
                    with lock:
                        events.append((self.host, action))
                        if action == 'prepare':
                            prepared.add(self.host)
                            if fail_prepare and self.host == 'fl04':
                                raise RuntimeError('preflight failed')
                        else:
                            assert prepared == set(runner.HOSTS)
                        if action == 'submit':
                            assert received == set(runner.HOSTS)
                    if action == 'publish':
                        publish.wait()
                        with lock:
                            received.add(self.host)
                    return {'ok': True}
                return pool.submit(invoke)
        try:
            runner.orchestrate({h: Session(h) for h in runner.HOSTS}, lambda v: None,
                               time.monotonic() + 5, 'mock')
        finally:
            pool.shutdown()
            self.events = events

    def test_all_prepare_and_publish_overlap_then_submit(self):
        self.exercise()
        self.assertEqual(sum(a == 'publish' for h, a in self.events), 5)
        self.assertEqual(self.events[-1], ('fl00', 'verify'))

    def test_failed_prepare_stops_all_test_actions(self):
        with self.assertRaises(RuntimeError):
            self.exercise(True)
        self.assertEqual(len(self.events), 5)
        self.assertTrue(all(a == 'prepare' for h, a in self.events))

    def test_radio_request_carries_full_patch_and_targets(self):
        with patch('node_agent.socket.gethostname', return_value='fl00'):
            node = node_agent.Node()
        reply = io.BytesIO(json.dumps(dict(status='finalized', calibration=False)).encode())
        with patch.object(node, 'daemon_identity', return_value=123), patch(
                'node_agent.urllib.request.urlopen', return_value=reply) as http:
            result = node.radio_transaction('reconfigure', node_agent.PATCH)
        body = json.loads(http.call_args.args[0].data)
        self.assertEqual(body, dict(confirmed=True, target_nodes=[1, 2, 4, 5], patch=node_agent.PATCH))
        self.assertIs(result['calibration'], False)

    def test_radio_request_rejects_incorrect_calibration_flag(self):
        with patch('node_agent.socket.gethostname', return_value='fl00'):
            node = node_agent.Node()
        reply = io.BytesIO(json.dumps(dict(status='finalized', calibration=True)).encode())
        with patch.object(node, 'daemon_identity', return_value=123), patch(
                'node_agent.urllib.request.urlopen', return_value=reply):
            with self.assertRaisesRegex(RuntimeError, 'radio transaction unsuccessful'):
                node.radio_transaction('reconfigure', node_agent.PATCH)

    def test_cleanup_retains_route_when_interface_identity_changes(self):
        with patch('node_agent.socket.gethostname', return_value='fl00'):
            node = node_agent.Node()
        with tempfile.TemporaryDirectory() as directory:
            node.root = Path(directory)
            node.tun, node.ifindex = 'fl-s', 100
            node.routes = {'239.80.41.1/32': [{'dev': 'fl-s'}]}
            with patch('node_agent.run', return_value='[{"ifindex":101}]') as command:
                result = node.close()
            self.assertFalse(result['ok'])
            self.assertEqual(command.call_count, 1)
            self.assertNotIn('del', command.call_args.args[0])

    def test_unreviewed_execution_does_not_start_ssh(self):
        with patch('sys.argv', ['runner', '--execute', '--reviewed-sha', 'invalid']), patch(
                'subprocess.Popen') as spawn:
            with self.assertRaises(SystemExit) as error:
                runner.main()
            self.assertEqual(error.exception.code, 2)
            spawn.assert_not_called()

    def test_dry_run_never_starts_ssh(self):
        with patch('sys.argv', ['runner']), patch('subprocess.Popen') as spawn:
            self.assertEqual(runner.main(), 0)
            spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
