#!/usr/bin/env python3
"""Stage 4 三机实体验收归档契约与 fail-closed 校验器。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wfb_ng.fl.artifacts import write_json_atomic, validate_path_safe_identifier
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.evidence import MAX_EVIDENCE_FILE_BYTES
from wfb_ng.fl.runtime import validate_round_state
from tests.fl_runtime.stage3_archive import _file, _json as evidence_json, _whitelisted, _equal_fields, _require

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


def validate_terminal_state(state: dict[str, Any], job_id: str, targets: list[int], *, aborted: bool) -> None:
    job = state.get("recent_job") or {}
    _require(state.get("current_job") is None and job.get("job_id") == job_id, "job is not terminal")
    _require(job.get("recovery_state") == "ready" and state.get("server", {}).get("state") == "idle",
             "job recovery is not ready")
    _require(job.get("execution_result") == ("aborted" if aborted else "succeeded"), "unexpected execution result")
    if not aborted:
        _require(job.get("rounds_completed") == 2, "missing completed rounds")
    selected = [n for n in state.get("nodes", []) if n.get("node_id") in targets]
    _require(len(selected) == len(targets) and {n["node_id"] for n in selected} == set(targets)
             and all(n.get("readiness") == "READY" and n.get("state") == "idle" for n in selected),
             "target nodes are not IDLE/READY")


def _journal_match(root: Path, role: str, pattern: str) -> re.Match:
    text = _file(root, f"{role}-journal.txt").read_text()
    matches = [re.search(pattern, line) for line in text.splitlines()]
    matches = [match for match in matches if match is not None]
    _require(len(matches) == 1, f"missing/ambiguous job-scoped {role} journal: {pattern}")
    return matches[0]


def validate_control_bindings(root: Path) -> None:
    server = evidence_json(root, "control-plane/server-deployment.json")
    _equal_fields({key: server.get(key, default) for key, default in {
        "tun_ip": "10.80.0.1", "control_bind_host": "10.80.0.1", "control_bind_port": 9001,
        "control_broadcast_addr": "255.255.255.255", "control_broadcast_port": 9000}.items()},
        dict(tun_ip="10.80.0.1", control_bind_host="10.80.0.1", control_bind_port=9001,
             control_broadcast_addr="255.255.255.255", control_broadcast_port=9000))
    for role in ("server", "client1", "client2"):
        if role != "server":
            node = int(role.removeprefix("client"))
            config = evidence_json(root, f"control-plane/{role}-deployment.json")
            _require(config.get("node_id") == node and config.get("tun_ip", "").split("/")[0] == f"10.80.0.{10 + node}",
                     "deployment node identity mismatch")
            _require(config.get("server_control_host", "10.80.0.1") == "10.80.0.1"
                     and config.get("server_control_port", 9001) == 9001 and config.get("broadcast_port", 9000) == 9000,
                     "client control configuration mismatch")
        rows = [line.split() for line in _file(root, f"control-plane/{role}-udp-sockets.txt").read_text().splitlines()]
        endpoints = {row[3] for row in rows if len(row) >= 5}
        expected = {"10.80.0.1:9001"} if role == "server" else {"0.0.0.0:9000", "*:9000"}
        _require(bool(endpoints & expected), f"{role}: actual UDP control bind mismatch")
        control = {endpoint for endpoint in endpoints if endpoint.rsplit(":", 1)[-1] in ("9000", "9001")}
        _require(control <= expected, f"{role}: unexpected UDP control bind")


def validate_transfer_start(value: dict[str, Any], job_id: str) -> None:
    validate_path_safe_identifier(job_id, "job_id")
    _require(value.get("job_id") == job_id and type(value.get("pid")) is int and value["pid"] > 0
             and type(value.get("start_ticks")) is int and value["start_ticks"] > 0, "invalid actual UFTP process identity")
    _require(type(value.get("observed_at")) in (int, float) and math.isfinite(value["observed_at"]), "invalid UFTP observation time")
    argv = value["argv"]
    _require(isinstance(argv, list) and argv and Path(argv[0]).name == "uftp", "missing actual UFTP argv")
    def option(name):
        _require(argv.count(name) == 1 and argv.index(name) + 1 < len(argv), f"missing UFTP {name}")
        return argv[argv.index(name) + 1]
    rid = option("-D")
    _require(str(uuid.UUID(rid)) == rid, "invalid UFTP round UUID")
    expected = Path("/tmp/wfb-ng-fl/server") / ("job_" + job_id + "_role") / "rounds" / rid
    _require(value.get("cwd") == str(expected), "UFTP cwd belongs to another job")
    for flag in ("-L", "-S"):
        path = Path(option(flag))
        _require(path.parent == expected and '..' not in path.parts, "UFTP evidence path belongs to another job")
    _require(option("-p") == "1044" and option("-I") == "10.80.0.1"
             and option("-M") == "239.80.41.1" and option("-P") == "239.80.41.2"
             and argv[-2:] == ["model.bin", "model.manifest.json"], "UFTP data-plane argv mismatch")


def validate_job_evidence(root: Path, *, commit: str, aborted: bool) -> dict[str, Any]:
    """Offline validation of original runtime files; never trusts runner verdict flags."""
    binding = evidence_json(root, "binding.json")
    job_id, run_id, targets = binding["job_id"], binding["run_id"], binding["target_nodes"]
    validate_path_safe_identifier(job_id, "job_id")
    validate_path_safe_identifier(run_id, "run_id")
    _require(root.name == job_id and targets == [1, 2], "job evidence binding mismatch")
    terminal = evidence_json(root, "terminal.json")
    validate_terminal_state(terminal, job_id, targets, aborted=aborted)
    _equal_fields(terminal["recent_job"], dict(run_id=run_id, target_nodes=targets))
    tokens = {event.get("type") for event in terminal.get("events", [])
              if isinstance(event, dict) and event.get("job_id") == job_id}
    required = {"JOB_ABORTED", "RECOVERY_COMPLETED"} if aborted else {
        "JOB_STARTED", "ROUND_STARTED", "ROUND_COMPLETED", "JOB_COMPLETED", "RECOVERY_COMPLETED"}
    _require(required <= tokens, "missing job-scoped control events")
    escaped_job = re.escape(job_id)
    _journal_match(root, "server", rf"作业 {escaped_job} 成功通过前置门禁并启动(?:\s|$)")
    terminal_type = "JOB_ABORT" if aborted else "JOB_COMPLETED"
    _journal_match(root, "server", rf"作业终态广播成功 type={terminal_type} job_id={escaped_job}(?:\s|$)")
    for node in targets:
        _journal_match(root, "server", rf"TASK_READY job_id={escaped_job} node_id={node} source=10\.80\.0\.{10 + node}:\d+(?:\s|$)")
    results = {}
    for node in targets:
        client = root / "clients" / str(node)
        manifest = evidence_json(client, "evidence_manifest.json")
        _equal_fields(manifest, dict(schema_version=1, run_id=run_id, job_id=job_id,
                                    node_id=node, runtime_commit=commit))
        outcome = manifest.get("lifecycle_outcome")
        _require(type(manifest.get("returncode")) is int, "invalid client returncode")
        if aborted:
            _require(outcome == "aborted", "client abort lifecycle failed")
        else:
            _require(outcome == "succeeded" and manifest["returncode"] in (0, -15, -9), "client lifecycle not succeeded")
        _journal_match(root, f"client{node}", rf"TASK_READY_ACK job_id={escaped_job} node_id={node} server=10\.80\.0\.1:9001(?:\s|$)")
        _journal_match(root, f"client{node}", rf"收到作业终态 type={terminal_type} job_id={escaped_job} node_id={node}(?:\s|$)")
        cleanup = _journal_match(root, f"client{node}", rf"回收作业 {escaped_job} 资源 \(exit_code=(-?\d+)\)")
        _require(int(cleanup[1]) == manifest["returncode"], "client cleanup/evidence returncode mismatch")
        role_config = evidence_json(client, "client_role.json")
        _equal_fields(role_config, dict(node_id=node, uftp_port=1044, server_http_host="10.80.0.1", server_http_port=8080,
            uftp_bind_host=f"10.80.0.{10 + node}", uftp_multicast_host="239.80.41.1", uftp_private_multicast_host="239.80.41.2"))
        files = manifest["files"]
        required_files = {"build_identity.json", "client_role.json", "role_service.log"}
        if not aborted:
            required_files.update({"algorithm_config.json", "uftpd.log", "current-round.json", "issue41-client-result.json"})
        _require(required_files <= files.keys(), "missing client lifecycle files")
        actual = set()
        for path in client.rglob("*"):
            _require(not path.is_symlink(), "symlink in client evidence")
            if path.is_file() and path.relative_to(client).as_posix() != "evidence_manifest.json":
                actual.add(path.relative_to(client).as_posix())
        _require(actual == set(files), "unlisted or missing client evidence")
        for rel, info in files.items():
            _require(_whitelisted(rel), "file outside Stage3 evidence whitelist")
            path = _file(client, rel)
            _require(type(info.get("size_bytes")) is int and 0 <= info["size_bytes"] <= MAX_EVIDENCE_FILE_BYTES,
                     "invalid evidence size")
            _require(path.stat().st_size == info["size_bytes"] and _digest(path) == info.get("sha256"),
                     "client evidence integrity mismatch")
        _equal_fields(evidence_json(client, "build_identity.json"), dict(schema_version=1, commit=commit))
        _require(manifest.get("build_identity_sha256") == files["build_identity.json"]["sha256"],
                 "build identity digest mismatch")
        if not aborted:
            config = evidence_json(client, "algorithm_config.json")
            _require(config.get("file_simulation") is True and config.get("rounds") == 2, "not a two-round file simulation")
            if evidence_json(client, "client_role.json").get("live_observation") is True:
                _require("observation.jsonl" in files, "missing configured observation evidence")
            result = evidence_json(client, "issue41-client-result.json")
            _equal_fields(result, dict(schema_version=1, role="client", node_id=node, conclusion="succeeded"))
            _require(len(result["rounds"]) == 2, "missing client rounds")
            results[node] = result["rounds"]
    if aborted:
        return {"control_plane_verified": True, "data_plane_verified": False}
    summary = evidence_json(root, "coordinator_summary.json")
    _equal_fields(summary, dict(schema_version=1, job_id=job_id, run_id=run_id, status="succeeded", mode="sync",
                               rounds_total=2, rounds_completed=2, target_nodes=targets,
                               final_model_sha256=CANONICAL_MODEL_SHA256, final_model_size_bytes=40 * 1024 * 1024))
    rounds = summary["rounds"]
    _require(len(rounds) == 2, "missing coordinator rounds")
    round_ids = []
    for index, summary_round in enumerate(rounds, 1):
        _equal_fields(summary_round, dict(round_index=index, committed_nodes=targets, dropped_out_nodes=[],
                                          input_model_sha256=CANONICAL_MODEL_SHA256, output_model_sha256=CANONICAL_MODEL_SHA256))
        runtime_job = Path("/tmp/wfb-ng-fl/server") / ("job_" + job_id)
        expected_output = runtime_job / "artifacts" / f"global-model-round-{index:04d}.bin"
        _require(summary_round.get("output_model_path") == str(expected_output), "coordinator output path outside current job")
        if index == len(rounds):
            _require(summary.get("final_model_path") == str(expected_output), "final model path outside current job")
        rid = results[targets[0]][index - 1]["round_id"]
        _require(str(uuid.UUID(rid)) == rid and rid not in round_ids, "invalid or duplicate round UUID")
        round_ids.append(rid)
        server_round = root / "server-role" / "rounds" / rid
        model_manifest = evidence_json(server_round, "model.manifest.json")
        expected_model = dict(schema_version=1, artifact_type="model", round_id=rid, size_bytes=40 * 1024 * 1024,
                              sha256=CANONICAL_MODEL_SHA256, participant_node_ids=targets)
        _require(model_manifest == expected_model, "server model manifest mismatch")
        for binary in (_file(server_round, "model.bin"), _file(root, f"output-models/{index}.bin")):
            _require(binary.stat().st_size == 40 * 1024 * 1024 and _digest(binary) == CANONICAL_MODEL_SHA256,
                     "actual model SHA/size mismatch")
        state = evidence_json(server_round, "round-state.json")
        validate_round_state(state, rid, "server", ())
        _equal_fields(state, dict(state="succeeded", participant_node_ids=targets, committed_update_node_ids=targets))
        for node in targets:
            result = results[node][index - 1]
            _equal_fields(result, dict(round_index=index, round_id=rid, node_id=node,
                model_size_bytes=40 * 1024 * 1024, update_size_bytes=40 * 1024 * 1024,
                model_sha256=CANONICAL_MODEL_SHA256, update_sha256=CANONICAL_MODEL_SHA256))
            expected_update = dict(schema_version=1, artifact_type="update", round_id=rid, node_id=node,
                                   size_bytes=40 * 1024 * 1024, sha256=CANONICAL_MODEL_SHA256)
            client_round = root / "clients" / str(node) / "rounds" / rid
            _require(evidence_json(client_round, "model.manifest.json") == expected_model, "client model mismatch")
            _require(evidence_json(client_round, "update.manifest.json") == expected_update
                     and evidence_json(server_round, f"updates/{node}/update.manifest.json") == expected_update,
                     "server/client update manifest mismatch")
            update = _file(server_round, f"updates/{node}/update.bin")
            _require(update.stat().st_size == 40 * 1024 * 1024 and _digest(update) == CANONICAL_MODEL_SHA256,
                     "actual update SHA/size mismatch")
            cstate = evidence_json(client_round, "round-state.json")
            validate_round_state(cstate, rid, "client", ())
            _equal_fields(cstate, dict(state="succeeded"))
    _require({p.name for p in (root / "server-role" / "rounds").iterdir()} == set(round_ids), "mixed server rounds")
    for node in targets:
        client = root / "clients" / str(node)
        _require({p.name for p in (client / "rounds").iterdir()} == set(round_ids), "mixed client rounds")
        _equal_fields(evidence_json(client, "current-round.json"), dict(schema_version=1, role="client", round_id=round_ids[-1]))
    return {"control_plane_verified": True, "data_plane_verified": True, "rounds": rounds, "update_count": 4}


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
        if tuple(x.get("stage") for x in envelope.get("stages", [])) != STAGES or any(x.get("status") != "passed" for x in envelope["stages"]):
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
        validate_control_bindings(root)
        normal_id = detail["job_id"]
        validate_path_safe_identifier(normal_id, "job_id")
        validate_job_evidence(root / "data-plane" / "jobs" / normal_id, commit=envelope["commit"], aborted=False)
        abort = _json(root / "control-plane" / "04-abort-job.json")
        if abort.get("detail", {}).get("execution_result") != "aborted" or abort.get("detail", {}).get("recovery_state") != "ready":
            raise ValueError("abort recovery evidence missing")
        abort_id = abort["detail"]["job_id"]
        validate_path_safe_identifier(abort_id, "job_id")
        if normal_id == abort_id:
            raise ValueError("normal and abort jobs must be isolated")
        validate_job_evidence(root / "data-plane" / "jobs" / abort_id, commit=envelope["commit"], aborted=True)
        active = evidence_json(root, "management-web/abort-active.json")["samples"][-1].get("current_job") or {}
        _require(active.get("job_id") == abort_id and active.get("server_phase") == "publishing_model",
                 "abort was not observed during model publishing")
        windows = []
        for job_id in (normal_id, abort_id):
            binding = evidence_json(root / "data-plane" / "jobs" / job_id, "binding.json")
            start, end = binding["window_started_at"], binding["window_ended_at"]
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(end) or start > end):
                raise ValueError("invalid job window audit")
            windows.append((start, end))
        audit = _file(root, "summary/orchestration.jsonl").read_text().splitlines()
        if not audit:
            raise ValueError("missing orchestration audit")
        for line in audit:
            entry = json.loads(line)
            target, transport = entry["target"], entry["transport"]
            if target not in ("vm0", "vm1", "vm2") or transport != ("local" if target == "vm0" else "ssh"):
                raise ValueError("invalid orchestration target/transport")
            start, end = entry["started_at"], entry["ended_at"]
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(end) or start > end
                    or entry.get("stage") not in STAGES or type(entry.get("returncode")) is not int):
                raise ValueError("invalid orchestration record")
            if transport == "ssh" and any(start <= last and end >= first for first, last in windows):
                raise ValueError("SSH overlaps protected job window")
        transfer = evidence_json(root, "control-plane/abort-transfer-start.json")
        validate_transfer_start(transfer, abort_id)
        abort_binding = evidence_json(root / "data-plane" / "jobs" / abort_id, "binding.json")
        _require(abort_binding["window_started_at"] <= transfer["observed_at"] <= abort_binding["window_ended_at"],
                 "UFTP observation outside job window")
        gone = evidence_json(root, "lifecycle/abort-transfer-exit.json")
        _equal_fields(gone, dict(job_id=abort_id, pid=transfer["pid"], start_ticks=transfer["start_ticks"], original_process_present=False))
        _require(gone["observed_at"] >= abort_binding["window_ended_at"], "UFTP exit observation before recovery")
        lifecycle = _json(root / "lifecycle" / "05-collect.json")
        if lifecycle.get("detail", {}).get("resources_recovered") is not True:
            raise ValueError("resource recovery evidence missing")
        recovery_gate = _json(root / "control-plane" / "recovery-gate.json")
        if (recovery_gate.get("status") != "passed" or recovery_gate.get("job_id") != abort_id
                or recovery_gate.get("http_status") != 409 or recovery_gate.get("error_code") != "preflight_engine_conflict"
                or (recovery_gate.get("sample", {}).get("recent_job") or {}).get("recovery_state") != "recovering"
                or recovery_gate.get("sample", {}).get("current_job") is not None
                or (recovery_gate.get("sample", {}).get("recent_job") or {}).get("job_id") != abort_id
                or recovery_gate.get("response", {}).get("error", {}).get("code") != "preflight_engine_conflict"):

            raise ValueError("recovery gate evidence missing")
        resources = _json(root / "lifecycle" / "resources.json")
        for role, expected_tun in (("server", "fl-s"), ("client1", "fl-c1"), ("client2", "fl-c2")):
            if (resources.get(role, {}).get("job_sandboxes") != [] or resources.get(role, {}).get("service") != "active" or resources.get(role, {}).get("transient_processes")
                    or resources.get(role, {}).get("tuns") != [expected_tun]):
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
    except (OSError, ValueError, KeyError, TypeError, AttributeError, FLRuntimeError) as exc:
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
