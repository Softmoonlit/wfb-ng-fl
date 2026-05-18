# Issue #11: 实现 receiver 到 sender 的 Unix datagram IPC 事件发送

- 原始链接: https://github.com/Softmoonlit/wfb-ng/issues/11
- 状态: 已完成
- 标签: needs-triage
- 创建时间: 2026-05-07T14:06:21Z
- 更新时间: 2026-05-07T14:06:21Z

---

## Parent

https://github.com/Softmoonlit/wfb-ng/issues/6

## What to build

Client 接收进程在接受有效 Token 后，通过 Unix 域 datagram socket 向本机发送进程发送授权事件。IPC 必须保留消息边界，不占用网络端口，并在 sender 不可用或发送失败时保持 fail-closed，不允许因此开启上行发送。

## TDD

已完成的测试：
- [x] Unix datagram IPC 能完整传递单条授权事件
- [x] 多条事件保持消息边界
- [x] socket 不存在或 sender 未启动时发送失败可观测
- [x] 发送失败不会产生本地发送授权
- [x] receiver 接受合法 Token 后会通过 Unix datagram 发出授权事件

## Acceptance criteria

- [x] receiver 通过 Unix 域 datagram socket 发送 Token 授权事件
- [x] IPC 消息结构包含 sender 更新授权状态所需字段
- [x] IPC 不使用 UDP/TCP 网络端口
- [x] IPC 不可用时系统 fail-closed，Client 保持静默
- [x] IPC 资源支持干净清理

## 已实现内容

- 新增 `src/token_event_ipc.hpp` / `src/token_event_ipc.cpp`，定义 `TokenAuthorizationEvent` 与 `TokenEventDatagramSender`
- 在 `src/rx.hpp` 中新增 `TokenEventDatagramListener`，把合法 Token 转成 sender 侧授权事件
- 在 `src/rx.cpp` 本地 `-U unix_socket` 路径下自动安装该 listener，使 receiver 默认把合法 Token 发往本机 sender Unix datagram socket
- 新增 `src/rx_token_ipc_test.cpp`，覆盖单条事件、多条消息边界、sender 未启动 fail-closed
- 扩展 `src/rx_token_listener_test.cpp`，覆盖 receiver 接受合法 Token 后通过 Unix datagram 发出授权事件

## 验证

- `make rx_token_ipc_test && ./rx_token_ipc_test`
- `make rx_token_listener_test && ./rx_token_listener_test`
- `make wfb_rx`

## Blocked by

- https://github.com/Softmoonlit/wfb-ng/issues/10
