# 01: 建立 Web 管理入口与可恢复状态快照

**What to build:** 让操作者可以通过显式管理网地址访问 Server Daemon 托管的原生静态页面，并读取足以恢复当前控制台视图的权威状态快照。该切片建立后续模型库、作业、射频和 SSE 共用的 Web application service 接缝。

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] Server Daemon 保留本机 IPC，同时按显式管理网地址提供固定端口的同源静态 Web listener；不绑定所有接口、TUN 地址或无线数据接口。
- [x] 管理网地址暂不可用或 Web listener 绑定失败时，核心 daemon、无线控制面、TUN 和 Client Daemon 继续运行，并能暴露明确的 Web 不可用状态。
- [x] 静态页面、同源 API 和状态快照可访问，未知 API 请求返回结构化 JSON 错误，不回退为 HTML。
- [x] 状态快照包含 Server、管理 Web、节点、当前/最近作业、射频配置、启动阻塞原因、实例身份和状态版本，并足以恢复基础控制台视图。
- [x] 领域、HTTP 集成和基础页面测试覆盖 listener 绑定边界、状态 Schema、错误映射和无管理网降级行为。

## Answer

管理控制台由 `wfb_ng/fl/console.py` 的 application service 维护权威快照，`console_http.py` 负责同源 HTTP 与管理 listener，安装包中的原生 HTML/CSS/JavaScript 展示基础状态。`web_host` 由部署者显式设置，端口固定为 8080；用已有 pyroute2 校验地址实际归属，拒绝无线、TUN、回环及通配地址。地址未就绪或绑定失败时只降级管理 Web，后台重试；本机 IPC 和核心无线运行时继续工作。

Web 的 `/api/v1/state` 与 `/api/v1/status` 返回同一完整快照，未知 API 和不支持的方法返回统一 JSON 错误，已退出 Web 契约的旧写路由返回 410。基础页面支持刷新恢复、连接失败提示、重试与 Server 实例变化提示。当前/最近作业只投影既有生命周期及已知的本机资源恢复事实；未增加客户端心跳恢复屏障、恢复超时或新的作业准入状态机。可靠恢复确认与 SSE 由 Issue 05 承担，模型、射频和作业操作由对应后续 Issue 承担。

静态资源同时进入 setuptools、sdist 与直接安装路径，部署配置说明见 `docs/deployment/部署与运行基准.md`。测试通过 application service、真实 HTTP 与实际 JavaScript 行为验证；真实管理 IPv4 的校验、8080 绑定和服务组合测试已运行通过。页面行为测试使用现有 Node 和 DOM/fetch stub，未声称完成真实浏览器或三机硬件验收。

## Comments

- 双轴代码审查已完成：规范轴与规格轴均通过，无剩余可操作缺陷。根据审查撤回扩大范围的客户端恢复状态机，修正 Web 停止、配置错误与观察失败隔离边界，并对相同 Web 绑定失败事件去重。
- 最终完整验证：`python -m pytest -q` → **427 passed**；mypy 检查 `console.py`、`console_http.py`、`server_daemon.py` 通过；JavaScript 和安装脚本语法检查、`git diff --check` 通过。
- 直接安装临时目录与 `sdist → wheel` 的 HTML/CSS/JavaScript 均与源码一致。真实管理 IPv4 组合 HTTP 测试通过，基础页面使用现有 Node 执行实际 JavaScript 行为测试。
