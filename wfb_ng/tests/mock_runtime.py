#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Shared test harness and mock runtimes for Stage 2 FL coordinator testing."""

import threading
import time
from typing import Dict, List, Optional, Set

from wfb_ng.fl.errors import FLRuntimeError


class MockServerRuntime:
    """Deterministic Mock ServerRuntime implementing the ServerRuntime protocol."""

    def __init__(
        self,
        participant_node_ids: tuple,
        update_fixtures_by_node: Optional[Dict[int, str]] = None,
        simulate_straggler_nodes: Optional[Set[int]] = None,
        timeout_on_wait: bool = False,
    ):
        self.participant_node_ids = tuple(sorted(participant_node_ids))
        self.update_fixtures_by_node = update_fixtures_by_node or {}
        self.simulate_straggler_nodes = simulate_straggler_nodes or set()
        self.timeout_on_wait = timeout_on_wait

        self.published_models: List[str] = []
        self.active_round_model: Optional[str] = None
        self.round_id: Optional[str] = None
        self.wait_calls = 0
        self.aborted = False
        self.abort_reason: Optional[str] = None

        # Synchronization events for zero-sleep deterministic race-free testing
        self.waiting_updates_event = threading.Event()
        self.abort_event = threading.Event()

    def publish_model(self, model_path: str) -> None:
        if self.aborted:
            raise FLRuntimeError("aborted", "Runtime 已被中止")
        self.published_models.append(model_path)
        self.active_round_model = model_path
        self.round_id = f"round_{len(self.published_models)}"

    def wait_for_updates(
        self, min_updates: Optional[int] = None, timeout: Optional[float] = None
    ) -> Dict[int, str]:
        if self.aborted:
            raise FLRuntimeError("aborted", "Runtime 已被中止")
        self.wait_calls += 1
        self.waiting_updates_event.set()

        if self.timeout_on_wait:
            raise FLRuntimeError("round_timeout", f"轮次等待 update 超时 ({timeout}s)")

        min_req = min_updates if min_updates is not None else len(self.participant_node_ids)
        available = {}
        for nid in self.participant_node_ids:
            if nid in self.simulate_straggler_nodes:
                continue
            if nid in self.update_fixtures_by_node:
                available[nid] = self.update_fixtures_by_node[nid]

        if len(available) < min_req:
            raise FLRuntimeError(
                "round_timeout",
                f"轮次等待 update 超时，未达到最小配额: 期望 {min_req}, 实际 {len(available)}",
            )
        return dict(available)

    def abort(self, reason: str = "aborted") -> None:
        self.aborted = True
        self.abort_reason = reason
        self.abort_event.set()
