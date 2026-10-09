# 07: 修复作业终态链路重启后无法恢复 IDLE/READY

Type: task
Status: ready-for-agent
Blocked by: 05

## Problem Statement

Stage 3 正式三机两轮 40 MiB 作业已多次完成，但终态资源复位后仍可能无法在 20 秒内从 Server REST 观察到两 Client `IDLE/READY`。最近一次在控制广播已绑定 TUN 的提交 `23c08af6b59300b0b1a59e7566cc17642234b1ca` 上复现，因此不能将问题归因于旧广播经管理网的旁路，也不能放宽等待时间或反复重启来获得通过。

## Evidence

- `tests/logs/stage3_20261009_175625_b5de8bffbe5f/`：目标提交 `23c08af`，preflight/install/start-services 通过，Coordinator 两轮 succeeded，run-sync 在 wait_ready(20) 失败；三端失败清理核查均 clean，无进程或 TUN 残留。
- `tests/logs/stage3_20261009_172803_29b31eab95a7/`：两轮分别 52.280 秒、55.002 秒，两个 Client 都在自然退出后恢复 idle link；Server 重启持久链路、广播 JOB_COMPLETED 后 Client 又重启一次 idle link，最终恢复门禁失败。
- `tests/logs/stage3_20261009_173418_4d7f1cee9af0/`：一次作业复位通过；后续射频场景因旧控制广播未绑定 TUN、故障规则被绕过而失败。该归档不构成通过证据。
- 前两次的 Server 沙箱已原样移入相应失败归档的 `server-failure/`，后一次失败的现场仍在 `/tmp/wfb-ng-fl/server/job_stage3_20261009_175625_b5de8bffbe5f_sync{,_role}`，下一轮前须保留后移出预检沙箱路径。

## Investigation

只读审查发现以下可证伪的机制，尚不可作为已确认根因：

- trusted_plaintext TX 每次启动 block_idx 从 0 开始，不发送 session packet；RX 以 source 的 last_known_block 静默拒绝旧 DATA。Client 自然退出和 JOB_COMPLETED 双重重启可能使新 Server RX 先看到第一次 idle link 的高水位，再丢弃第二次 idle link 从 0 开始的数据。
- Server TokenScheduler sequence 重启归零，未同步重置的 Client 会拒绝旧 GRANT。
- registry 超过 10 秒未收到有效心跳会推导 OFFLINE；随后直接 IDLE 可能落入 CONNECTING，Client 收到 ACK 却不重新 HUNTING。
- `wait_ready()` 失败时不保留最后 REST 状态，现有 `job-latest-status.json` 仅覆盖 Coordinator 终态前，不足以判定这 20 秒的心跳因果链。

## Acceptance criteria

- 用本地 REST 时间线、三端 idle `wfb_uplink.log` 和必要的有针对性诊断明确根因，区分 DATA 高水位、GRANT 门禁和 registry READY 推导。
- 更新 Stage 3 canonical spec 中需要明确的会话/终态契约后再实现；继续复用现有双 daemon、RoleService 和控制面，不增加平行运行时。
- 修复重启会话或生命周期所有权机制；禁止固定 sleep、扩大验收 timeout、人工补发成功或 SSH 协助正常作业复位。
- 在正确调用边界补充可红可绿的回归测试，并完成软件回归与独立审查。
- 三端同提交、干净工作树，用新 run/job ID 完整执行工单 06 的唯一正式入口；正常作业恢复及射频租约回退均须通过。

## Comments

2026-10-09：工单 06 的后续正式验收被此问题阻塞。磁盘已通过缓存清理和旧归档无损压缩恢复空间；不需要换网卡。

### 2026-10-09 诊断与软件修复

- 已将原服务端失败沙箱无损移出预检路径，并保存三端空闲 `wfb_uplink.log` 到 `tests/logs/stage3_issue07_diagnosis_20261009/`，操作记录在该诊断目录的 orchestration log；它属于诊断证据，不构成正式通过归档。
- 最新失败的 Client 1 空闲底座先接受旧 Server GRANT `sequence=760`，随后最后一组 `GRANT_FILTER` 为 `71:1:0:47:0:23:0`，包含 23 次 stale 拒绝。Server 重启后序号从零开始，Client 序号水位未同步复位，是已取得实际日志支持的根因。
- 通过真实 `ControlPlaneServer.handle_datagram()` 入口、可控 monotonic 时钟稳定复现另一条恢复阻塞：已 READY/ACTIVE 节点静默 11 秒后连续发送新鲜 IDLE，每次收到 ACK 却一直 CONNECTING；OFFLINE 投影同时丢弃 timestamp，使旧 HUNTING/RUNNING 能刷新活性。完成握手的身份现已与 OFFLINE 活性分离，投影完整保留原字段；陌生直接 IDLE 不放行。
- 现有 Client daemon 所有权回归实际复现自然退出先恢复空闲链路、终态回调再启动第三条链路。修复为复用已健康的空闲链路，重复/旧作业终态消息保持幂等。
- canonical spec 新增 3.1 终态会话与重新就绪契约；执行器补齐逐样本 REST 时间线、最后状态、Coordinator 成功与终态恢复的独立记录，并在失败清理保存三端空闲日志。正常终态恢复仍为 20 秒，不扩大 timeout。
- 首轮 Python 全量 372 项通过；独立审查发现缺失/无效 timestamp 可绕过新鲜度检查，已追加生产 datagram 回归并严格校验必填正整数，相关 93 项通过。
- 最终 Python 全量 374 项通过，Python 独立复审通过。C++ 新增跟踪源码回归与 `make test_session_recovery` 入口，真实原 HEAD TX/RX 恢复断言已复现失败；当前实现验证 DATA 启动会话、source/FEC 隔离、退役包拒绝、GRANT → IPC → 授权及独立 scheduler/READY 入口。`make build_v6 all_bin` 通过。
- C++ 首轮独立审查发现过期 IPC 新会话会错误退役当前授权、重启 nonce 复用导致 unique 漏计、会话拒绝计数未进入日志，均已补回归修复。最终 C++ 独立复审通过，新增真实 libsodium keypair/session-key/DATA/FEC 加密往返与认证篡改拒绝测试，已纳入同一 make 回归入口并通过。三机正式验收待执行，暂不标记 resolved。
