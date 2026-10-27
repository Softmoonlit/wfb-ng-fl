# 07: 展示训练结果并从快照恢复作业

**What to build:** 操作者在现有作业详情中查看初始与逐轮评估、固定参与者准备和训练摘要、模型身份以及独立结果和恢复状态；页面重载或管理连接变化后依靠服务器快照恢复事实。

**Blocked by:** 04（[接入 40 MHz 真实单轮训练流程](04-real-single-round-training.md)）。

**Status:** resolved

**测试底座：** [fl00 + fl01、fl02、fl04 四板与已有通信基线](../spec.md#当前四板测试底座与实施进度)。复用已完成票 02 的候选和选择行为。

**规格依据：** [控制台快照与最小结果呈现](../spec.md#implementation-decisions)，用户故事 24、25、37～42。

**交付边界：** 本票交付服务器快照与真实页面的完整呈现流程，可检查受控状态的显示和恢复，当前四板用于实际联动验证。与无线／GPU 实际状态联动交给[关键故障与控制台实机检查](12-fault-and-console-hardware-acceptance.md)，真实五轮结果交给[连续三次五轮训练与最终交付](13-three-run-final-acceptance.md)。候选交互由独立工单负责，不因共享页面文件建立非必要依赖。

- [x] 权威快照含服务实例与版本、冻结入口／配置摘要、初始制品与固定目标、轮数／阶段、模型身份和产物引用，不将服务器原始位置变成 Web 文件授权。
- [x] 展示初始及每轮有限损失、准确率和完成事实；未取得的指标及未判定效果明确缺失／未判定，不用零或成功占位。
- [x] 固定参与者详情保留当前在线事实、准备结果、样本数、优化器步数、训练损失及设备摘要；离线节点不从作业中删除。
- [x] 失联、恢复上线和已有失败事实分别呈现，不推断断电或物理根因，不因重新上线恢复终态作业。
- [x] 执行结果、效果结果、资源恢复及未确认节点分别展示；停止受理不等同资源恢复，附加错误保留阶段、节点和原因。
- [ ] 预留规格确定的有限无线事务结果呈现，尚未发生事务时按事实显示，不伪造全网生效；无线实现接入后沿同一快照契约展示。
- [x] 刷新或同一实例重连从快照恢复当前／最近作业，不依赖事件回放或重发启动；实例变化能让选择草稿按既定规则清空。
- [x] 管理状态过时保留最后画面并暂停启动；HTTP 轮询有效时不因 SSE 单路中断禁用，恢复后使用有效快照，不改写无线节点活性。
- [x] 只增加最小评估表和必要摘要，有限事件辅助说明，不增加曲线、比较、历史数据库或集中大日志，不向 Runtime manifest 加页面字段。
- [x] 完成当前环境可执行的状态序列和真实页面呈现检查，明确受控快照不是无线实测；不新增通用前端状态平台或恢复旧自动回归体系。

## Answer

07 的软件实现已包括 Console/UI、宿主有限训练摘要与参考 update metadata helper，基线为 `e53de00`。宿主在真实聚合后读取服务端实际收到的参考 update，校验身份、轮次、基础模型摘要和准备样本数，将有限训练事实复制到控制台轮次事件与终态 summary。通用算法保持不透明。Console 只投影已有内存事实，不调用 torch、不读 update 文件、不建立缓存指纹层；Coordinator 与共享诊断事件契约保持不变。

[console.py](../../../wfb_ng/fl/console.py) 现在只从内存 raw job/summary facts 投影白名单：初始和逐轮评估、模型 SHA/大小、冻结算法摘要、固定参与者准备报告、逐轮 8-field training metadata、`recovery_missing_nodes`、`recovery_reports`、`server_recovery_complete`、`additional_errors`、`first_failure`、`liveness_events` 和客户端错误。路径、update payload、Runtime source、控制面 source address 和未知字段不会进入 Web snapshot。缺失 training facts 保持“未取得”；`verdict` 缺失保持“未判定”，不会填零或成功。

[static/index.html](../../../wfb_ng/fl/static/index.html) 和 [static/console.js](../../../wfb_ng/fl/static/console.js) 增加最小评估表、固定参与者准备/训练摘要和恢复事实提示，保留已有 HTTP 过时保护、SSE/HTTP fallback、实例变化清空草稿和整页 snapshot 恢复。遗留“文件仿真”页面标题/启动文案已清理为联邦训练表述。

TDD 覆盖受控内存 snapshot 白名单、路径/未知字段隔离、零值指标、超大整数安全失败、未判定/未确认效果、06 recovery facts、页面 VM 渲染和现有选择/连接恢复。真实 Chromium 检查使用现有 loopback 临时 ConsoleHTTPServer、`/snap/bin/chromium --headless --remote-debugging-port=0`、Node 原生 CDP；通过 `Page.loadEventFired`、旧 DOM sentinel 和 HTTP-only（SSE 阻断）检查重载恢复，不会访问真实 9001/9002/9200 或改变无线状态。宿主接线已完成；受控宿主测试与 metadata helper 共 37 项通过，覆盖参考入口、通用入口不透明、事件／summary 副本及失败后保留已取得事实。真实页面使用受控快照，不作为四板训练或射频验收。

## Comments

- 本票由主线统一检查、双轴审查与单独提交；真实四板页面联动由 12 验收。
- 11 可复用 MNIST metadata 的字段／身份格式 validator，但不复用生产聚合的数值求和；Console 不承担 payload 读取，宿主通过参考算法公开 helper 读取。
- 当前 07 证据见 [evidence/07-console-snapshot-ui/README.md](../evidence/07-console-snapshot-ui/README.md)、[宿主接线](../evidence/07-host-metadata/README.md)和[参考 metadata helper](../evidence/07-update-metadata-helper/README.md)。最终独立检查：完整 Console **34 项通过**；宿主与 metadata helper **38 项通过**；五文件类型检查通过；真实 Chromium/CDP 独立运行通过。Standards、Spec 双轴最终均闭合。
