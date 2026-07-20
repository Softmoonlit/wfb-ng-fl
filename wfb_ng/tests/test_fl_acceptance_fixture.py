#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import json
import os
import shutil
import tempfile
import unittest
import uuid
from collections import OrderedDict
from unittest import mock

from wfb_ng.fl import FLRuntimeError
from wfb_ng.fl.acceptance_fixture import client, server


class AcceptanceFixtureTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-acceptance-fixture-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.model_content = b'deterministic-model\nweights=1,2,3\n'
        self.model_source = self.write_file('model-source.bin', self.model_content)
        self.round_id = str(uuid.uuid4())
        self.managed_model = self.make_managed_model()

    def test_two_clients_and_server_complete_with_atomic_results(self):
        submitted = {}
        delays = []
        client_results = {}
        for node_id, delay in ((1, 0.25), (2, 0.75)):
            runtime = ClientRuntimeStub(
                os.path.join(self.root, 'client-%d' % node_id), node_id,
                self.managed_model, submitted)
            result_path = os.path.join(
                self.root, 'results', 'client-%d.json' % node_id)
            with mock.patch(
                    'wfb_ng.fl.acceptance_fixture.time.sleep',
                    side_effect=lambda value: delays.append(value)):
                self.assertIsNone(client(runtime, {
                    'node_id': node_id,
                    'participant_node_ids': [1, 2],
                    'delay_seconds': delay,
                    'result_path': result_path,
                }))
            client_results[node_id] = self.read_json(result_path)

        server_runtime = ServerRuntimeStub(
            os.path.join(self.root, 'server'), (1, 2),
            OrderedDict((node_id, submitted[node_id]) for node_id in (1, 2)))
        result_path = os.path.join(self.root, 'results', 'server.json')
        self.assertIsNone(server(server_runtime, {
            'model_path': self.model_source,
            'participant_node_ids': [1, 2],
            'result_path': result_path,
        }))

        self.assertEqual([0.25, 0.75], delays)
        self.assertEqual([self.model_source], server_runtime.published_models)
        self.assertEqual(1, server_runtime.wait_count)
        server_result = self.read_json(result_path)
        self.assertEqual([1, 2], [
            item['node_id'] for item in server_result['update_mapping']])
        self.assertEqual(
            {'1', '2'}, set(server_result['output_sha256_by_node']))
        self.assertEqual(self.sha256(self.model_source), server_result['input_sha256'])
        self.assertEqual(
            {'publish_model', 'wait_for_updates'},
            set(server_result['interface_timings']))
        for node_id, expected_delay in ((1, 0.25), (2, 0.75)):
            result = client_results[node_id]
            self.assertEqual(node_id, result['node_id'])
            self.assertEqual(expected_delay, result['configured_delay_seconds'])
            self.assertEqual(
                {'wait_for_model', 'submit_update'},
                set(result['interface_timings']))
            self.assertEqual(self.sha256(self.managed_model), result['input_sha256'])
            self.assertEqual(self.sha256(submitted[node_id]), result['output_sha256'])
            self.assertEqual(node_id, result['update_mapping'][0]['node_id'])
            for timing in result['interface_timings'].values():
                self.assertLessEqual(timing['started_at'], timing['completed_at'])
                self.assertGreaterEqual(timing['elapsed_seconds'], 0)
        self.assertFalse(self.temporary_json_files(os.path.join(self.root, 'results')))

    def test_server_waits_for_optional_start_gate(self):
        updates = self.make_valid_updates()
        runtime = ServerRuntimeStub(
            os.path.join(self.root, 'server-gate'), (1, 2), updates)
        gate_path = os.path.join(self.root, 'gate', 'start')

        def open_gate(delay):
            self.assertEqual(0.1, delay)
            os.makedirs(os.path.dirname(gate_path), exist_ok=True)
            with open(gate_path, 'w', encoding='ascii'):
                pass

        with mock.patch(
                'wfb_ng.fl.acceptance_fixture.time.sleep',
                side_effect=open_gate):
            server(runtime, {
                'model_path': self.model_source,
                'participant_node_ids': [1, 2],
                'start_gate_path': gate_path,
                'start_gate_timeout_seconds': 1,
            })
        self.assertEqual([self.model_source], runtime.published_models)

    def test_server_start_gate_times_out_before_publish(self):
        runtime = ServerRuntimeStub(
            os.path.join(self.root, 'server-timeout'), (1, 2), {})
        gate_path = os.path.join(self.root, 'missing-gate')
        monotonic = iter((0, 2))
        with mock.patch(
                'wfb_ng.fl.acceptance_fixture.time.monotonic',
                side_effect=lambda: next(monotonic)):
            with self.assertRaises(FLRuntimeError) as raised:
                server(runtime, {
                    'model_path': self.model_source,
                    'participant_node_ids': [1, 2],
                    'start_gate_path': gate_path,
                    'start_gate_timeout_seconds': 1,
                })
        self.assertEqual('acceptance_start_timeout', raised.exception.error_code)
        self.assertEqual([], runtime.published_models)

    def test_server_rejects_gate_timeout_without_path(self):
        runtime = ServerRuntimeStub(
            os.path.join(self.root, 'server-invalid-gate'), (1, 2), {})
        with self.assertRaises(FLRuntimeError) as raised:
            server(runtime, {
                'model_path': self.model_source,
                'participant_node_ids': [1, 2],
                'start_gate_timeout_seconds': 1,
            })
        self.assertEqual('invalid_acceptance_config', raised.exception.error_code)
        self.assertEqual([], runtime.published_models)

    def test_server_rejects_unsorted_and_incomplete_update_mapping(self):
        updates = self.make_valid_updates()
        cases = {
            'unsorted': OrderedDict(((2, updates[2]), (1, updates[1]))),
            'missing': OrderedDict(((1, updates[1]),)),
        }
        for name, mapping in cases.items():
            with self.subTest(name=name):
                runtime = ServerRuntimeStub(
                    os.path.join(self.root, 'server-' + name), (1, 2), mapping)
                with self.assertRaises(FLRuntimeError) as raised:
                    server(runtime, {
                        'model_path': self.model_source,
                        'participant_node_ids': [1, 2],
                    })
                self.assertEqual(
                    'acceptance_validation_failed', raised.exception.error_code)
                self.assertIn('数值升序完整', raised.exception.error_message)

    def test_client_independently_rejects_manifest_size_sha_and_participants(self):
        cases = ('manifest', 'size', 'sha', 'participants')
        for damage in cases:
            with self.subTest(damage=damage):
                model_path = self.make_managed_model(name=damage)
                manifest_path = os.path.join(
                    os.path.dirname(model_path), 'model.manifest.json')
                manifest = self.read_json(manifest_path)
                if damage == 'manifest':
                    manifest['unexpected'] = True
                elif damage == 'size':
                    manifest['size_bytes'] += 1
                elif damage == 'sha':
                    manifest['sha256'] = '0' * 64
                else:
                    manifest['participant_node_ids'] = [1, 3]
                self.write_json(manifest_path, manifest)
                runtime = ClientRuntimeStub(
                    os.path.join(self.root, 'damaged-' + damage), 1,
                    model_path, {})

                with self.assertRaises(FLRuntimeError) as raised:
                    client(runtime, {
                        'node_id': 1,
                        'participant_node_ids': [1, 2],
                        'delay_seconds': 0,
                    })

                self.assertEqual(
                    'acceptance_validation_failed', raised.exception.error_code)
                self.assertEqual([], runtime.submitted_paths)

    def test_client_normalizes_malformed_manifest_validation_error(self):
        model_path = self.make_managed_model(name='malformed')
        manifest_path = os.path.join(
            os.path.dirname(model_path), 'model.manifest.json')
        with open(manifest_path, 'w', encoding='utf-8') as fh:
            fh.write('{invalid')
        runtime = ClientRuntimeStub(
            os.path.join(self.root, 'malformed-client'), 1, model_path, {})

        with self.assertRaises(FLRuntimeError) as raised:
            client(runtime, {
                'node_id': 1,
                'participant_node_ids': [1, 2],
                'delay_seconds': 0,
            })

        self.assertEqual(
            'acceptance_validation_failed', raised.exception.error_code)
        self.assertIn('manifest', raised.exception.error_message)

    def test_client_rejects_non_v4_manifest_round_id(self):
        model_path = self.make_managed_model(name='uuid-version')
        manifest_path = os.path.join(
            os.path.dirname(model_path), 'model.manifest.json')
        manifest = self.read_json(manifest_path)
        manifest['round_id'] = str(uuid.uuid1())
        self.write_json(manifest_path, manifest)
        runtime = ClientRuntimeStub(
            os.path.join(self.root, 'uuid-version-client'), 1, model_path, {})

        with self.assertRaises(FLRuntimeError) as raised:
            client(runtime, {
                'node_id': 1,
                'participant_node_ids': [1, 2],
                'delay_seconds': 0,
            })

        self.assertEqual(
            'acceptance_validation_failed', raised.exception.error_code)
        self.assertIn('manifest', raised.exception.error_message)

    def test_client_wraps_atomic_update_write_failure(self):
        runtime = ClientRuntimeStub(
            os.path.join(self.root, 'write-failure-client'), 1,
            self.managed_model, {})
        with mock.patch(
                'wfb_ng.fl.acceptance_fixture.write_json_atomic',
                side_effect=OSError('disk full')):
            with self.assertRaises(FLRuntimeError) as raised:
                client(runtime, {
                    'node_id': 1,
                    'participant_node_ids': [1, 2],
                    'delay_seconds': 0,
                })

        self.assertEqual('acceptance_update_failed', raised.exception.error_code)
        self.assertEqual(1, raised.exception.node_id)
        self.assertIn('update', raised.exception.error_message)
        self.assertEqual([], runtime.submitted_paths)

    def test_strict_configuration_rejects_invalid_values_before_runtime_calls(self):
        server_runtime = ServerRuntimeStub(
            os.path.join(self.root, 'server-config'), (1, 2), {})
        client_runtime = ClientRuntimeStub(
            os.path.join(self.root, 'client-config'), 1,
            self.managed_model, {})
        cases = (
            (server, server_runtime, None),
            (server, server_runtime, {
                'model_path': 'relative.bin',
                'participant_node_ids': [1, 2],
            }),
            (server, server_runtime, {
                'model_path': self.model_source,
                'participant_node_ids': [2, 1],
            }),
            (server, server_runtime, {
                'model_path': self.model_source,
                'participant_node_ids': [1, 2],
                'unexpected': True,
            }),
            (client, client_runtime, {
                'node_id': 2,
                'participant_node_ids': [1, 2],
                'delay_seconds': 0,
            }),
            (client, client_runtime, {
                'node_id': 1,
                'participant_node_ids': [1, 2],
                'delay_seconds': float('inf'),
            }),
            (client, client_runtime, {
                'node_id': 1,
                'participant_node_ids': [1, 2],
                'delay_seconds': True,
            }),
        )
        for call, runtime, config in cases:
            with self.subTest(call=call.__name__, config=config):
                with self.assertRaises(FLRuntimeError) as raised:
                    call(runtime, config)
                self.assertEqual(
                    'invalid_acceptance_config', raised.exception.error_code)
                self.assertTrue(raised.exception.error_message)
        self.assertEqual([], server_runtime.published_models)
        self.assertEqual(0, client_runtime.wait_count)

    def test_result_defaults_under_runtime_work_dir(self):
        submitted = {}
        runtime = ClientRuntimeStub(
            os.path.join(self.root, 'default-client'), 1,
            self.managed_model, submitted)
        with mock.patch('wfb_ng.fl.acceptance_fixture.time.sleep'):
            client(runtime, {
                'node_id': 1,
                'participant_node_ids': [1, 2],
                'delay_seconds': 0,
            })

        result_path = os.path.join(
            runtime.work_dir, 'acceptance-fixture', 'client-1-result.json')
        self.assertEqual('succeeded', self.read_json(result_path)['status'])

    def make_managed_model(self, name='managed'):
        round_dir = os.path.join(self.root, name, self.round_id)
        os.makedirs(round_dir, exist_ok=True)
        model_path = self.write_file(
            os.path.join(name, self.round_id, 'model.bin'), self.model_content)
        self.write_json(os.path.join(round_dir, 'model.manifest.json'), {
            'schema_version': 1,
            'artifact_type': 'model',
            'round_id': self.round_id,
            'size_bytes': len(self.model_content),
            'sha256': hashlib.sha256(self.model_content).hexdigest(),
            'participant_node_ids': [1, 2],
        })
        return model_path

    def make_valid_updates(self):
        updates = {}
        for node_id in (1, 2):
            path = os.path.join(self.root, 'updates', '%d.json' % node_id)
            self.write_json(path, {
                'schema_version': 1,
                'artifact_type': 'deterministic-update',
                'node_id': node_id,
                'participant_node_ids': [1, 2],
                'model_size_bytes': len(self.model_content),
                'model_sha256': hashlib.sha256(self.model_content).hexdigest(),
            })
            updates[node_id] = path
        return updates

    def write_file(self, name, content):
        path = name if os.path.isabs(name) else os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(content)
        return path

    def write_json(self, path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(value, fh)

    def read_json(self, path):
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)

    def sha256(self, path):
        digest = hashlib.sha256()
        with open(path, 'rb') as fh:
            digest.update(fh.read())
        return digest.hexdigest()

    def temporary_json_files(self, root):
        return [
            name for name in os.listdir(root)
            if name.startswith('.') and name.endswith('.json')
        ]


class ClientRuntimeStub(object):
    def __init__(self, work_dir, node_id, model_path, submitted):
        self.work_dir = work_dir
        self.node_id = node_id
        self.model_path = model_path
        self.submitted = submitted
        self.wait_count = 0
        self.submitted_paths = []

    def wait_for_model(self):
        self.wait_count += 1
        return self.model_path

    def submit_update(self, update_path):
        self.submitted_paths.append(update_path)
        target = os.path.join(
            self.work_dir, 'submitted', 'update-%d.json' % self.node_id)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(update_path, target)
        self.submitted[self.node_id] = target
        return None


class ServerRuntimeStub(object):
    def __init__(self, work_dir, participant_node_ids, updates):
        self.work_dir = work_dir
        self.participant_node_ids = participant_node_ids
        self.updates = updates
        self.published_models = []
        self.wait_count = 0

    def publish_model(self, model_path):
        self.published_models.append(model_path)
        return None

    def wait_for_updates(self):
        self.wait_count += 1
        return self.updates


if __name__ == '__main__':
    unittest.main()
