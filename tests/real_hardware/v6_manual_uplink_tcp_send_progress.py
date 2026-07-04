#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v6 手动上行演示：带可视化进度的 TCP 发送端。"""

import argparse
import hashlib
import os
import socket
import sys
import time
from pathlib import Path


class Reporter:
    def __init__(self, label: str, log_file: str = "") -> None:
        self.label = label
        self.use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        self.log_handle = None
        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            self.log_handle = open(log_file, "a", encoding="utf-8")

    def close(self) -> None:
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None

    def color(self, code: str, text: str) -> str:
        if not self.use_color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def emit(self, state: str, message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} [{state}] {self.label} | {message}"
        screen_line = line
        if state == "通过":
            screen_line = self.color("32;1", line)
        elif state == "失败":
            screen_line = self.color("31;1", line)
        elif state == "等待":
            screen_line = self.color("33;1", line)
        elif state == "上传中":
            screen_line = self.color("36;1", line)
        print(screen_line, flush=True)
        if self.log_handle:
            self.log_handle.write(line + "\n")
            self.log_handle.flush()


def file_sha256(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    total = 0
    with path.open("rb") as fh:
        while True:
            data = fh.read(1024 * 1024)
            if not data:
                break
            h.update(data)
            total += len(data)
    return h.hexdigest(), total


def progress_bar(done: int, total: int, width: int = 32) -> str:
    if total <= 0:
        return "[" + "?" * width + "]"
    ratio = max(0.0, min(1.0, done / total))
    filled = int(round(ratio * width))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def mib(value: int) -> float:
    return value / 1048576.0


def main() -> int:
    parser = argparse.ArgumentParser(description="发送一个 TCP 文件，给现场演示输出可视化进度")
    parser.add_argument("--label", default="客户端上传")
    parser.add_argument("--source-ip", default="")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--progress-interval", type=float, default=1.0)
    parser.add_argument("--log-file", default="")
    args = parser.parse_args()

    reporter = Reporter(args.label, args.log_file)
    source = Path(args.input)
    try:
        if not source.is_file():
            reporter.emit("失败", f"源文件不存在: {source}")
            return 2

        source_sha, source_size = file_sha256(source)
        reporter.emit("准备", f"源文件 {source.name}，大小 {source_size} bytes / {mib(source_size):.2f} MiB")
        reporter.emit("准备", f"源文件 SHA256={source_sha}")

        if args.delay:
            reporter.emit("等待", f"延迟 {args.delay:.1f} 秒后开始连接")
            time.sleep(args.delay)

        deadline = time.time() + args.timeout
        last_error = None
        attempt = 0
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                reporter.emit("失败", f"连接 server 超时，最后错误: {last_error}")
                return 3
            attempt += 1
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(max(1.0, min(5.0, remaining)))
            try:
                if args.source_ip:
                    sock.bind((args.source_ip, 0))
                reporter.emit("等待", f"连接第 {attempt} 次：{args.source_ip or '自动'} -> {args.host}:{args.port}")
                sock.connect((args.host, args.port))
                sock.settimeout(args.timeout)
                local = sock.getsockname()
                peer = sock.getpeername()
                reporter.emit("通过", f"TCP 已连接：{local[0]}:{local[1]} -> {peer[0]}:{peer[1]}")
                break
            except OSError as exc:
                last_error = exc
                try:
                    sock.close()
                except Exception:
                    pass
                reporter.emit("等待", f"server 接收器尚未接通：{exc}")
                time.sleep(0.5)

        total = 0
        start = time.monotonic()
        last = start
        last_total = 0
        with sock, source.open("rb") as fh:
            while True:
                data = fh.read(65536)
                if not data:
                    break
                sock.sendall(data)
                total += len(data)
                now = time.monotonic()
                if now - last >= args.progress_interval:
                    interval = max(now - last, 1e-9)
                    elapsed = max(now - start, 1e-9)
                    inst = (total - last_total) / interval / 1048576.0
                    avg = total / elapsed / 1048576.0
                    pct = (100.0 * total / source_size) if source_size else 0.0
                    reporter.emit(
                        "上传中",
                        f"{progress_bar(total, source_size)} {pct:5.1f}%  {mib(total):.2f}/{mib(source_size):.2f} MiB  当前 {inst:.2f} MiB/s  平均 {avg:.2f} MiB/s",
                    )
                    last = now
                    last_total = total
            sock.shutdown(socket.SHUT_WR)

        elapsed = max(time.monotonic() - start, 1e-9)
        if total == source_size:
            reporter.emit("通过", f"上传完成：sent_bytes={total}，耗时 {elapsed:.3f}s，平均 {total / elapsed / 1048576.0:.2f} MiB/s")
            reporter.emit("通过", f"请在 server 上确认收到文件 SHA256={source_sha}")
            return 0
        reporter.emit("失败", f"上传字节数不完整：sent_bytes={total} expected={source_size}")
        return 4
    finally:
        reporter.close()


if __name__ == "__main__":
    raise SystemExit(main())
