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

## 算法作业与入口

一个算法入口的一次执行表示该角色参与的一次完整 FL 算法作业，而不是单个联邦学习轮次或单项训练任务。server 和 client 算法入口分别在作业内拥有自己的多轮业务循环：server 决定初始模型、聚合、评估、下一轮模型和停止条件；client 持续等待每轮模型、执行本地训练并提交该轮 update。算法入口正常返回表示该角色的完整算法作业已经结束；角色主进程随后按序关闭该作业使用的 Runtime、Transport 和链路资源并正常退出。算法入口抛出异常表示作业失败，角色主进程仍须完成同样的资源清理，再以失败结果退出。

算法入口是 FL 算法层的最小正式进程内 callable 契约。角色主进程在 Runtime 与 Transport ready 后，把同一 Runtime 实例交给算法入口；算法入口直接调用 Runtime 四接口，不经过验收专用分支、中间 Runner 层、独立算法进程或 Runtime RPC。生产算法和确定性验收 fixture 必须实现同一入口契约，fixture 不是绕过正式调用路径的特殊入口。

角色可执行入口通过必填命令行参数 `--algorithm package.module:callable` 显式定位本次作业的算法函数，并通过必填的 `--algorithm-config /absolute/path.json` 指定独立算法作业配置。角色主进程把配置文件读取为一个 JSON object 后，以 `callable(runtime, config)` 形式在进程内调用算法入口；`config` 字段的语义和严格校验由具体算法拥有。角色入口只校验路径为绝对路径、文件可读、JSON 合法且顶层为 object，不解释算法字段。模型、update、Runtime 四接口参数和结果都不经过命令行。

算法作业 JSON 与角色基础设施 JSON 分离：前者表达轮数、模型输入、算法参数和算法产物位置等作业语义，后者继续只表达链路、Transport、Runtime 工作目录和角色身份。算法入口不能通过约定环境变量、固定隐藏路径或动态导入副作用取得未显式传入的作业配置。

算法入口契约只定义角色、Runtime 和算法作业配置等必要输入以及正常返回或异常结果，不建立插件注册表、App 分发、多租户、多 run、热切换或动态依赖安装机制。Flower 的 `ServerApp`/`ClientApp` 只作为“算法代码通过明确入口使用运行时且不管理底层通信”的职责参考；Flower 的 SuperExec、AppIo 和独立 App 进程不属于 v8。

同一个 Runtime 与 Transport 在作业的全部轮次之间持续复用。单轮结束只结束该轮的 Runtime 状态和按次 Transport operation，不关闭常驻 server HTTP listener、client `uftpd`、Runtime 或链路进程；这些资源只在完整算法作业及其宿主角色服务的生命周期边界按跨层契约处理。

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
