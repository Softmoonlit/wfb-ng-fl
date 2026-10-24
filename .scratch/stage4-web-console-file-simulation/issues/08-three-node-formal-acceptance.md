# 08: 执行三机 40 MiB 控制面与数据面闭环验收

**What to build:** 让正式验收可以在本机 Server、`vm1`（Client 1）和 `vm2`（Client 2）上，从用户电脑经管理网 Web 上传 canonical 40 MiB 文件，再通过真实无线控制面和数据面完成固定同步轮次及 Web 急停场景，并生成可审计归档。

**Blocked by:** 07（建立 Stage 4 无硬件回归与机械归档）

**Status:** ready-for-human

- [x] 实体验收固定使用本机 Server、`vm1`（Client 1）和 `vm2`（Client 2）；动态发现并归档每台机器的 `wl*` 接口、节点身份、驱动、USB/xHCI、当前射频配置和提交信息。
- [ ] 从用户电脑经直连管理网 Web 完成模型上传和 SHA-256 校验；该阶段不绕过模型库、不复制 Server 本地路径到 Client。
- [x] 无线控制面真实完成节点发现、心跳、注册、当前作业 `TASK_READY`、轮次屏障、急停传播和终态待命链路恢复；作业窗口内不得通过 SSH 启动作业、复制文件、补发消息或恢复资源。
- [x] 无线数据面真实完成 UFTP 模型下发和 HTTP PUT update 上传；每个 update 的作业、轮次、节点、大小和 SHA-256 均通过校验，固定轮次完成并保持跨轮摘要连续性。
- [x] 正常多轮闭环和一个 Web 急停场景均验证执行结果、资源恢复状态、临时模型/update 清理和恢复前拒绝新作业。
- [x] 证据至少分为管理 Web、控制面、数据面、生命周期和汇总分区；控制面或数据面任一门禁失败都判定实体闭环失败。
- [x] 复用既有 Stage 2/3 射频和生命周期归档；只有运行时二进制、控制协议、数据面协议、射频逻辑或验收语义变化时才重新执行受影响的底层实体验收。

## Comments

- 2026-10-10 最终生产提交 `29da5ba`：正常两轮及传输中 Web 急停归档 `tests/logs/stage4-hardware-20261010T043817Z-47f7cc63/`，正式入口退出 0、离线校验通过。射频补充归档 `tests/logs/stage4-radio-20261010T043351Z-79b8aa83/` 及独立重判均通过：固定 12 dBm 的 MCS 变更、单独 13→12 dBm 功率、165 HT20→157 HT40+、实际 PID/TUN 重建、常驻 daemon 与控制 socket 恢复收发、受控失败与 session-bound 租约回退、后续两轮作业实际 `uftp -R 18000`。最终恢复 157/12 dBm/down3/up6/15000。全量软件回归 `609 passed, 1 skipped`；需要权限的真实内核 TUN/socket 回归另行执行通过，生产修复与执行器均完成审查。未验证用户电脑单网线直连拓扑，因此只留下上传验收项，状态为 `ready-for-human`，不声明整项 resolved。

- 2026-10-10 射频实测捕获 TUN 重建过早放行竞态：接口已存在而地址/路由尚未就绪，Server 广播 `NEW_CHANNEL_PING` 抛 E101。`bef6a65`/`29da5ba` 增加 UP、完整 CIDR、控制目的路由出口屏障；真实 network namespace 回归证明旧 socket 无需重绑定即可在屏障后继续发送。首次混合配置实测中的 Client2 裸 READY 丢失原因未确定，失败归档保留；最终完整补充验收已通过。

- 2026-10-10 现场复验先恢复 Stage 3 基线，再继续本工单。三端在 `stage4` 当前提交保持一致；Stage 3 已通过 preflight、install、start-services，真实作业同样在 `TASK_READY` 屏障超时（归档 `tests/logs/stage3_20261010_102343_0e37a57891b8/`）。正在以并发回归测试核验 Server 作业启动状态锁与 UDP 心跳回调之间的阻塞；修复前不声明 Stage 3 或 Stage 4 通过。早前 Client1 无法就绪还受到本机 Server 上误启动 Client daemon 的干扰，已停止该服务；现有证据不足以判定网卡硬件故障。

- 2026-10-10 Stage 3 基线已恢复并通过：`tests/logs/stage3_20261010_105422_df2452879ccb/`。Stage 4 正常两轮及真实 UFTP 运行期间 Web 急停已通过：`tests/logs/stage4-hardware-20261010T035439Z-40dd3c5d/`，执行提交 `90b7211`。独立归档校验 `valid=true`，正常四份 40 MiB update 与 canonical 模型 SHA 相同；正常 Client 生命周期均 `succeeded/0`，急停均 `aborted/0`。恢复中实测拒启为 `409 preflight_engine_conflict`，三端最终 `IDLE/READY`。本次 HTTP 请求由 Server 上的执行器访问明确管理 IP 发出，未验证用户电脑单网线直连拓扑；射频变更补充实体验收仍在执行，工单保持未完成。

- 工单 03 修复了射频事务中上下行 MCS 仅更新记录而未作用常驻链路的问题，并使后续任务读取已生效的功率／MCS 和 UFTP 速率。验收需覆盖 Web 配置后实际链路参数、MCS／165 信道频宽变更触发的 TUN 重建、既有控制 socket 在重建后继续收发、可回退失败恢复，以及后续作业使用确认后的速率；这些运行时变更不能仅以旧 Stage 2/3 射频归档代替。软件测试已覆盖进程命令、回退与实际 `uftp -R` 参数，但尚无本次硬件证据。
