#!/usr/bin/env python3
"""远端只读 admission marker；不导入仓库模块、不写远端文件。"""
import json
import os
from pathlib import Path
import socket
import subprocess
import time


def run(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=8)
        return {"argv": argv, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except Exception as exc:
        return {"argv": argv, "error": repr(exc)}

host = socket.gethostname()
role = "server" if host == "fl00" else "client"
service_name = "wfb-fl-server-daemon.service" if role == "server" else "wfb-fl-client-daemon.service"
service = run(["systemctl", "show", service_name, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID"])
boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
uptime = Path("/proc/uptime").read_text().strip()
stat = os.statvfs("/srv/wfb-ng-fl")
print(json.dumps({
    "host": host,
    "role": role,
    "boot_id": boot,
    "uptime": uptime,
    "wall_ns": time.time_ns(),
    "monotonic_ns": time.monotonic_ns(),
    "service": service,
    "storage": {"available_bytes": stat.f_bavail * stat.f_frsize,
                 "available_inodes": stat.f_favail},
    "power_query": run(["nvpmodel", "-q"]),
    "kernel_release": os.uname().release,
}, ensure_ascii=False))
