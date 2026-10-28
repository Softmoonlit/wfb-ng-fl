"""通过宿主公开入口验证物理换频与 native 声明重新武装的顺序。"""

import socket
import struct
import threading
from unittest import mock

from wfb_ng.fl.client_daemon import ClientDaemon, ClientDaemonConfig
from wfb_ng.fl.control import ControlPlaneClient
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter


def test_daemon_rearms_native_after_each_hunt_channel(tmp_path, monkeypatch):
    """真实本机 UDP 确认完成后，才允许该信道的心跳发出。"""
    real_socket = socket.socket
    ports = set()

    class LoopbackSocket(real_socket):
        def bind(self, address):
            assert address == ('127.0.0.1', 0)
            result = super().bind(address)
            ports.add(self.getsockname()[1])
            return result

        def check_peer(self, address):
            assert address[0] == '127.0.0.1' and address[1] in ports
            assert address[1] not in {9000, 9001, 9002, 9200}
            if self.getsockname()[1] == 0:
                self.bind(('127.0.0.1', 0))

        def connect(self, address):
            self.check_peer(address)
            return super().connect(address)

        def sendto(self, data, *args):
            self.check_peer(args[-1])
            return super().sendto(data, *args)

    monkeypatch.setattr(socket, 'socket', LoopbackSocket)
    adapter = MockNetworkAdapter(['wlan-test'])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as native, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as heartbeat:
        native.bind(('127.0.0.1', 0))
        heartbeat.bind(('127.0.0.1', 0))
        native.settimeout(2.0)
        heartbeat.settimeout(2.0)
        config = ClientDaemonConfig(
            node_id=4, tun_ip='10.80.0.14', tun_cidr='10.80.0.14/24',
            work_dir=str(tmp_path), channel=149, radio_txpower_dbm=14, uplink_mcs=4,
            server_control_host='127.0.0.1', server_control_port=heartbeat.getsockname()[1],
            broadcast_port=heartbeat.getsockname()[1], radio_control_port=native.getsockname()[1],
            enable_control_plane=True, enable_link_process=True,
        )
        process = mock.Mock()
        process.poll.return_value = None

        def start_native(*args, **kwargs):
            adapter.setup_tun(config.tun_name, config.tun_cidr)
            return process

        daemon = ClientDaemon(config, network_adapter=adapter, _link_process_factory=start_native)
        monkeypatch.setattr('wfb_ng.fl.client_daemon.shutil.which', lambda name: '/test/wfb_v6_uplink')
        assert daemon.poll_hardware_once() == 'wlan-test'
        daemon.start_idle_link()
        assert daemon.is_idle_link_running
        # 不启动监督线程，随后直接调用同一控制对象的公开寻频入口。
        with mock.patch.object(ControlPlaneClient, 'start'):
            daemon.start_control_plane()
        client = daemon.control_plane
        client.attempt_timeout_seconds = 0.03
        client.max_attempts_per_channel = 1
        records = []
        errors = []

        def native_peer():
            try:
                for channel in (149, 157):
                    request, address = native.recvfrom(128)
                    request_id, command, epoch = struct.unpack('!IBQ', request)
                    assert command == 5
                    assert adapter.wireless_states['wlan-test']['channel'] == channel
                    records.append((channel, epoch))
                    native.sendto(struct.pack('!II', request_id, 0), address)
                    raw, _ = heartbeat.recvfrom(4096)
                    assert b'NODE_HEARTBEAT' in raw
            except BaseException as exc:
                errors.append(exc)

        peer = threading.Thread(target=native_peer, daemon=True)
        peer.start()
        try:
            assert client.hunt_once(ladder=[149, 157]) is False
        finally:
            client.stop()
            peer.join(timeout=3.0)
            # 假进程不持有系统资源，不启动真实 native 或执行系统网卡命令。
            daemon._link_process = None
            if daemon._link_log_file is not None:
                daemon._link_log_file.close()
        assert not peer.is_alive(), '本机控制端点必须有界退出'
        assert not errors, repr(errors)
        assert [record[0] for record in records] == [149, 157]
        assert 0 < records[0][1] < records[1][1]
