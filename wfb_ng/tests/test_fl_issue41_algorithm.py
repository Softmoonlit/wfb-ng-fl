#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
import uuid

from wfb_ng.fl.issue41_algorithm import client_main, server_main


class Issue41AlgorithmTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-issue41-algorithm-')
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_server_waits_for_complete_ordered_updates_and_writes_result(self):
        runtime = ServerFixtureRuntime(os.path.join(self.root, 'server'), (1, 2))
        result_path = os.path.join(self.root, 'server-result.json')

        server_main(runtime, {
            'rounds': 1,
            'participant_node_ids': [1, 2],
            'client_dataset_seeds': {'1': 'seed-1', '2': 'seed-2'},
            'result_path': result_path,
        })

        with open(result_path, 'r', encoding='utf-8') as fh:
            result = json.load(fh)
        self.assertEqual('succeeded', result['conclusion'])
        self.assertEqual([1, 2], result['rounds'][0]['update_node_ids'])
        self.assertLess(
            runtime.events.index('publish_model'),
            runtime.events.index('wait_for_updates'))

    def test_client_waits_for_model_delays_and_submits_deterministic_update(self):
        candidate = self.make_model_candidate((1, 2))
        runtime = ClientFixtureRuntime(os.path.join(self.root, 'client'), 2, candidate)
        result_path = os.path.join(self.root, 'client-result.json')

        client_main(runtime, {
            'rounds': 1,
            'node_id': 2,
            'training_delay_ms': 0,
            'client_dataset_seed': 'client-2-seed',
            'result_path': result_path,
        })

        with open(result_path, 'r', encoding='utf-8') as fh:
            result = json.load(fh)
        self.assertEqual('succeeded', result['conclusion'])
        self.assertEqual(2, result['node_id'])
        self.assertEqual(1, len(runtime.submitted_updates))
        submitted = runtime.submitted_updates[0]
        self.assertEqual(result['rounds'][0]['update_sha256'], sha256(submitted))
        with open(submitted, 'rb') as fh:
            payload = json.loads(fh.read().decode('utf-8'))
        self.assertEqual('client-2-seed', payload['client_dataset_seed'])
        self.assertEqual(2, payload['node_id'])

    def make_model_candidate(self, participant_node_ids):
        round_id = str(uuid.uuid4())
        candidate = os.path.join(self.root, 'candidate', round_id)
        os.makedirs(candidate)
        model_path = os.path.join(candidate, 'model.bin')
        with open(model_path, 'wb') as fh:
            fh.write(b'issue41-model')
        with open(os.path.join(candidate, 'model.manifest.json'), 'w',
                  encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'artifact_type': 'model',
                'round_id': round_id,
                'size_bytes': os.path.getsize(model_path),
                'sha256': sha256(model_path),
                'participant_node_ids': list(participant_node_ids),
            }, fh)
        return model_path


class ServerFixtureRuntime(object):
    def __init__(self, work_dir, participant_node_ids):
        self.work_dir = work_dir
        self.participant_node_ids = tuple(participant_node_ids)
        self.events = []

    def publish_model(self, model_path):
        self.events.append('publish_model')
        round_id = str(uuid.uuid4())
        os.makedirs(self.work_dir, exist_ok=True)
        with open(os.path.join(self.work_dir, 'current-round.json'), 'w',
                  encoding='utf-8') as fh:
            json.dump({'schema_version': 1, 'role': 'server', 'round_id': round_id}, fh)
        self.round_id = round_id
        self.model_sha256 = sha256(model_path)

    def wait_for_updates(self):
        self.events.append('wait_for_updates')
        updates = {}
        for node_id in self.participant_node_ids:
            path = os.path.join(self.work_dir, 'updates', str(node_id), 'update.bin')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = {
                'fixture_version': 'issue41-v1',
                'round_id': self.round_id,
                'node_id': node_id,
                'model_sha256': self.model_sha256,
                'client_dataset_seed': 'seed-%d' % node_id,
            }
            with open(path, 'wb') as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8') + b'\n')
            updates[node_id] = path
        return updates


class ClientFixtureRuntime(object):
    def __init__(self, work_dir, node_id, model_path):
        self.work_dir = work_dir
        self.node_id = node_id
        self.model_path = model_path
        self.submitted_updates = []

    def wait_for_model(self):
        return self.model_path

    def submit_update(self, update_path):
        self.submitted_updates.append(update_path)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == '__main__':
    unittest.main()
