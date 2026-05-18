# Issue #15: 补齐本地 Token Passing 自动化回归测试

- 原始链接: https://github.com/Softmoonlit/wfb-ng/issues/15
- 状态: open
- 标签: needs-triage
- 创建时间: 2026-05-07T14:06:33Z
- 更新时间: 2026-05-07T14:06:33Z

---

## Parent

https://github.com/Softmoonlit/wfb-ng/issues/6

## What to build

增加本地自动化测试脚本或测试套件，覆盖第一阶段 Token Passing 的端到端可验证行为：split baseline 不回归、scheduler 顺序、IPC 更新、Token denial、Token expiry、Token targeting、单客户端授权发送、双客户端只有 Token holder 可发。

## TDD

适合 TDD。这是测试聚合型切片，可先落失败测试脚本，再补齐实现缺口。

建议先写的测试：
- [x] split 架构 baseline ping 或等价本地连通性回归
- [x] scheduler 固定顺序测试
- [x] receiver 到 sender IPC 更新测试
- [x] 无 Token denial 测试
- [x] Token expiry 停止发送测试
- [x] Token targeting 测试
- [x] 单客户端授权发送测试
- [x] 双客户端只有 Token holder 可发送测试

## Acceptance criteria

- [x] 本地自动化测试能一键运行
- [x] 测试覆盖 Token 允许和拒绝两类路径
- [x] 测试覆盖 targeting、expiry、IPC、scheduler 顺序
- [x] baseline split 架构未启用 Token 变化时仍可通过回归
- [x] 测试输出日志便于定位失败原因

## Blocked by

- https://github.com/Softmoonlit/wfb-ng/issues/14
