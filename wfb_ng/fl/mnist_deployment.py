"""已发布 MNIST 算法 bundle 的固定入口映射。"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MnistDeployment:
    algorithm_id: str
    model_format: str
    module_name: str


_ENTRY_BUNDLES = {
    (
        'wfb_ng.fl.mnist:client_entry',
        'wfb_ng.fl.mnist:aggregate_entry',
        'wfb_ng.fl.mnist:evaluate_entry',
    ): MnistDeployment(
        algorithm_id='mnist-two-conv-fp32-v1',
        model_format='mnist-two-conv-fp32-v1',
        module_name='wfb_ng.fl.mnist',
    ),
    (
        'wfb_ng.fl.mnist_cnn_32_64:client_entry',
        'wfb_ng.fl.mnist_cnn_32_64:aggregate_entry',
        'wfb_ng.fl.mnist_cnn_32_64:evaluate_entry',
    ): MnistDeployment(
        algorithm_id='mnist-cnn-32-64-fp32-v1',
        model_format='mnist-cnn-32-64-fp32-v1',
        module_name='wfb_ng.fl.mnist_cnn_32_64',
    ),
}


_KNOWN_MNIST_ENTRIES = frozenset(entry for entries in _ENTRY_BUNDLES for entry in entries)


def is_known_mnist_entry(value: object) -> bool:
    return isinstance(value, str) and value in _KNOWN_MNIST_ENTRIES


def get_mnist_deployment(client_entry: object, aggregate_entry: object,
                         evaluate_entry: object) -> Optional[MnistDeployment]:
    if (not isinstance(client_entry, str)
            or not isinstance(aggregate_entry, str)
            or not isinstance(evaluate_entry, str)):
        return None
    return _ENTRY_BUNDLES.get((client_entry, aggregate_entry, evaluate_entry))
