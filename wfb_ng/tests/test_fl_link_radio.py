"""通过动态 loopback UDP 验证公开的上行重武装 helper。"""
import socket
import struct
import threading

import pytest

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.link_radio import rearm_link_uplink


@pytest.mark.parametrize("response_kind", ["ok", "reqid", "status", "length"])
def test_rearm_link_uplink_udp(response_kind):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(1)
        port = server.getsockname()[1]
        observed = []
        errors = []

        def serve():
            try:
                request, peer = server.recvfrom(4096)
                observed.append((request, peer))
                reqid, command, epoch = struct.unpack("!IBQ", request)
                response = struct.pack("!II", reqid ^ (response_kind == "reqid"),
                                       95 if response_kind == "status" else 0)
                if response_kind == "length":
                    response += b"x"
                server.sendto(response, peer)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=serve)
        worker.start()
        try:
            if response_kind == "ok":
                assert rearm_link_uplink(port, 0x123456789ABCDEF) is None
            else:
                with pytest.raises(FLRuntimeError) as error:
                    rearm_link_uplink(port, 0x123456789ABCDEF)
                assert error.value.error_code == "link_uplink_rearm_failed"
        finally:
            worker.join(timeout=2)
        assert not worker.is_alive()
        assert not errors
        request, peer = observed[0]
        assert peer[0] == "127.0.0.1"
        assert len(request) == 13
        assert struct.unpack("!IBQ", request)[1:] == (5, 0x123456789ABCDEF)


@pytest.mark.parametrize("port,epoch", [
    (True, 1), (0, 1), (65536, 1), (1.0, 1),
    (12345, True), (12345, 0), (12345, -1), (12345, 1.0),
    (12345, 1 << 64),
])
def test_rearm_link_uplink_rejects_invalid_values_before_udp(port, epoch):
    with pytest.raises(ValueError):
        rearm_link_uplink(port, epoch)
