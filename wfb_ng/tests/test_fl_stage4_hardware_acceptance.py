from __future__ import annotations

import json
import os
import uuid
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.fl_runtime.evidence_collection import collect_server_file
from wfb_ng.fl.errors import FLRuntimeError

from tests.fl_runtime.stage4_hardware_acceptance import AuditedExecutor, Stage4HardwareRunner, WebRequestError, _multipart
from tests.fl_runtime.stage4_hardware_archive import STAGES, init_archive, record_stage, validate_terminal_state, validate_job_evidence, validate_archive, seal_archive, CANONICAL_MODEL_SHA256

from wfb_ng.fl.artifacts import write_json_atomic, file_sha256
from wfb_ng.fl.evidence import archive_client_evidence
from wfb_ng.fl.issue41_fixtures import generate_model_fixture


def transfer_fixture(job_id):
    rid = "00000000-0000-0000-0000-000000000001"
    cwd = f"/tmp/wfb-ng-fl/server/job_{job_id}_role/rounds/{rid}"
    return dict(job_id=job_id, pid=4242, start_ticks=1234, observed_at=15, cwd=cwd,
        argv=["/usr/bin/uftp", "-I", "10.80.0.1", "-M", "239.80.41.1", "-P", "239.80.41.2", "-p", "1044",
              "-L", cwd + "/uftp-log", "-S", cwd + "/uftp-status", "-D", rid, "model.bin", "model.manifest.json"])


class Stage4OfflineEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_temp = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.fixture_temp.name) / "model.bin"
        generate_model_fixture(str(cls.fixture), size_bytes=40 * 1024 * 1024)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture_temp.cleanup()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Path(self.temp.name) / "stage4-hardware-20261010T120000Z-abcdef12"
        init_archive(self.archive, run_id=self.archive.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
        self.normal = self.make_job("web-normal", aborted=False)
        self.aborted = self.make_job("web-abort", aborted=True)

    def link(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.link(self.fixture, destination)

    def make_job(self, job_id: str, *, aborted: bool) -> Path:
        root = self.archive / "data-plane" / "jobs" / job_id
        run_id = "run-" + job_id
        write_json_atomic(root / "binding.json", dict(job_id=job_id, run_id=run_id, target_nodes=[1, 2], window_started_at=10, window_ended_at=20))
        tokens = ["JOB_ABORTED", "RECOVERY_COMPLETED"] if aborted else [
            "JOB_STARTED", "ROUND_STARTED", "ROUND_COMPLETED", "JOB_COMPLETED", "RECOVERY_COMPLETED"]
        terminal = dict(current_job=None, recent_job=dict(job_id=job_id, run_id=run_id, target_nodes=[1, 2],
            execution_result="aborted" if aborted else "succeeded", recovery_state="ready", rounds_completed=0 if aborted else 2),
            server={"state": "idle"}, nodes=[dict(node_id=n, state="idle", readiness="READY") for n in (1, 2)],
            events=[dict(type=token, job_id=job_id) for token in tokens])
        write_json_atomic(root / "terminal.json", terminal)
        terminal_type = "JOB_ABORT" if aborted else "JOB_COMPLETED"
        (root / "server-journal.txt").write_text(
            f"10 TASK_READY job_id={job_id} node_id=1 source=10.80.0.11:9000\n"
            f"10 TASK_READY job_id={job_id} node_id=2 source=10.80.0.12:9000\n"
            f"11 作业 {job_id} 成功通过前置门禁并启动 (模式: sync)\n"
            f"12 作业终态广播成功 type={terminal_type} job_id={job_id}\n")
        for node in (1, 2):
            (root / f"client{node}-journal.txt").write_text(
                f"10 TASK_READY_ACK job_id={job_id} node_id={node} server=10.80.0.1:9001\n"
                f"12 收到作业终态 type={terminal_type} job_id={job_id} node_id={node}\n"
                f"13 回收作业 {job_id} 资源 (exit_code={-15 if aborted else 0})\n")
        ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        identity = Path(self.temp.name) / "identity" / "build_identity.json"
        write_json_atomic(identity, dict(schema_version=1, commit="a" * 40))
        for node in (1, 2):
            sandbox = Path(self.temp.name) / (job_id + str(node))
            sandbox.mkdir()
            (sandbox / "role_service.log").write_text("actual role log")
            write_json_atomic(sandbox / "client_role.json", dict(node_id=node, live_observation=False,
                uftp_port=1044, server_http_host="10.80.0.1", server_http_port=8080, uftp_bind_host=f"10.80.0.{10 + node}",
                uftp_multicast_host="239.80.41.1", uftp_private_multicast_host="239.80.41.2"))
            if not aborted:
                (sandbox / "uftpd.log").write_text("actual receiver log")
                write_json_atomic(sandbox / "algorithm_config.json", dict(file_simulation=True, rounds=2))
                write_json_atomic(sandbox / "current-round.json", dict(schema_version=1, role="client", round_id=ids[-1]))
                results = []
                for index, rid in enumerate(ids, 1):
                    cround = sandbox / "rounds" / rid
                    write_json_atomic(cround / "model.manifest.json", dict(schema_version=1, artifact_type="model", round_id=rid,
                        size_bytes=40 * 1024 * 1024, sha256=CANONICAL_MODEL_SHA256, participant_node_ids=[1, 2]))
                    write_json_atomic(cround / "update.manifest.json", dict(schema_version=1, artifact_type="update", round_id=rid,
                        node_id=node, size_bytes=40 * 1024 * 1024, sha256=CANONICAL_MODEL_SHA256))
                    write_json_atomic(cround / "round-state.json", dict(schema_version=1, role="client", round_id=rid, state="succeeded"))
                    results.append(dict(round_index=index, round_id=rid, node_id=node,
                        model_size_bytes=40 * 1024 * 1024, update_size_bytes=40 * 1024 * 1024,
                        model_sha256=CANONICAL_MODEL_SHA256, update_sha256=CANONICAL_MODEL_SHA256))
                write_json_atomic(sandbox / "issue41-client-result.json", dict(schema_version=1, role="client", node_id=node,
                    conclusion="succeeded", rounds=results))
            with patch("wfb_ng.fl.evidence.BUILD_IDENTITY_PATH", identity):
                produced = Path(archive_client_evidence(str(sandbox), str(root / "pending" / str(node)),
                    run_id=run_id, job_id=job_id, node_id=node, lifecycle_outcome="aborted" if aborted else "succeeded",
                    returncode=-15 if aborted else 0))
            destination = root / "clients" / str(node)
            destination.parent.mkdir(parents=True, exist_ok=True)
            produced.rename(destination)
            produced.parent.rmdir()
        (root / "pending").rmdir()
        if not aborted:
            rounds = []
            for index, rid in enumerate(ids, 1):
                server = root / "server-role" / "rounds" / rid
                write_json_atomic(server / "model.manifest.json", json.loads((root / "clients/1/rounds" / rid / "model.manifest.json").read_text()))
                write_json_atomic(server / "round-state.json", dict(schema_version=1, role="server", round_id=rid,
                    state="succeeded", participant_node_ids=[1, 2], committed_update_node_ids=[1, 2]))
                self.link(server / "model.bin")
                for node in (1, 2):
                    write_json_atomic(server / f"updates/{node}/update.manifest.json", json.loads(
                        (root / f"clients/{node}/rounds" / rid / "update.manifest.json").read_text()))
                    self.link(server / f"updates/{node}/update.bin")
                self.link(root / f"output-models/{index}.bin")
                rounds.append(dict(round_index=index, committed_nodes=[1, 2], dropped_out_nodes=[],
                    output_model_path=f"/tmp/wfb-ng-fl/server/job_{job_id}/artifacts/global-model-round-{index:04d}.bin",
                    input_model_sha256=CANONICAL_MODEL_SHA256, output_model_sha256=CANONICAL_MODEL_SHA256))
            write_json_atomic(root / "coordinator_summary.json", dict(schema_version=1, job_id=job_id, run_id=run_id,
                status="succeeded", mode="sync", rounds_total=2, rounds_completed=2, target_nodes=[1, 2], rounds=rounds,
                final_model_path=f"/tmp/wfb-ng-fl/server/job_{job_id}/artifacts/global-model-round-0002.bin",
                final_model_sha256=CANONICAL_MODEL_SHA256, final_model_size_bytes=40 * 1024 * 1024))
        return root

    def seal_valid_archive(self) -> None:
        (self.archive / "summary/orchestration.jsonl").write_text(json.dumps(dict(target="vm1", transport="ssh",
            started_at=21, ended_at=22, stage="collect", returncode=0, command="read evidence")) + "\n")
        write_json_atomic(self.archive / "control-plane/abort-transfer-start.json", transfer_fixture("web-abort"))
        write_json_atomic(self.archive / "lifecycle/abort-transfer-exit.json", dict(job_id="web-abort", pid=4242,
            start_ticks=1234, original_process_present=False, observed_at=21))
        self.link(self.archive / "data-plane/model-fixture.bin")
        write_json_atomic(self.archive / "data-plane/fixture.json", dict(size_bytes=40 * 1024 * 1024, sha256=CANONICAL_MODEL_SHA256))
        write_json_atomic(self.archive / "lifecycle/resources.json", {
            role: dict(service="active", transient_processes=[], tuns=[tun], job_sandboxes=[]) for role, tun in
            (("server", "fl-s"), ("client1", "fl-c1"), ("client2", "fl-c2"))})
        write_json_atomic(self.archive / "management-web/abort-active.json", {
            "samples": [{"current_job": {"job_id": "web-abort", "server_phase": "publishing_model"}}]})
        for role in ("server", "client1", "client2"):
            config = {} if role == "server" else dict(node_id=int(role[-1]), tun_ip=f"10.80.0.{10 + int(role[-1])}/24")
            write_json_atomic(self.archive / f"control-plane/{role}-deployment.json", config)
            endpoint = "10.80.0.1:9001" if role == "server" else "0.0.0.0:9000"
            (self.archive / f"control-plane/{role}-udp-sockets.txt").write_text(f"UNCONN 0 0 {endpoint} 0.0.0.0:* users:((python3))\n")
        sample = json.loads((self.aborted / "terminal.json").read_text())
        sample["recent_job"]["recovery_state"] = "recovering"
        write_json_atomic(self.archive / "control-plane/recovery-gate.json", dict(status="passed", job_id="web-abort",
            sample=sample, http_status=409, error_code="preflight_engine_conflict",
            response={"error": {"code": "preflight_engine_conflict"}}))
        details = {"normal-job": dict(job_id="web-normal", execution_result="succeeded", rounds_completed=2,
                    control_plane_verified=True, data_plane_verified=True),
                   "abort-job": dict(job_id="web-abort", execution_result="aborted", recovery_state="ready"),
                   "collect": dict(resources_recovered=True)}
        for stage in STAGES:
            record_stage(self.archive, stage, status="passed", detail=details.get(stage, {}))
        seal_archive(self.archive, conclusion="passed")

    def test_full_offline_archive_validates_normal_and_abort_production_files(self) -> None:
        self.seal_valid_archive()
        self.assertEqual(validate_archive(self.archive), [])
        # Even a resealed archive must fail domain checks, independently of runner flags.
        binary = next((self.normal / "server-role/rounds").glob("*/updates/1/update.bin"))
        binary.unlink(); binary.write_bytes(b"wrong update")
        seal_archive(self.archive, conclusion="passed")
        self.assertIn("actual update", ";".join(validate_archive(self.archive)))

    def test_abort_rejects_succeeded_client_and_missing_raw_ready_journal(self) -> None:
        path = self.aborted / "clients/1/evidence_manifest.json"
        manifest = json.loads(path.read_text()); manifest["lifecycle_outcome"] = "succeeded"
        write_json_atomic(path, manifest)
        with self.assertRaisesRegex(ValueError, "abort lifecycle"):
            validate_job_evidence(self.aborted, commit="a" * 40, aborted=True)
        (self.normal / "server-journal.txt").write_text("11 作业 web-normal 成功通过前置门禁并启动 (模式: sync)\n"
            "12 作业终态广播成功 type=JOB_COMPLETED job_id=web-normal\n")
        with self.assertRaisesRegex(ValueError, "TASK_READY"):
            validate_job_evidence(self.normal, commit="a" * 40, aborted=False)

    def test_full_archive_rejects_management_udp_bind_and_retained_sandbox(self) -> None:
        self.seal_valid_archive()
        sockets = self.archive / "control-plane/server-udp-sockets.txt"
        original = sockets.read_text()
        sockets.write_text(original.replace("10.80.0.1:9001", "192.168.1.1:9001"))
        seal_archive(self.archive, conclusion="passed")
        self.assertIn("actual UDP control bind", ";".join(validate_archive(self.archive)))
        sockets.write_text(original)
        path = self.archive / "lifecycle/resources.json"
        resources = json.loads(path.read_text()); resources["client1"]["job_sandboxes"] = ["/tmp/wfb-ng-fl/client/job_stale"]
        write_json_atomic(path, resources)
        seal_archive(self.archive, conclusion="passed")
        self.assertIn("resource recovery", ";".join(validate_archive(self.archive)))

    def test_full_archive_rejects_missing_actual_transfer_and_wrong_job_process(self) -> None:
        self.seal_valid_archive()
        path = self.archive / "control-plane/abort-transfer-start.json"
        path.unlink()
        seal_archive(self.archive, conclusion="passed")
        self.assertTrue(validate_archive(self.archive))
        wrong = transfer_fixture("another-job")
        wrong["job_id"] = "web-abort"
        write_json_atomic(path, wrong)
        seal_archive(self.archive, conclusion="passed")
        self.assertIn("another job", ";".join(validate_archive(self.archive)))

    def test_full_archive_rejects_aborted_transfer_process_still_present(self) -> None:
        self.seal_valid_archive()
        path = self.archive / "lifecycle/abort-transfer-exit.json"
        value = json.loads(path.read_text()); value["original_process_present"] = True
        write_json_atomic(path, value)
        seal_archive(self.archive, conclusion="passed")
        self.assertTrue(validate_archive(self.archive))

    def test_full_archive_rejects_unobserved_recovery_gate(self) -> None:
        self.seal_valid_archive()
        write_json_atomic(self.archive / "control-plane/recovery-gate.json", dict(status="not_observed", job_id="web-abort"))
        seal_archive(self.archive, conclusion="passed")
        self.assertIn("recovery gate", ";".join(validate_archive(self.archive)))

    def test_full_archive_rejects_ssh_inside_job_window(self) -> None:
        self.seal_valid_archive()
        path = self.archive / "summary/orchestration.jsonl"
        value = json.loads(path.read_text()); value.update(started_at=15, ended_at=16)
        path.write_text(json.dumps(value) + "\n")
        seal_archive(self.archive, conclusion="passed")
        self.assertIn("SSH overlaps", ";".join(validate_archive(self.archive)))

    def test_normal_terminal_signal_exit_still_requires_complete_result(self) -> None:
        path = self.normal / "clients/1/evidence_manifest.json"
        manifest = json.loads(path.read_text()); manifest["returncode"] = -15
        write_json_atomic(path, manifest)
        journal = self.normal / "client1-journal.txt"
        journal.write_text(journal.read_text().replace("exit_code=0", "exit_code=-15"))
        validate_job_evidence(self.normal, commit="a" * 40, aborted=False)
        (self.normal / "clients/1/issue41-client-result.json").unlink()
        with self.assertRaisesRegex(ValueError, "missing client evidence"):
            validate_job_evidence(self.normal, commit="a" * 40, aborted=False)

    def test_production_evidence_accepts_identical_updates_and_zero_round_abort(self) -> None:
        self.assertEqual(validate_job_evidence(self.normal, commit="a" * 40, aborted=False)["update_count"], 4)
        self.assertFalse(validate_job_evidence(self.aborted, commit="a" * 40, aborted=True)["data_plane_verified"])
        self.assertTrue((self.normal / "clients/1/issue41-client-result.json").is_file())

    def test_rejects_wrong_server_update_manifest(self) -> None:
        manifest = next((self.normal / "server-role/rounds").glob("*/updates/1/update.manifest.json"))
        value = json.loads(manifest.read_text()); value["node_id"] = 2
        write_json_atomic(manifest, value)
        with self.assertRaisesRegex(ValueError, "update manifest"):
            validate_job_evidence(self.normal, commit="a" * 40, aborted=False)

    def test_rejects_actual_update_corruption(self) -> None:
        binary = next((self.normal / "server-role/rounds").glob("*/updates/1/update.bin"))
        binary.unlink(); binary.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "actual update"):
            validate_job_evidence(self.normal, commit="a" * 40, aborted=False)

    def test_rejects_cross_round_client_result_even_with_resealed_client_manifest(self) -> None:
        client = self.normal / "clients/2"
        path = client / "issue41-client-result.json"
        value = json.loads(path.read_text()); value["rounds"].reverse()
        write_json_atomic(path, value)
        manifest = json.loads((client / "evidence_manifest.json").read_text())
        manifest["files"][path.name] = dict(size_bytes=path.stat().st_size, sha256=file_sha256(path))
        write_json_atomic(client / "evidence_manifest.json", manifest)
        with self.assertRaises(ValueError):
            validate_job_evidence(self.normal, commit="a" * 40, aborted=False)

    def test_rejects_failed_client_lifecycle_and_unlisted_files(self) -> None:
        client = self.normal / "clients/1"
        manifest = json.loads((client / "evidence_manifest.json").read_text())
        manifest["lifecycle_outcome"] = "failed"
        write_json_atomic(client / "evidence_manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            validate_job_evidence(self.normal, commit="a" * 40, aborted=False)
        manifest["lifecycle_outcome"] = "succeeded"
        write_json_atomic(client / "evidence_manifest.json", manifest)
        (client / "unlisted.txt").write_text("unlisted")
        with self.assertRaisesRegex(ValueError, "unlisted"):
            validate_job_evidence(self.normal, commit="a" * 40, aborted=False)

    def test_old_job_events_do_not_prove_abort(self) -> None:
        path = self.aborted / "terminal.json"
        value = json.loads(path.read_text())
        for event in value["events"]:
            event["job_id"] = "web-normal"
        write_json_atomic(path, value)
        with self.assertRaisesRegex(ValueError, "job-scoped"):
            validate_job_evidence(self.aborted, commit="a" * 40, aborted=True)


class Stage4HardwareToolTests(unittest.TestCase):
    def test_terminal_waits_for_recovery_and_ignores_unrelated_nodes(self) -> None:
        state = {"current_job": None, "recent_job": {"job_id": "job", "execution_result": "succeeded",
                 "rounds_completed": 2, "recovery_state": "recovering"}, "server": {"state": "idle"},
                 "nodes": [{"node_id": 1, "readiness": "READY", "state": "idle"},
                           {"node_id": 2, "readiness": "READY", "state": "idle"},
                           {"node_id": 7, "readiness": "OFFLINE", "state": "offline"}]}
        with self.assertRaisesRegex(ValueError, "recovery"):
            validate_terminal_state(state, "job", [1, 2], aborted=False)
        state["recent_job"]["recovery_state"] = "ready"
        validate_terminal_state(state, "job", [1, 2], aborted=False)
        state["recent_job"]["execution_result"] = "failed"
        with self.assertRaisesRegex(ValueError, "execution"):
            validate_terminal_state(state, "job", [1, 2], aborted=False)

    def test_recovery_probe_rejects_unrelated_http_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            runner = Stage4HardwareRunner(root, "http://10.0.0.1:8080", Path(temp))
            state = {"current_job": None, "recent_job": {"job_id": "job", "recovery_state": "recovering"}}
            with patch("tests.fl_runtime.stage4_hardware_acceptance._request",
                       side_effect=WebRequestError(503, {"error": {"code": "JOB_UNAVAILABLE"}})):
                with self.assertRaisesRegex(RuntimeError, "unexpected HTTP"):
                    runner._probe_recovery_gate(state, "job", b"{}", "key")
            self.assertEqual(json.loads((root / "control-plane/recovery-gate.json").read_text())["status"], "failed")

    def test_abort_response_does_not_block_recovery_sampling(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            runner = Stage4HardwareRunner(root, "http://10.0.0.1:8080", Path(temp))
            abort_started, gate_sampled = threading.Event(), threading.Event()
            job = dict(job_id="job", execution_result="aborted", recovery_state="ready")
            terminal = dict(current_job=None, recent_job=job, server={"state": "idle"},
                nodes=[dict(node_id=n, state="idle", readiness="READY") for n in (1, 2)],
                events=[dict(type="JOB_ABORTED", job_id="job")])
            def request(base, path, **kwargs):
                if path.endswith("/abort"):
                    abort_started.set()
                    if not gate_sampled.wait(2):
                        raise RuntimeError("abort response blocked recovery sampling")
                    return 202, {"status": "accepted", "job_id": "job"}
                if path == "/api/v1/jobs":
                    if kwargs.get("headers", {}).get("Idempotency-Key", "").endswith("-recovery-probe"):
                        gate_sampled.set()
                        raise WebRequestError(409, {"error": {"code": "preflight_engine_conflict"}})
                    return 202, {"job_id": "job"}
                if not abort_started.is_set():
                    return 200, {"current_job": {"job_id": "job", "server_phase": "publishing_model"}}
                if not gate_sampled.is_set():
                    return 200, {"current_job": None, "recent_job": {"job_id": "job", "recovery_state": "recovering"}}
                return 200, terminal
            with patch("tests.fl_runtime.stage4_hardware_acceptance._request", side_effect=request), patch.object(
                    runner, "_verify_job_evidence", return_value={"control_plane_verified": True, "data_plane_verified": False}), patch.object(
                    runner, "_sample_uftp", return_value=transfer_fixture("job")):
                self.assertEqual(runner.job("b" * 64, abort=True)["execution_result"], "aborted")
            self.assertTrue(gate_sampled.is_set())
            self.assertEqual(json.loads((root / "control-plane/recovery-gate.json").read_text())["status"], "passed")
            self.assertFalse(runner.executor.job_window)

    def test_unknown_create_response_resolves_same_key_and_cleans_accepted_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            runner = Stage4HardwareRunner(root, "http://10.0.0.1:8080", Path(temp))
            keys, aborts = [], []
            def request(base, path, **kwargs):
                if path == "/api/v1/jobs":
                    keys.append(kwargs["headers"]["Idempotency-Key"])
                    if len(keys) < 3:
                        raise TimeoutError("response was lost after acceptance")
                    return 202, {"job_id": "accepted-job"}
                if path.endswith("/abort"):
                    aborts.append(path)
                    return 202, {"status": "accepted", "job_id": "accepted-job"}
                if not aborts:
                    return 200, {"current_job": {"job_id": "accepted-job"}}
                return 200, {"current_job": None, "recent_job": {"job_id": "accepted-job", "recovery_state": "ready"}}
            with patch.object(runner, "preflight"), patch.object(runner, "upload", return_value="b" * 64), patch(
                    "tests.fl_runtime.stage4_hardware_acceptance._request", side_effect=request):
                self.assertEqual(runner.run(Path(temp) / "fixture"), 1)
            self.assertEqual(len(keys), 3)
            self.assertEqual(len(set(keys)), 1)
            self.assertEqual(aborts, ["/api/v1/jobs/accepted-job/abort"])
            self.assertIsNone(runner.active_job_id)
            self.assertIsNone(runner.pending_create)
            self.assertFalse(runner.executor.job_window)

    def test_python_runner_does_not_return_success_when_offline_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            runner = Stage4HardwareRunner(root, "http://10.0.0.1:8080", Path(temp))
            write_json_atomic(root / "control-plane/recovery-gate.json", {"status": "passed"})
            with patch.object(runner, "preflight"), patch.object(runner, "upload", return_value="b" * 64), patch.object(
                    runner, "job", side_effect=[{"job_id": "normal"}, {"job_id": "abort"}]), patch.object(runner, "collect"), patch(
                    "tests.fl_runtime.stage4_hardware_acceptance.validate_archive", return_value=["raw evidence missing"]) as validate:
                self.assertEqual(runner.run(Path(temp) / "fixture"), 1)
            validate.assert_called_once_with(root)
            self.assertEqual(json.loads((root / "envelope.json").read_text())["conclusion"], "failed")

    def test_output_copy_rejects_foreign_path_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            executor = AuditedExecutor(root)
            job = Path(temp) / "job"; job.mkdir()
            foreign = Path(temp) / "foreign.bin"; foreign.write_bytes(b"foreign")
            with self.assertRaises(ValueError):
                collect_server_file(executor, foreign, root / "output.bin", source_root=job)
            link = job / "output.bin"; link.symlink_to(foreign)
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                collect_server_file(executor, link, root / "output.bin", source_root=job)
            self.assertFalse((root / "output.bin").exists())

    def test_create_rejects_unsafe_job_id_before_use(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            runner = Stage4HardwareRunner(root, "http://10.0.0.1:8080", Path(temp))
            runner.pending_create = (b"{}", "key")
            with patch("tests.fl_runtime.stage4_hardware_acceptance._request", return_value=(202, {"job_id": "../escape"})):
                with self.assertRaises(FLRuntimeError):
                    runner._resolve_create()
            self.assertIsNone(runner.active_job_id)

    def test_archive_stages_are_strictly_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            record_stage(root, "preflight", status="passed")
            with self.assertRaisesRegex(ValueError, "invalid stage order"):
                record_stage(root, "normal-job", status="passed")

    def test_executor_refuses_client_ssh_during_job_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            executor = AuditedExecutor(root)
            executor.job_window = True
            with self.assertRaisesRegex(RuntimeError, "SSH is forbidden"):
                executor.run("client1", "true")

    def test_server_commands_remain_local_during_job_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            executor = AuditedExecutor(root)
            executor.job_window = True
            with patch("subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "ok\n"
                run.return_value.stderr = ""
                self.assertEqual(executor.checked("server", "true"), "ok\n")
                self.assertEqual(run.call_args.args[0][0:2], ["bash", "-lc"])

    def test_multipart_contains_only_the_model_file_part(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "model.bin"
            source.write_bytes(b"model")
            body, boundary = _multipart(source)
            self.assertEqual(body.count(("--" + boundary).encode()), 2)
            self.assertIn(b'name="file"; filename="model.bin"', body)
            self.assertTrue(body.endswith(("--" + boundary + "--\r\n").encode()))

    def test_archive_contract_lists_required_partitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "stage4-hardware-20261010T120000Z-abcdef12"
            envelope = init_archive(root, run_id=root.name, commit="a" * 40, web_url="http://10.0.0.1:8080")
            self.assertEqual(tuple(envelope["topology"]["clients"][i]["node_id"] for i in range(2)), (1, 2))
            self.assertEqual(tuple(sorted(p.name for p in root.iterdir() if p.is_dir())),
                             ("control-plane", "data-plane", "lifecycle", "management-web", "summary"))
            self.assertEqual(tuple(STAGES), ("preflight", "web-upload", "normal-job", "abort-job", "collect", "summary"))


if __name__ == "__main__":
    unittest.main()
