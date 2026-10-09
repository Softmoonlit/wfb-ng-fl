# 01: 建立 Web 管理入口与可恢复状态快照

**What to build:** 让操作者可以通过显式管理网地址访问 Server Daemon 托管的原生静态页面，并读取足以恢复当前控制台视图的权威状态快照。该切片建立后续模型库、作业、射频和 SSE 共用的 Web application service 接缝。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Server Daemon 保留本机 IPC，同时按显式管理网地址提供固定端口的同源静态 Web listener；不绑定所有接口、TUN 地址或无线数据接口。
- [ ] 管理网地址暂不可用或 Web listener 绑定失败时，核心 daemon、无线控制面、TUN 和 Client Daemon 继续运行，并能暴露明确的 Web 不可用状态。
- [ ] 静态页面、同源 API 和状态快照可访问，未知 API 请求返回结构化 JSON 错误，不回退为 HTML。
- [ ] 状态快照包含 Server、管理 Web、节点、当前/最近作业、射频配置、启动阻塞原因、实例身份和状态版本，并足以恢复基础控制台视图。
- [ ] 领域、HTTP 集成和基础页面测试覆盖 listener 绑定边界、状态 Schema、错误映射和无管理网降级行为。
