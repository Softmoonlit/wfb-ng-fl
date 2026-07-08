#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 v6 手动上行演示脚本传给 wfb_v6_uplink 的关键参数。"""

import os
import stat
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tests/real_hardware/v6_manual_uplink_demo.sh"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def create_fake_environment(tmp: Path) -> tuple[Path, Path]:
    project_root = tmp / "fake_project"
    bin_dir = tmp / "bin"
    project_root.mkdir()
    bin_dir.mkdir()

    write_executable(
        bin_dir / "sudo",
        """#!/bin/sh
if [ "${1:-}" = "-v" ]; then
    exit 0
fi
exec "$@"
""",
    )
    write_executable(
        bin_dir / "ip",
        """#!/bin/sh
exit 1
""",
    )
    write_executable(
        bin_dir / "iw",
        """#!/bin/sh
exit 0
""",
    )
    write_executable(
        project_root / "wfb_v6_uplink",
        """#!/bin/sh
: "${ARGV_FILE:?missing ARGV_FILE}"
printf '%s\n' "$@" > "$ARGV_FILE"
exit 0
""",
    )
    return project_root, bin_dir


def run_demo_action(action: str, extra_env: dict[str, str] | None = None) -> list[str]:
    with tempfile.TemporaryDirectory(prefix=f"v6-manual-args-{action}-") as tmp_name:
        tmp = Path(tmp_name)
        project_root, bin_dir = create_fake_environment(tmp)
        argv_file = tmp / "argv.txt"
        env = os.environ.copy()
        env.update(
            {
                "PROJECT_ROOT": str(project_root),
                "DEMO_NAME": f"args-{action}",
                "NO_ALT_SCREEN": "1",
                "NO_COLOR": "1",
                "TERM": "xterm-256color",
                "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
                "ARGV_FILE": str(argv_file),
                "FEC_K": "8",
                "FEC_N": "12",
            }
        )
        if extra_env:
            env.update(extra_env)
        subprocess.run(["bash", str(SCRIPT), action], cwd=REPO_ROOT, env=env, check=True)
        return argv_file.read_text(encoding="utf-8").splitlines()


def assert_option_value(argv: list[str], option: str, expected: str) -> None:
    try:
        idx = argv.index(option)
    except ValueError as exc:
        raise AssertionError(f"缺少参数 {option}: {argv}") from exc
    actual = argv[idx + 1] if idx + 1 < len(argv) else None
    if actual != expected:
        raise AssertionError(f"参数 {option} 期望 {expected}，实际 {actual}，argv={argv}")


def assert_has_flag(argv: list[str], option: str) -> None:
    if option not in argv:
        raise AssertionError(f"缺少 flag {option}: {argv}")


def assert_lacks_flag(argv: list[str], option: str) -> None:
    if option in argv:
        raise AssertionError(f"不应出现 flag {option}: {argv}")


def test_server_default_radio_args() -> None:
    argv = run_demo_action("server-wfb")
    assert_option_value(argv, "--role", "server")
    assert_option_value(argv, "--fec-k", "8")
    assert_option_value(argv, "--fec-n", "12")
    assert_option_value(argv, "--radio-bandwidth", "40")
    assert_option_value(argv, "--radio-mcs-index", "1")
    assert_has_flag(argv, "--radio-short-gi")


def test_client1_default_radio_and_queue_args() -> None:
    argv = run_demo_action("client1-wfb")
    assert_option_value(argv, "--role", "client")
    assert_option_value(argv, "--node-id", "1")
    assert_option_value(argv, "--fec-k", "8")
    assert_option_value(argv, "--fec-n", "12")
    assert_option_value(argv, "--radio-bandwidth", "40")
    assert_option_value(argv, "--radio-mcs-index", "1")
    assert_has_flag(argv, "--radio-short-gi")
    assert_option_value(argv, "--uplink-pause-threshold-bytes", "131072")
    assert_option_value(argv, "--uplink-resume-threshold-bytes", "65536")
    assert_option_value(argv, "--uplink-queue-packets-limit", "64")


def test_client1_radio_and_queue_overrides() -> None:
    argv = run_demo_action(
        "client1-wfb",
        {
            "FEC_K": "1",
            "FEC_N": "1",
            "RADIO_BANDWIDTH": "20",
            "RADIO_MCS_INDEX": "1",
            "RADIO_SHORT_GI": "0",
            "CLIENT1_UPLINK_PAUSE_THRESHOLD_BYTES": "98304",
            "CLIENT1_UPLINK_RESUME_THRESHOLD_BYTES": "32768",
            "CLIENT1_UPLINK_QUEUE_PACKETS_LIMIT": "17",
        },
    )
    assert_option_value(argv, "--role", "client")
    assert_option_value(argv, "--node-id", "1")
    assert_option_value(argv, "--fec-k", "1")
    assert_option_value(argv, "--fec-n", "1")
    assert_option_value(argv, "--radio-bandwidth", "20")
    assert_option_value(argv, "--radio-mcs-index", "1")
    assert_lacks_flag(argv, "--radio-short-gi")
    assert_option_value(argv, "--uplink-pause-threshold-bytes", "98304")
    assert_option_value(argv, "--uplink-resume-threshold-bytes", "32768")
    assert_option_value(argv, "--uplink-queue-packets-limit", "17")


def main() -> None:
    tests = [
        ("test_server_default_radio_args", test_server_default_radio_args),
        ("test_client1_default_radio_and_queue_args", test_client1_default_radio_and_queue_args),
        ("test_client1_radio_and_queue_overrides", test_client1_radio_and_queue_overrides),
    ]
    for name, func in tests:
        func()
        print(f"通过：{name}")


if __name__ == "__main__":
    main()
