# Transport Backend 传输契约

<a id="transport-contract"></a>

## 所有权

本文档唯一拥有文件交换到具体 Transport 的映射、UFTP/HTTP 资源和元数据、传输完成条件、响应边界和传输失败语义。它不拥有 FL Runtime 的轮次状态机，也不拥有 READY、GRANT、Token Passing 或物理层机制。

角色主进程、Runtime 与 Transport 的进程边界由[系统分层与跨层契约](系统分层与跨层契约.md#cross-layer-contracts)拥有。Transport 在角色进程启动时先初始化并创建角色所需的常驻接收器；Transport 报告 ready 后 Runtime 四接口才可用。client Transport 常驻运行一个原生 `uftpd` 子进程接收下行，server Transport 在主进程内常驻运行 HTTP listener 接收上行。常驻只描述接收器生命周期：server 的 `uftp` 发送进程仍由每次 `publish_model(...)` 对应的下行 operation 按需启动，client 的 HTTP PUT 仍由每次 `submit_update(...)` 对应的提交 operation 单独发起。接收器的启动、监控和停止均属于 Transport 实现，Runtime 不直接管理 `uftpd`、HTTP listener 或连接。

## 下行 UFTP

Runtime 把托管模型文件和 model manifest 交给 Transport；Transport 根据本轮参与客户端集合建立 closed group，调用原生 UFTP 完成下行，并维护显式的 `NODE_ID -> UFTP` 目标映射。Runtime 不直接调用 UFTP、拼接 UFTP 命令或解析 UFTP 输出；model manifest 不承载 UFTP 目标身份。

UFTP 必须启用 `-q` 和 `-S status_file`。每个下行 operation 在固定工作目录中使用一个全新、专属且不与其他轮次复用的 status 文件；启动前该路径必须不存在，不能在旧内容后追加本轮记录。UFTP 子进程自然退出并被回收后，Transport 才对该文件进行最终解析。进程退出码为 `0` 只是自然成功的必要条件，不能单独证明全部目标完成；下行自然成功必须同时满足：

- 每个预期 UFTP client 都有且只有一个 `CONNECT;success`。
- `model.bin` 和 `model.manifest.json` 对每个预期 client 各有且只有一个 `RESULT`，状态均为 `copy`。
- 没有缺失、重复、未知 client、`rejected`、`skipped` 或其他非 `copy` 状态。
- 固定的新轮目录不接受 `overwrite`。

`STATS` 只用于观测，不能替代逐 client、逐文件结果矩阵。status 文件只参与本次 operation 的自然完成结果校验，不能判断取消请求是否成功，也不能替代 Transport 对取消与自然完成先后关系的裁决。UFTP 已报告文件交付成功，不等于 client Runtime 的 manifest、参与集合和 SHA-256 校验成功；client 必须独立校验。

client `uftpd` 不通过公开 `wait_for_model()` 启动或停止；它由 client Transport 在服务启动阶段启动并持续接收候选下行文件。client `uftpd` 必须使用与 server `uftp` 一致的公共组播地址加入下行接收组；该地址属于 client Transport 配置，不由 Runtime 轮次接口传递。`wait_for_model()` 只等待和消费 Transport 已提交的候选交付物，再由 Runtime 执行 manifest、参与集合和 SHA-256 校验。v8 每个 client Transport 实例只运行一个 `uftpd`；其意外退出使当前模型等待明确失败，不定义进程内自动恢复状态机，由服务重启恢复。

下行反馈机会由链路层调度。Client 可以先行训练和提交 update，但 Transport 只有确认全部预期目标的模型文件结果后，才向 Runtime 交付下行完成结果。

Transport 接受并启动下行 UFTP 时，向 Runtime 返回只标识本次下行操作的不透明句柄，并独立管理 UFTP 子进程。Runtime 是该操作的调用者和生命周期所有者，可以通过内部 `cancel_downlink(operation_handle)` 显式请求停止；Runtime 不持有或直接操作 UFTP PID。Transport 必须把取消请求与自然完成串行裁决，并返回以下独立结果之一：`cancelled` 表示取消先取得裁决权且本地 UFTP 子进程已退出并被回收，`already_completed` 表示 UFTP 已先形成最终成功或失败结果。取消不能追溯改写已经完成的 UFTP 结果，任何取消结果也不直接决定或改写 Runtime 轮次状态。未知或不属于当前 Transport 的操作句柄属于内部接口违约，直接 fail-fast，不作为取消结果进入 Runtime 轮次状态机。

`cancel_downlink(...)` 必须在返回前形成最终取消裁决，不能只表示停止请求已经发出。Transport 先请求原生 UFTP 优雅停止并在宽限期内等待；宽限期到达后强制结束，再等待并确认本地子进程退出和回收。该裁决只拥有本地 UFTP operation 的终态，不承诺 `ABORT` 已送达任何 client，也不等待或证明远端 client 已完成文件清理。v8 最小闭环不为强制结束后仍无法回收子进程的操作系统极端失效定义额外业务结果。

同一 Transport 实例同时最多有一个活动下行 UFTP operation；启动第二个活动下行属于内部接口违约并直接 fail-fast。Transport 不维护已终态 operation 的跨轮历史门禁；Runtime 的同步接口完成边界和严格串行轮次门禁保证新下行启动前旧 operation 已经形成终态并被回收。

UFTP 自然传输依赖原生协议的 GRTT、ROBUST、消息重传和阶段失败机制形成终态；相关参数必须适配目标 Token 调度环境。v8 不为整个下行 operation 增加总时长 deadline，以免把随模型大小和空口吞吐变化的正常长传输误判为失败。显式取消的停止宽限期只约束本地子进程在取消裁决中的退出，不是自然传输 timeout。

## 上行 HTTP 资源

上行采用每个 client 一条独立 HTTP 请求和 TCP 连接向 server 提交 update，不维护长期 HTTP session。HTTP 提供请求/响应边界、内容长度和最终状态码，不定义私有二进制 framing。

固定资源形态为：

```http
PUT /v1/rounds/{round_id}/updates/{node_id}
Content-Length: <size_bytes>
Content-Digest: sha-256=:<base64-digest>:
Content-Type: application/octet-stream

<update.bin bytes>
```

request body 只承载 `update.bin`。`round_id` 和 `node_id` 只能出现在受控资源路径中并严格校验；client 不得提交任意服务器路径。URL 是两个业务身份字段的唯一权威来源，不定义 `X-Round-ID` 或 `X-Node-ID`；未知 header 不能覆盖路径身份。

server 只接受 Transport 当前准入上下文所标识的轮次、合法 UUID、合法 `node_id`、静态已知 client、本轮参与集合和首次 `(round_id, NODE_ID)` 提交。该准入上下文由 Runtime 开轮时一次性安装，至少冻结 `round_id`、参与集合、大小限制和固定目录；安装完成后，Runtime 状态不参与单个请求的准入裁决，也不能撤回该上下文。Runtime 安装新的准入上下文时，Transport 原子替换当前上下文；尚未被接受的旧 `round_id` 请求随后拒绝。替换前已经预留上传槽位的操作持有接受时的旧上下文快照，继续独立完成，不因当前上下文变化而中止或改判。若没有新上下文替换，旧上下文不因 Runtime 进入终态而自动失效。TCP peer IP 可写入排障日志，但不参与接收裁决。受信任部署边界不构成密码学身份认证；本契约不引入 bearer token、TLS 或 mTLS。

## 元数据和准入

`Content-Length` 必须存在、为已知值且不超过正整数 `max_update_size_bytes`；不接受 chunked body。server 与所有 client 使用同一配置值，运行中不热更新。client 在归档后、发起请求前先按上限 fail-fast，server 在允许 body 前再次检查。

`Content-Digest` 必须是唯一、语法正确且算法为 `sha-256` 的标准字段。client 使用归档得到的同一 32 字节摘要编码为 base64；本地 manifest 仍保存 64 位小写十六进制。server 流式读取 body 时独立计算摘要并比较。摘要用于交付完整性，不构成敌对环境认证。

server 先完成 method、路径、身份、Transport 当前准入上下文、参与集合、重复提交、媒体类型、长度、摘要语法、大小和存储检查。全部通过后，Transport 为 `(round_id, NODE_ID)` 原子预留唯一上传槽位；预留成功是 Transport 接受本次上传操作的边界，发生在尝试发送 `100 Continue` 之前。预留必须同时捕获不可变的轮次上下文快照，包括 `round_id`、参与集合、固定目录、大小限制和失败回调。操作被接受不表示已经接收任何上行数据：只有 `100 Continue` 成功写回后，client 才能发送 body，server 才进入 body 接收。预留前检查失败只拒绝本次请求，当前 Transport 准入上下文继续等待该 client 的有效首份提交；client 不自动重试。

client 固定使用 `Expect: 100-continue`，在收到 `100 Continue` 前不得写入任何 body。若先收到最终 `4xx`/`5xx`，或等待中间响应超时，直接失败，不降级为先发 body。TCP SYN 和 headers 已足以进入 TUN 上行队列，不需要 body 才能触发链路层 READY。

## 接收和提交

通过准入后，server 把 body 流式写入最终目录旁唯一临时文件，同时累计实际长度和 SHA-256。只有实际长度、声明长度和摘要全部一致时，才生成权威 `update.manifest.json`，按“数据文件先落位、manifest 最后原子落位”提交到：

```text
<work_dir>/rounds/<round_id>/updates/<node_id>/
  update.bin
  update.manifest.json
```

首次有效 `(round_id, NODE_ID)` 提交在 `update.bin` 与 `update.manifest.json` 都原子落位后达到不可逆的 Transport 成功边界，并返回无 body 的 `201 Created`。越过最终 manifest 提交点后，即使 Runtime 随后或并发进入 `failed`，当前请求仍必须按已创建资源的事实尝试返回 `201`，不得重新查询上层终态并改写为 `round_failed`。从上传槽位预留成功开始，本次操作始终由 Transport 独立推进和裁决，Runtime 状态不能截断它或改写其结果。`201` 只确认该 update 资源已按 Transport 契约持久接受，不等待或声明 Runtime 的收齐快照、轮次状态或全局聚合结果；Runtime 后续记账或终态提交失败不能追溯撤销已经成立的 Transport 成功。不要求 `Location`。`200`、`204` 或其他状态码都不表示成功。已有有效同键资源返回 `409 Conflict`，不覆盖、不按幂等成功处理；只有临时文件时可以清理或隔离后重新成为首次提交。

Transport 只向 Runtime 交付同时满足以下条件的有效 update：`round_id` 匹配该操作接受时持有的准入上下文快照、`NODE_ID` 属于该快照的参与集合、传输完成、完整性校验通过。最终 `update.manifest.json` 的原子出现是该成功结果的持久权威事实；Transport 随后可以发送进程内通知以立即唤醒 Runtime，但通知允许丢失、不承载唯一状态，也不要求 Runtime 同步回调成功。Runtime 只按自身当前轮的固定路径幂等发现和消费匹配结果；旧轮操作在新准入上下文安装后完成时，其结果仍属于原 `round_id`，不能计入新轮。迟到、未知节点和重复提交拒收并留痕，不计入收齐。

server HTTP listener 不通过公开 `wait_for_updates()` 启动或停止；它由 server Transport 在服务启动阶段启动并持续监听。每个 HTTP PUT 仍是由远端 client 单独发起并由 listener 接受的一次请求 operation，不维护长期 HTTP session。client 可以在下行仍处于发布状态时提前提交，Transport 持久化有效结果并报告其状态，但下行完成屏障之前不能使 Runtime 成功。

## 失败和状态码

固定错误映射如下：

| HTTP 状态码 | 使用场景 |
| --- | --- |
| `400 Bad Request` | URL、header、摘要格式或请求结构非法 |
| `404 Not Found` | `round_id` 不存在或不匹配 Transport 当前准入上下文 |
| `409 Conflict` | `(round_id, NODE_ID)` 已有有效 update、已接受的同键竞争请求，或该键已经用尽首次提交机会 |
| `411 Length Required` | 缺少 `Content-Length` |
| `413 Content Too Large` | update 超过配置上限 |
| `415 Unsupported Media Type` | `Content-Type` 不是 `application/octet-stream` |
| `422 Unprocessable Content` | body 长度或 SHA-256 与声明不一致 |
| `500 Internal Server Error` | manifest、Transport 内部状态或其他内部处理失败 |
| `507 Insufficient Storage` | 磁盘空间不足或落盘失败 |

错误响应使用 `Content-Type: application/json; charset=utf-8`，body 不超过 16 KiB，至少含稳定 `error_code` 和仅用于日志排障的 `error_message`。不得泄露本地路径、堆栈、命令行或临时文件名。client 同时依据状态码和 `error_code` 判断失败；错误 body 缺失或解析失败不能升级为成功。

连接在上传槽位预留前中断时，请求尚未被 Transport 接受，清理请求并让该准入上下文继续等待。槽位预留后，`100 Continue` 写回失败、连接中断，或 body 已开始但最终 manifest 尚未提交，均由 Transport 清理临时文件并把已接受的本次上传裁决为失败；严格同步 Runtime 是否以及如何消费该失败由层间结果契约决定，同一节点不能在该 Transport 准入上下文中重提。已提交 update 但最终 `201` 到达 client 前丢失时，server 保留有效 update 并继续接受该准入上下文允许的其他节点；client 报告失败。Transport 不查询 Runtime 状态消除歧义，也不在同轮重连、续传或覆盖。

上传槽位预留后、最终 manifest 提交前发生的 `100 Continue` 写回失败、连接中断、长度或摘要不符、数据文件或 manifest 原子提交失败，都必须使本次已接受的 Transport 提交失败。请求处理流程必须通过同进程可靠结果调用把带原 `round_id` 和 `NODE_ID` 的失败同步交给 Runtime 后才能结束，不能使用允许静默丢弃的通知；失败不建立额外的磁盘结果文件，也不要求 Runtime 从磁盘恢复原始 Transport 错误。Runtime 根据结果身份决定是否推进匹配轮次；无法可靠接收并分类结果或无法持久化必要的轮次失败时升级为 Runtime 致命错误，但不能改变已经成立的 Transport 失败事实。两份文件已经原子提交后，Runtime 的收齐记账或状态持久化失败属于上层轮次失败，不改变该 update 的 Transport 成功事实，也不删除或覆盖已提交文件。Transport 根据自己持有的准入上下文独立裁决后续请求，不查询 Runtime 终态。若该失败使 Runtime 请求取消同时运行的下行 UFTP，Transport 独立裁决取消与 UFTP 自然完成的先后关系；UFTP 原结果和取消结果都不能覆盖最初轮次错误。

## 并发、grant 和连接等待

同一轮中，不同参与 `NODE_ID` 的 HTTP request 可以同时取得各自的 `(round_id, NODE_ID)` 上传预留并进入已接受状态。server 可以并发解析连接和 headers，并且必须把轮次验证、参与集合验证、同键首次提交和预留创建纳入同一次原子操作；只有预留成功的请求尝试返回 `100 Continue`。同一键的活动竞争和提交后重复请求立即返回 `409 Conflict`，仍属未被 Transport 接受，不得排队或自动重试。

活动上传的总数由每个已接受操作的冻结参与集合约束：一个参与节点最多持有其一个键的预留，因此单个轮次上下文最多同时有该参与集合大小的活动上传。Transport 不硬编码演示拓扑的节点数量，也不使用全局单上传槽位。每个已接受操作使用自己的上下文快照完成接收、临时文件清理、manifest 提交和失败回调；一个操作的成功、失败或资源释放只影响其自身预留，不能取消、释放或重新准入其他活动操作。新准入上下文安装后，已接受的旧操作仍按其旧 `round_id` 和 `NODE_ID` 独立裁决并报告。

HTTP 请求和 TCP 连接可以跨多个链路层 grant 保持；grant 到期只暂停空口发送，连接、请求和暂存文件继续保持，后续 grant 从同一 body 继续。Transport 依赖[链路层空口调度与反压](链路层空口调度与反压.md#link-contract)提供发送机会和有限队列，不调用链路层 Token API。

HTTP/TCP 的连接建立、等待 `100 Continue`、body 读写和最终响应都必须设置有界的连接与 I/O 等待期限，使每次请求 operation 最终形成成功或失败裁决。默认连接和 I/O timeout 为 10 秒；该期限必须覆盖 Token 调度下的最坏正常轮换等待，不能把正常 grant 排队误判为 Transport 无响应。具体数值由实现和目标运行环境冻结，不与 Runtime 轮次等待混同。优先使用 HTTP 库、socket 或事件循环提供的连接、读写和响应 timeout，不要求新增独立 watchdog 组件。

一条连接最多承载一个 PUT，client 使用 `Connection: close`，不使用 pipelining。HTTP client 库必须禁用全部自动重试；一次 `submit_update(...)` 只建立一次连接、发送一次 PUT。connect、body、最终响应缺失和全部 `4xx`/`5xx` 都是明确失败，不能自动重发。

server 若已提交 update 但响应写回失败，只记录自身可观测的 `response_write_completed` 或 `response_write_failed_after_commit`，不声称 client 已收到 `201`。client 记录 `final_response_received`、`final_response_missing` 或 `final_response_invalid`；两端使用 `round_id` 与 `NODE_ID` 关联排障。

## Runtime 交付边界

Transport 只交付文件传输结果、完整性结果和结构化传输错误。成功结果以最终 manifest 为持久权威事实，进程内通知只负责唤醒；正式提交失败则通过同进程可靠结果调用同步上报，不额外持久化 Transport 失败文件。Runtime 决定轮次状态、下行完成屏障、结果消费和严格同步失败传播；Transport 不复制或改写 Runtime 的状态机。跨层完整流程见[系统分层与跨层契约](系统分层与跨层契约.md#cross-layer-contracts)。

## 后续扩展（非当前版本规范）

本节整体不属于现行规范，不进入 v8 spec。候选必须在未来版本中重新评审并激活为现行规范后，才能形成实现要求。

### UFTP 本机控制与结构化终态增强

- **状态**：候选方向。
- **保留理由**：原生 UFTP 的阶段取消表现和 status 文件观测能力有限，已有源码调查结论可避免未来重复研究。
- **触发条件**：真实测试证明现有原生 UFTP 计时、专属 status file 或 `TERM -> 停止宽限期 -> KILL -> reap` 无法满足已经确认的取消或观测需求。
- **候选方向**：保持 UFTP 子进程隔离，只研究本机控制通道和结构化终态增强。
- **必须重新裁决**：是否修改 UFTP 源码、本机控制协议、终态字段、兼容与部署方式，以及 Transport 内部接口。
- **当前不承诺**：不修改 UFTP wire protocol，不增加 `ABORT` ACK，也不预先冻结任何本机控制接口或结构化结果 schema。
