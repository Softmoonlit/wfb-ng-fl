#!/usr/bin/env python
# -*- coding: utf-8 -*-

import io
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

    def test_unwritable_path_raises_oserror_without_fallback(self):
        conflict_file = os.path.join(self.root, 'conflict_file')
        with open(conflict_file, 'w', encoding='utf-8') as fh:
            fh.write('not a directory')
        bad_path = os.path.join(conflict_file, 'sub', 'observation.jsonl')
        with self.assertRaises(OSError):
            LiveObservation(True, path=bad_path)

    def test_writer_used_when_path_is_none(self):
        buf = io.StringIO()
        observation = LiveObservation(True, writer=buf, path=None)
        observation.emit('round_started', round_id='r1')
        observation.close()
        self.assertIn('round_started', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
