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
                "-P '$UFTP_PRIVATE_GROUP'",
                "-p '$UFTP_PORT'",
                "-U 0x000000ff",
                "-H 0x00000001,0x00000002",
                "-Y none",
                "-R 15000",
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
            '"uftp_bind_host":"${addr%/*}","uftp_multicast_host":"$UFTP_GROUP","uftp_private_multicast_host":"$UFTP_PRIVATE_GROUP"',
            self.script)

    def test_formal_runtime_uses_mcs3_by_default(self):
        self.assertIn('ISSUE41_RADIO_MCS_INDEX:-3', self.script)

    def test_formal_runtime_defaults_to_two_40mib_rounds_with_immediate_clients(self):
        for fragment in (
                'ROUNDS="${ISSUE41_ROUNDS:-2}"',
                'INPUT_SIZE_BYTES=$((40 * 1024 * 1024))',
                'update-client1-40mib.bin',
                'update-client2-40mib.bin',
                'required_artifact_size_bytes',
                'delay=0; update_template_path="$CLIENT2_UPDATE_TEMPLATE_PATH"',
                '两个 client 的 40 MiB update 模板 SHA-256 必须不同',
                '"live_observation":true',
                'issue41_build_summary.py',
                'ISSUE41_LINK_LOG_INTERVAL_MS:-1000',
                '"--log-interval","$LINK_LOG_INTERVAL_MS"'):
            self.assertIn(fragment, self.script)

    def test_formal_runtime_enables_immediate_known_client_feedback_window(self):
        for fragment in (
                'ISSUE41_FEEDBACK_WINDOW_PERIOD_MS:-500',
                'ISSUE41_FEEDBACK_WINDOW_DURATION_MS:-15',
                '"--feedback-window-period-ms","$FEEDBACK_WINDOW_PERIOD_MS"',
                '"--feedback-window-duration-ms","$FEEDBACK_WINDOW_DURATION_MS"',
                '"--feedback-window-start-immediately"'):
            self.assertIn(fragment, self.script)

    def test_formal_runtime_asserts_uftp_routes_use_runtime_tuns(self):
        self.assertIn('assert_runtime_uftp_routes', self.script)
        for fragment in (
                'assert_runtime_uftp_route server "$SERVER_TUN"',
                'for group in "$UFTP_GROUP" "$UFTP_PRIVATE_GROUP"',
                'for role in client1 client2',
                'ip route show "$group/32"',
                '*"$group dev $tun"*)',
                'ip route get "$group" from "$source_ip"',
                'UFTP_PRIVATE_GROUP',
                'assert_runtime_uftp_routes\n    capture_runtime_routes'):
            self.assertIn(fragment, self.script)

    def test_formal_runtime_configures_monitor_radios_before_services(self):
        for fragment in (
                'configure_runtime_monitors',
                'server 空口网卡未进入 monitor 模式',
                'client1 空口网卡 monitor/UP 验证失败',
                'client2 空口网卡 monitor/UP 验证失败',
                'formal_runtime_loop/server/radio-health'):
            self.assertIn(fragment, self.script)

        runtime_body = self.script.split('cmd_run_runtime_loop() {', 1)[1]
        runtime_body = runtime_body.split('\n}', 1)[0]
        self.assertLess(
            runtime_body.index('prepare_runtime_state'),
            runtime_body.index('configure_runtime_monitors'))
        self.assertLess(
            runtime_body.index('configure_runtime_monitors'),
            runtime_body.index('write_issue41_configs server'))

    def test_runtime_disables_role_journal_rate_limiting_for_observations(self):
        self.assertEqual(2, self.script.count('LogRateLimitIntervalSec=0'))

    def test_runtime_starts_clients_ready_before_server_and_disables_restarts(self):
        for fragment in (
                'wait_remote_service_ready "$role" wfb-fl-client.service',
                'assert_remote_service_active "$role" wfb-fl-client.service',
                'sudo systemctl restart wfb-fl-server.service',
                'wait_runtime_results',
                'sudo python3 -c',
                "printf '[Service]\\nRestart=no\\nLogRateLimitIntervalSec=0\\nExecStart=\\n",
                'cmd_lifecycle_stop_restart',
                'cmd_collect\n    sudo rm -rf /var/lib/wfb-ng/issue41/server'):
            self.assertIn(fragment, self.script)

    def test_runtime_rejects_stale_work_dir_without_explicit_reset(self):
        for fragment in (
                'prepare_runtime_state',
                'assert_runtime_processes_stopped',
                'ISSUE41_RESET_RUNTIME_STATE',
                '默认拒绝复用，请先执行 clean',
                'sudo rm -rf /var/lib/wfb-ng/issue41/server',
                'sudo rm -rf /var/lib/wfb-ng/issue41/client'):
            self.assertIn(fragment, self.script)

    def test_stop_waits_for_processes_and_tuns_to_disappear(self):
        for fragment in (
                'STOP_CLEANUP_TIMEOUT_SECONDS',
                'wait_local_issue41_cleanup',
                'wait_remote_issue41_cleanup',
                '/sys/class/net/$SERVER_TUN',
                '/sys/class/net/$tun',
                '本机 issue41 停止后清理超时',
                'restart 后清理超时'):
            self.assertIn(fragment, self.script)

    def test_summary_keeps_collected_server_artifacts_readable_and_fails_closed(self):
        for fragment in (
                'sudo chown -R "$(id -u):$(id -g)" "$ARCHIVE_DIR/formal_runtime_loop/server"',
                '[ "$status" = passed ] || die "归档结论为 failed：$reason"'):
            self.assertIn(fragment, self.script)

    def test_smoke_writes_summary_markers(self):
        self.assertIn('write_smoke_marker "$name"', self.script)
        self.assertIn('"smoke":"$name"', self.script)
        self.assertIn('pre_runtime_smoke/%s', self.script)

    def test_preflight_archives_usb_health_with_optional_strict_mode(self):
        for fragment in (
                'ISSUE41_RADIO_MIN_USB_SPEED',
                'ISSUE41_STRICT_USB_SPEED',
                'capture_local_radio_health server',
                'capture_remote_radio_health "$role"',
                'usb-speed.txt',
                'usb-topology.txt',
                'kernel-radio.log',
                '低于建议值',
                '低于严格阈值'):
            self.assertIn(fragment, self.script)

    def test_downlink_failure_distinguishes_declare_from_ready_accept(self):
        for fragment in (
                'write_downlink_failure_diagnosis',
                "'client_declared_locally': client_declared",
                "'server_ready_accepted': server_accepted",
                "'local_declare_is_not_server_accept': True",
                "'missing_server_accept_is_not_a_sleep_transition': True",
                "'strict_runtime_participants_may_not_be_downgraded': True",
                'server_radio_receive_path_unhealthy_or_disconnected'):
            self.assertIn(fragment, self.script)


if __name__ == '__main__':
    unittest.main()
