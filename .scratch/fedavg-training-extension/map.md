Status: resolved
Type: map
Labels: wayfinder:map

# Stage 4 基础上的真实 FedAvg 训练规划

## Destination

收敛 Stage 4 基础上的真实 FedAvg 路线，明确数据、本地训练、加权聚合、多轮推进、算法无线配置及在线客户端 Web 展示的目标契约、验收标准和实施顺序。各端预置本地训练数据，不实现服务器指定数据划分。

用户最终确认以决策收敛为本次终点，收尾地图，暂不生成正式 `spec.md` 或实施任务；本地图不是已交付的实施规格。产品编码、部署与最终真实硬件验收仍属于后续工作。Jetson 实机环境检查和 20 MHz 速率标定已移交部署阶段，不阻塞本次决策收尾；对应票结单不代表已完成实测。实际启用 20 MHz 配置前仍须完成标定并补齐组合速率表，真实训练仍须通过环境检查与硬件验收。

## Notes

- 本地图为新工作，不重开或改写 Stage 4 文件仿真工作的已关闭决策；既有范围限制若需改变，由本工作显式确定对应的设计演进。
- 为优先完成真实 FedAvg 流程，用户决定将服务器指定数据划分整体移出本地图。[定义服务器按样本索引区间分配数据的契约](issues/03-sample-range-assignment.md)按范围关闭，不作为训练接入或验收的前置条件。待真实 FedAvg 能稳定运行后，再通过独立工作重新评审。
- 第一版完成一个明确的真实 FedAvg 参考任务，保留训练和聚合调用入口；不先建立通用算法插件注册、动态安装或多算法平台。
- 无线配置的权限、调用及重配边界由[定义算法可调用的无线配置接口与轮次间重配边界](issues/04-algorithm-radio-configuration.md#answer)拥有。
- 数据接入的最小范围由[确定首版 MNIST 本地数据最小接入](issues/02-client-dataset-library.md#answer)拥有，具体作业接入由训练作业契约票落实。不设计服务器端分区规则、区间配置或索引下发；FedAvg 所需的样本权重须与实际使用的本地数据一致。
- Web 在线列表、目标选择与失联作业呈现由[定义 Web 在线节点展示与失联作业呈现](issues/05-online-client-view.md#answer)拥有。
- 建图阶段只记录待决策问题，不将建议写成已验证能力；`Decisions so far` 只索引后续正式解决的决策票，首轮范围共识保留在本节。
- 每次只处理一个非 research 决策票；领取时在票中填写 `Assignee:` 并记录领取信息。只有 `Blocked by:` 中所有前置票均为 `resolved` 才可进入该票。
- 决策会话使用 grilling 与 domain-modeling；设计接口时按需使用 codebase-design，需要具体交互样稿时按需使用 prototype。
- 确认的跨层规则已写入唯一设计所有者，术语由根目录 CONTEXT.md 拥有，不让地图替代正式设计。用户在最终验收决策后明确暂缓生成 `spec.md` 与实施任务；此前的规格与任务交接计划按此调整，仅保留已确认的实施顺序，后续按用户指示再开展。
- 验证以受影响的当前硬件入口与具体能力为准，不恢复已停止维护的本机自动回归和 namespace 隔离验收体系。

## Foundation baseline

- 建图时工作区位于 `stage4`，HEAD 为 `b3bd0da215d223c2146b66a888430b3205cbb635`。
- 既有实现规格为[Stage 4 Web 控制台与同步文件仿真](../stage4-web-console-file-simulation/spec.md)，其真实训练、聚合、数据集管理范围尚未实现，不能以文件闭环作为真实 FedAvg 证据。
- 模型库由服务器持有；客户端接收模型并保留作业临时副本，不能据此推断各端已有持久模型库或数据集库。源码入口为 `wfb_ng/fl/model_library.py`、`runtime.py`、`transport.py`。
- Web 作业当前只接受模型摘要、目标节点、轮数，并固定文件仿真算法。RoleService 已有 callable 加载入口，Coordinator 已有聚合注入位置，但两者不等同于完整真实训练接入。源码入口为 `console.py`、`server_daemon.py`、`service.py`、`coordinator.py`。
- 射频重配当前只允许空闲集群；轮次之间重配需要扩展正式运行边界。源码入口为 `console.py`、`server_daemon.py`、`control.py`、`radio.py`。
- 当前节点快照区分在线与就绪，页面仍渲染全部节点。在线展示应复用服务器事实，不在浏览器另造心跳判定。源码入口为 `console.py`、`static/console.js`。
- 当前尚无数据集库或训练数据分区实现。本地图只建设 MNIST 参考任务所需的数据加载、就绪检查和样本统计；通用数据集库与数据划分协议均不在本次范围。
- 所有上述事实为源码静态检查结果，不能代替真实训练及新增能力的硬件验证。读取基线时应重新检查 HEAD，避免将历史行号作为当前实现事实。

## Decisions so far

- [确定真实 FedAvg 参考任务与效果判据](issues/01-fedavg-reference-task.md#answer)：采用 Jetson Orin Nano 上的 PyTorch GPU／MNIST 小型卷积模型，执行五轮同步 FedAvg，以真实训练、加权聚合、跨轮模型关联和整体效果改善分别验证。
- [确定首版 MNIST 本地数据最小接入](issues/02-client-dataset-library.md#answer)：采用本地目录配置、固定 MNIST 文件检查与作业准备确认，满足参考任务即可，不展开通用数据集库。
- [定义算法可调用的无线配置接口与轮次间重配边界](issues/04-algorithm-radio-configuration.md#answer)：算法通过作业绑定同步调用调整统一发射配置，信道由操作者固定；轮次间屏障后在线更新，保留任务资源并明确失败与停止作业边界。
- [定义 Web 在线节点展示与失联作业呈现](issues/05-online-client-view.md#answer)：在线候选显式勾选，状态变化不自动加入目标；固定作业参与者的失联事实独立保留，管理连接异常不冒充无线离线。
- [核实 Jetson GPU 训练环境与依赖约束](issues/08-jetson-training-environment.md#answer)：以用户确认的目标能力继续设计，实机环境与 GPU 训练检查后移至部署阶段，结单不代表已实测。
- [标定 20 MHz 下行注入速率范围与推荐值](issues/09-20mhz-uftp-rate-calibration.md#answer)：确定标定方法与交付责任后继续设计和规划交接，实测表在部署时、实际启用 20 MHz 前补齐，未填入未经验证的数值。
- [定义真实训练作业的算法接入与跨层契约](issues/06-fedavg-job-contract.md#answer)：Coordinator 唯一推进服务端轮次，显式装配训练／聚合／评估及可选无线策略，统一准备报告、算法文件、期限、失联与最小结果快照，并同步正式设计。
- [冻结端到端验收边界与实施交接条件](issues/07-acceptance-and-handoff.md#answer)：冻结三次五轮组合、独立聚合复核、故障与 Web 边界、证据保留和实施顺序；按用户指示仅收尾决策，暂不生成规格或实施任务。

## Not yet specified

无。地图内决策均已解决，范围关闭项保留在 Out of scope，没有开放子票或可领取 frontier。验收与实施顺序由[冻结端到端验收边界与实施交接条件](issues/07-acceptance-and-handoff.md#answer)记录；规格和实施任务是用户明确暂缓的产物，不是未决定的问题，也不新建占位票。

本次收尾不表示实现或实测完成；环境、期限数值、速率标定及最终训练证据仍由后续部署和验收取得。

## Out of scope

- 客户端与客户端之间通信，包括直接无线互传、服务器中转和相应 HTTP 文件接口。
- [定义服务器按样本索引区间分配数据的契约](issues/03-sample-range-assignment.md)：为优先完成 FedAvg 流程，暂时关闭；服务器指定区间、索引下发及 IID／非 IID 划分均不在本地图范围。待 FedAvg 稳定后另开工作评审，不放入本地图的待明确区域。
- 通用数据集库的详细设计，包括多数据集注册管理、通用身份／版本体系和目录摘要机制；待真实 FedAvg 稳定后另开工作评审，首版只保留 MNIST 参考任务必需的数据接入、检查与样本统计。
- 数据集通过 Web 集中上传、服务器向客户端分发实际样本、运行时在线下载公共数据集。
- 通用插件市场、动态代码与依赖安装、任意训练框架自动适配。
- 半异步、异步聚合、运行中动态缩减参与者及断点续训。
- 传输进行中的射频切换、自动选信道，以及无明确调用的自主调参。
- 本次建图中的产品代码实现与最终真实硬件验收执行；不排除已明确登记、仅为决策取证的前置任务。
