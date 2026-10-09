Status: ready-for-agent
Type: map

# Stage 3 虚拟机管理下真实射频闭环实施路线

## Destination

在 vm0、vm1、vm2 三台 Linux VM 与 USB 直通真实无线网卡上，形成一组绑定提交、配置和 `run_id` 的正式证据，证明双 Client、两轮、40 MiB、`sync` 作业运行时无 SSH 依赖，并通过 SSH 受控故障注入验证射频两阶段切换的 15 秒租约回退。

## Canonical spec

[Stage 3 规格](spec.md)

## References

- [Stage 2 三机无 SSH 运行时自治验收指南](../../docs/acceptance/stage2三机无SSH运行时自治验收指南.md)：重要实施与诊断参考，但不是 Stage 3 基线、规格或通过依据；冲突时以 Stage 3 `spec.md` 和工单为准。

## Decisions so far

- 管理以太网与 SSH 保持可达；不声明 air-gapped 或管理网物理隔离。
- 不执行 VM reboot，不验收 daemon 开机自启。
- 正常作业观察窗口内 Stage 3 执行器禁止连接 vm1/vm2；SSH 只用于准备、指定故障注入和事后取证。
- 正常场景固定为双 Client、两轮、40 MiB、`sync` 和确定性占位算法。
- 射频场景固定为 157→149，在 Client 2 预置精确控制消息丢弃规则，验证 Server 与 Client 自动回退 157。
- Client 在终态清理前持久化白名单小型证据，不复制模型/update 本体。
- 工单 01 已完成：四种 lifecycle outcome 共用原子 evidence 归档，包内 build identity 绑定 commit 与资源摘要；ADR-0015 固化必选文件矩阵，归档失败保留沙箱且正常资源回收后恢复待命。全量回归 201 项与 Mypy 通过。
- 增加正式本地 `POST /api/v1/radio/reconfigure`，不增加 CLI。
- 工单 02 已完成：人工确认 REST 复用既有控制面事务，采用 FINALIZED/CONFIRMED 双向最终屏障；提交前异常回退、提交后确认失败进入 `RADIO_ERROR`，禁用自动对齐。扫频与重配共享状态互斥，生效配置与未知硬件状态显式报告；全量 234 项测试、四模块 Mypy 通过，完成双轴审查。
- Stage 3 直接复用 Stage 2 已实现的双 daemon、控制面、RoleService、Coordinator、Runtime、UFTP/HTTP 数据面、`TASK_READY` 和终态复位，不另建平行运行时。
- Issue #41 的硬件预检、独立构建安装、canonical fixture、SHA-256、资源核查和失败归档能力应直接调用或小范围提取；禁止无理由复制。
- 不复用 Issue #41 的 SSH RoleService/Runtime 作业编排、五分区 envelope 或旧 validator 结论语义；Stage 3 的独立性仅限于证据结构和裁决规则。
- 正式入口为 `tests/fl_runtime/stage3_vm_physical_loop.sh`。
- 工单 04 已完成：Stage 3 独立 runner、envelope、严格 validator 和无硬件回归测试已交付；正式入口严格串联七个执行阶段后只读 validate，复用边界已记录在 `tests/fl_runtime/stage3三机真实射频执行器.md`。全量 pytest 326 项、Stage 3 定向 91 项通过，Mypy 和 shell/Python 静态检查通过。实体三机验收待工单 06。
## Work graph

1. [01 Client 节点本地 evidence](issues/01-client-local-evidence-archive.md)
2. [02 射频重配 REST](issues/02-radio-reconfigure-rest.md)
3. [03 作业 I/O/观测参数贯通与终态一致性](issues/03-job-parameters-and-terminal-consistency.md)
4. [04 Stage 3 执行器、envelope 与 validator](issues/04-stage3-runner-envelope-validator.md)，依赖 01、02、03
5. [05 软件回归与双轴审查](issues/05-software-regression-and-review.md)，依赖 01、02、03、04
6. [06 三机正式硬件验收](issues/06-three-vm-formal-hardware-acceptance.md)，依赖 05，并等待 USB 无线网卡直通

## Out of scope

- 管理网物理隔离、拔线或真正 air-gapped 运行。
- VM reboot、systemd 开机自启或冷启动自治。
- 真实训练、FedAvg、评估或收敛。
- `semi_async`、`async` 或多于两个 Client 的真实硬件验收。
- Web、CLI、生产鉴权和历史归档浏览。
