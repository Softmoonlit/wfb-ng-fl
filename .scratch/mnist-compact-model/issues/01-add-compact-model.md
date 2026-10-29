# 新增紧凑 MNIST 模型

Status: resolved
Type: task

## 目标

实现 `mnist-cnn-32-64-fp32-v1` 固定模型和显式 FedAvg 入口，保留旧 9k 模型和默认部署行为。

## 验证记录

- 模型制品、入口聚合和旧回归已执行通过；双轴审查无阻断。
- 未引入未来论文配置、入口 fallback 或默认配置变更。

## Answer

新增 `mnist-cnn-32-64-fp32-v1` 及独立 `wfb_ng.fl.mnist_cnn_32_64` 三入口；旧 9k 默认模型保留。Server 对已知 MNIST bundle 强制三入口成组，Console 和评估曲线识别新 bundle。seed 2026 制品为 50,186 个 FP32 参数、203,080 字节。

验证：相关 Python 回归 199 passed、3 skipped，8 个模块 mypy 通过，Node 曲线测试和 `setup.py` 编译通过，双轴审查无阻断。未部署实机；最终论文模型仍未确定。

## Comments

2026-10-29：用户同意新增约 203KB 模型，最终论文训练任务仍未确定。

2026-10-29：80294df 已统一发布到 fl00/fl01/fl02/fl04；四节点 `[1,2,4]` 两轮真实 GPU FedAvg 成功，模型格式、三端 CUDA、聚合、评估和 recovery 均通过。持久证据见 `.scratch/real-fedavg-training/evidence/compact-model-four-node/web-66ef4968e6514452971ee5d4d00a2352/README.md`。
