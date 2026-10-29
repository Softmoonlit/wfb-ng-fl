# 工单 12 两客户端阶段性诊断

日期：2026-10-29

## 范围

因 fl04 在 HT20 长流中持续产生大量 NAK 并导致整组 UFTP 超时，本次暂时停止 fl04 常驻服务，仅使用 fl01、fl02 做阶段性诊断。用户已明确授权当前09/12/13使用固定目标[1,2]。该记录不替代工单12在该集合上的完整故障／页面矩阵，也不替代工单13规定的双带宽三次五轮。

## 两客户端真实作业

- 入口：管理网页 `/api/v1/jobs`
- 作业：`web-82816dafbc9d44d6b859dc428b3e5547`
- 运行标识：`web-23c1f5392a2849cdb999b277e2bae625`
- 目标：`[1, 2]`
- 轮数：2
- 模型 SHA-256：`b1a1c1734bec69acd26df1ed6c6aaafecd0b7a6b5b3c2f9b594043d7fca453d1`
- 运行射频：149 / HT40+ / 14档；下行 MCS3、上行 MCS4；控制帧 MCS0

两客户端均完成两轮 CUDA Orin 训练。每端每轮使用 60,000 个样本和 938 个优化步；两轮提交、聚合、评估及 recovery ACK 均通过。独立作业状态记录的准确率为：

```text
初始 8.91% -> 第1轮 87.47% -> 第2轮 93.93%
```

最终状态为 `execution_result=succeeded`、`recovery_state=ready`；Server 为 IDLE，fl01/fl02 为 READY，`recovery_missing_nodes=[]`。完整状态快照保存在本目录的 `web-status-000.json`；启动请求和响应分别为 `start-request.json`、`web-start-response.json`。

## 边界

该作业证明两客户端子集的真实 GPU 训练、聚合、管理HTTP启动和恢复链路可工作。它未覆盖真实浏览器页面交互、完整故障矩阵和双带宽验收，也未消除fl04的接收／反馈故障。09速率表、12完整检查及13双带宽验收仍未完成；后续三次HT40五轮及30份update复核见[阶段记录](../13-two-client-three-runs-20261029/README.md)，该结果不推断三客户端容量。
