"""持久化 MNIST FedAvg 制品的独立 CPU FP64 审计工具。

本模块只消费 Coordinator summary 和 summary 指向的文件，不导入或调用生产聚合入口；
参考值在 CPU 上独立计算，并按固定容差与实际 FP32 输出逐元素比较。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from collections.abc import Mapping
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Dict, List, NoReturn, Optional, Sequence, Tuple
import uuid

import torch

from .artifacts import (
    CHUNK_SIZE,
    file_sha256,
    fsync_directory,
    preflight_artifact,
    validate_artifact,
    write_json_atomic,
)
from .errors import FLRuntimeError
from .reference_model import MAX_MODEL_BYTES, MODEL_FORMAT, load_model, validate_state_dict


TOOL_NAME = "wfb_ng.fl.fp64_audit"
TOOL_VERSION = "1.0.0"
REPORT_SCHEMA_VERSION = 1
AUDIT_RULE_VERSION = "fp64-fedavg-v1"
ABSOLUTE_TOLERANCE = 1e-6
RELATIVE_TOLERANCE = 1e-5
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

UPDATE_FIELDS = {
    "model_format",
    "state_dict",
    "base_model_sha256",
    "node_id",
    "round_index",
    "sample_count",
    "optimizer_steps",
    "train_loss",
    "device",
}
DEVICE_FIELDS = {"type", "name", "torch_version", "cuda_version"}
BATCH_SIZE = 64


class AuditFailure(Exception):
    """确定、可写入报告的审计失败。"""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.partial: Optional[Dict[str, Any]] = None


def _fail(code: str, message: str, **details: Any) -> NoReturn:
    raise AuditFailure(code, message, **details)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _require_sha256(value: Any, field: str) -> str:
    if not _is_sha256(value):
        _fail("invalid_summary", f"{field} must be a lowercase SHA-256")
    return value


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("invalid_summary", f"{field} must be a non-empty string")
    return value


def _require_positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        _fail("invalid_summary", f"{field} must be a positive integer")
    return value


def _require_node_list(value: Any, field: str) -> Tuple[int, ...]:
    if not isinstance(value, list) or not value:
        _fail("invalid_participants", f"{field} must be a non-empty list")
    nodes = []
    for node in value:
        if type(node) is not int or not 1 <= node <= 10:
            _fail("invalid_participants", f"{field} contains an invalid node")
        nodes.append(node)
    if nodes != sorted(set(nodes)):
        _fail("invalid_participants", f"{field} must be sorted and unique")
    return tuple(nodes)


def _node_key(value: Any) -> int:
    if type(value) is int and 1 <= value <= 10:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        parsed = int(value)
        if 1 <= parsed <= 10 and str(parsed) == value:
            return parsed
    _fail("invalid_participants", "updates contains an invalid node key")


def _resolve_source(path_value: Any, job_root: Path, field: str) -> Path:
    value = _require_string(path_value, field)
    path = Path(value)
    if not path.is_absolute():
        path = job_root / path
    return path.resolve(strict=False)


def _stable_snapshot(path: Path, field: str,
                     max_size_bytes: Optional[int] = None) -> Dict[str, Any]:
    """读取一份稳定字节快照，供解析、摘要、校验和备份共同使用。"""
    if not path.exists():
        _fail("artifact_missing", f"{field} does not exist", path=str(path))
    if not path.is_file():
        _fail("artifact_invalid", f"{field} is not a regular file", path=str(path))
    try:
        stream, before = preflight_artifact(str(path), max_size_bytes)
    except FLRuntimeError as exc:
        _fail(exc.error_code if exc.error_code == "artifact_too_large" else "artifact_unreadable",
              f"{field} cannot be opened", path=str(path), error_code=exc.error_code)
    digest = hashlib.sha256()
    data = bytearray()
    try:
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            data.extend(chunk)
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    except OSError as exc:
        _fail("artifact_unreadable", f"{field} cannot be read", path=str(path),
              error_type=type(exc).__name__)
    finally:
        stream.close()
    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if (any(getattr(before, key) != getattr(after, key) for key in identity)
            or len(data) != after.st_size):
        _fail("artifact_changed", f"{field} changed while being read", path=str(path))
    return {
        "path": str(path),
        "data": bytes(data),
        "size_bytes": len(data),
        "sha256": digest.hexdigest(),
        "identity": {key: getattr(before, key) for key in identity},
    }


def _strict_json_object(pairs: Any) -> Dict[str, Any]:
    value: Dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid JSON constant: {value}")


def _parse_snapshot_json(snapshot: Dict[str, Any], field: str) -> Dict[str, Any]:
    try:
        value = json.loads(
            snapshot["data"].decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError, TypeError) as exc:
        _fail("invalid_manifest", f"{field} is not strict JSON", path=snapshot["path"],
              error_type=type(exc).__name__)
    if not isinstance(value, dict):
        _fail("invalid_manifest", f"{field} must be a JSON object", path=snapshot["path"])
    return value


def _runtime_round_binding(path: Path, node_id: int, size_bytes: int,
                           sha256: str) -> Dict[str, Any]:
    parts = path.parts
    if (path.name != "update.bin" or len(parts) < 5
            or parts[-3] != "updates" or parts[-5] != "rounds"
            or parts[-2] != str(node_id)):
        _fail("update_manifest_invalid", "update path does not encode rounds/<round>/updates/<node>/update.bin",
              path=str(path), node_id=node_id)
    runtime_round_id = parts[-4]
    try:
        parsed = uuid.UUID(runtime_round_id)
    except (ValueError, AttributeError) as exc:
        _fail("update_manifest_invalid", "update path round id is not a UUID", path=str(path),
              error_type=type(exc).__name__)
    if parsed.version != 4 or str(parsed) != runtime_round_id:
        _fail("update_manifest_invalid", "update path round id is not canonical UUID v4",
              path=str(path))
    manifest_path = path.parent / "update.manifest.json"
    try:
        snapshot = _stable_snapshot(manifest_path, "update manifest")
    except AuditFailure as exc:
        if exc.code == "artifact_missing":
            _fail("update_manifest_missing", "update manifest does not exist",
                  path=str(manifest_path))
        raise
    manifest = _parse_snapshot_json(snapshot, "update manifest")
    if set(manifest) != {"schema_version", "artifact_type", "round_id", "node_id",
                         "size_bytes", "sha256"}:
        _fail("update_manifest_invalid", "update manifest fields are invalid",
              path=str(manifest_path))
    if (manifest["schema_version"] != 1 or manifest["artifact_type"] != "update"
            or manifest["round_id"] != runtime_round_id
            or type(manifest["node_id"]) is not int
            or manifest["node_id"] != node_id
            or type(manifest["size_bytes"]) is not int
            or manifest["size_bytes"] != size_bytes
            or manifest["sha256"] != sha256):
        _fail("update_manifest_invalid", "update manifest does not match path and summary",
              path=str(manifest_path), node_id=node_id, runtime_round_id=runtime_round_id)
    _require_sha256(manifest["sha256"], "update manifest sha256")
    return {
        "runtime_round_id": runtime_round_id,
        "manifest_path": str(manifest_path),
        "manifest_size_bytes": snapshot["size_bytes"],
        "manifest_sha256": snapshot["sha256"],
        "manifest_snapshot": snapshot,
    }


def _backup_snapshot(snapshot: Dict[str, Any], target: Path,
                     files: List[Dict[str, Any]]) -> None:
    if target.exists():
        _fail("backup_target_exists", f"backup target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=str(target.parent),
                                         prefix=f".{target.name}.", delete=False) as stream:
            temp_path = stream.name
            stream.write(snapshot["data"])
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
        temp_path = None
        fsync_directory(str(target.parent))
        try:
            validate_artifact(str(target), snapshot["size_bytes"], snapshot["sha256"])
        except FLRuntimeError as exc:
            _fail("backup_failed", "backup target failed post-replace integrity verification",
                  path=str(target), error_code=exc.error_code)
    except AuditFailure:
        raise
    except (OSError, FLRuntimeError) as exc:
        _fail("backup_failed", f"cannot backup snapshot {snapshot['path']}",
              path=snapshot["path"], error_type=type(exc).__name__)
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
    files.append({
        "source_path": snapshot["path"],
        "backup_path": str(target),
        "size_bytes": snapshot["size_bytes"],
        "sha256": snapshot["sha256"],
        "verified": True,
        "from_snapshot": True,
    })


def _read_summary_snapshot(summary_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not summary_path.is_file():
        _fail("summary_missing", f"coordinator summary does not exist: {summary_path}")
    snapshot = _stable_snapshot(summary_path, "coordinator summary")
    return _parse_snapshot_json(snapshot, "coordinator summary"), snapshot


def _summary_integrity(summary_path: Path, job_id: str,
                       summary_snapshot: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    manifest_path = summary_path.with_name("coordinator_summary.manifest.json")
    if not manifest_path.is_file():
        _fail("summary_manifest_missing", f"coordinator summary manifest does not exist: {manifest_path}")
    manifest_snapshot = _stable_snapshot(manifest_path, "coordinator summary manifest")
    manifest = _parse_snapshot_json(manifest_snapshot, "coordinator summary manifest")
    timestamp = manifest.get("timestamp")
    if (set(manifest) != {"schema_version", "artifact_type", "job_id",
                          "summary_path", "summary_sha256", "timestamp"}
            or manifest.get("schema_version") != 1
            or manifest.get("artifact_type") != "coordinator_summary_manifest"
            or manifest.get("job_id") != job_id
            or not isinstance(manifest.get("summary_path"), str)
            or not _is_sha256(manifest.get("summary_sha256"))
            or not isinstance(timestamp, (int, float))
            or isinstance(timestamp, bool)):
        _fail("summary_manifest_invalid", "coordinator summary manifest fields are invalid")
    if not math.isfinite(float(timestamp)):
        _fail("summary_manifest_invalid", "coordinator summary manifest timestamp is not finite")
    manifest_summary_path = Path(manifest["summary_path"])
    if not manifest_summary_path.is_absolute():
        manifest_summary_path = manifest_path.parent / manifest_summary_path
    if manifest_summary_path.resolve(strict=False) != summary_path.resolve(strict=False):
        _fail("summary_manifest_invalid", "coordinator summary manifest summary_path does not match input",
              expected=str(summary_path.resolve(strict=False)),
              actual=str(manifest_summary_path.resolve(strict=False)))
    if manifest["summary_sha256"] != summary_snapshot["sha256"]:
        _fail("summary_tampered", "coordinator summary SHA-256 does not match its manifest",
              expected=manifest["summary_sha256"], actual=summary_snapshot["sha256"])
    return {
        "status": "verified",
        "path": str(manifest_path),
        "summary_sha256": summary_snapshot["sha256"],
        "summary_size_bytes": summary_snapshot["size_bytes"],
        "manifest_sha256": manifest_snapshot["sha256"],
        "manifest_size_bytes": manifest_snapshot["size_bytes"],
    }, manifest_snapshot


def _validate_summary(summary: Dict[str, Any]) -> Tuple[Tuple[int, ...], List[Dict[str, Any]]]:
    if summary.get("schema_version") != REPORT_SCHEMA_VERSION:
        _fail("invalid_summary", "summary schema_version is unsupported")
    for field in ("job_id", "run_id", "status", "rounds_total", "rounds_completed",
                  "target_nodes", "rounds"):
        if field not in summary:
            _fail("invalid_summary", f"summary is missing {field}")
    _require_string(summary["job_id"], "job_id")
    _require_string(summary["run_id"], "run_id")
    status = summary["status"]
    if status not in ("succeeded", "failed", "aborted"):
        _fail("invalid_summary", "summary status is invalid")
    execution_result = summary.get("execution_result")
    if execution_result != "succeeded":
        _fail("execution_incomplete", "summary must explicitly report execution_result=succeeded",
              execution_result=execution_result)
    rounds_total = _require_positive_int(summary["rounds_total"], "rounds_total")
    if type(summary["rounds_completed"]) is not int or summary["rounds_completed"] != rounds_total:
        _fail("execution_incomplete", "summary does not contain all completed rounds")
    nodes = _require_node_list(summary["target_nodes"], "target_nodes")
    rounds = summary["rounds"]
    if not isinstance(rounds, list) or len(rounds) != rounds_total:
        _fail("invalid_rounds", "summary rounds does not match rounds_total")
    return nodes, rounds


def _load_model_snapshot(snapshot: Dict[str, Any], expected_sha256: str,
                         field: str) -> Dict[str, Any]:
    if snapshot["sha256"] != expected_sha256:
        _fail("artifact_integrity_failed", f"{field} SHA-256 does not match summary",
              path=snapshot["path"], expected=expected_sha256, actual=snapshot["sha256"])
    try:
        # load_model 只有路径接口；这里只把已冻结字节复制到独立暂存目录，
        # 严禁它重新打开审计源路径。
        with tempfile.TemporaryDirectory(prefix="fp64-model-staging-") as staging:
            staged = Path(staging) / "model.pt"
            staged.write_bytes(snapshot["data"])
            artifact = load_model(str(staged))
    except FLRuntimeError as exc:
        _fail("model_invalid", f"{field} is not a valid reference model",
              path=snapshot["path"], error_code=exc.error_code)
    if artifact.sha256 != snapshot["sha256"]:
        _fail("artifact_integrity_failed", f"{field} staging hash changed",
              path=snapshot["path"])
    return {
        "path": snapshot["path"],
        "size_bytes": snapshot["size_bytes"],
        "sha256": snapshot["sha256"],
        "initialization_seed": artifact.initialization_seed,
        "artifact": artifact,
    }


def _read_update(path: Path, size_bytes: int, expected_sha256: str,
                 node_id: int, round_index: int, input_sha256: str) -> Dict[str, Any]:
    body_snapshot = _stable_snapshot(path, "update", MAX_MODEL_BYTES)
    if (body_snapshot["size_bytes"] != size_bytes
            or body_snapshot["sha256"] != expected_sha256):
        _fail("artifact_integrity_failed", "update snapshot does not match summary",
              path=str(path), expected_size_bytes=size_bytes,
              actual_size_bytes=body_snapshot["size_bytes"],
              expected_sha256=expected_sha256, actual_sha256=body_snapshot["sha256"])
    binding = _runtime_round_binding(path, node_id, size_bytes, expected_sha256)
    try:
        value = torch.load(io.BytesIO(body_snapshot["data"]), map_location="cpu", weights_only=True)
    except Exception as exc:
        _fail("update_invalid", "update payload cannot be loaded from frozen bytes",
              path=str(path), error_type=type(exc).__name__)
    if type(value) is not dict or set(value) != UPDATE_FIELDS:
        _fail("update_invalid", "update payload fields are invalid", path=str(path))
    if value["model_format"] != MODEL_FORMAT:
        _fail("update_invalid", "update model_format is invalid", path=str(path))
    if type(value["node_id"]) is not int or value["node_id"] != node_id:
        _fail("update_identity_mismatch", "update node_id does not match summary", path=str(path))
    if type(value["round_index"]) is not int or value["round_index"] != round_index:
        _fail("update_identity_mismatch", "update round_index does not match summary", path=str(path))
    if value["base_model_sha256"] != input_sha256:
        _fail("update_identity_mismatch", "update base_model_sha256 does not match input model",
              path=str(path), expected=input_sha256, actual=value["base_model_sha256"])
    sample_count = value["sample_count"]
    if type(sample_count) is not int or not 0 < sample_count <= 60000:
        _fail("update_invalid", "update sample_count is invalid", path=str(path))
    steps = value["optimizer_steps"]
    if type(steps) is not int or steps != (sample_count + BATCH_SIZE - 1) // BATCH_SIZE:
        _fail("update_invalid", "update optimizer_steps is invalid", path=str(path))
    loss = value["train_loss"]
    if type(loss) not in (int, float) or not math.isfinite(loss) or loss < 0:
        _fail("update_invalid", "update train_loss is not finite", path=str(path))
    device = value["device"]
    if (type(device) is not dict or set(device) != DEVICE_FIELDS
            or device.get("type") != "cuda"
            or any(type(item) is not str or not item or len(item) > 128
                   for item in device.values())):
        _fail("update_invalid", "update device description is invalid", path=str(path))
    try:
        state = value["state_dict"]
        if isinstance(state, dict):
            for name, tensor in state.items():
                if isinstance(tensor, torch.Tensor) and not torch.isfinite(tensor).all().item():
                    _fail("nonfinite_parameter", f"update tensor {name} contains non-finite values",
                          path=str(path))
        validate_state_dict(state)
    except AuditFailure:
        raise
    except FLRuntimeError as exc:
        _fail("update_invalid", "update state_dict is invalid", path=str(path),
              error_code=exc.error_code)
    return {
        "path": str(path),
        "size_bytes": body_snapshot["size_bytes"],
        "sha256": body_snapshot["sha256"],
        "value": value,
        "sample_count": sample_count,
        "base_model_sha256": value["base_model_sha256"],
        "runtime_round_id": binding["runtime_round_id"],
        "manifest_path": binding["manifest_path"],
        "manifest_size_bytes": binding["manifest_size_bytes"],
        "manifest_sha256": binding["manifest_sha256"],
        "manifest_snapshot": binding["manifest_snapshot"],
        "body_snapshot": body_snapshot,
    }


def _validate_state_compatibility(update: Dict[str, Any], model: Dict[str, Any],
                                   field: str) -> None:
    update_state = update["value"]["state_dict"]
    model_state = model["artifact"].model.state_dict()
    if set(update_state) != set(model_state):
        _fail("structure_mismatch", f"{field} parameter keys do not match model")
    for name, expected in model_state.items():
        actual = update_state[name]
        if (actual.device.type != "cpu" or actual.dtype != torch.float32
                or actual.shape != expected.shape or actual.layout != expected.layout):
            _fail("structure_mismatch", f"{field} tensor {name} shape/dtype/device mismatch")
        if not torch.isfinite(actual).all().item():
            _fail("nonfinite_parameter", f"{field} tensor {name} contains non-finite values")


def _reference_round(input_model: Dict[str, Any], output_model: Dict[str, Any],
                     updates: Sequence[Dict[str, Any]], round_index: int,
                     target_nodes: Tuple[int, ...]) -> Dict[str, Any]:
    total_samples = sum(item["sample_count"] for item in updates)
    if total_samples <= 0:
        _fail("update_invalid", "total sample_count must be positive", round_index=round_index)
    weights = {
        item["value"]["node_id"]: item["sample_count"] / total_samples
        for item in updates
    }
    update_state = [item["value"]["state_dict"] for item in updates]
    input_state = input_model["artifact"].model.state_dict()
    output_state = output_model["artifact"].model.state_dict()
    if set(output_state) != set(input_state):
        _fail("structure_mismatch", "output model parameter keys do not match input model",
              round_index=round_index)
    tensor_reports: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    all_passed = True
    max_abs_error = 0.0
    max_allowed_error = 0.0
    max_violations = 0
    total_violations = 0
    for name, input_tensor in input_state.items():
        tensors = []
        for item, state in zip(updates, update_state):
            tensor = state[name]
            if (tensor.device.type != "cpu" or tensor.dtype != torch.float32
                    or tensor.shape != input_tensor.shape):
                _fail("structure_mismatch", f"round {round_index} tensor {name} mismatch")
            tensors.append(tensor.to(dtype=torch.float64))
        # 独立参考路径：先堆叠客户端张量，再广播权重并沿客户端维归约。
        # 生产实现使用零初始化后顺序累加；此处不复用其累加顺序。
        stacked = torch.stack(tensors, dim=0)
        weight_tensor = torch.tensor(
            [weights[item["value"]["node_id"]] for item in updates],
            dtype=torch.float64,
        ).reshape((len(tensors),) + (1,) * input_tensor.ndim)
        reference = (stacked * weight_tensor).sum(dim=0, dtype=torch.float64)
        if not torch.isfinite(reference).all().item():
            _fail("nonfinite_reference", f"round {round_index} tensor {name} reference is non-finite")
        actual = output_state[name]
        if (actual.device.type != "cpu" or actual.dtype != torch.float32
                or actual.shape != input_tensor.shape):
            _fail("structure_mismatch", f"round {round_index} output tensor {name} mismatch")
        actual64 = actual.to(dtype=torch.float64)
        difference = (actual64 - reference).abs()
        allowed = ABSOLUTE_TOLERANCE + RELATIVE_TOLERANCE * reference.abs()
        passed = bool(torch.all(difference <= allowed).item())
        violations = int((difference > allowed).sum().item())
        tensor_max_abs_error = float(difference.max().item())
        tensor_max_allowed_error = float(allowed.max().item())
        max_abs_error = max(max_abs_error, tensor_max_abs_error)
        max_allowed_error = max(max_allowed_error, tensor_max_allowed_error)
        max_violations = max(max_violations, violations)
        total_violations += violations
        tensor_report = {
            "name": name,
            "shape": list(input_tensor.shape),
            "dtype": str(input_tensor.dtype),
            "reference_dtype": str(reference.dtype),
            "max_abs_error": tensor_max_abs_error,
            "max_allowed_error": tensor_max_allowed_error,
            "violations": violations,
            "passed": passed,
        }
        tensor_reports.append(tensor_report)
        if not passed:
            errors.append(tensor_report)
        all_passed = all_passed and passed
    return {
        "round_index": round_index,
        "participants": list(target_nodes),
        "total_sample_count": total_samples,
        "weights": {str(node): weights[node] for node in target_nodes},
        "tensors": tensor_reports,
        "errors": errors,
        "max_abs_error": max_abs_error,
        "max_allowed_error": max_allowed_error,
        "max_violations": max_violations,
        "total_violations": total_violations,
        "passed": all_passed,
    }


def _model_report(record: Dict[str, Any], logical_name: str) -> Dict[str, Any]:
    return {
        "logical_name": logical_name,
        "source_path": record["path"],
        "size_bytes": record["size_bytes"],
        "sha256": record["sha256"],
        "initialization_seed": record["initialization_seed"],
    }


def _audit_summary(job_root: Path, summary_path: Path,
                   summary: Dict[str, Any], summary_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    target_nodes, rounds = _validate_summary(summary)
    summary_integrity, summary_manifest_snapshot = _summary_integrity(
        summary_path, summary["job_id"], summary_snapshot)
    model_reports: List[Dict[str, Any]] = []
    round_reports: List[Dict[str, Any]] = []
    previous_output: Optional[Dict[str, Any]] = None
    initial_seed: Optional[int] = None
    seen_model_paths: set[str] = set()
    seen_update_paths: set[str] = set()
    seen_runtime_round_ids: set[str] = set()
    model_snapshots: Dict[str, Dict[str, Any]] = {}
    update_body_snapshots: Dict[Tuple[int, int], Dict[str, Any]] = {}
    update_manifest_snapshots: Dict[Tuple[int, int], Dict[str, Any]] = {}
    tolerance_failures: List[Dict[str, Any]] = []

    for expected_index, row in enumerate(rounds, 1):
        if not isinstance(row, dict):
            _fail("invalid_rounds", f"round {expected_index} must be an object")
        required = ("round_index", "input_model_path", "input_model_sha256",
                    "output_model_path", "output_model_sha256", "committed_nodes", "updates")
        for field in required:
            if field not in row:
                _fail("invalid_rounds", f"round {expected_index} is missing {field}")
        if row["round_index"] != expected_index:
            _fail("invalid_rounds", "round_index sequence is not contiguous",
                  expected=expected_index, actual=row["round_index"])
        committed = _require_node_list(row["committed_nodes"],
                                       f"round {expected_index}.committed_nodes")
        if committed != target_nodes:
            _fail("invalid_participants", f"round {expected_index} participants do not match target_nodes")
        input_path = _resolve_source(row["input_model_path"], job_root,
                                     f"round {expected_index}.input_model_path")
        output_path = _resolve_source(row["output_model_path"], job_root,
                                      f"round {expected_index}.output_model_path")
        input_sha256 = _require_sha256(row["input_model_sha256"],
                                       f"round {expected_index}.input_model_sha256")
        output_sha256 = _require_sha256(row["output_model_sha256"],
                                        f"round {expected_index}.output_model_sha256")
        if str(input_path) not in model_snapshots:
            model_snapshots[str(input_path)] = _stable_snapshot(
                input_path, f"round {expected_index} input model", MAX_MODEL_BYTES)
        if str(output_path) not in model_snapshots:
            model_snapshots[str(output_path)] = _stable_snapshot(
                output_path, f"round {expected_index} output model", MAX_MODEL_BYTES)
        input_model = _load_model_snapshot(
            model_snapshots[str(input_path)], input_sha256,
            f"round {expected_index} input model")
        output_model = _load_model_snapshot(
            model_snapshots[str(output_path)], output_sha256,
            f"round {expected_index} output model")
        if input_path == output_path:
            _fail("model_chain_invalid", f"round {expected_index} input/output model path is identical")
        if previous_output is not None:
            if (input_path != Path(previous_output["path"])
                    or input_sha256 != previous_output["sha256"]):
                _fail("model_chain_invalid", f"round {expected_index} input is not previous output")
        if initial_seed is None:
            initial_seed = input_model["initialization_seed"]
            model_reports.append(_model_report(input_model, "M0"))
            seen_model_paths.add(str(input_path))
        elif input_model["initialization_seed"] != initial_seed:
            _fail("model_chain_invalid", "model initialization seed changed across rounds")
        if output_model["initialization_seed"] != initial_seed:
            _fail("model_chain_invalid", f"round {expected_index} output initialization seed changed")
        if str(output_path) in seen_model_paths:
            _fail("model_chain_invalid", f"round {expected_index} output model path was reused")
        seen_model_paths.add(str(output_path))
        model_reports.append(_model_report(output_model, f"M{expected_index}"))

        raw_updates = row["updates"]
        if not isinstance(raw_updates, Mapping):
            _fail("invalid_updates", f"round {expected_index}.updates must be an object")
        update_entries: Dict[int, Dict[str, Any]] = {}
        for raw_node, value in raw_updates.items():
            node = _node_key(raw_node)
            if node in update_entries:
                _fail("invalid_participants", f"round {expected_index} has duplicate node")
            if not isinstance(value, dict):
                _fail("invalid_updates", f"round {expected_index} node {node} entry must be an object")
            if set(value) != {"path", "size_bytes", "sha256"}:
                _fail("invalid_updates", f"round {expected_index} node {node} fields are invalid")
            size_bytes = value["size_bytes"]
            if type(size_bytes) is not int or size_bytes < 0:
                _fail("invalid_updates", f"round {expected_index} node {node} size_bytes is invalid")
            sha256 = _require_sha256(value["sha256"],
                                     f"round {expected_index} node {node}.sha256")
            update_path = _resolve_source(value["path"], job_root,
                                          f"round {expected_index} node {node}.path")
            if str(update_path) in seen_update_paths:
                _fail("invalid_updates", f"update path is reused: {update_path}")
            seen_update_paths.add(str(update_path))
            update_entries[node] = {
                "node_id": node,
                "path": update_path,
                "size_bytes": size_bytes,
                "sha256": sha256,
            }
        if tuple(sorted(update_entries)) != target_nodes:
            _fail("invalid_participants", f"round {expected_index} update nodes do not match target_nodes")

        updates: List[Dict[str, Any]] = []
        update_reports: List[Dict[str, Any]] = []
        for node in target_nodes:
            entry = update_entries[node]
            update = _read_update(entry["path"], entry["size_bytes"], entry["sha256"],
                                   node, expected_index, input_sha256)
            _validate_state_compatibility(update, input_model,
                                          f"round {expected_index} node {node} update")
            update["node_id"] = node
            updates.append(update)
            update_manifest_snapshots[(expected_index, node)] = update["manifest_snapshot"]
            update_body_snapshots[(expected_index, node)] = update["body_snapshot"]
            update_reports.append({
                "node_id": node,
                "source_path": str(entry["path"]),
                "size_bytes": entry["size_bytes"],
                "sha256": entry["sha256"],
                "base_model_sha256": update["base_model_sha256"],
                "payload_round_index": update["value"]["round_index"],
                "sample_count": update["sample_count"],
                "runtime_round_id": update["runtime_round_id"],
                "manifest_path": update["manifest_path"],
                "manifest_size_bytes": update["manifest_size_bytes"],
                "manifest_sha256": update["manifest_sha256"],
            })
        runtime_round_ids = {update["runtime_round_id"] for update in updates}
        if len(runtime_round_ids) != 1:
            _fail("round_binding_invalid", f"round {expected_index} updates use different runtime rounds")
        runtime_round_id = next(iter(runtime_round_ids))
        if runtime_round_id in seen_runtime_round_ids:
            _fail("round_binding_invalid", f"runtime round id is reused: {runtime_round_id}")
        seen_runtime_round_ids.add(runtime_round_id)
        # 这里只计算权重分母这个标量；参数张量归约在独立参考路径中完成。
        total_samples = sum(item["sample_count"] for item in updates)
        if total_samples <= 0:
            _fail("update_invalid", f"round {expected_index} total sample_count is invalid")
        for item, update_report in zip(updates, update_reports):
            update_report["weight"] = item["sample_count"] / total_samples
        numeric = _reference_round(input_model, output_model, updates,
                                   expected_index, target_nodes)
        numeric["input_model_sha256"] = input_sha256
        numeric["output_model_sha256"] = output_sha256
        numeric["input_model_path"] = str(input_path)
        numeric["output_model_path"] = str(output_path)
        numeric["updates"] = update_reports
        numeric["runtime_round_id"] = runtime_round_id
        numeric["round_binding"] = {
            "round_index": expected_index,
            "runtime_round_id": runtime_round_id,
            "participants": list(target_nodes),
            "input_model_sha256": input_sha256,
            "output_model_sha256": output_sha256,
            "updates": [
                {
                    "node_id": report["node_id"],
                    "manifest_path": report["manifest_path"],
                    "manifest_sha256": report["manifest_sha256"],
                    "body_path": report["source_path"],
                    "body_sha256": report["sha256"],
                    "body_size_bytes": report["size_bytes"],
                    "base_model_sha256": report["base_model_sha256"],
                    "payload_round_index": report["payload_round_index"],
                }
                for report in update_reports
            ],
        }
        round_reports.append(numeric)
        if not numeric["passed"]:
            tolerance_failures.append({
                "round_index": expected_index,
                "runtime_round_id": runtime_round_id,
                "max_abs_error": numeric["max_abs_error"],
                "max_allowed_error": numeric["max_allowed_error"],
                "max_violations": numeric["max_violations"],
                "total_violations": numeric["total_violations"],
                "errors": numeric["errors"],
            })
        previous_output = output_model

    audit = {
        "summary_integrity": summary_integrity,
        "total_report_facts": _total_report_facts(summary, initial_seed),
        "job": {
            "job_id": summary["job_id"],
            "run_id": summary["run_id"],
            "job_root": str(job_root),
            "target_nodes": list(target_nodes),
            "rounds_total": summary["rounds_total"],
        },
        "numeric_status": "failed" if tolerance_failures else "passed",
        "models": model_reports,
        "rounds": round_reports,
        "errors": tolerance_failures,
        "_snapshots": {
            "summary": summary_snapshot,
            "summary_manifest": summary_manifest_snapshot,
            "models": model_snapshots,
            "update_bodies": update_body_snapshots,
            "update_manifests": update_manifest_snapshots,
        },
    }
    if tolerance_failures:
        failure = AuditFailure(
            "tolerance_exceeded",
            "one or more rounds exceed the fixed FP64 tolerance",
            rounds=tolerance_failures,
        )
        failure.partial = audit
        raise failure
    return audit


def _backup_sources(out_dir: Path, summary_path: Path, audit: Dict[str, Any]) -> Dict[str, Any]:
    backup_root = out_dir / "backup"
    snapshots = audit["_snapshots"]
    files: List[Dict[str, Any]] = []
    models_dir = backup_root / "models"
    updates_dir = backup_root / "updates"
    metadata_dir = backup_root / "metadata"
    for directory in (models_dir, updates_dir, metadata_dir):
        directory.mkdir(parents=True, exist_ok=True)
    for model in audit["models"]:
        source = Path(model["source_path"])
        suffix = source.suffix or ".bin"
        target = models_dir / f"{model['logical_name']}{suffix}"
        _backup_snapshot(snapshots["models"][str(source)], target, files)
        model["backup_path"] = str(target)
    for row in audit["rounds"]:
        round_dir = updates_dir / f"round-{row['round_index']:04d}"
        round_dir.mkdir(parents=True, exist_ok=True)
        for update in row["updates"]:
            source = Path(update["source_path"])
            node_dir = round_dir / f"node-{update['node_id']}"
            node_dir.mkdir(parents=True, exist_ok=True)
            target = node_dir / source.name
            key = (row["round_index"], update["node_id"])
            _backup_snapshot(snapshots["update_bodies"][key], target, files)
            update["backup_path"] = str(target)
            manifest_target = node_dir / "update.manifest.json"
            _backup_snapshot(snapshots["update_manifests"][key], manifest_target, files)
            update["backup_manifest_path"] = str(manifest_target)
    _backup_snapshot(snapshots["summary"], metadata_dir / "coordinator_summary.json", files)
    _backup_snapshot(snapshots["summary_manifest"],
                     metadata_dir / "coordinator_summary.manifest.json", files)
    manifest = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "artifact_type": "fp64_audit_backup_manifest",
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "files": files,
    }
    manifest_path = out_dir / "backup-manifest.json"
    write_json_atomic(str(manifest_path), manifest)
    return {
        "status": "verified",
        "root": str(backup_root),
        "manifest_path": str(manifest_path),
        "manifest_sha256": file_sha256(str(manifest_path)),
        "files": files,
    }


def _missing_context_evidence() -> Dict[str, Dict[str, str]]:
    return {
        "wireless": {"status": "missing", "reason": "not supplied to the numeric audit tool"},
        "web": {"status": "missing", "reason": "not supplied to the numeric audit tool"},
        "environment": {"status": "missing", "reason": "not supplied to the numeric audit tool"},
    }


def _total_report_facts(summary: Dict[str, Any], initial_model_seed: Optional[int]) -> Dict[str, Any]:
    missing = {
        "status": "missing",
        "reason": "not present in coordinator_summary and not supplied to this CLI",
    }
    rounds_value = summary.get("rounds")
    rounds: List[Any] = rounds_value if isinstance(rounds_value, list) else []
    round_metrics = [
        {
            "round_index": row.get("round_index"),
            "metrics": row.get("metrics"),
            "verdict": row.get("verdict"),
            "reason": row.get("reason"),
        }
        for row in rounds if isinstance(row, dict)
    ]
    metrics_value = {
        "initial_metrics": summary.get("initial_metrics"),
        "rounds": round_metrics,
        "effect_result": summary.get("effect_result"),
    }
    has_metrics = (
        metrics_value["initial_metrics"] is not None
        or any(item["metrics"] is not None for item in round_metrics)
        or metrics_value["effect_result"] is not None
    )
    return {
        "config": dict(missing),
        "seed": {
            "status": "partial",
            "model_initialization": {
                "status": "known" if initial_model_seed is not None else "missing",
                "source": "M0 model initialization metadata" if initial_model_seed is not None else "not supplied",
                "value": initial_model_seed,
            },
            "training_data": {
                "status": "missing",
                "reason": "training data_seed is not persisted in coordinator summary",
            },
        },
        "model_initialization_seed": {
            "status": "known" if initial_model_seed is not None else "missing",
            "source": "M0 model initialization metadata" if initial_model_seed is not None else "not supplied",
            "value": initial_model_seed,
        },
        "training_data_seed": {
            "status": "missing",
            "reason": "training data_seed is not persisted in coordinator summary",
        },
        "all_attempts": dict(missing),
        "metrics": {
            "status": "present" if has_metrics else "missing",
            "source": "coordinator_summary" if has_metrics else "not supplied",
            "value": metrics_value if has_metrics else None,
        },
        "client_audit": dict(missing),
        "faults": {
            "status": "known",
            "source": "coordinator_summary",
            "value": {
                key: summary[key]
                for key in ("status", "error_code", "error_message", "events")
                if key in summary
            },
        },
        "web": dict(missing),
        "radio": dict(missing),
        "environment": dict(missing),
    }


def _base_report(job_root: Path, out_dir: Path) -> Dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "audit_rule": {
            "version": AUDIT_RULE_VERSION,
            "reference_precision": "CPU torch.float64",
            "actual_precision": "serialized torch.float32",
            "absolute_tolerance": ABSOLUTE_TOLERANCE,
            "relative_tolerance": RELATIVE_TOLERANCE,
            "formula": "abs(actual-reference) <= 1e-6 + 1e-5 * abs(reference)",
        },
        "scope": "numeric_only",
        "status": "failed",
        "overall_acceptance": {
            "status": "not_assessed",
            "reason": "wireless, Web, environment and final multi-run acceptance are outside this CLI",
        },
        "job_root": str(job_root),
        "out": str(out_dir),
        "missing_context_evidence": _missing_context_evidence(),
        "total_report_facts": {
            key: {"status": "missing", "reason": "not supplied to this CLI"}
            for key in ("config", "seed", "all_attempts", "metrics", "client_audit",
                        "faults", "web", "radio", "environment")
        },
        "backup": {"status": "not_run"},
        "rounds": [],
        "models": [],
    }


def audit_job(job_root: os.PathLike[str] | str, out_dir: os.PathLike[str] | str,
              *, summary_path: os.PathLike[str] | str | None = None) -> Dict[str, Any]:
    """审计单个作业，并写出报告和已核对的制品备份。

函数不会删除源文件或失败时已留下的部分备份。
"""
    root = Path(job_root).resolve()
    output = Path(out_dir).resolve()
    if os.path.lexists(output):
        _fail("output_exists", f"audit output path already exists: {output}")
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        _fail("output_exists", f"audit output path already exists: {output}")
    summary = Path(summary_path).resolve() if summary_path is not None else root / "coordinator_summary.json"
    report = _base_report(root, output)
    try:
        summary_value, summary_snapshot = _read_summary_snapshot(summary)
        audit = _audit_summary(root, summary, summary_value, summary_snapshot)
        report.update(audit)
        report["backup"] = _backup_sources(output, summary, audit)
        report["status"] = "passed"
    except AuditFailure as exc:
        report["failure"] = {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
        partial = exc.partial
        if partial is not None:
            report.update(partial)
            try:
                report["backup"] = _backup_sources(output, summary, partial)
            except AuditFailure as backup_exc:
                report["backup"] = {"status": "failed", "reason": backup_exc.code}
        elif "audit" in locals():
            report.update(audit)
            report["backup"] = {"status": "failed", "reason": exc.code}
    report.pop("_snapshots", None)
    report_path = output / "fp64-audit-report.json"
    report["report_path"] = str(report_path)
    write_json_atomic(str(report_path), report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Independently audit persisted MNIST FedAvg rounds with CPU FP64."
    )
    parser.add_argument("--job-root", required=True,
                        help="Coordinator job root containing coordinator_summary.json")
    parser.add_argument("--out", required=True,
                        help="New operator-selected persistent evidence directory")
    parser.add_argument("--summary", default=None,
                        help="Summary path; defaults to <job-root>/coordinator_summary.json")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = audit_job(args.job_root, args.out, summary_path=args.summary)
    except AuditFailure as exc:
        print(f"{TOOL_NAME}: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"{TOOL_NAME}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(__import__("json").dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
