# Issue #10: 实现 Token targeting 与 stale/duplicate 过滤

- 原始链接: https://github.com/Softmoonlit/wfb-ng/issues/10
- 状态: open
- 标签: needs-triage
- 创建时间: 2026-05-07T14:06:18Z
- 更新时间: 2026-05-08T01:25:00Z

---

## Parent

https://github.com/Softmoonlit/wfb-ng/issues/6

## What to build

Client 接收进程只接受发给本节点的新 Token。目标节点不是本机、已过期、重复 sequence、回退 sequence 或其它 stale Token 必须忽略，避免旧控制信息重新打开上行发送权限。

## TDD

适合 TDD。

建议先写的测试：
- [x] 本节点新 Token 被接受
- [x] 其他节点 Token 被忽略
- [x] 重复 Token 被忽略
- [x] 旧 sequence Token 被忽略
- [x] 过期 Token 被忽略

## Acceptance criteria

- [x] 接收逻辑以本机 node_id 判断 Token 是否属于本节点
- [x] stale/duplicate Token 不会产生授权事件
- [x] 忽略路径可计数或可日志观测
- [x] 覆盖 targeting 和 stale/duplicate 场景的自动化测试通过

## 已完成实现

- `wfb_rx` 新增 `-N <local_node_id>` 参数，用于显式传入本机 `node_id`
- `src/token_control_packet.hpp` / `src/token_control_packet.cpp` 新增 Token 过滤判定、过滤状态与计数器
- `src/rx.cpp` 在 Token 接收路径接入过滤逻辑，只有合法 Token 才会触发 `TokenControlListener`
- `src/rx.cpp` 新增 `TOKEN_FILTER` 统计输出，记录 accepted / wrong_node / duplicate / stale / expired
- 新增 `src/token_control_filter_test.cpp`，覆盖 5 类过滤行为
- 新增 `src/rx_token_listener_test.cpp`，验证 listener 只接收合法 Token

## 验证结果

- `./token_control_filter_test` 通过（5 个测试用例，38 条断言）
- `./rx_token_listener_test` 通过（1 个测试用例，27 条断言）
- `make token_control_filter_test wfb_rx rx_token_listener_test` 通过

## Blocked by

- https://github.com/Softmoonlit/wfb-ng/issues/9
