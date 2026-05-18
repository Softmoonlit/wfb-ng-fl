# Issue #12: 实现 sender 侧 Token 授权状态机

- 原始链接: https://github.com/Softmoonlit/wfb-ng/issues/12
- 状态: 已完成
- 标签: needs-triage
- 创建时间: 2026-05-07T14:06:23Z
- 更新时间: 2026-05-07T14:06:23Z

---

## Parent

https://github.com/Softmoonlit/wfb-ng/issues/6

## What to build

Client 发送进程维护本地 Token 授权状态和 token_expire_time_ms。收到有效授权事件后进入可发送状态；授权过期、没有 Token、IPC 不可用或状态无效时默认禁止发送。

## TDD

已完成的测试：
- [x] 无 Token 时发送状态为禁止
- [x] 有效 Token 事件使状态变为允许
- [x] token_expire_time_ms 到期后状态自动失效
- [x] 新 Token 能延长或替换授权窗口
- [x] IPC 不可用或事件非法时默认禁止
- [x] sender 授权事件到达后才允许门控发送
- [x] data_source 在未授权 / 已授权 / 过期场景下正确门控发送

## Acceptance criteria

- [x] sender 拥有本地授权状态，不依赖发送前远程查询
- [x] sender 在每次判断时使用 token_expire_time_ms 校验过期
- [x] 未授权、过期或异常状态均 fail-closed
- [x] 状态变化有 counters 或日志可观测
- [x] 状态机测试覆盖允许、拒绝、过期和替换场景

## 已实现内容

- 新增 `src/token_authorization.hpp` / `src/token_authorization.cpp`，实现 sender 本地授权状态、过期时间和 counters
- 新增 `src/token_authorization_ipc.hpp` / `src/token_authorization_ipc.cpp`，实现 sender 侧 Unix datagram 授权事件接收
- 新增 `src/tx_token_gate.hpp` / `src/tx_token_gate.cpp`，实现发送前授权门控辅助逻辑
- 在 `src/tx.cpp` 中接入授权事件 drain 与发送前门控检查，使 sender 在未授权、过期或异常状态下保持 fail-closed
- 新增并接入 `token_authorization_test`、`token_authorization_ipc_test`、`tx_token_gate_test`、`tx_authorization_integration_test`、`tx_data_source_gate_test`

## 验证

- `make token_authorization_test && ./token_authorization_test`
- `make token_authorization_ipc_test && ./token_authorization_ipc_test`
- `make tx_token_gate_test && ./tx_token_gate_test`
- `make tx_authorization_integration_test && ./tx_authorization_integration_test`
- `make tx_data_source_gate_test && ./tx_data_source_gate_test`
- `make test`

## Blocked by

- https://github.com/Softmoonlit/wfb-ng/issues/11
