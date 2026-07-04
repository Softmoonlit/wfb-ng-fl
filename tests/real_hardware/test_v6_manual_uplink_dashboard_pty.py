#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""server-wfb 面板在异常 PTY 换行状态下的回归测试。"""

import errno
import os
import pty
import select
import signal
import stat
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tests/real_hardware/v6_manual_uplink_demo.sh"
PANEL_MARKER = "server 上行链路面板".encode("utf-8")
PANEL_READY_LINE = "演示名：pty-crlf-regression".encode("utf-8")
DIAGONAL_MESSAGE = (
    "server-wfb 面板进入后必须恢复 CRLF；否则现场终端处于 -onlcr/-opost 时，"
    "中文面板会只换行不回车，出现斜向错位。"
)
STATUS_ALIGNMENT_MESSAGE = (
    "server-wfb 面板中 [等待]/[通过] 状态标签必须锚定到第 1 列，"
    "防止等待/通过状态标签不对齐。"
)
STATUS_TAGS = ("[等待]".encode("utf-8"), "[通过]".encode("utf-8"))
KEY_DATA_LINE = b"0\tGRANT_FILTER\t1:1:0:0:0:0:0"
NON_STATUS_ALIGNMENT_MESSAGE = (
    "server-wfb 面板中口径/提示/停止/最近需要关注/关键数据行必须锚定到第 1 列，"
    "防止口径/提示/停止/最近关注/关键数据不左对齐。"
)
NON_STATUS_PANEL_ANCHORS = (
    ("口径", "口径：".encode("utf-8")),
    ("提示", "提示：".encode("utf-8")),
    ("停止", "停止：".encode("utf-8")),
    ("最近需要关注", "最近需要关注的信息：".encode("utf-8")),
    ("关键数据", KEY_DATA_LINE),
)


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
if [ "${1:-}" = "addr" ] && [ "${2:-}" = "show" ] && [ "${3:-}" = "v6us0" ]; then
    printf '2: v6us0: <POINTOPOINT,UP> mtu 1500\n'
    exit 0
fi
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
        bin_dir / "tput",
        """#!/bin/sh
if [ "${1:-}" = "cols" ]; then
    printf '80\n'
fi
exit 0
""",
    )
    write_executable(
        project_root / "wfb_v6_uplink",
        """#!/bin/sh
trap 'exit 0' INT TERM
printf 'trusted_plaintext fake link ready\n'
printf '0\tGRANT_FILTER\t1:1:0:0:0:0:0\n'
sleep 1
printf 'ready_accept node_id=1 fake\n'
printf 'ready_accept node_id=2 fake\n'
printf 'grant seq=1 node_id=1 fake\n'
printf 'grant seq=2 node_id=2 fake\n'
printf 'radio\tPKT\ta\tb\tc\td\t7\n'
while :; do
    printf 'fake heartbeat\n'
    sleep 0.05
done
""",
    )
    return project_root, bin_dir


def force_bad_output_mode(slave_fd: int) -> None:
    attrs = termios.tcgetattr(slave_fd)
    oflag = attrs[1]
    changed = False
    if hasattr(termios, "ONLCR"):
        oflag &= ~termios.ONLCR
        changed = True
    if hasattr(termios, "OPOST"):
        oflag &= ~termios.OPOST
        changed = True
    if not changed:
        raise RuntimeError("当前平台没有可用的 ONLCR/OPOST 标志，无法构造 PTY 换行回归夹具")
    attrs[1] = oflag
    termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)


def read_available(master_fd: int, output: bytearray) -> None:
    while True:
        try:
            chunk = os.read(master_fd, 4096)
        except BlockingIOError:
            return
        except OSError as exc:
            if exc.errno == errno.EIO:
                return
            raise
        if not chunk:
            return
        output.extend(chunk)


def wait_for_panel(master_fd: int, output: bytearray, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        readable, _, _ = select.select([master_fd], [], [], min(0.05, remaining))
        if readable:
            read_available(master_fd, output)
        start = bytes(output).find(PANEL_MARKER)
        panel = bytes(output)[start:] if start != -1 else b""
        if start != -1 and PANEL_READY_LINE in panel and KEY_DATA_LINE in panel:
            return
    raise AssertionError(
        "未捕获到包含最近关注关键数据行的 server-wfb 面板输出，无法验证 PTY -onlcr/-opost 回归。\n"
        + bytes(output).decode("utf-8", errors="replace")[-2000:]
    )


def stop_process_group(proc: subprocess.Popen[bytes], master_fd: int, output: bytearray) -> None:
    try:
        os.killpg(proc.pid, signal.SIGINT)
    except ProcessLookupError:
        pass

    deadline = time.monotonic() + 2.0
    while proc.poll() is None and time.monotonic() < deadline:
        readable, _, _ = select.select([master_fd], [], [], 0.05)
        if readable:
            read_available(master_fd, output)

    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=2.0)
    else:
        proc.wait(timeout=0.1)
    read_available(master_fd, output)


def has_lone_lf(data: bytes) -> bool:
    return any(byte == 0x0A and (index == 0 or data[index - 1] != 0x0D) for index, byte in enumerate(data))


def require_status_tags_at_column_one(panel_segment: bytes) -> None:
    missing = [tag.decode("utf-8") for tag in STATUS_TAGS if b"\r" + tag not in panel_segment]
    if missing:
        raise AssertionError(
            STATUS_ALIGNMENT_MESSAGE
            + " 缺少显式回车行首标签："
            + "、".join(missing)
            + "。"
        )

def require_non_status_panel_lines_at_column_one(panel_segment: bytes) -> None:
    missing = [
        label
        for label, expected in NON_STATUS_PANEL_ANCHORS
        if b"\r" + expected not in panel_segment
    ]
    if missing:
        raise AssertionError(
            NON_STATUS_ALIGNMENT_MESSAGE
            + " 缺少显式回车行首内容："
            + "、".join(missing)
            + "。"
        )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="v6-dashboard-pty-") as tmp_name:
        tmp = Path(tmp_name)
        project_root, bin_dir = create_fake_environment(tmp)
        master_fd, slave_fd = pty.openpty()
        proc: subprocess.Popen[bytes] | None = None
        output = bytearray()
        try:
            force_bad_output_mode(slave_fd)
            os.set_blocking(master_fd, False)

            env = os.environ.copy()
            env.update(
                {
                    "PROJECT_ROOT": str(project_root),
                    "DEMO_NAME": "pty-crlf-regression",
                    "DASHBOARD_INTERVAL": "0.1",
                    "NO_ALT_SCREEN": "1",
                    "NO_COLOR": "1",
                    "TERM": "xterm-256color",
                    "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
                }
            )
            proc = subprocess.Popen(
                ["bash", str(SCRIPT), "server-wfb"],
                cwd=REPO_ROOT,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                preexec_fn=os.setsid,
                close_fds=True,
            )

            wait_for_panel(master_fd, output, timeout=5.0)
            stop_process_group(proc, master_fd, output)
        finally:
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=2.0)
            os.close(slave_fd)
            os.close(master_fd)

    raw = bytes(output)
    panel_start = raw.find(PANEL_MARKER)
    if panel_start == -1:
        raise AssertionError("未找到 server-wfb 面板标题，测试没有进入目标输出阶段。")

    startup = raw[:panel_start]
    if not has_lone_lf(startup):
        raise AssertionError("测试夹具未证明启动前 PTY 处于 -onlcr/-opost 异常状态，回归覆盖无效。")

    panel_segment = raw[panel_start : panel_start + 2400]
    crlf_count = panel_segment.count(b"\r\n")
    if crlf_count < 5:
        raise AssertionError(DIAGONAL_MESSAGE + f" 面板片段 CRLF 只有 {crlf_count} 处。")
    if PANEL_READY_LINE not in panel_segment:
        raise AssertionError("面板 CRLF 断言没有覆盖演示名行，可能误统计了启动前日志。")
    require_status_tags_at_column_one(panel_segment)
    require_non_status_panel_lines_at_column_one(panel_segment)

    print("通过：server-wfb 面板在 -onlcr/-opost PTY 中恢复 CRLF，避免中文面板斜向错位。")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        raise
