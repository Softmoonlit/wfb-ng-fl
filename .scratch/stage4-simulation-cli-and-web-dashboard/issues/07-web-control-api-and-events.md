Status: ready-for-human
Type: grilling
Blocked by: 01 (定义文件仿真作业运行契约), 02 (定义内容寻址模型库契约), 03 (定义控制台运行状态与进度语义), 04 (定义扫频与集群射频准备流程), 05 (定义无外网直连管理网部署契约)

# 定义 Web 控制面的 HTTP 与 SSE 契约

## Question

在 Stage 2 已有版本化 REST 路由和 `/api/v1/logs/stream` SSE 基础上，`wfb-fl-server-daemon` 还需要哪些版本化资源、请求响应 Schema、错误模型、静态资源路由和状态恢复契约来支持模型库、拓扑、射频准备与文件仿真作业控制；如何保持 handler 只做 Web 适配而不复制 Stage 2/3 领域逻辑？
