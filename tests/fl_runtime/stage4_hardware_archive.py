#!/usr/bin/env python3
"""Stage 4 三机实体验收归档契约与 fail-closed 校验器。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wfb_ng.fl.artifacts import write_json_atomic

PARTITIONS = ("management-web", "control-plane", "data-plane", "lifecycle", "summary")
STAGES = ("preflight", "web-upload", "normal-job", "abort-job", "collect", "summary")
RUN_ID = re.compile(r"stage4-hardware-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
SHA256 = re.compile(r"[0-9a-f]{64}$")
CANONICAL_MODEL_SHA256 = "a544c81f86c7a9e089dc45b3b0d3ff6490b933bb76153d8a8478c1a7703c7841"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def init_archive(root: Path, *, run_id: str, commit: str, web_url: str) -> dict[str, Any]:
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("invalid stage4 hardware run_id")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("invalid commit")
    root.mkdir(parents=True, exist_ok=False)
    for partition in PARTITIONS:
        (root / partition).mkdir()
    envelope = {
        "schema_version": 1,
        "kind": "stage4-hardware-acceptance",
        "run_id": run_id,
        "commit": commit,
        "web_url": web_url,
        "topology": {"server": "local", "clients": [{"alias": "vm1", "node_id": 1}, {"alias": "vm2", "node_id": 2}]},
        "control_plane": {"downlink": "255.255.255.255:9000", "uplink": "10.80.0.1:9001"},
        "data_plane": {"uftp": "udp:1044", "multicast": ["239.80.41.1", "239.80.41.2"], "http_put": "wireless-data-plane"},
        "acceptance_contract": {"interface_prefix": "wl*", "ssh_during_job_window": False, "management_http_is_not_control_plane": True,
                                "fixture_size_bytes": 40 * 1024 * 1024},
        "stages": [], "completed": False,
    }
    write_json_atomic(root / "envelope.json", envelope)
    return envelope


def record_stage(root: Path, stage: str, *, status: str, detail: dict[str, Any] | None = None,
                 command: list[str] | None = None, returncode: int | None = None,
                 stdout: str = "", stderr: str = "") -> None:
    envelope = _json(root / "envelope.json")
    if stage not in STAGES or len(envelope["stages"]) >= len(STAGES) or stage != STAGES[len(envelope["stages"])]:
        raise ValueError(f"invalid stage order: {stage}")
    if status not in {"passed", "failed", "skipped"}:
        raise ValueError("invalid stage status")
    entry = {"stage": stage, "status": status, "command": command or [], "returncode": returncode,
             "recorded_at": datetime.now(timezone.utc).isoformat(), "detail": detail or {}}
    partition = {"preflight": "summary", "web-upload": "management-web", "normal-job": "data-plane",
                 "abort-job": "control-plane", "collect": "lifecycle", "summary": "summary"}[stage]
    write_json_atomic(root / partition / f"{len(envelope['stages']) + 1:02d}-{stage}.json",
                      {**entry, "stdout": stdout, "stderr": stderr})
    envelope["stages"].append(entry)
    write_json_atomic(root / "envelope.json", envelope)


def seal_archive(root: Path, *, conclusion: str, failure_boundary: str | None = None) -> None:
    envelope = _json(root / "envelope.json")
    if tuple(x.get("stage") for x in envelope["stages"]) != STAGES:
        raise ValueError("all stages are required before sealing")
    envelope["completed"] = True
    envelope["conclusion"] = conclusion
    if failure_boundary:
        envelope["failure_boundary"] = failure_boundary
    write_json_atomic(root / "envelope.json", envelope)
    files: dict[str, Any] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in {"archive_manifest.json", "validation.json"}:
            files[path.relative_to(root).as_posix()] = {"size_bytes": path.stat().st_size, "sha256": _digest(path)}
    write_json_atomic(root / "archive_manifest.json", {"schema_version": 1, "run_id": envelope["run_id"], "files": files})


def validate_archive(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        envelope = _json(root / "envelope.json")
        if envelope.get("kind") != "stage4-hardware-acceptance" or not RUN_ID.fullmatch(envelope.get("run_id", "")):
            raise ValueError("invalid envelope identity")
        if not envelope.get("completed") or envelope.get("conclusion") != "passed":
            raise ValueError("hardware acceptance is not passed")
        if tuple(x.get("stage") for x in envelope.get("stages", [])) != STAGES:
            raise ValueError("stage sequence mismatch")
        if envelope.get("acceptance_contract", {}).get("interface_prefix") != "wl*":
            raise ValueError("wireless interface must be dynamically discovered")
        if envelope.get("acceptance_contract", {}).get("ssh_during_job_window") is not False:
            raise ValueError("SSH job-window contract missing")
        if envelope.get("control_plane") != {"downlink": "255.255.255.255:9000", "uplink": "10.80.0.1:9001"}:
            raise ValueError("control-plane contract mismatch")
        if envelope.get("data_plane", {}).get("uftp") != "udp:1044":
            raise ValueError("data-plane contract mismatch")
        fixture = _json(root / "data-plane" / "fixture.json")
        path = root / "data-plane" / "model-fixture.bin"
        if fixture.get("size_bytes") != 40 * 1024 * 1024 or fixture.get("sha256") != CANONICAL_MODEL_SHA256:
            raise ValueError("invalid canonical fixture metadata")
        if not path.is_file() or path.stat().st_size != fixture["size_bytes"] or _digest(path) != fixture["sha256"]:
            raise ValueError("canonical fixture integrity mismatch")
        normal = _json(root / "data-plane" / "03-normal-job.json")
        detail = normal.get("detail", {})
        if detail.get("execution_result") != "succeeded" or detail.get("rounds_completed") != 2:
            raise ValueError("normal two-round job evidence missing")
        if not detail.get("control_plane_verified") or not detail.get("data_plane_verified"):
            raise ValueError("control/data plane gate missing")
        summary = _json(root / "data-plane" / "coordinator-summary.json")
        if summary.get("conclusion") != "succeeded" or len(summary.get("rounds", [])) != 2:
            raise ValueError("coordinator summary is incomplete")
        for node in (1, 2):
            evidence = _json(root / "data-plane" / f"client{node}-evidence-manifest.json")
            manifest = evidence.get("manifest", {})
            if manifest.get("runtime_commit") != envelope.get("commit") or manifest.get("node_id") != node:
                raise ValueError("client evidence identity mismatch")
            if not evidence.get("updates") or any(not SHA256.fullmatch(item.get("sha256", "")) for item in evidence["updates"]):
                raise ValueError("client update manifest evidence is incomplete")
        abort = _json(root / "control-plane" / "04-abort-job.json")
        if abort.get("detail", {}).get("execution_result") != "aborted" or abort.get("detail", {}).get("recovery_state") != "ready":
            raise ValueError("abort recovery evidence missing")
        lifecycle = _json(root / "lifecycle" / "05-collect.json")
        if lifecycle.get("detail", {}).get("resources_recovered") is not True:
            raise ValueError("resource recovery evidence missing")
        recovery_gate = _json(root / "control-plane" / "recovery-gate.json")
        if recovery_gate.get("new_job_rejected_before_recovery") is not True:
            raise ValueError("recovery gate evidence missing")
        resources = _json(root / "lifecycle" / "resources.json")
        for role, expected_tun in (("server", "fl-s"), ("client1", "fl-c1"), ("client2", "fl-c2")):
            if resources.get(role, {}).get("transient_processes") or resources.get(role, {}).get("tuns") != [expected_tun]:
                raise ValueError("idle resource recovery evidence mismatch")
        manifest = _json(root / "archive_manifest.json")
        actual = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"symlink in archive: {path}")
            if path.is_file() and path.name not in {"archive_manifest.json", "validation.json"}:
                actual[path.relative_to(root).as_posix()] = {"size_bytes": path.stat().st_size, "sha256": _digest(path)}
        if actual != manifest.get("files"):
            raise ValueError("archive manifest mismatch")
        for partition in PARTITIONS:
            if not any((root / partition).iterdir()):
                raise ValueError(f"empty evidence partition: {partition}")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    result = {"valid": not errors, "run_id": envelope.get("run_id") if "envelope" in locals() else None, "errors": errors}
    if root.exists():
        write_json_atomic(root / "validation.json", result)
    return errors


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    failures = validate_archive(args.archive)
    print("OK: stage4 hardware archive validation passed" if not failures else "ERROR: " + "; ".join(failures))
    raise SystemExit(bool(failures))
