#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from wfb_ng.fl import issue41_algorithm
from wfb_ng.fl.issue41_algorithm import client_main, server_main


MODEL_SIZE_BYTES = 40 * 1024 * 1024


class Issue41AlgorithmTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-issue41-algorithm-')
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_server_republishes_unchanged_model_after_placeholder_aggregation(self):
        initial_model = self.make_binary_file(
            'input/model.bin', MODEL_SIZE_BYTES, b'model-content')
        runtime = ServerOpaqueRuntime(
            os.path.join(self.root, 'server'), (1, 2))
        result_path = os.path.join(self.root, 'server-result.json')

        with mock.patch.object(issue41_algorithm.time, 'sleep') as sleep:
            server_main(runtime, {
                'rounds': 2,
                'participant_node_ids': [1, 2],
                'initial_model_path': initial_model,
                'aggregation_delay_ms': 25,
                'result_path': result_path,
            })

        self.assertEqual(
            ['publish_model', 'wait_for_updates'] * 2,
            runtime.events)
        self.assertEqual(2, len(runtime.published_models))
        self.assertEqual(MODEL_SIZE_BYTES, os.path.getsize(runtime.published_models[0]))
        self.assertEqual(
            sha256(runtime.published_models[0]),
            sha256(runtime.published_models[1]))
        self.assertEqual([mock.call(0.025), mock.call(0.025)], sleep.call_args_list)

        result = read_json(result_path)
        self.assertEqual('succeeded', result['conclusion'])
        self.assertEqual(MODEL_SIZE_BYTES, result['initial_model_size_bytes'])
        self.assertEqual([1, 2], result['rounds'][0]['update_node_ids'])
        self.assertEqual(
            result['rounds'][0]['input_model_sha256'],
            result['rounds'][0]['output_model_sha256'])
        self.assertEqual(
            result['rounds'][0]['output_model_path'],
            result['rounds'][1]['input_model_path'])
        self.assertIn('aggregate_start', event_names(result))
        self.assertIn('aggregate_done', event_names(result))

    def test_client_submits_opaque_update_template_after_training_delay(self):
        model_path = self.make_binary_file(
            'candidate/model.bin', MODEL_SIZE_BYTES, b'opaque-model')
        update_template = self.make_binary_file(
            'input/update.params', 1024 * 1024, b'opaque-update')
        runtime = ClientOpaqueRuntime(
            os.path.join(self.root, 'client'), 2, model_path)
        result_path = os.path.join(self.root, 'client-result.json')

        with mock.patch.object(issue41_algorithm.time, 'sleep') as sleep:
            client_main(runtime, {
                'rounds': 1,
                'node_id': 2,
                'training_delay_ms': 30,
                'update_template_path': update_template,
                'result_path': result_path,
            })

        sleep.assert_called_once_with(0.03)
        self.assertEqual(1, len(runtime.submitted_updates))
        submitted = runtime.submitted_updates[0]
        self.assertEqual(os.path.getsize(update_template), os.path.getsize(submitted))
        self.assertEqual(sha256(update_template), sha256(submitted))

        result = read_json(result_path)
        self.assertEqual('succeeded', result['conclusion'])
        self.assertEqual(2, result['node_id'])
        self.assertEqual(MODEL_SIZE_BYTES, result['rounds'][0]['model_size_bytes'])
        self.assertEqual(sha256(submitted), result['rounds'][0]['update_sha256'])
        self.assertEqual(
            ['wait_for_model_start', 'wait_for_model_done',
             'train_start', 'train_done',
             'submit_update_start', 'submit_update_done'],
            event_names(result))

    def test_client_records_round_identity_and_immediate_40mib_template_copy(self):
        model_path = self.make_binary_file(
            'candidate/model.bin', MODEL_SIZE_BYTES, b'opaque-model')
        update_template = self.make_binary_file(
            'input/update-node-1.params', MODEL_SIZE_BYTES, b'node-1-update')
        runtime = ClientOpaqueRuntime(
            os.path.join(self.root, 'client'), 1, model_path)
        result_path = os.path.join(self.root, 'client-result.json')

        with mock.patch.object(issue41_algorithm.time, 'sleep') as sleep:
            client_main(runtime, {
                'rounds': 2,
                'node_id': 1,
                'training_delay_ms': 0,
                'required_artifact_size_bytes': MODEL_SIZE_BYTES,
                'update_template_path': update_template,
                'result_path': result_path,
            })

        sleep.assert_not_called()
        result = read_json(result_path)
        self.assertEqual(2, len(result['rounds']))
        self.assertNotEqual(
            result['rounds'][0]['round_id'], result['rounds'][1]['round_id'])
        self.assertEqual(MODEL_SIZE_BYTES, result['rounds'][0]['model_size_bytes'])
        self.assertEqual(MODEL_SIZE_BYTES, result['rounds'][0]['update_size_bytes'])
        self.assertLessEqual(
            result['rounds'][0]['model_receive_interval']['start'],
            result['rounds'][0]['model_receive_interval']['end'])
        self.assertLessEqual(
            result['rounds'][0]['put_interval']['start'],
            result['rounds'][0]['put_interval']['end'])

    def test_required_input_files_fail_before_runtime_operations(self):
        server_runtime = ServerOpaqueRuntime(
            os.path.join(self.root, 'server'), (1, 2))
        with self.assertRaisesRegex(Exception, 'initial_model_path'):
            server_main(server_runtime, {
                'rounds': 1,
                'participant_node_ids': [1, 2],
            })
        self.assertEqual([], server_runtime.events)

        client_runtime = ClientOpaqueRuntime(
            os.path.join(self.root, 'client'), 1,
            self.make_binary_file('candidate/model.bin', 16, b'model'))
        with self.assertRaisesRegex(Exception, 'update_template_path'):
            client_main(client_runtime, {'rounds': 1, 'node_id': 1})
        self.assertEqual(0, client_runtime.wait_count)

    def make_binary_file(self, relative_path, size_bytes, pattern):
        path = os.path.join(self.root, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        chunk = (pattern * ((64 * 1024 // len(pattern)) + 1))[:64 * 1024]
        with open(path, 'wb') as fh:
            remaining = size_bytes
            while remaining:
                current = chunk[:remaining]
                fh.write(current)
                remaining -= len(current)
        return path


class ServerOpaqueRuntime(object):
    def __init__(self, work_dir, participant_node_ids):
        self.work_dir = work_dir
        self.participant_node_ids = tuple(participant_node_ids)
        self.events = []
        self.published_models = []
        self.round_index = 0

    def publish_model(self, model_path):
        self.events.append('publish_model')
        self.published_models.append(model_path)
        self.round_index += 1

    def wait_for_updates(self):
        self.events.append('wait_for_updates')
        updates = {}
        for node_id in self.participant_node_ids:
            path = os.path.join(
                self.work_dir, 'updates', str(self.round_index),
                str(node_id), 'update.bin')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as fh:
                fh.write(
                    b'opaque-update-node-' + str(node_id).encode('ascii'))
            with open(os.path.join(os.path.dirname(path), 'update.manifest.json'), 'w', encoding='utf-8') as fh:
                json.dump({'round_id': 'test-server-round-%d' % self.round_index}, fh)
            updates[node_id] = path
        return updates


class ClientOpaqueRuntime(object):
    def __init__(self, work_dir, node_id, model_path):
        self.work_dir = work_dir
        self.node_id = node_id
        self.model_path = model_path
        self.submitted_updates = []
        self.wait_count = 0

    def wait_for_model(self):
        self.wait_count += 1
        round_id = 'test-round-%d' % self.wait_count
        round_dir = os.path.join(self.work_dir, 'rounds', round_id)
        os.makedirs(round_dir, exist_ok=True)
        model_path = os.path.join(round_dir, 'model.bin')
        shutil.copyfile(self.model_path, model_path)
        with open(os.path.join(round_dir, 'model.manifest.json'), 'w', encoding='utf-8') as fh:
            json.dump({'round_id': round_id}, fh)
        return model_path

    def submit_update(self, update_path):
        self.submitted_updates.append(update_path)


def read_json(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def event_names(result):
    return [event['name'] for event in result['events']]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == '__main__':
    unittest.main()
