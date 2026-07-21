# V8 issue #41 SSH 编排真实硬件 FL Runtime 闭环验收手册

本文是 GitHub issue #41（“v8: 补齐真实硬件 downlink 并验收完整 FL Runtime 闭环”）的专用验收规格和现场 runbook。它复用 v6 真实三机基线的无线、TUN、证据和归档经验，但本手册不是 v6 无 SSH 手动上行流程的改名版。

## 0. 定位与边界

### 0.1 本手册验证什么

本手册验证三台独立真实机器上，安装后的 V8 server/client systemd 角色服务能运行一轮完整 FL Runtime 严格同步闭环：

1. server 通过 Runtime `publish_model()` 发布模型。
2. Transport 用一次 shared UFTP group operation 下发 `model.bin` 和 `model.manifest.json`。
3. client1/client2 分别通过 Runtime `wait_for_model()` 收到并校验模型。
4. client1/client2 执行确定性训练 fixture，并各自通过 Runtime `submit_update()` 发起单连接、单 PUT HTTP update。
5. server 通过 Runtime `wait_for_updates()` 在两个有效 update 全部收齐后返回按 `NODE_ID` 数值升序排列的完整映射。
6. 同一轮保留 UFTP feedback、v6 READY/GRANT、authorized sends、queue/backpressure、reassembly、systemd lifecycle 和无孤儿进程证据。

### 0.2 本手册不验证什么

- 不用 namespace、same-host 或 split-process 结果替代真实硬件。
- 不重跑旧普通 TCP 文件探针作为本次 #41 成功条件。
- 不用管理网 IP 传输模型或 update。
- 不把 SSH 编排、远端仓库同步、本机归档汇总角色写成正式产品语义。
- 不推广本次现场 IP、SSH 地址、网卡名或 fixture 参数为正式默认值。
- 不把 smoke test 成功当作 Runtime 正式验收成功。

### 0.3 与 v6 手册的关系

`v6新底座无SSH手动上行演示手册.md` 是 v6 阶段的三机无 SSH 手动 uplink 基线手册，仍然有参考价值：无线配置、TUN 规划、READY/GRANT、queue summary、正式摘要和归档思路都应复用。

本手册是 V8 issue #41 专用 SSH 编排验收手册。V8 #41 不把旧手册改造成 SSH/Runtime 手册，而是在新脚本中复用可用经验，并在最终 `result.md` 中明确区分：

- `baseline_uplink`：v6 旧手册/旧脚本定义的成熟基线口径，本次不重跑普通 TCP 探针。
- `pre_runtime_smoke`：本次新增的 UFTP downlink 与 HTTP PUT uplink 基础设施冒烟。
- `formal_runtime_loop`：本次正式 Runtime 四接口闭环。
- `lifecycle`：systemd stop/restart、cgroup 和无孤儿进程证据。

## 1. 现场拓扑

### 1.1 角色机

| 验证阶段身份 | 正式角色 | SSH 管理地址 | 已探测仓库 | 已探测主机名 |
| --- | --- | --- | --- | --- |
| server / orchestrator / 归档汇总机 | server | 本机 | `/home/kilome/code/wfb-ng-fl` | 本机 hostname 归档时记录 |
| client1 | client | `virt@192.168.122.198` | `/home/virt/code/wfb-ng-fl` | `virt1` |
| client2 | client | `virt@192.168.122.106` | `/home/virt/code/wfb-ng-fl` | `virt2` |

说明：

- `orchestrator` 和“归档汇总机”只是 issue #41 验证阶段的辅助身份；正式版只把本机视为 server 角色机。
- `192.168.122.*` 管理网只用于 SSH、远端命令执行和证据拉取，不能作为 FL 数据面成功证据。
- 密码不写入本文档、脚本、日志模板或归档。脚本优先使用 SSH key；如需密码，必须通过临时环境变量或交互式输入传递，且不得回显。

### 1.2 无线网卡

三台机器的默认网卡选择规则：解析 `iw dev` 输出中 `Interface` 后以 `wlx` 开头的接口。每台机器必须恰好发现一个 `wlx*` 接口；发现 0 个或多个时默认 fail-closed，要求人工显式指定。

本次已探测接口：

| 角色 | `wlx*` 接口 |
| --- | --- |
| server | `wlxfc221c300cbb` |
| client1 | `wlxfc221c500a88` |
| client2 | `wlxfc221c300cbc` |

issue #41 脚本允许自动接管这一个 `wlx*` 接口：down/up、设置 monitor、固定信道、验证状态。脚本不得操作其他网络接口。

### 1.3 TUN 与数据面地址

FL 数据面必须走 WFB/TUN/无线链路：

| 角色 | TUN 名称 | TUN 地址 | NODE_ID | UFTP UID |
| --- | --- | --- | --- | --- |
| server | `v8i41s0` | `10.80.0.1/24` | `255` | `255` |
| client1 | `v8i41c1` | `10.80.0.11/24` | `1` | `1` |
| client2 | `v8i41c2` | `10.80.0.12/24` | `2` | `2` |

Transport 参数：

| 项 | 值 |
| --- | --- |
| UFTP multicast/group | `239.80.41.1` |
| UFTP port | `1044` |
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
| radio MCS index | `1` |
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

若 issue #41 验证完全通过，这种“外部配置选择角色基础设施配置、算法入口和算法作业配置”的结构可作为后续正式版配置形态候选；但本次现场 SSH、网卡、fixture seed、training delay 和 issue41 work_dir 不直接推广为正式默认值。

## 4. 验收阶段

脚本以分阶段为主，`run-all` 只串联阶段，不隐藏额外动作，不自动 push。

推荐阶段：

```bash
bash tests/real_hardware/issue41_fl_runtime_loop.sh push-branch
bash tests/real_hardware/issue41_fl_runtime_loop.sh sync-remotes
bash tests/real_hardware/issue41_fl_runtime_loop.sh preflight
bash tests/real_hardware/issue41_fl_runtime_loop.sh install
bash tests/real_hardware/issue41_fl_runtime_loop.sh smoke-downlink-uftp
bash tests/real_hardware/issue41_fl_runtime_loop.sh smoke-uplink-http-put
bash tests/real_hardware/issue41_fl_runtime_loop.sh run-runtime-loop
bash tests/real_hardware/issue41_fl_runtime_loop.sh lifecycle-stop-restart
bash tests/real_hardware/issue41_fl_runtime_loop.sh collect
bash tests/real_hardware/issue41_fl_runtime_loop.sh summary
```

稳定环境可使用：

```bash
bash tests/real_hardware/issue41_fl_runtime_loop.sh run-all
```

`run-all` 默认失败时进入 fail-closed 收尾；显式排障模式可用：

```bash
ISSUE41_KEEP_RUNNING_ON_FAIL=1 bash tests/real_hardware/issue41_fl_runtime_loop.sh run-all
```

排障模式尽量保留现场进程，但该归档必须标记为 debug mode，不作为正式通过证据。

### 4.1 preflight

preflight 检查：

- SSH 免密或凭据方式可用。
- 三台机器 sudo 可用。
- 三台机器仓库 branch/commit/status 一致。
- 每台机器恰好一个 `wlx*` 接口，或显式指定。
- 三个角色统一归档无线接口 sysfs path、驱动、USB speed、USB 拓扑、接口统计和相关 kernel journal；该规则不依赖 client 是 VM USB passthrough 还是真实机器。
- USB speed 默认建议至少为 `480 Mbit/s`。低于建议值时默认明确告警并继续，现场 USB 受限时仍可验收，但结论必须保留硬件风险证据；设置 `ISSUE41_STRICT_USB_SPEED=1` 后低于 `ISSUE41_RADIO_MIN_USB_SPEED`（默认 `480`）才 fail-closed。
- 脚本不默认自动 reset 或重新枚举 USB 网卡，避免 VM passthrough 或真实机器设备身份被意外改变；需要时由操作者显式处理硬件后重跑。
- `rfkill` 未 soft blocked。
- `ip`、`iw`、`systemctl`、`journalctl`、`make`、`python3`、`uftp`、`uftpd` 等依赖存在。
- TUN 名称不存在。
- `10.80.0.1:8080`、UFTP port 未被未知进程占用。
- `/var/lib/wfb-ng/issue41/*` 和 `/var/tmp/wfb-ng-issue41-smoke` 清理边界可确认。
- 没有旧 `wfb-fl-*`、`wfb_v6_uplink`、`uftp`、`uftpd` 残留进程；若是上次 issue41 残留，可由 stop/clean 子命令处理。

USB 严格检查示例：

```bash
ISSUE41_STRICT_USB_SPEED=1 ISSUE41_RADIO_MIN_USB_SPEED=480 \
  bash tests/real_hardware/issue41_fl_runtime_loop.sh preflight
```

READY 诊断必须区分两个事实：client 日志中的 `first_declare` 只表示本地尝试发送 READY，server 日志中的 `ready_accept` 才表示声明已送达并进入普通活跃队列。当前正式实现只有单层活跃队列；未送达的 READY 不是一次“降为睡眠节点”的状态转换。对于 issue #41 固定参与集合 `[1,2]`，链路层暂时不活跃不能改变 Runtime 严格同步参与集合。

Issue #41 P0 的正式 Runtime server 额外启用立即开始的静态 feedback window：它按 `[1,2]` 轮发短 GRANT，不以 `ready_accept` 为 UFTP REGISTER 的前提。client 仍可能发出 READY，且 server 仍可记录它，但 UFTP downlink 失败不能仅因缺少 `ready_accept` 归类为链路健康失败；必须同时检查 UFTP status matrix、feedback window、短 GRANT 和 `feedback_uplink_hit` 证据。

### 4.2 smoke-downlink-uftp

目的：只验证真实 TUN/无线上的 shared UFTP downlink 和 feedback 基础设施。

边界：

- 使用临时进程，不使用 systemd 角色服务。
- 可直接调用原生 `uftp`/`uftpd`。
- 不经过 Runtime 四接口。
- 成功不能替代正式 Runtime 验收。

行为：

1. 三台机器启动临时 `wfb_v6_uplink` 链路进程。
2. client1/client2 启动临时 `uftpd -I <client_tun_ip> -M 239.80.41.1`，加入与 server 一致的公共组播地址。
3. server 执行一次原生 `uftp -I 10.80.0.1 -M 239.80.41.1 -p 1044 -U 0x000000ff -H 0x00000001,0x00000002 ...`。
4. 下发小模型样例和 manifest。
5. 校验两个 client 文件 SHA-256 与 server 源文件一致。
6. 校验 UFTP status matrix：两个 client、两个文件均为 `copy`，且每个 client 有 `CONNECT;success`。
7. 采集 feedback window open/close/hit、queue/reassembly、原始日志。
8. 停止临时进程并确认无孤儿。

### 4.3 smoke-uplink-http-put

目的：只验证真实 TUN/无线上的 HTTP PUT over TCP uplink 基础设施。

边界：

- 使用临时进程，不使用 systemd 角色服务。
- 可使用最小 HTTP PUT receiver/client。
- 不经过 Runtime 四接口。
- 成功不能替代正式 Runtime 验收。

行为：

1. 三台机器启动临时 `wfb_v6_uplink` 链路进程。
2. server 在 `10.80.0.1:8080` 启动最小 HTTP PUT receiver。
3. client1/client2 分别通过 TUN 地址发起单连接 PUT。
4. 每个 client 记录 HTTP status；server 记录路径、长度、SHA-256。
5. 采集 READY/GRANT、authorized sends、server packets、queue/reassembly。
6. 停止临时进程并确认无孤儿。

### 4.4 run-runtime-loop

目的：通过安装后的 systemd 角色服务运行正式 Runtime 四接口闭环。

边界：

- 必须使用 `wfb-fl-server.service` 和 `wfb-fl-client.service`。
- 必须使用 issue41 drop-in 和 `/etc/wfb-ng/issue41/*` 配置。
- 数据面必须走 `10.80.0.0/24` TUN 地址。
- 只有 Runtime 四接口驱动正式闭环，不能用 ping、普通 TCP 文件探针或人工复制替代。
- server `link_args` 使用 `ISSUE41_FEEDBACK_WINDOW_PERIOD_MS`（默认 `500`）、`ISSUE41_FEEDBACK_WINDOW_DURATION_MS`（默认 `15`）和 `--feedback-window-start-immediately`。该 P0 配置从链路启动即按静态 `[1,2]` 轮发短 GRANT，持续到角色服务结束；两个参数只用于 Issue #41 脚本，可通过环境变量调参，不是产品默认值。
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

算法 fixture：

- 默认单轮 `rounds=1`。
- server 生成确定性模型文件。
- client1/client2 调用 `wait_for_model()`，校验 manifest、参与集合、大小、SHA-256 后训练。
- client update 由 `round_id`、`node_id`、`model_sha256`、`client_dataset_seed`、`fixture_version` 确定性生成。
- client1 `training_delay_ms=0`。
- client2 `training_delay_ms=3000`。
- client1 必须早于 client2 完成 HTTP PUT；server 在只收到 client1 时不得返回 partial result。
- server `wait_for_updates()` 只能在两个有效 update 全部收齐后返回完整 `[1, 2]` 映射。
- 本次不强制制造“client1 早于 server 下行完成屏障提交”的极端时序；若自然发生，可记录为额外证据。

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
tests/logs/v8_issue41_<timestamp>/
  orchestration/
  pre_runtime_smoke/
    downlink_uftp/
    uplink_http_put/
  formal_runtime_loop/
    server/
    client1/
    client2/
  lifecycle/
  raw/
  result.md
  issue41_summary.json
```

### 5.2 summary 分区

结构化 summary 至少分区：

```json
{
  "orchestration": {},
  "pre_runtime_smoke": {
    "downlink_uftp": {},
    "uplink_http_put": {}
  },
  "formal_runtime_loop": {},
  "lifecycle": {},
  "conclusion": {}
}
```

管理面日志可以进入归档，但必须只作为编排证据。任何 `192.168.122.*` 管理网传输不得计入 FL 数据面成功事实。

### 5.3 fail-closed

任何关键证据缺失都不能报告成功。失败时默认执行：

1. 停止本机和远端服务。
2. 停止 smoke 临时进程。
3. 收集失败现场证据。
4. 检查无孤儿进程。
5. 生成 failed `result.md` 和 `issue41_summary.json`。
6. 不删除 work_dir，留待排障。

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
3. `smoke-downlink-uftp` 通过，但它只作为诊断前置。
4. `smoke-uplink-http-put` 通过，但它只作为诊断前置。
5. Runtime 正式闭环通过：`publish_model`、两个 `wait_for_model`、两个 `submit_update`、`wait_for_updates` 全部经正式接口完成。
6. shared UFTP downlink 每轮一次，两个 client 的 model 和 manifest status matrix 完整。
7. 两个 client 的模型 SHA 与 server 一致；两个 update 的 SHA 与 server 收到结果一致。
8. client1 早于 client2 完成 update，server 不返回 partial result，最终返回 `[1, 2]` 完整映射。
9. v6 直接依赖证据完整：READY/GRANT、authorized sends、server 接收、queue pause/resume、bytes/packets 反压、reassembly、sender isolation、feedback window。
10. lifecycle stop/restart 和无孤儿进程验证通过。
11. `result.md` 和 `issue41_summary.json` 明确区分 baseline、smoke、formal runtime loop、lifecycle。
12. 任何失败均生成 failed 结论，而不是缺证据成功。
