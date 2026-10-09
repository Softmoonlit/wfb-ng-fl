Status: resolved
Type: grilling
Blocked by:

# 定义扫频与集群射频准备流程

## Question

空闲状态下如何复用 Stage 2 已有的 `/api/v1/survey` 与 Stage 3 已验证的 `/api/v1/radio/reconfigure`，把扫频推荐、扁平参数校验、UFTP 速率动态约束、操作者确认、集群级应用、双最终屏障、失败回退和重新就绪组成完整 Web 操作流程；各 Server 状态下哪些射频动作必须由既有 API 和 Web 同时禁用？

## Answer

首版不交付扫频推荐流程。`/api/v1/survey`、候选信道计算和扫频结果展示延期到后续版本；首版不因缺少扫频结果而阻止操作者手动配置。

首版射频准备从 Web 中的扁平参数表单开始，参数为 `channel`、`radio_txpower_dbm`、`downlink_mcs` 和 `uplink_mcs`。操作者先编辑并提交目标配置进行 Server 端校验，再查看一次性的待确认配置摘要，最后明确点击“应用到集群”。浏览器校验只用于交互提示，Server 必须在实际应用前重新校验完整配置；修改任一字段都会使此前的确认失效。

参数处理遵循以下规则：

- 非法或不在允许范围内的参数直接拒绝。
- `downlink_mcs` 导致 UFTP 速率超出安全区间时，显示强告警，并要求第二次明确确认后才允许提交。
- 射频应用不恢复 robust/standard/performance 旧预设，也不通过 Web 校验绕过扁平参数白名单。

只有在 Server 处于空闲状态、全部目标节点在线且就绪、没有活动作业并且资源恢复完成时，才允许应用集群级射频配置。空闲就绪时允许查看、编辑、校验和应用；等待操作者确认时允许重新编辑，但重新编辑会使旧确认失效。射频应用进行中、作业准备/运行/中止、作业结束但资源仍在恢复、节点离线或节点配置不一致时，Web 可以展示当前配置，但不得允许集群应用；`RADIO_ERROR` 或恢复受阻时禁止普通应用流程。状态门禁必须同时存在于 Web 和 REST API，不能只依赖按钮禁用。

集群应用复用 Stage 3 的双最终屏障与 15 秒租约：Server 先校验整份集群配置，Client 接收配置并回报 `RADIO_SWITCH_FINALIZED`；Server 收齐全部节点确认后进入不可逆 commit，并发送 `RADIO_SWITCH_CONFIRMED`；Client 收到确认后解除租约并 ACK。`RADIO_SWITCH_FINALIZED` 只表示提议，不表示应用成功。

commit 前失败统一回退到 157。commit 后确认不完整进入 fail-closed 的 `RADIO_ERROR`，由操作者重新发起恢复，不自动重试或自动修改射频参数。只有所有节点配置一致、租约解除、资源重新进入空闲后，Web 才显示射频准备完成并允许下一次手动配置。成功后不自动重新扫频。

## Comments

### 决策收敛

- 首版扫频推荐延期，后续版本再决定如何组合 `/api/v1/survey` 与推荐结果。
- 首版采用手动扁平参数配置、Server 校验、强告警二次确认和集群级应用。
- 射频操作只允许在全部节点就绪且资源已恢复的空闲状态执行；Web 与 REST API 同时实施状态门禁。
- 复用双最终屏障与 15 秒租约；commit 前失败回退 157，commit 后确认不完整进入 `RADIO_ERROR`。
