#!/usr/bin/env python3
"""Falsifiable pre-retune UDP residue probe; only public client hunt/send calls.

Run from repository root: PYTHONPATH=. python3 <this-file>
No RF/TUN/production endpoints; no sleeps, fake sockets, or production patches.
Private socket reference is used solely for non-consuming queue observation and
cleanup; every heartbeat and ACK consumption uses the real public client path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import threading
import time

from wfb_ng.fl import control
from wfb_ng.fl.control import ControlPlaneClient, HeartbeatAck

GUARD_SECONDS = 2.0  # Event/thread failure guard, never a client timeout override.


def require_event(event, label):
    if not event.wait(GUARD_SECONDS):
        raise RuntimeError(f"barrier failed: {label}")


def run_once(index):
    entered_149 = threading.Event()
    old_ack_sent = threading.Event()
    release_149 = threading.Event()
    server_done = threading.Event()
    hunt_done = threading.Event()
    trace = []
    trace_lock = threading.Lock()
    errors = []
    received = []
    hunt_result = []
    start = time.monotonic_ns()

    def record(event, **fields):
        with trace_lock:
            trace.append({"event": event, "elapsed_ms": round(
                (time.monotonic_ns() - start) / 1e6, 3), **fields})

    class ChannelAdapter:
        def set_channel(self, interface, *, channel, channel_width):
            record("set_channel_enter", channel=channel)
            if channel == 149:
                entered_149.set()
                require_event(release_149, "release set_channel149")
            record("set_channel_return", channel=channel)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.settimeout(GUARD_SECONDS)
    server_addr = server_sock.getsockname()
    client = ControlPlaneClient(
        node_id=2, tun_ip="127.0.0.1", network_adapter=ChannelAdapter(),
        air_interface="diagnostic-channel-only", server_host="127.0.0.1",
        server_port=server_addr[1], broadcast_port=0,
    )
    assert client.attempt_timeout_seconds == 0.23
    assert client.max_attempts_per_channel == 2

    def serve():
        try:
            # 2 HUNT157 + 2 HUNT149 + 1 explicit public send on current149.
            for count in range(1, 6):
                payload, source = server_sock.recvfrom(4096)
                body = json.loads(payload)
                received.append(body)
                record("server_receive_hb", count=count,
                       channel=body["current_channel"], state=body["state"])
                if count == 2:
                    assert body["current_channel"] == 157
                    require_event(entered_149, "retune149 entered")
                    ack = HeartbeatAck().to_bytes()
                    sent_bytes = server_sock.sendto(ack, source)
                    assert sent_bytes == len(ack)
                    record("old157_ack_kernel_sendto_complete", bytes=sent_bytes,
                           origin_hb_count=2, origin_channel=157)
                    old_ack_sent.set()
                # All channel149 heartbeats deliberately get no ACK.
        except BaseException as exc:
            errors.append(f"server: {type(exc).__name__}: {exc}")
        finally:
            server_done.set()

    def hunt():
        try:
            hunt_result.append(client.hunt_once(ladder=[157, 149]))
            record("public_hunt_return", result=hunt_result[-1],
                   state=client.state.value, locked_channel=client.locked_channel)
        except BaseException as exc:
            errors.append(f"hunt: {type(exc).__name__}: {exc}")
        finally:
            hunt_done.set()

    server_thread = threading.Thread(target=serve, daemon=True)
    hunt_thread = threading.Thread(target=hunt, daemon=True)
    server_thread.start()
    hunt_thread.start()
    try:
        require_event(entered_149, "retune149 entered")
        require_event(old_ack_sent, "old ACK sendto completed")
        # Stronger than sendto completion: prove exact bytes already queued on
        # the real receiving socket while set_channel149 is still blocked.
        sock = client._uplink_sock
        assert sock is not None
        queued, source = sock.recvfrom(4096, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        assert queued == HeartbeatAck().to_bytes()
        assert source == server_addr
        assert client.current_channel == 157
        record("old_ack_peeked_before_retune_complete", payload=queued.decode(),
               client_current_channel=client.current_channel,
               retune_complete=False)
        release_149.set()
        require_event(hunt_done, "public hunt completed")
        if errors:
            raise RuntimeError(errors)
        state_after_hunt = client.state.value
        lock_after_hunt = client.locked_channel
        # Exercise the public send method too, without changing its 230ms wait.
        ack = client.send_heartbeat_once()
        record("public_send_heartbeat_return", ack=ack is not None,
               state=client.state.value, locked_channel=client.locked_channel)
        require_event(server_done, "server collected five heartbeats")
        if errors:
            raise RuntimeError(errors)
        channels = [body["current_channel"] for body in received]
        red = bool(hunt_result[0] and state_after_hunt == "IDLE"
                   and lock_after_hunt == 149)
        assert channels == [157, 157, 149, 149, 149], channels
        return {
            "run": index, "verdict": "RED_FALSE_LOCK149" if red else "NOT_REPRODUCED",
            "server_address": server_addr,
            "client_address": sock.getsockname(),
            "attempt_timeout_seconds": client.attempt_timeout_seconds,
            "max_attempts_per_channel": client.max_attempts_per_channel,
            "hunt_result": hunt_result[0], "state_after_hunt": state_after_hunt,
            "locked_channel_after_hunt": lock_after_hunt,
            "public_send_ack": ack is not None,
            "heartbeat_channels": channels, "acks_sent": 1,
            "queued_old_ack_confirmed_before_retune_complete": True,
            "trace": trace,
        }
    finally:
        release_149.set()
        hunt_thread.join(GUARD_SECONDS)
        server_thread.join(GUARD_SECONDS)
        if client._uplink_sock is not None:
            client._uplink_sock.close()
        server_sock.close()
        if hunt_thread.is_alive() or server_thread.is_alive():
            raise RuntimeError("diagnostic worker did not stop")


def main():
    rows = [run_once(index) for index in range(1, 4)]
    source = Path(control.__file__)
    output = {
        "scope": "independent control protocol, pre-retune local UDP receive residue",
        "control_source": str(source),
        "control_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "runs": rows,
        "red_runs": sum(row["verdict"] == "RED_FALSE_LOCK149" for row in rows),
        "total_runs": len(rows),
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    # A red is a diagnostic result, not a harness failure. Failures of barriers
    # or unexpected traffic above are harness failures and must not become red.


if __name__ == "__main__":
    main()
