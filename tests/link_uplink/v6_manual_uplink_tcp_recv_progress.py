#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v6 手动上行演示：带可视化进度的 TCP 接收端。"""

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
        elif state == "接收中":
            screen_line = self.color("36;1", line)
        print(screen_line, flush=True)
        if self.log_handle:
            self.log_handle.write(line + "\n")
            self.log_handle.flush()


def progress_bar(done: int, total: int, width: int = 32) -> str:
    if total <= 0:
        return "[" + "?" * width + "]"
    ratio = max(0.0, min(1.0, done / total))
    filled = int(round(ratio * width))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def mib(value: int) -> float:
    return value / 1048576.0


def main() -> int:
    parser = argparse.ArgumentParser(description="接收一个 TCP 文件，给现场演示输出可视化进度")
    parser.add_argument("--label", default="server 接收上传")
    parser.add_argument("--bind-ip", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-bytes", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--progress-interval", type=float, default=1.0)
    parser.add_argument("--log-file", default="")
    args = parser.parse_args()

    reporter = Reporter(args.label, args.log_file)
    output = Path(args.output)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()
            reporter.emit("准备", f"已删除旧接收文件: {output}")

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.settimeout(args.timeout)
        try:
            server.bind((args.bind_ip, args.port))
        except OSError as exc:
            reporter.emit("失败", f"绑定 {args.bind_ip}:{args.port} 失败：{exc}；请先确认 server TUN 已出现")
            return 2
        server.listen(1)
        reporter.emit("等待", f"接收器已就绪：{args.bind_ip}:{args.port}，等待 client 连接")

        try:
            conn, addr = server.accept()
        except socket.timeout:
            reporter.emit("失败", f"等待 client 连接超时 {args.timeout:.1f}s")
            return 3
        conn.settimeout(args.timeout)
        reporter.emit("通过", f"client 已连接：{addr[0]}:{addr[1]}")

        h = hashlib.sha256()
        total = 0
        start = time.monotonic()
        last = start
        last_total = 0
        with conn, output.open("wb") as fh:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                fh.write(data)
                h.update(data)
                total += len(data)
                now = time.monotonic()
                if now - last >= args.progress_interval:
                    interval = max(now - last, 1e-9)
                    elapsed = max(now - start, 1e-9)
                    inst = (total - last_total) / interval / 1048576.0
                    avg = total / elapsed / 1048576.0
                    if args.expected_bytes > 0:
                        pct = 100.0 * total / args.expected_bytes
                        progress = f"{progress_bar(total, args.expected_bytes)} {pct:5.1f}%  {mib(total):.2f}/{mib(args.expected_bytes):.2f} MiB"
                    else:
                        progress = f"{mib(total):.2f} MiB"
                    reporter.emit("接收中", f"{progress}  当前 {inst:.2f} MiB/s  平均 {avg:.2f} MiB/s")
                    last = now
                    last_total = total

        elapsed = max(time.monotonic() - start, 1e-9)
        digest = h.hexdigest()
        if args.expected_bytes > 0 and total != args.expected_bytes:
            reporter.emit("失败", f"接收字节数不完整：received_bytes={total} expected={args.expected_bytes} sha256={digest}")
            return 4
        reporter.emit("通过", f"接收完成：received_bytes={total}，sha256={digest}，耗时 {elapsed:.3f}s，平均 {total / elapsed / 1048576.0:.2f} MiB/s")
        return 0
    finally:
        reporter.close()


if __name__ == "__main__":
    raise SystemExit(main())
