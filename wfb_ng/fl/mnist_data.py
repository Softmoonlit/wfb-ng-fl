"""直接校验预先解压的固定 MNIST IDX，不下载、复制入库或改写源文件。"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import struct

import torch

from .artifacts import CHUNK_SIZE, file_sha256, preflight_artifact
from .errors import FLRuntimeError


@dataclass(frozen=True)
class MNISTData:
    images: torch.Tensor
    labels: torch.Tensor
    sha256: dict


def _read_idx(path, expected_size, check_stop):
    check_stop()
    source, before = preflight_artifact(path, expected_size)
    try:
        if before.st_size != expected_size:
            raise FLRuntimeError('invalid_mnist_data', 'MNIST IDX 文件长度错误')
        data = bytearray()
        while len(data) < expected_size:
            check_stop()
            chunk = source.read(min(CHUNK_SIZE, expected_size - len(data)))
            if not chunk:
                raise FLRuntimeError('invalid_mnist_data', 'MNIST IDX 文件读取不完整')
            data.extend(chunk)
        after = os.fstat(source.fileno())
        identity = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if any(getattr(before, key) != getattr(after, key) for key in identity):
            raise FLRuntimeError('invalid_mnist_data', 'MNIST IDX 在读取期间发生变化')
    finally:
        source.close()
    check_stop()
    digest = hashlib.sha256(data).hexdigest()
    # 摘要与实际内存数据绑定，并复核源路径；官方来源由部署证据确认。
    if file_sha256(path) != digest:
        raise FLRuntimeError('invalid_mnist_data', 'MNIST IDX 摘要校验失败')
    check_stop()
    return data, digest


def load_mnist(data_dir, split, check_stop):
    """读取 train 的 60000 或 test 的 10000 样本，返回原始灰度及标签和摘要。"""
    if split not in ('train', 'test'):
        raise FLRuntimeError('invalid_mnist_data', 'MNIST 仅支持固定 train/test IDX')
    if not isinstance(data_dir, (str, Path)) or not str(data_dir):
        raise FLRuntimeError('invalid_mnist_data', '必须配置本机 mnist_data_dir')
    count, prefix = (60000, 'train') if split == 'train' else (10000, 't10k')
    image_name, label_name = f'{prefix}-images-idx3-ubyte', f'{prefix}-labels-idx1-ubyte'
    images, image_sha = _read_idx(Path(data_dir) / image_name, 16 + count * 784, check_stop)
    labels, label_sha = _read_idx(Path(data_dir) / label_name, 8 + count, check_stop)
    if (struct.unpack('>IIII', images[:16]) != (2051, count, 28, 28)
            or struct.unpack('>II', labels[:8]) != (2049, count)):
        raise FLRuntimeError('invalid_mnist_data', 'MNIST IDX 类型、维度或样本计数错误')
    # 原始字节自然约束图像到 0～255；每批再转换 FP32 并除以 255。
    image_tensor = torch.frombuffer(images, dtype=torch.uint8, offset=16).reshape(count, 1, 28, 28)
    label_tensor = torch.frombuffer(labels, dtype=torch.uint8, offset=8).to(torch.int64)
    if label_tensor.min().item() < 0 or label_tensor.max().item() > 9:
        raise FLRuntimeError('invalid_mnist_data', 'MNIST 标签必须为 0～9')
    check_stop()
    return MNISTData(image_tensor, label_tensor, {image_name: image_sha, label_name: label_sha})
