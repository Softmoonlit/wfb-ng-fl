# FL Runtime 轮次与文件契约

<a id="runtime-contract"></a>

## 所有权

本文档唯一拥有 FL Runtime / SDK 的四个本机编程接口、轮次上下文、参与集合、manifest、托管文件、状态机、并发门禁、原子持久化、完成结果和结构化错误。它不拥有 UFTP/HTTP 字段、TCP 连接调度、READY/GRANT 或无线机制。

## 四个本机接口

Runtime 以本地库接口表达严格同步轮次语义：

- server：`publish_model(model_path) -> None`
- server：`wait_for_updates() -> updates_by_node`
- client：`wait_for_model() -> model_path`
- client：`submit_update(update_path) -> None`

接口不接收或返回 `round_id`。Runtime 自动生成、读取和持久化当前轮次上下文；算法层不得传入、覆盖或根据标识查询历史轮次。成功的空返回是命令接口唯一成功信号，失败通过带稳定 `error_code` 的结构化错误报告。

`publish_model(...)` 成功表示已达到下行 Transport 完成边界；`submit_update(...)` 成功表示 client 已收到该提交的最终 `201 Created`。`wait_for_updates()` 成功只返回严格同步本轮的完整 update 映射。

## 轮次身份与生命周期

server 在 `publish_model(...)` 通过 Runtime 内部生成标准 UUID v4 的规范小写字符串作为 `round_id`，并在分配前确认目标轮次目录不存在；碰撞时重新生成，不能覆盖目录。失败轮次不回收、不复用。

`round_id` 用于 manifest、目录、HTTP 资源、日志和错误关联，不进入四接口成功返回值或调用参数。server 和 client 各自严格串行，同一时刻最多有一个当前轮次。`wait_for_updates()`、`submit_update(...)` 没有当前轮次时以 `no_active_round` fail-fast。

server 的开轮顺序如下：

1. 检查 Runtime 致命状态、工作目录锁和当前轮次门禁。
2. 确认不存在活动轮次或未消费的终态结果。
3. 在不分配轮次的前提下解析并固定输入路径，打开文件，确认最终目标是可读取普通文件。
4. 预检成功后在受保护临界区生成 `round_id`、创建目录并原子落盘 `publishing_model` 初始状态。
5. 归档模型、生成 manifest，并把托管文件交给 Transport。

并发竞争时，只有一个调用取得开轮门禁；其他调用在分配标识前以 `operation_in_progress` 失败。已有活动轮次时返回 `round_in_progress`，当前终态结果未消费时返回 `round_result_pending`。这些前序拒绝和预检失败不得访问新调用传入的模型文件，也不得创建轮次目录。预检通过后发生的读取、源文件稳定性、manifest、持久化或传输失败都会形成保留的失败轮次。

server 状态为：

```text
publishing_model -> waiting_for_updates -> succeeded
        |                    |
        +--------------------+-> failed
```

client 状态为：

```text
model_received -> submitting_update -> succeeded
       |                  |
       +------------------+-> failed
```

状态只能沿定义方向推进，终态不能回退。`publishing_model` 期间允许接收本轮参与 client 的提前 update，但下行完成屏障未达成前不能成功。Transport 失败、交付校验失败或重启中断使轮次进入 `failed`。严格同步模式下，正式提交阶段任一参与 client 的确定性失败使整个轮次失败；不能返回部分结果。

## 参与集合和模型 manifest

server 初始化时持有非空默认训练客户端集合，成员是 `NODE_ID`，并且属于链路层静态已知 client 集。启动和开轮时都执行 fail-fast 校验。

每次开轮从默认集合冻结本轮参与客户端集合。该快照同时约束模型发布目标和 `wait_for_updates()` 的收齐集合，不等同于静态已知 client 集，也不承载底层逐包目标。

模型 manifest 使用 UTF-8 严格 JSON，顶层必须为 object，至少包含：

```json
{
  "schema_version": 1,
  "artifact_type": "model",
  "round_id": "550e8400-e29b-41d4-a716-446655440000",
  "size_bytes": 123456,
  "sha256": "...",
  "participant_node_ids": [1, 2]
}
```

只接受整数 `schema_version: 1`。必需字段缺失、重复、类型错误或值域非法时失败；未知字段可忽略，字段顺序无语义。改变既有字段含义或作不兼容变更必须新增 schema 版本，不能静默重定义。manifest 不签名、不加密；`sha256` 只表示文件完整性。

Runtime 通过 Transport 收到“全部预期目标的模型文件交付成功”结果后，才把 `publish_model(...)` 视为成功。单个 client 在本机完整校验模型后可以先返回 `wait_for_model()` 并训练，不需要等待其他 client。

## client 模型接收

`wait_for_model()` 不接收参数，使用部署配置中的本机 `NODE_ID` 和工作目录阻塞等待下一份模型。只有 manifest 合法、模型文件完整性通过且本机在 `participant_node_ids` 中时，才持久化当前 `round_id` 并返回托管 `model.bin` 的规范化绝对路径。

对已出现最终 manifest 的候选交付物，裁决顺序固定为：

1. 严格验证 manifest 结构、schema 和字段值。
2. 判断本机 `NODE_ID` 是否在参与集合中。
3. 仅对目标模型检查数据文件存在性、大小和 SHA-256。

完整合法但不包含本机的模型移入隔离/拒收位置，记录 `model_not_for_this_node`，继续等待；此时不要求数据文件存在，也不读取内容。manifest 无法解析、schema 不支持、参与集合缺失或字段非法时不能推定为非目标模型，当前等待必须以接收错误失败。属于本机但数据文件配对错误、大小或摘要不符时立即失败，不继续等待替代模型。

能够可靠取得合法 `round_id` 时，创建或更新对应 client 轮次并持久化 `failed`；无法取得时只隔离交付物并结束本次调用，后续调用可以继续等待。尚未出现最终 manifest 的临时文件、孤立数据文件和接收中的半成品不触发该错误。

同一 `round_id` 的重复模型若 schema、参与集合、大小、摘要和实际内容完全一致，记录 `duplicate_model_ignored`，不覆盖也不再次返回。关键字段或内容不一致时记录 `round_id_content_conflict`，保留原文件并隔离冲突物；活动轮次失败，终态轮次保持原终态。同一标识不能代表两个内容版本。

同一 client 同时最多一个未完成的 `wait_for_model()`；并发调用以 `operation_in_progress` 失败。成功返回后，在轮次终态前再次等待以 `round_in_progress` 拒绝；进入终态后才可等待新轮次。

## update 提交和 update manifest

`submit_update(update_path)` 从唯一当前轮次上下文读取 `round_id`，不接收轮次参数。Runtime 把输入归档到当前轮托管目录并生成本地 manifest：

```json
{
  "schema_version": 1,
  "artifact_type": "update",
  "round_id": "550e8400-e29b-41d4-a716-446655440000",
  "node_id": 1,
  "size_bytes": 12345,
  "sha256": "..."
}
```

每个 client 每轮最多占用一次真正提交机会。只有状态为 `model_received` 时才允许进入受并发门禁保护的无副作用预检；预检确认固定路径、可读取普通文件且大小不超过 `max_update_size_bytes`。预检失败保持 `model_received`，不占用提交机会，调用方可修正后重试。

预检通过后，Runtime 在开始归档前原子转入 `submitting_update`，这是提交机会的占用边界。之后不能替换路径、取消或同轮重提；归档、manifest、持久化、连接或 HTTP 失败均使本轮 `failed`。已占用提交机会的失败即使发生在发送 body 前也不允许同轮重提。状态为 `succeeded` 或 `failed` 时分别返回 `round_already_succeeded` 或 `round_already_failed`。

client 本地归档和 server 接收都必须独立校验 manifest 与数据文件。client 的 update manifest 不作为独立 HTTP 资源上传；server 根据已验证传输元数据重新生成权威 manifest。

## 文件归档、原子性与目录

模型和 update 的源文件可以位于 `work_dir` 外。输入同时接受绝对和相对路径；相对路径在调用入口按当时工作目录立即解析为绝对路径。最终目标必须是可读取普通文件；目录、FIFO、socket 和设备文件拒绝，符号链接只能指向普通文件。

归档从同一文件描述符读取，并比较平台可用的文件身份、大小和修改时间等元数据；发现归档期间变化则失败。调用方应在调用前完成源文件写入并停止修改，推荐先写临时文件再原子重命名。

固定目录布局如下，不使用调用方原始文件名：

```text
<work_dir>/
  rounds/
    <round_id>/
      model.bin
      model.manifest.json
      round-state.json
      updates/
        <node_id>/
          update.bin
          update.manifest.json
```

client 目录在同一轮下使用 `model.bin`、`model.manifest.json`、`round-state.json`、`update.bin` 和 `update.manifest.json`。

数据文件先写入最终目录旁唯一临时文件，完成大小和 SHA-256 后原子重命名为固定数据名；manifest 再写入临时文件并最后原子重命名。manifest 的最终出现是本地提交标志；临时文件不参与消费或收齐。Transport 必须先写暂存路径，不能把未完成网络接收暴露到最终 Runtime 路径。

模型和 update 不设置最小文件大小；零字节普通文件只要所有声明、实际字节和完整性结果一致即可合法。`max_model_size_bytes` 不在本契约中冻结；`max_update_size_bytes` 是 server 与所有 client 共享的正整数配置。

## 收齐结果和消费

server 只把同时满足以下条件的 update 记为有效：`round_id` 匹配当前轮、本机 `NODE_ID` 属于本轮集合、传输完成且完整性校验通过。每个 `(round_id, NODE_ID)` 只接受第一份有效 update；重复、迟到或未知节点不计入收齐且不得覆盖既有文件。

`wait_for_updates()` 阻塞到下行完成屏障已达成且本轮参与集合全部收齐，或发生明确错误。成功前先持久化 `succeeded`、参与集合和 `updates_by_node`。返回值为只读映射：键是整数 `NODE_ID`，按数值升序稳定迭代，值是规范化绝对 `update_path`；键集合必须与冻结参与集合完全相等。

同一活动轮次最多一个未完成的 `wait_for_updates()`；并发调用以 `operation_in_progress` 失败。接口没有 `timeout`、`min_clients` 或 `allow_partial` 参数。

结果交付到当前调用栈的返回或抛错边界前，在内存中标记为已消费，不写入状态文件。成功或失败结果消费后，下一轮开启前重复调用 `wait_for_updates()` 可以从托管目录和状态记录重建相同结果；成功重读复核 manifest、固定路径、存在性和大小，不再次计算整文件 SHA-256。失败终态重放结构化错误。`publish_model(...)` 直接报告的轮次错误同样视为已交付。

## 状态持久化、重启与工作目录锁

`round-state.json` 使用严格 JSON，至少包含 `schema_version: 1`、`round_id`、`role` 和 `state`；失败状态还包含 `error_code`、`error_message`，update 失败在可判定时包含 `node_id`。它不是实时阻塞指示器、网络进度或文件有效性判据。

每个 Runtime 实例对工作目录持有操作系统排他的跨进程锁；第二个实例必须 fail-fast。server 和 client 使用不同工作目录，同机多个 client 也必须各自独占目录。离线清理工具获取同一把锁后才可清理终态轮次。

进程重启发现非终态轮次时，把它原子改写为 `failed` 并记录 `error_code: "runtime_restarted"`，保留标识、manifest、已归档文件和错误记录，但不恢复等待、传输或提交上下文。成功/失败终态不恢复为新进程当前轮；新进程必须以新的 `round_id` 开始。临时或未校验文件不得恢复为有效交付。

终态目录默认不自动删除。托管路径只承诺在当前部署和工作目录位置下有效，调用方不得原地修改、重命名或删除；清理只能处理可确认终态，不能删除活动轮次。Runtime 不提供在线清理、历史查询或跨进程恢复接口。

## 错误顺序和失败传播

四接口统一先检查 Runtime 致命状态，再检查并发冲突和轮次资格，最后对带文件参数的接口执行路径、文件类型、可读性和大小预检。前序错误成立时不得访问调用方文件。

发生明确错误时，Runtime 尽力先原子写入 `failed`、`error_code` 和 `error_message`，再向调用方报告。状态写入失败本身也是错误；若连失败状态都无法落盘，仍同时报告原始错误和持久化错误。

Transport 把已提交的 update、body 接收失败、响应丢失等结果按其定义的边界交给 Runtime。任何参与 client 在正式 body 接收阶段确定性失败，严格同步轮次立即失败，`wait_for_updates()` 不返回已收齐的部分映射。若失败发生在 `publish_model(...)` 阻塞期间，Runtime 终止正在运行的下行子进程但不让附加退出结果覆盖最初主错误。失败终态不能逆转为成功。

错误至少包含稳定 `error_code`，可在可靠取得时附带 `round_id`、`node_id` 和底层原因。调用方不得依赖可读错误文本控制流程；不冻结具体语言异常类名。
