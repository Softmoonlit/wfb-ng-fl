# Issue #9: 定义 Token 控制包格式与解析测试

- 原始链接: https://github.com/Softmoonlit/wfb-ng/issues/9
- 状态: open
- 标签: ready-for-human
- 创建时间: 2026-05-07T14:06:16Z
- 更新时间: 2026-05-08T00:00:00Z

---

## Parent

https://github.com/Softmoonlit/wfb-ng/issues/6

## What to build

定义第一阶段 Token 控制包的最小字段，并让 Client 接收路径能可靠识别 Token 控制包，解析 node_id、过期时间或 duration、sequence 等授权所需信息，同时不误判普通数据包。

## TDD

适合 TDD。

建议先写的测试：
- [x] 合法 Token 控制包可被识别和解析
- [x] 非 Token 数据包不会被误识别
- [x] 非法长度、非法 magic/version、字段边界值会被拒绝
- [x] 解析结果包含后续授权判断所需字段

## Acceptance criteria

- [x] Token 控制包格式有明确字段定义
- [x] 接收路径能区分 Token 控制包和普通数据包
- [x] 非法 Token 不会产生授权事件
- [x] 解析逻辑有本地单元测试覆盖

## 实现结果

已完成第一阶段最小实现，采用“独立解析模块 + rx 单点接入”方案：

- 在 `src/wifibroadcast.hpp` 中新增 `WFB_PACKET_TOKEN_CONTROL` 与 `wtoken_control_hdr_t`
- 在 `src/token_control_packet.hpp` / `src/token_control_packet.cpp` 中新增独立解析器
- 在 `src/rx.cpp` 的 `Aggregator::process_packet()` 中新增 Token 控制包分支
- 在 `src/rx.hpp` 中新增 `TokenControlListener` 作为最小观察接口
- 在 `src/token_control_packet_test.cpp` 中补齐 Catch2 单元测试
- 在 `Makefile` 中接入 `token_control_packet_test` 与 `wfb_rx` 链接依赖

当前解析字段包括：
- `node_id`
- `sequence`
- `duration_ms`
- `expires_at_ms`（由接收端基于 `now_ms + duration_ms` 派生，溢出时饱和到 `UINT64_MAX`）

当前已覆盖行为：
- 合法 Token 控制包识别与解析
- 普通数据包不误判
- 非法长度拒绝
- 非法 magic 拒绝
- 非法 version 拒绝
- `node_id / sequence / duration_ms / expires_at_ms` 边界值覆盖

## 验证结果

已完成以下验证：

- `make token_control_packet_test && ./token_control_packet_test` 通过
- `make wfb_rx` 通过
- `make test` 通过
  - 32 项测试执行成功
  - 1 项因 root / 系统限制跳过（既有行为，与本 issue 无关）

## 后续衔接

本 issue 已为后续授权判断打通最小前置能力，但尚未实现：

- 仅接受发给本机节点的 Token
- stale / duplicate Token 过滤
- receiver 到 sender 的授权事件传递
- sender 侧 Token 授权状态机与发送门控

后续工作由 issue 10 继续承接。

## Blocked by

- https://github.com/Softmoonlit/wfb-ng/issues/8
