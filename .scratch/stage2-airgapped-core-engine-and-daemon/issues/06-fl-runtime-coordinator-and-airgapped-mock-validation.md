# 06: 联邦学习运行层三种协同范式调度器与无 SSH 端到端本地自治验证

**What to build:** 在联邦学习算法运行层交付 `FLCoordinator` 协同调度器（支持 `sync` 同步全员、`semi_async` 半异步配额与 `async` 异步随到随聚）；打通 Server Daemon 任务发布到各 Client Daemon 子进程沙箱动态派生 `RoleService` 执行；在无 SSH、本地虚拟网络接口对环境下，模拟全生命周期多轮 40 MiB 双客户端并发仿真作业，验证任务发布、模型 UFTP 下发、本地训练进度推流、HTTP PUT 并发上传、收齐判定与 FedAvg 聚合、作业完成及全集群安全复位待命，断言零僵尸进程残留与审计日志完整性。

**Blocked by:** 05: 服务端常驻协同守护引擎、预置链路层槽位池、本地专用 REST IPC 与作业前置门禁

**Status:** ready-for-agent

- [ ] 在联邦学习算法运行层交付 `FLCoordinator` 协同调度器，支持作业配置的 `sync`（同步全员等待门禁）、`semi_async`（半异步最小更新配额 `min_updates` 并标记掉队者）与 `async`（流水线随到随聚）三种模式。
- [ ] 打通 Server Daemon 任务发布到各 Client Daemon 子进程沙箱动态派生 `RoleService` 执行。
- [ ] 在无 SSH、本地虚拟网络接口对环境下，模拟全生命周期多轮 40 MiB 双客户端并发仿真作业。
- [ ] 验证任务广播唤醒、40 MiB 全局模型 UFTP 下发、各客户端本地训练进度上报、40 MiB HTTP PUT 并发上传、收齐判定与 FedAvg 聚合、多轮轮次演进、作业正常完成及全集群安全复位待命。
- [ ] 验证带内日志审计与不可篡改归档生成，断言全过程无僵尸进程残留、TUN 设备安全回收，系统无缝回到就绪状态。
