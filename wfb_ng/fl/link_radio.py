"""v6 链路本机 UDP 射频更新，沿用 tx_cmd 的完整参数报文。"""

import secrets
import socket
import struct

from .errors import FLRuntimeError

DEFAULT_RADIO_CONTROL_PORT = 9200


def apply_link_radio(port: int, bandwidth_mhz: int, mcs: int) -> None:
    """收到匹配请求的成功响应后才返回；FEC 8/14 由链路启动时固定。"""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("radio_control_port 必须为 1..65535 的整数")
    if type(bandwidth_mhz) is not int or bandwidth_mhz not in (20, 40):
        raise ValueError("bandwidth_mhz 必须为整数 20 或 40")
    if type(mcs) is not int or mcs not in (3, 4, 5, 6):
        raise ValueError("mcs 必须为整数 3、4、5 或 6")
    request_id = secrets.randbits(32)
    # req_id、CMD_SET_RADIO、STBC、LDPC、short GI、带宽、MCS、VHT、NSS。
    request = struct.pack("!IB7B", request_id, 2, 0, 0, 1, bandwidth_mhz, mcs, 0, 1)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.settimeout(1.0)
            udp.connect(("127.0.0.1", port))
            udp.send(request)
            response = udp.recv(4096)
        if len(response) != 8:
            raise ValueError("本机射频响应长度无效")
        response_id, status = struct.unpack("!II", response)
        if response_id != request_id:
            raise ValueError("本机射频响应请求身份不匹配")
        if status != 0:
            raise ValueError(f"本机射频更新被拒绝，状态码 {status}")
    except (OSError, ValueError) as exc:
        raise FLRuntimeError("link_radio_update_failed", f"v6 本机射频更新失败: {exc}") from exc
