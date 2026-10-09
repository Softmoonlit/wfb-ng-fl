Status: ready-for-agent

# Stage 3: 虚拟机管理下真实射频全流程闭环验收

## 当前测试拓扑与结论边界

本阶段固定使用 1 个 Server VM 与 2 个 Client VM：

- Server：本机 vm0，TUN IP `10.80.0.1`；
- Client 1：SSH 别名 `vm1`，`node_id=1`，TUN IP `10.80.0.11`；
- Client 2：SSH 别名 `vm2`，`node_id=2`，TUN IP `10.80.0.12`。

三台 Linux VM 使用 USB 直通的真实 `rtl88xxau_wfb` 无线网卡，物理接口必须按 `wlx*` 动态发现。管理以太网与 SSH 在验收期间保持可达，用于准备、受控服务管理、指定故障注入和事后取证；被测作业的客户端唤醒、模型下发、update 上传、轮次推进、终态复位和射频租约回退不得依赖 SSH。

本阶段的正式结论术语是**作业运行时无 SSH 依赖**。不得把结果表述为 `air-gapped`、真正断网、纯断网物理环境或无网络运行，因为 WFB/TUN 带内网络与以太网管理平面都实际存在。

## 参考资料

[Stage 2 三机无 SSH 运行时自治验收指南](../../docs/acceptance/stage2三机无SSH运行时自治验收指南.md)是 Stage 3 实施与现场诊断的重要参考，记录了现有双 daemon 自治运行时已经验证的结论边界、作业级 `TASK_READY` 屏障、空闲链路与任务链路所有权、作业终态复位、运行期 SSH 禁止事项、故障诊断顺序和资源清理方法。Stage 3 应复用其中仍适用的经验，避免重新引入已解决的生命周期与链路问题。

该指南属于 Stage 2，不是 Stage 3 的验收基线、规格或通过依据。其 4 MiB、人工 `systemd-run`、终态前沙箱取证等内容不得直接套用为 Stage 3 要求；两者不一致时，以本规格及 Stage 3 工单为准。

## Problem Statement

Stage 2 已在 vm0、vm1、vm2 上完成 4 MiB、两轮 `sync` 的真实射频自治闭环，证明 Server Daemon 能通过带内 UDP 发布任务，Client Daemon 能自主派生 RoleService，UFTP/HTTP 数据面能够完成双向传输，并在终态恢复空闲链路。然而该结果不证明以下能力：

1. 双 Client 40 MiB 制品在真实射频下的容量闭环；
2. Client 沙箱清理后仍可取得完整的节点本地审计证据；
3. 射频两阶段切换在真实 daemon、真实网卡和确定性单侧丢包下能够按 15 秒租约回退；
4. 一套机械化入口能严格区分服务准备、正常作业、受控故障、证据收集、验证和停服清理。

当前 Client Daemon 在终态立即删除 `job_<job_id>` 沙箱，事后无法稳定取得 RoleService、UFTP 和 Runtime 的关键小型证据。Server Daemon 也尚未通过正式本地 REST 暴露人工确认的射频重配入口。因此不能仅靠手工命令或现场截图完成本阶段。

## Goals

1. 增加 Client 终态前的小型审计证据持久化，保留日志、配置、结果 JSON 和 manifest，不复制 40 MiB 模型或 update 本体。
2. 增加仅在 Server `IDLE` 状态接受的人工确认射频重配 REST 入口。
3. 交付单一 Stage 3 验收执行器，完成 preflight、安装、服务启动、40 MiB `sync` 作业、射频租约回退、证据收集、机械验证和停服清理。
4. 在当前三机拓扑上形成一次绑定提交、配置和 `run_id` 的正式通过归档；失败尝试同样保留并使用新 `run_id` 重跑。

## 复用边界

Stage 3 是对 Stage 2 自治运行时的增量完善，不另建第二套 Server/Client Daemon、控制面、RoleService、Coordinator、Runtime 或 UFTP/HTTP 数据面。现有作业发布、`TASK_READY` 屏障、Client 自主派生 RoleService、空闲链路与任务链路所有权切换以及作业终态复位必须直接复用；实现改动只用于补齐本规格明确要求的节点 evidence、射频可靠最终屏障、作业参数贯通和验收可观测性。

Stage 2 三机验收指南是上述现有运行时的人工开发验收方法，不是可直接裁决 Stage 3 的机械化执行器。现有 `tests/fl_runtime/issue41_fl_runtime_loop.sh` 及其 Python helper 已沉淀真实硬件预检、独立构建安装、确定性 fixture、SHA-256、资源核查和失败归档能力；Stage 3 实现前必须先识别并直接调用或小范围提取这些通用能力，禁止无理由复制实现。只有在现有 helper 与 Issue #41 的 SSH RoleService 编排、五分区目录或旧结论模型耦合且无法保持清晰接口时，才编写 Stage 3 专用逻辑。

Stage 3 不得复用 Issue #41 通过 SSH 启动 Client RoleService/Runtime、搬运模型或 update、促成作业成功的编排路径，也不得沿用其五分区 envelope 和 validator 结论语义。独立 Stage 3 envelope 与 validator 表示证据结构和裁决规则独立，不表示重写底层运行时或重复实现通用验收工具。

## Fixed Configuration

正式运行固定使用以下配置，不允许执行器静默改写：

| 字段 | 值 |
| --- | --- |
| `channel` | `157` |
| `radio_txpower_dbm` | `12` |
| `downlink_mcs` | `3` |
| `uplink_mcs` | `6` |
| `uftp_rate_kbps` | `15000` |
| 频宽 | `HT40+` |
| Guard Interval | `Short GI` |
| FEC | `8/14` |
| WFB 调度 | `120 ms` grant / `10 ms` guard |
| 模型与每节点 update | `40 MiB` |
| 作业模式 | `sync` |
| 轮数 | `2` |
| 目标节点 | `[1, 2]` |
| Coordinator 单轮 timeout | `120 s` |
| 两端 Transport 单次 I/O timeout | `120 s` |
| `live_observation` | `true` |
| REST 接受到 Coordinator 终态 | `<= 400 s` |
| 服务启动到 node 1/2 `IDLE/READY` | `<= 120 s` |
| 射频租约 | `15 s` |
| 租约触发回退后恢复 `IDLE/READY` | `<= 20 s` |

模型和节点 update 必须通过 `wfb_ng.fl.issue41_fixtures` 生成；禁止在 shell 中另写随机或临时 fixture 生成器。确定性算法使用正式算法入口和 Runtime 四接口，但只复制节点特定 update 并保持聚合模型内容不变，因此只能称为**确定性占位训练/聚合**。

## Solution

### 1. Client 节点本地审计证据

Client Daemon 在删除作业沙箱前，把白名单内实际存在的小型文件复制到 `/var/lib/wfb-ng-fl/evidence/<job_id>/`。白名单包括：

- `role_service.log`；
- `uftpd.log`；
- RoleService 基础设施配置；
- 算法配置与算法结果 JSON；
- observation JSONL；
- Runtime 生成的模型/update manifest 与轮次状态 JSON。

归档必须遵守以下规则：

- 使用临时目录完整写入并生成 `evidence_manifest.json` 后原子重命名；
- manifest 记录 schema、`run_id`、`job_id`、`node_id`、运行时提交、lifecycle outcome、相对路径、大小和 SHA-256；`run_id` 来自受校验的任务字段，运行时提交必须直接读取安装包内不可被运行配置覆盖的 build identity 资源，禁止采用请求方自报提交或合并后的全局配置值；
- 只允许规格白名单内的小型文件，禁止复制 `model.bin`、`update.bin` 或其他大文件本体；
- 目标目录已存在时拒绝覆盖；
- 归档失败时保留原沙箱并记录明确错误，但仍须回收任务进程、恢复空闲链路并回到待命；
- Coordinator 成功不能覆盖证据失败，正式验收校验器必须把缺失或无效 evidence 判为失败；
- 不自动过期删除，由操作者显式清理。

通用归档器只复制白名单内实际存在的文件，因为失败发生阶段不同。Stage 3 正常成功场景另行强制要求 RoleService/UFTP 日志、两份配置、算法结果、observation JSONL、两轮状态及模型/update manifest 全部存在；validator 必须按 lifecycle outcome 使用确定的必选文件矩阵，不能把应有文件缺失降级为“可选”。

SHA-256 只证明完整性，不构成防恶意篡改、签名或不可抵赖能力。

### 2. 人工确认的射频重配 REST

Server Daemon 增加：

```http
POST /api/v1/radio/reconfigure
Content-Type: application/json
```

请求最小形态：

```json
{
  "patch": {"channel": 149},
  "target_nodes": [1, 2],
  "confirmed": true
}
```

契约如下：

- 仅在 Server `IDLE` 且目标节点全部 `IDLE/READY` 时接受；
- `confirmed` 必须严格为 JSON boolean `true`；
- patch 只接受现行射频参数白名单并复用既有严格校验器；
- 执行期间 Server 状态为 `SWITCHING_RADIO`，拒绝新作业、扫频和第二次重配；
- 调用同步等待协议形成 `finalized`、`rolled_back` 或 fail-closed `radio_error` 终态；
- `RADIO_SWITCH_FINALIZED` 只表示最终提交提议，不再让 Client 立即解除租约。Client 保持看门狗并回复 session-scoped `RADIO_SWITCH_FINALIZED_ACK`；Server 收齐全部目标节点 ACK 前仍可安全回退；
- Server 收齐 FINALIZED ACK 后原子进入不可逆 committed 状态，此后不得因确认消息发送失败而回退。Server 重发 `RADIO_SWITCH_CONFIRMED`，Client 只有收到它才解除租约并回复 `RADIO_SWITCH_CONFIRMED_ACK`；REST 只有收齐全部 CONFIRMED ACK 才返回 `finalized`；
- 最终提议 ACK 超时发生在不可逆 commit 前，必须回退 157；不可逆 commit 后若无法收齐 CONFIRMED ACK，则 Server 保持新信道并进入 `RADIO_ERROR`，由仍持有租约的 Client 回退/寻频自愈，绝不能形成虚假的 finalized 或 rolled_back；
- 结果包含 `session_id`、上述终态、失败阶段、未响应节点和协议结束后的生效协议配置；
- REST 与控制协议共同形成异常安全事务：底层始终在 `finally` 清理 session/ping/watchdog 临时状态；不可逆 commit 前的广播、信道/功率应用或 FINALIZED 提议任一步骤异常，都必须尝试把 Server 回退到基准信道并广播 abort，由 Client 租约完成自愈；不可逆 commit 后的异常不得回退，必须保持新信道并进入 `RADIO_ERROR`；
- 预期协议失败或已确认回退后恢复 `IDLE`；如果 Server 硬件回退本身失败或无法确认，则进入 fail-closed `RADIO_ERROR`，拒绝新作业、扫频和后续重配，绝不能伪装为 `IDLE`；
- REST 适配层把既有 `RadioSwitchResult` 显式映射为稳定 JSON，并在同一状态临界区同步 `ServerDaemon.radio_config` 与 `ControlPlaneServer.active_radio_config`，`GET /api/v1/status` 不得报告旧配置；
- `SWITCHING_RADIO` 必须同时进入 REST handler 与 daemon 内部方法的扫频互斥门禁，不能只依赖单层 HTTP 检查；
- 预期成功或协议回退都确定性退出 `SWITCHING_RADIO`；不可确认的硬件异常按前述规则进入 `RADIO_ERROR`；
- 调用方不能覆盖协议内部 timeout；
- REST 只是本地人工操作入口，不允许后台自动扫频后静默切信道或改频宽。

请求非法返回 `400`，状态冲突返回 `409`；已接受并完成但发生协议回退属于可审计的 operation 结果，不得伪装成 finalized 成功。

### 3. 作业参数贯通与终态一致性

`POST /api/v1/jobs/start` 必须要求并严格校验 `run_id`、`io_timeout_seconds` 与 `live_observation`，缺失任一字段即拒绝请求；校验后把它们依次贯通到 Server 作业状态、ServerRole、`TASK_ANNOUNCE`、ClientJobConfig 和 Client RoleService：

- `run_id` 与 `job_id` 使用严格路径安全标识并贯通到 Client evidence manifest；Stage 3 每次重跑生成全新的二者，validator 将任务 `run_id`、唯一 `job_id`、Server envelope 与两端 evidence 做等值绑定；

- Stage 3 固定 `io_timeout_seconds=120`；它与 `round_timeout_seconds=120` 是两个独立门限，不能相互代替；
- ServerRole 和两端 RoleService 生成配置必须回读为 120 秒，禁止继续使用 10 秒默认值却在 envelope 中声称 120 秒；
- Stage 3 固定 `live_observation=true`，Client 不接受 Server 提供任意 observation 绝对路径，而是在本作业沙箱内生成固定文件；
- 生成的 Server/Client 配置摘要必须进入 run envelope 和 evidence 交叉校验；
- 人工 `jobs/abort` 与自然完成、Coordinator 失败使用同一 Server 终态收口：关闭 Coordinator/ServerRole、重置持久链路、广播带 `job_id` 的终态并恢复 `IDLE`。

### 4. 唯一正式验收入口

新增 `tests/fl_runtime/stage3_vm_physical_loop.sh`，只提供以下正式阶段：

```text
preflight
install
start-services
run-sync
run-radio-recovery
collect
stop-services
validate
run-all
```

`run-all` 必须严格按前述顺序串联。`collect` 先保存作业终态与仍在运行的空闲资源状态；`stop-services` 停服并把停服后的进程/TUN 核查追加到同一归档；`validate` 最后同时裁决作业终态和停服终态。失败 trap 也必须按“清理故障规则 → 停止受管服务 → 追加失败现场 → 生成失败分类”收口，不得删除失败归档。

执行器可以复用 Issue #41 的 fixture 与通用 SHA-256/manifest 能力，但不得继承其 SSH RoleService 编排、五分区或旧目录假设。Stage 3 使用独立 envelope 和 validator，默认归档到 `tests/logs/<run_id>/`，并允许通过单一环境变量覆盖归档根目录。

### 5. SSH 使用边界

所有 SSH 命令、时间、目标节点、退出码和用途必须写入 orchestration log。

- `preflight/install/start-services`：允许同步代码、安装当前提交构建的制品、配置身份文件、生成 fixture，以及启动正式 systemd daemon unit。
- `run-sync`：从 REST 请求被接受到 Coordinator 终态期间，Stage 3 执行器不得发起或维持 vm1/vm2 SSH 连接；禁止 SSH 启动作业、复制模型/update、补发控制消息、重启链路或修改节点状态。该非敌对受信任验收只能机械证明执行器自身没有 SSH 操作，不宣称能够排除机器外部的其他管理员会话。
- `run-radio-recovery`：只允许执行规格固定的故障规则安装、观察和撤销命令；禁止通过 SSH 恢复信道、重启 daemon 或伪造协议消息。
- `collect`：允许收集本地持久证据、journal、作业终态和空闲资源状态；
- `stop-services`：允许通过 systemd 停止受管单元，并把停服后的进程/TUN 核查追加到归档；
- `validate`：只读取已封口归档，不再改变被测系统。

### 6. 正常 40 MiB 场景

1. Preflight 严格确认三端提交一致、工作树干净、`wlx*` 网卡存在、驱动正确、USB xHCI 正确、身份配置合法、端口无冲突且无旧进程/TUN。
2. `start-services` 通过正式 `wfb-fl-server-daemon.service` 和 `wfb-fl-client-daemon.service` 启动服务；不使用 `systemd-run`。
3. 120 秒内从 Server REST 观察到 node 1/2 均为 `IDLE/READY`。
4. Server 本地向 `POST /api/v1/jobs/start` 提交两轮、40 MiB、`sync` 作业。
5. 作业观察窗口内执行器不连接 vm1/vm2；执行器只轮询 Server 本地状态和协调摘要。
6. 两轮均要求 node 1/2 提交有效 update，无掉队节点；第 2 轮输入模型 SHA-256 必须等于第 1 轮聚合输出模型 SHA-256。
7. 作业终态后 Server 与 node 1/2 必须恢复 `IDLE/READY`，Client evidence 目录必须已原子形成。

### 7. 射频租约回退场景

该场景只在正常作业结束且全集群恢复空闲后执行：

1. 通过 SSH 在 Client 2 的空闲 TUN 输入路径预置精确 payload 匹配规则，只丢弃 `NEW_CHANNEL_PING` 与 `RADIO_SWITCH_FINALIZED`，不得丢弃 PREPARE、COMMIT、heartbeat 或普通数据；
2. Server 本地调用 `/api/v1/radio/reconfigure`，人工确认从 157 切换到 149；
3. Client 2 接收 COMMIT 并执行切换，但因匹配规则不能续租或收到 finalized；
4. Server 未收齐全部 `COMMIT_SUCCESS`，协议结果必须为 `rolled_back` 并回到 157；
5. Client 2 必须由 15 秒租约超时自行回退 157，禁止 SSH 直接设置信道；
6. 从租约触发回退起 20 秒内，node 1/2 都必须恢复 `IDLE/READY`；
7. 观察到自动回退后撤销故障规则；执行器使用 trap 保证异常退出时也清理该规则；
8. 校验 journal 中的 `session_id`、PREPARE/COMMIT、Server 回退、Client 租约超时和最终拓扑因果链。

Preflight 必须先证明远端内核支持所用 payload matcher，并用无副作用探针验证规则可安装和删除。固定 sleep 不能替代状态、journal 或接口结果门禁。

## Acceptance Criteria

一次正式通过必须同时满足：

- 三端提交、工作树、配置和 unit 摘要与 run envelope 一致；
- 正常场景从 REST 接受到 Coordinator 终态不超过 400 秒；
- 两轮状态均为成功，`committed_nodes == [1, 2]` 且 `dropped_out_nodes == []`；
- 每轮模型与两份 update 都严格为 40 MiB，manifest 与实际 SHA-256 一致；
- 跨轮模型 SHA-256 连续性成立；
- Stage 3 执行器的 orchestration log 证明正常作业观察窗口没有 vm1/vm2 SSH 操作；
- 两个 Client evidence manifest 均完整，正常成功场景的 RoleService/UFTP 日志、两份配置、算法结果、observation、两轮状态与模型/update manifest 全部存在，且未覆盖、未混入其他 `run_id` 或 `job_id`；
- Server/Client 生成配置回读均为 `io_timeout_seconds=120`、`live_observation=true`，不能只校验请求值；
- 正常作业终态只保留 daemon、空闲 `wfb_v6_uplink` 和空闲 TUN，不遗留 RoleService、UFTP 任务进程或任务态资源；
- 射频场景确实形成 157→149 COMMIT、Server 协议回退、Client 租约超时回退和最终 157 上的 `IDLE/READY`；
- 故障规则最终不存在；
- `stop-services` 后三端不遗留 daemon、RoleService、`wfb_v6_uplink`、UFTP 进程或 `fl-s`/`fl-cN` TUN；
- validator 以 `passed` 终态结束，任何证据不足都不能降级为告警通过。

原始空口丢包和 FEC 恢复量只作为诊断指标，不设置必须大于 0 的下限，也不要求落入 `1%~2%`。最终文件完整性、协议终态和时间门限是硬门禁。

## Failure and Retry Policy

- 每个目标提交只要求一组完整通过的正常场景与射频场景，不要求连续多次成功；
- 任一失败尝试都必须保留原始归档与机器可读失败分类；
- 修复后必须使用新的 `run_id` 和 `job_id`，不得覆盖、拼接或修改旧归档；
- 三端提交不一致、工作树不干净、无线接口缺失或 Client 证据目录冲突时必须在 destructive action 前 fail closed；
- `install` 必须在 vm0 的临时独立 clean build worktree 中对目标提交执行 `make deb`，选择唯一生成的 Debian 包，记录包 SHA-256，将同一包复制并安装到三端，再核对 `dpkg-query`、console entry point、systemd unit 和 `/usr/bin/wfb_v6_uplink` 均来自该制品；禁止污染或借用三台运行工作树进行打包，禁止沿用不安装 daemon unit 的 `make install_v8`，也禁止启动机器上无法绑定到本次包摘要的遗留二进制；
- 正式硬件运行后若后续提交不改变运行时二进制、协议逻辑或验收器语义，可按现行证据复用规则判断是否需要重跑。

## Documentation Deliverables

实现前必须同步完成：

- 在 `CONTEXT.md` 固化“作业运行时无 SSH 依赖”“作业级就绪屏障”和“节点本地审计证据”；
- 在权威设计中纳入外层 daemon、空闲链路与任务链路所有权、RoleService 沙箱和 `TASK_READY`；
- 新增 ADR-0015，记录 Client 终态前持久化小型审计证据、排除大文件本体及失败语义；
- 更新 ADR 索引与 Stage 3 本地实施工单。

## Out of Scope

- 管理网物理隔离、拔除网线或真正 air-gapped 运行；
- 三台 VM reboot、daemon 开机自启或冷启动自治；
- 真实机器学习训练、FedAvg 数值计算、评估或收敛；
- `semi_async`、`async` 的真实硬件协同语义；
- 多于两个 Client 的实体容量验证；
- 普通运行期干扰触发自动跳频；
- 自动扫频后自主切换信道或频宽；
- `wfb-fl-core` CLI、Web 控制台和生产鉴权；
- 长期历史归档浏览、签名、加密或不可抵赖存储。
