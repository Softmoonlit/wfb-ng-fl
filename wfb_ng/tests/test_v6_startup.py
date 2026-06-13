#!/usr/bin/env python
# -*- coding: utf-8 -*-

import importlib.util
import os
import types
import unittest


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MODULE_PATH = os.path.join(PROJECT_ROOT, 'wfb_ng', 'v6_startup.py')


def load_module():
    spec = importlib.util.spec_from_file_location('v6_startup_under_test', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_settings(sections):
    settings = types.SimpleNamespace()
    for name, values in sections.items():
        setattr(settings, name, types.SimpleNamespace(**values))
    return settings


class V6StartupConfigTestCase(unittest.TestCase):
    def test_resolve_server_profile_builds_effective_summary(self):
        module = load_module()
        settings = make_settings({
            'common': {
                'log_interval': 1500,
            },
            'server_profile': {
                'role': 'server',
                'node_id': 9,
                'link_domain': 'test-link',
                'uplink_stream': 0x20,
                'downlink_stream': 0xa0,
                'link_security_mode': 'legacy_encrypted',
                'streams': [
                    {
                        'name': 'tunnel',
                        'service_type': 'tunnel',
                        'stream_rx': 0xa0,
                        'stream_tx': 0x20,
                        'profiles': ['base', 'server_tunnel'],
                    },
                ],
            },
            'base': {
                'stream_rx': None,
                'stream_tx': None,
            },
            'server_tunnel': {
                'ifname': 'srv-wfb',
                'ifaddr': '10.0.0.1/24',
                'default_route': False,
            },
            'server_profile_server': {
                'known_clients': [1, 2],
                'grant_duration_ms': 75,
                'guard_interval_ms': 10,
                'feedback_window_period_ms': 500,
                'feedback_window_duration_ms': 15,
                'downlink_pause_threshold_bytes': 131072,
                'downlink_resume_threshold_bytes': 65536,
                'downlink_queue_packets_limit': 64,
                'rx_reassembly_unfinished_block_limit': 16,
            },
        })

        summary = module.resolve_v6_startup_config(settings, 'server_profile', ['wlan0'])

        self.assertEqual('server_profile', summary['profile'])
        self.assertEqual('server', summary['common']['role'])
        self.assertEqual(9, summary['common']['node_id'])
        self.assertEqual('test-link', summary['common']['link_domain'])
        self.assertEqual(module.hash_link_domain('test-link'), summary['common']['link_id'])
        self.assertEqual(0x20, summary['common']['uplink_stream'])
        self.assertEqual(0xa0, summary['common']['downlink_stream'])
        self.assertEqual(['wlan0'], summary['common']['radio_interfaces'])
        self.assertEqual('legacy_encrypted', summary['common']['link_security_mode'])
        self.assertEqual(1500, summary['common']['log_interval_ms'])
        self.assertEqual({'ifname': 'srv-wfb', 'ifaddr': '10.0.0.1/24', 'default_route': False}, summary['server']['tun'])
        self.assertEqual([1, 2], summary['server']['known_clients'])
        self.assertEqual({'duration_ms': 75, 'guard_interval_ms': 10}, summary['server']['grant'])
        self.assertEqual({'period_ms': 500, 'duration_ms': 15}, summary['server']['feedback_window'])
        self.assertEqual({
            'pause_threshold_bytes': 131072,
            'resume_threshold_bytes': 65536,
            'queued_packets_limit': 64,
        }, summary['server']['downlink_queue'])
        self.assertEqual({'unfinished_block_limit': 16}, summary['server']['rx_reassembly'])


    def test_role_override_conflicts_with_profile_role(self):
        module = load_module()
        settings = make_settings({
            'common': {
                'log_interval': 1000,
            },
            'server_profile': {
                'role': 'server',
                'node_id': 9,
                'link_domain': 'test-link',
                'uplink_stream': 0x20,
                'downlink_stream': 0xa0,
                'link_security_mode': 'legacy_encrypted',
                'streams': [
                    {
                        'name': 'tunnel',
                        'service_type': 'tunnel',
                        'stream_rx': 0xa0,
                        'stream_tx': 0x20,
                        'profiles': ['base', 'server_tunnel'],
                    },
                ],
            },
            'base': {
                'stream_rx': None,
                'stream_tx': None,
            },
            'server_tunnel': {
                'ifname': 'srv-wfb',
                'ifaddr': '10.0.0.1/24',
                'default_route': False,
            },
            'server_profile_server': {
                'known_clients': [1],
                'grant_duration_ms': 75,
                'guard_interval_ms': 10,
                'feedback_window_period_ms': 500,
                'feedback_window_duration_ms': 15,
                'downlink_pause_threshold_bytes': 131072,
                'downlink_resume_threshold_bytes': 65536,
                'downlink_queue_packets_limit': 64,
                'rx_reassembly_unfinished_block_limit': 16,
            },
        })

        with self.assertRaisesRegex(module.V6StartupConfigError, 'role 来源冲突'):
            module.resolve_v6_startup_config(settings, 'server_profile', ['wlan0'], role_override='client')
    def test_server_requires_non_empty_known_clients(self):
        module = load_module()
        settings = make_settings({
            'common': {
                'log_interval': 1000,
            },
            'server_profile': {
                'role': 'server',
                'node_id': 9,
                'link_domain': 'test-link',
                'uplink_stream': 0x20,
                'downlink_stream': 0xa0,
                'link_security_mode': 'legacy_encrypted',
                'streams': [
                    {
                        'name': 'tunnel',
                        'service_type': 'tunnel',
                        'stream_rx': 0xa0,
                        'stream_tx': 0x20,
                        'profiles': ['base', 'server_tunnel'],
                    },
                ],
            },
            'base': {
                'stream_rx': None,
                'stream_tx': None,
            },
            'server_tunnel': {
                'ifname': 'srv-wfb',
                'ifaddr': '10.0.0.1/24',
                'default_route': False,
            },
            'server_profile_server': {
                'known_clients': [],
                'grant_duration_ms': 75,
                'guard_interval_ms': 10,
                'feedback_window_period_ms': 500,
                'feedback_window_duration_ms': 15,
                'downlink_pause_threshold_bytes': 131072,
                'downlink_resume_threshold_bytes': 65536,
                'downlink_queue_packets_limit': 64,
                'rx_reassembly_unfinished_block_limit': 16,
            },
        })

        with self.assertRaisesRegex(module.V6StartupConfigError, 'known_clients 必须是非空 NODE_ID 集合'):
            module.resolve_v6_startup_config(settings, 'server_profile', ['wlan0'])
    def test_server_rejects_client_only_role_section(self):
        module = load_module()
        settings = make_settings({
            'common': {
                'log_interval': 1000,
            },
            'server_profile': {
                'role': 'server',
                'node_id': 9,
                'link_domain': 'test-link',
                'uplink_stream': 0x20,
                'downlink_stream': 0xa0,
                'link_security_mode': 'legacy_encrypted',
                'streams': [
                    {
                        'name': 'tunnel',
                        'service_type': 'tunnel',
                        'stream_rx': 0xa0,
                        'stream_tx': 0x20,
                        'profiles': ['base', 'server_tunnel'],
                    },
                ],
            },
            'base': {
                'stream_rx': None,
                'stream_tx': None,
            },
            'server_tunnel': {
                'ifname': 'srv-wfb',
                'ifaddr': '10.0.0.1/24',
                'default_route': False,
            },
            'server_profile_server': {
                'known_clients': [1],
                'grant_duration_ms': 75,
                'guard_interval_ms': 10,
                'feedback_window_period_ms': 500,
                'feedback_window_duration_ms': 15,
                'downlink_pause_threshold_bytes': 131072,
                'downlink_resume_threshold_bytes': 65536,
                'downlink_queue_packets_limit': 64,
                'rx_reassembly_unfinished_block_limit': 16,
            },
            'server_profile_client': {
                'uplink_token_gate_enabled': True,
                'uplink_pause_threshold_bytes': 131072,
                'uplink_resume_threshold_bytes': 65536,
                'uplink_queue_packets_limit': 64,
            },
        })

        with self.assertRaisesRegex(module.V6StartupConfigError, '专属参数段'):
            module.resolve_v6_startup_config(settings, 'server_profile', ['wlan0'])

    def test_multiple_profiles_must_resolve_to_same_role(self):
        module = load_module()
        settings = make_settings({
            'common': {
                'log_interval': 1000,
            },
            'server_profile': {
                'role': 'server',
                'node_id': 9,
                'link_domain': 'server-link',
                'uplink_stream': 0x20,
                'downlink_stream': 0xa0,
                'link_security_mode': 'legacy_encrypted',
                'streams': [
                    {
                        'name': 'tunnel',
                        'service_type': 'tunnel',
                        'stream_rx': 0xa0,
                        'stream_tx': 0x20,
                        'profiles': ['base', 'server_tunnel'],
                    },
                ],
            },
            'client_profile': {
                'role': 'client',
                'node_id': 10,
                'link_domain': 'client-link',
                'uplink_stream': 0x21,
                'downlink_stream': 0xa1,
                'link_security_mode': 'legacy_encrypted',
                'streams': [
                    {
                        'name': 'tunnel',
                        'service_type': 'tunnel',
                        'stream_rx': 0xa1,
                        'stream_tx': 0x21,
                        'profiles': ['base', 'client_tunnel'],
                    },
                ],
            },
            'base': {
                'stream_rx': None,
                'stream_tx': None,
            },
            'server_tunnel': {
                'ifname': 'srv-wfb',
                'ifaddr': '10.0.0.1/24',
                'default_route': False,
            },
            'client_tunnel': {
                'ifname': 'cli-wfb',
                'ifaddr': '10.0.0.2/24',
                'default_route': False,
            },
            'server_profile_server': {
                'known_clients': [10],
                'grant_duration_ms': 75,
                'guard_interval_ms': 10,
                'feedback_window_period_ms': 500,
                'feedback_window_duration_ms': 15,
                'downlink_pause_threshold_bytes': 131072,
                'downlink_resume_threshold_bytes': 65536,
                'downlink_queue_packets_limit': 64,
                'rx_reassembly_unfinished_block_limit': 16,
            },
            'client_profile_client': {
                'uplink_token_gate_enabled': True,
                'uplink_pause_threshold_bytes': 131072,
                'uplink_resume_threshold_bytes': 65536,
                'uplink_queue_packets_limit': 64,
            },
        })

        with self.assertRaisesRegex(module.V6StartupConfigError, '所有 profile 必须解析为同一 role'):
            module.resolve_v6_startup_configs(settings, ['server_profile', 'client_profile'], ['wlan0'])
    def test_format_summary_stable_json(self):
        module = load_module()
        settings = make_settings({
            'common': {
                'log_interval': 1500,
            },
            'server_profile': {
                'role': 'server',
                'node_id': 9,
                'link_domain': 'test-link',
                'uplink_stream': 0x20,
                'downlink_stream': 0xa0,
                'link_security_mode': 'legacy_encrypted',
                'streams': [
                    {
                        'name': 'tunnel',
                        'service_type': 'tunnel',
                        'stream_rx': 0xa0,
                        'stream_tx': 0x20,
                        'profiles': ['base', 'server_tunnel'],
                    },
                ],
            },
            'base': {
                'stream_rx': None,
                'stream_tx': None,
            },
            'server_tunnel': {
                'ifname': 'srv-wfb',
                'ifaddr': '10.0.0.1/24',
                'default_route': False,
            },
            'server_profile_server': {
                'known_clients': [1, 2],
                'grant_duration_ms': 75,
                'guard_interval_ms': 10,
                'feedback_window_period_ms': 500,
                'feedback_window_duration_ms': 15,
                'downlink_pause_threshold_bytes': 131072,
                'downlink_resume_threshold_bytes': 65536,
                'downlink_queue_packets_limit': 64,
                'rx_reassembly_unfinished_block_limit': 16,
            },
        })

        summary = module.resolve_v6_startup_configs(settings, ['server_profile'], ['wlan0'])
        text = module.format_v6_startup_summary(summary)

        self.assertEqual(text, module.format_v6_startup_summary(summary))
        self.assertIn('"role": "server"', text)
        self.assertIn('"known_clients": [1, 2]', text)
    def test_env_role_can_supply_explicit_role(self):
        module = load_module()
        settings = make_settings({
            'common': {
                'log_interval': 1500,
            },
            'server_profile': {
                'node_id': 9,
                'link_domain': 'test-link',
                'uplink_stream': 0x20,
                'downlink_stream': 0xa0,
                'link_security_mode': 'legacy_encrypted',
                'streams': [
                    {
                        'name': 'tunnel',
                        'service_type': 'tunnel',
                        'stream_rx': 0xa0,
                        'stream_tx': 0x20,
                        'profiles': ['base', 'server_tunnel'],
                    },
                ],
            },
            'base': {
                'stream_rx': None,
                'stream_tx': None,
            },
            'server_tunnel': {
                'ifname': 'srv-wfb',
                'ifaddr': '10.0.0.1/24',
                'default_route': False,
            },
            'server_profile_server': {
                'known_clients': [1, 2],
                'grant_duration_ms': 75,
                'guard_interval_ms': 10,
                'feedback_window_period_ms': 500,
                'feedback_window_duration_ms': 15,
                'downlink_pause_threshold_bytes': 131072,
                'downlink_resume_threshold_bytes': 65536,
                'downlink_queue_packets_limit': 64,
                'rx_reassembly_unfinished_block_limit': 16,
            },
        })

        summary = module.resolve_v6_startup_config(settings, 'server_profile', ['wlan0'], env={'WFB_ROLE': 'server'})
        self.assertEqual('server', summary['common']['role'])
    def test_detect_v6_opt_in_from_profile_role(self):
        module = load_module()
        settings = make_settings({
            'server_profile': {
                'role': 'server',
            },
        })

        self.assertTrue(module.should_use_v6_startup_path(settings, ['server_profile'], None, {}))

    def test_detect_v6_opt_in_stays_disabled_for_legacy_profile(self):
        module = load_module()
        settings = make_settings({
            'legacy_profile': {
                'streams': [],
            },
        })

        self.assertFalse(module.should_use_v6_startup_path(settings, ['legacy_profile'], None, {}))
    def test_explicit_cli_role_enables_v6_path_without_profile_role(self):
        module = load_module()
        settings = make_settings({
            'legacy_profile': {
                'streams': [],
            },
        })

        self.assertTrue(module.should_use_v6_startup_path(settings, ['legacy_profile'], 'server', {}))
    def test_format_summary_preserves_trusted_plaintext_marker_field(self):
        module = load_module()
        settings = make_settings({
            'common': {
                'log_interval': 1500,
            },
            'client_profile': {
                'role': 'client',
                'node_id': 9,
                'link_domain': 'test-link',
                'uplink_stream': 0x20,
                'downlink_stream': 0xa0,
                'link_security_mode': 'trusted_plaintext',
                'streams': [
                    {
                        'name': 'tunnel',
                        'service_type': 'tunnel',
                        'stream_rx': 0xa0,
                        'stream_tx': 0x20,
                        'profiles': ['base', 'client_tunnel'],
                    },
                ],
            },
            'base': {
                'stream_rx': None,
                'stream_tx': None,
            },
            'client_tunnel': {
                'ifname': 'cli-wfb',
                'ifaddr': '10.0.0.2/24',
                'default_route': False,
            },
            'client_profile_client': {
                'uplink_token_gate_enabled': True,
                'uplink_pause_threshold_bytes': 131072,
                'uplink_resume_threshold_bytes': 65536,
                'uplink_queue_packets_limit': 64,
            },
        })

        summary = module.resolve_v6_startup_configs(settings, ['client_profile'], ['wlan0'])
        text = module.format_v6_startup_summary(summary)

        self.assertIn('"link_security_mode": "trusted_plaintext"', text)
if __name__ == '__main__':
    unittest.main()
