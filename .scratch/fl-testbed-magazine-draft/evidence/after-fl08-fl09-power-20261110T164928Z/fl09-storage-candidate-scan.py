#!/usr/bin/env python3
"""只读枚举 fl09 模型路径，保留实际 stat/SHA，不执行删除。"""
import hashlib
import json
import os
from pathlib import Path
import time

SIZE = 44770302
SHA = "4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96"
ROOT = Path("/srv/wfb-ng-fl")

def file_fact(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"path": str(path), "exists": False}
    row = {"path": str(path), "exists": True, "regular": path.is_file() and not path.is_symlink(),
           "size_bytes": info.st_size, "inode": info.st_ino, "device": info.st_dev,
           "nlink": info.st_nlink, "mode": oct(info.st_mode), "mtime_ns": info.st_mtime_ns}
    if row["regular"] and info.st_size == SIZE:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        row["sha256"] = digest.hexdigest()
    return row

rows = []
for run in sorted(ROOT.glob("comm-*")):
    if not run.is_dir() or run.is_symlink():
        continue
    inbox = run / "role" / "inbox"
    rounds = run / "role" / "rounds"
    paths = [inbox / "model.bin"]
    if rounds.is_dir() and not rounds.is_symlink():
        for round_dir in sorted(rounds.iterdir()):
            if round_dir.is_dir() and not round_dir.is_symlink():
                paths.extend([round_dir / "model.bin", round_dir / "update.bin"])
    for path in paths:
        fact = file_fact(path)
        if fact.get("exists"):
            rows.append(fact)
free = os.statvfs(ROOT)
exact = [row for row in rows if row.get("regular") and row.get("size_bytes") == SIZE and row.get("sha256") == SHA]
print(json.dumps({"host": os.uname().nodename, "wall_ns": time.time_ns(),
                  "available_bytes": free.f_bavail * free.f_frsize,
                  "path_count": len(rows), "exact_sha_candidates": exact,
                  "scan_note": "read-only fixed role paths; no glob unlink"},
                 ensure_ascii=False, indent=2))
