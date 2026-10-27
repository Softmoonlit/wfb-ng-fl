#!/usr/bin/env python3
"""Real mnist import red loop for service.main, with transport faked."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time

from wfb_ng.fl import algorithm as algorithm_module
from wfb_ng.fl import service


class FakeRoleService:
    def __init__(self, events):
        self.runtime = object()
        self.events = events

    def start(self):
        self.events.append(("task_link_started", time.monotonic()))
        return self.runtime

    def wait(self, interval=0.2):
        time.sleep(min(float(interval), 0.005))

    def close(self):
        self.events.append(("task_link_closed", time.monotonic()))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="wfb-diag-real-mnist-") as root:
        root_path = Path(root)
        role_config = {
            "schema_version": 1,
            "role": "client",
            "work_dir": str(root_path / "role"),
            "node_id": 2,
            "uftp_uid": 2,
            "uftp_port": 19044,
            "server_http_host": "10.80.0.1",
            "server_http_port": 18080,
            "uftp_bind_host": "10.80.0.12",
            "uftp_multicast_host": "239.80.41.1",
            "uftp_private_multicast_host": "239.80.41.2",
            "channel": 157,
            "channel_width": "HT40+",
            "radio_txpower_dbm": 14,
            "max_update_size_bytes": 1024,
            "live_observation": False,
            "observation_path": None,
            "io_timeout_seconds": 1.0,
            "link_args": ["--tun-name", "diag-tun-not-real"],
        }
        role_path = root_path / "role.json"
        role_path.write_text(json.dumps(role_config), encoding="ascii")
        algorithm_config_path = root_path / "algorithm.json"
        algorithm_config_path.write_text(json.dumps({"data_seed": 2026}), encoding="ascii")
        context_path = root_path / "context.json"
        context_path.write_text(json.dumps({
            "job_id": "diag-job",
            "node_id": 2,
            "target_nodes": [2],
            "rounds": 1,
            "artifact_dir": str(root_path / "artifacts"),
            "local_config": {},
            "preparation_timeout_seconds": 1.0,
        }), encoding="ascii")

        events = [("idle_link_stopped", time.monotonic())]
        fake = FakeRoleService(events)
        real_loader = algorithm_module.load_algorithm
        real_role_loader = service.load_role_service

        def traced_loader(spec, *args, **kwargs):
            events.append(("algorithm_import_started", time.monotonic()))
            result = real_loader(spec, *args, **kwargs)
            events.append(("algorithm_import_finished", time.monotonic()))
            return result

        algorithm_module.load_algorithm = traced_loader
        service.load_role_service = lambda path, expected_role=None: fake
        os.environ.pop("WFB_FL_NOTIFY_SOCKET", None)
        stop_deadline = 0.05
        stop_requested = {}

        def request_stop():
            time.sleep(0.03)
            stop_requested["at"] = time.monotonic()
            os.kill(os.getpid(), signal.SIGTERM)

        stopper = threading.Thread(target=request_stop, daemon=True)
        stopper.start()
        started = time.monotonic()
        old_argv = sys.argv
        sys.argv = [
            "wfb-role-service",
            "--config", str(role_path),
            "--job-context", str(context_path),
            "--algorithm", "wfb_ng.fl.mnist:client_entry",
            "--algorithm-config", str(algorithm_config_path),
        ]
        try:
            rc = service.main("client")
        finally:
            sys.argv = old_argv
            algorithm_module.load_algorithm = real_loader
            service.load_role_service = real_role_loader

        stopper.join(timeout=1.0)
        link_started = next(t for name, t in events if name == "task_link_started")
        import_started = next(t for name, t in events if name == "algorithm_import_started")
        import_finished = next(t for name, t in events if name == "algorithm_import_finished")
        print(json.dumps({
            "returncode": rc,
            "idle_link_stopped": round(events[0][1] - started, 3),
            "algorithm_import_started": round(import_started - started, 3),
            "algorithm_import_finished": round(import_finished - started, 3),
            "task_link_started": round(link_started - started, 3),
            "stop_requested": round(stop_requested["at"] - started, 3),
            "stop_deadline_seconds": stop_deadline,
            "events": [(name, round(ts - started, 3)) for name, ts in events],
        }, sort_keys=True))

        assert link_started <= import_started, (
            "RED: public RoleService.main starts task link after real mnist import"
        )
        assert link_started <= stop_requested["at"] + stop_deadline, (
            "RED: public RoleService.main misses stop deadline while importing mnist"
        )


if __name__ == "__main__":
    main()
