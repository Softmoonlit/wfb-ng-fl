import hashlib
import json
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wfb_ng.fl.client_daemon import ClientDaemon, ClientDaemonConfig, DaemonState, JobSandbox
from wfb_ng.fl.evidence import archive_client_evidence
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.issue41_fixtures import generate_client_fixture, generate_model_fixture
from wfb_ng.fl.runtime import ClientRuntime
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter, _make_client_job


class TestClientEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = self.root / 'build_identity.json'
        self.identity.write_text(json.dumps({'schema_version': 1, 'commit': 'a' * 40}))
        self.adapter = MockNetworkAdapter(interfaces=['wlx001'])
        self.identity_patch = mock.patch(
            'wfb_ng.fl.evidence.BUILD_IDENTITY_PATH', self.identity)
        self.identity_patch.start()
        self.addCleanup(self.identity_patch.stop)

    def sandbox(self, script='pass', command=None):
        return JobSandbox(
            str(self.root / 'work'), self.adapter,
            _command_prefix=command or [sys.executable, '-c', script],
            evidence_dir=str(self.root / 'evidence'))

    def manifest(self, job_id='job_001'):
        target = self.root / 'evidence' / job_id
        return target, json.loads((target / 'evidence_manifest.json').read_text())

    def test_completed_job_archives_small_evidence_before_removing_sandbox(self):
        sandbox = self.sandbox()
        sandbox.start(_make_client_job(), 'wlx001')
        work = self.root / 'work' / 'job_job_001'
        (work / 'unknown.json').write_text('secret')
        (work / 'model.bin').write_bytes(b'model')
        (work / 'update.bin').write_bytes(b'update')
        sandbox.wait(timeout=2)
        target, manifest = self.manifest()
        self.assertEqual(manifest['lifecycle_outcome'], 'succeeded')
        self.assertEqual(manifest['run_id'], 'run_001')
        self.assertEqual(manifest['job_id'], 'job_001')
        self.assertEqual(manifest['node_id'], 1)
        self.assertEqual(manifest['runtime_commit'], 'a' * 40)
        self.assertEqual(manifest['build_identity_sha256'], hashlib.sha256(self.identity.read_bytes()).hexdigest())
        self.assertEqual(set(manifest['files']), {'build_identity.json', 'client_role.json', 'role_service.log'})
        self.assertFalse(work.exists())
        for name, info in manifest['files'].items():
            content = (target / name).read_bytes()
            self.assertEqual(info['size_bytes'], len(content))
            self.assertEqual(info['sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual(sandbox.state, DaemonState.IDLE)

    def test_failed_started_and_aborted_jobs_share_manifest_schema(self):
        cases = [('failed', 'raise SystemExit(42)', None),
                 ('aborted', 'import time; time.sleep(60)', None),
                 ('start_failed', '', ['/does-not-exist'])]
        schemas = []
        for outcome, script, command in cases:
            with self.subTest(outcome=outcome):
                sandbox = self.sandbox(script, command)
                job = _make_client_job(job_id=outcome)
                if outcome == 'start_failed':
                    with self.assertRaises(FLRuntimeError):
                        sandbox.start(job, 'wlx001')
                else:
                    sandbox.start(job, 'wlx001')
                    if outcome == 'aborted':
                        sandbox.abort(timeout=0.01)
                    else:
                        self.assertEqual(sandbox.wait(timeout=2), 42)
                _, manifest = self.manifest(outcome)
                schemas.append(set(manifest))
                self.assertEqual(manifest['lifecycle_outcome'], outcome)
                self.assertFalse((self.root / 'work' / f'job_{outcome}').exists())
                self.assertFalse(self.adapter.is_tun_active('fl-c1'))
                self.assertEqual(sandbox.state, DaemonState.IDLE)
        self.assertEqual(schemas[0], schemas[1])
        self.assertEqual(schemas[1], schemas[2])

    def test_runtime_round_whitelist_excludes_binary_and_unknown_files(self):
        sandbox = self.sandbox()
        sandbox.start(_make_client_job(), 'wlx001')
        work = self.root / 'work' / 'job_job_001'
        for round_id in ['11111111-1111-4111-8111-111111111111',
                         '22222222-2222-4222-8222-222222222222']:
            directory = work / 'rounds' / round_id
            directory.mkdir(parents=True)
            for name in ['round-state.json', 'model.manifest.json', 'update.manifest.json']:
                (directory / name).write_text('{}')
            for name in ['model.bin', 'update.bin', 'unknown.json']:
                (directory / name).write_text('must not be copied')
        for name in ['uftpd.log', 'algorithm_config.json', 'issue41-client-result.json', 'observation.jsonl']:
            (work / name).write_text('{}')
        sandbox.wait(timeout=2)
        target, manifest = self.manifest()
        self.assertEqual(len([name for name in manifest['files'] if name.startswith('rounds/')]), 6)
        self.assertFalse(any(path.name in ['model.bin', 'update.bin', 'unknown.json'] for path in target.rglob('*')))

    def test_illegal_whitelisted_types_preserve_sandbox_and_release_resources(self):
        for kind in ['symlink', 'fifo', 'directory', 'round_symlink', 'oversized']:
            with self.subTest(kind=kind):
                sandbox = self.sandbox()
                sandbox.start(_make_client_job(job_id=kind), 'wlx001')
                work = self.root / 'work' / f'job_{kind}'
                path = work / 'uftpd.log'
                if kind == 'symlink':
                    path.symlink_to(self.identity)
                elif kind == 'fifo':
                    os.mkfifo(path)
                elif kind == 'directory':
                    path.mkdir()
                elif kind == 'round_symlink':
                    (work / 'rounds').symlink_to(self.root, target_is_directory=True)
                else:
                    with path.open('wb') as fh:
                        fh.truncate(16 * 1024 * 1024 + 1)
                sandbox.wait(timeout=2)
                self.assertTrue(work.exists())
                self.assertTrue((work / 'evidence_error.json').is_file())
                self.assertIsNotNone(sandbox.last_evidence_error)
                self.assertFalse((self.root / 'evidence' / kind).exists())
                self.assertFalse(self.adapter.is_tun_active('fl-c1'))
                self.assertEqual(sandbox.state, DaemonState.IDLE)

    def test_existing_archive_is_never_overwritten_even_when_empty(self):
        for occupied in [False, True]:
            job_id = f'existing_{occupied}'
            target = self.root / 'evidence' / job_id
            target.mkdir(parents=True)
            if occupied:
                (target / 'original').write_text('preserve')
            sandbox = self.sandbox()
            sandbox.start(_make_client_job(job_id=job_id), 'wlx001')
            sandbox.wait(timeout=2)
            self.assertEqual(sorted(p.name for p in target.iterdir()), ['original'] if occupied else [])
            self.assertTrue((self.root / 'work' / f'job_{job_id}').exists())
            self.assertIsNotNone(sandbox.last_evidence_error)

    def test_archive_write_failure_restores_daemon_standby_and_retains_error(self):
        daemon = ClientDaemon(ClientDaemonConfig(
            node_id=1, tun_ip='10.80.0.11', work_dir=str(self.root / 'work'),
            enable_control_plane=False, enable_link_process=False), self.adapter)
        daemon.sandbox.evidence_dir = str(self.root / 'evidence')
        daemon.sandbox._command_prefix = [sys.executable, '-c', 'pass']
        daemon.poll_hardware_once()
        daemon.trigger_job(_make_client_job())
        with mock.patch('wfb_ng.fl.artifacts.os.fsync', side_effect=OSError('disk failure')):
            self.assertEqual(daemon.wait_job(timeout=2), 0)
        self.assertEqual(daemon.state, DaemonState.IDLE)
        self.assertTrue(self.adapter.is_tun_active('fl-c1'))
        self.assertIn('disk failure', daemon.sandbox.last_evidence_error)
        self.assertTrue((self.root / 'work' / 'job_job_001').exists())
        self.assertFalse((self.root / 'evidence' / 'job_001').exists())

    def test_hash_failure_does_not_publish_partial_archive(self):
        sandbox = self.sandbox()
        sandbox.start(_make_client_job(), 'wlx001')
        with mock.patch('wfb_ng.fl.artifacts.hashlib.sha256', side_effect=ValueError('hash failure')):
            sandbox.wait(timeout=2)
        work = self.root / 'work' / 'job_job_001'
        self.assertTrue((work / 'evidence_error.json').is_file())
        self.assertFalse((self.root / 'evidence' / 'job_001').exists())
        self.assertEqual(list((self.root / 'evidence').iterdir()), [])

    def test_invalid_or_missing_build_identity_fails_closed(self):
        for identity in [None, {'schema_version': 1, 'commit': 'release'},
                         {'schema_version': True, 'commit': 'a' * 40}]:
            if identity is None:
                self.identity.unlink()
            else:
                self.identity.write_text(json.dumps(identity))
            work = self.root / 'source'
            work.mkdir(exist_ok=True)
            with self.assertRaises((OSError, FLRuntimeError)):
                archive_client_evidence(str(work), str(self.root / 'evidence'),
                    run_id='run', job_id='invalid_identity', node_id=1,
                    lifecycle_outcome='failed', returncode=1)
            self.assertFalse((self.root / 'evidence' / 'invalid_identity').exists())

    def test_symlink_parent_does_not_create_archive_outside_root(self):
        work = self.root / 'source'
        work.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'link'
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            archive_client_evidence(str(work), str(link / 'evidence'),
                run_id='run', job_id='job', node_id=1,
                lifecycle_outcome='failed', returncode=1)
        self.assertEqual(list(outside.iterdir()), [])

    def test_final_directory_appears_only_after_complete_manifest(self):
        import wfb_ng.fl.evidence as evidence
        publish = evidence._publish_directory

        def inspect_before_publish(root_fd, name, job_id):
            self.assertFalse((self.root / 'evidence' / job_id).exists())
            temporary = self.root / 'evidence' / name
            manifest = json.loads((temporary / 'evidence_manifest.json').read_text())
            self.assertTrue(all((temporary / path).is_file() for path in manifest['files']))
            # 模拟并发创建空目录；RENAME_NOREPLACE 仍必须拒绝。
            (self.root / 'evidence' / job_id).mkdir()
            return publish(root_fd, name, job_id)

        sandbox = self.sandbox()
        sandbox.start(_make_client_job(), 'wlx001')
        with mock.patch('wfb_ng.fl.evidence._publish_directory', side_effect=inspect_before_publish):
            sandbox.wait(timeout=2)
        self.assertEqual(list((self.root / 'evidence' / 'job_001').iterdir()), [])
        self.assertTrue((self.root / 'work' / 'job_job_001').exists())

    def test_failed_start_preserves_actual_child_exit_code(self):
        sandbox = JobSandbox(str(self.root / 'work'), self.adapter,
                             evidence_dir=str(self.root / 'evidence'))
        # 使用真实就绪 socket，子进程在 READY 前退出。
        real_popen = subprocess.Popen
        def exiting_child(cmd, **kwargs):
            return real_popen([sys.executable, '-c', 'raise SystemExit(42)'], **kwargs)
        with mock.patch('wfb_ng.fl.client_daemon.subprocess.Popen', side_effect=exiting_child):
            with self.assertRaises(FLRuntimeError):
                sandbox.start(_make_client_job(), 'wlx001')
        _, manifest = self.manifest()
        self.assertEqual(manifest['lifecycle_outcome'], 'start_failed')
        self.assertEqual(manifest['returncode'], 42)

    def test_tun_cleanup_failure_stops_sandbox_accepting_jobs(self):
        sandbox = self.sandbox()
        sandbox.start(_make_client_job(), 'wlx001')
        with mock.patch.object(self.adapter, 'teardown_tun', side_effect=OSError('TUN failure')):
            with self.assertRaises(FLRuntimeError):
                sandbox.wait(timeout=2)
        self.assertEqual(sandbox.state, DaemonState.STOPPED)
        with self.assertRaises(FLRuntimeError):
            sandbox.start(_make_client_job(job_id='next'), 'wlx001')
        self.assertTrue((self.root / 'evidence' / 'job_001').is_dir())

    def test_sandbox_removal_failure_records_retained_path(self):
        sandbox = self.sandbox()
        sandbox.start(_make_client_job(), 'wlx001')
        with mock.patch('wfb_ng.fl.client_daemon.shutil.rmtree', side_effect=OSError('remove failure')):
            with self.assertLogs('wfb_fl_client_daemon', level='ERROR') as log:
                sandbox.wait(timeout=2)
        work = self.root / 'work' / 'job_job_001'
        self.assertEqual(sandbox.retained_sandbox_dir, str(work))
        self.assertTrue(work.exists())
        self.assertIn('remove failure', '\n'.join(log.output))

    def test_existing_sandbox_is_preserved_on_start_collision(self):
        work = self.root / 'work' / 'job_job_001'
        work.mkdir(parents=True)
        (work / 'original').write_text('preserve')
        sandbox = self.sandbox()
        with self.assertRaises(FLRuntimeError):
            sandbox.start(_make_client_job(), 'wlx001')
        self.assertEqual((work / 'original').read_text(), 'preserve')
        self.assertFalse((self.root / 'evidence' / 'job_001').exists())

    def test_real_client_runtime_two_rounds_are_archived_on_sandbox_wait(self):
        sandbox = self.sandbox()
        job = _make_client_job()
        sandbox.start(job, 'wlx001')
        self.addCleanup(sandbox.abort)
        work = self.root / 'work' / 'job_job_001'
        round_ids = ['11111111-1111-4111-8111-111111111111',
                     '22222222-2222-4222-8222-222222222222']
        candidates = []
        model_manifests = []
        for round_id in round_ids:
            candidate = self.root / 'deliveries' / round_id
            model = generate_model_fixture(str(candidate / 'model.bin'), size_bytes=1024)
            model_manifest = {
                'schema_version': 1, 'artifact_type': 'model',
                'round_id': round_id, 'size_bytes': model['size_bytes'],
                'sha256': model['sha256'], 'participant_node_ids': [job.node_id],
            }
            (candidate / 'model.manifest.json').write_text(json.dumps(model_manifest))
            candidates.append(str(candidate))
            model_manifests.append(model_manifest)
        update = generate_client_fixture(
            str(self.root / 'update.bin'), job.node_id, size_bytes=2048)
        transport = mock.Mock(ready=True)
        transport.wait_for_model_candidate.side_effect = candidates
        runtime = ClientRuntime(str(work), job.node_id, job.max_update_size_bytes, transport)
        expected = {}
        try:
            for round_id, model_manifest in zip(round_ids, model_manifests):
                round_dir = work / 'rounds' / round_id
                self.assertEqual(runtime.wait_for_model(), str(round_dir / 'model.bin'))
                self.assertEqual((round_dir / 'model.bin').read_bytes(),
                                 (self.root / 'deliveries' / round_id / 'model.bin').read_bytes())
                self.assertIsNone(runtime.submit_update(update['path']))
                self.assertEqual((round_dir / 'update.bin').read_bytes(),
                                 Path(update['path']).read_bytes())
                expected[f'rounds/{round_id}/model.manifest.json'] = model_manifest
                expected[f'rounds/{round_id}/update.manifest.json'] = {
                    'schema_version': 1, 'artifact_type': 'update',
                    'round_id': round_id, 'node_id': job.node_id,
                    'size_bytes': update['size_bytes'], 'sha256': update['sha256'],
                }
                expected[f'rounds/{round_id}/round-state.json'] = {
                    'schema_version': 1, 'round_id': round_id,
                    'role': 'client', 'state': 'succeeded',
                }
            self.assertEqual(transport.wait_for_model_candidate.call_count, 2)
            self.assertEqual(transport.submit_update.call_args_list, [
                mock.call(round_id, job.node_id,
                          str(work / 'rounds' / round_id / 'update.bin'),
                          update['size_bytes'], update['sha256'])
                for round_id in round_ids
            ])
            expected['current-round.json'] = {
                'schema_version': 1, 'role': 'client', 'round_id': round_ids[-1],
            }
            original = {name: (work / name).read_bytes() for name in expected}
        finally:
            runtime.close()

        self.assertEqual(sandbox.wait(timeout=2), 0)
        target, manifest = self.manifest()
        self.assertIsNone(sandbox.last_evidence_error)
        self.assertEqual(manifest['lifecycle_outcome'], 'succeeded')
        self.assertEqual(manifest['returncode'], 0)
        self.assertEqual(set(manifest['files']), set(expected) | {
            'build_identity.json', 'client_role.json', 'role_service.log',
        })
        for name, value in expected.items():
            self.assertEqual(json.loads((target / name).read_text()), value)
            self.assertEqual((target / name).read_bytes(), original[name])
        for name, info in manifest['files'].items():
            content = (target / name).read_bytes()
            self.assertEqual(info['size_bytes'], len(content))
            self.assertEqual(info['sha256'], hashlib.sha256(content).hexdigest())
        self.assertFalse(any(path.suffix == '.bin' for path in target.rglob('*')))
        self.assertFalse(work.exists())
        self.assertFalse(self.adapter.is_tun_active(job.tun_name))
        self.assertEqual(sandbox.state, DaemonState.IDLE)
