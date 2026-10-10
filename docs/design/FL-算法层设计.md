# FL-算法层设计

## 所有权

本文档唯一拥有 FL 算法层的职责、代码入口、算法文件内容及计算结果契约。文件传输、Runtime 轮次状态和 manifest、跨层执行顺序、作业控制与无线事务分别由相应专题拥有。

以下真实训练契约是[真实 FedAvg 训练规划](../../.scratch/fedavg-training-extension/map.md)已确认的目标，不表示当前文件仿真代码已经实现真实训练、评估或在线重配。历史进程内入口决策的演进见 [ADR-0016](../adr/0016-真实训练由Coordinator推进并显式装配算法函数.md)。

## 算法作业与职责

一次 FL 算法作业包含一个或多个完整轮次。服务端的 FL Coordinator 唯一拥有模型发布、完整 update 收集和多轮推进，通过显式函数调用进入模型聚合、评估及无线配置策略。客户端算法入口拥有本地多轮循环，使用同一 ClientRuntime 等待模型、训练并提交 update。清理历史 server 算法入口中的重复循环，不保留第二套正式推进所有者。

模型与 update 的内容、张量和计算语义属于算法层，对 Runtime 与 Transport 保持不透明。算法只交付已完成写入的文件路径，不修改 Runtime 托管文件，也不把样本数、训练指标或算法配置塞入 Runtime manifest。下层文件成功不代表训练或聚合数值正确。

算法使用进程内函数调用，不增加算法进程、Runtime RPC、中间 Runner 或插件注册平台。客户端入口在 Client RoleService 内执行，服务端算法函数在 Daemon 内的作业执行上下文中由 Coordinator 调用；底层通信仍通过现有 Runtime 与 Transport。

## 显式装配与配置

服务器通过显式配置的算法 JSON 文件选择一组匹配代码：

| 字段 | 契约 |
| --- | --- |
| `client_entry` | 必填 `package.module:callable`，客户端完整作业入口 |
| `aggregate_entry` | 必填 `package.module:callable`，服务端聚合函数 |
| `evaluate_entry` | 必填 `package.module:callable`，服务端初始及每轮评估函数 |
| `radio_policy_entry` | 可选 `package.module:callable`，未配置表示不运行配置策略、沿用当前无线配置 |
| `algorithm_config` | 必填 JSON object，具体算法严格校验其语义；参考实现提供已冻结的 MNIST 配置 |

代码和依赖预先部署在执行端，服务器必须能导入服务端函数，客户端必须能导入客户端入口。不传输 Python 代码、不动态安装依赖、不通过导入副作用启动作业。操作者在集群空闲时切换配置；Server Daemon 正式受理时读取、验证并冻结本次内容与摘要，后续文件修改不改变活动作业。未实现代码热切换、算法菜单或通用配置编辑器。

Daemon 的算法配置文件位置是显式部署配置，不使用隐藏固定路径。各端通过本地 `mnist_data_dir` 配置自身数据目录；不把服务器目录当成客户端目录下发。准备、完整轮次和传输期限由服务器作业控制配置提供，属于[跨层契约](系统分层与跨层契约.md#期限停止和失败)，不混入模型参数或由算法自行放宽。

Client Daemon 从冻结的配置生成角色算法 JSON，并使用 `--algorithm`、`--algorithm-config` 显式传给 RoleService。算法 JSON 与角色基础设施 JSON 分开。模型、update 和 Runtime 四接口结果不经过命令行。入口位置、配置顶层类型和 callable 资格由宿主检查，算法配置内容由该算法检查。

## 进程内调用约定

真实训练路径使用以下调用形态，均为新增或演进目标；不保留旧二参数客户端入口作为兼容路径：

```python
client_entry(runtime, config, context) -> None
aggregate_entry(current_model_path, updates_by_node, output_model_path, config, context) -> None
evaluate_entry(model_path, config, context) -> evaluation
radio_policy_entry(config, context) -> None
```

`config` 是冻结的算法配置。`context` 绑定当前作业，提供只读的作业身份、节点身份（客户端）、总轮数、当前本地轮序／阶段、算法产物目录、必要本地配置和已取得的评估／训练摘要；输入数据不得被算法原地改写。它不是另一套运行器，也不持有任意底层对象。

- 客户端在完成数据、依赖与 GPU 检查后调用 `context.report_prepared(report)`；每次 `submit_update` 成功返回后、进入下一轮模型等待前调用 `context.report_round_complete(round_index)`。报告的身份、确认与门禁由跨层专题拥有，不是 Runtime API。
- 各函数通过 `context.check_stop()` 检查停止或期限到达，准备、批次训练、评估和聚合的合理计算边界都须检查。异常交给宿主形成结构化失败；不能吞掉停止信号后继续推进。
- 只有服务端无线策略上下文提供同步 `context.configure_radio(patch)`，其字段、门禁、结果和异常见[算法无线配置与跨层重配边界](系统分层与跨层契约.md#算法无线配置与跨层重配边界)。其他算法函数不能绕开指定调用位置操作无线参数。
- 聚合函数校验全部 update，向宿主指定的 `output_model_path` 原子写出完整下一轮模型，正常返回 `None`。不得覆盖当前模型或托管 update；异常不发布半成品。
- 评估函数在初始阶段先校验模型与本地测试数据，再计算指标，返回有限结构：`metrics`、初始阶段的 `preparation`、以及仅末轮确定的 `verdict` 和 `reason`。参考任务的 `metrics` 为有限数值 `loss` 与 `accuracy`；准确率范围为 0～1。初始／中间轮 `verdict` 为 `null`，末轮为布尔值；初始准备报告包含样本数、固定文件摘要、模型格式和实际评估设备等必要事实。末轮判据由算法拥有，Coordinator 记录和执行结果，不硬编码所有算法的效果阈值。
- 无线策略正常返回表示本次策略执行结束，既可不调整，也可完成一次合法同步配置调用。上下文提供初始／本轮指标、初始基线、当前生效配置和必要客户端摘要；回调异常使作业失败。执行前配置拒绝可由策略处理；已经开始跨节点协调后的失败必须终止作业。

客户端正常返回只证明其本地循环结束，不声明服务器聚合、效果或全局成功。客户端提前返回、报告的轮序不足、异常退出或配置检查失败均须由宿主如实报告。算法上下文不暴露 `round_id`、Transport、链路进程、网卡对象、READY 或 GRANT。

## 初始模型与算法文件

初始模型通过现有模型库上传、选择，以完整 SHA-256 引用。参考任务提供初始化工具，使用唯一固定的小型卷积结构和记录的随机种子生成未经训练的模型。模型库只管理不透明制品；初始格式和数值校验由参考算法在准备阶段完成。同一初始制品可重复用于不同作业，不能原地改写。

模型与 update 各为单个 PyTorch 文件，只保存 CPU 参数张量和必要的基本类型元数据，不保存可执行模型对象。首版参考模型参数采用 FP32，独立数值复核标准见[验收边界决议](../../.scratch/fedavg-training-extension/issues/07-acceptance-and-handoff.md#answer)。参考实现采用明确的格式标识和严格结构，模型至少包含 `model_format`、`state_dict`、初始化来源／种子等必要元数据；聚合产物沿用相同可加载模型结构并保留来源关联。客户端加载后将模型放到 GPU 执行计算。加载必须核对键、张量形状、dtype、有限数值与模型格式，不以 pickle 对象恢复任意类。

参考 update 固定包含：

| 内容 | 规则 |
| --- | --- |
| `model_format`、`state_dict` | 与固定模型匹配的本地训练后完整参数，CPU 张量 |
| `base_model_sha256` | 本轮实际读取的全局模型文件摘要 |
| `node_id`、`round_index` | 本机身份和从 1 开始的本地算法轮序；不是 Runtime `round_id` |
| `sample_count` | 本轮实际完成训练使用的样本数，正整数；参考任务为完整 60,000 样本一次 |
| `optimizer_steps` | 实际优化器更新步数，正整数，不能用它代替样本权重 |
| `train_loss` | 按实际样本统计的平均训练损失，必须有限 |
| `device` | 实际计算设备的有限描述，参考任务必须为 GPU |

聚合函数检查 update 的节点键集合、内部节点编号、轮序、基准模型摘要、参数结构及样本统计。任一不符即失败，不跳过 update 或复用旧结果。样本数和指标随同一文件接受完整性校验，不另建旁路提交协议。文件大小来自实际序列化结果；删除模板 update、固定大小复制和文件变化即训练成功的假设，保留各端共享的显式上行大小上限。

## MNIST 参考计算

参考任务及效果判据由[参考任务决议](../../.scratch/fedavg-training-extension/issues/01-fedavg-reference-task.md#answer)提供来源：两层卷积加分类层，输出 10 类；PyTorch GPU 本地训练，每轮完整数据 1 个 epoch、batch size 64、保留尾批次，SGD 学习率 0.01，无动量和权重衰减。实现须唯一固定并归档通道数、激活、初始化、图像归一化及普通分类损失，不增加运行时任意结构配置。记录加载随机种子和软件／设备信息，不承诺跨设备 GPU 位级一致。

各端直接读取 `mnist_data_dir` 下预先解压的官方 IDX 文件：客户端为 `train-images-idx3-ubyte` 与 `train-labels-idx1-ubyte`；服务器为 `t10k-images-idx3-ubyte` 与 `t10k-labels-idx1-ubyte`。检查 IDX 类型、维度、文件长度、图片／标签计数、28×28 灰度图与 0～9 标签，训练／测试分别为 60,000／10,000。复用现有文件摘要能力记录两份文件 SHA-256；所有客户端的对应内容必须一致，官方来源另由部署证据确认。统一训练／评估预处理，不复制入库、不运行时下载、不适配其他格式。作业期间操作者不得修改或删除数据。

客户端 GPU 不可用、必要操作失败或依赖不满足时明确失败，不降级 CPU；服务端聚合不强制 GPU，实际评估设备必须记录。PyTorch 的 Jetson 兼容安装组合由部署阶段确认，不虚构当前未取得的版本或性能。

FedAvg 为所有有效客户端完整模型参数的样本数加权平均，权重为 `sample_count / sum(sample_count)`，不能乘全局轮数、按步数计权或只处理部分集合。当前参考任务各端样本相同，仍保存实际计数和权重以供独立数值复核。

初始与每轮聚合后在服务端同一官方测试集记录平均损失和准确率，测试数据不参与训练或聚合。五轮参考任务的末轮准确率须高于初始值、损失低于初始值；不要求逐轮单调，不预设高准确率。非有限指标或未改善如实失败，保留已执行轮次和指标，不能事后修改判据或筛选成功运行。其他显式替换算法拥有自身检查与效果判断，不把该参考判据设成所有算法的基础设施不变量。

## Runtime 调用与完成边界

算法／Coordinator 通过四个本机文件接口完成交换：server 调用 `publish_model(model_path)` 和 `wait_for_updates()`；client 调用 `wait_for_model()` 和 `submit_update(update_path)`。算法不传入、覆盖或读取 `round_id` 作为轮次接口参数，不解析 HTTP/UFTP 或空口状态。

`wait_for_model` 成功后可以开始本地训练；`submit_update` 成功只表示本节点收到最终提交成功，不能声明全员收齐。`wait_for_updates` 成功提供的是下层已完整交付的路径映射；算法仍须校验其内容再聚合。传输或算法失败结束当前严格同步作业，不自动开新轮、换旧模型或返回部分成功。

相同 Runtime、Transport、接收器和任务链路跨轮复用。每轮结束只结束该轮文件操作；作业角色及资源的终止、末轮提交确认和恢复由[跨层执行序列](系统分层与跨层契约.md#常驻服务作业角色与执行序列)拥有。评估表、作业执行结果、效果结果和资源恢复事实分别投影，不把文件交付成功提升为算法或作业全局成功。
