#!/usr/bin/env python3
"""Stage 4 无硬件验收归档契约与离线校验器。"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from wfb_ng.fl.artifacts import file_sha256, write_json_atomic

STAGES = (
    "preflight",
    "domain-model-library",
    "http-sse",
    "browser",
    "control-plane-lifecycle",
    "collect",
    "summary",
)
PARTITIONS = ("management-web", "control-plane", "data-plane", "lifecycle", "summary")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"stage4-software-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\Z")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def _write(path: Path, value: Any) -> None:
    write_json_atomic(path, value)


def init_archive(root: Path, *, run_id: str, commit: str) -> dict[str, Any]:
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("invalid stage4 run_id")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("invalid commit")
    root.mkdir(parents=True, exist_ok=False)
    for partition in PARTITIONS:
        (root / partition).mkdir()
    envelope = {
        "schema_version": 1,
        "kind": "stage4-software-acceptance",
        "run_id": run_id,
        "commit": commit,
        "topology": {"server": "local", "clients": [{"alias": "vm1", "node_id": 1}, {"alias": "vm2", "node_id": 2}]},
        "control_plane": {"downlink": "255.255.255.255:9000", "uplink": "10.80.0.1:9001"},
        "data_plane": {"uftp": "udp:1044", "multicast": ["239.80.41.1", "239.80.41.2"], "http_put": "wireless-data-plane"},
        "acceptance_contract": {"interface_prefix": "wlx*", "resource_recovery": "ready-or-blocked", "management_http_is_not_control_plane": True},
        "fixture": {},
        "stages": [],
        "completed": False,
    }
    _write(root / "envelope.json", envelope)
    return envelope


def record_stage(root: Path, stage: str, *, status: str, command: list[str] | None = None,
                 returncode: int | None = None, stdout: str = "", stderr: str = "",
                 detail: dict[str, Any] | None = None) -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    envelope = _json(root / "envelope.json")
    if envelope["stages"] and envelope["stages"][-1]["stage"] == stage:
        raise ValueError(f"duplicate stage: {stage}")
    expected = STAGES[len(envelope["stages"])]
    if stage != expected:
        raise ValueError(f"stage order: expected {expected}, got {stage}")
    if status not in ("passed", "failed", "skipped"):
        raise ValueError("invalid stage status")
    entry = {"stage": stage, "status": status, "command": command or [], "returncode": returncode,
             "recorded_at": datetime.now(timezone.utc).isoformat(), "detail": detail or {}}
    output = (root / _partition_for(stage)) / f"{len(envelope['stages']) + 1:02d}-{stage}.json"
    _write(output, {**entry, "stdout": stdout, "stderr": stderr})
    envelope["stages"].append(entry)
    _write(root / "envelope.json", envelope)


def _partition_for(stage: str) -> str:
    return {"preflight": "summary", "domain-model-library": "data-plane", "http-sse": "management-web",
            "browser": "management-web", "control-plane-lifecycle": "control-plane", "collect": "lifecycle",
            "summary": "summary"}[stage]


def bind_fixture(root: Path, fixture: dict[str, Any]) -> None:
    required = {"path", "size_bytes", "sha256"}
    if set(fixture) != required or fixture["size_bytes"] != 40 * 1024 * 1024 or not SHA256.fullmatch(fixture["sha256"]):
        raise ValueError("invalid canonical 40 MiB fixture metadata")
    source = Path(fixture["path"])
    if not source.is_file() or source.stat().st_size != fixture["size_bytes"] or file_sha256(source) != fixture["sha256"]:
        raise ValueError("canonical fixture does not match metadata")
    target = root / "data-plane" / "model-fixture.bin"
    shutil.copyfile(source, target)
    if file_sha256(target) != fixture["sha256"]:
        raise ValueError("fixture archive copy digest mismatch")
    envelope = _json(root / "envelope.json")
    envelope["fixture"] = {"path": "data-plane/model-fixture.bin", "size_bytes": fixture["size_bytes"], "sha256": fixture["sha256"]}
    _write(root / "envelope.json", envelope)


def seal_archive(root: Path, *, conclusion: str, failure_boundary: str | None = None) -> dict[str, Any]:
    envelope = _json(root / "envelope.json")
    if len(envelope["stages"]) != len(STAGES) or envelope["stages"][-1]["stage"] != "summary":
        raise ValueError("archive must record every stage before sealing")
    envelope["completed"] = True
    envelope["conclusion"] = conclusion
    if failure_boundary:
        envelope["failure_boundary"] = failure_boundary
    _write(root / "envelope.json", envelope)
    manifest: dict[str, Any] = {"schema_version": 1, "run_id": envelope["run_id"], "files": {}}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in ("archive_manifest.json", "validation.json"):
            rel = path.relative_to(root).as_posix()
            manifest["files"][rel] = {"size_bytes": path.stat().st_size, "sha256": file_sha256(path)}
    _write(root / "archive_manifest.json", manifest)
    return manifest


def validate_archive(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        envelope = _json(root / "envelope.json")
        if envelope.get("kind") != "stage4-software-acceptance" or not RUN_ID.fullmatch(envelope.get("run_id", "")):
            raise ValueError("invalid envelope identity")
        if not envelope.get("completed"):
            raise ValueError("archive is not sealed")
        if tuple(entry.get("stage") for entry in envelope.get("stages", [])) != STAGES:
            raise ValueError("stage sequence mismatch")
        if envelope.get("control_plane") != {"downlink": "255.255.255.255:9000", "uplink": "10.80.0.1:9001"}:
            raise ValueError("control-plane port or direction mismatch")
        if envelope.get("data_plane", {}).get("uftp") != "udp:1044" or envelope.get("acceptance_contract", {}).get("management_http_is_not_control_plane") is not True:
            raise ValueError("control/data-plane isolation mismatch")
        if envelope.get("acceptance_contract", {}).get("interface_prefix") != "wlx*":
            raise ValueError("wireless interface must remain dynamically discovered")
        if envelope.get("acceptance_contract", {}).get("resource_recovery") != "ready-or-blocked":
            raise ValueError("resource recovery contract missing")
        failed = [i for i, entry in enumerate(envelope["stages"]) if entry.get("status") == "failed"]
        if failed and any(entry.get("status") == "passed" for entry in envelope["stages"][failed[0] + 1:]):
            raise ValueError("stage passed after failure")
        fixture = envelope.get("fixture", {})
        path = root / fixture.get("path", "")
        if fixture.get("size_bytes") != 40 * 1024 * 1024 or not SHA256.fullmatch(fixture.get("sha256", "")):
            raise ValueError("invalid 40 MiB fixture binding")
        if not path.is_file() or path.stat().st_size != fixture["size_bytes"] or file_sha256(path) != fixture["sha256"]:
            raise ValueError("fixture integrity mismatch")
        manifest = _json(root / "archive_manifest.json")
        if manifest.get("run_id") != envelope["run_id"]:
            raise ValueError("manifest run_id mismatch")
        actual = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"symlink in archive: {path}")
            if path.is_file() and path.name not in ("archive_manifest.json", "validation.json"):
                rel = path.relative_to(root).as_posix()
                actual[rel] = {"size_bytes": path.stat().st_size, "sha256": file_sha256(path)}
        if actual != manifest.get("files"):
            raise ValueError("archive manifest mismatch")
        for partition in PARTITIONS:
            if not any((root / partition).iterdir()):
                raise ValueError(f"empty evidence partition: {partition}")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    result = {"valid": not errors, "run_id": _safe_run_id(root), "errors": errors}
    if root.exists():
        _write(root / "validation.json", result)
    return errors


def _safe_run_id(root: Path) -> str | None:
    try:
        return _json(root / "envelope.json").get("run_id")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 4 无硬件验收归档校验")
    sub = parser.add_subparsers(dest="command", required=True)
    val = sub.add_parser("validate"); val.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    if args.command == "validate":
        errors = validate_archive(args.archive)
        print("OK: stage4 software archive validation passed" if not errors else "ERROR: " + "; ".join(errors))
        return 0 if not errors else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
