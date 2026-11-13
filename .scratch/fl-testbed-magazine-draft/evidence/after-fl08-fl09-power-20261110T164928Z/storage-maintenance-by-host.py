#!/usr/bin/env python3
"""按主机精确回收旧成功 summary 对应的 exact duplicate；不做 glob 删除。"""
import hashlib
import json
import os
from pathlib import Path
import time

SIZE = 44770302
SHA = "4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96"
REQUIRED = 671181818
SELECTED = {
    "fl01": [
        "/srv/wfb-ng-fl/comm-fec12-apr-20261109T185342Z-t2-g30-d5-model/role/rounds/28602245-36ff-47c5-b587-932a31c49623/model.bin",
        "/srv/wfb-ng-fl/comm-fec12-apr-20261109T185342Z-t2-g30-d5-model/role/rounds/28602245-36ff-47c5-b587-932a31c49623/update.bin",
    ],
    "fl02": [
        "/srv/wfb-ng-fl/comm-model-fec14-g10-d10/role/rounds/c26f3c4d-a2f6-400e-a85a-c784c45a2607/model.bin",
        "/srv/wfb-ng-fl/comm-model-fec14-g10-d10/role/rounds/c26f3c4d-a2f6-400e-a85a-c784c45a2607/update.bin",
    ],
    "fl07": [
        "/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g20-d10-model/role/rounds/cee8e9d4-af9e-465e-8df7-9881158f8423/model.bin",
    ],
}
PROTECTED = [
    "/srv/wfb-ng-fl/comm-rsmc20261109T060537Z-g30-d5-model/role/rounds/81f1fdf3-d962-48d1-890e-c4ffb6583d86/model.bin",
    "/srv/wfb-ng-fl/comm-post-fl02-20261110T142107Z-model/role/rounds/21b2b57d-6f48-48af-9eea-e2d74c89a83c/model.bin",
    "/srv/wfb-ng-fl/comm-second-eight-model-20261110T150044Z-model/role/rounds/26d375cd-7c5a-478c-ae4d-c4802b176958/model.bin",
]

def fact(value):
    path = Path(value)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"path": value, "exists": False}
    row = {"path": value, "exists": True, "regular": path.is_file() and not path.is_symlink(),
           "size_bytes": info.st_size, "inode": info.st_ino, "device": info.st_dev,
           "nlink": info.st_nlink, "mode": oct(info.st_mode), "mtime_ns": info.st_mtime_ns}
    if row["regular"]:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        row["sha256"] = digest.hexdigest()
    return row

def free_bytes():
    stat = os.statvfs("/srv/wfb-ng-fl")
    return stat.f_bavail * stat.f_frsize

host = os.uname().nodename
selected = SELECTED.get(host, [])
before = free_bytes()
protected_before = [fact(path) for path in PROTECTED]
selected_before = [fact(path) for path in selected]
if any(not row.get("regular") or row.get("size_bytes") != SIZE or row.get("sha256") != SHA
       for row in protected_before):
    result = {"status": "blocked", "reason": "protected_mismatch", "host": host,
              "free_before": before, "protected_before": protected_before}
    print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(2)
if any(not row.get("regular") or row.get("size_bytes") != SIZE or row.get("sha256") != SHA
       or row.get("nlink") != 1 for row in selected_before):
    result = {"status": "blocked", "reason": "selected_precondition_mismatch", "host": host,
              "free_before": before, "selected_before": selected_before}
    print(json.dumps(result, ensure_ascii=False, indent=2)); raise SystemExit(2)
rows = []
for path, row in zip(selected, selected_before):
    Path(path).unlink()
    rows.append({"before": row, "after": fact(path), "free_after": free_bytes()})
    if free_bytes() >= REQUIRED:
        break
result = {
    "status": "complete" if free_bytes() >= REQUIRED else "below_required",
    "host": host, "wall_ns": time.time_ns(), "required_bytes": REQUIRED,
    "free_before": before, "free_after": free_bytes(),
    "target": {"size_bytes": SIZE, "sha256": SHA}, "selected": selected,
    "deleted_rows": rows, "protected_before": protected_before,
    "protected_after": [fact(path) for path in PROTECTED],
    "old_reclamation_paths_reused": False, "failed_attempts_deleted": False,
    "source_success_evidence": {
        "fl01": "fl08-heartbeat-fix-validation/continuation-cccac233291c-0model-gate/trial2/run-summary.json",
        "fl02": "grant-fec-nine-20261108/after-power-restored/model-fec14-g10-d10/run-summary.json",
        "fl07": "grant-fec-nine-current-hardware-20261109T063029Z/fec12/fec12-g20-d10-model/run-summary.json",
    }.get(host),
}
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result["status"] == "complete" else 3)
