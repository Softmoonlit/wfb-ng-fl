# Issue #17: 在 Token 门控通过后验证 KCP 小文件上传

- 原始链接: https://github.com/Softmoonlit/wfb-ng/issues/17
- 状态: closed
- 标签: needs-triage
- 创建时间: 2026-05-07T14:06:41Z
- 更新时间: 2026-05-09T15:46:00Z

---

## Parent

https://github.com/Softmoonlit/wfb-ng/issues/6

## What to build

在 Token gating、ping 和小 UDP 通过后，再验证 KCP 小文件上传，确保 Token correctness 与 KCP/大流量缓冲问题分层定位。40MB 传输和 10 节点压力测试只作为后续阶段候选，不作为第一阶段硬验收。

## TDD

不适合纯 TDD：这是 HITL/集成验收测试。适合作为阶段验收脚本和真实硬件测试记录。

实现进展：当前目录已补入最小 KCP 工具构建目标，并在真实硬件 Token 验收脚本中加入 `kcp-small` 场景，用于在 Token grant 通过后执行 KCP 小文件上传、记录 SHA256，并按 Token grant、authorized_sends、服务端数据包、KCP 落盘文件逐层区分失败来源。

建议验收步骤：
- [x] 在 Token-gated split-process 链路上启动 KCP 小文件上传（脚本场景：`kcp-small`）
- [x] 验证文件完整性或校验和（SHA256）
- [x] 记录 Token 窗口、KCP 日志和吞吐表现（`token_context.txt` / `token_results.md` / `kcp_sender.log`）
- [x] 明确 40MB 和 10 节点压力测试不阻塞本阶段完成

## Acceptance criteria

- [x] KCP 小文件在 Token 门控通过后可以完成上传（真实硬件日志：`tests/logs/test_20260508_141216/token_results.md`）
- [x] 上传结果有完整性校验
- [x] 失败时能区分 Token 授权问题和 KCP/缓冲问题
- [x] 40MB 和 10 节点压力测试被记录为后续阶段，不作为本 issue 关闭条件

## 执行记录

- 测试时间：2026-05-08 14:12
- 日志目录：`tests/logs/test_20260508_141216`
- 结果：PASS
- Token：`grant=752 authorized=512 denied=192`
- KCP 源文件 SHA256：`99307919c2155625e0e8519d0dff7f07bb7d10ba0b277b947f59667cce5a3e68`
- KCP 接收文件 SHA256：`99307919c2155625e0e8519d0dff7f07bb7d10ba0b277b947f59667cce5a3e68`
- 备注：server `PKT count_p_data` 未增长但 KCP 接收文件已完整落盘，说明需要后续单独核查 PKT 统计口径，不阻塞本 issue。
- 测试时间：2026-05-09 15:46
- 日志目录：`tests/logs/test_20260509_154640`
- 结果：PASS
- Token：`grant=752 authorized=640 denied=64`
- KCP 源文件 SHA256：`e7683321f62dc39ff5fe1cb6d18c766850f0876ebde60d5b7d07679a62868d7c`
- KCP 接收文件 SHA256：`e7683321f62dc39ff5fe1cb6d18c766850f0876ebde60d5b7d07679a62868d7c`
- 备注：SHA256 校验一致。本次同步修复了 `no-token` 场景缺少主动探测导致 denied_sends=0 的问题，以及 `extract_server_data_packets` 只取最后一行 PKT 导致 dual 场景 server_data=0 误判的问题。所有单机场景（no-token / single / expiry / kcp-small / dual）已全部通过。

## Blocked by

- https://github.com/Softmoonlit/wfb-ng/issues/16
