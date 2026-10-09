"""从 Issue #41 提取的、与作业编排无关的硬件发现。"""
import os
import re
import shlex
from typing import Any, Dict


def discover_wireless_interface(executor: Any, target: str) -> str:
    """要求成功发现唯一的 wlx* 接口，禁止猜测接口名。"""
    rc, output, error = executor.run(target, "iw dev")
    interfaces = sorted(set(re.findall(r"^\s*Interface\s+(wlx[0-9a-zA-Z]+)\s*$", output, re.MULTILINE)))
    if rc != 0 or len(interfaces) != 1:
        raise RuntimeError(f"{target} 必须恰好发现一个 wlx* 网卡：{interfaces}；{error}")
    return interfaces[0]


def inspect_wireless_device(executor: Any, target: str) -> Dict[str, str]:
    """读取网卡及其实际 USB 控制器祖先，查询失败即拒绝。"""
    interface = discover_wireless_interface(executor, target)
    base = f"/sys/class/net/{interface}"
    queries = {
        "mac": f"cat {base}/address",
        "driver": f"readlink -f {base}/device/driver",
        "usb_speed": f"cat {base}/device/../speed",
        "device_path": f"readlink -f {base}/device",
    }
    facts = {"interface": interface}
    for key, command in queries.items():
        rc, out, error = executor.run(target, command)
        if rc != 0 or not out.strip():
            raise RuntimeError(f"{target} 无法读取 {key}：{error}")
        facts[key] = out.strip()
    facts["driver"] = os.path.basename(facts["driver"])
    # xHCI 必须是本接口 sysfs 祖先，而非另一 USB 总线的 lsusb 输出。
    device = shlex.quote(facts["device_path"])
    command = (
        f"device={device}; while [ \"$device\" != / ]; do "
        "if [ -L \"$device/driver\" ]; then basename \"$(readlink -f \"$device/driver\")\"; fi; "
        "device=$(dirname \"$device\"); done"
    )
    rc, output, error = executor.run(target, command)
    drivers = output.splitlines()
    if rc != 0 or "xhci_hcd" not in drivers or "ehci-pci" in drivers:
        raise RuntimeError(f"{target} 网卡未由 xHCI 纳管：{drivers}；{error}")
    facts["usb_controller"] = "xhci_hcd"
    if facts["driver"] not in ("rtl88xxau_wfb", "88XXau_wfb"):
        raise RuntimeError(f"{target} 无线驱动错误：{facts['driver']}")
    if float(facts["usb_speed"]) < 480:
        raise RuntimeError(f"{target} 无线 USB 速度低于 480 Mbps")
    return facts
