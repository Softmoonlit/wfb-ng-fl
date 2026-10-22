Status: ready-for-human
Type: grilling
Labels: wayfinder:grilling
Parent: ../map.md
Assignee: unassigned
Blocked by: 01, 02, 04

# 定义真实训练作业的算法接入与跨层契约

## Question

在既有 Stage 4 Web、守护进程、Coordinator、Runtime 与 Transport 基础上，如何接入已选定的 FedAvg 训练、聚合、本地数据集和无线配置，使各层拥有明确且唯一的职责？

决定 Web 作业字段与算法配置的传递、服务端聚合注入、客户端训练入口、初始与聚合模型身份、update 内容与样本权重、训练前数据检查、轮次间重配调用、异常／超时／急停语义。清理文件仿真的复制及固定大小假设，不以兼容层保留已废弃输入路径。使用各端预置数据，明确实际训练样本数与聚合权重；不新增服务器分区、区间配置或索引下发。

检查当前分支的算法入口与 Coordinator 所有权，避免照搬其他分支的历史描述。列出现行权威设计中需要显式修改的条目和唯一所有者；不将新业务状态或数据字段随意塞入 Runtime manifest，不重建通信协议。

形成足以编写实施规格的端到端调用与文件约定，但本票不编码或设计通用插件系统。指标呈现若仍有模糊需求，记录为新待决策问题，不自行扩成实验分析工具。

## Context

前置决策分别由以下票拥有：

- [确定真实 FedAvg 参考任务与效果判据](01-fedavg-reference-task.md)。
- [定义客户端本地数据集库与服务器可见清单](02-client-dataset-library.md)。
- [定义算法可调用的无线配置接口与轮次间重配边界](04-algorithm-radio-configuration.md)。

## Comments

- 建图时创建，待全部前置决策结单后领取。
- 数据划分已按用户要求移出本地图，移除对应前置依赖；本票继续明确真实训练所需的本地数据与样本权重。
