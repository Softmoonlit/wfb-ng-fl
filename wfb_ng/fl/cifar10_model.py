"""固定 CIFAR10 ResNet18 初始模型的初始化与制品读取。"""

import argparse
import json
from pathlib import Path

import torch
from torch import nn
from torchvision.models.resnet import BasicBlock, ResNet

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


MODEL_FORMAT = 'cifar10-resnet18-fp32-v1'
INITIALIZATION_SOURCE = (
    'torchvision-resnet18-basicblock-cifar10-kaiming-normal-fan-out-relu-'
    'bn-standard-kaiming-uniform-a-sqrt5-v1'
)


class CIFAR10ResNet18(ResNet):
    """输入为 CPU FP32 的 [N, 3, 32, 32] 图像，输出十类 logits。"""

    def __init__(self) -> None:
        super().__init__(BasicBlock, [2, 2, 2, 2], num_classes=10)
        # CIFAR10 不使用 ImageNet ResNet 的首层下采样。
        self.conv1 = nn.Conv2d(
            3, 64, kernel_size=3, stride=1, padding=1, bias=False,
            device='cpu', dtype=torch.float32,
        )
        self.maxpool = nn.Identity()
        self.to(device='cpu', dtype=torch.float32)


def _initialize_parameters(model: nn.Module, generator: torch.Generator) -> None:
    """用独立 CPU 生成器初始化所有浮点参数和 BN 状态。"""
    for layer in model.modules():
        if isinstance(layer, nn.Conv2d):
            nn.init.kaiming_normal_(layer.weight, mode='fan_out', nonlinearity='relu',
                                    generator=generator)
        elif isinstance(layer, nn.BatchNorm2d):
            nn.init.ones_(layer.weight)
            nn.init.zeros_(layer.bias)
            nn.init.zeros_(layer.running_mean)
            nn.init.ones_(layer.running_var)
            layer.num_batches_tracked.zero_()
        elif isinstance(layer, nn.Linear):
            nn.init.kaiming_uniform_(layer.weight, a=5 ** 0.5, generator=generator)
            bound = 1 / (layer.in_features ** 0.5)
            nn.init.uniform_(layer.bias, -bound, bound, generator=generator)


MODEL_DEFINITION = ModelDefinition(
    model_format=MODEL_FORMAT,
    initialization_source=INITIALIZATION_SOURCE,
    model_factory=CIFAR10ResNet18,
    initialize_parameters=_initialize_parameters,
    error_code='invalid_cifar10_resnet18_model',
    artifact_label='CIFAR10 ResNet18 模型',
    max_model_bytes=64 * 1024 * 1024,
)
MAX_MODEL_BYTES = MODEL_DEFINITION.max_model_bytes
MODEL_CLASS = CIFAR10ResNet18


def initialize_model(output_path: ModelPath, seed: int) -> str:
    """生成未训练的 CPU／FP32 CIFAR10 ResNet18。"""
    return _initialize_model(output_path, seed, MODEL_DEFINITION)


def write_model_atomic(output_path: ModelPath, state_dict: dict, initialization_seed: int) -> str:
    """严格校验 CPU 模型并原子发布 CIFAR10 ResNet18。"""
    return _write_model_atomic(output_path, state_dict, initialization_seed, MODEL_DEFINITION)


def validate_state_dict(state) -> None:
    """验证固定 ResNet18 参数结构及 CPU／FP32／BN 整数状态。"""
    _validate_state_dict(state, MODEL_DEFINITION)


def load_model(model_path: ModelPath) -> ModelArtifact:
    """返回可独立修改的 CPU 模型及实际读取内容的 SHA-256。"""
    return _load_model(model_path, MODEL_DEFINITION)


def _dtype_statistics(model: nn.Module) -> dict:
    statistics = {}
    for tensor in model.state_dict().values():
        key = str(tensor.dtype).removeprefix('torch.')
        entry = statistics.setdefault(key, {'elements': 0, 'bytes': 0})
        entry['elements'] += tensor.numel()
        entry['bytes'] += tensor.numel() * tensor.element_size()
    return statistics


def main() -> None:
    parser = argparse.ArgumentParser(description='生成固定 CIFAR10 ResNet18 的未训练初始模型')
    parser.add_argument('output_path', help='新模型文件路径；不覆盖已有文件')
    parser.add_argument('--seed', type=int, required=True, help='初始化种子，范围 0～2**63-1')
    args = parser.parse_args()
    try:
        digest = initialize_model(args.output_path, args.seed)
        artifact = load_model(args.output_path)
    except FLRuntimeError as exc:
        parser.error(str(exc))
    print(json.dumps({
        'sha256': digest,
        'model_format': MODEL_FORMAT,
        'size_bytes': Path(args.output_path).stat().st_size,
        'dtype_statistics': _dtype_statistics(artifact.model),
        'parameter_count': sum(parameter.numel() for parameter in artifact.model.parameters()),
        'initialization': {'source': INITIALIZATION_SOURCE, 'seed': args.seed},
    }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
