"""TUN existence must not release control traffic before its kernel route exists."""
from unittest.mock import patch

import pytest
from pyroute2 import NetlinkError

from wfb_ng.fl.client_daemon import LinuxNetworkAdapter


class Message(dict):
    def get_attr(self, name):
        return self.get(name)


@pytest.mark.parametrize('stage,expected', [
    ('absent', False), ('down', False), ('no-address', False),
    ('wrong-address', False), ('wrong-prefix', False), ('no-route', False),
    ('wrong-output', False), ('ready', True),
])
@pytest.mark.parametrize('destination', ['255.255.255.255', '10.80.0.1'])
def test_control_route_readiness_requires_up_address_and_correct_output(stage, expected, destination):
    with patch('wfb_ng.fl.client_daemon.IPRoute') as factory:
        ip = factory.return_value.__enter__.return_value
        ip.link_lookup.return_value = [] if stage == 'absent' else [42]
        ip.get_links.return_value = [Message(flags=0 if stage == 'down' else 1)]
        ip.get_addr.return_value = [] if stage == 'no-address' else [Message(
            IFA_LOCAL='10.80.0.13' if stage == 'wrong-address' else '10.80.0.12',
            prefixlen=32 if stage == 'wrong-prefix' else 24)]
        if stage == 'no-route':
            ip.route.side_effect = NetlinkError(101)
        else:
            ip.route.return_value = [Message(RTA_OIF=7 if stage == 'wrong-output' else 42)]
        assert LinuxNetworkAdapter().is_tun_ready('fl-c2', '10.80.0.12/24', destination) is expected
        if expected:
            ip.route.assert_called_once_with('get', dst=destination, src='10.80.0.12')


def test_unexpected_netlink_error_is_not_hidden_as_not_ready():
    with patch('wfb_ng.fl.client_daemon.IPRoute') as factory:
        ip = factory.return_value.__enter__.return_value
        ip.link_lookup.return_value = [42]
        ip.get_links.return_value = [Message(flags=1)]
        ip.get_addr.return_value = [Message(IFA_LOCAL='10.80.0.12', prefixlen=24)]
        ip.route.side_effect = NetlinkError(1)
        with pytest.raises(NetlinkError):
            LinuxNetworkAdapter().is_tun_ready('fl-c2', '10.80.0.12/24', '10.80.0.1')


def test_real_kernel_tun_creation_does_not_imply_control_route_ready():
    """Exercise TUNSETIFF -> UP -> address -> route in an isolated namespace."""
    import os
    import subprocess
    import sys
    import textwrap
    if os.geteuid() != 0:
        pytest.skip('run this isolated network namespace check as root')
    script = textwrap.dedent('''
        import fcntl, os, socket, struct
        from pyroute2 import IPRoute
        from wfb_ng.fl.client_daemon import LinuxNetworkAdapter
        fd = os.open('/dev/net/tun', os.O_RDWR)
        fcntl.ioctl(fd, 0x400454ca, struct.pack('16sH', b'fl-race', 0x1001))
        adapter = LinuxNetworkAdapter()
        with IPRoute() as ip:
            index = ip.link_lookup(ifname='fl-race')[0]
            assert index > 0  # netlink sees TUNSETIFF before UP/address/route
            assert not adapter.is_tun_ready('fl-race', '10.80.0.12/24', '10.80.0.1')
            ip.link('set', index=index, state='up')
            assert not adapter.is_tun_ready('fl-race', '10.80.0.12/24', '10.80.0.1')
            ip.addr('add', index=index, address='10.80.0.12', prefixlen=24)
            assert adapter.is_tun_ready('fl-race', '10.80.0.12/24', '10.80.0.1')
            ip.route('del', dst='10.80.0.0/24', oif=index, scope=253, proto=2)
            assert not adapter.is_tun_ready('fl-race', '10.80.0.12/24', '10.80.0.1')
            ip.route('add', dst='10.80.0.0/24', oif=index)
            assert adapter.is_tun_ready('fl-race', '10.80.0.12/24', '10.80.0.1')
            ip.addr('del', index=index, address='10.80.0.12', prefixlen=24)
            ip.addr('add', index=index, address='10.80.0.1', prefixlen=24)
            assert adapter.is_tun_ready('fl-race', '10.80.0.1/24', '255.255.255.255')
            # Reuse the same bound socket across the real TUN deletion/recreation.
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sender.bind(('10.80.0.1', 0))
            sender.sendto(b'baseline', ('255.255.255.255', 9000))
            os.close(fd)
            fd = os.open('/dev/net/tun', os.O_RDWR)
            fcntl.ioctl(fd, 0x400454ca, struct.pack('16sH', b'fl-race', 0x1001))
            index = ip.link_lookup(ifname='fl-race')[0]
            ip.link('set', index=index, state='up')
            assert not adapter.is_tun_ready('fl-race', '10.80.0.1/24', '255.255.255.255')
            try:
                sender.sendto(b'premature-ping', ('255.255.255.255', 9000))
            except OSError as exc:
                assert exc.errno == 101
            else:
                raise AssertionError('broadcast before address/route must reproduce E101')
            ip.addr('add', index=index, address='10.80.0.1', prefixlen=24)
            assert adapter.is_tun_ready('fl-race', '10.80.0.1/24', '255.255.255.255')
            sender.sendto(b'ready-ping', ('255.255.255.255', 9000))
            sender.close()
        os.close(fd)
    ''')
    result = subprocess.run(['unshare', '--net', sys.executable, '-c', script],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
