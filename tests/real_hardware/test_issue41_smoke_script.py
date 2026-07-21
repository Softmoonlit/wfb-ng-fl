#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import subprocess
import unittest


SCRIPT = os.path.join(
    os.path.dirname(__file__), 'issue41_fl_runtime_loop.sh')


class Issue41SmokeScriptTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SCRIPT, 'r', encoding='utf-8') as fh:
            cls.script = fh.read()

    def test_script_syntax_is_valid(self):
        result = subprocess.run(
            ['bash', '-n', SCRIPT], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, check=False)

        self.assertEqual('', result.stderr)
        self.assertEqual(0, result.returncode)

    def test_downlink_smoke_uses_expected_uftp_arguments(self):
        for fragment in (
                "uftp -q -I '${SERVER_TUN_ADDR%/*}'",
                "-M '$UFTP_GROUP'",
                "-p '$UFTP_PORT'",
                "-U 0x000000ff",
                "-H 0x00000001,0x00000002",
                "-Y none",
                "-S '$status'",
                "-D '$src' 'model.bin' 'model.manifest.json'",
                "uftpd -d -q -I '$(client_ip \"$role\")' -M '$UFTP_GROUP'",
                "-F '$(smoke_dir \"$name\")/$role/uftpd.status'",
        ):
            self.assertIn(fragment, self.script)

    def test_uplink_smoke_uses_tun_bound_python_put(self):
        for fragment in (
                "with Server((host, port), Handler) as server:",
                "HTTPConnection(host, port, timeout=10, source_address=(source_ip, 0))",
                "conn.request('PUT', path, body=body",
                "'client_address': self.client_address[0]",
                "if event['client_address'] != expected_addr:",
        ):
            self.assertIn(fragment, self.script)

    def test_smoke_configures_channel_and_stops_http_receiver(self):
        for fragment in (
                "sudo iw dev \"$iface\" set channel \"$CHANNEL\" \"$CHANNEL_WIDTH\"",
                "remote \"$role\" \"sudo ip link set '$iface' down || true; sudo iw dev '$iface' set type monitor",
                "stop_smoke_http_server",
                "HTTP smoke receiver 孤儿进程",
        ):
            self.assertIn(fragment, self.script)

    def test_formal_client_config_uses_shared_uftp_multicast_host(self):
        self.assertIn(
            '"role":"client","work_dir":"$work_dir","node_id":$node_id,'
            '"uftp_uid":$node_id,"uftp_port":$UFTP_PORT,'
            '"server_http_host":"$HTTP_HOST","server_http_port":$HTTP_PORT,'
            '"uftp_bind_host":"${addr%/*}","uftp_multicast_host":"$UFTP_GROUP"',
            self.script)

    def test_formal_runtime_asserts_uftp_routes_use_runtime_tuns(self):
        self.assertIn('assert_runtime_uftp_routes', self.script)
        for fragment in (
                'assert_runtime_uftp_route server "$SERVER_TUN"',
                'assert_runtime_uftp_route client1 "$CLIENT1_TUN"',
                'assert_runtime_uftp_route client2 "$CLIENT2_TUN"',
                'ip route get "$UFTP_GROUP"'):
            self.assertIn(fragment, self.script)

    def test_smoke_writes_summary_markers(self):
        self.assertIn('write_smoke_marker "$name"', self.script)
        self.assertIn('"smoke":"$name"', self.script)
        self.assertIn('pre_runtime_smoke/%s', self.script)


if __name__ == '__main__':
    unittest.main()
