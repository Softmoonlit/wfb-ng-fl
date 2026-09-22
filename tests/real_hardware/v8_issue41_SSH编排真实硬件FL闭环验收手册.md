# V8 issue #41 SSH 编排真实硬件 FL Runtime 闭环验收手册

本文是 GitHub issue #41（“v8: 补齐真实硬件 downlink 并验收完整 FL Runtime 闭环”）的专用验收规格和现场 runbook。它复用 v6 真实三机基线的无线、TUN、证据和归档经验，但本手册不是 v6 无 SSH 手动上行流程的改名版。

## 0. 定位与边界

### 0.1 本手册验证什么

本手册验证三台独立真实机器上，安装后的 V8 server/client systemd 角色服务能运行一轮完整 FL Runtime 严格同步闭环：

1. server 通过 Runtime `publish_model()` 发布模型。
2. Transport 用一次 shared UFTP group operation 下发 `model.bin` 和 `model.manifest.json`。
3. client1/client2 分别通过 Runtime `wait_for_model()` 收到并校验模型。
4. client1/client2 执行正式算法入口中的 `train()` 占位训练，并各自通过 Runtime `submit_update()` 发起单连接、单 PUT HTTP update。
5. server 通过 Runtime `wait_for_updates()` 在两个有效 update 全部收齐后返回按 `NODE_ID` 数值升序排列的完整映射，再执行正式算法入口中的 `aggregate()` 占位聚合；占位实现保持原模型内容不变。
6. 同一轮保留 UFTP feedback、v6 READY/GRANT、authorized sends、queue/backpressure、reassembly、systemd lifecycle 和无孤儿进程证据。

### 0.2 本手册不验证什么

- 不用 namespace、same-host 或 split-process 结果替代真实硬件。
- 不重跑旧普通 TCP 文件探针作为本次 #41 成功条件。
- 不用管理网 IP 传输模型或 update。
- 不把 SSH 编排、远端仓库同步、本机归档汇总角色写成正式产品语义。
- 不推广本次现场 IP、SSH 地址、网卡名或占位算法参数为正式默认值。
- 不把 smoke test 成功当作 Runtime 正式验收成功。

### 0.3 与 v6 手册的关系

`v6新底座无SSH手动上行演示手册.md` 是 v6 阶段的三机无 SSH 手动 uplink 基线手册，仍然有参考价值：无线配置、TUN 规划、READY/GRANT、queue summary、正式摘要和归档思路都应复用。

本手册是 V8 issue #41 专用 SSH 编排验收手册。V8 #41 不把旧手册改造成 SSH/Runtime 手册，而是在新脚本中复用可用经验，并在最终 `result.md` 与 `issue41_summary.json` 中统一管理五个分区：

- `orchestration`：动态拓扑扫描、版本与环境一致性、8 层 fail-closed 预检。
- `pre_runtime_smoke`：同组常驻进程下的连续三周期双向数据面 Gate 验收事实。
- `formal_runtime_loop`：严格等价链路配置下的正式 Runtime 四接口严格同步单轮闭环。
- `lifecycle`：systemd 角色服务 stop/restart/no-overlap、cgroup 及资源清理生命周期审计。
- `conclusion`：全流程不可篡改的最终判定结论（包含失败分类与分层定位）。

## 1. 现场拓扑

### 1.1 角色机

Stage 2 现场基准拓扑由 8 台机器构成（1 台 Server 本机 + 7 台 Client 远端机）：

| 验证阶段身份 | 正式角色 | SSH 管理别名 | 远端仓库路径 | 典型 NODE_ID |
| --- | --- | --- | --- | --- |
| server / orchestrator / 归档汇总机 | server | 本机 (local) | `/home/virt/projects/wfb-ng-fl` | `255` |
| client1 | client | `vm1` | `/home/virt/projects/wfb-ng-fl` | `1` |
| client2 | client | `vm2` | `/home/virt/projects/wfb-ng-fl` | `2` |
| client3 | client | `vm3` | `/home/virt/projects/wfb-ng-fl` | `3` |
| client4 | client | `vm4` | `/home/virt/projects/wfb-ng-fl` | `4` |
| client5 | client | `vm5` | `/home/virt/projects/wfb-ng-fl` | `5` |
| client6 | client | `vm6` | `/home/virt/projects/wfb-ng-fl` | `6` |
| client7 | client | `vm7` | `/home/virt/projects/wfb-ng-fl` | `7` |

说明：

- `orchestrator` 和“归档汇总机”只是验证阶段的编排身份；数据面只把本机视为 server 角色机。
- `192.168.122.*` 管理网只用于 SSH、远端命令执行和证据拉取，严禁作为 FL 数据面成功证据。
- 脚本支持通过环境变量 `ISSUE41_CLIENT_ROLES` 动态配置客户端集合（默认覆盖 `client1 client2 client3 client4 client5 client6 client7`）。

### 1.2 无线网卡（动态自动检测）

所有 8 台机器的无线网卡选择规则统一遵循**全自动动态探测**原则：
- 通过执行 `find_wlx` 解析本机与各远端机器上 `iw dev` 输出中 `Interface` 字段以 `wlx` 开头的接口。
- **严禁给每个机器硬编码指定网卡名**。每台机器只要接入合法的 `rtl88xxau_wfb` USB 无线网卡（名称动态呈现为 `wlx*`，如 `wlx000f00...` 或 `wlxfc221...`），脚本均通过 `find_wlx` 自动识别、验证、切换 monitor 模式并绑定。
- 每台机器必须恰好动态发现 1 个 `wlx*` 接口；发现 0 个或多于 1 个时严格 fail-closed 退出。
- 脚本在预检与启动时自动接管识别出的 `wlx*` 接口（down/up、设置 monitor、固定信道、UP 状态断言），不触碰任何其他非空口网卡。

### 1.3 TUN 与数据面地址

FL 数据面必须完全走 WFB/TUN/无线链路：

| 角色 | TUN 名称 | TUN 地址 | NODE_ID | UFTP UID |
| --- | --- | --- | --- | --- |
| server | `v8i41s0` | `10.80.0.1/24` | `255` | `0x000000ff` |
| client1 | `v8i41c1` | `10.80.0.11/24` | `1` | `0x00000001` |
| client2 | `v8i41c2` | `10.80.0.12/24` | `2` | `0x00000002` |
| client3 | `v8i41c3` | `10.80.0.13/24` | `3` | `0x00000003` |
| client4 | `v8i41c4` | `10.80.0.14/24` | `4` | `0x00000004` |
| client5 | `v8i41c5` | `10.80.0.15/24` | `5` | `0x00000005` |
| client6 | `v8i41c6` | `10.80.0.16/24` | `6` | `0x00000006` |
| client7 | `v8i41c7` | `10.80.0.17/24` | `7` | `0x00000007` |

Transport 参数：

| 项 | 值 |
| --- | --- |
| UFTP multicast/group | `239.80.41.1` |
| UFTP port | `1044` |
| UFTP 发送速率 | `15000 Kbps`（15 Mbps） |
| server HTTP bind | `10.80.0.1:8080` |
| server work_dir | `/var/lib/wfb-ng/issue41/server` |
| client work_dir | `/var/lib/wfb-ng/issue41/client` |
| smoke 临时目录 | `/var/tmp/wfb-ng-issue41-smoke` |

无线默认参数沿用 v6 真实硬件基线：

| 项 | 默认值 |
| --- | --- |
| channel | `157` |
| channel width | `HT40+` |
| link id | `406` |
| uplink stream | `32` |
| downlink stream | `33` |
| radio bandwidth | `40` |
| radio MCS index | `3` |
| radio short GI | `1` |
| FEC K/N | `8/12` |

## 2. 代码基线同步

### 2.1 分支来源

本次 #41 重新执行从提交：

```text
efcbbfef088a3cc03f0496e703472d7a4bff3135
```

新建分支：

```text
feat/41-real-hardware-fl-runtime-redo
```

### 2.2 push 是显式步骤

`run-all` 不自动 push。对外发布必须使用显式子命令，例如：

```bash
bash tests/real_hardware/issue41_fl_runtime_loop.sh push-branch
```

规则：

- 本机工作区必须干净。
- 当前 HEAD 必须是准备验收的 commit。
- push 到 `origin` 当前分支。
- 不在启动、同步或运行阶段隐式发布代码。

### 2.3 远端同步策略

远端同步由本机通过 SSH 控制 client1/client2 完成：

```bash
bash tests/real_hardware/issue41_fl_runtime_loop.sh sync-remotes
```

规则：

1. 本机 `origin/<branch>` 必须存在，且与本机 HEAD 一致。
2. client1/client2 执行 `git fetch origin`。
3. client1/client2 切换到同名分支并 `git reset --hard origin/<branch>`。
4. 远端当前分支若存在领先提交，不创建备份分支，允许直接切走。
5. 远端若存在未提交修改或未跟踪文件，默认失败；只有显式 force 模式才允许丢弃。
6. 不执行整仓库 `git clean -xfd`；清理只限 issue41 脚本明确管理的目录。
7. 三台机器运行前必须同 branch、同 commit、`git status --short` 干净。

归档必须记录三台机器：

- `hostname`
- `whoami`
- repo path
- `git remote -v`
- branch
- commit
- `git status --short --branch`

## 3. 安装与 systemd 装配

### 3.1 自动 build/install

issue #41 脚本允许在三台机器上自动执行：

```bash
make build_v6
sudo make install_v8
sudo systemctl daemon-reload
```

原因：仓库同步后如果不重新安装，systemd 可能仍运行旧产物，验收证据无法归因到当前 commit。

安装日志和以下检查必须归档：

- `command -v wfb-fl-server`
- `command -v wfb-fl-client`
- `command -v wfb_v6_uplink`
- `command -v uftp`
- `command -v uftpd`
- `systemctl cat wfb-fl-server.service`
- `systemctl cat wfb-fl-client.service`

### 3.2 issue41 专用配置与 drop-in

本次不覆盖正式默认 `/etc/wfb-ng/fl-server.json`、`/etc/wfb-ng/fl-client.json`，不修改包安装的 vendor unit 文件。

server 写入：

```text
/etc/wfb-ng/issue41/fl-server.json
/etc/wfb-ng/issue41/server-algorithm.json
/etc/systemd/system/wfb-fl-server.service.d/issue41.conf
```

client1/client2 写入：

```text
/etc/wfb-ng/issue41/fl-client.json
/etc/wfb-ng/issue41/client-algorithm.json
/etc/systemd/system/wfb-fl-client.service.d/issue41.conf
```

systemd drop-in 使用标准 `ExecStart=` 清空并重设入口，例如 server：

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/wfb-fl-server --config /etc/wfb-ng/issue41/fl-server.json --algorithm wfb_ng.fl.issue41_algorithm:server_main --algorithm-config /etc/wfb-ng/issue41/server-algorithm.json
```

client 类似，使用 `wfb_ng.fl.issue41_algorithm:client_main`。

若 issue #41 验证完全通过，这种“外部配置选择角色基础设施配置、算法入口和算法作业配置”的结构可作为后续正式版配置形态候选；但本次现场 SSH、网卡、占位数据 seed、训练/聚合延时和 issue41 work_dir 不直接推广为正式默认值。

## 4. 验收阶段

脚本以分阶段为主，`run-all` 严格按序串联全部阶段，不隐藏额外动作，不自动 push。

推荐阶段执行顺序：

```bash
bash tests/real_hardware/issue41_fl_runtime_loop.sh push-branch
bash tests/real_hardware/issue41_fl_runtime_loop.sh sync-remotes
bash tests/real_hardware/issue41_fl_runtime_loop.sh preflight
bash tests/real_hardware/issue41_fl_runtime_loop.sh install
bash tests/real_hardware/issue41_fl_runtime_loop.sh smoke-gate
bash tests/real_hardware/issue41_fl_runtime_loop.sh verify-config-equivalence
bash tests/real_hardware/issue41_fl_runtime_loop.sh run-runtime-loop
bash tests/real_hardware/issue41_fl_runtime_loop.sh lifecycle-stop-restart
bash tests/real_hardware/issue41_fl_runtime_loop.sh collect
bash tests/real_hardware/issue41_fl_runtime_loop.sh summary
```

稳定环境可使用唯一正式总入口：

```bash
bash tests/real_hardware/issue41_fl_runtime_loop.sh run-all
```

`run-all` 默认在任意阶段发生失败时进入 fail-closed 受控停止收尾并完整保留归档；显式排障模式可用：

```bash
ISSUE41_KEEP_RUNNING_ON_FAIL=1 bash tests/real_hardware/issue41_fl_runtime_loop.sh run-all
```

排障模式尽量保留现场进程，但该归档在 envelope 中记录 mode 为 diagnostic，归档校验器拒绝将其实施 formal 验收标记。

### 4.1 preflight

preflight 检查（执行 8 层 fail-closed 预检）：

- SSH 免密或凭据方式可用（`ssh_and_sudo` 层）。
- 三台机器 sudo 可用。
- 三台机器仓库 branch/commit/status 一致，无工作区污染（`repo_and_version` 层）。
- 每台机器恰好一个 `wlx*` 接口，或显式指定（`wireless_usb` 层）。
- 三个角色统一归档无线接口 sysfs path、驱动、USB speed、USB 拓扑、接口统计和相关 kernel journal；该规则不依赖 client 是 VM USB passthrough 还是真实机器。
- USB speed 默认建议至少为 `480 Mbit/s`。低于建议值时默认明确告警并继续，现场 USB 受限时仍可验收，但结论必须保留硬件风险证据；设置 `ISSUE41_STRICT_USB_SPEED=1` 后低于 `ISSUE41_RADIO_MIN_USB_SPEED`（默认 `480`）才 fail-closed。
- 脚本不默认自动 reset 或重新枚举 USB 网卡，避免 VM passthrough 或真实机器设备身份被意外改变；需要时由操作者显式处理硬件后重跑。
- `rfkill` 未 soft blocked，信道支持 monitor（`radio_monitor_channel` 层）。
- `ip`、`iw`、`systemctl`、`journalctl`、`make`、`python3`、`uftp`、`uftpd` 等依赖存在（`dependencies` 层）。
- TUN 名称不存在，端口未占用（`network_ports_and_tun` 层）。
- `/var/lib/wfb-ng/issue41/*` 和 `/var/tmp/wfb-ng-issue41-smoke` 清理边界可确认（`managed_directory_boundary` 层）。
- 没有旧 `wfb-fl-*`、`wfb_v6_uplink`、`uftp`、`uftpd` 残留进程（`residual_processes` 层）；若是上次 issue41 残留，由安全停止与受控重置处理。
- 预检失败时自动归类为 `environment`、`tooling`、`implementation` 或 `link_capability` 失败分类并写入审计归档。

### 4.2 smoke-gate（连续三周期双向数据面 Gate 验收）

目的：在正式 Runtime 启动前，以同一组常驻数据面进程连续运行 3 个双向数据传输周期，证明无线链路、TUN 与传输栈具备承载 FL 闭环的直接传输能力与遥测事实。

边界与原则：
- 三机上的 `wfb_v6_uplink`、client1/client2 的 `uftpd` 和 server 的 HTTP PUT receiver 进程必须常驻，严禁在周期之间重启进程、重置网卡或修改链路参数。
- 每个周期均包含：一次 shared UFTP 下行 4 MiB 载荷到两客户端，以及两个客户端各发起一次独立的 4 MiB HTTP PUT 上行。
- 单次 I/O 无进展超时固定为 120 秒（`SMOKE_IO_TIMEOUT_SECONDS=120`），单周期完成总耗时必须严格在 240 秒固定 deadline 内（`SMOKE_CYCLE_DEADLINE_SECONDS=240`）。
- 成功准则包含双端直接事实：UFTP CONNECT 矩阵 `success`、model/manifest 均成功 `copy` 且 SHA-256 匹配；HTTP PUT 上行均返回 HTTP 201 且 committed SHA-256 匹配；活动上传集合（active uploads）收敛为空；遥测事实（READY/GRANT、queue pause/resume 恢复、反压无丢弃、重组无溢出、隔离无注入）完整。
- 失败时 fail-closed 受控停止进程，采集诊断证据，并生成记录 `last_successful_layer` 与 `first_failing_layer` 的 failed 归档，绝对禁止启动正式 Runtime。

### 4.3 verify-config-equivalence（严格链路配置等价性核验）

目的：确保将要在正式 Runtime 使用的 systemd 角色服务配置（`/etc/wfb-ng/issue41/fl-server.json`、`fl-client.json`）与通过 Gate 验收的链路配置严格等价。

等价性比对维度：
- 无线参数：channel、channel_width、MCS index、FEC K/N、short GI。
- 数据平面与 TUN：IP 地址段、TUN 名称、MTU。
- 队列与流控：pause threshold、resume threshold、packets limit。
- 反馈窗口：feedback window period、duration。
- 运行时超时期望：smoke cycle deadline（240s）、runtime timeout（180s/400s）、io timeout（120s）。
- 严禁携带候选行为参数（如 `--feedback-window-start-immediately`）。
- 比对不通过时立即 fail-closed 终止，禁止启动正式 Runtime。

### 4.4 run-runtime-loop（正式 Runtime 闭环）

目的：通过安装后的 systemd 角色服务运行正式 Runtime 四接口闭环。

边界：

- 必须使用 `wfb-fl-server.service` 和 `wfb-fl-client.service`。
- 必须使用 issue41 drop-in 和 `/etc/wfb-ng/issue41/*` 配置。
- 数据面必须走 `10.80.0.0/24` TUN 地址。
- 只有 Runtime 四接口驱动正式闭环，不能用 ping、普通 TCP 文件探针或人工复制替代。
- server `link_args` 使用 `ISSUE41_FEEDBACK_WINDOW_PERIOD_MS`（默认 `500`）、`ISSUE41_FEEDBACK_WINDOW_DURATION_MS`（默认 `15`）。正式验收配置严禁携带 `--feedback-window-start-immediately` 候选行为；链路按既有调度维持 feedback window。
- `ISSUE41_IO_TIMEOUT_SECONDS` 默认 `120`，用于在单次 I/O 连续无进展时 fail-fast。不得通过增大该值掩盖无线丢包、FEC 后残余丢包或 TCP 退避；这些链路问题必须单独诊断和修复。
- P0 只消除 UFTP 注册对 READY 的启动依赖；它不实现 UFTP/HTTP 阶段隔离，短、长 GRANT 仍可能交错。动态 feedback lease、可靠上行阶段 release 和 client `submit_update(...)` 门禁属于后续设计，不是本次验收行为。

重试与失败现场：

- Runtime 失败时默认保留 `/var/lib/wfb-ng/issue41/{server,client}`，用于采集失败证据。
- `run-runtime-loop` 启动前会停止 issue41 相关服务并检查无残留进程；检测到旧 work_dir 时默认拒绝复用，避免旧的 `runtime.lock`、`uftp-tmp` 或未完成轮次影响新测试。
- 排障完成后，推荐显式清理再重试：

```bash
bash tests/real_hardware/issue41_fl_runtime_loop.sh clean
bash tests/real_hardware/issue41_fl_runtime_loop.sh run-runtime-loop
```

- 如确认不需要保留旧 work_dir，也可以使用显式重置：

```bash
ISSUE41_RESET_RUNTIME_STATE=1 \
  bash tests/real_hardware/issue41_fl_runtime_loop.sh run-runtime-loop
```

该选项只清理 issue41 管理的 Runtime work_dir，不删除归档、仓库或其他系统目录。未显式设置时脚本不得删除失败现场。

正式算法入口与占位算法：

- 模型和 update 对 Runtime 及当前占位算法都是不透明普通文件，不要求 JSON、checkpoint 或特定框架格式。
- 正式场景支持单轮/多轮及参数化载荷大小（如 4 MiB 或 40 MiB）。server 输入模型和各 client 模板均必须恰好为指定载荷大小；`preflight` 在 live run 前检查它们，不生成或修改输入。
- 全部 7 个客户端模板必须具有互不相同的 SHA-256。默认路径为 `model-<size>b.bin` 和 `update-client<N>-<size>b.bin`。
- 各 client 调用 `wait_for_model()`；模型 manifest、参与集合、大小和 SHA-256 由 Runtime 校验。模型校验完成后客户端执行占位训练。
- `train(model_path, output_update_path, config)` 的占位训练只复制必填 `update_template_path`，synthetic update 不来自真实训练。server `aggregate(...)` 的占位聚合只复制当前模型，不执行或声称执行 FedAvg。
- server `wait_for_updates()` 只在所有活跃客户端（如 `[1, 2, 3, 4, 5, 6, 7]`）的有效 update 全部收齐后返回完整映射；部分 update 到达时不返回 partial result。
- 每轮在算法结果和 systemd journal 中记录独立轮次标识、模型接收及 PUT 时间区间、大小、SHA-256、活动上传集合与最终 HTTP 结果。任何 `upload_in_progress` 或 client/server 提交事实不一致均不能通过。
- `server_main/client_main` 只负责 Runtime 编排。接入真实算法时保留 `train()`、`aggregate()` 的函数签名和输出文件约定，只替换两个函数内部实现。
- 结果事件必须包含 client 的 `train_start/train_done` 和 server 的 `aggregate_start/aggregate_done`；模型/update 的路径、大小和 SHA-256 必须进入算法结果 JSON。
- 本次占位训练与聚合用于证明正式算法执行边界和真实文件体量传输，不代表最终业务模型精度验收。

运行前分别在各台机器准备输入文件。可直接使用 `bash tests/real_hardware/issue41_fl_runtime_loop.sh generate-fixtures` 自动在全集群生成各客户端唯一的确定性模板：

```bash
# server 本机：操作者提供的初始模型
/var/lib/wfb-ng/issue41-input/model-4194304b.bin

# client1~client7：各 client 唯一的 synthetic update 模板
/var/lib/wfb-ng/issue41-input/update-client1-4194304b.bin
...
/var/lib/wfb-ng/issue41-input/update-client7-4194304b.bin
```

先运行 `preflight`。它会 fail-closed 检查所有输入的普通文件、可读性、恰好对应大小，以及各 client 模板 SHA-256 互不相同。输入位于 Runtime work_dir 外，live run 和 `clean` 都不会生成、修改或删除它们。

正式单轮运行使用：

```bash
ISSUE41_IO_TIMEOUT_SECONDS=120 \
ISSUE41_RUNTIME_TIMEOUT_SECONDS=400 \
  bash tests/real_hardware/issue41_fl_runtime_loop.sh run-runtime-loop
```

需要使用其他位置时，分别设置：

```text
ISSUE41_INITIAL_MODEL_PATH
ISSUE41_CLIENT1_UPDATE_TEMPLATE_PATH
ISSUE41_CLIENT2_UPDATE_TEMPLATE_PATH
```

`preflight` 会在 server 本机检查初始模型，并通过 SSH 在 client1/client2 检查各自 update 模板；任一文件缺失、不是普通文件、不可读、大小不是 4 MiB 或两份模板摘要相同都会 fail-closed。

必需归档：

- server/client algorithm result JSON。
- server/client Runtime `round-state.json`。
- model/update manifest。
- model/update SHA-256 清单。
- server UFTP `status`/`log`。
- HTTP PUT client success fact 和 server committed update fact。
- journal、systemctl status、systemd-cgls。
- v6 READY/GRANT、authorized sends、queue/reassembly、feedback 证据。

### 4.5 lifecycle-stop-restart

Runtime loop 结束后验证 systemd lifecycle：

1. 受控停止 server/client services。
2. 检查 unit inactive。
3. 检查 cgroup 无进程。
4. 检查无属于本次 issue41 的 `wfb-fl-*`、`wfb_v6_uplink`、`uftp`、`uftpd` 孤儿进程。
5. Runtime 成功时执行 restart/no-overlap 验证。
6. 再次 stop 并检查无孤儿。

lifecycle 失败时，即使 Runtime loop 成功，#41 总结论仍为失败。

## 5. 归档与结论

### 5.1 归档目录

本机每次运行新建归档目录，不覆盖旧目录：

```text
tests/logs/v8_issue41_<timestamp>/
```

建议结构：

```text
tests/logs/v8_issue41_<timestamp>_<token>/
  envelope.json
  orchestration/
    topology.json
    preflight_result.json
    radio-health/
  pre_runtime_smoke/
    gate_summary.json
    passed.json (或 failed.json)
    server/
    client1/
    client2/
  formal_runtime_loop/
    config_equivalence.json
    controlled_stop.json
    server/
      issue41-server-result.json
    client1/
      issue41-client1-result.json
    client2/
      issue41-client2-result.json
  lifecycle/
    initial_pids.json
    lifecycle_summary.json
    first_stop_evidence.json
    restart_evidence.json
    second_stop_evidence.json
  raw/
    server-journal.txt
    client1-journal.txt
    client2-journal.txt
    *-route-*.txt
  result.md
  issue41_summary.json
```

### 5.2 summary 分区

结构化 `issue41_summary.json` 严格包含五个分区，且顶层 `run_id` 必须与 `envelope.json` 严格一致：

```json
{
  "schema_version": 1,
  "run_id": "v8_issue41_<timestamp>_<token>",
  "orchestration": {
    "status": "passed",
    "radio_health_dir": "..."
  },
  "pre_runtime_smoke": {
    "schema_version": 1,
    "run_id": "v8_issue41_<timestamp>_<token>",
    "gate_type": "three_cycle_bidirectional",
    "status": "passed",
    "reused_processes": {...},
    "cycles": [...]
  },
  "formal_runtime_loop": {
    "status": "passed",
    "runtime_interfaces": ["publish_model", "wait_for_model", "submit_update", "wait_for_updates"],
    "data_plane": "10.80.0.0/24",
    "config_equivalence": {"status": "passed"},
    "controlled_stop": {"status": "passed"},
    "scenario": {...},
    "rounds": [...]
  },
  "lifecycle": {
    "schema_version": 1,
    "run_id": "v8_issue41_<timestamp>_<token>",
    "status": "passed",
    "first_stop": {...},
    "restart": {...},
    "second_stop": {...}
  },
  "conclusion": {
    "status": "passed",
    "reason": "..."
  }
}
```

管理面日志可以进入归档，但必须只作为编排证据。任何 `192.168.122.*` 管理网传输不得计入 FL 数据面成功事实。

### 5.3 fail-closed 与阶段证据保留

任何关键证据缺失都不能报告成功。在任一阶段失败时，必须保证：
1. 立即受控停止本机和远端服务及临时进程。
2. 完整保留此前已成功执行阶段的证据分区（如 Gate 成功证据、前序遥测与日志）。
3. 记录第一失败层（`first_failing_layer`）、最后成功层（`last_successful_layer`）、失败分类（`failure_category`）与具体原因（`failure_reason`）。
4. 生成标记为 `failed` 的 `result.md` 与 `issue41_summary.json`，且保证生成的失败归档自身可被 `issue41_validate_archive.py` 完整审计。
5. 不删除 Runtime work_dir 与 raw 日志，留待排障。

显式 `clean` 子命令才允许清理脚本管理的目录，并且必须先停止服务、确认无相关 PID，再列出并删除：

- `/var/lib/wfb-ng/issue41/server`
- `/var/lib/wfb-ng/issue41/client`
- `/var/tmp/wfb-ng-issue41-smoke`
- `/etc/wfb-ng/issue41/*`
- issue41 systemd drop-in

不得删除用户仓库，不得整仓库 `git clean -xfd`。

## 6. 通过条件

#41 通过必须同时满足：

1. 三台机器运行同一 branch、同一 commit，工作区干净。
2. 三台机器通过安装后的产物和 systemd 角色服务运行正式 Runtime loop。
3. 8 层 fail-closed 预检通过，拓扑与版本信息完整入库。
4. 连续三周期双向数据面 Gate 验收在同一组常驻进程下全部通过，单周期完成时间在 240s deadline 内，单次 I/O 无 120s stall。
5. 角色服务配置与 Gate 链路配置严格等价核验通过，参数未被中途修改，且未携带任何候选 feedback 特性。
6. Runtime 正式闭环通过：`publish_model`、两个 `wait_for_model`、两个 `submit_update`、`wait_for_updates` 全部经正式接口完成。
7. shared UFTP downlink 每轮一次，两个 client 的 model 和 manifest status matrix 完整。
8. 单轮中两个 client 的模型 SHA 均与 server 一致；每个 4 MiB update 的 SHA、大小、客户端 HTTP 201 与 server committed 事实一致。
9. 每轮均记录独立轮次标识、模型接收和 PUT 时间区间、活动上传集合及最终 HTTP 结果；不得出现 `upload_in_progress`，server 不返回 partial result，最终返回 `[1, 2]` 完整映射。
10. v6 直接依赖证据完整：READY/GRANT、authorized sends、server 接收、queue pause/resume 恢复、bytes/packets 反压、reassembly、sender isolation、feedback window。
11. lifecycle stop/restart/no-overlap（PID 不复用、cgroup 无交集）和无孤儿进程验证通过。
12. 归档目录未被覆盖，非 diagnostic 模式，顶层及各阶段 run_id 一致。
13. `result.md` 和 `issue41_summary.json` 完整包含全部五个分区，并通过 `issue41_validate_archive.py` 严格校验。
14. 任何失败均生成 failed 结论并准确分类，严禁缺证据或降级报告成功。
