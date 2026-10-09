Status: resolved
Type: grilling
Blocked by:

# 定义 Web 控制面的 HTTP 与 SSE 契约

## Question

在 Stage 2 已有版本化 REST 路由和 `/api/v1/logs/stream` SSE 基础上，`wfb-fl-server-daemon` 还需要哪些版本化资源、请求响应 Schema、错误模型、静态资源路由和状态恢复契约来支持模型库、拓扑、射频准备与文件仿真作业控制；如何保持 handler 只做 Web 适配而不复制 Stage 2/3 领域逻辑？

## Answer

### 资源和版本边界

Stage 4 使用同一 `wfb-fl-server-daemon` 的同源 HTTP listener。新增和稳定的控制台资源为：

- `GET /api/v1/state`：返回完整、可恢复当前视图的权威状态快照。
- `GET /api/v1/models`：按首次入库时间倒序返回模型库清单。
- `POST /api/v1/models`：通过管理网 HTTP multipart 流式上传模型文件。
- `DELETE /api/v1/models/{sha256}`：删除未被作业引用的模型制品。
- `POST /api/v1/jobs`：按完整 SHA-256、固定目标节点和固定同步轮数创建文件仿真作业。
- `POST /api/v1/jobs/{job_id}/abort`：请求急停。
- `GET /api/v1/events?limit=100`：返回当前 daemon 实例最近的操作者关键事件。
- `GET /api/v1/events/stream`：提供控制台快照和事件 SSE。
- `GET /api/v1/radio/config`：读取当前确认的扁平射频配置。
- `POST /api/v1/radio/config/validate`：校验待应用射频配置并生成确认上下文。
- `POST /api/v1/radio/config/apply`：使用有效确认上下文应用集群射频配置。

`GET /api/v1/status` 保留为只读稳定入口，并返回与 `/api/v1/state` 相同的完整快照。`/api/v1/logs/stream` 保留为诊断日志流，不承担控制台状态恢复。旧的 `/api/v1/jobs/start` 和 `/api/v1/radio/reconfigure` 写入形状退出 Stage 4 契约，返回 `410 ROUTE_REPLACED` 并指向新资源；不接受旧请求体作为兼容垫片。未版本化别名不提供。

### 作业请求与首版能力

创建作业使用：

```http
POST /api/v1/jobs
Content-Type: application/json
```

```json
{
  "model_sha256": "完整64位小写SHA-256",
  "target_nodes": [1, 2],
  "rounds": 3
}
```

Server 在受理时原子校验模型存在、目标节点集合、轮数、射频状态和资源恢复状态，并立即取得模型引用。目标节点集合固定于作业生命周期，运行中不因节点掉线而缩减。响应为 `202 Accepted`，包含 `job_id`、初始快照或状态版本。

Web API 不接受 `mode`、插件选择、训练参数、聚合参数或 `model_path`。首版固定同步文件仿真作业；非 `sync` 能力不作为字段暴露。底层已有其他 Coordinator 模式可以保留给既有范围或后续工作，但不进入本 Web 契约。

创建作业使用 `Idempotency-Key`。相同 key 在 Server 实例生命周期内返回同一受理结果；不接受任意 Server 本地路径。

### 管理网模型上传

模型从用户电脑经直连管理网使用同源 HTTP 上传，不使用 FTP、SMB 或 SFTP。管理网只决定 TCP/IP 的连通路径，文件协议由 Web API 提供：

```http
POST http://<web_host>:8080/api/v1/models
Content-Type: multipart/form-data
```

请求只允许一个 `file` 部件。Server 流式写入受控模型库临时文件，边接收边计算 SHA-256，完成后校验文件名、实际大小、单文件上限、容量预留并持久化；上传内容不进入 SSE，也不经过无线数据面。模型库使用 1 GiB 单文件上限和 10 GiB 总容量上限，空文件拒绝，同一内容按 SHA-256 去重。

首版使用单请求上传，不支持断点续传或 `Transfer-Encoding: chunked`。JSON 请求体上限为 1 MiB；multipart 请求必须携带 `Content-Length`，超过单文件上限加 multipart 开销立即返回 `413 MODEL_TOO_LARGE`。接收超过声明大小、容量预留、连接断开、超时或写入失败时，删除临时文件并释放预留容量。

新制品返回 `201 Created`：

```json
{
  "model": {
    "sha256": "...",
    "filename": "initial.bin",
    "size_bytes": 41943040,
    "created_at": "RFC3339 timestamp",
    "referenced_by_job": false
  },
  "deduplicated": false
}
```

相同内容已存在时返回 `200 OK`，结构相同且 `deduplicated` 为 `true`。重复上传是正常去重结果，不是冲突错误。

### 状态快照

Server Daemon 维护 Web 权威状态快照；Web 不读取 Client 文件、不直接访问 WFB、TUN、UFTP 或 HTTP PUT，也不从事件历史拼装状态。快照至少包含：

```json
{
  "schema_version": 1,
  "instance_id": "...",
  "state_version": 42,
  "generated_at": "RFC3339 timestamp",
  "server": {
    "state": "idle",
    "management_web": "ready",
    "radio": {},
    "can_start_job": false,
    "start_blockers": []
  },
  "nodes": [],
  "job": null,
  "events": []
}
```

`job` 存在时包含作业身份、模型摘要、固定目标节点、总轮数、当前轮次、已完成轮数、Server 阶段、作业/轮次/阶段耗时、执行结果、资源恢复状态、错误和阻塞节点。执行结果与资源恢复严格分开，例如作业可以已经失败但仍处于 `recovery: recovering`，此时仍禁止启动新作业。

Web 枚举由领域状态显式映射而来，不直接暴露内部类名。冻结的主要值为：

- Server：`starting`、`idle`、`preparing`、`running`、`aborting`、`radio_error`、`unavailable`。
- 节点：`offline`、`hunting`、`idle`、`preparing`、`running`、`aborting`、`error`；节点就绪是独立的 readiness 事实。
- Server 阶段：`preparing`、`distributing_model`、`waiting_updates`、`recovering_idle`。
- 执行结果：`none`、`running`、`succeeded`、`failed`、`aborted`。
- 恢复状态：`not_required`、`recovering`、`ready`、`blocked`。

不提供总完成百分比、文件传输百分比、预计剩余时间、逐节点文件阶段或链路实时遥测。

### 射频配置确认

射频采用完整扁平配置：`channel`、`radio_txpower_dbm`、`downlink_mcs`、`uplink_mcs`。校验使用：

```http
POST /api/v1/radio/config/validate
```

响应返回完整规范化配置、`validation_id`、警告、是否需要操作者明确确认和过期时间。UFTP 安全区间越界返回强告警，不能由普通提交绕过。

应用使用：

```http
POST /api/v1/radio/config/apply
```

```json
{
  "validation_id": "...",
  "confirm_warning": true
}
```

`validation_id` 绑定完整配置、状态版本和 Server `instance_id`。配置变更、状态变更、过期、服务重启、作业启动或节点就绪变化都会使它失效。应用过程返回 `202 Accepted` 和操作 ID，最终结果写入状态快照及 SSE。底层继续复用 Stage 3 双最终屏障和 15 秒租约。

### 错误模型

所有 API 错误统一返回：

```json
{
  "error": {
    "code": "JOB_RESOURCE_NOT_READY",
    "message": "上一项作业仍在恢复待命",
    "details": {},
    "request_id": "..."
  }
}
```

主要 HTTP 映射为：

- `400`：请求体、字段、文件名或基础参数非法。
- `404`：模型、作业或资源不存在。
- `405`：HTTP 方法不允许，并返回 `Allow`。
- `409`：状态冲突、模型被引用、作业冲突或确认上下文冲突。
- `410`：旧版本化写路由已退出 Stage 4 契约。
- `413`：单文件、请求体或模型库容量限制。
- `422`：格式正确但领域校验失败。
- `503`：服务或管理 Web listener 暂不可用。

错误只暴露稳定错误码、可读消息、结构化细节和 `request_id`，不返回本地路径、临时目录、堆栈、SSH 命令、私有路径、凭据或原始异常文本。详细异常进入 Server 本地日志或证据归档。

### SSE、事件和恢复

控制台使用：

```http
GET /api/v1/events/stream
Accept: text/event-stream
```

建立连接先发送完整 `snapshot`，随后发送完整 `state_changed` 快照和独立的 `operator_event`。每条帧带单调递增 ID；`Last-Event-ID` 用于判断是否发生丢失，但 Server 不保证回放全部中间事件。状态快照完整发送，不使用浏览器端 JSON Patch 状态机。

Server 在内存保留当前 daemon 实例最近 100 条操作者关键事件，`GET /api/v1/events?limit=100` 按递增 sequence 返回。事件包括作业受理、作业开始、轮次开始/完成、Server 阶段变化、节点离线、作业失败/急停、执行完成、恢复开始/完成/阻塞和射频应用结果。周期心跳不进入操作者事件；同一领域事实只产生一条事件，消除 Daemon 与 Coordinator 重复来源。

每个 SSE 连接使用有限内存队列。客户端过慢导致队列溢出时关闭连接，客户端重新连接并取得完整快照；不能让慢客户端阻塞领域运行时。心跳使用 SSE 注释帧，不制造操作者事件。

浏览器刷新或重连通过完整快照恢复，事件缺失不影响状态正确性。浏览器失联时暂停本地耗时推算并标记状态可能过时；急停入口保留，只有 Server 返回受理确认后才显示急停已受理。

急停使用 `POST /api/v1/jobs/{job_id}/abort`。活动作业返回 `202 Accepted`，表示请求已受理；最终 `aborted` 和 `recovery.ready` 由快照/SSE 表达。重复急停幂等返回当前状态；不存在的作业返回 `404`；执行已结束但仍在恢复时不得宣告恢复完成。恢复未完成前始终拒绝新作业。

Server 重启生成新的 `instance_id`，新快照不把旧作业伪造为成功，旧事件不跨实例恢复；旧作业身份不可继续操作。服务重启后的页面明确显示前次运行状态不可用。

### 静态资源和请求边界

- `GET /` 返回内置控制台 HTML。
- `GET /assets/<固定文件名>` 返回安装包内置 CSS/JavaScript。
- 静态资源不接受任意文件系统路径，不支持目录遍历或任意代理。
- API 和 SSE 与静态资源由同一个明确 `web_host:8080` listener 提供。
- API 要求 Host 匹配配置的管理网地址和端口，不启用 CORS；状态、模型和事件接口使用 `Cache-Control: no-store`，带内容版本的静态资源可短期缓存。
- API 严格匹配 `/api/v1/...` 和 HTTP 方法，不自动兼容末尾斜杠；未知 API 路径返回 JSON `404`，不回退到 HTML。
- 首版按已确认部署边界使用明文 `http://<web_host>:8080/`，不增加 HTTPS、应用鉴权或跨域远程访问。

### handler 与领域服务边界

HTTP handler 只负责解析 HTTP、JSON、multipart 和 SSE，执行基础格式校验，将请求映射到 Server Daemon application service，并将领域结果、领域错误和权威状态映射为 Web Schema。模型库、作业生命周期、射频准备、状态快照、事件去重和资源门禁由领域服务拥有。

handler 不直接调用 Client、SSH、WFB、TUN、UFTP 或 HTTP PUT，不复制 Stage 2/3 的 Coordinator、Runtime、Transport、控制面和生命周期逻辑。正常作业仍由 Server Daemon 经现有控制面、Client Daemon、RoleService 和无线数据面完成。

### 验收边界

本契约至少覆盖：直连管理网获取静态页面；HTTP 上传确定性 40 MiB 文件；重复内容去重；仅用 SHA-256 创建同步作业；拒绝 `model_path`、非 `sync` 和未版本化路径；快照轮次/阶段/恢复状态真实性；SSE 首帧快照、断线重连和事件去重；恢复完成前拒绝新作业；射频越界二次确认；模型不存在、模型被引用、容量不足、节点离线和作业冲突的统一错误；Server 重启后的新实例身份和旧作业不可伪造恢复。

本票只完成 Web HTTP/SSE 决策，不实现运行时代码，也不替代最终验收矩阵票。
