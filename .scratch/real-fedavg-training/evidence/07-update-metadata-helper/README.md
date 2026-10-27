# 07 前置：参考 update 有限元数据读取 helper

## 范围与公开接口

本次仅修改 `wfb_ng/fl/mnist.py` 新增公开 helper，并新增独立测试文件 `wfb_ng/tests/test_fl_mnist_update_metadata.py`。没有修改训练、聚合或评估入口的行为，也没有修改 Coordinator、Server Daemon、控制台、Runtime、FP64 audit、spec 或工单状态；没有提交。

```python
read_update_metadata(path, *, node_id, round_index,
                     base_model_sha256, expected_sample_count) -> dict
```

helper 复用 `_load_update` 的文件大小限制、CPU storage 及 `weights_only=True` 读取，并复用 `_validate_update` 的固定格式、完整参数、身份、轮序、基准摘要、样本数、步数、有限损失和设备检查。额外严格校验调用方传入的约束：客户端节点为 1～10 的整数，本地轮序为正整数且小于 `2**63`，基准摘要为 64 位小写十六进制，预期样本数为 1～60000 的整数。布尔值不作为整数接受。update 的实际样本数必须等于预期样本数。

返回恰有八个字段：

```python
{
    'model_format': 'mnist-two-conv-fp32-v1',
    'base_model_sha256': '<64位小写摘要>',
    'node_id': 2,
    'round_index': 3,
    'sample_count': 60000,
    'optimizer_steps': 938,
    'train_loss': 1.25,
    'device': {
        'type': 'cuda',
        'name': '<制品记录的设备名>',
        'torch_version': '<制品记录的版本>',
        'cuda_version': '<制品记录的版本>',
    },
}
```

不返回 `state_dict`、Tensor、模型对象、路径或任意附加字段。损失转为有限 float；设备描述继承每字段 1～128 字符的上限，并返回独立的小字典。读取及返回值的修改不会改写源文件。坏文件或违反身份／样本关联等约束时抛出 `FLRuntimeError`；模型张量结构错误沿用已有严格 state 校验的错误码，helper 不吞掉这些错误。超大损失整数导致的数值溢出转为明确的 `invalid_mnist_update`。

07 宿主稍后应只对明确部署的 MNIST 参考算法、实际通过提交／聚合校验的 update 调用此接口，使用已绑定的节点、轮序、实际基础模型摘要与已通过准备的样本数。helper 本身不会按算法名自动分发，也不解析未知算法格式。返回的是所读取制品的受约束事实，不独立证明 GPU 优化、训练效果或全局成功；例如 `device` 来自制品，不能单靠它推断 GPU 已实际执行。

## TDD 证据

- `red-public-helper.txt`／`.exitcode`：先写真实 torch 制品的公开接口测试，因 helper 尚不存在而失败，退出码 1。
- `red-constraints.txt`／`.exitcode`：成功读取切片通过后，增加非法预期约束和异常内容检查，出现 13 失败、20 通过，退出码 1。暴露了预期身份／摘要类型未限制以及超大损失整数溢出。
- `helper-tests.txt`／`.exitcode`：修复后 33 项全部通过，退出码 0。
- `affected-tests.txt`／`.exitcode`：相关参考计算、IDX、新 helper 和票 01 模型回归共 114 通过、3 跳过，退出码 0。跳过的是需显式启用的单轮、两轮、五轮完整 GPU 训练；此次未重新训练。
- `typecheck.txt`／`.exitcode`：三个计算实现与新测试文件的 mypy 检查通过，退出码 0。

测试使用独立 fixture 生成真实 `torch.save` 文件，通过公开 helper 检查，不依赖其他并行测试文件的 fixture。覆盖有限字典内容、JSON 字节预算、读取不改写、返回设备字典修改不影响后续读取，以及错误节点／轮序／基础摘要／样本数、错误优化器步数、NaN／Inf／负损失、超大损失整数、参数缺失／额外／形状／dtype／NaN／Inf、未知格式、额外字段、CPU 设备、超长设备名及损坏文件的拒绝。

## 复现命令与验证边界

```bash
OMP_NUM_THREADS=4 python -m pytest -q \
  wfb_ng/tests/test_fl_mnist_update_metadata.py --tb=short

OMP_NUM_THREADS=4 python -m pytest -q \
  wfb_ng/tests/test_fl_mnist.py \
  wfb_ng/tests/test_fl_mnist_data.py \
  wfb_ng/tests/test_fl_mnist_update_metadata.py \
  wfb_ng/tests/test_fl_reference_model.py --tb=short

python -m mypy --follow-imports=silent --check-untyped-defs \
  wfb_ng/fl/mnist.py wfb_ng/fl/mnist_data.py wfb_ng/fl/reference_model.py \
  wfb_ng/tests/test_fl_mnist_update_metadata.py
```

测试制品使用未训练的参考参数和明确标为 fixture 的 GPU／版本描述；合法格式测试不伪装成已训练模型。这里没有生产作业 update、官方数据训练、无线、宿主 round-done 集成或 Web 展示证据，也不代替 11 的实际输入 FP64 复核及 08／12／13 实机验收。
