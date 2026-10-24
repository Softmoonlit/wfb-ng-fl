"""真实硬件发现接口的无硬件回归。"""
import pytest

from tests.fl_runtime.hardware import discover_wireless_interface, inspect_wireless_device


class DeviceExecutor:
    def __init__(self, output, returncode=0):
        self.output = output
        self.returncode = returncode

    def run(self, target, command):
        return self.returncode, self.output, ""


def test_discovers_only_one_dynamic_wlx_interface():
    assert discover_wireless_interface(DeviceExecutor("Interface wlan0\nInterface wlxabcdef\n"), "server") == "wlxabcdef"


@pytest.mark.parametrize("output,returncode", [("", 0), ("Interface wlxabc\nInterface wlxdef\n", 0), ("Interface wlxabc\n", 1)])
def test_rejects_missing_ambiguous_or_failed_wireless_discovery(output, returncode):
    with pytest.raises(RuntimeError):
        discover_wireless_interface(DeviceExecutor(output, returncode), "client1")


class USBExecutor:
    def __init__(self, ancestors):
        self.ancestors = ancestors

    def run(self, target, command):
        if command == "iw dev":
            return 0, "Interface wlxabcdef\n", ""
        if command.endswith("/address"):
            return 0, "5c:ff:ff:af:6d:8c\n", ""
        if command.endswith("/device/driver"):
            return 0, "/sys/bus/usb/drivers/88XXau_wfb\n", ""
        if command.endswith("/speed"):
            return 0, "480\n", ""
        if command.endswith("/device"):
            return 0, "/sys/devices/pci0000:00/usb1/1-1/1-1:1.0\n", ""
        return 0, self.ancestors, ""


def test_accepts_xhci_ancestor_for_this_wireless_device():
    facts = inspect_wireless_device(USBExecutor("88XXau_wfb\nxhci_hcd\n"), "server")
    assert facts["usb_controller"] == "xhci_hcd"
    assert facts["usb_speed"] == "480"


@pytest.mark.parametrize("ancestors", ["88XXau_wfb\nehci-pci\n", "88XXau_wfb\n", "xhci_hcd\nehci-pci\n"])
def test_rejects_usb_devices_without_verified_xhci_ancestry(ancestors):
    with pytest.raises(RuntimeError, match="xHCI"):
        inspect_wireless_device(USBExecutor(ancestors), "client2")
