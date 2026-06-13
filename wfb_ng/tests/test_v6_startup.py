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


def make_server_profile(link_security_mode='legacy_encrypted', include_keypair=True, known_clients=None):
    if known_clients is None:
        known_clients = [1, 2]

    profiles = ['base', 'server_tunnel']
    sections = {
        'common': {
            'log_interval': 1500,
        },
        'server_profile': {
            'role': 'server',
            'node_id': 9,
            'link_domain': 'test-link',
            'uplink_stream': 0x20,
            'downlink_stream': 0xa0,
            'link_security_mode': link_security_mode,
            'streams': [
                {
                    'name': 'tunnel',
                    'service_type': 'tunnel',
                    'stream_rx': 0xa0,
                    'stream_tx': 0x20,
                    'profiles': profiles,
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
            'known_clients': known_clients,
            'grant_duration_ms': 75,
            'guard_interval_ms': 10,
            'feedback_window_period_ms': 500,
            'feedback_window_duration_ms': 15,
            'downlink_pause_threshold_bytes': 131072,
            'downlink_resume_threshold_bytes': 65536,
            'downlink_queue_packets_limit': 64,
            'rx_reassembly_unfinished_block_limit': 16,
        },
    }

    if include_keypair:
        profiles.insert(1, 'server_crypto_base')
        sections['server_crypto_base'] = {
            'keypair': 'gs.key',
        }

    return sections


def make_client_profile(link_security_mode='legacy_encrypted', include_keypair=True):
    profiles = ['base', 'client_tunnel']
    sections = {
        'common': {
            'log_interval': 1500,
        },
        'client_profile': {
            'role': 'client',
            'node_id': 9,
            'link_domain': 'test-link',
            'uplink_stream': 0x20,
            'downlink_stream': 0xa0,
            'link_security_mode': link_security_mode,
            'streams': [
                {
                    'name': 'tunnel',
                    'service_type': 'tunnel',
                    'stream_rx': 0xa0,
                    'stream_tx': 0x20,
                    'profiles': profiles,
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
    }

    if include_keypair:
        profiles.insert(1, 'client_crypto_base')
        sections['client_crypto_base'] = {
            'keypair': 'drone.key',
        }

    return sections


class V6StartupConfigTestCase(unittest.TestCase):
    def test_resolve_server_profile_builds_effective_summary(self):
        module = load_module()
        settings = make_settings(make_server_profile())

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
        settings = make_settings(make_server_profile(known_clients=[1]))

        with self.assertRaisesRegex(module.V6StartupConfigError, 'role 来源冲突'):
            module.resolve_v6_startup_config(settings, 'server_profile', ['wlan0'], role_override='client')

    def test_server_requires_non_empty_known_clients(self):
        module = load_module()
        settings = make_settings(make_server_profile(known_clients=[]))

        with self.assertRaisesRegex(module.V6StartupConfigError, 'known_clients 必须是非空 NODE_ID 集合'):
            module.resolve_v6_startup_config(settings, 'server_profile', ['wlan0'])

    def test_trusted_plaintext_rejects_legacy_keypair_material(self):
        module = load_module()
        settings = make_settings(make_client_profile(link_security_mode='trusted_plaintext', include_keypair=True))

        with self.assertRaisesRegex(module.V6StartupConfigError, 'trusted_plaintext 下禁止旧 keypair'):
            module.resolve_v6_startup_config(settings, 'client_profile', ['wlan0'])

    def test_legacy_encrypted_requires_keypair_material(self):
        module = load_module()
        settings = make_settings(make_client_profile(link_security_mode='legacy_encrypted', include_keypair=False))

        with self.assertRaisesRegex(module.V6StartupConfigError, 'legacy_encrypted 缺少旧 keypair'):
            module.resolve_v6_startup_config(settings, 'client_profile', ['wlan0'])

    def test_multiple_profiles_must_share_link_security_mode(self):
        module = load_module()
        sections = {
            'common': {
                'log_interval': 1000,
            },
            'client_a': {
                'role': 'client',
                'node_id': 9,
                'link_domain': 'client-a-link',
                'uplink_stream': 0x20,
                'downlink_stream': 0xa0,
                'link_security_mode': 'trusted_plaintext',
                'streams': [
                    {
                        'name': 'tunnel',
                        'service_type': 'tunnel',
                        'stream_rx': 0xa0,
                        'stream_tx': 0x20,
                        'profiles': ['base', 'client_a_tunnel'],
                    },
                ],
            },
            'client_b': {
                'role': 'client',
                'node_id': 10,
                'link_domain': 'client-b-link',
                'uplink_stream': 0x21,
                'downlink_stream': 0xa1,
                'link_security_mode': 'legacy_encrypted',
                'streams': [
                    {
                        'name': 'tunnel',
                        'service_type': 'tunnel',
                        'stream_rx': 0xa1,
                        'stream_tx': 0x21,
                        'profiles': ['base', 'client_b_crypto_base', 'client_b_tunnel'],
                    },
                ],
            },
            'base': {
                'stream_rx': None,
                'stream_tx': None,
            },
            'client_a_tunnel': {
                'ifname': 'cli-a-wfb',
                'ifaddr': '10.0.0.2/24',
                'default_route': False,
            },
            'client_b_crypto_base': {
                'keypair': 'drone.key',
            },
            'client_b_tunnel': {
                'ifname': 'cli-b-wfb',
                'ifaddr': '10.0.0.3/24',
                'default_route': False,
            },
            'client_a_client': {
                'uplink_token_gate_enabled': True,
                'uplink_pause_threshold_bytes': 131072,
                'uplink_resume_threshold_bytes': 65536,
                'uplink_queue_packets_limit': 64,
            },
            'client_b_client': {
                'uplink_token_gate_enabled': True,
                'uplink_pause_threshold_bytes': 131072,
                'uplink_resume_threshold_bytes': 65536,
                'uplink_queue_packets_limit': 64,
            },
        }
        settings = make_settings(sections)

        with self.assertRaisesRegex(module.V6StartupConfigError, '所有 profile 必须解析为同一 link_security_mode'):
            module.resolve_v6_startup_configs(settings, ['client_a', 'client_b'], ['wlan0'])

    def test_server_rejects_client_only_role_section(self):
        module = load_module()
        sections = make_server_profile(known_clients=[1])
        sections['server_profile_client'] = {
            'uplink_token_gate_enabled': True,
            'uplink_pause_threshold_bytes': 131072,
            'uplink_resume_threshold_bytes': 65536,
            'uplink_queue_packets_limit': 64,
        }
        settings = make_settings(sections)

        with self.assertRaisesRegex(module.V6StartupConfigError, '专属参数段'):
            module.resolve_v6_startup_config(settings, 'server_profile', ['wlan0'])

    def test_unknown_link_security_mode_is_rejected(self):
        module = load_module()
        settings = make_settings(make_client_profile(link_security_mode='mystery_mode', include_keypair=False))

        with self.assertRaisesRegex(module.V6StartupConfigError, 'link_security_mode 必须是 trusted_plaintext 或 legacy_encrypted'):
            module.resolve_v6_startup_config(settings, 'client_profile', ['wlan0'])

    def test_multiple_profiles_must_resolve_to_same_role(self):
        module = load_module()
        sections = {
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
                        'profiles': ['base', 'server_crypto_base', 'server_tunnel'],
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
                        'profiles': ['base', 'client_crypto_base', 'client_tunnel'],
                    },
                ],
            },
            'base': {
                'stream_rx': None,
                'stream_tx': None,
            },
            'server_crypto_base': {
                'keypair': 'gs.key',
            },
            'server_tunnel': {
                'ifname': 'srv-wfb',
                'ifaddr': '10.0.0.1/24',
                'default_route': False,
            },
            'client_crypto_base': {
                'keypair': 'drone.key',
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
        }
        settings = make_settings(sections)

        with self.assertRaisesRegex(module.V6StartupConfigError, '所有 profile 必须解析为同一 role'):
            module.resolve_v6_startup_configs(settings, ['server_profile', 'client_profile'], ['wlan0'])

    def test_format_summary_stable_json(self):
        module = load_module()
        settings = make_settings(make_server_profile())

        summary = module.resolve_v6_startup_configs(settings, ['server_profile'], ['wlan0'])
        text = module.format_v6_startup_summary(summary)

        self.assertEqual(text, module.format_v6_startup_summary(summary))
        self.assertIn('"role": "server"', text)
        self.assertIn('"known_clients": [1, 2]', text)
        self.assertIn('"link_security_mode": "legacy_encrypted"', text)

    def test_env_role_can_supply_explicit_role(self):
        module = load_module()
        sections = make_server_profile()
        del sections['server_profile']['role']
        settings = make_settings(sections)

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
        settings = make_settings(make_client_profile(link_security_mode='trusted_plaintext', include_keypair=False))

        summary = module.resolve_v6_startup_configs(settings, ['client_profile'], ['wlan0'])
        text = module.format_v6_startup_summary(summary)

        self.assertIn('"link_security_mode": "trusted_plaintext"', text)



    def test_services_trusted_plaintext_uses_runtime_sentinel_and_rx_fec_args(self):
        import sys
        from unittest.mock import MagicMock
        sys.modules['twisted'] = MagicMock()
        sys.modules['twisted.python'] = MagicMock()
        sys.modules['twisted.python.log'] = MagicMock()
        sys.modules['twisted.python.failure'] = MagicMock()
        sys.modules['twisted.internet'] = MagicMock()
        sys.modules['twisted.internet.serialport'] = MagicMock()
        sys.modules['twisted.internet.protocol'] = MagicMock()
        sys.modules['twisted.internet.endpoints'] = MagicMock()
        services_path = os.path.join(PROJECT_ROOT, 'wfb_ng', 'services.py')
        spec = importlib.util.spec_from_file_location('services_under_test', services_path)
        services = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(services)

        original_settings = services.settings
        try:
            services.settings = types.SimpleNamespace(
                path=types.SimpleNamespace(bin_dir='/bin', conf_dir='/conf'),
                common=types.SimpleNamespace(tx_rcv_buf_size=111, rx_snd_buf_size=222, log_interval=333),
            )
            cfg = types.SimpleNamespace(
                link_security_mode='trusted_plaintext',
                keypair='ignored.key',
                fec_k=4,
                fec_n=6,
            )

            self.assertEqual('__trusted_plaintext__', services.resolve_runtime_keypair(cfg))
            self.assertEqual(' -k 4 -n 6', services.build_plaintext_rx_fec_args(cfg))

            legacy_cfg = types.SimpleNamespace(
                link_security_mode='legacy_encrypted',
                keypair='gs.key',
                fec_k=8,
                fec_n=12,
            )
            self.assertEqual('/conf/gs.key', services.resolve_runtime_keypair(legacy_cfg))
            self.assertEqual('', services.build_plaintext_rx_fec_args(legacy_cfg))
        finally:
            services.settings = original_settings
if __name__ == '__main__':
    unittest.main()
