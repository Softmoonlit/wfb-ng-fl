"""显式装配 32/64 通道 MNIST CNN 的 FedAvg 入口。"""

from . import mnist as _algorithm
from . import mnist_cnn_model as _model


MODEL_FORMAT = _model.MODEL_FORMAT
MAX_MODEL_BYTES = _model.MAX_MODEL_BYTES
MODEL_CLASS = _model.MODEL_CLASS
BATCH_SIZE = _algorithm.BATCH_SIZE


def validate_config(config):
    return _algorithm.validate_config(config)


def validate_preparation_report(report, *, expected_samples=60000, require_gpu=True,
                                expected_seed=None):
    return _algorithm.validate_preparation_report(
        report, expected_samples=expected_samples, require_gpu=require_gpu,
        expected_seed=expected_seed, model_module=_model)


def read_update_metadata(path, *, node_id, round_index, base_model_sha256,
                         expected_sample_count):
    return _algorithm.read_update_metadata(
        path, node_id=node_id, round_index=round_index,
        base_model_sha256=base_model_sha256, expected_sample_count=expected_sample_count,
        model_module=_model)


def aggregate_entry(current_model_path, updates_by_node, output_model_path, config, context):
    return _algorithm.aggregate_entry(
        current_model_path, updates_by_node, output_model_path, config, context,
        model_module=_model)


def evaluate_entry(model_path, config, context):
    return _algorithm.evaluate_entry(model_path, config, context, model_module=_model)


def client_entry(runtime, config, context):
    return _algorithm.client_entry(runtime, config, context, model_module=_model)
