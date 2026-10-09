# 项目文档导航

`docs/` 按设计、部署、验收、报告、决策、工程流程和第三方参考分类。现行资料入口如下；历史迁移规划、参数迁移矩阵、路线图和兼容页面已退役，历史内容通过 Git 追溯。

| 目录 | 入口与职责 |
| --- | --- |
| `design/` | [总设计入口](design/README.md)与六篇权威设计专题。 |
| `deployment/` | [ARMv8 集群操作手册](deployment/ARMv8集群部署与演示操作手册.md)和[部署与运行基准](deployment/部署与运行基准.md)，负责环境准备、部署与服务运行。 |
| `acceptance/` | [v6 硬件验收细则](acceptance/v6第一版正式验收标准细则.md)和 [Stage 2 三机无 SSH 运行时自治验收指南](acceptance/stage2三机无SSH运行时自治验收指南.md)，负责链路验收与 daemon/Runtime 自治闭环验收口径。 |
| `reports/` | 阶段实测报告，记录当时的实验配置、结果与证据。 |
| `adr/` | [架构决策索引](adr/README.md)，解释决策背景及历史状态。 |
| `agents/` | [领域文档读取约定](agents/domain.md)等工程协作流程。 |
| `reference/` | [UFTP 5.0 官方参考快照](reference/uftp-5.0/README.md)，保存第三方协议与命令原文。 |

## 部署与现场演示

首次搭建 ARMv8 多板集群或准备现场汇报，先读 **[ARMv8 集群部署与演示操作手册](deployment/ARMv8集群部署与演示操作手册.md)**。手册覆盖代码同步、环境装配、集群配置、SSH 互信、批量部署和下行／上行演示。

| 任务 | 入口 | 证据边界 |
| --- | --- | --- |
| ARMv8 集群部署与现场演示 | [ARMv8 操作手册](deployment/ARMv8集群部署与演示操作手册.md)、[演示脚本与指标工具](../tests/demo/README.md) | 展示真实无线文件传输与遥测；不能替代正式 Runtime 验收。 |
| 安装角色服务、配置算法入口、停服与清理 | [部署与运行基准](deployment/部署与运行基准.md) | 宿主机依赖和 systemd 服务生命周期。 |
| Stage 2 三机无 SSH 运行时自治验收 | [三机自治验收指南](acceptance/stage2三机无SSH运行时自治验收指南.md) | 验证任务发布、RoleService 自主派生、多轮 Runtime、双向数据面和终态复位；确定性 fixture 不代表真实训练或 FedAvg。 |
| v8 真实硬件 FL Runtime 闭环验收 | [Runtime 验收导航](../tests/fl_runtime/README.md)、[Issue #41 SSH 编排验收手册](../tests/fl_runtime/v8_issue41_SSH编排真实硬件FL闭环验收手册.md) | 验证正式算法入口、Runtime 四接口、文件契约和服务生命周期。 |
| v6 链路层硬件验收 | [v6 验收细则](acceptance/v6第一版正式验收标准细则.md)、[链路上行导航](../tests/link_uplink/README.md)、[无 SSH 手动上行手册](../tests/link_uplink/v6新底座无SSH手动上行演示手册.md) | 验证链路语义与 2A／2B 证据，不声明 Runtime 或算法作业完成。 |

## 理解与修改设计

从 **[联邦学习系统设计入口](design/README.md)** 查找问题的唯一所有者，再读取对应专题：

| 专题 | 负责内容 |
| --- | --- |
| [系统分层与跨层契约](design/系统分层与跨层契约.md) | 五层职责、完整上下行顺序、跨层完成边界与不变量。 |
| [FL 算法层设计](design/FL-算法层设计.md) | 训练、评估、聚合与算法调用边界。 |
| [FL Runtime 轮次与文件契约](design/FL-Runtime轮次与文件契约.md) | 四个本机接口、轮次、manifest、状态机与文件契约。 |
| [Transport Backend 传输契约](design/Transport-Backend传输契约.md) | UFTP、HTTP/TCP、文件传输完成与失败边界。 |
| [链路层空口调度与反压](design/链路层空口调度与反压.md) | TUN、READY/GRANT、发送权、反馈窗口、队列与反压。 |
| [无线物理层设计](design/无线物理层设计.md) | monitor、raw injection、广播与射频观测能力。 |

[CONTEXT.md](../CONTEXT.md) 定义全局术语和领域关系。设计入口与六篇专题共同拥有设计规则；设计正文包含已实现基线与已确认目标契约，不能直接当作当前实现清单。验收、部署、ADR 和源码分别承担证据、运行指导、决策背景与实现职责，不能补齐或覆盖设计集。

## 阶段实测报告

- [第一、二阶段传输与调度技术报告](reports/stage1-stage2-fl-airtime-throughput-and-scheduling-report.md)：记录大文件传输、射频配置与多客户端调度的阶段结果。
- [无线传输攻坚与六节点调度优化实测汇报](reports/联邦学习无线传输攻坚与六节点调度优化实测汇报.md)：汇总阶段问题、优化方案和验收归档记录。

报告保留当时的配置、工具路径和测试规模，作为阶段实验记录阅读；当前部署与验收入口以本页导航为准，报告不覆盖现行设计契约。

## 决策、工程流程与参考资料

- [完整 ADR 索引](adr/README.md)：查决策背景、历史状态、部分替代关系及目标设计与当前实现的区别。
- [领域文档读取约定](agents/domain.md)、[本地问题追踪器](agents/issue-tracker.md)、[分流与完成状态](agents/triage-labels.md)：工程协作流程。
- [UFTP 5.0 官方参考快照](reference/uftp-5.0/README.md)：第三方协议与命令原文。
- [仓库测试导航](../tests/README.md)：按演示、Runtime 与链路上行目的选择入口。

## 历史资料追溯

退役页面不再作为现行依赖或兼容入口保留。需要查看删除前的内容时，在仓库根目录按历史路径查询，例如：

```bash
git log --all -- docs/v5迁移基线规划.md
git show <删除前提交>:docs/v5迁移基线规划.md
```

ADR 中保留的历史决策只能结合其状态和当时版本理解；当前运行路径以操作手册、验收入口和实际证据为准。
