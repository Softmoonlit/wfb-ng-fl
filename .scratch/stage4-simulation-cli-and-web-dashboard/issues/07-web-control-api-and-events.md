Status: ready-for-human
Type: grilling
Blocked by: 01 (定义平台编排与算法插件运行契约), 02 (定义内容寻址模型库契约), 03 (定义控制台运行状态与进度语义), 04 (定义扫频与集群射频准备流程), 05 (定义无外网直连管理网部署契约), 06 (定义插件清单、参数 Schema 与全节点就绪门禁)

# 定义 Web 控制面的 HTTP 与 SSE 契约

## Question

为静态 Web 控制台提供支持时，`wfb-fl-server-daemon` 应以哪些版本化 REST 资源、请求响应 Schema、错误模型、静态资源路由和 SSE 事件契约暴露模型库、插件目录、拓扑、射频准备、作业控制与状态恢复；如何保持 handler 仅作为适配器而不承载领域逻辑？
