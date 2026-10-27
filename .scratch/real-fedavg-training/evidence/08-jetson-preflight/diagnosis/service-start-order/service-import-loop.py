#!/usr/bin/env python3
"""Temporary red loop for service.main import-before-link ordering.

Uses the public service.main entry, the real dynamic load_algorithm path, and a
fake RoleService seam. No production files, hardware, TUN, or network sockets
are touched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time

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
    with tempfile.TemporaryDirectory(prefix="wfb-diag-service-") as root:
        root_path = Path(root)
        module_name = "diag_slow_entry"
        (root_path / (module_name + ".py")).write_text(
            "import os\n"
            "import time\n"
            "with open(os.environ['DIAG_IMPORT_MARKER'], 'w', encoding='ascii') as marker:\n"
            "    marker.write(str(time.monotonic()))\n"
            "time.sleep(0.25)\n"
            "def entry(runtime, config, context):\n"
            "    return None\n",
            encoding="ascii",
        )
        import_marker = root_path / "algorithm-import.marker"
        os.environ["DIAG_IMPORT_MARKER"] = str(import_marker)
        sys.path.insert(0, str(root_path))
        sys.modules.pop(module_name, None)

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
        algorithm_config_path.write_text("{}", encoding="ascii")
        context_path = root_path / "context.json"
        context_path.write_text(
            json.dumps({
                "job_id": "diag-job",
                "node_id": 2,
                "target_nodes": [2],
                "rounds": 1,
                "artifact_dir": str(root_path / "artifacts"),
                "local_config": {},
                "preparation_timeout_seconds": 1.0,
            }),
            encoding="ascii",
        )

        events = [("idle_link_stopped", time.monotonic())]
        fake = FakeRoleService(events)
        original_loader = service.load_role_service
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
        try:
            rc = service.main("client") if False else None
            old_argv = sys.argv
            sys.argv = [
                "wfb-role-service",
                "--config", str(role_path),
                "--job-context", str(context_path),
                "--algorithm", module_name + ":entry",
                "--algorithm-config", str(algorithm_config_path),
            ]
            try:
                rc = service.main("client")
            finally:
                sys.argv = old_argv
        finally:
            service.load_role_service = original_loader
            if sys.path and sys.path[0] == str(root_path):
                sys.path.pop(0)

        stopper.join(timeout=1.0)
        link_started = next((t for name, t in events if name == "task_link_started"), None)
        import_started = float(import_marker.read_text(encoding="ascii"))
        idle_stopped = events[0][1]
        print(json.dumps({
            "returncode": rc,
            "idle_link_stopped": round(idle_stopped - started, 3),
            "algorithm_import_started": round(import_started - started, 3),
            "stop_requested": round(stop_requested.get("at", started) - started, 3),
            "task_link_started": None if link_started is None else round(link_started - started, 3),
            "slow_import_seconds": 0.25,
            "stop_deadline_seconds": stop_deadline,
            "events": [(name, round(ts - started, 3)) for name, ts in events],
        }, sort_keys=True))

        assert stop_requested, "SIGTERM stop request was not observed"
        assert import_marker.exists(), "slow algorithm import marker was not written"
        assert link_started is not None, "task link never reached RoleService.start"
        assert link_started <= import_started, (
            "RED: service.main started the task link after load_algorithm/import"
        )
        assert link_started <= stop_requested["at"] + stop_deadline, (
            "RED: service.main loaded the heavy algorithm before starting the task link; "
            f"link_start={link_started - started:.3f}s, "
            f"stop_deadline={(stop_requested['at'] + stop_deadline) - started:.3f}s"
        )


if __name__ == "__main__":
    main()
