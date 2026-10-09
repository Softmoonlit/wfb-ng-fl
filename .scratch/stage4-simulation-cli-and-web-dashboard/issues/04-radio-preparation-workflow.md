Status: ready-for-human
Type: grilling
Blocked by:

# 定义扫频与集群射频准备流程

## Question

空闲状态下如何复用 Stage 2 已有的 `/api/v1/survey` 与 Stage 3 已验证的 `/api/v1/radio/reconfigure`，把扫频推荐、扁平参数校验、UFTP 速率动态约束、操作者确认、集群级应用、双最终屏障、失败回退和重新就绪组成完整 Web 操作流程；各 Server 状态下哪些射频动作必须由既有 API 和 Web 同时禁用？
