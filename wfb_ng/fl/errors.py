#!/usr/bin/env python
# -*- coding: utf-8 -*-


class FLRuntimeError(Exception):
    def __init__(self, error_code, error_message, round_id=None, node_id=None):
        super().__init__(error_message)
        self.error_code = error_code
        self.error_message = error_message
        self.round_id = round_id
        self.node_id = node_id
