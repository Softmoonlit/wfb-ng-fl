"""控制下行必须使用监听平面的源地址，避免走管理网。"""
import json
import socket

from wfb_ng.fl.control import ControlPlaneServer, RadioConfig
from wfb_ng.fl.server_daemon import ServerDaemonConfig, DEFAULT_TUN_IP


def test_daemon_control_binds_tun_address_by_default():
    assert ServerDaemonConfig().control_bind_host == DEFAULT_TUN_IP


def test_control_downlink_uses_configured_source_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(2)
        server = ControlPlaneServer(
            active_radio_config=RadioConfig(),
            bind_host='127.0.0.2', bind_port=0,
            broadcast_addr='127.0.0.1',
            broadcast_port=receiver.getsockname()[1],
        )
        try:
            server.start()
            server.broadcast_downlink({'type': 'TEST_CONTROL_SOURCE'})
            payload, source = receiver.recvfrom(4096)
            assert json.loads(payload)['type'] == 'TEST_CONTROL_SOURCE'
            assert source[0] == '127.0.0.2'
        finally:
            server.stop()
