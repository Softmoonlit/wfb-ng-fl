# Issue #8: 实现独立 C/C++ Token 调度器最小闭环

- 原始链接: https://github.com/Softmoonlit/wfb-ng/issues/8
- 状态: completed
- 标签: ready-for-human
- 创建时间: 2026-05-07T14:06:13Z
- 更新时间: 2026-05-08T00:00:00Z

---

## Parent

https://github.com/Softmoonlit/wfb-ng/issues/6

## What to build

实现独立常驻 C/C++ Token scheduler，按命令行静态节点列表进行固定 round-robin Token 发放。调度器支持显式配置 Token duration 和 guard interval，能输出每次发放给哪个节点的日志，并能在重复硬件测试中干净启动和退出。

## TDD

适合 TDD。

建议先写的测试：
- [x] 参数解析：静态 node list、duration_ms、guard interval
- [x] 固定 round-robin 发放顺序
- [x] duration 和 guard 间隔按配置生效
- [x] 退出时清理资源并返回成功状态

## Acceptance criteria

- [x] 调度器是独立 C/C++ 常驻进程，不依赖 Python runtime
- [x] 命令行参数能复现固定硬件拓扑
- [x] Token 发放顺序按静态节点列表 round-robin 重复
- [x] 每次 Token 发放都有可关联节点 ID 的日志
- [x] 调度器支持干净关闭，不遗留测试资源

## Completion notes

- 已新增 `src/token_scheduler.hpp`、`src/token_scheduler.cpp`、`src/main_token_scheduler.cpp`、`src/token_scheduler_test.cpp`。
- 已在 `Makefile` 中接入 `wfb_token_scheduler` 与 `token_scheduler_test` 目标。
- 本地验证已通过：`./token_scheduler_test`（26 条断言）与 `wfb_token_scheduler -n 1,2 -d 1 -g 1` 的启动/日志/信号退出冒烟测试。

## Blocked by

- https://github.com/Softmoonlit/wfb-ng/issues/7
