# FL-算法层设计

## 所有权

本文档唯一拥有 FL 算法层的职责边界和算法调用与 Runtime 之间的语义边界。它不拥有文件传输、轮次状态机、manifest 字段、Token Passing、链路调度或无线物理行为。

## 当前职责

FL 算法层表达同步联邦学习业务语义：

- 服务端准备和发布全局模型。
- 客户端读取 Runtime 托管模型并执行本地训练。
- 客户端生成本轮 update 并提交给 Runtime。
- 服务端从 Runtime 获得本轮完整 update 集合并执行聚合。
- 在业务需要时执行评估，并把评估结果留在算法层语义内。

模型和 update 的内容、序列化格式、框架对象类型以及聚合器内部算法对本层之外保持不透明。算法层只把可读取的模型路径或 update 路径交给 Runtime，不把原始路径写入 Runtime 托管目录。

## Runtime 调用边界

算法层通过 Runtime 的四个本机接口表达文件交换：

- server 调用 `publish_model(model_path)` 发布模型。
- client 调用 `wait_for_model()` 获得 Runtime 托管的模型路径。
- client 训练后调用 `submit_update(update_path)` 提交 update。
- server 调用 `wait_for_updates()` 获得按 `NODE_ID` 索引的 Runtime 托管 update 路径。

算法层不生成、传入或覆盖 `round_id`，不解析底层 HTTP/UFTP 状态，不调用 READY、GRANT 或 Token API。接口成功和结构化错误由 FL Runtime 专题定义；跨层顺序见[系统分层与跨层契约](系统分层与跨层契约.md#cross-layer-contracts)。

## 边界结果

- `wait_for_model()` 返回只读使用的托管模型路径后，客户端可以开始训练；模型是否已经交付其他客户端不属于算法层判断。
- `submit_update(...)` 成功只表示 client 已收到本节点 update 的 Transport 最终 `201 Created`，不表示 server 已全员收齐、轮次全局成功或聚合已经发生。
- `wait_for_updates()` 成功只在严格同步收齐条件满足时提供完整 update 映射；算法层不自行把部分集合解释为成功。
- 传输失败、文件校验失败或轮次失败由 Runtime 作为结构化错误交付，算法层决定是否终止当前业务流程或显式开启新的轮次。

本专题不为尚未冻结的模型序列化、异步或半同步聚合、迟到策略、停止条件、动态客户端选择或算法框架适配增加默认设计。
