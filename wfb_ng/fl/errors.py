#!/usr/bin/env python
# -*- coding: utf-8 -*-


from typing import Any


class FLRuntimeError(Exception):
    def __init__(
        self,
        error_code: str,
        error_message: str,
        round_id: Any = None,
        node_id: Any = None,
        details: Any = None,
    ):
        super().__init__(error_message)
        self.error_code = error_code
        self.error_message = error_message
        self.round_id = round_id
        self.node_id = node_id
        self.details = details
