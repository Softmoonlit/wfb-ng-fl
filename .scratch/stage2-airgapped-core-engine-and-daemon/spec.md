Status: ready-for-agent

# Stage 2: 无网络环境下的无 SSH 核心协同引擎、Client 常驻服务与射频感知

## Problem Statement

在现有的三机联邦学习实现中，Server 依赖集中的 SSH 远程网络编排（通过带外以太网管理网）来探测客户端硬件、下发运行配置、拉起角色服务以及收集日志。然而在真实的无外网现场演示和物理仿真环境中，节点之间根本没有管理以太网，Server 无法通过 SSH 连接 Client1 与 Client2。

操作者若在每台机器终端上手动执行脚本、配置网卡与启动服务，极易因参数不一致（如误设具有驱动致命越界缺陷的信道 161、配错带宽导致解调失败、或将 UFTP 发送速率设得过高击穿内核 TUN 缓冲导致静默丢包与重传雪崩）而使演示中断。在没有任务运行的空闲期，Server 缺乏常驻进程持续监听心跳，导致外部 Web 面板或 CLI 无法获知客户端实时在线拓扑；而在信道切换过程中，若部分客户端丢包未完成切换，极易引发信道脑裂（部分节点在目标信道，部分节点在原信道），导致节点永久孤儿化失联。此外，原架构将任务收齐语义死锁在全员必须同时在线的强同步模式，无法支持半异步或异步联邦学习实验。

因此，系统急需在脱离 SSH 依赖的前提下：
1. 在 Server 侧提供开机常驻的协同守护引擎（`wfb-fl-server-daemon`）并暴露统一本地 IPC；
2. 在 Client 侧提供开机自启的常驻守护服务（`wfb-fl-client-daemon`），采用子进程沙箱隔离每一次算法作业生命周期；
3. 建立纯粹、极轻量的对称 UDP 控制信令平面（下行广播、上行单播），与大文件数据平面（UFTP 下行、HTTP PUT 上行）正交解耦；
4. 建立防信道脑裂的租约式看门狗自愈协议；
5. 建立面向用户的三种联邦学习协同范式（同步、半异步、异步）及纯底层网卡预设与速率区间保护。

## Solution

1. **服务端常驻协同守护引擎与统一本地 IPC (Server Daemon & Local IPC)**：
   Server 部署开机常驻守护服务 `wfb-fl-server-daemon`，开机后独占纳管无线网卡并持续在后台监听客户端心跳，维护全局客户端实时在线拓扑视界（`NodeHorizonRegistry`）。对外暴露本地 Unix Domain Socket 或 Localhost 接口，为 CLI 工具与 Web 后端提供无冲突的常态化状态推流、扫频触发与任务下发通道。

2. **客户端常驻守护服务与子进程沙箱隔离 (Client Daemon & RoleService Sandbox)**：
   Client 部署开机自启 systemd 常驻守护服务 `wfb-fl-client-daemon`。基于本机固化配置文件（`/etc/wfb-ng-fl/node.json`）确定身份与 TUN IP，开机后将无线网卡置为 Monitor 模式并在默认 Channel 157 待命；收到 Server 任务配置后，Daemon 派生独立的 `RoleService` 子进程执行本轮模型接收、占位/模拟训练与参数上传闭环；任务结束后干净回收子进程并恢复待命，严格符合 ADR-0010 单作业生命周期隔离契约。

3. **控制信令面与数据面正交分离的双平面架构 (Dual-Plane Protocol)**：
   - **控制信令面（小 JSON，对称轻量 UDP）**：下行由 Server 向 `10.80.0.255:9000` 广播（任务宣告、信道切换、心跳探针），上行由各 Client 向 `10.80.0.1:9001` 单播（心跳保活、状态汇报、切换回执），零 HTTP/TCP 握手延迟，享底层 FEC 8/14 保护；
   - **数据传输面（40 MiB 大文件）**：保持下行 UFTP 多播分发模型、上行 HTTP PUT 单播上传 update，确保大文件传输的可靠性与流控。

4. **防脑裂租约式看门狗自愈机制 (Lease-based Anti-Lockup Watchdog)**：
   射频信道切换采用“两阶段准备 + 租约式看门狗”协议。Client 切换至目标信道后启用 15 秒心跳租约看门狗；若 Server 发现有节点未上线而放弃切换并退回 157，Server 在目标信道停止发心跳，被孤立 Client 租约耗尽后由看门狗触发硬件回滚，自动切回默认 Channel 157，确保全集群在任何断网异常下均整齐汇合，彻底杜绝信道脑裂。

5. **纯底层网卡参数预设分级与扫频探路 (Radio Presets & Spectrum Survey)**：
   - 固化 FEC 为全局基准 `8/14`；提供 `robust`（MCS 2 HT20）、`standard`（MCS 3 HT40+，默认）、`performance`（MCS 5 HT40+）三档纯网卡参数预设；
   - 扫频探路扫描合法 5 GHz 信道池 `[149, 153, 157, 165]`，**Channel 161 因驱动内核越界致命缺陷作为系统级硬黑名单永久禁选与剔除**；
   - 依据 MCS 承载力标定 UFTP 速率区间（MCS 3 默认 15 Mbps），越界强告警需确认（自动化测试需加 `--force`）。

6. **三位一体联邦学习协同范式 (FL Collaboration Paradigms)**：
   统一支持 `sync`（同步，默认要求全员收齐）、`semi_async`（半异步，设置最小配额 `min_updates`，支持抗掉队者）与 `async`（完全异步，单节点随练随交流水线聚合）。

## User Stories

1. 作为 现场操作者，我希望无论是否有任务正在运行，都能在 CLI 或 Web 上实时查看各客户端的在线状态与信号质量，以便在开始仿真前确认硬件就绪。
2. 作为 现场操作者，我希望在启动仿真前主动执行扫频探路，以便获知当前场地 5 GHz 频段的信道干扰情况与推荐排行。
3. 作为 现场操作者，我希望系统坚决屏蔽已知有驱动致命崩溃缺陷的 Channel 161，以便我绝不会因误选该信道而引发射频失效。
4. 作为 现场操作者，我希望在全频段皆有较强外部干扰时系统输出醒目的风险警告并联动建议 `robust` 预设，以便我在复杂环境下提前做好抗干扰防范。
5. 作为 仿真操作者，我希望只需指定 `robust`、`standard` 或 `performance` 预设名称即可完成网卡射频配置，以便无经验用户无需记忆复杂的 MCS、频宽与 GI 组合。
6. 作为 仿真操作者，我希望选择 `standard` 预设时系统自动带出 15 Mbps 的默认下行速率，以便无需手动反复核算 UFTP 参数。
7. 作为 算法开发人员，我希望能够在推荐区间内微调下行发送速率，以便探索不同发送速度对模型交付效率的影响。
8. 作为 架构维护者，我希望当用户输入的下行速率超出当前 MCS 安全区间时系统输出强风险告警，以便阻止内核 TUN 缓冲被静默打满导致丢包雪崩。
9. 作为 自动化测试执行者，我希望在非交互式脚本中必须显式传入 `--force` 标记才允许越界速率运行，以便在无人工确认时严格 fail-closed。
10. 作为 现场维护人员，我希望两台 Client 物理机开机后能自动启动常驻守护服务，以便无需通过 SSH 或接显示器即可完成客户端初始化。
11. 作为 Client 守护进程，我希望在开机后自动读取本地 `/etc/wfb-ng-fl/node.json` 确定 Node ID 与 TUN IP，以便免去脆弱的动态 IP 申请握手。
12. 作为 Client 守护进程，我希望在开机后自动完成真实无线网卡的发现与 Monitor 模式配置，并在默认 Channel 157 上待命。
13. 作为 Client 守护进程，我希望收到仿真任务时派生独立的 `RoleService` 子进程执行训练与数据交换，以便在任务结束后干净释放资源，防止内存残留。
14. 作为 Server 协同守护引擎，我希望开机常驻并在后台持续收集 Client 心跳，以便随时维护全局节点的在线拓扑视界。
15. 作为 Server 协同守护引擎，我希望对外暴露统一本地 IPC 接口，以便 CLI 工具与 Web 后端能无冲突地发起查询与任务控制。
16. 作为 链路控制面，我希望所有心跳、状态汇报与任务信令采用轻量 UDP 报文承载，以便消除 TCP 连接建立延迟并复用空口物理广播红利。
17. 作为 仿真操作者，我希望在发布任务时能够在“同步（sync）”、“半异步（semi_async）”与“异步（async）”三种协同范式间自由选择，以便进行不同算法特性的科研对比。
18. 作为 算法研究者，我希望在选择“半异步”模式时指定最小收齐节点数（`min_updates`），以便在部分慢节点掉队或超时时不中断轮次推进。
19. 作为 算法研究者，我希望在选择“同步”模式时引擎严格等待全部目标客户端，以便确保基线实验数据的严谨性。
20. 作为 仿真操作者，我希望 Server 核心引擎能够向常驻客户端下发新的射频参数（如切换信道），以便根据扫频推荐动态应用最优配置。
21. 作为 Client 守护进程，我希望在收到射频重配信令后执行两阶段确认，以便向 Server 证明本机已准备好同步切换。
22. 作为 Client 守护进程，我希望在切换信道后启用 15 秒心跳租约看门狗，以便在未收到 Server 持续心跳时自动回滚至基准 Channel 157。
23. 作为 Server 协同守护引擎，我希望在新信道未全员上线时停止发送心跳并安全退回原信道，以便让孤立客户端由看门狗触发回退，杜绝信道脑裂。
24. 作为 Server 协同守护引擎，我希望在新信道全员上线后广播 `RADIO_SWITCH_FINALIZED` 终审指令，以便客户端正式锁定新信道。
25. 作为 审计人员，我希望所有带内信令交互、状态跃迁、看门狗回退事件与分源遥测均记录结构化带内日志，以便在无网测试后提取审计。

## Implementation Decisions

- **服务端常驻守护引擎与本地 IPC 契约 (Server Daemon & Local IPC)**：
  - 常驻服务名为 `wfb-fl-server-daemon`，以 systemd 单元运行；
  - 启动后独占纳管服务端的 `wlx*` 真实网卡，绑定 TUN `10.80.0.1`，常驻监听 UDP 端口 `9001` 接收客户端心跳；
  - 维护内存级 `NodeHorizonRegistry`，记录参与节点 ID、IP、当前状态机阶段（`IDLE`、`RUNNING` 等）、最后心跳时间戳（毫秒）与当前信道；
  - 对外提供统一的本地 Unix Domain Socket（`/run/wfb-fl/server.sock`）或本地专用 REST API（`http://127.0.0.1:9090`），支持的 IPC 动作：
    - `GET /status`：返回所有已知节点的实时拓扑、信道与状态；
    - `POST /survey`：触发底层网卡执行 5 GHz 频段快速扫频探路并返回排行；
    - `POST /jobs/start`：提交包含预设、模式、参与节点与模型路径的仿真作业；
    - `POST /jobs/abort`：强制中止当前作业并重置集群；
    - `GET /logs/stream`：流式输出带内控制信令与协同推进日志。

- **客户端常驻守护与子进程沙箱生命周期 (Client Daemon & Subprocess Sandbox)**：
  - 常驻服务名为 `wfb-fl-client-daemon`，以 systemd 单元运行；
  - 读取本地固化配置 `/etc/wfb-ng-fl/node.json`（包含 `node_id` 与 `tun_ip`）；
  - 开机后自动将 `wlx*` 网卡置为 Monitor 模式，信道设为默认 157，配置 TUN IP 并启动 UDP 客户端；
  - 每 2 秒向 `10.80.0.1:9001` 单播发送心跳 `NODE_HEARTBEAT`；
  - 收到 Server 的 `TASK_ANNOUNCE` 时，状态机从 `IDLE` 跃迁至 `PREPARING`，并在本地隔离的工作目录下通过 `subprocess.Popen` 动态派生独立的 `RoleService(role='client')` 执行本轮任务；
  - 任务正常完成或异常中止后，Daemon 负责 `terminate/kill` 子进程、清理临时接收区并回收资源，重新切回 `IDLE` 状态，坚决不驻留脏数据。

- **轻量对称 UDP 控制信令平面 (Symmetric UDP Control Plane)**：
  - **下行方向（Server -> Clients）**：
    - 单播回复：收到任一客户端心跳后，即刻单播回复极简 `HEARTBEAT_ACK`（仅包含 `{"ack": true}`，空口不足 20 字节）；若检测到该客户端预设与集群当前生效预设不一致，自动下发对齐指令；
    - 广播通知：向 `10.80.0.255:9000` 广播任务级指令：
      - `TASK_ANNOUNCE`：宣布新任务（轮次数、参与节点名单、协同模式、模型大小与 SHA-256）；
      - `CONFIG_RADIO_PREPARE`：两阶段信道切换预备请求；
      - `CONFIG_RADIO_COMMIT`：两阶段信道切换生效指令（带 2s 延时）；
      - `RADIO_SWITCH_FINALIZED`：新信道全员上线终审定案；
      - `JOB_ABORT`：紧急中止指令。
  - **上行方向（Client -> Server，单播）**：Client 向 `10.80.0.1:9001` 发送 JSON 数据报：
    - `NODE_HEARTBEAT`：全流程细粒度状态载体（携带 `node_id`、`state`、`elapsed_ms`、`current_channel`、`preset` 与错误码）；状态跃迁时即刻抢跑发送，常态每 5 秒保活；
    - `PREPARE_ACK`：信道切换预备就绪回执（带 seq_id 幂等重发）；
    - `COMMIT_SUCCESS`：新信道上线打卡汇报。
  - **三级寻频自愈与全自动热发现 (Three-Tier Hunting & Auto-Discovery)**：
    - 客户端通电后，按“本地缓存信道 -> 基准信道 157 -> 候选池 [149, 153, 165]”顺序逐频发送 `NODE_HEARTBEAT`；
    - 收到 `HEARTBEAT_ACK` 即刻锁定频点；服务端自动在内存注册该节点并在 Web 实时点亮绿灯，全程零广播发射，Web 零手动扫描操作。

- **两阶段防脑裂租约式看门狗自愈协议 (Lease-based Anti-Lockup Watchdog)**：
  1. **Phase 1 (Prepare)**：Server 广播 `CONFIG_RADIO_PREPARE(target_ch=149, preset="standard")`；
     - 客户端校验硬件支持度，回复 `PREPARE_ACK`；
     - **全员门禁**：Server 必须在 5 秒内收齐全部目标客户端的 `PREPARE_ACK`；若有任一节点超时，Server 广播取消，集群停留在原信道。
  2. **Phase 2 (Commit & Arm Lease)**：Server 广播 `CONFIG_RADIO_COMMIT(delay_ms=2000)`；
     - 客户端启动 **15 秒租约式看门狗**，延时 2 秒将网卡置为 Channel 149；
     - Server 延时 2 秒将网卡置为 Channel 149，并在新信道上以 1 秒周期广播 `NEW_CHANNEL_PING`；
  3. **租约刷新与终审定案**：
     - Client 在 149 收到 Ping 后回复 `COMMIT_SUCCESS`，同时将看门狗租约刷新为 15 秒（不关闭看门狗）；
     - **全员上线**：Server 在 10 秒内收齐全部客户端的 `COMMIT_SUCCESS`，广播 `RADIO_SWITCH_FINALIZED`；客户端收到终审定案，正式解除看门狗并锁定 Channel 149；
     - **局部失败自愈（防脑裂）**：若 Server 在 10 秒内未收齐，Server 立即停止在 149 上发心跳并退回 Channel 157；新信道上的客户端由于 Server 消失，15 秒租约耗尽，看门狗强制将物理网卡切回 Channel 157。三机最终整齐汇合于基准信道 157！

- **联邦学习协同范式 Schema 与行为契约 (FL Collaboration Paradigms)**：
  - 作业配置 `job.json` 核心字段：
    ```json
    {
      "mode": "sync", // 可选: "sync", "semi_async", "async"
      "target_nodes": [1, 2],
      "min_updates": 2, // 当 mode 为 semi_async 时必须指定，sync 模式自动等同于 len(target_nodes)
      "max_staleness": 0, // 当 mode 为 async 时支持落后版本聚合
      "round_timeout_seconds": 120
    }
    ```
  - `sync` 模式下任一节点超时触发全局中止与自愈；`semi_async` 模式下达到 `min_updates` 即完成本轮聚合推进下一轮，未提交节点记为 `dropped_out`。

- **扫频探路、预设与速率安全区间 (Radio Presets & Spectrum Survey)**：
  - 继承 ADR-0011：固化 FEC 8/14；内置 `robust`、`standard`（默认）、`performance` 三档底层网卡预设；
  - 扫频探路扫描 `[149, 153, 157, 165]`，**Channel 161 硬黑名单禁选**，全拥堵时警示并推荐 `robust`；
  - 下行速率字典强校验（MCS 3 默认 15 Mbps，区间 12~18 Mbps），越界需 `--force` 否则拒绝启动。

## Testing Decisions

- **测试设计原则 (Seams at the highest level)**：
  - 遵循深模块与高接缝原则，在可执行 CLI、本地 IPC 套接字与虚拟网络设备层构建端到端测试，严禁对内部私有变量或中间状态进行脆弱断言；
  - 保证在无物理无线网卡的开发与 CI 环境中，通过虚拟 TAP/TUN 与 Mock 套接字能够完整测试协议、状态机与看门狗逻辑。

- **五大核心测试接缝 (Primary Testing Seams)**：
  1. **Server 常驻守护与 Local IPC 接缝 (Server Daemon IPC Seam)**：
     - 测试 `wfb-fl-server-daemon` 的 Unix Domain Socket / REST 接口；
     - 断言 `GET /status` 能正确反映从模拟 UDP 心跳中解析出的客户端拓扑与信噪比；
     - 断言 `POST /jobs/start` 能够正确生成任务广播并推进协同状态。
  2. **对称 UDP 控制信令编解码与通信接缝 (Symmetric UDP Protocol Seam)**：
     - 在虚拟 TUN 接口对之间收发下行广播（9000 端口）与上行单播（9001 端口）；
     - 注入高频乱序与重发，验证 `PREPARE_ACK` 幂等性、心跳包丢弃容忍度与毫秒级时间戳单调性。
  3. **防脑裂租约式看门狗自愈接缝 (Lease Watchdog Anti-Split-Brain Seam)**：
     - 模拟信道切换场景：Server 与 Client 1 成功切换，但人为丢弃 Client 2 的所有心跳模拟掉队；
     - 断言 Server 超时未集齐后停止在新信道发心跳并退回 Channel 157；
     - 断言 Client 1 在 15 秒租约耗尽后，看门狗成功触发物理网卡回退，最终两端均回到 Channel 157，无任何节点孤立。
  4. **Client 常驻守护子进程生命周期接缝 (Client Daemon Sandbox Seam)**：
     - 测试 `wfb-fl-client-daemon` 收到任务后派生 `RoleService` 子进程并监控其 PID；
     - 模拟任务正常完成、被 Server `JOB_ABORT` 中止，以及子进程异常退出等场景；
     - 严格断言子进程完全销毁、TUN 设备释放、无残留僵尸进程，守护进程安全恢复至 `IDLE`。
  5. **协同范式参数解析与收齐接缝 (Collaboration Paradigm Seam)**：
     - 测试 `sync` 与 `semi_async` 模式；
     - 断言 `sync` 模式在任一节点缺失时 fail-closed；
     - 断言 `semi_async` 模式在满足 `min_updates` 时即使有节点缺失亦能成功触发聚合并标记落选节点。

- **既有实现借鉴 (Prior Art)**：
  - 借鉴 `tests/real_hardware/test_issue41_lifecycle.py` 建立无残留进程与 cgroup 审计断言；
  - 借鉴 `wfb_ng/tests/test_fl_role_service_lifecycle.py` 建立子进程生命周期监管；
  - 借鉴 `tests/real_hardware/test_issue41_validate_archive.py` 建立机械化强校验。

## Out of Scope

- 浏览器 Web 前端单页应用与 SSE 推流可视化界面（明确归属 Stage 4）。
- 面向用户的终端丰富交互 TUI（明确归属 Stage 4）。
- 真实的拔掉以太网网线纯物理闭环验证（明确归属 Stage 3）。
- 运行时空口动态自适应跳频（已由 ADR-0011 彻底否决）。
- 2.4 GHz 频段支持（仅聚焦 5 GHz 合法频段）。

## Further Notes

- Stage 2 是本项目从“测试脚本编排”迈向“自治分布式系统”的最关键 Seam。
- 完成 Stage 2 后，系统无需 SSH 即可在三台机器上自主完成网卡发现、拓扑组网、信道重配与两轮 FL 闭环，Stage 3 的物理断网闭环与 Stage 4 的 Web 界面只需作为上层消费端直接接入。
