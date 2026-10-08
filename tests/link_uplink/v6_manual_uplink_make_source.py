#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v6 手动上行演示：生成确定性上传源文件。"""

import argparse
import hashlib
import time
from pathlib import Path


PATTERNS = {
    "client1": b"client1-uplink-demo\n",
    "client2": b"client2-uplink-demo\n",
}


def progress_bar(done: int, total: int, width: int = 32) -> str:
    if total <= 0:
        return "[" + "?" * width + "]"
    ratio = max(0.0, min(1.0, done / total))
    filled = int(round(ratio * width))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def mib(value: int) -> float:
    return value / 1048576.0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            data = fh.read(1024 * 1024)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 client 上行演示源文件")
    parser.add_argument("--client", choices=sorted(PATTERNS), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--sha-output", default="")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    pattern = PATTERNS[args.client]
    print(f"{time.strftime('%H:%M:%S')} [准备] 生成 {args.client} 上传源文件：{output}", flush=True)
    print(f"{time.strftime('%H:%M:%S')} [准备] 目标大小：{args.size} bytes / {mib(args.size):.2f} MiB", flush=True)

    written = 0
    next_report = 0
    with output.open("wb") as fh:
        remaining = args.size
        while remaining > 0:
            chunk = pattern if remaining >= len(pattern) else pattern[:remaining]
            fh.write(chunk)
            written += len(chunk)
            remaining -= len(chunk)
            if args.size > 0 and written >= next_report:
                pct = 100.0 * written / args.size
                print(f"{time.strftime('%H:%M:%S')} [生成中] {progress_bar(written, args.size)} {pct:5.1f}%  {mib(written):.2f}/{mib(args.size):.2f} MiB", flush=True)
                next_report += max(args.size // 4, 1)

    digest = sha256_file(output)
    print(f"{time.strftime('%H:%M:%S')} [通过] 源文件完成：bytes={written} sha256={digest}", flush=True)
    if args.sha_output:
        sha_path = Path(args.sha_output)
        sha_path.parent.mkdir(parents=True, exist_ok=True)
        sha_path.write_text(f"{digest}  {output}\n", encoding="utf-8")
        print(f"{time.strftime('%H:%M:%S')} [通过] SHA 已写入：{sha_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
