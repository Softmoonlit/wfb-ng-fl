"""真实作业宿主入口检查；计算正确性由 MNIST 专项测试覆盖。"""
import tempfile
import unittest
from pathlib import Path

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.server_daemon import ServerDaemonConfig, load_server_config


class TrainingDeploymentConfigTests(unittest.TestCase):
    def test_defaults_are_explicit_real_algorithm_functions(self):
        config = ServerDaemonConfig()
        self.assertEqual(config.client_entry, 'wfb_ng.fl.mnist:client_entry')
        self.assertEqual(config.aggregate_entry, 'wfb_ng.fl.mnist:aggregate_entry')
        self.assertEqual(config.evaluate_entry, 'wfb_ng.fl.mnist:evaluate_entry')
        self.assertEqual(config.algorithm_config, {'data_seed': 2026})
        self.assertIsNone(config.mnist_data_dir)

    def test_algorithm_and_local_dataset_are_loaded_from_deployment_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'server.json'
            path.write_text('{"mnist_data_dir":"/data/test","algorithm_config":{"data_seed":17},'
                            '"client_entry":"custom:client","aggregate_entry":"custom:aggregate",'
                            '"evaluate_entry":"custom:evaluate"}')
            config = load_server_config(str(path))
            self.assertEqual(config.mnist_data_dir, '/data/test')
            self.assertEqual(config.algorithm_config, {'data_seed': 17})
            self.assertEqual(config.aggregate_entry, 'custom:aggregate')

    def test_local_paths_are_not_permitted_in_wire_algorithm_config(self):
        for config in ({'mnist_data_dir': '/server/private'}, {'model_path': '/server/model'},
                       {'nested': {'train_data_path': '/server/train'}}):
            with self.subTest(config=config), self.assertRaises(FLRuntimeError):
                ServerDaemonConfig(algorithm_config=config)

    def test_all_deployment_deadlines_are_loaded_and_reject_nonfinite_and_boolean(self):
        import json
        from dataclasses import FrozenInstanceError
        for field in ('preparation_timeout_seconds', 'round_timeout_seconds', 'io_timeout_seconds'):
            for invalid in (True, False, 0, -1, float('inf'), float('nan'), None):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(FLRuntimeError):
                    ServerDaemonConfig(**{field: invalid})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'server.json'
            path.write_text(json.dumps(dict(preparation_timeout_seconds=11,
                                            round_timeout_seconds=900, io_timeout_seconds=180)))
            config = load_server_config(str(path))
            self.assertEqual((config.preparation_timeout_seconds, config.round_timeout_seconds,
                              config.io_timeout_seconds), (11.0, 900.0, 180.0))
            with self.assertRaises(FrozenInstanceError):
                config.round_timeout_seconds = 1
