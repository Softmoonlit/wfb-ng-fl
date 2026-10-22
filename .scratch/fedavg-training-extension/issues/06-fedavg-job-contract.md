Status: ready-for-human
Type: grilling
Labels: wayfinder:grilling
Parent: ../map.md
Assignee: unassigned
Blocked by: 01, 02, 04, 08

# 定义真实训练作业的算法接入与跨层契约

## Question

在既有 Stage 4 Web、守护进程、Coordinator、Runtime 与 Transport 基础上，如何接入已选定的 FedAvg 训练、聚合、MNIST 本地数据和无线配置，使各层拥有明确且唯一的职责？

决定 Web 作业字段与算法配置的传递、服务端聚合注入、客户端训练入口、初始与聚合模型身份、update 内容与样本权重、训练前数据检查、轮次间重配调用、异常／超时／急停语义。清理文件仿真的复制及固定大小假设，不以兼容层保留已废弃输入路径。使用各端预置数据，明确实际训练样本数与聚合权重；不新增服务器分区、区间配置或索引下发。

检查当前分支的算法入口与 Coordinator 所有权，避免照搬其他分支的历史描述。列出现行权威设计中需要显式修改的条目和唯一所有者；不将新业务状态或数据字段随意塞入 Runtime manifest，不重建通信协议。

形成足以编写实施规格的端到端调用与文件约定，但本票不编码或设计通用插件系统。指标呈现若仍有模糊需求，记录为新待决策问题，不自行扩成实验分析工具。

## Context

前置决策分别由以下票拥有：

- [确定真实 FedAvg 参考任务与效果判据](01-fedavg-reference-task.md)。
- [确定首版 MNIST 本地数据最小接入](02-client-dataset-library.md#answer)。
- [定义算法可调用的无线配置接口与轮次间重配边界](04-algorithm-radio-configuration.md)。
- [核实 Jetson GPU 训练环境与依赖约束](08-jetson-training-environment.md)。

## Comments

- 建图时创建，待全部前置决策结单后领取。
- 本地数据前置已收敛为 MNIST 最小接入：本票确定目录配置字段、唯一固定文件格式与加载适配，以及作业准备检查结果的有限消息承载和确认顺序，遵循数据接入票的 Answer。不得重新引入通用注册命令、数据集版本管理或清单浏览作为前置。
- 数据划分已按用户要求移出本地图，移除对应前置依赖；本票继续明确真实训练所需的本地数据与样本权重。
- 参考任务确定为 Jetson GPU 训练后，新增实际软件环境核实前置依赖；不能在目标环境未知时冻结 PyTorch 部署组合。
- 已发现权威文档冲突：`docs/design/FL-算法层设计.md` 的“算法作业与入口”仍写 server 与 client 算法入口分别拥有多轮循环，而 `CONTEXT.md` 与 `docs/design/系统分层与跨层契约.md` 明确由 FL Coordinator 拥有服务端多轮推进。此冲突影响服务端真实算法接入，本票须先向用户报告并明确裁决，受影响设计不得静默选边或直接实施。推荐沿现有 Coordinator 注入训练任务所需聚合／评估能力，并同步统一设计正文；这只是待裁决建议，不是已确认决策。
