#!/usr/bin/env python3
"""Stage 4 首版正式无硬件回归入口。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from wfb_ng.fl.issue41_fixtures import generate_model_fixture
from tests.fl_runtime.stage4_archive import STAGES, bind_fixture, init_archive, record_stage, seal_archive

ROOT = Path(__file__).resolve().parents[2]

COMMANDS = {
    "domain-model-library": ["wfb_ng/tests/test_fl_model_library.py", "wfb_ng/tests/test_fl_models_http.py", "wfb_ng/tests/test_fl_console.py"],
    "http-sse": ["wfb_ng/tests/test_fl_console.py", "wfb_ng/tests/test_fl_sse_abort.py", "wfb_ng/tests/test_fl_models_http.py", "wfb_ng/tests/test_fl_radio_console_http.py"],
    "browser": [],
    "control-plane-lifecycle": ["wfb_ng/tests/test_fl_control_plane.py", "wfb_ng/tests/test_fl_control_routing.py", "wfb_ng/tests/test_fl_job_contract_stage3.py", "wfb_ng/tests/test_fl_client_daemon.py", "wfb_ng/tests/test_fl_e2e_airgapped.py"],
}


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=300, env={**os.environ, "PYTHONPATH": str(cwd)})


def _git_commit() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _run_id() -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"stage4-software-{now}-{uuid.uuid4().hex[:8]}"


def run(archive: Path, *, skip_tests: bool = False) -> int:
    envelope = init_archive(archive, run_id=_run_id(), commit=_git_commit())
    with tempfile.TemporaryDirectory(prefix="stage4-fixture-") as temp:
        fixture = generate_model_fixture(temp, size_bytes=40 * 1024 * 1024)
        bind_fixture(archive, fixture)

    envelope = json.loads((archive / "envelope.json").read_text(encoding="utf-8"))
    preflight = {"hardware_required": False, "fixture": envelope["fixture"], "python": sys.version.split()[0],
                 "control_plane": envelope["control_plane"], "data_plane": envelope["data_plane"]}
    record_stage(archive, "preflight", status="passed", detail=preflight)
    failed = bool(skip_tests)
    failure_boundary: str | None = "test-execution-skipped" if skip_tests else None

    for stage in STAGES[1:5]:
        if failed:
            record_stage(archive, stage, status="skipped", detail={"blocked_by": failure_boundary})
            continue
        if skip_tests:
            record_stage(archive, stage, status="skipped", detail={"reason": "--skip-tests"})
            continue
        if stage == "browser":
            command = ["node", "wfb_ng/tests/desktop_workflow_page_test.js",
                       "wfb_ng/fl/static/console.js", "wfb_ng/fl/static/models.js", "wfb_ng/fl/static/radio.js"]
        else:
            command = [sys.executable, "-m", "pytest", "-q", *COMMANDS[stage]]
        try:
            result = _run(command, cwd=ROOT)
            status = "passed" if result.returncode == 0 else "failed"
            record_stage(archive, stage, status=status, command=command, returncode=result.returncode,
                         stdout=result.stdout, stderr=result.stderr)
            if status == "failed":
                failed = True
                failure_boundary = stage
        except subprocess.TimeoutExpired as exc:
            record_stage(archive, stage, status="failed", command=command, returncode=124,
                         stdout=str(exc.stdout or ""), stderr=str(exc.stderr or "timeout"))
            failed = True
            failure_boundary = stage

    collect_status = "failed" if failed else "passed"
    record_stage(archive, "collect", status=collect_status,
                 detail={"evidence_partitions": ["management-web", "control-plane", "data-plane", "lifecycle", "summary"],
                         "failure_boundary": failure_boundary})
    conclusion = "failed" if failed else "passed"
    record_stage(archive, "summary", status=collect_status,
                 detail={"conclusion": conclusion, "failure_boundary": failure_boundary})
    seal_archive(archive, conclusion=conclusion, failure_boundary=failure_boundary)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 4 软件无硬件分层验收")
    parser.add_argument("--archive-root", type=Path, default=ROOT / "tests/logs")
    parser.add_argument("--archive", type=Path, default=None)
    parser.add_argument("--skip-tests", action="store_true", help="只生成结构验证归档，供工具测试使用")
    args = parser.parse_args(argv)
    archive = args.archive or (args.archive_root / _run_id())
    archive.parent.mkdir(parents=True, exist_ok=True)
    code = run(archive, skip_tests=args.skip_tests)
    print(f"stage4 archive: {archive}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
