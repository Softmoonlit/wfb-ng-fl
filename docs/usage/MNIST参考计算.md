# MNIST 参考计算

本实现对应[参考任务决议](../../.scratch/fedavg-training-extension/issues/01-fedavg-reference-task.md#answer)，代码位于 `wfb_ng/fl/mnist_reference.py`。它提供真实模型计算函数，不拥有服务端多轮推进。Coordinator、客户端作业入口、准备屏障和 Web 的完整接入由[跨层契约票](../../.scratch/fedavg-training-extension/issues/06-fedavg-job-contract.md)继续实施；现行文件仿真作业不能通过选择这个模型文件自动变成真实训练。

## 固定计算配置

- 输入为官方 MNIST 28×28 灰度图片，转 FP32 后除以 255，训练和评估均采用 [0, 1] 范围，不做数据增强。
- 网络为 `Conv2d(1, 8, 3, padding=1)` → ReLU → 2×2 最大池化 → `Conv2d(8, 16, 3, padding=1)` → ReLU → 2×2 最大池化 → 展平 → `Linear(784, 10)`，输出 logits。没有 BatchNorm、Dropout 或预训练权重。
- 初始化使用指定种子的 Xavier uniform 权重和全零偏置。参数均为 FP32，固定格式为 `mnist-two-conv-fp32-v1`。
- 客户端使用 CUDA GPU，从每轮全局模型重新构造 SGD，无动量、无权重衰减，学习率 0.01。完整训练集训练一遍，batch size 64，保留最后 32 个样本，共 938 次优化器更新。
- 训练集每轮独立打乱，使用显式 `training_seed` 的 CPU 数据加载生成器。相同种子不保证 GPU 跨设备位级一致。
- 训练损失按训练期间每批更新前的交叉熵、按样本数平均；评估损失为固定测试集上的样本平均交叉熵。准确率使用 argmax 分类。
- 聚合在 CPU 用 FP64 累加各客户端完整参数的样本加权平均，结果转换为 FP32。文件记录更新摘要、样本数、权重及基准模型摘要，不使用优化器步数作为权重。
- 参考作业为 5 个全员同步轮次。初始及每轮全局模型均评估，末轮准确率须高于初始且损失低于初始；少轮计算可检查函数，不代替参考验收。

## 依赖和数据准备

包的 `mnist` 可选依赖声明 `torch>=2.4`。Jetson 必须使用与实际 JetPack／CUDA 匹配的 NVIDIA PyTorch 安装组合，先部署兼容包并检查 CUDA 运算，不能用通用 CPU wheel 满足 GPU 参考任务。实现直接使用 PyTorch 的张量、DataLoader、安全张量加载和 SGD，不需要 torchvision 或额外数据集框架。

在作业开始前解压并预置官方 IDX 文件。客户端本地目录包含 `train-images-idx3-ubyte` 和 `train-labels-idx1-ubyte`，服务端本地目录包含 `t10k-images-idx3-ubyte` 和 `t10k-labels-idx1-ubyte`。`load_mnist(data_dir, split, context)` 校验固定样本数、magic、维度、长度及标签范围，并返回数据和两文件摘要。训练和评估不会下载、复制或划分数据。所有客户端使用相同完整训练集，数据同一性仍须由未来准备屏障比较各端报告。

## 初始化及函数调用

生成未训练模型供现有模型库上传：

```bash
python3 -m wfb_ng.fl.mnist_reference /absolute/path/initial.pt --seed 7
```

命令输出整个模型文件的 SHA-256。生成器种子保存在模型中；训练使用的 `training_seed` 单独记录在 update 中。

计算函数与跨层目标契约一致：

```python
train(model_path, output_update_path, config, context) -> None
aggregate(current_model_path, updates_by_node, output_model_path, config, context) -> None
evaluate(model_path, config, context) -> evaluation
```

`config` 只允许 `training_seed`（默认 0）与 `evaluation_device`（默认 `cpu`，也可显式选择 `cuda:0`）。模型结构、优化器和预处理固定，不接受任意覆盖。客户端始终使用 `cuda:0`，CUDA 不可用或计算失败会报错。

宿主提供的 `context` 属性和能力：

| 计算函数 | 必需内容 |
| --- | --- |
| 全部 | `check_stop()`，抛出宿主停止／期限异常；计算不会吞掉该异常 |
| `train` | `node_id`、从 1 开始的 `round_index`、本地 `mnist_data_dir` |
| `aggregate` | `round_index`、完整固定的 `participant_node_ids`、准备阶段确认的 `sample_counts_by_node`（节点编号到实际样本数的映射，参考作业各端必须为 60,000） |
| `evaluate` | `round_index`（初始为 0）、`rounds`、本地 `mnist_data_dir`；末轮提供 `initial_metrics` |

聚合严格检查输入节点集合、update 内部身份、轮序、基准模型摘要、参数键、形状、FP32 类型、有限值、冻结训练种子和准备阶段确认的样本统计；失败不发布结果。训练完整读取 60,000 样本。聚合要求 update 的样本数等于 `context.sample_counts_by_node` 中该节点的准备结果，并要求步数为 `ceil(sample_count / 64)`。真实参考作业准备结果必须为每端 60,000 样本；决议要求的 1∶3 受控算术核验则显式提供 `{1: 1, 2: 3}` 作为预期统计，不能将该受控上下文用于真实参考作业。

模型和 update 均从同一字节快照通过 `torch.load(..., map_location='cpu', weights_only=True)` 加载和计算摘要，只保存张量和基本类型元数据。算法文件在加载前限制为 1 MiB；IDX 同样从实际计算快照取摘要。输出经临时文件、fsync、原子替换发布；训练和聚合拒绝覆盖输入模型／update，训练也保护本地 IDX 文件。模型和 update 的内容字段属于算法层，不修改 Runtime manifest。

评估返回 `metrics`、仅初始阶段的 `preparation`、仅五轮参考作业末轮确定的 `verdict` 和 `reason`。其他轮数的试跑仅记录指标并保留 `verdict=null`，不能判参考效果达标。准备结果包含数据摘要、样本数、模型格式、PyTorch／CUDA／cuDNN 版本及实际设备。模型链通过文件 SHA-256 及 `round_index` 校验，不解析 Runtime 的 `round_id`。执行结果、效果结果和资源恢复的展示、持久化及协调仍由宿主负责。

## 验证边界

`wfb_ng/tests/test_fl_mnist_reference.py` 是针对正式聚合函数的专项核验：已知参数 2 和 6、样本数 1 和 3 的结果应为 5；另外核验非法身份、统计、非有限值、结构、不完整集合、覆盖输入和停止请求的拒绝。它不建立模拟训练、namespace 或新的本机回归验收体系。

真实板端计算记录和环境事实见[本次参考计算报告](../reports/mnist-reference-compute-check.md)。单板计算不能证明多客户端全员同步、无线交付、全网准备或全网停止；完整验收仍遵守[验收边界](../../.scratch/fedavg-training-extension/issues/07-acceptance-and-handoff.md#answer)。
