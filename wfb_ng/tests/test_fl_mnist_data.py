"""固定官方 IDX 文件的公开读取边界；测试不下载或复制数据集。"""
import hashlib
import struct

import pytest

torch = pytest.importorskip('torch')
from wfb_ng.fl.errors import FLRuntimeError


def write_test_idx(directory, defect=None):
    images = struct.pack('>IIII', 2051, 10000, 28, 28) + bytes(10000 * 784)
    labels = struct.pack('>II', 2049, 10000) + bytes(10000)
    if defect == 'magic':
        images = bytes(4) + images[4:]
    elif defect == 'dimension':
        images = struct.pack('>IIII', 2051, 10000, 14, 56) + images[16:]
    elif defect == 'length':
        images += b'\0'
    elif defect == 'count':
        labels = struct.pack('>II', 2049, 9999) + bytes(9999)
    elif defect == 'label':
        labels = labels[:-1] + bytes([10])
    (directory / 't10k-images-idx3-ubyte').write_bytes(images)
    (directory / 't10k-labels-idx1-ubyte').write_bytes(labels)
    return images, labels


def test_read_fixed_test_idx_keeps_bytes_and_reports_actual_sha(tmp_path):
    from wfb_ng.fl.mnist_data import load_mnist
    images, labels = write_test_idx(tmp_path)
    dataset = load_mnist(tmp_path, 'test', lambda: None)
    assert len(dataset.images) == len(dataset.labels) == 10000
    assert dataset.images.shape == (10000, 1, 28, 28)
    assert dataset.images.dtype == torch.uint8
    assert dataset.labels.dtype == torch.int64
    assert dataset.sha256 == {'t10k-images-idx3-ubyte': hashlib.sha256(images).hexdigest(),
                              't10k-labels-idx1-ubyte': hashlib.sha256(labels).hexdigest()}


@pytest.mark.parametrize('defect', ['magic', 'dimension', 'length', 'count', 'label'])
def test_invalid_idx_is_rejected(tmp_path, defect):
    from wfb_ng.fl.mnist_data import load_mnist
    write_test_idx(tmp_path, defect)
    with pytest.raises(FLRuntimeError):
        load_mnist(tmp_path, 'test', lambda: None)
