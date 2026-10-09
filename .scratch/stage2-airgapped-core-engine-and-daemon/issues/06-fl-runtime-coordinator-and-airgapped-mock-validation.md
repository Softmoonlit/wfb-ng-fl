# 06: 联邦学习运行层三种协同范式调度器与无 SSH 端到端本地自治验证

**What to build:** 在联邦学习算法运行层交付 `FLCoordinator` 协同调度器（支持 `sync` 同步全员、`semi_async` 半异步配额与 `async` 异步随到随聚）；打通 Server Daemon 任务发布到各 Client Daemon 子进程沙箱动态派生 `RoleService` 执行；在运行时无 SSH 依赖的本机虚拟网络测试环境中，以多轮 40 MiB 双客户端 fixture 验证任务发布、客户端状态跃迁、协调器收齐判定与占位聚合、作业终态及全集群安全复位，断言零僵尸进程残留并生成结构化审计摘要及 SHA-256 完整性 manifest。软件测试通过 `MockServerRuntime` 注入 update，不覆盖 UFTP/HTTP 数据传输或 Client 算法执行；这些能力由后述 4 MiB 三机真实硬件基线覆盖。平台 1~10 节点容量仅通过配置与单元测试覆盖。

**当前测试拓扑：** 40 MiB 软件端到端测试在本机单进程内使用 1 个 Server 和 2 个虚拟 Client；三机真实硬件基线另行使用本机 vm0 Server、`vm1`（client1）和 `vm2`（client2），规模为 4 MiB。不得把软件 mock 或未提供的节点写成实体集群验收。

**Blocked by:** 05: 服务端常驻协同守护引擎、预置链路层槽位池、本地专用 REST IPC 与作业前置门禁

**Status:** resolved

- [x] 在联邦学习算法运行层交付 `FLCoordinator` 协同调度器，支持作业配置的 `sync`（同步全员等待门禁）、`semi_async`（半异步最小更新配额 `min_updates` 并标记掉队者）与 `async`（流水线随到随聚）三种模式。
- [x] 打通 Server Daemon 任务发布到各 Client Daemon 子进程沙箱动态派生 `RoleService` 执行。
- [x] 在运行时无 SSH 依赖的本机虚拟网络测试环境中，以多轮 40 MiB 双客户端 fixture 验证任务发布、客户端状态跃迁、协调器收齐判定与占位聚合、多轮轮次演进、作业终态及全集群安全复位待命。该测试使用 `MockServerRuntime` 注入 update，不声明 UFTP/HTTP 数据传输或 Client 算法闭环；1~10 节点容量仅通过配置与单元测试覆盖。
- [x] 在 4 MiB 三机真实硬件基线中验证任务广播唤醒、全局模型 UFTP 下发、两 Client 占位算法执行、HTTP PUT 并发上传、收齐判定与占位聚合、两轮演进、作业正常完成及全集群安全复位待命。
- [x] 验证带内状态审计、结构化协调摘要及 SHA-256 完整性 manifest 生成，断言全过程无僵尸进程残留、TUN 设备安全回收，系统无缝回到就绪状态。

## Comments

- 当前测试资源固定为本机 Server、`vm1`（client1）和 `vm2`（client2）；已移除不适用的实体集群验收表述，`1~10` 仍表示平台能力容量，不表示本阶段具备对应数量的测试节点。
- 实现交付并在 `wfb_ng/tests/test_fl_coordinator.py` 与 `wfb_ng/tests/test_fl_e2e_airgapped.py` 中完整验证三大范式（sync / semi_async / async）调度、多轮 SHA-256 跨轮连续性、掉队节点容错、主动中止安全复位、以及沙箱回收无残留。最终全量 168 个测试用例通过，Mypy 静态类型校验无告警。

## 三机真实硬件功能验收（2026-10-09）

- 验收提交：`a0c9284`；三端提交一致，vm1/vm2 工作树干净。
- 拓扑：本机 vm0 Server、vm1 client1、vm2 client2；信道 157，发射功率 12 dBm。
- 作业：`hw_sync_2round_4mib_a0c9284`，`sync` 全员模式，2 轮，每轮向两个客户端下发 4 MiB 确定性模型并收取两份 update。
- 结果：作业 `succeeded`，两轮均由 node 1/2 提交且无掉队；第 1 轮 6.351 秒，第 2 轮 7.641 秒。
- 连续性：第 2 轮输入 SHA-256 严格等于第 1 轮聚合输出 SHA-256，均为 `6d31405ed992d003e19bc26a027b64bbcf4d13b69582f6a0744f989540aa6c17`。
- 终态：Server 自动回到 `IDLE`，node 1/2 自动恢复 `IDLE/READY`；停止临时 systemd 单元后，三端无 daemon、RoleService、`wfb_v6_uplink`、UFTP 进程或 TUN 残留。
- 本次属于 4 MiB 真实射频、运行时无 SSH 依赖的端到端本地自治闭环，不替代 40 MiB 实体容量验收，也不代表真实训练或 FedAvg。完整协调摘要生成路径为 `/tmp/wfb-ng-fl/server/job_hw_sync_2round_4mib_a0c9284/coordinator_summary.json`；该 `/tmp` 路径不是持久归档位置。复现和判定方法见 [Stage 2 三机无 SSH 运行时自治验收指南](../../../docs/acceptance/stage2三机无SSH运行时自治验收指南.md)。
