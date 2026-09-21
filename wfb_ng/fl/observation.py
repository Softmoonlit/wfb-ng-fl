#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
import sys
import threading


EVENT_PREFIX = 'WFB_FL_EVENT '


class LiveObservation(object):
    def __init__(self, enabled=False, writer=None, path=None):
        self.enabled = enabled
        self._lock = threading.Lock()
        self._owned_writer = None
        if enabled and path is not None:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._owned_writer = open(path, 'a', encoding='utf-8')
            self.writer = self._owned_writer
        else:
            self.writer = writer or sys.stdout

    def emit(self, event, **fields):
        if not self.enabled or self.writer is None:
            return
        value = {'event': event}
        value.update(fields)
        try:
            with self._lock:
                self.writer.write(EVENT_PREFIX + json.dumps(
                    value, ensure_ascii=True, sort_keys=True,
                    separators=(',', ':')) + '\n')
                self.writer.flush()
        except Exception:
            pass

    def close(self):
        if self._owned_writer is not None:
            self._owned_writer.close()
            self._owned_writer = None
