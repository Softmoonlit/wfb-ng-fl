# Stage 3/4 三机验收经验与后续现场验证指南

本文记录 2026-10-10 本机 Server、vm1、vm2 的完整验收过程，供电脑单网线直连管理网及 10 Client 扩展验收复用。内容覆盖失败原因、修复、通过证据、执行顺序和待验证事项；它是验收经验与操作指南，不替代设计契约，也不把历史三机结果外推为大集群通过。

相关入口：

- [Runtime 验收导航](../../tests/fl_runtime/README.md)。
- [Stage 3 三机执行器](../../tests/fl_runtime/stage3三机真实射频执行器.md)。
- [Stage 4 射频补充执行说明](../../tests/fl_runtime/stage4射频补充验收.md)。
- [工单 08](../../.scratch/stage4-web-console-file-simulation/issues/08-three-node-formal-acceptance.md)。
- [TUN 启动就绪竞态规格及证据](../../.scratch/radio-link-startup-readiness/spec.md)。
- [部署与运行基准](../deployment/部署与运行基准.md)和[系统分层与跨层契约](../design/系统分层与跨层契约.md)。

## 1. 本次结果与证据边界

现场为 1 个 Server 加 2 个 Client，真实无线网卡、WFB/TUN 控制链路、UFTP 下行和 HTTP PUT 上行。管理 Web 由同一个 Server daemon 托管，不存在独立的 Stage 4 Web daemon 服务。

文件仿真通过 Web 上传模型，Client 不训练，原样复制收到的模型作为 update；Server 不进行真实聚合，继续使用同一模型。每个节点、每轮的 SHA 应相同，通过作业、轮次 UUID、node_id 和两端 manifest 证明各份 update 的独立身份。本次不证明真实训练、FedAvg 或模型收敛。

| 验收 | 证据目录（仓库根相对路径） | 实际结论 |
| --- | --- | --- |
| Stage 3 基线恢复 | `tests/logs/stage3_20261010_105422_df2452879ccb/` | 三端生产提交 `663dcd5`，两轮真实闭环、终态恢复及射频租约回退通过；初次入口被归档门禁误判，修正观测分类后独立校验通过。 |
| Stage 4 首次完整通过 | `tests/logs/stage4-hardware-20261010T035439Z-40dd3c5d/` | 生产提交 `90b7211`，正常两轮、传输中 Web 急停、恢复期间 409 拒启、原始证据校验通过。 |
| 最终射频补充验收 | `tests/logs/stage4-radio-20261010T043351Z-79b8aa83/` | 生产/执行提交 `29da5ba`，MCS、功率、165/157 频宽重建、控制链路恢复、受控回退、后续实际 UFTP 速率通过，独立重判通过。 |
| 最终正常/急停复验 | `tests/logs/stage4-hardware-20261010T043817Z-47f7cc63/` | 同一 `29da5ba` 生产提交，入口退出 0，完整离线校验通过。 |
| Windows 浏览器人工启动 | `tests/logs/stage4-browser-review-web-f7ce85ac260b42088ca0ac8f92115be0/` | 作业 `web-f7ce85ac260b42088ca0ac8f92115be0`，三轮成功，六份 Server 实际 update、Client 原始 manifest 与生命周期核验通过；完成后 Client1 有一次 ACK 缺失告警，见第 8 节。此目录为事后检查证据，不是正式入口封存的验收包。 |

这些目录是本地实测产物，未承诺已纳入 Git 或长期备份。复用时先确认目录仍存在，并把完整归档、校验结果及运行版本一同保存到现场证据盘；只保存本页不能替代原始证据。

最终软件回归为 `609 passed, 1 skipped`；普通用户环境跳过需要内核权限的测试，真实 network namespace 的 TUN/socket 回归另行执行通过。浏览器 HTTP 修复后的相关可并行测试为 35 项通过；现场 Server 占用 8080，单独的同地址绑定测试不能同时运行。

Windows 已实际访问管理地址并启动成功。但未记录单网线拓扑、断开其他网络后的路由证据，也未记录该次从 Windows 上传新模型。因此仍不能声明“电脑单网线无外网直连全过程”已验收。10 Client 实体验收也未执行。

## 2. 三机现场基线与网络契约

| 项目 | 本次基线 |
| --- | --- |
| Server | vm0，本机；管理 IP `192.168.108.100`；Server 身份 255 |
| Client | vm1/node 1、vm2/node 2；身份来自各自 `/etc/wfb-ng-fl/node.json` |
| 无线接口 | 每台动态发现 `wl*`，不按主机硬编码、不限定 `wlx*` |
| 驱动与 USB | `rtl88xxau_wfb`；VMware 使用 xHCI，检查 `xhci_hcd` 与实际 USB 拓扑 |
| 信道/频宽 | 157 / HT40+；165 使用 HT20；禁止 161/HT40+ 的驱动越界组合 |
| 功率 | 12 dBm；实际驱动命令用负值 mBm，例如 `iw ... set txpower fixed -1200` |
| MCS / UFTP 速率 | 下行 3，上行 6，`uftp_rate_kbps=15000` |
| Server TUN | `fl-s`，`10.80.0.1/24`，`txqueuelen=5000` |
| Client TUN | `fl-c1` / `10.80.0.11/24`，`fl-c2` / `10.80.0.12/24` |
| canonical 模型 | 40 MiB = 41,943,040 字节；SHA-256 `a544c81f86c7a9e089dc45b3b0d3ff6490b933bb76153d8a8478c1a7703c7841` |

| 网络用途 | 地址/端口 | 解释 |
| --- | --- | --- |
| 管理 Web | `192.168.108.100:8080` | 明确管理地址，不绑定 `0.0.0.0`；`web_host` 必须配置且地址存在。 |
| 本机 IPC | `127.0.0.1:9090` | 和管理 Web 共享领域逻辑，不能用 IPC 成功替代电脑 Web 接入。 |
| Server 控制面下行 | `255.255.255.255:9000` | 广播发送 socket 绑定 Server TUN IP，避免走管理以太网。 |
| Client 控制上行 | `10.80.0.1:9001` | Server 实际监听该 TUN IP；Client 控制接收端沿现行实现监听 `0.0.0.0:9000`，需结合路由、发送源和原始日志判断无线路径。 |
| UFTP | UDP 1044；`239.80.41.1` / `239.80.41.2` | 不得和控制面 9000/9001 混用。 |
| update HTTP PUT | `10.80.0.1:8080` | 与管理 Web 同端口但不同绑定地址；必须核验 TUN 目标，不能把更新文件走管理网当成无线成功。 |

功率读回和命令必须一起解释：此驱动利用负值 mBm 设置私有功率钳制。不要“修正”为正值，否则可能回到全功率发射，近距测试可能发生接收饱和。12 dBm 的本次通过结果不保证所有位置、天线间距和 10 Client 环境都适用。

## 3. 推荐执行顺序

1. 核对身份、安装版本、接口、USB、服务角色、磁盘及残留；保留失败证据后再清理。
2. 运行软件回归。涉及地址绑定的测试和硬件 daemon 分时运行；构建与全量收集也要避免互相污染。
3. 共用运行时发生变化或其基线不可信时，先完成 Stage 3；不要把 Stage 4 的所有失败都归因于 Web。
4. 按同一生产提交部署三端，检查已安装文件；启动正确角色的 daemon，等目标节点 READY。
5. 运行 Stage 4 正常多轮、实际传输期间急停、恢复拒启及终态清理，独立验证归档。
6. 射频/MCS/频宽实现变化时，补执行实际读回、TUN 重建、控制链路连续性、受控回退及后续速率验收。
7. 用真实 Windows 浏览器检查普通 HTTP 页面、模型上传、启动、观察、急停和刷新重连；HTTP 脚本成功不能代替浏览器能力验证。
8. 从三机基线逐步扩大参与集合，每个规模形成独立归档；最后执行全体 10 Client。

Stage 3 和 Stage 4 共用任务通知、RoleService、TASK_READY、TUN、WFB 和数据面。Stage 3 也在 TASK_READY 超时，就应先查共用链路；Stage 3 通过而 Stage 4 未发出请求或字段映射错误，才把范围收缩到管理 Web/浏览器接缝。Stage 3 不验证 Web 上传、浏览器 API 和 Web 急停，不能取代 Stage 4。

## 4. 部署与预检中实际遇到的问题

### 4.1 源码提交相同不代表安装包相同

初次启动的 `/usr/bin/wfb-fl-server-daemon` 是旧安装包，尚无 Stage 4 Web listener。只启动服务不能修复该问题。

正式验收至少记录：三端 checkout HEAD 和 clean 状态、已安装 `build_identity.json`、生产源码 SHA、链路二进制 SHA、systemd ExecStart/MainPID 和实际 argv。Python 身份检查应从仓库外或用 `python3 -I`，避免导入 checkout 后把旧安装误判为新版。只修改文档/工具时可分别记录执行器提交与生产包提交，但必须证明生产文件一致；不能借此容忍运行时代码偏差。

Client 分支名称可以仍是历史名称，不能只据名称判断新旧；本次通过 git bundle、`git fetch` 和 `git merge --ff-only` 实现提交一致。禁止用 `git reset --hard` 覆盖远端状态。构建曾因缺少 `stdeb` 失败，需在构建环境解决后生成正式包。

浏览器修复 `9efe371` 是在已安装运行时之外直接部署静态资源的现场修补：修复来源提交不等于生产包身份，`29da5ba` 的原始包仍包含旧脚本。现场操作已同步修复源码并直接更新 Server 安装目录中的 `console.js`，三轮作业的 runtime_commit 仍为 `29da5ba`。浏览器事后检查目录没有保存当时的 HTTP 脚本正文和响应头，不能仅凭该目录机械证明静态资源版本。

整理本指南时于 `2026-10-10T05:41:03Z` 再次读取 `http://192.168.108.100:8080/assets/console.js`：15,963 字节，SHA-256 `f5b8483abdc9dd3286188acdccf336504d4dc759da5964d81389bbe86c9b36c0`，`Cache-Control: no-store`，正文与 `git show 9efe371:wfb_ng/fl/static/console.js` 完全相同。这是整理时的读回记录，不替代浏览器作业时的历史快照。以后正式验收应重新构建包含该修复的包，或保存静态资源正文/SHA、HTTP响应和部署记录；不能宣称已安装包自动变成 `9efe371`。

### 4.2 Debian 安装可能拉起错误角色

包的 systemd postinst 曾在 Client 上拉起 Server daemon，也在 Server 上拉起 Client daemon，导致 TUN/网卡所有权冲突。Server 和 Client 服务不能在同一台设备同时承担无线角色。

Stage 3 安装阶段还要求资源为空。验收编排因此在安装前对四个单元使用运行时 mask，安装后审计资源，再解除：

```bash
sudo systemctl mask --runtime --now \
  wfb-fl-client-daemon.service wfb-fl-client.service \
  wfb-fl-server-daemon.service wfb-fl-server.service
# 在受控部署阶段安装已确认的软件包并检查资源。
sudo systemctl unmask --runtime \
  wfb-fl-client-daemon.service wfb-fl-client.service \
  wfb-fl-server-daemon.service wfb-fl-server.service
```

必须显式 `unmask --runtime`；普通 `unmask` 曾留下 `/run` 的 mask，导致 start-services 看到 `masked-runtime`。随后禁用不属于该主机的角色，只启动对应 daemon。以上是部署/验收安装操作，不应在正在运行的 FL 作业期间执行。

### 4.3 代理、磁盘与沙箱残留

Server shell 的 HTTP/HTTPS 代理曾拦截管理地址请求。Python urllib 不能依赖 `NO_PROXY=192.168.108.0/24` 的 CIDR 排除，应列出实际地址：

```bash
NO_PROXY=192.168.108.100,127.0.0.1 \
no_proxy=192.168.108.100,127.0.0.1 \
STAGE4_WEB_URL=http://192.168.108.100:8080 \
bash tests/fl_runtime/stage4_hardware_acceptance.sh
```

这解决管理 HTTP 请求，不解释 TASK_READY、裸 READY 或无线传输故障。Windows 浏览器的系统/PAC代理另行检查。

Stage 3 曾因根分区只剩 92 MiB 未达 1 GiB 预检门槛而失败。清理了已确认可再生成的旧吞吐归档后恢复空间；该门槛仅是最低线，不是大集群容量预算。多轮 Server update、模型副本、失败沙箱、构建目录和多次归档都可能占用大量空间。

失败后的 `job_*` 沙箱在确认无作业、无任务进程后移到 `/var/tmp/wfb-failed-sandboxes/` 保留证据，再清空验收目录。不能无条件删除正在运行的目录，也不能把“进程停止”当成临时文件已清理。

网卡重新插拔后可能回到 managed/DOWN，本次也遇到。仅在停服维护阶段检查并按正式部署路径恢复 monitor；不要把 replug、硬编码网卡或 SSH 重启作为作业期补救。VMware USB 必须核对 xHCI；高吞吐在 EHCI 下可能静默大量丢包。

构建生成的 `deb_dist` 副本曾让无范围 `pytest` 出现 import mismatch。使用 `python3 -m pytest -q wfb_ng/tests` 明确测试根，并在适当阶段清理已确认可再生成的构建产物；不要把收集错误视为运行时回归。

## 5. 共用运行时的两项关键修复

### 5.1 TASK_READY 被诊断回调锁住

症状：两台 Client 已收任务通知、启动 RoleService，Server 仍在 10 秒屏障返回 `client_startup_timeout`。Stage 3 与 Stage 4 均复现。

根因：`start_job` 持有 daemon 状态锁等待 TASK_READY；串行 UDP 接收线程先处理 PREPARING heartbeat，其新增 Web 事件回调也等待同一把锁，后续 TASK_READY 无法被读取。

`30ebe58` 将 heartbeat 诊断事件分流，不阻塞 UDP 接收线程；状态锁忙时跳过本次 Web snapshot 刷新，后续事件/心跳再投影权威状态。真实 UDP 并发回归在持有 daemon 锁时发送 PREPARING heartbeat 和 TASK_READY，旧实现失败，新实现完成屏障和 ACK。不要延长启动超时掩盖锁等待。

C++ `READY_FILTER` 是裸空口 READY 统计，不是 Python TASK_READY 或 RoleService `READY=1` 的计数。三个 READY 层次必须分开：本地 RoleService 通知 → 作业级双向 TASK_READY → 链路层裸 READY/GRANT。

### 5.2 TUN 已存在但地址和路由尚未就绪

症状：MCS/频宽重建后 Server 发 NEW_CHANNEL_PING，报 `[Errno 101] Network is unreachable`，立即 COMMIT 失败并回滚。同一版本偶尔成功，不能据一次通过排除启动竞态。

根因：`TUNSETIFF` 使接口名称先出现；UP、IPv4 CIDR 和路由稍后建立。原启动等待仅判断名称存在，可能提前放行。

`bef6a65` / `29da5ba` 的就绪屏障检查：

- TUN `IFF_UP`；
- 预期 IPv4 地址和前缀完整；
- 控制目的地址的实际路由出口 `RTA_OIF` 是该 TUN。

Server 检查受限广播路由，Client 检查 Server 控制地址路由。保留原启动预算，失败回收进程/TUN/日志。真实 network namespace 回归保留同一个绑定 socket、删除重建 TUN：地址/路由未配置时复现 E101，屏障通过后原 socket 正常广播。此例无需盲目 rebind、固定 sleep 或延长 COMMIT timeout。

## 6. 射频实测、故障注入与误判经验

首次把功率 12→11 dBm、down MCS3→4、up MCS6→5 和速率一起变更时，Client2 新链路未完成上线确认，事务回滚。其日志显示下行可接收、所有 GRANT 都指向 Client1；Server 没收到 Client2 可识别的裸 READY。这个断点不能由上层 UDP socket 缓存单独解释，也没有旧 GRANT 序号拒绝证据。

保持 12 dBm 单独改变 MCS 曾成功；随后正式复验捕获 E101，并完成上节修复。首次裸 READY 丢失的最终原因仍未确定，不应归因于“网卡坏了”或直接称作同一竞态。失败证据保留在：

- `tests/logs/stage4-radio-20261010T040651Z-ad5266fd/`：首次混合变更失败，较早工具的失败取证不完整。
- `tests/logs/stage4-radio-20261010T041811Z-e7b420f3/`：固定功率的 MCS 重建捕获 E101，失败前后 journal 和清理结果完整。
- `tests/logs/stage4-radio-probe-20261010T041104Z/`：三端无线抓包及固定功率变更/恢复结果；属于诊断证据，非完整正式包。

最终补充验收拆开变量：固定 12 dBm 验 MCS → 独立 13/12 dBm 功率读回 → 165 HT20 → 157 HT40+ → 故障注入回退 → 后续两轮实际 `-R 18000` → 恢复基线。每步同时采集 Web状态、daemon PID、链路 PID/start_ticks/argv、TUN ifindex、`iw`、控制监听和 READY；只看 Web 配置记录不能证明实际生效。

Stage 3 原规则装在 Client2，但真正耗尽租约的可能是 Client1：Client2 收到 RADIO_ABORT 主动回退，Client1 没完成最终确认而等 15 秒租约过期。早期 runner 硬编码查 Client2，误判为无证据。现按同一 session 在目标集合中找到实际 lease_node，并同步校验事件的 node_id。故障施加节点和租约耗尽节点不可混为一谈。

故障规则只在独立射频场景使用、先归档安装意图、正常/异常路径都移除并检查；强制中断后依 run_id 检查残留。不能靠故障规则未清理来制造后续失败，也不能把 session 不匹配的旧日志当成本轮回退证据。

## 7. Stage 4 证据契约与执行器必须防止的假通过

### 7.1 文件仿真和生产目录

Client 文件仿真没有独立 update template。旧结果写入仍对 `None` 求 SHA，可能在上传完成后抛 TypeError；已修为明确的 null，并用完整两轮 client_main 回归验证。Server 成功不能代替 Client 成功。

| 证据 | 生产路径/字段 |
| --- | --- |
| Coordinator 摘要 | `<server work_dir>/job_<job_id>/coordinator_summary.json`；`status`、`rounds_completed`、每轮 `committed_nodes`。没有 `conclusion` 或 `round.updates`。 |
| Server 实际制品 | `<server work_dir>/job_<job_id>_role/rounds/<round_id>/`，update 为 `updates/<node_id>/update.bin` 和 `update.manifest.json`。 |
| Client 持久证据 | `<evidence_dir>/<job_id>/`；包含 result、白名单文件及 `evidence_manifest.json`，不含大模型/update二进制。 |
| update manifest | `schema_version/artifact_type/round_id/node_id/size_bytes/sha256`；没有路径、没有 round_index。 |
| 轮次对应 | 用 Client result 的 `round_index → round_id` 关联两端原始 manifest，再计算 Server 实际二进制 SHA/size。 |

摘要身份、目标集合、轮次数、无掉队、跨轮 input/output/final SHA均要核对。四份或六份 update 的 SHA相同是本模式预期，不应借用 Stage 3 node-specific fixture 的“节点 SHA不同”要求。

### 7.2 终态与急停

`current_job=null` 或出现 `recent_job` 不等于回收结束。必须匹配本次 job_id、预期 execution_result、`recovery_state=ready`、Server idle、所有目标 Client IDLE/READY；已知未参与/离线节点可以存在，不应要求全量 registry 恰好等于本轮目标。

Web 的 `publishing_model` 事件在真实 UFTP start 之前发生。急停必须先观察到当前作业的非僵尸 UFTP PID、启动标识、argv/cwd/轮次归属，再触发；终态再确认原 PID/start_ticks 对应进程已消失。零轮 aborted 可以是合法结果，但没有活动传输证据就不能证明取消了正在进行的传输。

恢复拒启必须在当前作业 `recovering` 快照期间真正发起新请求，并保存明确的 `409 preflight_engine_conflict`；任意 HTTP 错误不能算通过。Client必须有匹配job的JOB_ABORT接收/回收日志及aborted生命周期，不能接受succeeded冒充急停。

### 7.3 取证和异常收尾

- 作业窗口从请求发出前开启，到终态恢复确认后关闭；其间禁止Client SSH控制、复制或补救，允许管理Web和Server本机只读观察。
- 创建POST响应超时存在“已受理但未拿到job_id”的窗口；用同一请求和原Idempotency-Key解析，避免创建重复作业；异常清理不能仅依赖已赋值的active_job_id。
- job_id在用于URL/文件路径之前校验；coordinator输出路径必须在本次job根内，特权复制前拒绝越界、符号链接和非普通文件。
- Server journal照常采集，不可按 `int(role[-1])` 把server解析成Client。
- 正常与急停按job_id分目录，不能让急停覆盖正常证据。
- 核验实际文件集合、清单size/SHA、build identity、原始日志和实际制品；最终检查任务进程、任务TUN、Client沙箱、临时模型/update与保留沙箱，不只检查daemon active。
- 控制地址常量只是预期值；需部署配置、实际ss绑定、job-scoped TASK_READY/ACK/终态接收日志与角色数据面配置共同证明。
- Python入口成功前执行完整离线validator，shell再次校验；封存清单本身不代表通过，失败归档必须保持失败。

## 8. Windows 浏览器实测与完成后告警

普通HTTP管理地址不是安全上下文。前端原先在显示“正在提交…”之后、try之外调用 `crypto.randomUUID()`，本次 Windows 浏览器的 HTTP 管理地址环境中该 API 不可用，异常使按钮永远处于提交状态，POST尚未发出。Windows 浏览器并非普遍不支持此 API；本次缺陷是运行环境要求与普通 HTTP 部署契约不符。

`9efe371` 改用普通HTTP可用的 `crypto.getRandomValues(new Uint8Array(16))` 生成128-bit十六进制幂等键，并放进try/catch/finally。测试环境只暴露getRandomValues，旧实现红、新实现绿；部署后通过 `/assets/console.js` 实际HTTP读回核对，响应 `Cache-Control: no-store`。已打开页面仍保留旧JavaScript，需要Ctrl+F5重新加载。

随后用户从Windows成功启动三轮作业。实测耗时：

| 轮次 | 模型下发 | 下发完成后收齐update | 整轮 |
| --- | ---: | ---: | ---: |
| 1 | 29.917 s | 21.657 s | 51.787 s |
| 2 | 25.138 s | 20.840 s | 46.187 s |
| 3 | 25.869 s | 22.548 s | 48.651 s |

收齐update时间是Server在模型发布结束后的剩余等待，不等于两台Client全部PUT耗时之和；Client可在Server结束组播收尾前开始PUT。UFTP日志只有少量NAK补包，每轮都收到双节点COMPLETE；两台Client exit0，六份实际update完全匹配。

作业12:57:03结束；Client1在12:57:18报告连续两次未收到heartbeat ACK并进入寻频自愈，之后恢复READY。该告警未影响已完成的数据结果，但不能定性为假告警。现有日志无法区分Server未收到heartbeat、未发ACK、ACK回程丢失。代码只在IDLE且没有射频事务时累计该告警，因此不能由此推断RUNNING必然走同一路径。后续应抓取UDP9000/9001和终态重建时序，而不是直接延长阈值或要求忽略warning。

## 9. 电脑单网线直连管理网：下一次操作与通过标准

### 9.1 物理拓扑与部署准备

电脑只通过一根网线连接Server管理以太网；无线网卡保留专用于WFB。示例给Windows配置`192.168.108.101/24`、Server`192.168.108.100/24`，先确认地址无冲突；此直连链路不需要默认网关或DNS。记录实际网卡、地址、线缆连接图及日期。若其他网络会改变路由，验收期间断开并记录，不能只截图浏览器页面就宣称隔离。

Server启动正确daemon，确认配置web_host、实际management_web ready和目标Client待命。部署可用SSH；开始作业后禁止依赖SSH。首次启动或网线插拔时，管理listener降级/恢复属于同一服务生命周期，不应另起第二个daemon占用TUN。

Windows参考检查：

```powershell
Get-NetAdapter
Get-NetIPConfiguration
Get-NetRoute -AddressFamily IPv4
Test-NetConnection 192.168.108.100 -Port 8080
netsh winhttp show proxy
```

WinHTTP代理信息不等于浏览器系统/PAC代理；另检查Windows代理配置和浏览器DevTools实际请求地址。不得通过HTTPS/localhost测试绕过普通HTTP缺陷。

### 9.2 浏览器全过程

1. 打开`http://192.168.108.100:8080`，记录console.js版本/响应和控制台无异常。
2. 在Windows生成或准备canonical文件，`Get-FileHash -Algorithm SHA256 <文件路径>`记录完整SHA，再从模型库页面上传；核对41,943,040字节、SHA、去重行为。不能由Server本地已有模型推断Windows上传已通过。
3. 选择模型，输入本次全部目标和明确轮数；只点击一次，记录POST body、Idempotency-Key、响应job_id和受理时间。若请求不确定，不以反复新建代替诊断。
4. 完成正常多轮；刷新页面、短暂关闭/重开页面及SSE断线重连均应恢复同一权威作业状态，不重复创建或用浏览器本地计时假装Server进度。
5. 再创建一项作业，在可证明的实际传输期间急停，核对恢复中拒启和最终ready；通过原始Client证据证明广播和清理。
6. 完成后保留一段空闲观察窗口，记录所有heartbeat失联/恢复事件，专门关注上一节完成后ACK告警。

### 9.3 证据包

保存Windows网络配置/路由、浏览器截图、脱敏后的HAR/控制台错误、文件SHA、job_id和时间；Server保存权威快照、双端控制日志、coordinator摘要、Client evidence、Server实际制品及生命周期检查。敏感头/凭据需脱敏。电脑物理拓扑只能由现场证据补齐，Server脚本不能凭管理IP自请求证明。

必要时在管理接口抓包核对数据面是否被错误路由：模型上传的HTTP走管理网是预期；UFTP1044、控制9000/9001和Client update PUT不应走管理网。结合TUN/无线端口、实际目标地址和逐包方向判断，不能只因管理接口有8080流量就判定绕过无线。

通过标准为“拓扑、电脑上传、浏览器创建/观察、真实无线数据闭环、传输急停、恢复与清理”全部有证据；保留短时告警和未解释事件，不能因页面显示成功自动抹去。

## 10. 10 Client 扩展验收计划

10 Client 指1个Server加10个Client，共11台角色设备。节点1～10、10个槽位或Web显示10行不等于10台实体设备通过。现阶段正式Stage3/Stage4 normal/abort入口是双Client基线，不得把它直接重命名为10 Client报告。

### 10.1 先使编排与校验覆盖真实目标集合

维护一个明确的`node_id → SSH维护别名 → 静态身份/TUN地址`拓扑集合，所有预检、READY屏障、作业请求、采集、终态检查、摘要和错误清理从同一集合派生。禁止复制client1/client2分支或靠字符串末位解析节点10。Server known_clients、调度目标与生产角色配置必须包含实际参与集合。现行 Server 已预置节点 1～10，但每项作业仍需显式提交全部目标；Web目标列表支持1～10并拒绝重复，不能由 known pool 自动推断全员参与。

正式入口扩展前先补离线失败路径：缺节点、重复身份、错轮、漏update、额外非目标update、失效证据、某节点恢复未ready、残留沙箱、受理不确定及故障规则未清理。无论参与多少节点，作业窗口仍禁止Client SSH补救。

射频补充入口已有重复`--client NODE_ID=SSH_HOST`集合，但其受控回退复用fl-c2故障规则，并依赖当前基线和三机验证过的机制；10 Client故障场景需明确定义施加节点及可能租约耗尽节点，不能假设三机预期在10节点自动成立。

### 10.2 当前时间与调度参数

| 参数 | 当前生产 Web 路径 | 10 Client 验收关注点 |
| --- | --- | --- |
| Server TASK_READY屏障 | 10秒 | 全目标节点必须到齐；逐节点记录本地启动与Server接收时间。 |
| Client TASK_READY ACK等待 | 8秒，socket重试间隔最多250 ms | 与Server的10秒预算不是同一个值，需两端证据。 |
| 每轮收update | 默认120秒 | Coordinator的有效update等待预算；不是整轮含下发的总墙钟限制。 |
| Web作业HTTP I/O | 固定120秒 | 当前Transport的socket/I/O操作超时，不等于整个PUT统一120秒总时限；按实际阻塞位置分析。 |
| 心跳 / OFFLINE | 空闲5秒、活动2秒；超过10秒投影OFFLINE | 区分已验证身份和活性，OFFLINE不会自动撤销身份；记录拥塞期间ACK缺失。 |
| Token Grant / Guard | 当前C++默认120 ms / 10 ms；daemon不覆写 | 从实际argv和启动日志确认；这是代码默认，尚无本次10 Client性能证明。 |

上述事实对应 `server_daemon.py` 的启动屏障和Web作业翻译、`control.py` 的TASK_READY/心跳、`coordinator.py` 的round预算、`transport.py` 的I/O、`src/v6_uplink.cpp` 的调度默认。HTTP和round可以在其他底层入口有不同配置，本表不把Web取值扩展为所有API的硬上限。历史180/15调度组合及15 ms feedback window是不同参数，不能相互代替。

### 10.3 容量和射频重新测量

- 计划以2、4、6、8、10 Client逐级运行相同canonical文件，每一级分别封存正常/急停证据；最终结论只声明实际跑过的规模。
- 每轮10份update共400 MiB；三轮仅Server收到的update本体就1,200 MiB，另有模型副本、最终模型、Server归档复制、失败保留和构建缓存。验收前按轮数和重复次数预算磁盘，1 GiB最低预检不足以覆盖该规模。
- 多Client共享UFTP下行会增加NAK反馈和重传放大；检查公共缺块、接收窗口、TUN队列drop和Server `txqueuelen`，不能只比较平均速率。
- 同时PUT造成调度等待、TCP RTT和Client HTTP watchdog压力。历史6 Client的180 ms/15 ms实验配置不是10 Client推荐值，也不能覆盖现行生产参数；记录当前实际argv/默认值再建立矩阵。
- 不靠任意滑动窗口限制客户端并发来假装稳定：Client上传计时已开始，排队可能耗尽预算。任何调度/超时变更需先规格化，再验证真实拥塞和公平性。
- 12 dBm和当前MCS只是近距三机基线，10 Client按实际位置重新测RSSI、重传和公平性；MCS、功率、频宽、UFTP速率分别改变，保持每次实验可归因。

### 10.4 每轮及终态判据

每轮必须收齐全部10个目标有效update，文件仿真各SHA均等于模型。按`(job_id, round_id, node_id)`校验唯一性和完整性；仅摘要count=10或全文件相同SHA不够。记录每节点模型接收、PUT开始/结束、Server接受时间，报告最慢节点和分布；全体sync的推进由最慢有效参与节点决定。

急停必须发生于当前作业真实活动传输，证明10个Client匹配终态并恢复，任何一个未恢复/未清理均不能判passed。恢复期间拒启需真实409证据；最终逐节点查daemon、任务进程、TUN、沙箱和evidence完整性。

统计下行重试/NAK、上传吞吐与尾延迟、READY耗时、heartbeat ACK间隙、offline投影和恢复时间。目标节点在忙传输期出现短暂offline投影时，同时检查其控制心跳与数据面事实，不能忽略，也不能仅凭UI离线就把成功数据认定为丢失。

10 Client全过程不能只完成一次“页面绿色”测试。至少保留正常、活动传输急停、恢复后新作业、射频重建及受控回退的独立证据；具体轮数、重复次数和预算在执行前写入本地规格。

## 11. 现场快速定位表

| 症状 | 先查的事实 | 本次经验 |
| --- | --- | --- |
| Web打不开 | 同一Server daemon、web_host、地址、已安装版本、实际监听、代理 | 启动旧包不会生成新Web功能。 |
| 页面永久“正在提交”而无Server新作业 | 浏览器控制台、实际POST是否发出、普通HTTP API可用性 | randomUUID在try外抛异常；刷新后用修复脚本。 |
| TASK_READY超时 | 目标Client收到任务、本地READY、job-scoped双向ACK、UDP接收线程是否阻塞 | PREPARING heartbeat Web回调锁阻塞曾影响Stage3/4共同路径。 |
| MCS重建立即E101 | TUN UP、CIDR、控制路由出口 | 名称存在不等于TUN可用；无需盲rebind。 |
| Client有GRANT但不能上行 | GRANT目标/拒绝原因、Server是否收到该节点裸READY | wrong_target不等于旧序号过滤；READY丢失位置需要抓包。 |
| 租约证据找不到 | session及实际耗尽节点、是否主动ABORT | Client2受故障不代表Client2一定耗尽。 |
| 程序成功但Client证据失败 | client_main结果、exit code、evidence、沙箱 | 文件仿真结束对None求SHA曾导致末尾异常。 |
| 正常结果被急停覆盖 | job分目录、同key受理和事件绑定 | 旧事件和覆盖文件不能满足本轮门禁。 |
| pytest导入/绑定失败 | deb_dist副本、测试根、现场8080服务 | 测试环境冲突要单独记录，不等同于生产失败。 |
| 完成后短时ACK告警 | 两端UDP逐包、空闲链路重建及watchdog状态 | 本次真实发生但原因未完全定位，不定性为假告警。 |

新验收应记录执行器/安装身份、拓扑、期望/实际结果和未解释事件，保留失败归档。只有独立机械校验和现场物理证据都满足预先定义的范围，才能写“通过”。
