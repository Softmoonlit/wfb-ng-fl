#!/usr/bin/env python3
"""只回收已由旧 success summary 证明的 fl09 完整重复副本。"""
import hashlib
import json
import os
from pathlib import Path
import time

SIZE = 44770302
SHA = "4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96"
REQUIRED = 671181818
SELECTED = [
    "/srv/wfb-ng-fl/comm-fec12-apr-20261109T185342Z-t1-g30-d5-model/role/rounds/135a2d3f-31e4-4f36-bc1f-2d63c971a9a1/model.bin",
    "/srv/wfb-ng-fl/comm-fec12-apr-20261109T185342Z-t1-g30-d5-model/role/rounds/135a2d3f-31e4-4f36-bc1f-2d63c971a9a1/update.bin",
    "/srv/wfb-ng-fl/comm-fec12-apr-20261109T185342Z-t2-g30-d5-model/role/rounds/28602245-36ff-47c5-b587-932a31c49623/model.bin",
]
PROTECTED = [
    "/srv/wfb-ng-fl/comm-rsmc20261109T060537Z-g30-d5-model/role/rounds/81f1fdf3-d962-48d1-890e-c4ffb6583d86/model.bin",
    "/srv/wfb-ng-fl/comm-post-fl02-20261110T142107Z-model/role/rounds/21b2b57d-6f48-48af-9eea-e2d74c89a83c/model.bin",
    "/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g40-d10-model/role/rounds/f80703de-e660-496e-b684-812e75dbe672/model.bin",
]


def fact(value):
    path = Path(value)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"path": value, "exists": False}
    result = {
        "path": value,
        "exists": True,
        "regular": path.is_file() and not path.is_symlink(),
        "size_bytes": info.st_size,
        "inode": info.st_ino,
        "device": info.st_dev,
        "nlink": info.st_nlink,
        "mode": oct(info.st_mode),
        "mtime_ns": info.st_mtime_ns,
    }
    if result["regular"]:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        result["sha256"] = digest.hexdigest()
    return result


def free_bytes():
    stat = os.statvfs("/srv/wfb-ng-fl")
    return stat.f_bavail * stat.f_frsize


before = free_bytes()
protected_before = [fact(path) for path in PROTECTED]
if any(not row.get("regular") or row.get("size_bytes") != SIZE or row.get("sha256") != SHA
       for row in protected_before):
    print(json.dumps({"status": "blocked", "reason": "protected_mismatch",
                      "free_before": before, "protected_before": protected_before}, ensure_ascii=False, indent=2))
    raise SystemExit(2)
selected_before = [fact(path) for path in SELECTED]
if any(not row.get("regular") or row.get("size_bytes") != SIZE or row.get("sha256") != SHA
       or row.get("nlink") != 1 for row in selected_before):
    print(json.dumps({"status": "blocked", "reason": "selected_precondition_mismatch",
                      "free_before": before, "selected_before": selected_before}, ensure_ascii=False, indent=2))
    raise SystemExit(2)
rows = []
for path, row in zip(SELECTED, selected_before):
    Path(path).unlink()
    rows.append({"before": row, "after": fact(path), "free_after": free_bytes()})
    if free_bytes() >= REQUIRED:
        break
result = {
    "status": "complete" if free_bytes() >= REQUIRED else "below_required",
    "host": os.uname().nodename,
    "wall_ns": time.time_ns(),
    "required_bytes": REQUIRED,
    "free_before": before,
    "free_after": free_bytes(),
    "target": {"size_bytes": SIZE, "sha256": SHA},
    "selected": SELECTED,
    "deleted_rows": rows,
    "protected_before": protected_before,
    "protected_after": [fact(path) for path in PROTECTED],
    "old_reclamation_paths_reused": False,
    "failed_attempts_deleted": False,
    "source_success_evidence": [
        "fl08-heartbeat-fix-validation/continuation-cccac233291c-0model-gate/trial1/run-summary.json",
        "fl08-heartbeat-fix-validation/continuation-cccac233291c-0model-gate/trial2/run-summary.json",
    ],}
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["status"] == "complete" else 3)
