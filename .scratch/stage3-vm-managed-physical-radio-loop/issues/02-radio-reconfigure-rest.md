# 02: Server 人工确认射频重配 REST 入口

Type: task
Status: ready-for-agent
Blocked by: None

## What to build

在 `wfb-fl-server-daemon` 增加 `POST /api/v1/radio/reconfigure`，把本地人工确认请求映射到既有 `ControlPlaneServer.reconfigure_radio()`。接口只负责准入、状态互斥和结构化结果，不复制两阶段协议实现，也不暴露协议内部 timeout 调参。

## Acceptance criteria

- 请求严格要求 object body、`confirmed is True`、非空 `patch` 和合法 `target_nodes`。
- patch 复用现行射频参数白名单及严格校验，拒绝 Channel 161、未知字段和非法值。
- 仅 Server `IDLE` 且目标节点全部 `IDLE/READY` 时接受。
- 执行期间状态为 `SWITCHING_RADIO`，新作业、扫频和第二次重配返回 `409`。
- 把原先单向 `RADIO_SWITCH_FINALIZED` 改为 session-scoped 最终屏障：Client 收到 FINALIZED 提议后保持租约并发送 `RADIO_SWITCH_FINALIZED_ACK`；Server 全员收齐后才进入不可逆 committed 状态并重发 `RADIO_SWITCH_CONFIRMED`；Client 收到 CONFIRMED 后才解除租约并回复 `RADIO_SWITCH_CONFIRMED_ACK`。
- REST 只有收齐全员 CONFIRMED ACK 才返回 `finalized`。不可逆 commit 前 FINALIZED ACK 不齐可回退；commit 后 CONFIRMED ACK 不齐时 Server 保持新信道并进入 `RADIO_ERROR`，禁止错误回退或伪装成功。
- 同步结果显式映射为稳定 JSON，包含 `session_id`、`finalized`/`rolled_back`/`radio_error`、失败阶段、未响应节点和协议结束后的生效配置。
- 同时加固 `ControlPlaneServer.reconfigure_radio()` 的事务边界：外层 `try/finally` 始终清理 active session、ping/watchdog 临时状态；不可逆 commit 前的广播、信道/功率应用或 FINALIZED 提议异常，都尝试把 Server 回退到基准信道并广播 abort，由 Client 租约完成自愈；不可逆 commit 后异常则保持新信道并进入 `RADIO_ERROR`。
- 成功、已确认回退和异常恢复路径都在同一状态临界区同步 `ServerDaemon.radio_config` 与 `ControlPlaneServer.active_radio_config`；随后 `GET /api/v1/status` 必须报告协议生效配置而非请求 patch 或旧缓存。
- 预期协议失败或已确认回退后退出 `SWITCHING_RADIO` 并回到 `IDLE`；Server 硬件回退失败或状态无法确认时进入 fail-closed `RADIO_ERROR`，拒绝新作业、扫频和重配，禁止伪装成 `IDLE`。
- `SWITCHING_RADIO` 同时进入 REST survey handler 和 daemon `run_spectrum_survey()` 的互斥门禁，直接方法调用和并发 HTTP 扫频都必须失败。
- API 调用本身只代表人工确认，不引入自动扫频后切换或运行期自主跳频。
- 单元测试覆盖成功、PREPARE 失败、COMMIT 回退、Client 丢失全部 FINALIZED、FINALIZED 部分送达后 Server 出错、CONFIRMED/ACK 丢失、各广播阶段异常、Server 部分硬件应用异常、回退失败进入 `RADIO_ERROR`、非法请求、节点未就绪、并发重配、两层扫频互斥、配置同步和异常状态恢复。
- Server Daemon、控制面测试及 Mypy 通过。

## Comments
