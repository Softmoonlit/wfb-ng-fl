#!/usr/bin/env python3
"""Controlled, hardware-free liveness loop for the monotonic evidence."""
from __future__ import annotations

import json
import time

from wfb_ng.fl.control import ClientNodeState, ControlPlaneServer, NodeHeartbeat
from wfb_ng.fl.radio import RadioConfig

SCALE = 0.01
SERVER_THRESHOLD = 10.0
NATIVE_GAP = 2.444


def heartbeat(state: str, timestamp_ms: int) -> NodeHeartbeat:
    return NodeHeartbeat(
        node_id=2, state=state, elapsed_ms=0, current_channel=157,
        txpower_dbm=12, uplink_mcs=6, timestamp_ms=timestamp_ms,
    )


def main() -> None:
    exact_last_hb = 57333.316
    exact_offline = 57343.316
    arithmetic = {
        "server_last_hb_monotonic": exact_last_hb,
        "server_offline_monotonic": exact_offline,
        "native_gap_seconds": NATIVE_GAP,
        "gap_end_monotonic": exact_last_hb + NATIVE_GAP,
        "remaining_to_offline_seconds": exact_offline - exact_last_hb - NATIVE_GAP,
        "fl02_wall_clock_lead_seconds": 3.064,
    }
    assert arithmetic["remaining_to_offline_seconds"] > 0

    server = ControlPlaneServer(
        active_radio_config=RadioConfig(
            channel=157, radio_txpower_dbm=12, uplink_mcs=6, downlink_mcs=3,
        ),
        offline_threshold_seconds=SERVER_THRESHOLD * SCALE,
    )
    address = ("diag-node2", 1)
    server.registry.update_heartbeat(
        heartbeat(ClientNodeState.HUNTING.value, 1000), address, server.active_radio_config
    )
    server.registry.update_heartbeat(
        heartbeat(ClientNodeState.IDLE.value, 1001), address, server.active_radio_config
    )
    events = []
    server.watch_job_liveness("handoff", [2], events.append)
    try:
        time.sleep(NATIVE_GAP * SCALE)
        server.registry.update_heartbeat(
            heartbeat(ClientNodeState.PREPARING.value, 1002), address, server.active_radio_config
        )
        time.sleep(0.02)
        handoff_events = list(events)

        time.sleep((SERVER_THRESHOLD * SCALE) + 0.03)
        deadline = time.monotonic() + 0.5
        while not any(event.get("state") == "offline" for event in events):
            if time.monotonic() >= deadline:
                raise AssertionError("scaled pure-idle silence did not emit offline")
            time.sleep(0.005)
        pure_idle_events = list(events)
    finally:
        server.unwatch_job_liveness("handoff")
        server.stop()

    result = {
        "arithmetic": arithmetic,
        "scaled_threshold_seconds": SERVER_THRESHOLD * SCALE,
        "scaled_native_gap_seconds": NATIVE_GAP * SCALE,
        "handoff_events_before_idle_silence": handoff_events,
        "pure_idle_events": pure_idle_events,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    assert not any(event.get("state") == "offline" for event in handoff_events)
    assert any(event.get("state") == "offline" for event in pure_idle_events)


if __name__ == "__main__":
    main()
