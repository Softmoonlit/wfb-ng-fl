# 工单 05：五轮模型链与末轮收尾的软件证据

基线：`dee58ab`（工单 04 已交付）。Standards、Spec 双轴审查均已闭合。本目录随源码持久保存；测试生成的大型受控制品位于 pytest 沙箱，不属于正式训练交付制品。

## 已有能力与实际补齐

复用 04：Coordinator 唯一推进严格同步轮次；参考客户端每轮读取实际模型并绑定其摘要、本地轮序和节点身份；正式聚合拒绝不完整节点集合、陈旧/错轮 update；客户端仅在 submit_update 成功返回后报告本地完成；每轮及末轮均经过宿主报告门禁。原子模型/update、数据/GPU 检查及训练/评估判据沿用 04，不建立复制版算法或额外框架。

05 修复与补齐：

- Server 正常终态只回收任务角色，保留 Daemon 持久链路和最后确认的无线配置；重复终态不再次回收或重启链路。
- Client 保留当前/最近一次已受理 job_id 的有限 receipt，正常完成、终止或自然返回后迟到的相同 TASK_ANNOUNCE 不会再次启动；新 job_id 使用新上下文和制品目录。不维护无限历史身份集合；更早作业的重放身份规则交 06 明确。
- round_summary 增加 `updates`，每节点保存本轮 wait_for_updates 实际返回制品的绝对 `path`、`size_bytes`、`sha256`，可与已有输入/输出模型引用对应，不依赖 UUID 排序。不暴露 Runtime round_id，不新增索引或 manifest 业务字段。
- `client_submission_returns_received` 表示宿主已收齐客户端 submit_update 成功返回报告；不表示客户端已收到报告 ACK。不需要 ACK-of-ACK。屏障失败保留已完成计算轮数和模型，但执行结果为失败。
- 完整计算与全部提交返回报告均完成后，即使算法效果未改善，summary 保留 `execution_result=succeeded` 与 `effect_result.verdict=false`，全局 `status=failed`。宿主完整执行终态必须有明确 summary 执行事实；准备失败与停止分别裁决，不用缺失 summary 推断成功。
- 终态回收前显式 deepcopy 保存 summary、完成轮数和效果到 recent_job，不依赖之后的终态事件回填。

## 公开检查与结果

| 证据 | 结果和边界 |
| --- | --- |
| `final-changed-tests.txt` | 最新审查修复后：132 passed、3 skipped。五个直接修改的测试文件，覆盖 Coordinator、正式 MNIST 计算、Server/Client 宿主和真实私有报告通路。 |
| `affected-tests.txt` | 收紧 receipt/终态校验前的十文件适用合并检查：211 passed、3 skipped。含控制报告、算法上下文、客户端沙箱和已交付无线应用行为。之后改动通过上行最终五文件与 mypy 复验。 |
| `model-chain-tests.txt` | 69 passed、3 deselected。正式 FedAvg 参与五轮 M0→M5，受控样本权重 1:3，逐轮独立手算张量均值为 4/8/12/16/20；实际发布 M0→M4，M5 不额外广播。每轮 update 关联实际模型摘要，summary 能定位输入、全部 update 和输出，摘要与大小可核对。 |
| `gpu-five-round.txt` | 1 passed、52 deselected。`WFB_RUN_MNIST_GPU_TEST=1`，本机实际 GPU 运行正式客户端五轮，每轮完整 60,000 样本、938 次 SGD，逐轮核对实际加载模型摘要、轮序、样本数及完成报告。数据为受控 IDX，不是官方 MNIST 效果证据。 |
| `client-new-context.txt` | 3 passed。正常终态、终止、自然返回后的迟到启动不重做训练；新 job_id 启动新角色和执行上下文。最终五文件检查已覆盖有限 receipt 版本。 |
| `typecheck.txt` | 最新三个实现文件 `mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs` 通过。 |
| `red-*.txt` | 保留实际红灯：成功归档后重复启动、独立执行结果、recent 缺失 summary、缺少明确执行结果、缺少 update 身份引用。持久链路原先重启的红灯亦在实施中观察，最终直接终态检查已迁移为保留链路契约。 |
| `expanded-legacy-tests.txt` | 扩大检查保留的原始结果：222 passed、3 skipped、5 failed。失败全部位于旧 Stage3 文件，包含旧 issue41/Runtime fixture、缺失已有构造必填参数、预期终态重启链路和空控制面夹具。按“不恢复旧回归体系”范围未扩修；05 对应终态要求由新直接终态及公开 training_host 检查替代，其余必要 fixture 迁移交 06。 |

五轮宿主检查使用真实 start_job、控制报告解析、有限 receipt 和快照接口，受控 Runtime 只提供文件交换边界。验证 M0 初评及 M1～M5 评估、同一次 Runtime 装配和任务发布、全员末轮报告；单节点和其重复报告不能越过末轮屏障。客户端真实子进程通知五轮复用同一角色。无线链路、Transport、接收器、TUN 的实现所有权沿用 04；跨轮实物资源身份由 08/13 实机验证，不以受控宿主替代证明。

合并命令使用 `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python -m pytest -q`，适用文件为 `test_fl_coordinator.py`、`test_fl_mnist.py`、`test_fl_client_service.py`、`test_fl_client_daemon.py`、`test_fl_training_host.py`、`test_fl_server_daemon.py`、`test_fl_job_reports.py`、`test_fl_algorithm_context.py`、`test_fl_client_radio_application.py`、`test_fl_radio_runtime_application.py`。最终五文件为其中直接修改的 Coordinator/MNIST/ClientService/TrainingHost/ServerDaemon。

## 后续边界

- 06：准备/完整轮次/I/O 期限、失联、停止和恢复确认；复用最近受理身份，不新增本票之外的作业恢复机制。
- 07：训练摘要投影；10：受控无线策略接线。现有聚合返回 None，05 不建立算法 metadata 旁路，也不让通用 Coordinator 解析任意算法 payload。
- 08：部署持久 work_dir、官方数据来源及逐端环境、40 MHz 少轮和跨轮实物资源验证；本票不改变全局默认目录。
- 11：从 round_summary.updates 定位实际 update，检查 payload 样本数/权重并独立复算，不依赖目录排序。
- 13：三次四板五轮、实际模型改善、全部节点正常恢复与正式制品持久备份。本票未执行四板训练，不填官方数据效果、跨轮无线行为或恢复实机通过。
