# Issue #14: 补齐 Token counters 与日志可观测性

- 原始链接: https://github.com/Softmoonlit/wfb-ng/issues/14
- 状态: open
- 标签: needs-triage
- 创建时间: 2026-05-07T14:06:30Z
- 更新时间: 2026-05-07T14:06:30Z

---

## Parent

https://github.com/Softmoonlit/wfb-ng/issues/6

## What to build

为第一阶段 Token Passing 补齐可观测 counters 和日志，至少覆盖 Token received、accepted、ignored、authorized sends、denied sends，并让真实硬件测试能从日志中关联调度器 Token 发放、receiver 接受/忽略、sender 授权/拒绝发送。

## TDD

适合 TDD。

已完成测试：
- [x] 收到 Token 时 received counter 递增
- [x] 接受本节点 Token 时 accepted counter 递增
- [x] 忽略其他节点、stale、duplicate Token 时 ignored counter 递增
- [x] 授权发送时 authorized sends counter 递增
- [x] 拒绝发送时 denied sends counter 递增

## Acceptance criteria

- [x] 暴露 Token received / accepted / ignored counters
- [x] 暴露 authorized sends / denied sends counters
- [x] scheduler、receiver、sender 日志能关联 node_id 和 Token 窗口
- [x] counters 在关键路径测试中被断言
- [x] 日志足以支持真实硬件测试排障

## 当前实现落点

- receiver 计数与过滤：`src/token_control_packet.hpp`、`src/token_control_packet.cpp`
- receiver 周期日志：`src/rx.cpp` 的 `TOKEN_FILTER`
- sender 授权计数：`src/token_authorization.hpp`、`src/tx_token_gate.cpp`
- sender 周期日志：`src/tx.cpp` 的 `TOKEN_AUTH`
- scheduler 窗口日志：`src/token_scheduler.cpp`

## 三端日志对照样例

```text
scheduler
grant seq=12 node_id=3 duration_ms=40 guard_interval_ms=5 window_end_offset_ms=40
guard seq=12 guard_interval_ms=5 next_seq=13

receiver
1715151515000	TOKEN_FILTER	2:1:1:0:0:0
# 含义：received=2 accepted=1 ignored_wrong_node=1 ignored_duplicate=0 ignored_stale=0 ignored_expired=0

sender
1715151515000	PKT	0:10:1200:4:480:6:0	TOKEN_AUTH	1:0:4:6
# TOKEN_AUTH 含义：accepted_events=1 rejected_events=0 authorized_sends=4 denied_sends=6
```

## 真实硬件排障用法

1. 先看 scheduler 的 `grant seq/node_id/window_end_offset_ms`，确认服务端已按预期发放窗口。
2. 再看 receiver 的 `TOKEN_FILTER`，确认客户端是否收到了 Token，以及被忽略的原因是 wrong_node、duplicate、stale 还是 expired。
3. 最后看 sender 的 `TOKEN_AUTH`，确认授权事件是否真正转化为 authorized_sends，或是否因为窗口过期表现为 denied_sends 增长。
4. 三端联查时优先按 `seq`、`node_id` 和时间窗口关联，而不是只看单端包计数。

## Blocked by

- https://github.com/Softmoonlit/wfb-ng/issues/13
