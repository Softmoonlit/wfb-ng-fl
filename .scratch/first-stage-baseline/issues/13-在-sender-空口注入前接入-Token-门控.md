# Issue #13: 在 sender 空口注入前接入 Token 门控

- 原始链接: https://github.com/Softmoonlit/wfb-ng/issues/13
- 状态: open
- 标签: needs-triage
- 创建时间: 2026-05-07T14:06:27Z
- 更新时间: 2026-05-07T14:07:58Z

---

## Parent

https://github.com/Softmoonlit/wfb-ng/issues/6

## What to build

把 sender 侧 Token 授权状态接入实际上行发送路径，在每次空口注入前检查授权。未授权或 Token 过期时不读取或不注入上行数据，确保 Client 没有有效 Token 时绝对不发射，同时不重写 `wfb_tun` 核心 read/write/batching 行为。

## TDD

适合 TDD。

当前已补充并通过的测试：
- [x] unauthorized 状态不会调用空口注入
- [x] authorized 状态允许调用空口注入
- [x] Token 过期后停止注入
- [x] 门控接入不改变 `wfb_tun` 核心读写批处理路径，发送路径门控逻辑已抽取为可测试入口
- [x] 授权事件排空逻辑可按顺序吸收多条事件，并安全忽略空指针输入
- [ ] 其他节点 Token 不会导致本节点注入

## Acceptance criteria

- [x] 每次空口注入前都检查 Token 授权状态
- [x] 没有有效 Token 时 Client 上行保持静默
- [x] Token 过期后 sender 停止注入，直到新有效 Token 到达
- [x] 不修改 `wfb_tun` 核心 read/write/batching 行为
- [x] 发送路径测试覆盖授权、拒绝和过期场景

## Blocked by

- https://github.com/Softmoonlit/wfb-ng/issues/12
