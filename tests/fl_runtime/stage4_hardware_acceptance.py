#!/usr/bin/env python3
"""Stage 4 三机 Web -> 无线控制面/数据面正式验收入口。"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wfb_ng.fl.artifacts import file_sha256, write_json_atomic
from wfb_ng.fl.issue41_fixtures import generate_model_fixture
from tests.fl_runtime.hardware import inspect_wireless_device
from tests.fl_runtime.stage4_hardware_archive import STAGES, init_archive, record_stage, seal_archive

ROOT = Path(__file__).resolve().parents[2]
ROLES = ("server", "client1", "client2")
UNITS = {"server": "wfb-fl-server-daemon.service", "client1": "wfb-fl-client-daemon.service", "client2": "wfb-fl-client-daemon.service"}
CANONICAL_MODEL_SHA256 = "a544c81f86c7a9e089dc45b3b0d3ff6490b933bb76153d8a8478c1a7703c7841"


class AuditedExecutor:
    """SSH inspection executor with a hard job-window boundary."""
    def __init__(self, archive: Path) -> None:
        self.archive = archive
        self.job_window = False
        self.stage = "preflight"

    def run(self, target: str, command: str, timeout: float = 30) -> tuple[int, str, str]:
        if target not in ROLES:
            raise ValueError(target)
        if target != "server" and self.job_window:
            raise RuntimeError("SSH is forbidden during Stage 4 job window")
        argv = ["bash", "-lc", command] if target == "server" else [
            "ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none",
            "-o", "ControlPersist=no", "-o", "ConnectTimeout=10", "vm" + target[-1], command]
        started = time.time()
        try:
            result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
            returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as exc:
            returncode, stdout, stderr = 124, str(exc.stdout or ""), str(exc.stderr or "command timeout")
        finally:
            record = {"target": "vm0" if target == "server" else "vm" + target[-1], "stage": self.stage,
                      "transport": "local" if target == "server" else "ssh", "command": command,
                      "started_at": started, "ended_at": time.time(), "returncode": locals().get("returncode", 125)}
            with (self.archive / "summary" / "orchestration.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return returncode, stdout, stderr

    def checked(self, target: str, command: str, timeout: float = 30) -> str:
        rc, out, err = self.run(target, command, timeout)
        if rc:
            raise RuntimeError(f"{target}: command exited {rc}: {err or out}")
        return out


def _request(base: str, path: str, *, method: str = "GET", body: bytes | None = None,
             headers: dict[str, str] | None = None, timeout: float = 30) -> tuple[int, Any]:
    request = urllib.request.Request(base.rstrip("/") + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            detail = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = raw.decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _multipart(path: Path) -> tuple[bytes, str]:
    boundary = "stage4-" + uuid.uuid4().hex
    content = path.read_bytes()
    prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
              "Content-Type: application/octet-stream\r\n\r\n").encode()
    return prefix + content + f"\r\n--{boundary}--\r\n".encode(), boundary


def _poll(base: str, predicate, timeout: float, archive: Path, name: str) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    samples: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        _, state = _request(base, "/api/v1/state")
        samples.append(state)
        if predicate(state):
            write_json_atomic(archive / "management-web" / f"{name}.json", {"status": "passed", "samples": samples})
            return state
        time.sleep(1)
    write_json_atomic(archive / "management-web" / f"{name}.json", {"status": "failed", "samples": samples})
    raise TimeoutError(f"timeout waiting for {name}")


class Stage4HardwareRunner:
    def __init__(self, archive: Path, web_url: str, repo: Path) -> None:
        self.archive, self.web_url, self.repo = archive, web_url.rstrip("/"), repo
        self.executor = AuditedExecutor(archive)
        self.meta = json.loads((archive / "envelope.json").read_text(encoding="utf-8"))
        self.last_evidence: dict[str, Any] = {}
        self.active_job_id: str | None = None
        self.model: dict[str, Any] = {}

    def save(self, relative: str, value: Any) -> None:
        write_json_atomic(self.archive / relative, value)

    def preflight(self) -> None:
        topology: dict[str, Any] = {}
        commit = self.meta["commit"]
        for role in ROLES:
            head = self.executor.checked(role, f"git -C {shlex.quote(str(self.repo))} rev-parse HEAD").strip()
            clean = not self.executor.checked(role, f"git -C {shlex.quote(str(self.repo))} status --porcelain").strip()
            if head != commit or not clean:
                raise RuntimeError(f"{role}: commit parity or clean worktree failed")
            wireless = inspect_wireless_device(self.executor, role)
            identity = None
            if role != "server":
                identity = json.loads(self.executor.checked(role, "sudo -n cat /etc/wfb-ng-fl/node.json"))
                if identity.get("node_id") != int(role[-1]):
                    raise RuntimeError(f"{role}: node identity mismatch")
            active = self.executor.checked(role, f"sudo -n systemctl is-active {UNITS[role]}").strip()
            if active != "active":
                raise RuntimeError(f"{role}: daemon is not active")
            topology[role] = {"commit": head, "workspace_clean": clean, "wireless": wireless, "identity": identity,
                              "unit": UNITS[role], "service_active": True}
        status_code, state = _request(self.web_url, "/api/v1/state")
        if status_code != 200 or state.get("server", {}).get("management_web", {}).get("status") != "management_web_ready":
            raise RuntimeError("management Web is not ready")
        if not all(node.get("readiness") == "READY" and node.get("state") == "idle" for node in state.get("nodes", [])):
            raise RuntimeError("all target nodes must be IDLE/READY")
        self.save("summary/topology.json", topology)
        self.save("summary/preflight.json", {"status": "passed", "commit": commit, "state": state,
                                               "control_plane": self.meta["control_plane"], "data_plane": self.meta["data_plane"]})

    def upload(self, fixture: Path) -> str:
        body, boundary = _multipart(fixture)
        status, result = _request(self.web_url, "/api/v1/models", method="POST", body=body,
                                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                                           "Content-Length": str(len(body))})
        if status not in (200, 201) or result.get("model", {}).get("size_bytes") != 40 * 1024 * 1024:
            raise RuntimeError(f"unexpected model upload result: {result}")
        digest = result["model"]["sha256"]
        if digest != CANONICAL_MODEL_SHA256 or digest != file_sha256(fixture) or fixture.stat().st_size != 40 * 1024 * 1024:
            raise RuntimeError("uploaded fixture is not the canonical 40 MiB model")
        self.model = {"sha256": digest, "size_bytes": fixture.stat().st_size, "filename": fixture.name}
        fixture_target = self.archive / "data-plane" / "model-fixture.bin"
        fixture_target.write_bytes(fixture.read_bytes())
        self.save("data-plane/fixture.json", self.model)
        self.save("management-web/upload.json", {"status": "passed", "http_status": status, "model": self.model})
        return digest

    def _read_client_evidence(self, role: str, job_id: str, *, require_rounds: bool = True) -> dict[str, Any]:
        script = '''import hashlib,json,pathlib,sys
root=pathlib.Path('/var/lib/wfb-ng-fl/evidence')/sys.argv[1]
require_rounds=sys.argv[4]=='1'
manifest=json.loads((root/'evidence_manifest.json').read_text())
assert manifest['job_id']==sys.argv[1] and manifest['node_id']==int(sys.argv[2])
assert manifest['runtime_commit']==sys.argv[3]
for rel,info in manifest['files'].items():
 p=(root/rel).resolve(); p.relative_to(root.resolve())
 assert p.is_file() and p.stat().st_size==info['size_bytes']
 h=hashlib.sha256(p.read_bytes()).hexdigest(); assert h==info['sha256']
updates=[]
for p in root.glob('rounds/*/update.manifest.json'):
 updates.append(json.loads(p.read_text()))
assert (not require_rounds) or len(updates) == 2
print(json.dumps({'manifest':manifest,'updates':updates}))'''
        command = "python3 -c " + shlex.quote(script) + " " + " ".join(shlex.quote(value) for value in (job_id, role[-1], self.meta["commit"], "1" if require_rounds else "0"))
        result = json.loads(self.executor.checked(role, "sudo -n " + command, timeout=60))
        self.save(f"data-plane/{role}-evidence-manifest.json", result)
        return result

    def _verify_job_evidence(self, job_id: str, digest: str, *, aborted: bool,
                             terminal_state: dict[str, Any]) -> dict[str, Any]:
        """Read daemon evidence after the protected window and verify both planes."""
        if aborted:
            control_tokens = ("JOB_ABORTED",)
        else:
            control_tokens = ("JOB_STARTED", "ROUND_STARTED", "ROUND_COMPLETED")
        if (not all(node.get("node_id") in (1, 2) and node.get("readiness") == "READY"
                    for node in terminal_state.get("nodes", [])) or
                {node.get("node_id") for node in terminal_state.get("nodes", [])} != {1, 2}):
            raise RuntimeError("terminal Web state does not contain exactly two ready target nodes")
        events = terminal_state.get("events", [])
        event_types = {event.get("type") for event in events if isinstance(event, dict)}
        if not all(token in event_types for token in control_tokens):
            raise RuntimeError(f"Web state lacks control-plane/runtime events: {sorted(event_types)}")
        journals: dict[str, str] = {}
        client_evidence: dict[int, dict[str, Any]] = {}
        for role in ROLES:
            journals[role] = self.executor.checked(
                role, f"sudo -n journalctl -u {UNITS[role]} --no-pager -o cat | tail -n 400", timeout=60)
            self.save(f"control-plane/{role}-journal-{job_id}.txt", journals[role])
            client_evidence[int(role[-1])] = self._read_client_evidence(role, job_id, require_rounds=not aborted)
        if not aborted:
            summary_text = self.executor.checked(
                "server", "find /tmp/wfb-ng-fl/server -type f -name coordinator_summary.json "
                f"-path {shlex.quote('*job_' + job_id + '*')} -print -quit | xargs -r cat", timeout=60)
            summary = json.loads(summary_text)
            rounds = summary.get("rounds", [])
            if summary.get("conclusion") != "succeeded" or len(rounds) != 2:
                raise RuntimeError("coordinator evidence is not a successful two-round run")
            for round_data in rounds:
                if round_data.get("update_node_ids") != [1, 2] or round_data.get("input_model_sha256") != digest:
                    raise RuntimeError("round barrier or model digest evidence mismatch")
                updates = round_data.get("updates", [])
                if len(updates) != 2 or any(u.get("size_bytes") != 40 * 1024 * 1024 for u in updates):
                    raise RuntimeError("update size evidence is incomplete")
                if {u.get("node_id") for u in updates} != {1, 2} or len({u.get("sha256") for u in updates}) != 2:
                    raise RuntimeError("per-node update digest evidence is incomplete")
            expected_updates = {node: [item["sha256"] for round_data in rounds for item in round_data["updates"] if item["node_id"] == node]
                               for node in (1, 2)}
            actual_updates = {node: [item.get("sha256") for update in client_evidence[node]["updates"] for item in [update]]
                              for node in (1, 2)}
            self.save("data-plane/coordinator-summary.json", summary)
            return {"control_plane_verified": True, "data_plane_verified": True,
                    "rounds": rounds, "update_count": 4}
        return {"control_plane_verified": True, "data_plane_verified": False}

    def job(self, digest: str, *, abort: bool = False) -> dict[str, Any]:
        key = "stage4-" + ("abort" if abort else "normal") + "-" + self.meta["run_id"]
        request = json.dumps({"model_sha256": digest, "target_nodes": [1, 2], "rounds": 2}).encode()
        _, accepted = _request(self.web_url, "/api/v1/jobs", method="POST", body=request,
                               headers={"Content-Type": "application/json", "Idempotency-Key": key})
        job_id = accepted["job_id"]
        self.active_job_id = job_id
        self.executor.job_window = True
        try:
            if abort:
                # First observe the active job through Web, then request the stop through Web.
                _poll(self.web_url, lambda state: (state.get("current_job") or {}).get("job_id") == job_id,
                      60, self.archive, "abort-active")
                try:
                    _request(self.web_url, "/api/v1/jobs", method="POST", body=request,
                             headers={"Content-Type": "application/json", "Idempotency-Key": key + "-blocked"})
                except RuntimeError as exc:
                    self.save("control-plane/recovery-gate.json", {"new_job_rejected_before_recovery": True,
                                                                     "error": str(exc)})
                else:
                    raise RuntimeError("new job was accepted before abort recovery")
                _, abort_result = _request(self.web_url, f"/api/v1/jobs/{job_id}/abort", method="POST",
                                           body=b'{"reason":"Stage 4 formal emergency stop"}',
                                           headers={"Content-Type": "application/json"})
                self.save("control-plane/abort-request.json", abort_result)
            terminal = _poll(self.web_url, lambda state: state.get("current_job") is None and
                             state.get("recent_job", {}).get("job_id") == job_id, 600, self.archive,
                             "abort-terminal" if abort else "normal-terminal")
        finally:
            self.executor.job_window = False
        job = terminal.get("recent_job") or {}
        self.active_job_id = None
        evidence = self._verify_job_evidence(job_id, digest, aborted=abort, terminal_state=terminal)
        self.last_evidence = evidence
        if abort:
            control = {"execution_result": job.get("execution_result"), "recovery_state": job.get("recovery_state"),
                       "job_id": job_id, **evidence}
            self.save("control-plane/abort-job.json", {"stage": "abort-job", "status": "passed", "detail": control})
        else:
            detail = {"execution_result": job.get("execution_result"), "rounds_completed": job.get("rounds_completed"),
                      "job_id": job_id, **evidence, "model_sha256": digest, "target_nodes": [1, 2]}
            self.save("data-plane/normal-job.json", {"stage": "normal-job", "status": "passed", "detail": detail})
        return job

    def collect(self) -> None:
        resources: dict[str, Any] = {}
        for role in ROLES:
            resources[role] = {"transient_processes": self.executor.checked(
                                   role, "ps -eo comm=,args= | awk '$1 == \"uftp\" || $1 == \"uftpd\" || $0 ~ /wfb_ng\\.fl\\.role_service/'"
                               ).splitlines(),
                               "tuns": self.executor.checked(role, "ip -o link show | awk -F': ' '$2 ~ /^fl-(s|c[0-9]+)/ {split($2,a,\"@\"); print a[1]}'").splitlines(),
                               "service": self.executor.checked(role, f"sudo -n systemctl is-active {UNITS[role]}").strip()}
        expected_tuns = {"server": ["fl-s"], "client1": ["fl-c1"], "client2": ["fl-c2"]}
        if any(value["transient_processes"] or sorted(value["tuns"]) != expected_tuns[role]
               for role, value in resources.items()):
            raise RuntimeError("temporary job resources remain or idle TUN was not restored")
        self.save("lifecycle/resources.json", resources)
        self.save("lifecycle/05-collect.json", {"stage": "collect", "status": "passed",
                                                  "detail": {"resources_recovered": True, "recovery_state": "ready"}})

    def run(self, fixture: Path) -> int:
        failure: str | None = None
        try:
            self.preflight()
            record_stage(self.archive, "preflight", status="passed", detail={"topology": "summary/topology.json"})
            digest = self.upload(fixture)
            record_stage(self.archive, "web-upload", status="passed", detail=self.model)
            normal = self.job(digest)
            record_stage(self.archive, "normal-job", status="passed", detail={"execution_result": normal.get("execution_result"),
                                                                                "rounds_completed": normal.get("rounds_completed"),
                                                                                **self.last_evidence})
            aborted = self.job(digest, abort=True)
            record_stage(self.archive, "abort-job", status="passed", detail={"execution_result": aborted.get("execution_result"),
                                                                              "recovery_state": aborted.get("recovery_state"),
                                                                              **self.last_evidence})
            self.collect()
            record_stage(self.archive, "collect", status="passed", detail={"resources_recovered": True})
            record_stage(self.archive, "summary", status="passed", detail={"conclusion": "passed"})
            seal_archive(self.archive, conclusion="passed")
            return 0
        except BaseException as exc:
            failure = str(exc)
            if self.active_job_id is not None:
                try:
                    self.executor.job_window = False
                    _request(self.web_url, f"/api/v1/jobs/{self.active_job_id}/abort", method="POST",
                             body=b'{"reason":"Stage 4 acceptance failure cleanup"}',
                             headers={"Content-Type": "application/json"})
                    _poll(self.web_url, lambda state: state.get("current_job") is None,
                          60, self.archive, "failure-recovery")
                except Exception as cleanup_error:
                    failure += f"; cleanup failed: {cleanup_error}"
                finally:
                    self.active_job_id = None
            completed = json.loads((self.archive / "envelope.json").read_text(encoding="utf-8"))["stages"]
            while len(completed) < len(STAGES) - 1:
                stage = STAGES[len(completed)]
                record_stage(self.archive, stage, status="skipped", detail={"blocked_by": failure})
                completed.append(stage)
            record_stage(self.archive, "summary", status="failed", detail={"conclusion": "failed", "failure": failure})
            seal_archive(self.archive, conclusion="failed", failure_boundary=STAGES[len(completed) - 1])
            return 1


def initialize(root: Path, repo: Path, web_url: str) -> Path:
    run_id = "stage4-hardware-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    archive = root / run_id
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    init_archive(archive, run_id=run_id, commit=commit, web_url=web_url)
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--archive-root", type=Path, default=ROOT / "tests/logs")
    parser.add_argument("--web-url", default=os.environ.get("STAGE4_WEB_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args(argv)
    archive = args.archive or initialize(args.archive_root, ROOT, args.web_url)
    fixture = args.fixture
    if fixture is not None:
        code = Stage4HardwareRunner(archive, args.web_url, ROOT).run(fixture)
    else:
        with tempfile.TemporaryDirectory(prefix="stage4-hardware-fixture-") as temp:
            fixture = Path(temp) / "model-40mib.bin"
            generate_model_fixture(str(fixture), size_bytes=40 * 1024 * 1024)
            code = Stage4HardwareRunner(archive, args.web_url, ROOT).run(fixture)
    print(f"stage4 hardware archive: {archive}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
