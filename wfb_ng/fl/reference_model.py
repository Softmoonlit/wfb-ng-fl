"""固定 MNIST 最小参考模型的初始化与制品读取。"""

import argparse
import json

import torch
from torch import nn
from torch.nn import functional as F

from .errors import FLRuntimeError
from .model_artifact import (
    ModelArtifact,
    ModelDefinition,
    ModelPath,
    initialize_model as _initialize_model,
    load_model as _load_model,
    validate_state_dict as _validate_state_dict,
    write_model_atomic as _write_model_atomic,
)


MODEL_FORMAT = 'mnist-two-conv-fp32-v1'
INITIALIZATION_SOURCE = 'xavier-uniform-weights-zero-bias-v1'


class MNISTReferenceModel(nn.Module):
    """输入为 [0, 1] 的 FP32 灰度图，输出十类 logits。"""

    def __init__(self) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(1, 8, 3, padding=1, device='cpu', dtype=torch.float32)
        self.conv2 = nn.Conv2d(8, 16, 3, padding=1, device='cpu', dtype=torch.float32)
        self.classifier = nn.Linear(16 * 7 * 7, 10, device='cpu', dtype=torch.float32)

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        images = F.max_pool2d(F.relu(self.conv1(images)), 2)
        images = F.max_pool2d(F.relu(self.conv2(images)), 2)
        return self.classifier(images.flatten(1))


def _initialize_parameters(model: nn.Module, generator: torch.Generator) -> None:
    for layer in (model.conv1, model.conv2, model.classifier):
        nn.init.xavier_uniform_(layer.weight, generator=generator)
        assert layer.bias is not None
        nn.init.zeros_(layer.bias)


MODEL_DEFINITION = ModelDefinition(
    model_format=MODEL_FORMAT,
    initialization_source=INITIALIZATION_SOURCE,
    model_factory=MNISTReferenceModel,
    initialize_parameters=_initialize_parameters,
    error_code='invalid_reference_model',
    artifact_label='参考模型',
    publication_error_code='reference_model_write_failed',
    cleanup_error_code='reference_model_cleanup_failed',
    cleanup_error_message='模型清理未完成，目标模型可能仍然存在；请检查输出路径和临时文件',
)
MAX_MODEL_BYTES = MODEL_DEFINITION.max_model_bytes
MODEL_CLASS = MNISTReferenceModel


def initialize_model(output_path: ModelPath, seed: int) -> str:
    """生成未训练的 CPU／FP32 最小参考模型。"""
    return _initialize_model(output_path, seed, MODEL_DEFINITION)


def write_model_atomic(output_path: ModelPath, state_dict: dict, initialization_seed: int) -> str:
    """严格校验 CPU 完整参数并原子发布最小参考模型。"""
    return _write_model_atomic(output_path, state_dict, initialization_seed, MODEL_DEFINITION)


def validate_state_dict(state) -> None:
    """验证固定 9098 参数结构及 CPU／FP32／有限数值。"""
    _validate_state_dict(state, MODEL_DEFINITION)


def load_model(model_path: ModelPath) -> ModelArtifact:
    """返回可独立修改的 CPU 模型及实际读取内容的 SHA-256。"""
    return _load_model(model_path, MODEL_DEFINITION)


def main() -> None:
    parser = argparse.ArgumentParser(description='生成固定 MNIST 最小参考任务的未训练初始模型')
    parser.add_argument('output_path', help='新模型文件路径；不覆盖已有文件')
    parser.add_argument('--seed', type=int, required=True, help='初始化种子，范围 0～2**63-1')
    args = parser.parse_args()
    try:
        digest = initialize_model(args.output_path, args.seed)
    except FLRuntimeError as exc:
        parser.error(str(exc))
    print(json.dumps({'sha256': digest, 'model_format': MODEL_FORMAT,
                      'initialization': {'source': INITIALIZATION_SOURCE, 'seed': args.seed}},
                     ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
