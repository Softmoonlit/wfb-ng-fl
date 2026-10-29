# 紧凑 MNIST CNN FedAvg 验证模型

## 范围

新增一个显式的 `mnist-cnn-32-64-fp32-v1` 模型和对应 FedAvg 算法入口，用于比现有 9,098 参数参考模型更有代表性的 MNIST 流程与通信验证。该模型不是最终论文模型，也不替换现有默认入口。

## 固定契约

- 网络：`Conv2d(1,32,3)`、ReLU、2 倍最大池化、`Conv2d(32,64,3)`、ReLU、2 倍最大池化、`Linear(64*7*7,10)`。
- 参数：50,186 个 FP32 参数；seed 2026 生成的 PyTorch 制品约 203,080 字节。
- 模型格式：`mnist-cnn-32-64-fp32-v1`；只接受 CPU、FP32、稠密、有限参数和固定键形状。
- 算法入口：`wfb_ng.fl.mnist_cnn_32_64:{client_entry,aggregate_entry,evaluate_entry}`，三个入口必须成组使用。
- 旧 `mnist-two-conv-fp32-v1` 及其默认 `wfb_ng.fl.mnist:*` 入口继续保留，不能由新入口 fallback 或自动探测。
- 训练、聚合、评估、准备报告、update 校验沿用既有 MNIST 契约，只替换固定模型定义。
- Web 评估曲线识别两个已登记的 MNIST bundle。
- 不修改 UFTP、FEC、无线调度、最终论文任务、数据集来源或模型大小限制。

## 验收

- 新旧制品可以分别生成、读取和严格拒绝对方格式。
- 新入口执行 CPU 聚合接缝并保持样本加权。
- Server/Console 能识别新入口 bundle，默认配置仍使用旧 bundle。
- 控制台曲线可消费新 bundle 的快照。
- 类型检查、相关 Python 回归、Node 曲线测试、差异检查和双轴审查通过。

## 不在范围

不新增数据分片、论文训练轮数、绝对准确率门槛或实机部署；这些待最终论文任务确定后另立规格。

## Comments

2026-10-29：用户确认新增约 203KB 紧凑模型，保留 9KB 模型作为最小通信基线；MNIST 仅用于验证 FedAvg 流程。
