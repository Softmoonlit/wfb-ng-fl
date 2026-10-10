#!/usr/bin/env python3
"""Stage 4 三机 Web -> 无线控制面/数据面正式验收入口。"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
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

from wfb_ng.fl.artifacts import file_sha256, write_json_atomic, validate_path_safe_identifier
from wfb_ng.fl.issue41_fixtures import generate_model_fixture
from tests.fl_runtime.hardware import inspect_wireless_device
from tests.fl_runtime.evidence_collection import collect_tree, collect_server_file
from tests.fl_runtime.stage4_hardware_archive import STAGES, init_archive, record_stage, seal_archive, validate_job_evidence, validate_terminal_state, validate_archive

ROOT = Path(__file__).resolve().parents[2]
CLIENTS = {"client1": 1, "client2": 2}
ROLES = ("server", *CLIENTS)
TARGET_NODES = list(CLIENTS.values())
UNITS = {"server": "wfb-fl-server-daemon.service", "client1": "wfb-fl-client-daemon.service", "client2": "wfb-fl-client-daemon.service"}
CANONICAL_MODEL_SHA256 = "a544c81f86c7a9e089dc45b3b0d3ff6490b933bb76153d8a8478c1a7703c7841"


class WebRequestError(RuntimeError):
    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.detail = status, detail


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
        raise WebRequestError(exc.code, detail) from exc


def _multipart(path: Path) -> tuple[bytes, str]:
    boundary = "stage4-" + uuid.uuid4().hex
    content = path.read_bytes()
    prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
              "Content-Type: application/octet-stream\r\n\r\n").encode()
    return prefix + content + f"\r\n--{boundary}--\r\n".encode(), boundary


def _poll(base: str, predicate, timeout: float, archive: Path, name: str, *, interval: float = 1) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    samples: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        _, state = _request(base, "/api/v1/state")
        samples.append(state)
        if predicate(state):
            write_json_atomic(archive / "management-web" / f"{name}.json", {"status": "passed", "samples": samples})
            return state
        time.sleep(interval)
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
        self.job_window_started_at: float | None = None
        self.pending_create: tuple[bytes, str] | None = None

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
                if identity.get("node_id") != CLIENTS[role]:
                    raise RuntimeError(f"{role}: node identity mismatch")
            active = self.executor.checked(role, f"sudo -n systemctl is-active {UNITS[role]}").strip()
            if active != "active":
                raise RuntimeError(f"{role}: daemon is not active")
            sockets = self.executor.checked(role, "sudo -n ss -H -lunp")
            (self.archive / "control-plane" / f"{role}-udp-sockets.txt").write_text(sockets, encoding="utf-8")
            config_path = "/etc/wfb-ng-fl/server.json" if role == "server" else "/etc/wfb-ng-fl/node.json"
            config = json.loads(self.executor.checked(role, "sudo -n cat " + config_path))
            self.save(f"control-plane/{role}-deployment.json", config)
            topology[role] = {"commit": head, "workspace_clean": clean, "wireless": wireless, "identity": identity,
                              "unit": UNITS[role], "service_active": True}
        status_code, state = _request(self.web_url, "/api/v1/state")
        if status_code != 200 or state.get("server", {}).get("management_web", {}).get("status") != "ready":
            raise RuntimeError("management Web is not ready")
        target_nodes = [node for node in state.get("nodes", []) if node.get("node_id") in TARGET_NODES]
        if ({node.get("node_id") for node in target_nodes} != set(TARGET_NODES) or
                not all(node.get("readiness") == "READY" and node.get("state") == "idle"
                        for node in target_nodes)):
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

    def _verify_job_evidence(self, job_id: str, digest: str, *, aborted: bool,
                             terminal_state: dict[str, Any]) -> dict[str, Any]:
        """Collect original files after recovery; the offline validator decides the verdict."""
        validate_path_safe_identifier(job_id, "job_id")
        validate_terminal_state(terminal_state, job_id, TARGET_NODES, aborted=aborted)
        job = terminal_state["recent_job"]
        if job.get("model_sha256") != digest:
            raise RuntimeError("terminal model identity mismatch")
        root = self.archive / "data-plane" / "jobs" / job_id
        self.save(str(root.relative_to(self.archive) / "binding.json"), {
            "job_id": job_id, "run_id": job["run_id"], "target_nodes": TARGET_NODES,
            "window_started_at": self.job_window_started_at, "window_ended_at": time.time()})
        self.save(str(root.relative_to(self.archive) / "terminal.json"), terminal_state)
        for role in ROLES:
            journal = self.executor.checked(role, f"sudo -n journalctl -u {UNITS[role]} "
                f"--since @{job['started_at']} --no-pager -o short-unix", timeout=60)
            (root / f"{role}-journal.txt").write_text(journal, encoding="utf-8")
            if role in CLIENTS:
                collect_tree(self.executor, role, "/var/lib/wfb-ng-fl/evidence/" + job_id,
                                          root / "clients" / str(CLIENTS[role]))
        if not aborted:
            # These are the production daemon's two separate job directories.
            job_root = Path("/tmp/wfb-ng-fl/server") / ("job_" + job_id)
            collect_server_file(self.executor, job_root / "coordinator_summary.json", root / "coordinator_summary.json")
            collect_tree(self.executor, "server", str(job_root) + "_role", root / "server-role")
            summary = json.loads((root / "coordinator_summary.json").read_text())
            for index, round_data in enumerate(summary["rounds"], 1):
                collect_server_file(self.executor, round_data["output_model_path"], root / "output-models" / f"{index}.bin", source_root=job_root)
        return validate_job_evidence(root, commit=self.meta["commit"], aborted=aborted)

    def _probe_recovery_gate(self, state: dict[str, Any], job_id: str, request: bytes, key: str) -> None:
        recent = state.get("recent_job") or {}
        if state.get("current_job") is not None or recent.get("job_id") != job_id or recent.get("recovery_state") != "recovering":
            return
        probe_key = key + "-recovery-probe"
        self.pending_create = (request, probe_key)
        try:
            _, accepted = _request(self.web_url, "/api/v1/jobs", method="POST", body=request,
                                  headers={"Content-Type": "application/json", "Idempotency-Key": probe_key})
        except WebRequestError as exc:
            self.pending_create = None
            code = exc.detail.get("error", {}).get("code") if isinstance(exc.detail, dict) else None
            passed = exc.status == 409 and code == "preflight_engine_conflict"
            self.save("control-plane/recovery-gate.json", {"status": "passed" if passed else "failed",
                "job_id": job_id, "sample": state, "http_status": exc.status, "error_code": code, "response": exc.detail})
            if not passed:
                raise RuntimeError("recovery probe failed with an unexpected HTTP error") from exc
        else:
            # Recovery can finish between observation and POST. Clean up a real accepted probe;
            # neither that race nor a successful POST proves a rejection gate.
            validate_path_safe_identifier(accepted["job_id"], "job_id")
            self.active_job_id = accepted["job_id"]
            self.pending_create = None
            raise RuntimeError("recovery probe accepted a new job; recovery rejection was not established")

    def _resolve_create(self) -> str:
        """Resolve a possibly accepted POST with its original idempotency key."""
        request, key = self.pending_create
        status, accepted = _request(self.web_url, "/api/v1/jobs", method="POST", body=request,
                                   headers={"Content-Type": "application/json", "Idempotency-Key": key})
        job_id = accepted["job_id"]
        validate_path_safe_identifier(job_id, "job_id")
        if status != 202:
            raise RuntimeError("unexpected job acceptance response")
        self.active_job_id = job_id
        self.pending_create = None
        return job_id

    def _cleanup_job(self) -> None:
        if self.pending_create is not None:
            self._resolve_create()
        job_id = self.active_job_id
        if job_id is None:
            return
        validate_path_safe_identifier(job_id, "job_id")
        _, state = _request(self.web_url, "/api/v1/state")
        recent = state.get("recent_job") or {}
        if (state.get("current_job") or {}).get("job_id") == job_id:
            _request(self.web_url, f"/api/v1/jobs/{job_id}/abort", method="POST",
                     body=b'{"reason":"Stage 4 acceptance failure cleanup"}',
                     headers={"Content-Type": "application/json"})
        elif recent.get("job_id") != job_id:
            raise RuntimeError("cannot establish accepted job identity for cleanup")
        _poll(self.web_url, lambda value: value.get("current_job") is None and
              (value.get("recent_job") or {}).get("job_id") == job_id and
              (value.get("recent_job") or {}).get("recovery_state") == "ready",
              60, self.archive, "failure-recovery")
        self.executor.job_window = False
        self.active_job_id = None

    def job(self, digest: str, *, abort: bool = False) -> dict[str, Any]:
        key = "stage4-" + ("abort" if abort else "normal") + "-" + self.meta["run_id"]
        request = json.dumps({"model_sha256": digest, "target_nodes": TARGET_NODES, "rounds": 2}).encode()
        self.job_window_started_at = time.time()
        self.executor.job_window = True
        self.pending_create = (request, key)
        try:
            job_id = self._resolve_create()
        except (TimeoutError, urllib.error.URLError):
            job_id = self._resolve_create()
        abort_pool = ThreadPoolExecutor(max_workers=1) if abort else None
        abort_future = None
        try:
            if abort:
                # First observe the active job through Web, then request the stop through Web.
                _poll(self.web_url, lambda state: (state.get("current_job") or {}).get("job_id") == job_id and
                      (state.get("current_job") or {}).get("server_phase") == "publishing_model",
                      60, self.archive, "abort-active", interval=0.05)
                self.save("control-plane/recovery-gate.json", {"status": "not_observed", "job_id": job_id,
                    "reason": "No recovering snapshot with a confirmed rejection has been sampled"})
                abort_future = abort_pool.submit(
                    _request, self.web_url, f"/api/v1/jobs/{job_id}/abort", method="POST",
                    body=b'{"reason":"Stage 4 formal emergency stop"}',
                    headers={"Content-Type": "application/json"})
            gate_observed = False
            def recovered(state: dict[str, Any]) -> bool:
                nonlocal gate_observed
                if abort_future is not None and abort_future.done():
                    abort_future.result()  # Surface HTTP failures immediately, rather than timing out.
                recent = state.get("recent_job") or {}
                if recent.get("job_id") != job_id:
                    return False
                if abort and not gate_observed and recent.get("recovery_state") == "recovering":
                    self._probe_recovery_gate(state, job_id, request, key)
                    gate_observed = True
                if recent.get("recovery_state") == "blocked":
                    raise RuntimeError("job recovery is blocked")
                if recent.get("execution_result") not in (None, "aborted" if abort else "succeeded"):
                    raise RuntimeError("unexpected job execution result")
                if recent.get("recovery_state") != "ready":
                    return False
                try:
                    validate_terminal_state(state, job_id, TARGET_NODES, aborted=abort)
                except ValueError:
                    return False
                expected_event = "JOB_ABORTED" if abort else "JOB_COMPLETED"
                return any(e.get("job_id") == job_id and e.get("type") == expected_event for e in state.get("events", []))
            terminal = _poll(self.web_url, recovered, 600, self.archive,
                             "abort-terminal" if abort else "normal-terminal", interval=0.1)
            if abort_future is not None:
                status, abort_result = abort_future.result()
                self.save("control-plane/abort-request.json", abort_result)
                if status != 202 or abort_result.get("job_id") != job_id or abort_result.get("status") != "accepted":
                    raise RuntimeError("unexpected abort acceptance response")
        finally:
            if abort_pool is not None:
                abort_pool.shutdown(wait=True)
            # Keep the no-SSH boundary on failure until Web cleanup confirms recovery.
            if "terminal" in locals():
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
                      "job_id": job_id, **evidence, "model_sha256": digest, "target_nodes": TARGET_NODES}
            self.save("data-plane/normal-job.json", {"stage": "normal-job", "status": "passed", "detail": detail})
        return job

    def collect(self) -> None:
        resources: dict[str, Any] = {}
        # Match process argv tokens, not a ps/awk command containing its own search text.
        probe = """import json,pathlib,subprocess
rows=[]
for line in subprocess.check_output(['ps','-eo','pid=,comm='],text=True).splitlines():
 pid,comm=line.split()
 try: argv=(pathlib.Path('/proc')/pid/'cmdline').read_bytes().decode().strip('\\0').split('\\0')
 except (OSError,UnicodeError): continue
 if comm in ('uftp','uftpd') or any(x in argv for x in ('wfb_ng.fl.role_service','wfb_ng.fl.service')):
  rows.append(dict(pid=int(pid),comm=comm,args=argv))
print(json.dumps(rows))"""
        for role in ROLES:
            resources[role] = {
                "transient_processes": json.loads(self.executor.checked(role, "python3 -I -c " + shlex.quote(probe))),
                "tuns": self.executor.checked(role, "ip -o link show | awk -F': ' '$2 ~ /^fl-(s|c[0-9]+)(@[^:]+)?$/ {split($2,a,\"@\"); print a[1]}'").splitlines(),
                "job_sandboxes": (json.loads(self.executor.checked(role,
                    "sudo -n python3 -I -c " + shlex.quote("import json,pathlib; print(json.dumps([str(p) for p in pathlib.Path('/tmp/wfb-ng-fl/client').glob('job_*')]))")))
                    if role in CLIENTS else []),
                "service": self.executor.checked(role, f"sudo -n systemctl is-active {UNITS[role]}").strip()}
        expected_tuns = {"server": ["fl-s"], "client1": ["fl-c1"], "client2": ["fl-c2"]}
        if any(value["service"] != "active" or value["job_sandboxes"] or value["transient_processes"] or sorted(value["tuns"]) != expected_tuns[role]
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
            self.executor.stage = "web-upload"
            digest = self.upload(fixture)
            record_stage(self.archive, "web-upload", status="passed", detail=self.model)
            self.executor.stage = "normal-job"
            normal = self.job(digest)
            record_stage(self.archive, "normal-job", status="passed", detail={"job_id": normal["job_id"], "execution_result": normal.get("execution_result"),
                                                                                "rounds_completed": normal.get("rounds_completed"),
                                                                                **self.last_evidence})
            self.executor.stage = "abort-job"
            aborted = self.job(digest, abort=True)
            record_stage(self.archive, "abort-job", status="passed", detail={"job_id": aborted["job_id"], "execution_result": aborted.get("execution_result"),
                                                                              "recovery_state": aborted.get("recovery_state"),
                                                                              **self.last_evidence})
            self.executor.stage = "collect"
            self.collect()
            record_stage(self.archive, "collect", status="passed", detail={"resources_recovered": True})
            self.executor.stage = "summary"
            gate = json.loads((self.archive / "control-plane/recovery-gate.json").read_text())
            if gate.get("status") != "passed":
                raise RuntimeError("recovery rejection gate not observed; hardware acceptance is incomplete")
            record_stage(self.archive, "summary", status="passed", detail={"conclusion": "passed"})
            seal_archive(self.archive, conclusion="passed")
            errors = validate_archive(self.archive)
            if errors:
                raise RuntimeError("offline archive validation failed: " + "; ".join(errors))
            return 0
        except BaseException as exc:
            failure = str(exc)
            if self.active_job_id is not None or self.pending_create is not None:
                try:
                    self._cleanup_job()
                except Exception as cleanup_error:
                    failure += f"; cleanup failed: {cleanup_error}"
            failed_stage = self.executor.stage
            completed = json.loads((self.archive / "envelope.json").read_text(encoding="utf-8"))["stages"]
            while len(completed) < len(STAGES) - 1:
                stage = STAGES[len(completed)]
                record_stage(self.archive, stage, status="skipped", detail={"blocked_by": failure})
                completed.append(stage)
            if len(completed) == len(STAGES):
                envelope = json.loads((self.archive / "envelope.json").read_text())
                envelope["stages"][-1].update(status="failed", detail={"conclusion": "failed", "failure": failure})
                self.save("envelope.json", envelope)
                self.save("summary/06-summary.json", envelope["stages"][-1])
            else:
                record_stage(self.archive, "summary", status="failed", detail={"conclusion": "failed", "failure": failure})
            seal_archive(self.archive, conclusion="failed", failure_boundary=failed_stage)
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
