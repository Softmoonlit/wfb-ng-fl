"""独立 CPU FP64 审计工具的针对性测试。"""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

import pytest

import wfb_ng.fl.fp64_audit as fp64_audit

torch = pytest.importorskip("torch")

from wfb_ng.fl.artifacts import file_sha256, write_json_atomic
from wfb_ng.fl.algorithm import AlgorithmContext
from wfb_ng.fl.fp64_audit import AuditFailure, _backup_snapshot, _stable_snapshot, audit_job, main
from wfb_ng.fl.mnist import aggregate_entry
from wfb_ng.fl.reference_model import initialize_model, load_model


def _preparation(samples: int) -> dict:
    return {
        "model_format": "mnist-two-conv-fp32-v1",
        "sample_count": samples,
        "data_sha256": {
            "train-images-idx3-ubyte": "0" * 64,
            "train-labels-idx1-ubyte": "1" * 64,
        },
        "device": {
            "type": "cuda",
            "name": "controlled GPU",
            "torch_version": "test",
            "cuda_version": "test",
        },
        "data_seed": 1,
    }


def _update(path: Path, base_sha: str, node: int, samples: int, value: float,
            initial_path: Path) -> None:
    base = load_model(initial_path)
    payload = {
        "model_format": "mnist-two-conv-fp32-v1",
        "state_dict": {
            name: torch.full_like(tensor, value)
            for name, tensor in base.model.state_dict().items()
        },
        "base_model_sha256": base_sha,
        "node_id": node,
        "round_index": 1,
        "sample_count": samples,
        "optimizer_steps": (samples + 63) // 64,
        "train_loss": 2.0,
        "device": {
            "type": "cuda",
            "name": "controlled GPU",
            "torch_version": "test",
            "cuda_version": "test",
        },
    }
    torch.save(payload, path)


def _summary(job_root: Path, initial: Path, output: Path,
             updates: dict[int, Path]) -> Path:
    summary_path = job_root / "coordinator_summary.json"
    value = {
        "schema_version": 1,
        "job_id": "audit-job",
        "run_id": "audit-run",
        "status": "succeeded",
        "execution_result": "succeeded",
        "rounds_total": 1,
        "rounds_completed": 1,
        "target_nodes": [1, 2],
        "rounds": [{
            "round_index": 1,
            "input_model_path": str(initial),
            "input_model_sha256": file_sha256(str(initial)),
            "output_model_path": str(output),
            "output_model_sha256": file_sha256(str(output)),
            "committed_nodes": [1, 2],
            "updates": {
                str(node): {
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                    "sha256": file_sha256(str(path)),
                }
                for node, path in updates.items()
            },
            "metrics": {"loss": 1.0, "accuracy": 0.5},
            "verdict": True,
            "reason": "controlled audit fixture",
        }],
    }
    write_json_atomic(str(summary_path), value)
    write_json_atomic(str(job_root / "coordinator_summary.manifest.json"), {
        "schema_version": 1,
        "artifact_type": "coordinator_summary_manifest",
        "job_id": "audit-job",
        "summary_path": str(summary_path),
        "summary_sha256": file_sha256(str(summary_path)),
        "timestamp": 1,
    })
    return summary_path


def _refresh_summary_manifest(job_root: Path) -> None:
    manifest_path = job_root / "coordinator_summary.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["summary_sha256"] = file_sha256(str(job_root / "coordinator_summary.json"))
    write_json_atomic(str(manifest_path), manifest)


def _production_weight_fixture(tmp_path: Path) -> tuple[Path, Path, dict[int, Path]]:
    job_root = tmp_path / "job-audit"
    job_root.mkdir()
    initial = job_root / "initial.pt"
    initialize_model(initial, 7)
    base = load_model(initial)
    round_id = "550e8400-e29b-41d4-a716-446655440000"
    updates_root = job_root / "server-role" / "rounds" / round_id / "updates"
    updates_root.mkdir(parents=True)
    updates = {
        node: updates_root / str(node) / "update.bin"
        for node in (1, 2)
    }
    for node in (1, 2):
        (updates_root / str(node)).mkdir()
    _update(updates[1], base.sha256, 1, 1, 2.0, initial)
    _update(updates[2], base.sha256, 2, 3, 6.0, initial)
    for node, path in updates.items():
        write_json_atomic(str(path.parent / "update.manifest.json"), {
            "schema_version": 1,
            "artifact_type": "update",
            "round_id": round_id,
            "node_id": node,
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(str(path)),
        })
    context = AlgorithmContext(
        job_id="audit-job", node_id=255, target_nodes=(1, 2), rounds=1,
        artifact_dir=str(job_root), stop_event=threading.Event(),
        local_config={"client_preparation_reports": {
            1: _preparation(1), 2: _preparation(3),
        }},
    ).for_phase("aggregate", round_index=1)
    output = job_root / "global-model-round-0001.bin"
    # 测试中的唯一生产聚合调用仅用于可区分的 1:3 权重对照；
    # 审计模块本身独立导入，运行时不调用生产聚合。
    assert aggregate_entry(
        str(initial), updates, str(output), {"data_seed": 1}, context
    ) is None
    _summary(job_root, initial, output, updates)
    return job_root, output, updates


def test_backup_target_tamper_after_replace_fails_public_audit(tmp_path, monkeypatch):
    job_root, _, _ = _production_weight_fixture(tmp_path)
    original_validate = fp64_audit.validate_artifact
    injected = False

    def tamper_after_replace(path: str, size_bytes: int, expected_sha256: str,
                             *args: Any) -> None:
        nonlocal injected
        if not injected and str(path).endswith("M0.pt"):
            Path(path).write_bytes(b"short target")
            injected = True
        original_validate(path, size_bytes, expected_sha256, *args)

    monkeypatch.setattr(fp64_audit, "validate_artifact", tamper_after_replace)
    output = tmp_path / "post-replace-tamper"
    report = fp64_audit.audit_job(job_root, output)
    assert report["status"] == "failed"
    assert report["failure"]["code"] == "backup_failed"
    assert report["backup"]["status"] == "failed"
    assert (output / "fp64-audit-report.json").is_file()
    assert (job_root / "initial.pt").is_file()


def test_audit_rejects_existing_output_without_overwrite(tmp_path):
    job_root, _, _ = _production_weight_fixture(tmp_path)
    out = tmp_path / "existing-output"
    out.mkdir()
    marker = out / "marker"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(AuditFailure) as caught:
        audit_job(job_root, out)
    assert caught.value.code == "output_exists"
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("field", ["job_id", "summary_path"])
def test_summary_manifest_binds_job_and_summary_path(tmp_path, field):
    job_root, _, _ = _production_weight_fixture(tmp_path)
    manifest_path = job_root / "coordinator_summary.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest[field] = "wrong-job" if field == "job_id" else str(job_root / "other-summary.json")
    write_json_atomic(str(manifest_path), manifest)
    report = audit_job(job_root, tmp_path / "manifest-failed")
    assert report["status"] == "failed"
    assert report["failure"]["code"] == "summary_manifest_invalid"
    assert report["backup"]["status"] == "not_run"


def test_summary_missing_execution_result_is_rejected(tmp_path):
    job_root, _, _ = _production_weight_fixture(tmp_path)
    summary_path = job_root / "coordinator_summary.json"
    summary = json.loads(summary_path.read_text())
    del summary["execution_result"]
    write_json_atomic(str(summary_path), summary)
    _refresh_summary_manifest(job_root)
    report = audit_job(job_root, tmp_path / "missing-execution-result")
    assert report["status"] == "failed"
    assert report["failure"]["code"] == "execution_incomplete"


def test_fp64_audit_matches_production_aggregate_but_rejects_equal_weight(tmp_path):
    job_root, _, _ = _production_weight_fixture(tmp_path)
    report = audit_job(job_root, tmp_path / "persistent-evidence")

    assert report["status"] == "passed"
    assert report["tool"]["version"] == "1.0.0"
    assert report["missing_context_evidence"]["wireless"]["status"] == "missing"
    assert report["total_report_facts"]["model_initialization_seed"]["status"] == "known"
    assert report["total_report_facts"]["training_data_seed"]["status"] == "missing"
    assert report["total_report_facts"]["faults"]["status"] == "known"
    assert report["overall_acceptance"]["status"] == "not_assessed"
    round_report = report["rounds"][0]
    assert round_report["weights"] == {"1": 0.25, "2": 0.75}
    assert round_report["updates"][0]["weight"] == 0.25
    assert round_report["updates"][1]["weight"] == 0.75
    assert round_report["updates"][0]["runtime_round_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert round_report["round_binding"]["updates"][0]["payload_round_index"] == 1
    assert round_report["total_sample_count"] == 4
    assert all(tensor["passed"] for tensor in round_report["tensors"])
    assert all(tensor["max_abs_error"] <= tensor["max_allowed_error"]
               for tensor in round_report["tensors"])
    assert not report["rounds"][0]["weights"] == {"1": 0.5, "2": 0.5}
    # M0、M1、两个 update、两个 update manifest、summary、summary manifest
    assert len(report["backup"]["files"]) == 8
    for item in report["backup"]["files"]:
        assert item["verified"] is True
        assert Path(item["backup_path"]).is_file()


def test_fp64_audit_cli_writes_report_and_returns_success(tmp_path, capsys):
    job_root, _, _ = _production_weight_fixture(tmp_path)
    out = tmp_path / "cli-evidence"
    assert main(["--job-root", str(job_root), "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "passed"
    assert (out / "fp64-audit-report.json").is_file()
    assert (out / "backup-manifest.json").is_file()


@pytest.mark.parametrize("defect", ["missing", "tamper", "nonfinite"])
def test_fp64_audit_fails_closed_for_missing_tampered_or_nonfinite_update(tmp_path, defect):
    job_root, _, updates = _production_weight_fixture(tmp_path)
    target = updates[1]
    summary_path = job_root / "coordinator_summary.json"
    summary = json.loads(summary_path.read_text())
    if defect == "missing":
        target.unlink()
    elif defect == "tamper":
        target.write_bytes(target.read_bytes() + b"tamper")
    else:
        value = torch.load(target, weights_only=True)
        value["state_dict"]["conv1.weight"].fill_(float("nan"))
        torch.save(value, target)
        summary["rounds"][0]["updates"]["1"]["size_bytes"] = target.stat().st_size
        summary["rounds"][0]["updates"]["1"]["sha256"] = file_sha256(str(target))
        manifest_path = target.parent / "update.manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["size_bytes"] = target.stat().st_size
        manifest["sha256"] = summary["rounds"][0]["updates"]["1"]["sha256"]
        write_json_atomic(str(manifest_path), manifest)
    # 这里故意保留 summary manifest，并只改变 update/artifact 事实；
    # 任一完整性或数值缺陷都必须失败，不能由容差掩盖。
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    _refresh_summary_manifest(job_root)
    report = audit_job(job_root, tmp_path / f"failed-{defect}")
    assert report["status"] == "failed"
    assert report["backup"]["status"] == "not_run"
    assert report["failure"]["code"] in {
        "artifact_missing", "artifact_integrity_failed", "update_invalid",
        "nonfinite_parameter",
    }


def test_fp64_audit_rejects_round_or_base_identity_mismatch(tmp_path):
    job_root, _, _ = _production_weight_fixture(tmp_path)
    summary_path = job_root / "coordinator_summary.json"
    summary = json.loads(summary_path.read_text())
    summary["rounds"][0]["updates"]["1"]["path"] = summary["rounds"][0]["updates"]["2"]["path"]
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    _refresh_summary_manifest(job_root)
    report = audit_job(job_root, tmp_path / "identity-failed")
    assert report["status"] == "failed"
    assert report["failure"]["code"] in {"invalid_updates", "invalid_participants"}


def test_update_manifest_binds_path_round_node_and_body(tmp_path):
    job_root, _, updates = _production_weight_fixture(tmp_path)
    manifest_path = updates[1].parent / "update.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["node_id"] = 2
    write_json_atomic(str(manifest_path), manifest)
    report = audit_job(job_root, tmp_path / "update-manifest-failed")
    assert report["status"] == "failed"
    assert report["failure"]["code"] == "update_manifest_invalid"
    assert report["backup"]["status"] == "not_run"


def test_backup_rechecks_audited_source_hash(tmp_path):
    source = tmp_path / "source.bin"
    target = tmp_path / "backup.bin"
    source.write_bytes(b"original source")
    snapshot = _stable_snapshot(source, "test source")
    source.write_bytes(b"replaced source")
    _backup_snapshot(snapshot, target, [])
    assert target.read_bytes() == b"original source"


def test_fp64_audit_does_not_relax_fixed_tolerance(tmp_path):
    job_root, output, _ = _production_weight_fixture(tmp_path)
    wrong_output = job_root / "wrong-model-round-0001.bin"
    value = torch.load(output, weights_only=True)
    value["state_dict"]["conv1.weight"].view(-1)[0] += 0.01
    torch.save(value, wrong_output)
    summary_path = job_root / "coordinator_summary.json"
    summary = json.loads(summary_path.read_text())
    summary["rounds"][0]["output_model_path"] = str(wrong_output)
    summary["rounds"][0]["output_model_sha256"] = file_sha256(str(wrong_output))
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    _refresh_summary_manifest(job_root)

    report = audit_job(job_root, tmp_path / "tolerance-failed")
    assert report["status"] == "failed"
    assert report["failure"]["code"] == "tolerance_exceeded"
    assert report["rounds"][0]["passed"] is False
    assert report["rounds"][0]["max_abs_error"] > report["rounds"][0]["max_allowed_error"]
    assert report["rounds"][0]["max_violations"] > 0
    assert report["backup"]["status"] == "verified"
    assert len(report["backup"]["files"]) == 8
