# v6 新底座无 SSH 手动上行演示手册

本文用于现场只有三台独立机器、server 无法 SSH 到 client1/client2 时，手动演示 `wfb_v6_uplink --role server/client` 上行能力。

本版把原来的大段命令收敛为脚本文件，现场只需要执行短命令；关键状态用“面板 + 进度条”直接显示，不要求老师读原始日志。

---

## 0. 本手册配套脚本

仓库内新增 4 个脚本：

| 文件 | 用途 |
| --- | --- |
| `tests/real_hardware/v6_manual_uplink_demo.sh` | 总控脚本：准备、启动 WFB、接收、发送、打包、汇总 |
| `tests/real_hardware/v6_manual_uplink_tcp_send_progress.py` | client TCP sender，可视化显示上传进度 |
| `tests/real_hardware/v6_manual_uplink_tcp_recv_progress.py` | server TCP receiver，可视化显示接收进度 |
| `tests/real_hardware/v6_manual_uplink_make_source.py` | 生成确定性上传源文件并显示 SHA256 |

三台机器都需要有同一份仓库和这些脚本。不能 SSH 时，用 U 盘、现场文件共享、或其他离线方式同步仓库/脚本。

演示约束：

- 不使用 `ssh` / `scp` 自动控制 client。
- 不使用旧 `wfb_rx` / `wfb_tx` / `wfb_token_scheduler` / `-K`。
- 三台机器都运行同一个二进制：`wfb_v6_uplink`。
- server 用 `--role server`；client1/client2 用 `--role client`。
- 默认文件大小 `40MiB`；排障时三台机器都设置 `DEMO_FILE_SIZE=1048576` 做 1MiB 探针。

---

## 1. 拓扑与终端分工

默认三机拓扑：

| 角色 | 默认网卡 | TUN IP | TUN 名称 | node_id |
| --- | --- | --- | --- | --- |
| server | `wlxbcec23372588` | `10.80.0.1/24` | `v6us0` | `9` |
| client1 | `wlxfca386b38672` | `10.80.0.11/24` | `v6uc1` | `1` |
| client2 | `wlxfc221c300cbc` | `10.80.0.12/24` | `v6uc2` | `2` |

默认无线参数：

- channel：`157`
- width：`HT40+`
- link_id：`406`
- uplink stream：`32`

建议打开这些终端：

| 机器 | 终端 | 执行内容 |
| --- | --- | --- |
| server | S0 | 准备、最终解包和汇总 |
| server | S1 | `server-wfb` 面板 |
| server | S2 | `server-recv-client1` 接收进度条 |
| server | S3 | `server-recv-client2` 接收进度条 |
| client1 | C1-0 | 准备、打包日志 |
| client1 | C1-1 | `client1-wfb` 面板 |
| client1 | C1-2 | `client1-send` 上传进度条 |
| client2 | C2-0 | 准备、打包日志 |
| client2 | C2-1 | `client2-wfb` 面板 |
| client2 | C2-2 | `client2-send` 上传进度条 |

现场同步点：S2/S3 都显示“接收器已就绪”后，再启动 C1-2/C2-2。

---

## 2. 三台机器设置同一个 DEMO_NAME

server S0 生成演示名：

```bash
cd ~/code/wfb-ng-fl
bash tests/real_hardware/v6_manual_uplink_demo.sh new-name
```

输出类似：

```bash
export DEMO_NAME=v6手动上行_20260704_153000
```

把这一行手工复制到 server、client1、client2 三台机器的所有终端。

三台机器分别执行：

```bash
cd ~/code/wfb-ng-fl
export DEMO_NAME=v6手动上行_替换为现场实际值
```

如果要先跑 1MiB 探针，三台机器都额外执行：

```bash
export DEMO_FILE_SIZE=1048576
```

正式演示使用默认 40MiB，不需要设置 `DEMO_FILE_SIZE`。

---

## 3. 准备三台机器

### 3.1 server S0

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh server-prepare
```

脚本会自动做这些事：

- 构建 `wfb_v6_uplink`。
- 创建本轮日志目录。
- 清理本机残留 `wfb_v6_uplink` / 接收脚本。
- 把 server 网卡切到 `monitor/UP` 并固定到 channel `157 HT40+`。
- 在屏幕上显示准备是否通过。

通过标志：屏幕出现 `server 准备完成`。

### 3.2 client1 C1-0

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh client1-prepare
```

脚本会自动做这些事：

- 构建 `wfb_v6_uplink`。
- 创建本轮 client1 日志目录。
- 清理本机残留进程。
- 把 client1 网卡切到 `monitor/UP` 并固定到 channel `157 HT40+`。
- 生成 `client1_uplink.bin`。
- 显示源文件大小、生成进度条、源文件 SHA256。

通过标志：屏幕出现 `client1 准备完成`，并显示 `源文件 SHA`。

### 3.3 client2 C2-0

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh client2-prepare
```

通过标志同 client1。

---

## 4. 启动三台 WFB，可视化展示链路状态

### 4.1 server S1

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh server-wfb
```

屏幕会显示“server 上行链路面板”，而不是刷原始日志。示例：

```text
============================================================
server 上行链路面板：给老师看的实时状态
============================================================
演示名: v6手动上行_20260704_153000
运行时间: 00:01:12    日志: .../uplink/server/wfb_v6_uplink.log

[通过]   链路安全口径 trusted_plaintext 已出现才算正确
[通过]   server TUN v6us0 = 10.80.0.1/24
[通过]   client1 已进入调度 ready_accept=18
[通过]   client2 已进入调度 ready_accept=16
[通过]   client1 获得空口发送机会 grants=62
[通过]   client2 获得空口发送机会 grants=59
[通过]   server 收到无线数据 累计数据包=12840
[通过]   调度拒收 ready_reject=0

老师口径：两个 client 都出现“获得空口发送机会”，且 server 数据包增长，说明两台机器正在轮流通过真实无线链路上传。
```

如果屏幕出现 `[等待]`，表示该项还没发生；如果出现 `[注意]`，需要现场解释或排障。

### 4.2 client1 C1-1

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh client1-wfb
```

屏幕会显示 client1 面板：

```text
[通过]   链路安全口径 trusted_plaintext 已出现才算正确
[通过]   client1 TUN v6uc1 = 10.80.0.11/24
[通过]   收到 server 授权 grant_accept=61
[通过]   允许发送数据 authorized_sends=1200
[等待]   TCP 文件上传 发送窗口完成后会显示

老师口径：本机出现“收到 server 授权”和“允许发送数据”，说明它不是自己乱发，而是在 server 授权窗口内上传。
```

### 4.3 client2 C2-1

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh client2-wfb
```

通过标志同 client1。

> 这三个 WFB 面板终端要一直开着。停止时按 `Ctrl-C`，脚本会结束本机 WFB 并刷新 queue summary。

---

## 5. 执行双 client 上行，可视化展示文件传输

### 5.1 server S2：接收 client1

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh server-recv-client1
```

看到下面内容后保持等待：

```text
[等待] server 接收 client1 上传 | 接收器已就绪：10.80.0.1:19111，等待 client 连接
```

### 5.2 server S3：接收 client2

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh server-recv-client2
```

看到 `接收器已就绪：10.80.0.1:19112` 后，让两个 client 启动发送。

### 5.3 client1 C1-2：发送 client1 文件

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh client1-send
```

屏幕会展示：

```text
[准备] client1 上传到 server | 源文件 client1_uplink.bin，大小 41943040 bytes / 40.00 MiB
[准备] client1 上传到 server | 源文件 SHA256=...
[通过] client1 上传到 server | TCP 已连接：10.80.0.11:xxxxx -> 10.80.0.1:19111
[上传中] client1 上传到 server | [############--------------------]  38.0%  15.20/40.00 MiB  当前 2.10 MiB/s  平均 1.95 MiB/s
[通过] client1 上传到 server | 上传完成：sent_bytes=41943040，耗时 ...s，平均 ... MiB/s
```

给老师解释：进度条走到 100%，说明 client1 的 40MiB 文件已经通过 TUN TCP 送完；后面还要和 server 收到的 SHA256 对上。

### 5.4 client2 C2-2：发送 client2 文件

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh client2-send
```

通过标志同 client1。

### 5.5 server S2/S3：接收完成标志

两个 server 接收窗口都应显示：

```text
[接收中] server 接收 client1 上传 | [########################--------] ...
[通过] server 接收 client1 上传 | 接收完成：received_bytes=41943040，sha256=...，耗时 ...s，平均 ... MiB/s
```

和：

```text
[通过] server 接收 client2 上传 | 接收完成：received_bytes=41943040，sha256=...，耗时 ...s，平均 ... MiB/s
```

---

## 6. 现场先给出即时判断

在不看原始日志的情况下，可以直接用屏幕面板判断：

| 位置 | 要看到什么 | 表示什么 |
| --- | --- | --- |
| server S1 | `client1 获得空口发送机会` 为 `[通过]` | client1 被 server 调度到了 |
| server S1 | `client2 获得空口发送机会` 为 `[通过]` | client2 被 server 调度到了 |
| server S1 | `server 收到无线数据` 为 `[通过]` 且累计数据包增长 | server 真实收到空口数据 |
| client1 C1-1 | `收到 server 授权`、`允许发送数据` 为 `[通过]` | client1 在授权窗口内发送 |
| client2 C2-1 | `收到 server 授权`、`允许发送数据` 为 `[通过]` | client2 在授权窗口内发送 |
| client1 C1-2 | `上传完成：sent_bytes=41943040` | client1 TCP 文件发完 |
| client2 C2-2 | `上传完成：sent_bytes=41943040` | client2 TCP 文件发完 |
| server S2/S3 | `接收完成：received_bytes=41943040` | server 两个 TCP 文件都收完 |

即时通过口径：两个 client 都发完、server 都收完、server 面板中两个 client 都有 grant，才算“现场传输过程成功”。最终通过还要做 SHA 和 2A 摘要。

---

## 7. 停止 WFB 并离线归档 client 日志

### 7.1 停止三台 WFB

在 S1、C1-1、C2-1 三个 WFB 面板终端分别按：

```text
Ctrl-C
```

这一步必须做；queue summary 通常在 WFB 正常退出后才完整刷新。

### 7.2 client1 C1-0 打包日志

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh client1-pack
```

输出会显示压缩包路径，例如：

```text
[通过] 请人工拷贝到 server：/tmp/v6手动上行_20260704_153000_client1_uplink_logs.tgz
```

把这个文件人工拷贝到 server 的 `/tmp` 或其他目录。

### 7.3 client2 C2-0 打包日志

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh client2-pack
```

同样把压缩包人工拷贝到 server。

### 7.4 server S0 解包 client 日志

如果两个压缩包都放在 server `/tmp`，执行：

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh server-unpack
```

如果放在其他路径，显式传入：

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh server-unpack /path/client1.tgz /path/client2.tgz
```

---

## 8. 生成最终结果面板和 result.md

server S0 执行：

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh server-summary
```

脚本会：

- 比对 client1 source SHA 与 server received SHA。
- 比对 client2 source SHA 与 server received SHA。
- 提取 server 上两个 client 的 grant 数。
- 提取 client1/client2 的 authorized_sends。
- 提取 server `server_data_packets_sum`。
- 检查 `ready_reject_count`。
- 生成 `formal_2a_summary.json`。
- 生成 `$DEMO_ROOT/result.md`。
- 在屏幕上显示最终通过/未通过面板。

最终面板示例：

```text
============================================================
最终结果面板：给老师看的通过条件
============================================================
[通过]   client1 文件完整 source=... received=...
[通过]   client2 文件完整 source=... received=...
[通过]   client1 获得无线发送机会 grants=62
[通过]   client2 获得无线发送机会 grants=59
[通过]   client1 被授权发送 authorized_sends=1200
[通过]   client2 被授权发送 authorized_sends=980
[通过]   server 收到无线数据 server_data_packets_sum=12840
[通过]   无调度拒收 ready_reject=0
[通过]   2A 摘要生成 .../formal_2a_summary.json

[通过] 结论：本次无 SSH 手动双 client 上行演示通过。
```

---

## 9. 最终成功判定

必须同时满足：

1. 三台 WFB 面板都显示 `链路安全口径` 为 `[通过]`。
2. server `v6us0`、client1 `v6uc1`、client2 `v6uc2` 都显示 `[通过]`。
3. server 面板中 client1/client2 都获得空口发送机会。
4. client1/client2 面板中 `收到 server 授权`、`允许发送数据` 都为 `[通过]`。
5. client1 sender 显示 `上传完成：sent_bytes=$DEMO_FILE_SIZE`。
6. client2 sender 显示 `上传完成：sent_bytes=$DEMO_FILE_SIZE`。
7. server 两个 receiver 显示 `接收完成：received_bytes=$DEMO_FILE_SIZE`。
8. `server-summary` 中两个文件 SHA 都为 `[通过]`。
9. `server-summary` 中 `server_data_packets_sum > 0`。
10. `server-summary` 中 `ready_reject=0`。
11. `formal_2a_summary.json` 生成成功，且为上行 real-hardware / trusted_plaintext / dual-long-run 口径。

只通过单 client，不算双 client 上行演示通过。

---

## 10. 常见失败与老师可见解释

### 10.1 面板一直显示 TUN `[等待]`

说明：WFB 没有成功创建 TUN。

处理：回到对应机器执行准备脚本：

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh server-prepare
```

或对应 client：

```bash
bash tests/real_hardware/v6_manual_uplink_demo.sh client1-prepare
bash tests/real_hardware/v6_manual_uplink_demo.sh client2-prepare
```

重点看准备脚本是否显示网卡为 `monitor/UP`。

### 10.2 sender 一直显示“server 接收器尚未接通”

说明：client 还没连上 server TCP receiver。

检查顺序：

1. server S2/S3 是否已经显示“接收器已就绪”。
2. server S1 是否显示 `server TUN` 为 `[通过]`。
3. client C1-1/C2-1 是否显示 client TUN 为 `[通过]`。
4. server S1 是否显示该 client 获得空口发送机会。

### 10.3 只有一个 client 进度条动

说明：不能判定双 client 成功。

给老师解释：单车道上只有一辆车跑通，不代表两辆车都能被调度。

处理：看 server S1 面板中另一个 client 是否有 grant；没有 grant 就检查该 client 的网卡、TUN、WFB 面板和源文件发送窗口。

### 10.4 文件上传完成，但最终 SHA 不一致

说明：应用层文件证据失败，不能通过。

处理：确认三台机器使用同一个 `DEMO_NAME`，删除旧日志目录或换新 `DEMO_NAME` 重新跑。

### 10.5 2A 摘要失败

说明：最终归档证据不完整。

处理：

1. 确认 C1-1/C2-1 已按 `Ctrl-C` 正常退出 WFB。
2. 重新执行 `client1-pack` / `client2-pack`。
3. 人工拷贝到 server 后重新执行 `server-unpack`。
4. 再执行 `server-summary`。

---

## 11. 清理现场

三台机器如需强制清理残留进程：

```bash
sudo pkill -x wfb_v6_uplink || true
sudo pkill -f v6_manual_uplink_tcp_send_progress.py || true
sudo pkill -f v6_manual_uplink_tcp_recv_progress.py || true
```

如需把网卡切回 managed，按机器设置变量后执行：

server：

```bash
export SERVER_IFACE=wlxbcec23372588
sudo ip link set "$SERVER_IFACE" down || true
sudo iw dev "$SERVER_IFACE" set type managed || true
sudo ip link set "$SERVER_IFACE" up || true
```

client：

```bash
export CLIENT_IFACE=替换为本机_client_网卡名
sudo ip link set "$CLIENT_IFACE" down || true
sudo iw dev "$CLIENT_IFACE" set type managed || true
sudo ip link set "$CLIENT_IFACE" up || true
```
