#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import sys


EVENT_PREFIX = 'WFB_FL_EVENT '


class LiveObservation(object):
    def __init__(self, enabled=False, writer=None):
        self.enabled = enabled
        self.writer = writer if writer is not None else sys.stdout

    def emit(self, event, **fields):
        if not self.enabled:
            return
        value = {'event': event}
        value.update(fields)
        try:
            self.writer.write(EVENT_PREFIX + json.dumps(
                value, ensure_ascii=True, sort_keys=True,
                separators=(',', ':')) + '\n')
            self.writer.flush()
        except Exception:
            pass
