#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import os
import shutil
import tempfile
import unittest
import uuid
from unittest import mock

from wfb_ng.fl import ClientRuntime, FLRuntimeError, ServerRuntime
from wfb_ng.fl import artifacts


class FileContractTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-contract-')
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_publish_preflight_failure_does_not_allocate_round(self):
        work_dir = os.path.join(self.root, 'server')
        runtime = ServerRuntime(work_dir, 1, 1024, ServerStub())
        self.addCleanup(runtime.close)

        with self.assertRaises(FLRuntimeError) as raised:
            runtime.publish_model(os.path.join(self.root, 'missing-model'))

        self.assertEqual('invalid_artifact', raised.exception.error_code)
        self.assertFalse(os.path.exists(os.path.join(work_dir, 'rounds')))

    def test_invalid_path_type_has_stable_error(self):
        runtime = ServerRuntime(
            os.path.join(self.root, 'server-invalid-path'), 1, 1024, ServerStub())
        self.addCleanup(runtime.close)

        with self.assertRaises(FLRuntimeError) as raised:
            runtime.publish_model(object())

        self.assertEqual('invalid_artifact', raised.exception.error_code)

    def test_state_persistence_failure_enters_fatal_state(self):
        work_dir = os.path.join(self.root, 'fatal-server')
        runtime = ServerRuntime(work_dir, 1, 1024, ServerStub())
        self.addCleanup(runtime.close)
        real_write = artifacts.write_json_atomic

        def fail_round_state(path, value):
            if path.endswith('round-state.json'):
                raise OSError('disk full')
            return real_write(path, value)

        with mock.patch(
                'wfb_ng.fl.runtime.write_json_atomic', side_effect=fail_round_state):
            with self.assertRaises(FLRuntimeError) as first:
                runtime.publish_model(self.write_file('fatal-model', b'model'))
        self.assertEqual('state_persistence_failed', first.exception.error_code)

        with self.assertRaises(FLRuntimeError) as repeated:
            runtime.publish_model(object())
        self.assertEqual('state_persistence_failed', repeated.exception.error_code)

    def test_symbolic_links_to_regular_files_are_supported(self):
        transport = LoopbackTransport(os.path.join(self.root, 'symlink-inbox'))
        server = ServerRuntime(
            os.path.join(self.root, 'symlink-server'), 1, 1024, transport)
        client = ClientRuntime(
            os.path.join(self.root, 'symlink-client'), 1, 1024,
            transport.client())
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        model = self.write_file('symlink-model-source', b'model')
        update = self.write_file('symlink-update-source', b'update')
        model_link = os.path.join(self.root, 'model-link')
        update_link = os.path.join(self.root, 'update-link')
        os.symlink(model, model_link)
        os.symlink(update, update_link)

        server.publish_model(model_link)
        client.wait_for_model()
        client.submit_update(update_link)

        self.assertEqual(b'update', self.read_file(server.wait_for_updates()[1]))

    def test_manifest_is_present_when_transport_observes_delivery(self):
        transport = ManifestOrderTransport()
        runtime = ServerRuntime(
            os.path.join(self.root, 'ordered-server'), 1, 1024, transport)
        self.addCleanup(runtime.close)

        runtime.publish_model(self.write_file('ordered-model', b'model'))

        self.assertTrue(transport.model_present)
        self.assertTrue(transport.manifest_present)
        self.assertEqual([], transport.temporary_files)

    def test_current_success_result_rechecks_managed_update(self):
        transport = LoopbackTransport(os.path.join(self.root, 'recheck-inbox'))
        server = ServerRuntime(
            os.path.join(self.root, 'recheck-server'), 1, 1024, transport)
        client = ClientRuntime(
            os.path.join(self.root, 'recheck-client'), 1, 1024,
            transport.client())
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        server.publish_model(self.write_file('recheck-model', b'model'))
        client.wait_for_model()
        client.submit_update(self.write_file('recheck-update', b'update'))
        managed_update = server.wait_for_updates()[1]
        with open(managed_update, 'wb') as fh:
            fh.write(b'UPDATE')

        with self.assertRaises(FLRuntimeError) as raised:
            server.wait_for_updates()

        self.assertEqual('round_artifacts_corrupted', raised.exception.error_code)

    def test_quarantine_move_failure_has_stable_error(self):
        candidate = self.make_candidate(str(uuid.uuid4()), b'', (2,), 'move-failure')
        runtime = ClientRuntime(
            os.path.join(self.root, 'move-failure-client'), 1, 1024,
            CandidateQueueTransport([candidate]))
        self.addCleanup(runtime.close)

        with mock.patch('wfb_ng.fl.runtime.shutil.move', side_effect=OSError('denied')):
            with self.assertRaises(FLRuntimeError) as raised:
                runtime.wait_for_model()

        self.assertEqual('model_quarantine_failed', raised.exception.error_code)

    def test_zero_byte_model_and_update_are_valid(self):
        transport = LoopbackTransport(os.path.join(self.root, 'inbox'))
        server = ServerRuntime(os.path.join(self.root, 'server'), 1, 1024, transport)
        client = ClientRuntime(
            os.path.join(self.root, 'client'), 1, 1024, transport.client())
        self.addCleanup(server.close)
        self.addCleanup(client.close)

        server.publish_model(self.write_file('empty-model', b''))
        self.assertEqual(b'', self.read_file(client.wait_for_model()))
        client.submit_update(self.write_file('empty-update', b''))

        self.assertEqual(b'', self.read_file(server.wait_for_updates()[1]))

    def test_archive_rejects_content_change_with_restored_metadata(self):
        source_path = self.write_file('changing-source', b'a' * artifacts.CHUNK_SIZE)
        target_path = os.path.join(self.root, 'managed', 'model.bin')
        original_read = artifacts._read_archive_chunk
        changed = False

        def change_after_first_read(source):
            nonlocal changed
            chunk = original_read(source)
            if chunk and not changed:
                changed = True
                source_stat = os.stat(source_path)
                with open(source_path, 'r+b') as changed_source:
                    changed_source.write(b'b')
                os.utime(source_path, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            return chunk

        with mock.patch.object(
                artifacts, '_read_archive_chunk', side_effect=change_after_first_read):
            with self.assertRaises(FLRuntimeError) as raised:
                artifacts.archive_file(source_path, target_path)

        self.assertEqual('artifact_changed', raised.exception.error_code)
        self.assertFalse(os.path.exists(target_path))

    def test_manifest_rejects_duplicate_unknown_and_noncanonical_values(self):
        cases = {
            'duplicate': '{"schema_version":1,"schema_version":1}',
            'unknown': self.model_manifest_json(extra=',"unexpected":true'),
            'boolean-size': self.model_manifest_json(size='true'),
            'uppercase-sha': self.model_manifest_json(sha='"%s"' % ('A' * 64)),
            'noncanonical-uuid': self.model_manifest_json(
                round_id='"550E8400-E29B-41D4-A716-446655440000"'),
            'trailing': self.model_manifest_json() + ' trailing',
        }
        for name, manifest_text in cases.items():
            with self.subTest(name=name):
                candidate = self.make_raw_candidate(name, manifest_text, b'')
                runtime = ClientRuntime(
                    os.path.join(self.root, 'client-' + name), 1, 1024,
                    CandidateQueueTransport([candidate]))
                self.addCleanup(runtime.close)
                with self.assertRaises(FLRuntimeError) as raised:
                    runtime.wait_for_model()
                self.assertEqual('invalid_manifest', raised.exception.error_code)

    def test_invalid_manifest_with_round_id_persists_failed_round(self):
        round_id = str(uuid.uuid4())
        manifest_text = self.model_manifest_json(
            round_id='"%s"' % round_id, extra=',"unexpected":true')
        candidate = self.make_raw_candidate('invalid-with-id', manifest_text, b'')
        work_dir = os.path.join(self.root, 'client-invalid-with-id')
        runtime = ClientRuntime(
            work_dir, 1, 1024, CandidateQueueTransport([candidate]))
        self.addCleanup(runtime.close)

        with self.assertRaises(FLRuntimeError) as raised:
            runtime.wait_for_model()

        self.assertEqual('invalid_manifest', raised.exception.error_code)
        state = self.read_json(os.path.join(
            work_dir, 'rounds', round_id, 'round-state.json'))
        self.assertEqual('failed', state['state'])
        self.assertEqual('invalid_manifest', state['error_code'])

    def test_nonparticipant_model_is_quarantined_then_target_is_returned(self):
        wrong = self.make_candidate(str(uuid.uuid4()), b'', (2,), 'wrong')
        os.unlink(os.path.join(wrong, 'model.bin'))
        expected_round = str(uuid.uuid4())
        target = self.make_candidate(expected_round, b'model', (1,), 'target')
        work_dir = os.path.join(self.root, 'client')
        runtime = ClientRuntime(
            work_dir, 1, 1024, CandidateQueueTransport([wrong, target]))
        self.addCleanup(runtime.close)

        managed_model = runtime.wait_for_model()

        self.assertEqual(b'model', self.read_file(managed_model))
        self.assertIn('model_not_for_this_node', self.read_rejection_codes(work_dir))

    def test_target_model_damage_fails_round(self):
        for damage in ('missing', 'size', 'digest'):
            with self.subTest(damage=damage):
                round_id = str(uuid.uuid4())
                candidate = self.make_candidate(round_id, b'model', (1,), damage)
                model_path = os.path.join(candidate, 'model.bin')
                if damage == 'missing':
                    os.unlink(model_path)
                elif damage == 'size':
                    with open(model_path, 'ab') as fh:
                        fh.write(b'x')
                else:
                    with open(model_path, 'wb') as fh:
                        fh.write(b'MODEL')
                work_dir = os.path.join(self.root, 'client-' + damage)
                runtime = ClientRuntime(
                    work_dir, 1, 1024, CandidateQueueTransport([candidate]))
                self.addCleanup(runtime.close)

                with self.assertRaises(FLRuntimeError) as raised:
                    runtime.wait_for_model()

                self.assertEqual('artifact_integrity_failed', raised.exception.error_code)
                state = self.read_json(os.path.join(
                    work_dir, 'rounds', round_id, 'round-state.json'))
                self.assertEqual('failed', state['state'])

    def test_duplicate_is_ignored_without_second_training_opportunity(self):
        round_id = str(uuid.uuid4())
        first = self.make_candidate(round_id, b'model', (1,), 'first')
        duplicate = self.make_candidate(round_id, b'model', (1,), 'duplicate')
        next_round = str(uuid.uuid4())
        next_candidate = self.make_candidate(next_round, b'next', (1,), 'next')
        work_dir = os.path.join(self.root, 'client')
        transport = CandidateQueueTransport([first, duplicate, next_candidate])
        runtime = ClientRuntime(work_dir, 1, 1024, transport)
        self.addCleanup(runtime.close)
        runtime.wait_for_model()
        runtime.submit_update(self.write_file('update', b'update'))

        second_model = runtime.wait_for_model()

        self.assertIn(next_round, second_model)
        self.assertEqual(3, transport.wait_count)
        self.assertIn('duplicate_model_ignored', self.read_rejection_codes(work_dir))

    def test_conflicting_duplicate_is_quarantined_with_stable_error(self):
        round_id = str(uuid.uuid4())
        first = self.make_candidate(round_id, b'model', (1,), 'first')
        conflict = self.make_candidate(round_id, b'changed', (1,), 'conflict')
        work_dir = os.path.join(self.root, 'client')
        runtime = ClientRuntime(
            work_dir, 1, 1024, CandidateQueueTransport([first, conflict]))
        self.addCleanup(runtime.close)
        runtime.wait_for_model()
        runtime.submit_update(self.write_file('update', b'update'))

        with self.assertRaises(FLRuntimeError) as raised:
            runtime.wait_for_model()

        self.assertEqual('round_id_content_conflict', raised.exception.error_code)
        self.assertIn('round_id_content_conflict', self.read_rejection_codes(work_dir))

    def test_server_rejects_invalid_committed_update(self):
        for damage, expected_code in (
                ('oversized', 'artifact_too_large'),
                ('unknown-field', 'invalid_manifest'),
                ('uppercase-sha', 'invalid_manifest'),
                ('digest', 'artifact_integrity_failed')):
            with self.subTest(damage=damage):
                transport = DamagedUpdateTransport(damage)
                runtime = ServerRuntime(
                    os.path.join(self.root, 'server-' + damage), 1, 4, transport)
                self.addCleanup(runtime.close)
                model_path = self.write_file('model-' + damage, b'model')
                caught_error = None
                try:
                    runtime.publish_model(model_path)
                except FLRuntimeError as error:
                    caught_error = error
                if caught_error is None:
                    with self.assertRaises(FLRuntimeError) as raised:
                        runtime.wait_for_updates()
                    caught_error = raised.exception
                self.assertEqual(expected_code, caught_error.error_code)

    def model_manifest_json(self, size='0', sha=None, round_id=None, extra=''):
        sha = sha or '"%s"' % hashlib.sha256(b'').hexdigest()
        round_id = round_id or '"550e8400-e29b-41d4-a716-446655440000"'
        return (
            '{"schema_version":1,"artifact_type":"model",'
            '"round_id":%s,"size_bytes":%s,"sha256":%s,'
            '"participant_node_ids":[1]%s}' %
            (round_id, size, sha, extra))

    def make_raw_candidate(self, name, manifest_text, content):
        candidate = os.path.join(self.root, 'candidate', name)
        os.makedirs(candidate)
        with open(os.path.join(candidate, 'model.bin'), 'wb') as fh:
            fh.write(content)
        with open(os.path.join(candidate, 'model.manifest.json'), 'w',
                  encoding='utf-8') as fh:
            fh.write(manifest_text)
        return candidate

    def make_candidate(self, round_id, content, participants, name):
        candidate = os.path.join(self.root, 'candidate', name)
        os.makedirs(candidate)
        with open(os.path.join(candidate, 'model.bin'), 'wb') as fh:
            fh.write(content)
        with open(os.path.join(candidate, 'model.manifest.json'), 'w',
                  encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'artifact_type': 'model',
                'round_id': round_id,
                'size_bytes': len(content),
                'sha256': hashlib.sha256(content).hexdigest(),
                'participant_node_ids': list(participants),
            }, fh)
        return candidate

    def read_rejection_codes(self, work_dir):
        codes = []
        for root, _, files in os.walk(os.path.join(work_dir, 'rejected-models')):
            if 'rejection.json' in files:
                codes.append(self.read_json(os.path.join(root, 'rejection.json'))['error_code'])
        return codes

    def write_file(self, name, content):
        path = os.path.join(self.root, name)
        with open(path, 'wb') as fh:
            fh.write(content)
        return path

    def read_file(self, path):
        with open(path, 'rb') as fh:
            return fh.read()

    def read_json(self, path):
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)


class ServerStub(object):
    ready = True

    def install_round(self, **kwargs):
        return None

    def start_downlink(self, round_id, model_path, manifest_path):
        self.publish_model(round_id, model_path, manifest_path)
        return self

    def wait_downlink(self, operation):
        if operation is not self:
            raise RuntimeError('unexpected operation handle')

    def publish_model(self, round_id, model_path, manifest_path):
        return None


class ManifestOrderTransport(ServerStub):
    def __init__(self):
        self.model_present = False
        self.manifest_present = False
        self.temporary_files = None

    def install_round(self, round_id, participant_node_ids, round_dir,
                      max_update_size_bytes, failure_callback):
        self.round_dir = round_dir

    def publish_model(self, round_id, model_path, manifest_path):
        self.model_present = os.path.isfile(model_path)
        self.manifest_present = os.path.isfile(manifest_path)
        self.temporary_files = [
            name for name in os.listdir(self.round_dir) if name.startswith('.')]

    def wait_for_update(self, timeout):
        return None


class CandidateQueueTransport(object):
    ready = True

    def __init__(self, candidates):
        self.candidates = iter(candidates)
        self.wait_count = 0

    def wait_for_model_candidate(self):
        self.wait_count += 1
        return next(self.candidates)

    def submit_update(self, round_id, node_id, update_path, size_bytes, digest):
        return None


class LoopbackTransport(ServerStub):
    def __init__(self, inbox):
        self.inbox = inbox
        self.round_dir = None

    def client(self):
        return LoopbackClientTransport(self)

    def install_round(self, round_id, participant_node_ids, round_dir,
                      max_update_size_bytes, failure_callback):
        self.round_dir = round_dir
        self.round_id = round_id

    def publish_model(self, round_id, model_path, manifest_path):
        candidate = os.path.join(self.inbox, round_id)
        os.makedirs(candidate)
        shutil.copyfile(model_path, os.path.join(candidate, 'model.bin'))
        shutil.copyfile(manifest_path, os.path.join(candidate, 'model.manifest.json'))
        self.candidate = candidate

    def wait_for_update(self, timeout):
        return None


class LoopbackClientTransport(object):
    ready = True

    def __init__(self, shared):
        self.shared = shared

    def wait_for_model_candidate(self):
        return self.shared.candidate

    def submit_update(self, round_id, node_id, update_path, size_bytes, digest):
        update_dir = os.path.join(self.shared.round_dir, 'updates', str(node_id))
        os.makedirs(update_dir)
        shutil.copyfile(update_path, os.path.join(update_dir, 'update.bin'))
        with open(os.path.join(update_dir, 'update.manifest.json'), 'w',
                  encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'artifact_type': 'update',
                'round_id': round_id,
                'node_id': node_id,
                'size_bytes': size_bytes,
                'sha256': digest,
            }, fh)


class DamagedUpdateTransport(ServerStub):
    def __init__(self, damage):
        self.damage = damage

    def install_round(self, round_id, participant_node_ids, round_dir,
                      max_update_size_bytes, failure_callback):
        self.round_id = round_id
        self.round_dir = round_dir

    def publish_model(self, round_id, model_path, manifest_path):
        content = b'12345' if self.damage == 'oversized' else b'data'
        update_dir = os.path.join(self.round_dir, 'updates', '1')
        os.makedirs(update_dir)
        with open(os.path.join(update_dir, 'update.bin'), 'wb') as fh:
            fh.write(content if self.damage != 'digest' else b'DATA')
        manifest = {
            'schema_version': 1,
            'artifact_type': 'update',
            'round_id': round_id,
            'node_id': 1,
            'size_bytes': len(content),
            'sha256': hashlib.sha256(content).hexdigest(),
        }
        if self.damage == 'unknown-field':
            manifest['unexpected'] = True
        elif self.damage == 'uppercase-sha':
            manifest['sha256'] = manifest['sha256'].upper()
        with open(os.path.join(update_dir, 'update.manifest.json'), 'w',
                  encoding='utf-8') as fh:
            json.dump(manifest, fh)

    def wait_for_update(self, timeout):
        return None


if __name__ == '__main__':
    unittest.main()
