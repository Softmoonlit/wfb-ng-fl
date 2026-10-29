# 四节点紧凑 MNIST FedAvg 验证

- 作业：`web-66ef4968e6514452971ee5d4d00a2352`
- Run：`web-bf6132e7174a48a1bd7d3b068fd0af98`
- 源码提交：`80294dff43996378c20e5e354201b63ffbaf587b`
- 发布取证：`../08-jetson-preflight/heartbeat-149/release-80294dff4399-20261029T100728194822Z/`
- 目标节点：`[1, 2, 4]`，Server 为 fl00
- 轮数：2
- 运行结果：`succeeded`
- 恢复结果：`ready`
- Server 最终状态：`IDLE`
- 三个客户端最终状态：`READY`
- `first_failure`：无；`additional_errors`：空；`dropped_out_nodes`：空

## 模型

- 格式：`mnist-cnn-32-64-fp32-v1`
- 参数：`50,186` 个 FP32 参数
- 初始模型大小：`203080` 字节
- 初始模型 SHA-256：`320d1daab3e3d978fd41e0eb69eb2f72aac099a245a9ee328c2842319e1e60e6`
- 第 1 轮模型 SHA-256：`f326105e39278afac04da2b5255024dedbf8381dbe42a839f3067ae78b127b8a`
- 第 2 轮模型 SHA-256：`5821b4d3bc5c716722e9e23d0278e653e5a45d0a0b10d104592366e8d1f4b144`
- 三个模型文件均为 `203080` 字节，并由 `mnist_cnn_model.load_model` 独立读取为 `50186` 参数的 `MNISTCompactCNN`。

## 结果

| 模型 | 测试 loss | 测试 accuracy |
|---|---:|---:|
| M0 | 2.3030500816 | 10.56% |
| M1 | 0.3523975176 | 89.39% |
| M2 | 0.1885523708 | 94.44% |

每轮均收到节点 `1/2/4` 的 update。每端每轮报告 `60000` 个样本、`938` 个优化步骤、`Orin CUDA`；三端训练数据摘要一致。Server 使用独立测试集，准备样本数为 `10000`，设备为 CPU。

## 发布与配置

- 四端冻结发布、ABI、Python/native 身份、安装后运行身份和新鲜 READY 检查均通过。
- 无线核验：149 / HT40+ / `-14.00 dBm`，下行 MCS3，上行 MCS4，UFTP 15000 Kbps。
- Server 当前显式入口：
  - `wfb_ng.fl.mnist_cnn_32_64:client_entry`
  - `wfb_ng.fl.mnist_cnn_32_64:aggregate_entry`
  - `wfb_ng.fl.mnist_cnn_32_64:evaluate_entry`
- 切换前配置备份保留在 fl00：`/etc/wfb-ng-fl/server.json.before-compact-20261029T100846Z`。

原始 Web 快照和 Coordinator 摘要、模型链文件保存在本目录；本次验证证明四节点 204KB 模型的下发、三端 CUDA 训练、update 上传、聚合、评估和恢复链路已跑通，不代表最终论文任务或数据分片方案已经确定。
