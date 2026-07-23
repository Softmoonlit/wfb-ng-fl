#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
import shutil
import tempfile
import unittest

from wfb_ng.fl.observation import EVENT_PREFIX, LiveObservation


class LiveObservationTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-observation-')
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_persists_structured_event_to_configured_path(self):
        path = os.path.join(self.root, 'nested', 'observation.jsonl')
        observation = LiveObservation(True, path=path)
        observation.emit('upload_accepted', node_id=1, round='round-1')
        observation.close()

        with open(path, encoding='utf-8') as fh:
            line = fh.read()
        self.assertTrue(line.startswith(EVENT_PREFIX))
        self.assertEqual(
            {'event': 'upload_accepted', 'node_id': 1, 'round': 'round-1'},
            json.loads(line[len(EVENT_PREFIX):]))


if __name__ == '__main__':
    unittest.main()
