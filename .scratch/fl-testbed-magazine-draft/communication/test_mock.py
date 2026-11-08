"""本地编排与回收测试；不导入生产模块，不连接 socket 或 SSH。"""
import io
import hashlib
import json
import tempfile
import struct
import recover
from pathlib import Path
import concurrent.futures as futures
import threading
import time
import unittest
from unittest.mock import Mock, patch
import runner
import node_agent


class MockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.payload_path = Path(self.temporary.name) / 'model.fixture'
        self.payload_path.write_bytes(b'local model fixture\x00' * 23)
        self.payload = runner.payload_info(self.payload_path)
        self.manifest = dict(model_format='cifar10-resnet18-fp32-v1',
                             size_bytes=self.payload['size_bytes'], sha256=self.payload['sha256'],
                             initialization=dict(seed=17, source='本地测试fixture'))
        self.manifest_path = Path(self.temporary.name) / 'manifest.json'
        self.manifest_path.write_text(json.dumps(self.manifest))

    def node(self, host='fl00'):
        with patch('node_agent.socket.gethostname', return_value=host):
            node = node_agent.Node()
        node.payload = dict(self.payload)
        node.timing = runner.TIMING
        return node

    def uftp_fixture(self):
        paths = {}
        for name in ('uftp', 'uftpd'):
            path = Path(self.temporary.name) / name
            path.write_bytes(b'ELF fixture\x00UFTP version 5.0.3  Copyright\x00')
            paths[name] = str(path)
        return paths, {name: node_agent.sha(path) for name, path in paths.items()}

    def test_complete_eleven_board_scope_and_600_second_budgets(self):
        self.assertEqual(runner.HOSTS, [f'fl{n:02d}' for n in range(11)])
        self.assertEqual(runner.NODES, list(range(1, 11)))
        self.assertEqual(node_agent.NODES, runner.NODES)
        self.assertEqual(self.node('fl03').node, 3)
        timing = runner.TIMING
        self.assertEqual(timing['io_timeout'], 600)
        self.assertEqual(timing['orchestration_timeout'], 2 * 600 + 90)
        self.assertGreaterEqual(timing['orchestration_timeout'] - timing['close_restore_reserve'], 1200)
        self.assertEqual(timing['node_timeout'], timing['orchestration_timeout'] + 15)
        self.assertEqual(timing['orchestration_timeout'] + timing['recovery_budget'], 1350)

    def test_each_transfer_phase_records_dispatch_before_rpc(self):
        records, calls = [], []
        class Session:
            def call(self, action, **params):
                starts = [record for record in records if record.get('event') == 'dispatch_start']
                self_test.assertEqual(starts[-1]['phase'], action)
                self_test.assertLessEqual(starts[-1]['dispatch_start'], time.monotonic())
                calls.append(action)
                future = futures.Future()
                future.set_result(dict(ok=True))
                return future
        self_test = self
        runner.orchestrate({host: Session() for host in runner.HOSTS}, records.append,
                           time.monotonic() + 1, 'dispatch-test', self.payload)
        starts = [record for record in records if record.get('event') == 'dispatch_start']
        self.assertEqual([record['phase'] for record in starts], [
            'prepare', 'acquire_radio', 'verify_radio', 'start_receive', 'publish', 'submit', 'verify'])
        self.assertEqual(len(calls), 5 * 11 + 2)

    def test_upload_scanner_real_files_missing_cleanup_and_isolation(self):
        root = Path(self.temporary.name) / 'run'
        updates = root / 'role/rounds/round-1/updates'
        (updates / '3').mkdir(parents=True)
        partial = updates / '3/.update.bin.partial'
        partial.write_bytes(b'a' * 17)
        (updates / '4').mkdir()
        (updates / '4/update.bin').write_bytes(b'b' * 31)
        outside = Path(self.temporary.name) / 'outside'
        outside.mkdir()
        (outside / 'update.bin').write_bytes(b'outside')
        (updates / '5').symlink_to(outside, target_is_directory=True)
        (updates / '3/.update.bin.link').symlink_to(outside / 'update.bin')
        (updates / '3/ignored').write_bytes(b'ignored')
        snapshot = runner.scan_upload_progress(root)
        nodes = {record['node']: record for record in snapshot['nodes']}
        self.assertEqual(nodes[3]['bytes'], 17)
        self.assertFalse(nodes[3]['committed'])
        self.assertEqual(nodes[4]['bytes'], 31)
        self.assertTrue(nodes[4]['committed'])
        for node in (1, 5):
            self.assertIsNone(nodes[node]['bytes'])
            self.assertFalse(nodes[node]['observed'])
        partial.unlink()
        cleaned = {record['node']: record for record in runner.scan_upload_progress(root)['nodes']}
        self.assertIsNone(cleaned[3]['bytes'])
        self.assertIn('cleanup', snapshot['note'])
        self.assertEqual(runner.scan_upload_progress(root / 'missing')['nodes'], [])

    def test_upload_sampling_command_is_bounded_and_only_local_run_root(self):
        records = []
        reply = Mock(stdout=json.dumps(dict(nodes=[])))
        with patch('runner.subprocess.run', return_value=reply) as run:
            runner.sample_upload_progress('sample-1', records.append)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:3], ['sudo', '-n', '/usr/bin/python3'])
        self.assertEqual(argv[-1], '/srv/wfb-ng-fl/comm-sample-1')
        self.assertEqual(run.call_args.kwargs['timeout'], 3)
        # 执行实际发送的 scanner 源码；测试替换根目录且不执行 sudo。
        with patch('sys.argv', ['scanner', self.temporary.name]), patch(
                'sys.stdout', new_callable=io.StringIO) as output:
            exec(compile(argv[-2], '<upload-scanner>', 'exec'), {})
        self.assertEqual(json.loads(output.getvalue())['nodes'], [])
        self.assertEqual(records[0]['phase'], 'upload_progress')
        with patch('runner.subprocess.run', side_effect=TimeoutError('sample timeout')):
            runner.sample_upload_progress('sample-1', records.append)
        self.assertFalse(records[-1]['ok'])
        with patch('runner.subprocess.run') as run:
            with self.assertRaises(ValueError):
                runner.sample_upload_progress('../outside', records.append)
            run.assert_not_called()

    def test_submit_wait_samples_every_five_seconds_without_threads(self):
        future = futures.Future()
        clock = [0.0]
        def wait(pending, timeout, return_when):
            self.assertEqual(return_when, futures.FIRST_COMPLETED)
            self.assertEqual(timeout, 5)
            clock[0] += timeout
            if clock[0] == 15:
                future.set_result(dict(ok=True))
                return {future}, set()
            return set(), {future}
        records = []
        with patch('runner.time.monotonic', side_effect=lambda: clock[0]), patch(
                'runner.futures.wait', side_effect=wait), patch(
                'runner.sample_upload_progress') as sample:
            self.assertEqual(list(runner.wait_submit({future: 'fl00'}, 600, 'wait-test', records.append)), [future])
        self.assertEqual(sample.call_count, 2)
        self.assertTrue(all(call.kwargs['timeout'] == 3 for call in sample.call_args_list))

    def test_submit_wait_keeps_absolute_deadline(self):
        future = futures.Future()
        clock = [0.0]
        def wait(pending, timeout, return_when):
            clock[0] += timeout
            return set(), set(pending)
        with patch('runner.time.monotonic', side_effect=lambda: clock[0]), patch(
                'runner.futures.wait', side_effect=wait), patch(
                'runner.sample_upload_progress') as sample:
            with self.assertRaisesRegex(TimeoutError, 'submit deadline'):
                list(runner.wait_submit({future: 'fl00'}, 2, 'wait-test', lambda value: None))
        sample.assert_not_called()

    def test_node_prepare_accepts_expanded_radio_patch_before_payload_gate(self):
        for downlink in (3, 4):
            for uplink in (4, 6):
                for rate in (1, 26000):
                    node = self.node('fl03')
                    with patch('node_agent.signal.setitimer'):
                        with self.assertRaisesRegex(RuntimeError, 'invalid payload size'):
                            node.prepare('radio-test', {}, runner.TIMING,
                                dict(downlink_mcs=downlink, uplink_mcs=uplink, uftp_rate_kbps=rate))

    def test_radio_cli_expanded_mcs_and_positive_rate(self):
        argv = ['runner', '--payload', str(self.payload_path), '--model-manifest', str(self.manifest_path)]
        for rate in (1, 26000):
            with patch('sys.argv', argv + ['--downlink-mcs', '4', '--uplink-mcs', '6',
                       '--uftp-rate-kbps', str(rate)]), patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(runner.main(), 0)
            radio = json.loads(output.getvalue())['radio_patch']
            self.assertEqual((radio['downlink_mcs'], radio['uplink_mcs'], radio['uftp_rate_kbps']), (4, 6, rate))
        for option, value in [('--downlink-mcs', '6'), ('--uplink-mcs', '3'),
                              ('--uftp-rate-kbps', '0'), ('--uftp-rate-kbps', '26001')]:
            with patch('sys.argv', argv + [option, value]), patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    runner.main()

    def test_native_candidate_metadata_defaults_and_overrides(self):
        argv = ['runner', '--payload', str(self.payload_path), '--model-manifest', str(self.manifest_path)]
        for flags, expected in [([], dict(fec_k=8, fec_n=14, grant_duration_ms=15, guard_interval_ms=5)),
                (['--fec-n', '12', '--grant-duration-ms', '15', '--guard-interval-ms', '0'],
                 dict(fec_k=8, fec_n=12, grant_duration_ms=15, guard_interval_ms=0))]:
            with patch('sys.argv', argv + flags), patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(runner.main(), 0)
            self.assertEqual(json.loads(output.getvalue())['expected_native'], expected)

    def test_resident_candidate_argv_requires_fec_everywhere_and_server_schedule_only(self):
        expected = dict(fec_k=8, fec_n=12, grant_duration_ms=10, guard_interval_ms=10)
        fec = ['--fec-k', '8', '--fec-n', '12']
        for host in runner.HOSTS:
            node = self.node(host)
            node.expected_native = expected
            argv = fec + (['--grant-duration-ms', '10', '--guard-interval-ms', '10'] if host == 'fl00' else [])
            self.assertEqual(node.verify_native_parameters(argv)['fec_n'], 12)
            with self.assertRaisesRegex(RuntimeError, 'parameters mismatch'):
                node.verify_native_parameters([('14' if value == '12' else value) for value in argv])
        server = self.node()
        server.expected_native = expected
        with self.assertRaisesRegex(RuntimeError, 'missing/duplicate option'):
            server.verify_native_parameters(fec)
        for key in ('--grant-duration-ms', '--guard-interval-ms'):
            argv = fec + ['--grant-duration-ms', '10', '--guard-interval-ms', '10']
            argv[argv.index(key) + 1] = '20'
            with self.assertRaisesRegex(RuntimeError, 'parameters mismatch'):
                server.verify_native_parameters(argv)

    def test_get_fec_checks_command_peer_identity_return_code_and_active_values(self):
        node = self.node('fl03')
        node.control_port = 8000
        node.expected_native = dict(fec_k=8, fec_n=12)
        sock = Mock()
        with patch('node_agent.socket.socket') as socket:
            socket.return_value.__enter__.return_value = sock
            sock.recvfrom.return_value = (struct.pack('!II2B', 0x434f4d4d, 0, 8, 12), ('127.0.0.1', 8000))
            self.assertEqual(node.read_fec(), dict(k=8, n=12))
            sock.sendto.assert_called_with(struct.pack('!IB', 0x434f4d4d, 3), ('127.0.0.1', 8000))
            for ident, rc, k, n, port in [(1, 0, 8, 12, 8000), (0x434f4d4d, 1, 8, 12, 8000),
                    (0x434f4d4d, 0, 8, 14, 8000), (0x434f4d4d, 0, 8, 12, 8001)]:
                sock.recvfrom.return_value = (struct.pack('!II2B', ident, rc, k, n), ('127.0.0.1', port))
                with self.assertRaisesRegex(RuntimeError, 'GET FEC mismatch'):
                    node.read_fec()

    def test_acquire_routes_ht40_mcs4_12m_to_calibration_and_18m_to_ordinary(self):
        for bandwidth, mcs, rate, endpoint in [(40, 4, 12000, 'calibrate'),
                (40, 4, 18000, 'reconfigure'), (40, 4, 26000, 'reconfigure'),
                (40, 3, 12000, 'reconfigure'), (20, 3, 6000, 'calibrate')]:
            node = self.node()
            node.prepared = True
            node.original = {}
            node.patch = dict(node_agent.PATCH, bandwidth_mhz=bandwidth, downlink_mcs=mcs, uftp_rate_kbps=rate)
            with patch.object(node, 'registry_gate'), patch.object(node, 'persist_cleanup'), patch.object(
                    node, 'radio_transaction', return_value=dict(ok=True)) as transaction:
                node.acquire_radio()
            transaction.assert_called_once_with(endpoint, node.patch)

    def test_standard_release_checks_current_process_and_ignores_historical_loaded(self):
        base = Path('/usr/local/lib/python3.10/dist-packages/wfb_ng/fl')
        release = Path(node_agent.RELEASE_ROOT)
        for host, role in [('fl00', 'server'), ('fl03', 'client')]:
            node = self.node(host)
            module = 'wfb_ng.fl.' + role + '_daemon'
            argv = ['/usr/bin/python3', '-I', '-B', '-m', module, '--config', '/etc/node.json']
            manifest = dict(commit=node_agent.COMMIT, native_sha=node_agent.NATIVE_SHA,
                installed=str(base), files={role + '_daemon.py': 'digest', 'role.py': 'digest'})
            def read(path, *args, **kwargs):
                if path == release / 'actualargs.json':
                    return json.dumps(argv)
                if path == release / 'manifest.json':
                    return json.dumps(manifest)
                if path == base / 'build_identity.json':
                    return json.dumps(dict(commit=node_agent.COMMIT))
                raise AssertionError('unexpected identity file: ' + str(path))
            def resolve(path, *args, **kwargs):
                return Path('/usr/bin/python3') if path == Path('/proc/123/exe') else path
            resolved = {module: str(base / (role + '_daemon.py')), 'wfb_ng.fl.role': str(base / 'role.py')}
            with patch.object(Path, 'read_text', read), patch.object(Path, 'resolve', resolve), patch(
                    'node_agent.verify_source_files') as sources, patch('node_agent.sha', return_value='digest'), patch(
                    'node_agent.run', return_value=json.dumps(resolved)) as command:
                result = node.verify_standard_release(123, argv, str(release))
                self.assertEqual(result['pid'], 123)
                sources.assert_called_once_with(base, release / 'source/wfb_ng/fl', manifest['files'])
                self.assertEqual(command.call_args.args[0][:4], ['/usr/bin/python3', '-I', '-B', '-c'])
                with self.assertRaisesRegex(RuntimeError, 'standard isolated daemon entry'):
                    node.verify_standard_release(123, ['/usr/bin/python3', '-I', '-B', str(release / 'daemon.py')], str(release))
                command.return_value = json.dumps({**resolved, module: '/tmp/daemon.py'})
                with self.assertRaisesRegex(RuntimeError, 'isolated module origin'):
                    node.verify_standard_release(123, argv, str(release))
                command.return_value = json.dumps(resolved)
                manifest['commit'] = 'old-commit'
                with self.assertRaisesRegex(RuntimeError, 'release identity'):
                    node.verify_standard_release(123, argv, str(release))

    def test_uftp_pins_match_fl00_503(self):
        self.assertEqual(node_agent.UFTP_SHA256, {
            'uftp': '71d9c15497b3bd4286a2c60c9d37871a50b7032793eae8d4bf7b3b8977e015e3',
            'uftpd': '0d7a31c6cbf783ad5ecced0bdce6705c30530a258fdf605d555c748827aa85b9',
        })

    def test_uftp_identity_records_actual_which_paths_hashes_and_binary_versions(self):
        paths, digests = self.uftp_fixture()
        # PATH 可指向符号链接，记录 which 返回值及其最终目标。
        link = Path(self.temporary.name) / 'path-uftp'
        link.symlink_to(paths['uftp'])
        paths['uftp'] = str(link)
        with patch('node_agent.shutil.which', side_effect=paths.get) as which, patch.dict(
                node_agent.UFTP_SHA256, digests), patch('node_agent.subprocess.run') as command:
            tools = node_agent.verify_uftp_tools()
        self.assertEqual([call.args for call in which.call_args_list], [('uftp',), ('uftpd',)])
        command.assert_not_called()
        for name, identity in tools.items():
            self.assertEqual(identity, dict(path=paths[name],
                resolved_path=str(Path(paths[name]).resolve()), sha256=digests[name],
                version='5.0.3', version_source='binary_banner'))

    def test_uftp_identity_rejects_missing_or_changed_tools_including_same_version(self):
        for name in ('uftp', 'uftpd'):
            for failure in ('missing', 'changed'):
                with self.subTest(tool=name, failure=failure):
                    paths, digests = self.uftp_fixture()
                    if failure == 'missing':
                        paths[name] = None
                    else:
                        # 版本仍为 5.0.3；只比较版本会漏过这个变化。
                        with Path(paths[name]).open('ab') as stream:
                            stream.write(b'changed binary')
                    with patch('node_agent.shutil.which', side_effect=paths.get), patch.dict(
                            node_agent.UFTP_SHA256, digests):
                        with self.assertRaisesRegex(RuntimeError,
                                ('missing UFTP executable ' if failure == 'missing'
                                 else 'UFTP SHA mismatch ') + name):
                            node_agent.verify_uftp_tools()

    def test_uftp_identity_does_not_require_version_probe_output_or_success(self):
        paths, digests = self.uftp_fixture()
        # usage 长度及是否存在版本 banner 均不是准入条件，SHA 严格匹配即可。
        Path(paths['uftpd']).write_bytes(b'usage\n' * 20000)
        digests['uftpd'] = node_agent.sha(paths['uftpd'])
        with patch('node_agent.shutil.which', side_effect=paths.get), patch.dict(
                node_agent.UFTP_SHA256, digests), patch('node_agent.subprocess.run',
                side_effect=AssertionError('must not execute version probes')):
            tools = node_agent.verify_uftp_tools()
        self.assertIsNone(tools['uftpd']['version'])
        self.assertEqual(tools['uftpd']['sha256'], digests['uftpd'])

    def test_prepare_rejects_uftp_hash_before_frozen_release_checks(self):
        node = self.node('fl10')
        paths, digests = self.uftp_fixture()
        Path(paths['uftpd']).write_bytes(b'UFTP version 4.10.2\x00wrong executable')
        root = Path(self.temporary.name)
        real_path = Path

        def local_path(*args):
            return root if args == ('/srv/wfb-ng-fl',) else real_path(*args)

        with patch('node_agent.Path', side_effect=local_path), patch(
                'node_agent.os.geteuid', return_value=0), patch(
                'node_agent.signal.setitimer'), patch('node_agent.fcntl.flock'), patch(
                'builtins.open', return_value=io.StringIO()), patch(
                'node_agent.shutil.which', side_effect=paths.get), patch.dict(
                node_agent.UFTP_SHA256, digests), patch('node_agent.run') as command:
            with self.assertRaisesRegex(RuntimeError, 'UFTP SHA mismatch uftpd'):
                node.prepare('hashreject', {key: self.payload[key]
                    for key in ('size_bytes', 'sha256')}, runner.TIMING)
        self.assertFalse(node.prepared)
        command.assert_not_called()

    def exercise(self, fail_prepare=False):
        events = []
        lock = threading.Lock()
        publish = threading.Barrier(len(runner.HOSTS), timeout=2)
        prepared = set()
        received = set()
        pool = futures.ThreadPoolExecutor(max_workers=len(runner.HOSTS))
        self_test = self
        class Session:
            def __init__(self, host):
                self.host = host
            def call(self, action, **params):
                def invoke():
                    with lock:
                        events.append((self.host, action))
                        if action == 'prepare':
                            assert params['timing'] == runner.TIMING
                            assert params['expected_native'] == dict(fec_k=8, fec_n=14, grant_duration_ms=15, guard_interval_ms=5)
                            assert params['release_root'] == runner.RELEASE_ROOT
                            expected = dict(self_test.payload)
                            if self.host != 'fl00':
                                expected.pop('path')
                            assert params['payload'] == expected
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
                               time.monotonic() + 5, 'mock', self.payload)
        finally:
            pool.shutdown()
            self.events = events

    def test_all_prepare_and_publish_overlap_then_submit(self):
        self.exercise()
        self.assertEqual(sum(a == 'publish' for h, a in self.events), len(runner.HOSTS))
        self.assertEqual(self.events[-1], ('fl00', 'verify'))

    def test_failed_prepare_stops_all_test_actions(self):
        with self.assertRaises(RuntimeError):
            self.exercise(True)
        self.assertLessEqual(len(self.events), len(runner.HOSTS))
        self.assertIn(('fl04', 'prepare'), self.events)
        self.assertTrue(all(a == 'prepare' for h, a in self.events))

    def test_radio_request_carries_full_patch_and_targets(self):
        with patch('node_agent.socket.gethostname', return_value='fl00'):
            node = node_agent.Node()
        reply = io.BytesIO(json.dumps(dict(status='finalized', calibration=False)).encode())
        with patch.object(node, 'daemon_identity', return_value=123), patch(
                'node_agent.urllib.request.urlopen', return_value=reply) as http:
            result = node.radio_transaction('reconfigure', node_agent.PATCH)
        body = json.loads(http.call_args.args[0].data)
        self.assertEqual(body, dict(confirmed=True, target_nodes=list(range(1, 11)), patch=node_agent.PATCH))
        self.assertIs(result['calibration'], False)

    def test_start_receive_passes_configured_rate_to_server_role(self):
        for bandwidth, rate in ((40, 12000), (20, 6000)):
            with self.subTest(bandwidth_mhz=bandwidth, uftp_rate_kbps=rate):
                node = self.node()
                node.root = Path(self.temporary.name)
                node.tun, node.ip = 'fl-s', '10.80.0.1'
                node.prepared = node.radio_verified = True
                node.patch = dict(node_agent.PATCH, bandwidth_mhz=bandwidth,
                                  uftp_rate_kbps=rate)
                node.role_module = Mock()
                node.role_module.ServerRole.return_value.transport.uftp_rate_kbps = rate
                routes = {}

                def route_command(argv):
                    if len(argv) == 6 and argv[:5] == ['ip', '-j', 'route', 'show', 'exact']:
                        self.assertIn(argv[-1], node_agent.GROUPS)
                        return json.dumps(routes.get(argv[-1], []))
                    self.assertEqual(argv, ['ip', 'route', 'add', argv[3],
                                            'dev', node.tun, 'proto', '186'])
                    self.assertIn(argv[3], node_agent.GROUPS)
                    routes[argv[3]] = [dict(dst=argv[3], dev=node.tun,
                                           protocol='186', scope='link', flags=[])]
                    return ''

                with patch('node_agent.run', side_effect=route_command):
                    result = node.start_receive()

                node.role_module.ServerRole.assert_called_once_with(
                    participant_node_id=node_agent.NODES,
                    participant_uftp_uid=node_agent.NODES,
                    server_uftp_uid=100, http_port=18080, http_host=node.ip,
                    uftp_private_multicast_host='239.80.42.1',
                    uftp_rate_kbps=rate, work_dir=str(node.root / 'role'),
                    uftp_port=1144, max_update_size_bytes=self.payload['size_bytes'],
                    uftp_bind_host=node.ip, uftp_multicast_host='239.80.41.1',
                    io_timeout=runner.TIMING['io_timeout'])
                node.role_module.ClientRole.assert_not_called()
                node.role.start.assert_called_once_with()
                self.assertIs(node.runtime, node.role.start.return_value)
                self.assertTrue(node.started)
                self.assertEqual(result['event'], 'receive_started')
                self.assertEqual(result['actual_uftp_rate_kbps'], rate)
                self.assertEqual(set(routes), set(node_agent.GROUPS))
                self.assertEqual(node.routes, routes)

    def test_start_receive_rejects_actual_server_rate_mismatch(self):
        for rate in (12000, 6000):
            with self.subTest(uftp_rate_kbps=rate):
                node = self.node()
                node.root = Path(self.temporary.name)
                node.tun, node.ip = 'fl-s', '10.80.0.1'
                node.prepared = node.radio_verified = True
                node.patch = dict(node_agent.PATCH, uftp_rate_kbps=rate)
                node.role_module = Mock()
                node.role_module.ServerRole.return_value.transport.uftp_rate_kbps = 15000
                with patch('node_agent.run', return_value=json.dumps([dict(dev=node.tun)])), patch.object(
                        node, 'fact') as fact:
                    with self.assertRaisesRegex(RuntimeError, 'actual UFTP rate mismatch'):
                        node.start_receive()
                self.assertEqual(node.role_module.ServerRole.call_args.kwargs['uftp_rate_kbps'], rate)
                fact.assert_not_called()

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
        with patch('sys.argv', ['runner', '--payload', str(self.payload_path), '--model-manifest', str(self.manifest_path), '--execute', '--reviewed-sha', 'invalid']), patch(
                'subprocess.Popen') as spawn:
            with self.assertRaises(SystemExit) as error:
                runner.main()
            self.assertEqual(error.exception.code, 2)
            spawn.assert_not_called()

    def test_dry_run_never_starts_ssh(self):
        with patch('sys.argv', ['runner', '--payload', str(self.payload_path), '--model-manifest', str(self.manifest_path)]), patch('subprocess.Popen') as spawn:
            with patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(runner.main(), 0)
            plan = json.loads(output.getvalue())
            self.assertEqual(plan['payload'], self.payload)
            self.assertEqual(plan['kind'], 'model-equivalent-communication')
            self.assertTrue(plan['model_manifest_matched'])
            self.assertFalse(plan['model_semantics_validated_by_runner'])
            self.assertNotIn('formal_resnet_equivalent', plan)
            self.assertEqual(plan['model_manifest'], dict(path=str(self.manifest_path), content=self.manifest))
            self.assertEqual(plan['io_timeout'], 600)
            self.assertEqual(plan['deadline'] + plan['recovery_budget'], 1350)
            spawn.assert_not_called()

    def test_unit_uses_same_node_deadline_as_prepare(self):
        with patch('runner.subprocess.Popen', return_value=Mock()) as spawn, patch(
                'runner.threading.Thread'):
            session = runner.Session('fl00', '# 本地 mock', Path(self.temporary.name),
                                     time.monotonic() + runner.TIMING['orchestration_timeout'])
        self.addCleanup(session.stderr.close)
        self.addCleanup(session.worker.shutdown)
        argv = spawn.call_args.args[0]
        self.assertIn('--property=RuntimeMaxSec=' + str(runner.TIMING['node_timeout']), argv)
        self.assertIn('--property=TimeoutStopSec=' + str(runner.TIMING['unit_stop_timeout']), argv)

    def test_dispose_unblocks_actual_rpc_worker_without_deadline_wait(self):
        process = Mock()
        sent = threading.Event()
        process.stdin.flush.side_effect = sent.set
        with patch('runner.subprocess.Popen', return_value=process), patch('runner.threading.Thread'):
            session = runner.Session('fl00', '# mock', Path(self.temporary.name),
                                     time.monotonic() + 690)
        future = session.call('publish')
        self.assertTrue(sent.wait(timeout=1))
        try:
            session.dispose()
            with self.assertRaisesRegex(EOFError, 'session disposed'):
                future.result(timeout=1)
        finally:
            session.worker.shutdown(wait=True, cancel_futures=True)
            session.stderr.close()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_cli_requires_payload_before_any_process(self):
        with patch('sys.argv', ['runner']), patch('subprocess.Popen') as spawn:
            with self.assertRaises(SystemExit) as error:
                runner.main()
            self.assertEqual(error.exception.code, 2)
            spawn.assert_not_called()

    def test_missing_target_rejected_before_prepare(self):
        with self.assertRaisesRegex(RuntimeError, '十一节点会话不齐'):
            runner.orchestrate({h: None for h in runner.HOSTS if h != 'fl04'},
                               lambda v: None, time.monotonic() + 1, 'mock', self.payload)

    def test_streamed_metadata_and_integrity_reject_wrong_sha_or_size(self):
        self.assertEqual(self.payload['sha256'],
                         hashlib.sha256(self.payload_path.read_bytes()).hexdigest())
        node = self.node()
        self.assertEqual(node.check_file(self.payload_path)['size_bytes'], self.payload_path.stat().st_size)
        node.payload['sha256'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'integrity mismatch'):
            node.check_file(self.payload_path)
        node.payload = dict(self.payload, size_bytes=self.payload['size_bytes'] + 1)
        with self.assertRaisesRegex(RuntimeError, 'integrity mismatch'):
            node.check_file(self.payload_path)

    def test_payload_must_exist_and_be_nonempty(self):
        with self.assertRaises(FileNotFoundError):
            runner.payload_info(self.payload_path.with_name('missing'))
        self.payload_path.write_bytes(b'')
        with self.assertRaises(ValueError):
            runner.payload_info(self.payload_path)

    def test_server_copies_verified_real_bytes_before_publish(self):
        node = self.node()
        node.root = Path(self.temporary.name) / 'work'
        node.root.mkdir()
        node.started = True
        node.runtime = Mock(round_id=1)
        node.publish()
        target = node.root / 'payload.bin'
        self.assertEqual(target.read_bytes(), self.payload_path.read_bytes())
        node.runtime.publish_model.assert_called_once_with(str(target))
        # 保持相同大小但篡改内容，发布必须被本机校验拦截。
        self.payload_path.write_bytes(b'x' * self.payload['size_bytes'])
        node.runtime.reset_mock()
        with self.assertRaisesRegex(RuntimeError, 'integrity mismatch'):
            node.publish()
        node.runtime.publish_model.assert_not_called()

    def test_client_resubmits_received_file_without_generation(self):
        node = self.node('fl01')
        node.payload.pop('path')
        node.started = True
        node.received = self.payload_path
        node.runtime = Mock()
        node.submit()
        node.runtime.submit_update.assert_called_once_with(self.payload_path)

    def test_server_rejects_missing_update(self):
        node = self.node()
        node.started = True
        node.runtime = Mock()
        node.runtime.wait_for_updates.return_value = {1: 'a', 2: 'b', 4: 'c'}
        with self.assertRaisesRegex(RuntimeError, 'missing updates'):
            node.submit()
        node.runtime.wait_for_updates.assert_called_once_with(min_updates=10, timeout=600)
        node.runtime.wait_for_updates.return_value = {n: self.payload_path for n in runner.NODES}
        node.submit()
        self.assertEqual(set(node.verify()['files']), {str(n) for n in runner.NODES})

    def test_fixed_complete_topology_and_registry(self):
        expected = list(range(1, 11))
        self.assertEqual(runner.NODES, expected)
        self.assertEqual(node_agent.NODES, expected)
        self.assertEqual(runner.HOSTS, ['fl00'] + [f'fl{n:02d}' for n in expected])
        for host in runner.HOSTS:
            self.assertEqual(self.node(host).node, int(host[2:]))
        node = self.node()
        status = dict(active_job=None, server_state='IDLE', nodes={str(n): dict(
            readiness='READY', reported_state='IDLE', error_code=None) for n in expected})
        with patch('node_agent.get_json', return_value=status):
            node.registry_gate()
            status['nodes'].pop('10')
            with self.assertRaisesRegex(RuntimeError, 'online target set mismatch'):
                node.registry_gate()
            status['nodes']['11'] = dict(readiness='READY')
            with self.assertRaisesRegex(RuntimeError, 'online target set mismatch'):
                node.registry_gate()

    def test_prepare_source_identity_with_build_identity_rejects_extra_missing_and_tampering(self):
        installed = Path(self.temporary.name) / 'installed'
        source = Path(self.temporary.name) / 'source'
        installed.mkdir()
        source.mkdir()
        files = {'__init__.py': hashlib.sha256(b'# frozen').hexdigest(),
                 'role.py': hashlib.sha256(b'# frozen').hexdigest()}
        for directory in (installed, source):
            for name in files:
                (directory / name).write_bytes(b'# frozen')
        identity = json.dumps(dict(commit=node_agent.COMMIT, dirty=False)).encode()
        (installed / 'build_identity.json').write_bytes(identity)
        files['build_identity.json'] = hashlib.sha256(identity).hexdigest()
        node_agent.verify_source_files(installed, source, files)
        (installed / 'build_identity.json').write_bytes(b'{}')
        with self.assertRaisesRegex(RuntimeError, 'installed manifest mismatch build_identity.json'):
            node_agent.verify_source_files(installed, source, files)
        (installed / 'build_identity.json').unlink()
        with self.assertRaises(FileNotFoundError):
            node_agent.verify_source_files(installed, source, files)
        (installed / 'build_identity.json').write_bytes(identity)
        for directory in (installed, source):
            with self.subTest(directory=str(directory)):
                extra = directory / 'extra.py'
                extra.write_bytes(b'# extra')
                with self.assertRaisesRegex(RuntimeError, 'file set mismatch'):
                    node_agent.verify_source_files(installed, source, files)
                extra.unlink()
                missing = directory / 'role.py'
                missing.unlink()
                with self.assertRaisesRegex(RuntimeError, 'file set mismatch'):
                    node_agent.verify_source_files(installed, source, files)
                missing.write_bytes(b'# altered')
                with self.assertRaisesRegex(RuntimeError, 'manifest mismatch|source mismatch'):
                    node_agent.verify_source_files(installed, source, files)
                missing.write_bytes(b'# frozen')
        for name in ('../role.py', '..', '.', '/role.py', 'static/../../role.py',
                     'static/../role.py', '', 'static//role.py'):
            with self.subTest(manifest_name=name), self.assertRaisesRegex(RuntimeError, 'safe relative paths'):
                node_agent.verify_source_files(installed, source, dict(files, **{name: '0' * 64}))
        with self.assertRaisesRegex(RuntimeError, 'file set mismatch'):
            node_agent.verify_source_files(installed, source, dict(files, **{'extra.py': '0' * 64}))

    def test_prepare_source_identity_accepts_safe_nested_assets_and_checks_all_hashes(self):
        installed = Path(self.temporary.name) / 'installed'
        source = Path(self.temporary.name) / 'source'
        contents = {'role.py': b'# frozen', 'static/index.html': b'<html></html>',
                    'static/css/app.css': b'body {}', 'static/js/app.js': b'boot();',
                    'nested/helper.py': b'# nested frozen'}
        files = {name: hashlib.sha256(content).hexdigest()
                 for name, content in contents.items()}
        for directory in (installed, source):
            for name, content in contents.items():
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        node_agent.verify_source_files(installed, source, files)
        for directory in (installed, source):
            for name, content in contents.items():
                with self.subTest(directory=str(directory), name=name):
                    path = directory / name
                    path.write_bytes(b'tampered')
                    with self.assertRaisesRegex(RuntimeError, 'manifest mismatch|source mismatch'):
                        node_agent.verify_source_files(installed, source, files)
                    path.write_bytes(content)
                    if '/' in name:
                        path.unlink()
                        with self.assertRaises(FileNotFoundError):
                            node_agent.verify_source_files(installed, source, files)
                        path.write_bytes(content)

    def test_smoke_dry_run_generates_exact_1mib_without_process(self):
        argv = ['runner', '--smoke', '--output', self.temporary.name, '--run-id', 'smoke-test']
        with patch('sys.argv', argv), patch('subprocess.Popen') as spawn, patch(
                'subprocess.run') as run, patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(runner.main(), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan['kind'], 'synthetic-smoke-communication')
        self.assertEqual(plan['payload_mode'], 'smoke')
        self.assertTrue(plan['generated_payload'])
        self.assertIsNone(plan['model_manifest'])
        self.assertFalse(plan['model_manifest_matched'])
        self.assertEqual(plan['nodes'], node_agent.NODES)
        self.assertEqual(plan['hosts'], runner.HOSTS)
        self.assertEqual(Path(plan['payload']['path']).read_bytes(), bytes(1024 * 1024))
        self.assertEqual(plan['bytes'], 1024 * 1024)
        self.assertEqual(plan['radio_patch']['channel'], 157)
        self.assertEqual(plan['radio_patch']['uftp_rate_kbps'], 12000)
        spawn.assert_not_called()
        run.assert_not_called()

    def exercise_publish_failure(self, cleanup_result=True):
        run_id = "fail-fast-" + str(cleanup_result)
        events, records, blocked = [], [], []
        class Session:
            def __init__(self, host, *args):
                self.host = host
            def call(self, action, **params):
                events.append((self.host, action))
                future = futures.Future()
                if action == 'publish':
                    if self.host == 'fl10':
                        future.set_exception(RuntimeError('publish failed'))
                    else:
                        future.set_running_or_notify_cancel()
                        blocked.append(future)
                else:
                    future.set_result(dict(ok=True))
                return future
            def dispose(self):
                events.append((self.host, 'dispose'))
        reply = Mock(returncode=0, stdout=json.dumps(dict(ok=cleanup_result)), stderr='')
        def independent(argv, **kwargs):
            self.assertEqual(sum(action == 'dispose' for _, action in events), 11)
            if '--restore' in argv:
                self.assertEqual(sum(action == 'independent_stop' for _, action in events), 11)
                events.append(('fl00', 'independent_restore'))
            else:
                events.append(('owned-unit', 'independent_stop'))
            return reply
        argv = ['runner', '--payload', str(self.payload_path), '--model-manifest',
                str(self.manifest_path), '--execute', '--reviewed-sha', 'reviewed',
                '--output', self.temporary.name, '--run-id', run_id]
        started = time.monotonic()
        with patch('sys.argv', argv), patch('runner.bundle_sha', return_value='reviewed'), patch(
                'runner.Session', Session), patch('runner.subprocess.run', side_effect=independent) as run, patch(
                'sys.stdout', new_callable=io.StringIO):
            self.assertEqual(runner.main(), 1)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(len(blocked), 10)
        self.assertTrue(all(not future.done() for future in blocked))
        self.assertFalse(any(action in ('submit', 'verify', 'close', 'release_radio') for _, action in events))
        self.assertEqual(sum(action == 'dispose' for _, action in events), 11)
        self.assertEqual(run.call_count, 12 if cleanup_result is True else 11)
        if cleanup_result is not True:
            self.assertFalse(any(action == 'independent_restore' for _, action in events))
        records = [json.loads(line) for line in (Path(self.temporary.name) / run_id / 'progress.jsonl').read_text().splitlines()]
        self.assertEqual(sum(r['phase'] == 'independent_cleanup' for r in records), 11)
        self.assertFalse(records[-1]['transfer_verified'])
        self.assertFalse(records[-1]['restore_ok'])

    def test_publish_failure_exits_and_recovers_without_waiting_for_peers(self):
        self.exercise_publish_failure()

    def test_independent_cleanup_requires_explicit_true_before_restore(self):
        for value in (False, 1):
            with self.subTest(cleanup_ok=value):
                self.exercise_publish_failure(cleanup_result=value)

    def test_independent_recovery_stops_only_owned_unit_on_every_host(self):
        for host in runner.HOSTS:
            with self.subTest(host=host), patch('sys.argv', ['recover', 'local-test']), patch(
                    'recover.socket.gethostname', return_value=host), patch(
                    'recover.os.geteuid', return_value=0), patch.object(Path, 'exists', return_value=False), patch.object(
                    Path, 'is_dir', return_value=False), patch('recover.run') as command:
                command.side_effect = [Mock(stdout='', stderr=''), Mock(stdout='inactive')]
                self.assertTrue(recover.main()['ok'])
                unit = 'comm-smoke-local-test-' + host + '.service'
                self.assertEqual([call.args[0] for call in command.call_args_list], [
                    ['systemctl', 'stop', unit], ['systemctl', 'is-active', unit]])

    def test_get_radio_asserts_long_gi(self):
        node = self.node()
        node.interface, node.control_port = 'wlan-test', 8000
        node.patch = dict(node_agent.PATCH)
        iw = 'channel 157 width: 40 MHz txpower -18.00 dBm type monitor'
        sock = Mock()
        with patch('node_agent.run', return_value=iw), patch.object(
                Path, 'resolve', return_value=Path('/drivers/rtl88xxau_wfb')), patch(
                'node_agent.socket.socket') as socket:
            socket.return_value.__enter__.return_value = sock
            for short_gi in (0, 1):
                sock.recvfrom.return_value = (struct.pack('!II7B', 0x434f4d4d, 0,
                    0, 0, short_gi, 40, 3, 0, 1), ('127.0.0.1', 8000))
                if short_gi:
                    with self.assertRaisesRegex(RuntimeError, 'GET radio mismatch'):
                        node.read_radio()
                else:
                    self.assertEqual(node.read_radio()['get_radio'][4], 0)

    def test_manifest_mismatch_rejected_before_ssh(self):
        for key, value in [('size_bytes', self.payload['size_bytes'] + 1),
                           ('sha256', '0' * 64), ('model_format', 'other')]:
            with self.subTest(key=key):
                self.manifest_path.write_text(json.dumps(dict(self.manifest, **{key: value})))
                argv = ['runner', '--payload', str(self.payload_path),
                        '--model-manifest', str(self.manifest_path), '--execute']
                with patch('sys.argv', argv), patch('subprocess.Popen') as spawn:
                    with self.assertRaises(SystemExit) as error:
                        runner.main()
                    self.assertEqual(error.exception.code, 2)
                    spawn.assert_not_called()

    def test_manifest_initialization_validation(self):
        for seed, source in [(True, 'x'), (-1, 'x'), (2**63, 'x'), (1.5, 'x'), (1, ''), (1, '  '), (1, 4)]:
            with self.subTest(seed=seed, source=source):
                value = dict(self.manifest, initialization=dict(seed=seed, source=source))
                self.manifest_path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    runner.model_manifest_info(self.manifest_path, self.payload)
        for seed in [0, 2**63 - 1]:
            self.manifest_path.write_text(json.dumps(dict(self.manifest, initialization=dict(seed=seed, source='x'))))
            runner.model_manifest_info(self.manifest_path, self.payload)

    def test_cli_requires_manifest(self):
        with patch('sys.argv', ['runner', '--payload', str(self.payload_path)]), patch('subprocess.Popen') as spawn:
            with self.assertRaises(SystemExit) as error:
                runner.main()
            self.assertEqual(error.exception.code, 2)
            spawn.assert_not_called()

    def test_final_audit_requires_all_five_success_and_deadline(self):
        def sessions(failed=None, pending=False):
            result = {}
            for host in runner.HOSTS:
                future = futures.Future()
                if not pending:
                    future.set_result(dict(ok=host != failed))
                result[host] = Mock()
                result[host].call.return_value = future
            return result
        records = []
        group = sessions()
        self.assertTrue(runner.audit_final(group, records.append, time.monotonic() + 1))
        for session in group.values():
            session.call.assert_called_once_with('audit_final')
        self.assertEqual(len(records), len(runner.HOSTS))
        self.assertFalse(runner.audit_final(sessions('fl04'), lambda v: None, time.monotonic() + 1))
        self.assertFalse(runner.audit_final(sessions(pending=True), lambda v: None, time.monotonic() + .01))
        self.assertFalse(runner.audit_final(sessions(), lambda v: None, time.monotonic() - 1))
        group.pop('fl05')
        self.assertFalse(runner.audit_final(group, lambda v: None, time.monotonic() + 1))

    def test_node_final_audit_reads_actual_and_rejects_clock_or_pid_drift(self):
        node = self.node()
        node.prepared = node.closed = True
        node.original_channel = 149
        node.resident = {'123': '99', '456': '99'}
        radio = dict(iw='channel 149', get_radio=[0, 0, 0, 0, 0, 40, 3, 0, 1])
        def read(path, *args, **kwargs):
            name = str(path)
            if name.startswith('/proc/'):
                return '123 (resident) ' + ' '.join(['0'] * 19 + ['99'])
            if name.endswith('/cpu/online'):
                return '0-5'
            if name.endswith('/online'):
                return '1'
            if name.endswith('/scaling_max_freq'):
                return '1497600'
            if name.endswith('/max_freq'):
                return '612000000'
            if name.endswith('/emc'):
                return '2133000000'
            if name.endswith('/mrq_rate_locked'):
                return '0'
            raise AssertionError(name)
        with patch.object(node, 'read_radio', return_value=radio) as read_radio, patch(
                'node_agent.run', return_value='NV Power Mode: 15W\n0\n'), patch.object(Path, 'read_text', read):
            result = node.audit_final()
            self.assertTrue(result['ok'])
            self.assertEqual(result['actual']['resident'], node.resident)
            read_radio.assert_called_once_with(channels=(149,), expected_bandwidth=40)
            def drift(path, *args, **kwargs):
                return '1' if str(path).endswith('/mrq_rate_locked') else read(path)
            with patch.object(Path, 'read_text', drift):
                self.assertFalse(node.audit_final()['ok'])
            node.resident['123'] = '100'
            self.assertFalse(node.audit_final()['ok'])


if __name__ == '__main__':
    unittest.main()
