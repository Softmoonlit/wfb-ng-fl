# 06: 联邦学习运行层三种协同范式调度器与无 SSH 端到端本地自治验证

**What to build:** 在联邦学习算法运行层交付 `FLCoordinator` 协同调度器（支持 `sync` 同步全员、`semi_async` 半异步配额与 `async` 异步随到随聚）；打通 Server Daemon 任务发布到各 Client Daemon 子进程沙箱动态派生 `RoleService` 执行；在运行时无 SSH 依赖、使用本地虚拟网络接口对的测试环境下，模拟全生命周期多轮 40 MiB 双客户端并发仿真作业（当前测试拓扑为本机 Server + `vm1`/`vm2`；平台 1~10 节点容量仅通过配置与单元测试覆盖），验证任务发布、模型 UFTP 下发、本地训练进度推流、HTTP PUT 并发上传、收齐判定与 FedAvg 聚合、作业完成及全集群安全复位待命，断言零僵尸进程残留与审计日志完整性。

**当前测试拓扑：** 跨节点端到端运行固定为本机 Server、`vm1`（client1）和 `vm2`（client2）；不得将未提供的节点写入本阶段验收矩阵。

**Blocked by:** 05: 服务端常驻协同守护引擎、预置链路层槽位池、本地专用 REST IPC 与作业前置门禁

**Status:** resolved

- [x] 在联邦学习算法运行层交付 `FLCoordinator` 协同调度器，支持作业配置的 `sync`（同步全员等待门禁）、`semi_async`（半异步最小更新配额 `min_updates` 并标记掉队者）与 `async`（流水线随到随聚）三种模式。
- [x] 打通 Server Daemon 任务发布到各 Client Daemon 子进程沙箱动态派生 `RoleService` 执行。
- [x] 在运行时无 SSH 依赖、使用本地虚拟网络接口对的测试环境下，模拟全生命周期多轮 40 MiB 双客户端并发仿真作业（本机 Server + `vm1`/`vm2`），验证当前双客户端拓扑的任务发布、模型 UFTP 下发、本地训练进度推流、HTTP PUT 并发上传、收齐判定与 FedAvg 聚合、多轮轮次演进、作业正常完成及全集群安全复位待命。1~10 节点容量仅通过配置与单元测试覆盖。
- [x] 验证任务广播唤醒、40 MiB 全局模型 UFTP 下发、各客户端本地训练进度上报、40 MiB HTTP PUT 并发上传、收齐判定与 FedAvg 聚合、多轮轮次演进、作业正常完成及全集群安全复位待命。
- [x] 验证带内日志审计与不可篡改归档生成，断言全过程无僵尸进程残留、TUN 设备安全回收，系统无缝回到就绪状态。

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
- 本次属于 4 MiB 真实射频功能闭环，不替代 40 MiB 实体容量验收。完整协调摘要保留于 `/tmp/wfb-ng-fl/server/job_hw_sync_2round_4mib_a0c9284/coordinator_summary.json`。
