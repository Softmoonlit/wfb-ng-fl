#!/usr/bin/env python3
"""ControlPlaneClient timing loop for idle ACK budget vs fastscan.

Uses only ephemeral localhost UDP sockets. No field ports, TUN, radio, or
hardware. The delayed server returns a minimal real HeartbeatAck after a fixed
per-datagram delay.
"""
from __future__ import annotations

import contextlib
import json
import socket
import threading
import time

from wfb_ng.fl.control import ClientNodeState, ControlPlaneClient, HeartbeatAck

ATTEMPT_TIMEOUT = 0.23


class DropSocket:
    """Socket seam for deterministic idle two-miss state transition."""

    def settimeout(self, value):
        self.timeout = value

    def setblocking(self, value):
        self.blocking = value

    def sendto(self, payload, address):
        return len(payload)

    def recvfrom(self, size):
        if getattr(self, "blocking", True) is False:
            raise BlockingIOError
        raise socket.timeout()

    def close(self):
        pass


class DelayedAckServer:
    def __init__(self, delay):
        self.delay = delay
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.02)
        self.received = []
        self.sent = []
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    @property
    def address(self):
        return self.sock.getsockname()

    def _run(self):
        while not self.stop.is_set():
            try:
                payload, source = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            self.received.append((time.monotonic(), json.loads(payload.decode("utf-8"))))
            time.sleep(self.delay)
            try:
                self.sock.sendto(HeartbeatAck(ack=True).to_bytes(), source)
                self.sent.append(time.monotonic())
            except OSError:
                return

    def close(self):
        self.stop.set()
        self.sock.close()
        self.worker.join(1.0)


def run_fastscan(delay: float) -> dict:
    server = DelayedAckServer(delay)
    client = ControlPlaneClient(
        node_id=2,
        tun_ip="10.80.0.12",
        server_host="127.0.0.1",
        server_port=server.address[1],
        attempt_timeout_seconds=ATTEMPT_TIMEOUT,
        max_attempts_per_channel=2,
        network_adapter=None,
        air_interface=None,
    )
    started = time.monotonic()
    try:
        result = client.hunt_once(ladder=[157])
        elapsed = time.monotonic() - started
    finally:
        with contextlib.suppress(Exception):
            if client._uplink_sock is not None:
                client._uplink_sock.close()
        server.close()
    return {
        "delay_seconds": delay,
        "attempt_timeout_seconds": ATTEMPT_TIMEOUT,
        "result": result,
        "state": client.state.value,
        "elapsed_seconds": round(elapsed, 3),
        "heartbeats_received": len(server.received),
        "acks_sent": len(server.sent),
        "heartbeat_attempts": [
            {"state": body.get("state"), "channel": body.get("current_channel")}
            for _, body in server.received
        ],
    }


def run_idle_two_misses() -> dict:
    client = ControlPlaneClient(
        node_id=2,
        tun_ip="10.80.0.12",
        server_host="127.0.0.1",
        server_port=0,
        attempt_timeout_seconds=ATTEMPT_TIMEOUT,
        idle_interval_seconds=5.0,
        network_adapter=None,
        air_interface=None,
    )
    client.state = ClientNodeState.IDLE
    client._uplink_sock = DropSocket()
    first = client.send_heartbeat_once()
    second = client.send_heartbeat_once()
    client._uplink_sock.close()
    return {
        "first_ack": first is not None,
        "second_ack": second is not None,
        "missed_acks": client.missed_acks,
        "state_after_two_misses": client.state.value,
        "idle_interval_seconds": client.idle_interval_seconds,
        "nominal_two_miss_interval_seconds": client.idle_interval_seconds * 2,
        "per_attempt_wait_seconds": ATTEMPT_TIMEOUT,
    }


def main():
    idle = run_idle_two_misses()
    fastscan = [run_fastscan(delay) for delay in (0.30, 0.35, 0.40, 0.47)]
    result = {"idle_budget": idle, "fastscan": fastscan}
    print(json.dumps(result, indent=2, sort_keys=True))

    # At 0.30-0.40s, attempt 1 times out at 0.23s but its ACK arrives while
    # attempt 2 is waiting. The current protocol has no request/attempt id.
    red = [row for row in fastscan[:3] if row["result"] is True]
    assert red, "RED loop did not observe cross-attempt late-ACK acceptance"
    assert idle["state_after_two_misses"] == "HUNTING"
    if red:
        raise AssertionError(
            "RED: late attempt-1 ACK accepted during attempt-2 wait"
        )


if __name__ == "__main__":
    main()
