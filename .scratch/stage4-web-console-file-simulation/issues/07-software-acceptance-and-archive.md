# 07: 建立 Stage 4 无硬件回归与机械归档

**What to build:** 让 CI 或本地无硬件环境可以一次验证 Stage 4 的领域、模型库、HTTP/SSE、浏览器和无线控制面故障路径，并产出可机械校验的分层证据。该验收入口必须复用现有 Daemon、Runtime、mock runtime、canonical fixture 和归档校验能力。

**Blocked by:** 06（交付桌面 Web 操作台闭环）

**Status:** ready-for-agent

- [ ] 正式无硬件入口按领域/模型库、HTTP/SSE、浏览器和控制面回归顺序执行，前一层失败时保留已完成证据并阻止后续层伪造成功。
- [ ] 自动化覆盖模型空文件/超限/去重/引用保护、非法作业请求、节点离线、`TASK_READY` 超时、摘要错误、重复 update、SSE 断线、慢客户端、Server 重启、急停和恢复未完成时拒绝新作业。
- [ ] 控制面测试验证节点发现、心跳、任务宣告、`TASK_READY`、消息重复/乱序、控制面失联和控制面端口/方向，不把管理网 HTTP 成功当作无线控制面成功。
- [ ] 40 MiB 测试 payload 与 SHA-256 由 canonical `wfb_ng.fl.issue41_fixtures` 生成，不使用第二套 fixture 生成器或内联 shell 实现。
- [ ] 每次运行生成唯一 `run_id`，分别保留管理 Web、控制面、数据面、生命周期和汇总证据；机械校验器验证状态、摘要、配置、端口隔离和资源恢复结果。
- [ ] 无硬件回归不修改或重实现既有 Stage 2/3 无线运行时和传输协议，失败结果明确归属 Web、应用状态、控制面、生命周期或测试工具责任边界。
