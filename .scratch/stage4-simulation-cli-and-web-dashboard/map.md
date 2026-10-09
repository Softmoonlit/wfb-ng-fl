Status: ready-for-agent
Type: map

# Stage 4 Web 控制台规格与实施路线

## Destination

形成一份与现行 Stage 2 底座、ADR 和真实三机拓扑一致的 Stage 4 决策地图，并拆出具有明确依赖与验收边界的实施任务，使后续会话可以直接进入规格决策和实现而无需补做产品或架构决策。

## Notes

- 本地图只规划，不实现 Stage 4；Wayfinder 阶段不保留独立 `spec.md`，地图完成后的交付物是已收敛的决策记录与实施任务清单。
- Stage 4 是平台操作控制台，不是实验编排、参数扫描、结果对比或收敛分析工具。
- 首版唯一用户入口是 Web；取消 `wfb-fl-core` CLI。Web 与 REST/SSE 同源，由 `wfb-fl-server-daemon` 直接托管零构建依赖的原生 HTML/CSS/JavaScript。
- 平台拥有作业生命周期、轮次推进、协同范式、超时和急停；预装 Python 算法插件只提供训练与聚合能力。
- 插件代码定义参数名称、类型、约束和默认值，Web 配置单次作业参数值；首版只支持标量、枚举及基础约束，不支持节点级参数覆盖。
- 插件由固定目录中的 manifest 注册，代码与依赖由管理员预装；作业启动前必须验证 Server 与全部目标 Client 的插件 ID、版本和实现摘要一致。
- 首版不上传、分发或安装算法代码。后续版本可另行设计代码包上传与全网部署。
- Client 本地数据集、凭据、设备选择等私有配置不经 Web 或 Server 广播；控制台只显示插件就绪状态和错误原因。
- 模型通过受控模型库上传和选择，以 SHA-256 内容寻址；平台只管理普通文件的大小、摘要、生命周期和传输完整性，文件语义由插件负责。
- 射频采用 ADR-0014 扁平直配模型，禁止命名预设和速率越界绕过；扫频只建议，操作者手动应用集群级配置，运行中禁止重配。
- 首版显示有事实依据的轮次、阶段、已耗时和实时事件，不伪造训练或上传百分比；不交付链路级实时曲线、历史归档浏览或压缩包下载。
- 页面面向桌面操作台，最低目标视口 1280x720；移动端只保证不溢出并可执行急停。
- 浏览器通过单根网线组成的无外网直连管理网访问 Server。daemon 绑定显式管理网 IP，不绑定所有接口；首版不做应用鉴权、HTTPS或额外源地址防火墙。
- 运行时单作业独占；查看状态、事件、插件和模型清单始终可用，模型上传可并行，急停始终可用，其他冲突操作禁用。
- 实体验收拓扑固定为本机 Server、`vm1`（Client 1）和 `vm2`（Client 2）；1～10 节点仅验证协议、配置和界面容量。
- 首版用确定性 40 MiB 文件闭环插件验证模型下发、update 上传和多轮推进，不执行或宣称真实 FL 训练、聚合效果。
- 每个决策会话按需使用 `grilling`、`domain-modeling`、`codebase-design` 或 `prototype` 技能。

## Foundation baseline

- Stage 4 直接建立在 `.scratch/stage2-airgapped-core-engine-and-daemon/` 已交付的常驻 Server/Client Daemon、对称 UDP 控制面、RoleService 沙箱、FL Coordinator/Runtime、UFTP/HTTP 数据面、三种协同范式和本地 REST/SSE 之上，不新增第二套运行时、控制协议或传输实现。
- Stage 3 `.scratch/stage3-vm-managed-physical-radio-loop/` 是上述 Stage 2 运行时的真实 VM/射频验证与加固阶段。Stage 4 复用 Stage 3 已验证的作业无 SSH 依赖、`TASK_READY` 屏障、终态链路恢复、射频双最终屏障、15 秒租约和严格作业参数契约，但不把 Stage 3 执行器当作 Web 后端。
- Stage 2/3 已有的 `/api/v1/status`、`/api/v1/logs/stream`、`/api/v1/survey`、`/api/v1/radio/reconfigure`、`/api/v1/jobs/start` 和 `/api/v1/jobs/abort` 是 Stage 4 控制台的现有基础契约。Stage 4 新增的是插件目录、模型库、Web 管理入口和面向操作者的状态语义，不复制这些领域逻辑。
- Stage 2 和 Stage 3 的实体测试拓扑均限定为本机 Server、`vm1`（Client 1）和 `vm2`（Client 2）；1～10 节点是协议、配置和界面容量约束，不是 Stage 4 的实体硬件验收规模。
- Stage 4 的 40 MiB 闭环继续使用确定性占位算法和 canonical fixture，只验证模型下发、update 上传、轮次连续性、状态恢复和急停，不宣称真实机器学习训练、FedAvg 或收敛效果。


## Decisions so far

- Stage 4 Web 控制台由同一个 `wfb-fl-server-daemon` 托管；保留 `127.0.0.1:9090` 本机 IPC，并增加绑定明确管理网 IP 的 Web HTTP listener。两个 listener 共用同一套 REST handler、状态快照和领域逻辑，禁止绑定 `0.0.0.0`。管理网地址暂不可用时，管理 listener 进入不可用/降级状态，但不得阻止 Server Daemon 核心运行时或 Client Daemon 开机自启；网络地址就绪后由服务生命周期恢复管理 listener。
- Web 通过 Server Daemon、带内控制面和 Client Daemon 控制集群级射频参数（`channel`、`radio_txpower_dbm`、`downlink_mcs`、`uplink_mcs`）以及 FL 作业启动、观察和急停；不得直接访问 Client、WFB 底座、TUN、UFTP 或 HTTP PUT 数据面。
- Client 的物理网卡选择、`node.json` 身份、TUN/IP 映射、驱动、USB/xHCI、软件包、systemd 和私有数据集/凭据属于部署维护面。Web 只展示其就绪状态和错误原因，不提供动态修改或 SSH 终端。
- 正常 FL 作业从浏览器到 Server Daemon，再经控制面和 Client Daemon 完成；运行时禁止通过管理网 SSH 启动、复制数据、补发控制消息或恢复作业。

## Not yet specified

- 在 Stage 2 已有 REST/SSE 和 Stage 3 真实闭环基础上，Stage 4 应如何定义插件、模型库、Web 管理入口和控制台语义，使 Web 只作为现有 Server Daemon 的操作适配层，并能在不重复底层运行时实现的前提下完成 40 MiB 确定性闭环和急停验收。
- 后续版本的算法代码上传、依赖制品分发、安装回滚和全节点部署机制；首版仅需为未来演进留下清晰边界，不设计实现。

## Out of scope

- CLI 或全屏 TUI。
- React、Vue、Node.js 构建链及任何在线前端依赖。
- 实验编排、参数扫描、跨运行对比、loss/accuracy 展示和收敛分析。
- 首版中的真实训练与真实聚合效果验收。
- 节点级训练参数覆盖。
- 链路级实时丢包/FEC/队列/TCP 曲线、作业历史归档浏览及归档包下载。
- 应用用户体系、多租户、HTTPS，以及面向非受信任网络的远程开放。
- 运行时自主跳频、自动频宽切换或任何未由操作者确认的射频变更。
