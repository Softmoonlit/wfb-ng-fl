# Transport Backend 传输契约

<a id="transport-contract"></a>

## 所有权

本文档唯一拥有文件交换到具体 Transport 的映射、UFTP/HTTP 资源和元数据、传输完成条件、响应边界和传输失败语义。它不拥有 FL Runtime 的轮次状态机，也不拥有 READY、GRANT、Token Passing 或物理层机制。

## 下行 UFTP

Runtime 把模型文件和 model manifest 交给 UFTP。Transport 根据本轮参与客户端集合建立 closed group，并维护显式的 `NODE_ID -> UFTP` 目标映射；model manifest 不承载 UFTP 目标身份。

UFTP 必须启用 `-q` 并解析 `-S status_file`。进程退出码为 `0` 只是必要条件，不能单独证明全部目标完成。下行成功必须同时满足：

- 每个预期 UFTP client 都有且只有一个 `CONNECT;success`。
- `model.bin` 和 `model.manifest.json` 对每个预期 client 各有且只有一个 `RESULT`，状态均为 `copy`。
- 没有缺失、重复、未知 client、`rejected`、`skipped` 或其他非 `copy` 状态。
- 固定的新轮目录不接受 `overwrite`。

`STATS` 只用于观测，不能替代逐 client、逐文件结果矩阵。UFTP 已报告文件交付成功，不等于 client Runtime 的 manifest、参与集合和 SHA-256 校验成功；client 必须独立校验。

下行反馈机会由链路层调度。Client 可以先行训练和提交 update，但 Transport 只有确认全部预期目标的模型文件结果后，才向 Runtime 交付下行完成结果。

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

server 只接受当前活动轮、合法 UUID、合法 `node_id`、静态已知 client、本轮参与集合和首次 `(round_id, NODE_ID)` 提交。TCP peer IP 可写入排障日志，但不参与接收裁决。受信任部署边界不构成密码学身份认证；本契约不引入 bearer token、TLS 或 mTLS。

## 元数据和准入

`Content-Length` 必须存在、为已知值且不超过正整数 `max_update_size_bytes`；不接受 chunked body。server 与所有 client 使用同一配置值，运行中不热更新。client 在归档后、发起请求前先按上限 fail-fast，server 在允许 body 前再次检查。

`Content-Digest` 必须是唯一、语法正确且算法为 `sha-256` 的标准字段。client 使用归档得到的同一 32 字节摘要编码为 base64；本地 manifest 仍保存 64 位小写十六进制。server 流式读取 body 时独立计算摘要并比较。摘要用于交付完整性，不构成敌对环境认证。

server 先完成 method、路径、身份、轮次、参与集合、重复提交、媒体类型、长度、摘要语法、大小和存储检查，全部通过才返回 `100 Continue` 并读取 body。检查失败只拒绝本次请求，当前轮次继续等待该 client 的有效首份提交；client 不自动重试。

client 固定使用 `Expect: 100-continue`，在收到 `100 Continue` 前不得写入任何 body。若先收到最终 `4xx`/`5xx`，或等待中间响应超时，直接失败，不降级为先发 body。TCP SYN 和 headers 已足以进入 TUN 上行队列，不需要 body 才能触发链路层 READY。

## 接收和提交

通过准入后，server 把 body 流式写入最终目录旁唯一临时文件，同时累计实际长度和 SHA-256。只有实际长度、声明长度和摘要全部一致时，才生成权威 `update.manifest.json`，按“数据文件先落位、manifest 最后原子落位”提交到：

```text
<work_dir>/rounds/<round_id>/updates/<node_id>/
  update.bin
  update.manifest.json
```

首次有效 `(round_id, NODE_ID)` 提交在两份文件和 server 收齐快照都持久化后返回无 body 的 `201 Created`。不要求 `Location`。`200`、`204` 或其他状态码都不表示成功。已有有效同键资源返回 `409 Conflict`，不覆盖、不按幂等成功处理；只有临时文件时可以清理或隔离后重新成为首次提交。

Transport 只向 Runtime 交付同时满足以下条件的有效 update：`round_id` 匹配当前活动轮次、`NODE_ID` 属于本轮参与集合、传输完成、完整性校验通过。迟到、未知节点和重复提交拒收并留痕，不计入收齐。

HTTP server 不通过公开 `wait_for_updates()` 启动或停止；它随 server Runtime 持续监听。client 可以在下行仍处于发布状态时提前提交，Transport 持久化有效结果并报告其状态，但下行完成屏障之前不能使 Runtime 成功。

## 失败和状态码

固定错误映射如下：

| HTTP 状态码 | 使用场景 |
| --- | --- |
| `400 Bad Request` | URL、header、摘要格式或请求结构非法 |
| `404 Not Found` | `round_id` 不存在或不是当前活动轮次 |
| `409 Conflict` | `(round_id, NODE_ID)` 已有有效 update，或竞争请求遇到活跃 body |
| `411 Length Required` | 缺少 `Content-Length` |
| `413 Content Too Large` | update 超过配置上限 |
| `415 Unsupported Media Type` | `Content-Type` 不是 `application/octet-stream` |
| `422 Unprocessable Content` | body 长度或 SHA-256 与声明不一致 |
| `500 Internal Server Error` | manifest、轮次状态或其他内部处理失败 |
| `507 Insufficient Storage` | 磁盘空间不足或落盘失败 |

错误响应使用 `Content-Type: application/json; charset=utf-8`，body 不超过 16 KiB，至少含稳定 `error_code` 和仅用于日志排障的 `error_message`。不得泄露本地路径、堆栈、命令行或临时文件名。client 同时依据状态码和 `error_code` 判断失败；错误 body 缺失或解析失败不能升级为成功。

连接在 `100 Continue` 前中断且未开始 body 时，清理请求并让轮次继续等待。已返回 `100` 并开始 body、但尚未提交时，清理临时文件并使严格同步轮次失败，不等待该节点重提。已提交 update 但最终 `201` 到达 client 前丢失时，server 保留有效 update 并继续等待其他节点；client 报告失败。Transport 不查询状态消除歧义，也不在同轮重连、续传或覆盖。

正式 body 接收期间发生连接中断、长度/摘要不符、落盘、manifest 或收齐状态持久化失败，都必须使轮次进入明确失败。未获得 `100 Continue` 的同轮后续请求在轮次失败后返回 `404` 和 `round_failed`，不读取 body。附加的 UFTP 退出、终止或强制结束结果不能覆盖最初 update 错误。

## 并发、grant 和 watchdog

同一 server 同时只允许一个 HTTP request 进入 body 接收和磁盘写入阶段。server 可以解析少量连接和 headers，但只有唯一活跃请求返回 `100 Continue`。另一个请求立即返回 `409`、`upload_in_progress`，不得排队或自动重试。

HTTP 请求和 TCP 连接可以跨多个链路层 grant 保持；grant 到期只暂停空口发送，连接、请求和暂存文件继续保持，后续 grant 从同一 body 继续。Transport 依赖[链路层空口调度与反压](链路层空口调度与反压.md#link-contract)提供发送机会和有限队列，不调用链路层 Token API。

等待 `100 Continue`、最终响应和连接建立的 watchdog 必须覆盖 Token 调度下的最坏正常轮换等待，不能把正常 grant 排队误判为 Transport 无响应。具体数值由实现和目标运行环境冻结，不与 Runtime 的轮次等待 timeout 混同。

一条连接最多承载一个 PUT，client 使用 `Connection: close`，不使用 pipelining。HTTP client 库必须禁用全部自动重试；一次 `submit_update(...)` 只建立一次连接、发送一次 PUT。connect、body、最终响应缺失和全部 `4xx`/`5xx` 都是明确失败，不能自动重发。

server 若已提交 update 但响应写回失败，只记录自身可观测的 `response_write_completed` 或 `response_write_failed_after_commit`，不声称 client 已收到 `201`。client 记录 `final_response_received`、`final_response_missing` 或 `final_response_invalid`；两端使用 `round_id` 与 `NODE_ID` 关联排障。

## Runtime 交付边界

Transport 只交付文件传输结果、完整性结果和结构化传输错误。Runtime 决定轮次状态、下行完成屏障、结果消费和严格同步失败传播；Transport 不复制或改写 Runtime 的状态机。跨层完整流程见[系统分层与跨层契约](系统分层与跨层契约.md#cross-layer-contracts)。
