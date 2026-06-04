# 真实硬件测试指南

本文档指导开发者使用 split-process (`wfb_rx` / `wfb_tx`) 架构进行真实硬件上行链路测试，包括 Token-gated 上行测试与 `v5` real-hardware 双客户端长稳主场景。命令以三网卡实测环境为例，后续开发者可按自己的网卡名替换。

## 目录

- [前置条件](#前置条件)
- [通用环境准备](#通用环境准备)
- [v1-mainline 真实硬件 monitor 主线验收](#v1-mainline-真实硬件-monitor-主线验收)
- [Token-gated split-process 测试](#token-gated-split-process-测试)
- [v5 下行补充证据场景](#v5-下行补充证据场景)
- [`v5` 正式证据包与双层归档](#v5-正式证据包与双层归档)
- [跨机器 KCP 小文件上传测试](#跨机器-kcp-小文件上传测试)
- [参考资料](#参考资料)

---

## 前置条件

### 硬件拓扑

#### 单客户端场景

至少需要 2 张支持 monitor/injection 的 Wi-Fi 网卡：

| 角色 | 实测网卡 | 说明 |
| --- | --- | --- |
| server / receiver | `wlxbcec23372588` | 运行 `wfb_rx` |
| client1 / sender | `wlxfca386b38672` | 运行 `wfb_tx -g`，监听 UDP `5602` |

#### 双客户端场景（`dual` / `dual-long-run`）

至少需要 3 张支持 monitor/injection 的 Wi-Fi 网卡：

| 角色 | 实测网卡 | 说明 |
| --- | --- | --- |
| server / receiver | `wlxbcec23372588` | 运行 `wfb_rx` |
| client1 / sender | `wlxfca386b38672` | 运行 `wfb_tx -g`，监听 UDP `5602` |
| client2 / sender | `wlx08107a083815` | 运行 `wfb_tx -g`，监听 UDP `5603` |

三张网卡可以插在同一台开发机上，也可以分布在多台机器上；如果分布运行，需要把 `TOKEN_*_CMD` 包成对应的 SSH 启动命令，并确保日志能回收到执行脚本的机器。

### 软件要求

```bash
command -v ip && echo "✓ ip 已安装" || echo "✗ ip 未安装"
command -v iw && echo "✓ iw 已安装" || echo "✗ iw 未安装"
command -v nc && echo "✓ nc 已安装" || echo "✗ nc 未安装"
command -v timeout && echo "✓ timeout 已安装" || echo "✗ timeout 未安装"
command -v sha256sum && echo "✓ sha256sum 已安装" || echo "✗ sha256sum 未安装"
command -v tcpdump && echo "✓ tcpdump 已安装" || echo "✗ tcpdump 未安装（仅影响抓包留痕）"
test -f ./gs.key && echo "✓ gs.key 已就绪" || echo "✗ gs.key 缺失"
test -f ./drone.key && echo "✓ drone.key 已就绪" || echo "✗ drone.key 缺失"
test -x ./wfb_rx && echo "✓ wfb_rx 已编译" || echo "✗ wfb_rx 缺失"
test -x ./wfb_tx && echo "✓ wfb_tx 已编译" || echo "✗ wfb_tx 缺失"
test -x ./wfb_token_scheduler && echo "✓ wfb_token_scheduler 已编译" || echo "✗ wfb_token_scheduler 缺失"
```

### 关键约定

- `wfb_tx -g` 才启用 Token gate；不加 `-g` 时保持普通 split-process 行为，用于避免普通回归测试被 Token gate 拦截。
- `wfb_token_scheduler -s <base_socket>` 会向 `<base_socket>.token` Unix datagram socket 下发 Token 授权事件。
- 双客户端用 `-s node:base_socket,node:base_socket` 映射，例如 `-s 1:5602,2:5603`。
- `TOKEN_AUTH` 字段顺序为 `accepted_events:rejected_events:authorized_sends:denied_sends`。
- 服务端 `PKT` 中的 `count_p_data` 用于确认 receiver 侧确实收到数据包。

---

## 通用环境准备

```bash
# 1. 清理残留进程
sudo pkill -9 -f "wfb_core" || true
sudo pkill -9 -f "wfb_tx" || true
sudo pkill -9 -f "wfb_rx" || true
sudo pkill -9 -f "wfb_token_scheduler" || true
sudo pkill -9 -f "uftpd" || true
sudo pkill -9 -f "uftp" || true

# 2. 清理 TUN 设备（如测试 wfb_core/TUN 路径时使用）
sudo ip link delete wfb0 2>/dev/null || true
sudo ip link delete wfb1 2>/dev/null || true

# 3. 验证网卡状态
ip link show wlxbcec23372588
ip link show wlxfca386b38672
ip link show wlx08107a083815
```

---

## v1-mainline 真实硬件 monitor 主线验收

> 适用范围：issue 04。该入口用于把 `tests/acceptance/v1_mainline.sh` 已经在 `wlan emulation` 下跑通的单客户端不对称 Token 主线，迁移到真实 `monitor/injection` 无线链路上做补充验收。

统一编排脚本：

- `tests/real_hardware/v1_mainline_monitor.sh`

### 验收目标

该脚本保持与 `tests/acceptance/v1_mainline.sh` 一致的主线语义：

- 上层边界仍为 `TUN/IP`
- 进程形态仍为 split-process：`wfb_tun`、`wfb_tx`、`wfb_rx`
- `Server -> Client` 发送路径常开，不启用 Token gate
- `Client -> Server` 发送路径启用 Token gate，由 `wfb_token_scheduler` 向 client 的 `<base_socket>.token` socket 下发授权
- 验收项仍为双向 ping、上行 TCP 小文件传输、下行 TCP 小文件传输

与 acceptance 主线唯一不同的是底层链路介质：这里要求至少两张支持 `monitor/injection` 的真实 Wi-Fi 网卡，而不是本机 `wlan emulation`。

### 前置要求

1. 所有参与测试的无线网卡都必须提前手工切到 monitor 模式，并配置到同一信道。
2. 脚本只做检查和留痕，不会自动修改网卡模式、信道或其他无线环境。
3. 若角色分布在多台机器上，命令需要自行封装为 `ssh ...`，并确保日志、抓包文件或等价证据能够回收到执行脚本的机器。
4. `wfb_token_scheduler` 必须与 `client_tx` 运行在同一台机器上，因为 Token 通过本机 Unix domain socket `<base_socket>.token` 下发。

### 必填环境变量

运行 `v1_mainline_monitor.sh` 时，至少需要提供以下 7 个角色启动命令：

- `V1_SERVER_TUN_CMD`
- `V1_CLIENT_TUN_CMD`
- `V1_SERVER_TX_CMD`
- `V1_CLIENT_TX_CMD`
- `V1_SERVER_RX_CMD`
- `V1_CLIENT_RX_CMD`
- `V1_SCHEDULER_CMD`

这些命令既可以是本机直接启动命令，也可以是 `ssh <host> '<cmd>'` 形式的远程启动命令。

### 常用可选环境变量

- `SERVER_WIFI_IFACE`：服务端 monitor 网卡名，用于本地模式/信道留痕与默认抓包
- `CLIENT_WIFI_IFACE`：客户端 monitor 网卡名，用于本地模式/信道留痕
- `PCAP_CAPTURE_CMD`：自定义抓包命令；未设置时，如果 `SERVER_WIFI_IFACE` 在本机且安装了 `tcpdump`，脚本会默认对服务端网卡抓包
- `V1_SERVER_TO_CLIENT_PING_CMD` / `V1_CLIENT_TO_SERVER_PING_CMD`：需要自定义 ping 执行方式时覆盖
- `V1_TCP_UPLINK_LISTEN_CMD` / `V1_TCP_UPLINK_SEND_CMD`：需要自定义上行 TCP 小文件传输方式时覆盖
- `V1_TCP_DOWNLINK_LISTEN_CMD` / `V1_TCP_DOWNLINK_SEND_CMD`：需要自定义下行 TCP 小文件传输方式时覆盖

可通过以下命令查看完整帮助：

```bash
bash tests/real_hardware/v1_mainline_monitor.sh --help
```

### 单机双网卡示例

以下示例假设 server 与 client 角色都运行在同一台机器上，但使用两张不同的 monitor 网卡：

```bash
sudo env \
  SERVER_WIFI_IFACE='wlxbcec23372588' \
  CLIENT_WIFI_IFACE='wlxfca386b38672' \
  V1_SERVER_TUN_CMD='./wfb_tun -t wfb-v1s -a 10.23.0.1/24 -l 5800 -u 5700' \
  V1_CLIENT_TUN_CMD='./wfb_tun -t wfb-v1c -a 10.23.0.2/24 -l 5801 -u 5701' \
  V1_SERVER_TX_CMD='./wfb_tx -K ./gs.key -u 5700 -D 41002 -i 101 -e 123456 -R 524288 -s 524288 wlxbcec23372588' \
  V1_CLIENT_TX_CMD='./wfb_tx -g -K ./drone.key -u 5701 -D 41001 -i 101 -e 123456 -R 524288 -s 524288 wlxfca386b38672' \
  V1_SERVER_RX_CMD='./wfb_rx -a 41001 -K ./gs.key -c 127.0.0.1 -u 5800 -N 0 -i 101 -e 123456 -R 524288 -s 524288 wlxbcec23372588' \
  V1_CLIENT_RX_CMD='./wfb_rx -a 41002 -K ./drone.key -c 127.0.0.1 -u 5801 -N 1 -i 101 -e 123456 -R 524288 -s 524288 wlxfca386b38672' \
  V1_SCHEDULER_CMD='./wfb_token_scheduler -n 1 -d 1000 -g 100 -s 5701' \
  bash tests/real_hardware/v1_mainline_monitor.sh
```

说明：

- `server_tx` 未加 `-g`，保持下行常开。
- `client_tx` 加 `-g`，保持上行 Token 门控。
- `V1_SCHEDULER_CMD` 的 `-s 5701` 会把 grant 下发到 `5701.token`，必须与 `client_tx -u 5701` 对齐。

### 跨机示例

以下示例展示如何把角色命令包装为 SSH 启动命令。假设：

- 本机负责执行验收脚本并汇总日志
- `server-host` 挂服务端网卡并运行 `server_tun`、`server_tx`、`server_rx`
- `client-host` 挂客户端网卡并运行 `client_tun`、`client_tx`、`client_rx`、`scheduler`

```bash
sudo env \
  SERVER_WIFI_IFACE='' \
  CLIENT_WIFI_IFACE='' \
  V1_SERVER_TUN_CMD='ssh server-host "cd /path/to/wfb-ng && ./wfb_tun -t wfb-v1s -a 10.23.0.1/24 -l 5800 -u 5700"' \
  V1_CLIENT_TUN_CMD='ssh client-host "cd /path/to/wfb-ng && ./wfb_tun -t wfb-v1c -a 10.23.0.2/24 -l 5801 -u 5701"' \
  V1_SERVER_TX_CMD='ssh server-host "cd /path/to/wfb-ng && ./wfb_tx -K ./gs.key -u 5700 -D 41002 -i 101 -e 123456 -R 524288 -s 524288 <SERVER_NIC>"' \
  V1_CLIENT_TX_CMD='ssh client-host "cd /path/to/wfb-ng && ./wfb_tx -g -K ./drone.key -u 5701 -D 41001 -i 101 -e 123456 -R 524288 -s 524288 <CLIENT_NIC>"' \
  V1_SERVER_RX_CMD='ssh server-host "cd /path/to/wfb-ng && ./wfb_rx -a 41001 -K ./gs.key -c 127.0.0.1 -u 5800 -N 0 -i 101 -e 123456 -R 524288 -s 524288 <SERVER_NIC>"' \
  V1_CLIENT_RX_CMD='ssh client-host "cd /path/to/wfb-ng && ./wfb_rx -a 41002 -K ./drone.key -c 127.0.0.1 -u 5801 -N 1 -i 101 -e 123456 -R 524288 -s 524288 <CLIENT_NIC>"' \
  V1_SCHEDULER_CMD='ssh client-host "cd /path/to/wfb-ng && ./wfb_token_scheduler -n 1 -d 1000 -g 100 -s 5701"' \
  PCAP_CAPTURE_CMD='ssh server-host "tcpdump -i <SERVER_NIC> -w /tmp/v1-mainline-monitor.pcap"' \
  bash tests/real_hardware/v1_mainline_monitor.sh
```

说明：

- 跨机场景下，本机通常无法直接读取远端网卡模式/信道，因此 `SERVER_WIFI_IFACE` / `CLIENT_WIFI_IFACE` 可以留空，由远端命令与抓包/截图等方式提供等价证据。
- 若要把远端抓包文件回收到本机，可在测试结束后额外执行 `scp`，或把 `PCAP_CAPTURE_CMD` 改成包含远端路径回传逻辑的封装脚本。

### 结果与留痕

脚本执行后，日志目录下至少应包含：

- `environment.txt`：环境摘要、网卡名、模式、信道、启动命令
- `result.md`：验收结果汇总、Token 统计、文件 SHA256、日志清单
- `server_tun.log`、`client_tun.log`
- `server_tx.log`、`client_tx.log`
- `server_rx.log`、`client_rx.log`
- `scheduler.log`
- `ping_server_to_client.log`、`ping_client_to_server.log`
- `tcp_uplink_*`、`tcp_downlink_*` 文件与日志
- `pcap_capture.log`、`capture.pcap`（若启用抓包）

建议按以下顺序核验：

1. `result.md` 为 PASS。
2. `scheduler.log` 中存在 `grant seq=`。
3. `client_tx.log` 中存在 `TOKEN_AUTH`，且上行传输后 `authorized_sends` 增长。
4. `server_tx.log` 中不应出现 `TOKEN_AUTH`，以证明下行发送路径保持常开。
5. 上下行接收文件 SHA256 与源文件一致。
6. `environment.txt` 中记录的接口模式为 `monitor`，或记录了无法本地检查的原因。
7. 若存在 `capture.pcap`，抓包接口应为真实 monitor 网卡，并能作为无线链路证据。

---

## Token-gated split-process 测试

> 适用范围：split-process 真实硬件上行验收，以及 `v5` real-hardware 双客户端 Token-gated 上行长稳主场景。主线是 `wfb_tx / wfb_rx` 拆分架构，不是 `wfb_core` 单进程实验路径。

统一编排脚本：

- `tests/real_hardware/test_token_gated_uplink.sh`
- `tests/real_hardware/one_click_test.sh --token`（仅在你本地另行提供 `tests/config/test_config.sh` 时可用；当前仓库默认不自带该配置文件）

### 无线网卡配置

启动测试前，需要先将网卡切换为 monitor 模式并设置信道：

```bash
# 以 wlxbcec23372588 为例，替换为你的网卡名
sudo ip link set wlxbcec23372588 down
sudo iw dev wlxbcec23372588 set type monitor
sudo ip link set wlxbcec23372588 up
sudo iw dev wlxbcec23372588 set channel 157 HT40+
```

> 每张参与测试的网卡都需要执行以上配置。

### 测试场景

#### no-token 静默测试

**目的**：验证 Token gate 的默认拒绝行为。无 scheduler 授权时，Token gate 必须在静默状态，拒绝所有上行数据。

**必要性**：这是 Token gate 的基础安全语义 — 不授权则不放行。如果此项失败，后续所有授权测试都失去了对照基线。

无 scheduler 时，client 应拒绝上行，`client1.log` 中应出现 `denied_sends > 0`。

```bash
sudo env \
  TOKEN_SERVER_START_CMD='./wfb_rx -p 0 -u 5600 -K gs.key -R 16777216 -l 1000 -i 0 wlxbcec23372588' \
  TOKEN_CLIENT1_START_CMD='./wfb_tx -g -p 0 -u 5602 -K drone.key -B 40 -k 12 -n 12 -i 0 wlxfca386b38672' \
  TOKEN_PROBE_CMD='echo "TOKEN_TEST_PACKET" | nc -w 1 -u 127.0.0.1 5602' \
  bash tests/real_hardware/test_token_gated_uplink.sh --scenario no-token
```

#### single 授权窗口测试

**目的**：验证单客户端在 Token 授权窗口内能正常上行。scheduler 下发 grant 后，探测命令应成功，client 的 `authorized_sends` 应增长。

**必要性**：这是 Token gate 正常工作路径的最小验证 — 授权-发送闭环。如果此项失败，说明 Token 授权链路本身不通。

scheduler 向 client1 的 `5602.token` 下发授权，client1 应在窗口内上行成功。当前脚本的自动判定以 probe 成功和 `scheduler.log` 中存在 `grant` 留痕为准；如果需要补看 receiver 侧推进，再人工检查 `server.log` 中的 `PKT count_p_data`。

```bash
sudo env \
  TOKEN_SERVER_START_CMD='./wfb_rx -p 0 -u 5600 -K gs.key -R 16777216 -l 1000 -i 0 wlxbcec23372588' \
  TOKEN_CLIENT1_START_CMD='./wfb_tx -g -p 0 -u 5602 -K drone.key -B 40 -k 12 -n 12 -i 0 wlxfca386b38672' \
  TOKEN_SCHEDULER_CMD='./wfb_token_scheduler -n 1 -d 40 -g 5 -s 5602' \
  TOKEN_PROBE_CMD='echo "TOKEN_TEST_PACKET" | nc -w 1 -u 127.0.0.1 5602' \
  bash tests/real_hardware/test_token_gated_uplink.sh --scenario single
```

#### expiry 过期停发测试

**目的**：验证 Token 授权窗口过期后，gate 重新回到拒绝状态。scheduler 停止后，已下发的 Token 在窗口结束后失效，后续探测应被拒绝。

**必要性**：Token 的时效性是整个授权模型的核心。如果 Token 永不过期，等于失去了访问控制。必须在 `single` 通过后验证过期逻辑。

scheduler 停止后等待 Token 过期，再探测应被 gate 拒绝；当前脚本会先确认过期前已出现 `authorized_sends > 0`，再验证过期后 `denied_sends > 0`。

```bash
sudo env \
  TOKEN_SERVER_START_CMD='./wfb_rx -p 0 -u 5600 -K gs.key -R 16777216 -l 1000 -i 0 wlxbcec23372588' \
  TOKEN_CLIENT1_START_CMD='./wfb_tx -g -p 0 -u 5602 -K drone.key -B 40 -k 12 -n 12 -i 0 wlxfca386b38672' \
  TOKEN_SCHEDULER_CMD='./wfb_token_scheduler -n 1 -d 40 -g 5 -s 5602' \
  TOKEN_PROBE_CMD='echo "TOKEN_TEST_PACKET" | nc -w 1 -u 127.0.0.1 5602' \
  TOKEN_POST_EXPIRY_PROBE_CMD='echo "TOKEN_TEST_PACKET_AFTER_EXPIRY" | nc -w 1 -u 127.0.0.1 5602' \
  bash tests/real_hardware/test_token_gated_uplink.sh --scenario expiry
```

#### dual 双客户端映射测试

**目的**：验证多客户端场景下，scheduler 按 `node_id` 正确分发 Token 到不同客户端的独立 socket。两个 client 各自在授权窗口内上行成功，彼此不干扰。

**必要性**：`no-token`/`single`/`expiry` 只覆盖单客户端场景。多客户端是实际部署的常态，必须验证 `-s node:base_socket` 映射机制是否正确。

scheduler 使用 `-s 1:5602,2:5603` 按 `node_id` 分发 Token：grant `node_id=1` 时发到 `5602.token`，grant `node_id=2` 时发到 `5603.token`。脚本会分别探测两个 client，并自动判定 PASS/FAIL。

```bash
sudo env \
  TOKEN_SERVER_START_CMD='./wfb_rx -p 0 -u 5600 -K gs.key -R 16777216 -l 1000 -i 0 wlxbcec23372588' \
  TOKEN_CLIENT1_START_CMD='./wfb_tx -g -p 0 -u 5602 -K drone.key -B 40 -k 12 -n 12 -i 0 wlxfca386b38672' \
  TOKEN_CLIENT2_START_CMD='./wfb_tx -g -p 0 -u 5603 -K drone.key -B 40 -k 12 -n 12 -i 0 wlx08107a083815' \
  TOKEN_SCHEDULER_CMD='./wfb_token_scheduler -n 1,2 -d 1000 -g 100 -s 1:5602,2:5603' \
  TOKEN_CLIENT1_PROBE_CMD='echo "TOKEN_TEST_PACKET_CLIENT1" | nc -w 1 -u 127.0.0.1 5602' \
  TOKEN_CLIENT2_PROBE_CMD='echo "TOKEN_TEST_PACKET_CLIENT2" | nc -w 1 -u 127.0.0.1 5603' \
  bash tests/real_hardware/test_token_gated_uplink.sh --scenario dual
```

#### dual-long-run 双客户端长稳主场景

**目的**：在真实无线链路上固定 `v5` 的双客户端 Token-gated 上行 long-run 主场景，用于观察多客户端轮换、持续推进、控制面长期可用性，以及 `remove/rejoin/evict` 抖动风险。

**必要性**：`dual` 只证明双客户端 socket 映射正确，不证明“持续推进”能稳定维持。`v5` 迁移基线需要一条真实硬件主场景，在 `v6` 替换前后可反复对照。

**固定口径**（时间为主、事件数为辅）：

- 目标时长：`180` 秒
- 探测周期：`1` 秒
- 采样周期：`15` 秒
- 最小总 `grant` 数：`60`
- 每个 client 最小 `authorized_sends`：`10`
- 最大连续无推进窗口：`45` 秒
- `remove` / `evict` / 额外 `rejoin`：默认门槛均为 `0`

脚本会生成：

- `token_results.md`：自动结论与长稳摘要
- `token_context.txt`：机器可读的关键计数与固定口径
- `dual_long_run_samples.tsv`：按采样周期落盘的推进留痕

自动判定关注：

- `grant` 与 `guard` 持续出现
- 两个 client 都实际增长 `authorized_sends`
- `server.log` 中 `PKT count_p_data` 持续增长
- 没有超门槛的 `remove` / `evict` / 额外 `rejoin`
- 没有超过 `45` 秒的连续无推进窗口

人工判读仍需重点看：

- 是否出现虽然未触发硬失败、但明显恶化的时延抬升或恢复变慢
- `dual_long_run_samples.tsv` 中是否存在接近门槛的长时间平台期
- 调度器日志中的 `join/rejoin`、`remove`、`evict` 是否能被场景变化解释

```bash
sudo env \
  TOKEN_SERVER_START_CMD='./wfb_rx -p 0 -u 5600 -K gs.key -R 16777216 -l 1000 -i 0 wlxbcec23372588' \
  TOKEN_CLIENT1_START_CMD='./wfb_tx -g -p 0 -u 5602 -K drone.key -B 40 -k 12 -n 12 -i 0 wlxfca386b38672' \
  TOKEN_CLIENT2_START_CMD='./wfb_tx -g -p 0 -u 5603 -K drone.key -B 40 -k 12 -n 12 -i 0 wlx08107a083815' \
  TOKEN_SCHEDULER_CMD='./wfb_token_scheduler -n 1,2 -d 1000 -g 100 -s 1:5602,2:5603' \
  TOKEN_CLIENT1_PROBE_CMD='echo "TOKEN_LONG_RUN_CLIENT1" | nc -w 1 -u 127.0.0.1 5602' \
  TOKEN_CLIENT2_PROBE_CMD='echo "TOKEN_LONG_RUN_CLIENT2" | nc -w 1 -u 127.0.0.1 5603' \
  bash tests/real_hardware/test_token_gated_uplink.sh --scenario dual-long-run
```

如果需要按现场环境收紧或放宽门槛，可显式覆盖：

- `TOKEN_LONGRUN_DURATION_SEC`
- `TOKEN_LONGRUN_PROBE_INTERVAL_SEC`
- `TOKEN_LONGRUN_SAMPLE_INTERVAL_SEC`
- `TOKEN_LONGRUN_MIN_GRANTS`
- `TOKEN_LONGRUN_MIN_AUTHORIZED_SENDS`
- `TOKEN_LONGRUN_MAX_IDLE_SEC`
- `TOKEN_LONGRUN_MAX_REMOVE_COUNT`
- `TOKEN_LONGRUN_MAX_EVICT_COUNT`
- `TOKEN_LONGRUN_MAX_REJOIN_COUNT`

#### KCP 小文件上传测试

**目的**：验证 Token 授权通过后，端到端数据通道（KCP 层）能完整闭环 — 发送端注入文件，接收端落盘并校验 SHA256 一致。

**必要性**：前述 Token 控制场景只验证门控语义本身，不涉及实际数据载荷。KCP 小文件上传是端到端数据通道的集成验证，确保 Token 授权链路与数据传输链路协同正常。如果跳过此项，无法确认数据是否真正可达。

先构建当前目录内的最小 KCP 工具：

```bash
make kcp_tools
```

`kcp-small` 场景在 Token grant 通过后，用 KCP sender 向 client1 的 `wfb_tx -u 5602` 注入小文件，服务端 KCP receiver 从 `wfb_rx -u 5600` 接收并落盘，然后脚本校验 SHA256。

```bash
sudo env \
  TOKEN_SERVER_START_CMD='./wfb_rx -p 0 -u 5600 -K gs.key -R 16777216 -l 1000 -i 0 wlxbcec23372588' \
  TOKEN_CLIENT1_START_CMD='./wfb_tx -g -p 0 -u 5602 -K drone.key -B 40 -k 12 -n 12 -i 0 wlxfca386b38672' \
  TOKEN_SCHEDULER_CMD='./wfb_token_scheduler -n 1 -d 40 -g 5 -s 5602' \
  KCP_RECEIVER_CMD='./kcp_small_receiver --port 5600 --output "$KCP_RECEIVED_FILE" --timeout-ms 30000' \
  KCP_SENDER_CMD='./kcp_small_sender --host 127.0.0.1 --port 5602 --input "$KCP_SOURCE_FILE" --timeout-ms 30000' \
  bash tests/real_hardware/test_token_gated_uplink.sh --scenario kcp-small
```

成功条件：

- `scheduler.log` 有 `grant seq=`。
- `client1.log` 中 `TOKEN_AUTH authorized_sends` 增长。
- `server.log` 中 `PKT count_p_data` 增长。
- `kcp_receiver.log` 显示接收完成。
- `token_results.md` 中源文件和接收文件 SHA256 一致。

### 一键入口

如果你本地另行准备了 `tests/config/test_config.sh`，也可以通过 `one_click_test.sh` 快速执行：

```bash
sudo bash tests/real_hardware/one_click_test.sh --token --token-scenario single
sudo bash tests/real_hardware/one_click_test.sh --token --token-scenario expiry
sudo bash tests/real_hardware/one_click_test.sh --token --token-scenario dual
sudo bash tests/real_hardware/one_click_test.sh --token --token-scenario dual-long-run
```

如果当前 checkout 没有这份本地配置文件，请直接使用前文的 `sudo env ... bash tests/real_hardware/test_token_gated_uplink.sh --scenario ...` 形式。

---

## v5 下行补充证据场景

> 适用范围：issue #12。该入口用于 `v5` real-hardware 线的**下行补充证据**，不是双客户端 Token-gated 上行 long-run 主场景的替代品。

统一脚本：

- `tests/real_hardware/test_full_transfer.sh`

### 与上行 long-run 主场景的分工

- **双客户端 Token-gated 上行 long-run**：负责 `v5` real-hardware 主场景，关注长期推进、轮换稳定性、控制面持续可用性与停滞风险
- **下行补充证据场景**：负责大文件下行完整性、共享下行/分发相关证据，以及下行负载下的可见堵塞现象采样
- 下行场景的自动产物用于形成正式证据包；最终是否构成正式 `v5` 迁移证据，仍需人工按固定模板判读

### 场景形态

脚本支持两种固定口径：

1. `single`：单接收端大文件下行完整性补充证据
2. `shared`：在 `single` 基础上增加第二个接收端，补齐共享下行/分发相关证据

默认运行 `single`；显式传 `--scenario shared` 时启用第二个接收端。

### 自动产物

脚本执行完成后，会在 `LOG_DIR` 下生成至少以下产物：

- `downlink_results.md`：本次下行补充证据自动结论
- `downlink_context.txt`：机器可读的固定口径与关键结果
- `downlink_samples.tsv`：按固定周期采样的推进留痕
- `metrics.json`：自动指标汇总
- `summary.txt`：自动摘要报告

其中 `downlink_samples.tsv` 用于替代内部队列观测，关注：

- 传输耗时抬升
- 重传增加
- 连续无推进窗口（停滞）
- 长时间运行后恢复速度变差

### 运行方式

#### 1. 直接执行补充证据场景

```bash
sudo bash tests/real_hardware/test_full_transfer.sh --scenario single
sudo bash tests/real_hardware/test_full_transfer.sh --scenario shared
```

#### 2. 对既有日志目录补采摘要

如果本次只需要对既有 `LOG_DIR` 中的日志重新生成指标和摘要，可使用：

```bash
LOG_DIR=tests/logs/<已有目录> \
  bash tests/real_hardware/test_full_transfer.sh --analyze-only
```

### 常用环境变量钩子

- `DOWNLINK_PAYLOAD_SIZE`：自动生成 payload 大小，默认 `41943040` 字节
- `DOWNLINK_TRANSFER_TIMEOUT_SEC`：传输完成总超时
- `DOWNLINK_SAMPLE_INTERVAL_SEC`：采样周期
- `DOWNLINK_MAX_IDLE_SEC`：允许的最大连续无推进窗口
- `DOWNLINK_UFTP_SEND_CMD`：自定义发送命令；未设置时脚本尝试使用 `uftp`
- `DOWNLINK_CLIENT1_RECEIVE_CMD` / `DOWNLINK_CLIENT2_RECEIVE_CMD`：自定义接收命令；未设置时脚本尝试使用 `uftpd`
- `DOWNLINK_SERVER_TUN_CMD`、`DOWNLINK_CLIENT1_TUN_CMD`、`DOWNLINK_CLIENT2_TUN_CMD`：需要由脚本一并拉起 split-process/TUN 路径时使用
- `DOWNLINK_SERVER_TX_CMD` / `DOWNLINK_SERVER_RX_CMD` / `DOWNLINK_CLIENT{1,2}_TX_CMD` / `DOWNLINK_CLIENT{1,2}_RX_CMD`：需要脚本一并拉起底层 `wfb_tx` / `wfb_rx` 时使用
- `PCAP_CAPTURE_CMD`：自定义抓包命令；若未设置且本机存在 `tcpdump`，脚本会尝试对服务端网卡抓包

### 自动判定关注点

- 源 payload 与接收 payload 的 SHA256 一致
- `shared` 场景下两个接收端都收到同一份 payload
- `downlink_samples.tsv` 中没有超过门槛的连续无推进窗口
- 自动摘要已落盘到 `metrics.json` 与 `summary.txt`

### 人工判读关注点

- 即使自动结论为 PASS，也要检查样本文件是否出现接近门槛的平台期
- 结合 `uftp_server.log`、接收端日志与底层 `server/client` 日志，解释时延抬升、重传增加、恢复变慢
- 若启用了 `capture.pcap`，将抓包与摘要、人工判读结论一起归档到 issue / tracking issue

## `v5` 正式证据包与双层归档

> 适用范围：issue #13，以及后续 `v5` tracking issue 收口。该章节固定 `v5` real-hardware 线的正式证据包结构、人工判读模板与归档规则。

### 什么时候需要形成正式证据包

- 完成双客户端 Token-gated 上行 `dual-long-run` 主场景后
- 完成下行补充证据场景（`single` 或 `shared`）后
- 准备把本轮 real-hardware 结果回填到对应 GitHub issue / tracking issue 时

### 双层归档结构

#### 1. 原始产物层

原始产物层保留在约定日志目录，不进入仓库版本历史。至少需要保留：

- **上行主场景**：`token_results.md`、`token_context.txt`、`dual_long_run_samples.tsv`、`scheduler.log`、`server.log`、`client1.log`、`client2.log`
- **下行补充场景**：`downlink_results.md`、`downlink_context.txt`、`downlink_samples.tsv`、`metrics.json`、`summary.txt`、`uftp_server.log`、接收端日志、关键底层日志
- **可选但推荐**：`capture.pcap`、`pcap_capture.log`、现场截图、网卡模式/信道留痕、执行命令快照

要求：

- 原始产物层必须能回溯到本次运行的固定口径与自动结论
- 可以放在本地日志目录、共享存储或等价附件位置，但 issue 中必须给出可定位引用
- 不允许只在聊天或口头说明中留痕

#### 2. 正式结论层

正式结论层回填到对应 GitHub issue / tracking issue，用于 `v6` 前后对照。最小回填单元不是“日志目录已存在”，而是一份**人工判读后的正式结论**。

正式结论层必须至少包含以下字段：

- 运行场景与前置条件
- 原始产物层引用（日志目录、关键附件、自动摘要文件）
- 运行时长与关键事件计数
- 关键异常与风险信号
- 是否可作为正式 `v5` 基线证据
- 是否需要重跑

### 固定人工判读模板

回填 issue / tracking issue 时，使用以下 Markdown 模板：

```md
## v5 real-hardware 正式证据结论

- 运行批次：<日期 / 执行人 / 机器标识>
- 对应 issue：<#11 主场景 / #12 补充场景 / tracking issue>
- 结论状态：PASS / FAIL / 需重跑

### 运行场景与前置条件
- 上行主场景：dual-long-run / 未运行
- 下行补充场景：single / shared / 未运行
- 无线拓扑与网卡：<server/client 网卡、是否跨机、信道、monitor 模式留痕>
- 固定口径：<时长、采样周期、关键门槛、payload 大小、receiver_count>
- 关键启动命令或配置偏差：<若与 TEST_GUIDE 默认口径不同，必须写明>

### 原始产物层引用
- 上行日志目录：<路径或附件链接>
- 下行日志目录：<路径或附件链接>
- 自动摘要文件：<token_results.md / summary.txt / metrics.json / 其他>
- 关键附件：<capture.pcap / 截图 / 外部存储链接 / 无>

### 运行时长与关键事件计数
- dual-long-run：<实际时长、总 grant、client1/2 authorized_sends、最大无推进窗口、remove/evict/额外 rejoin>
- downlink：<传输耗时、retries、stall_events、最大无推进窗口、receiver_count、shared 分发结论>
- 文件完整性：<SHA256 是否一致>

### 关键异常与风险信号
- 时延抬升：<无 / 有，现象与上下文>
- 重传增加：<无 / 有，现象与上下文>
- 丢包或传输停滞：<无 / 有，现象与上下文>
- 控制面抖动：<无 / 有，join/rejoin/remove/evict 解释>
- 恢复能力变化：<无 / 有，现象与上下文>
- 无法解释的异常：<无 / 有，若有必须阻断基线结论>

### 人工判读结论
- 是否可作为正式 `v5` 基线证据：是 / 否
- 是否需要重跑：否 / 是（触发原因与重跑建议）
- 对 `v6` 的风险提示：<若无则写“无新增风险”>
```

### tracking issue 关闭前置条件

只有同时满足以下条件，`v5` tracking issue 才能关闭：

- `dual-long-run` 主场景和下行补充场景都已有可定位的原始产物层引用
- 对应 issue / tracking issue 已按上面的固定模板回填正式结论层
- 正式结论明确写出“可作为正式 `v5` 基线证据”或“需要重跑”
- 若存在抓包、外部日志目录或共享存储链接，issue 中已附上可追溯引用

反例：

- 只有本地日志目录，没有 issue 回填
- 只有自动摘要，没有人工判读结论
- 只有“看起来没问题”的口头描述，没有固定字段和证据引用

---

## 跨机器 KCP 小文件上传测试

> 适用场景：server 与 client 分布在不同机器上，需要跨机器完成 `kcp-small` 的单客户端文件上传闭环。

> 当前仓库里的 `kcp-small` 场景只覆盖 `client1 -> server` 单客户端上传，不提供“三台机器、两个 client 同时各传一个不同文件，再自动分别校验”的统一入口。若要验证双客户端数据上传，请分别运行两次单客户端上传，或把多客户端持续推进验证交给 `dual-long-run` 主场景。

### 确定机器角色和网卡

在开始测试前，需要先确定每台机器的角色及其网卡名称：

```bash
# 在每台机器上执行，查看本机的无线网卡
iw dev | grep Interface
```

最小角色划分如下：

- **1 台服务端机器** → 运行 `wfb_rx`、`kcp_small_receiver`
- **1 台客户端机器** → 运行 `wfb_tx`、`wfb_token_scheduler`、`kcp_small_sender`

记录两台机器的 IP 和网卡名，后续步骤中用 `<SERVER_NIC>`、`<CLIENT_NIC>` 占位符表示，请替换为实际网卡名。

### 部署布局示例

以下以跨两台机器的最小闭环为例，请根据实际情况替换：

| 机器 | 角色 | 网卡示例 | 运行的进程 |
| --- | --- | --- | --- |
| 机器 A（服务端） | server | `wlx08107a083815` | `wfb_rx`、`kcp_small_receiver` |
| 机器 B（客户端） | client1 | `wlxfca386b38672` | `wfb_tx -u 5602`、`wfb_token_scheduler`、`kcp_small_sender` |

> Token 调度器 (`wfb_token_scheduler`) 通过 Unix domain socket (`<port>.token`) 向同机的 `wfb_tx` 下发 Token，因此调度器必须和 `wfb_tx` 运行在同一台客户端机器上。

### 前置条件

两台机器都需要：

- 已编译的 `wfb_rx`、`wfb_tx`、`wfb_token_scheduler`、`kcp_small_receiver`、`kcp_small_sender`
- `gs.key`（server 端）和 `drone.key`（client 端）
- 网卡已切换为 monitor 模式并设置信道 157 HT40+（参见[无线网卡配置](#无线网卡配置)）
- 两台机器之间网络互通（用于分发文件和回收日志）

### 步骤 1 — 环境准备

两台机器都执行：

```bash
# 清理残留
sudo pkill -9 -f "wfb_tx" || true
sudo pkill -9 -f "wfb_rx" || true
sudo pkill -9 -f "wfb_token_scheduler" || true

# 验证网卡 monitor 模式（替换为实际网卡名）
iw dev <SERVER_NIC> info  # 服务端机器
iw dev <CLIENT_NIC> info  # 客户端机器
```

### 步骤 2 — 服务端机器：启动接收端

> **注意启动顺序**：`kcp_small_receiver` 必须先于 `wfb_rx` 启动。两者都需要绑定 UDP `5600`，但只有 receiver 需要 listen；`wfb_rx` 只是向该端口输出数据。若顺序颠倒，receiver 会因端口被占用而无法启动。

```bash
# 在服务端机器上执行，cd 到项目根目录
LOG_DIR="tests/logs/cross_machine_server"
mkdir -p "$LOG_DIR"

# 1. 先启动 kcp_small_receiver（监听本地 5600，等待 KCP 数据）
./kcp_small_receiver --port 5600 --output "$LOG_DIR/received.bin" --timeout-ms 120000 \
    > "$LOG_DIR/kcp_receiver.log" 2>&1 &
KCP_RX_PID=$!
sleep 1

# 2. 再启动 wfb_rx（替换 <SERVER_NIC> 为服务端网卡名）
sudo ./wfb_rx -p 0 -u 5600 -K gs.key -R 16777216 -l 1000 -i 0 <SERVER_NIC> \
    > "$LOG_DIR/server.log" 2>&1 &
WFB_RX_PID=$!

echo "服务端就绪: kcp_rx=$KCP_RX_PID wfb_rx=$WFB_RX_PID"
echo "日志目录: $LOG_DIR"
```

### 步骤 3 — 客户端机器：启动发送端并上传小文件

```bash
# 在客户端机器上执行，cd 到项目根目录
LOG_DIR="tests/logs/cross_machine_client1"
mkdir -p "$LOG_DIR"

# 1. 启动 wfb_tx（替换 <CLIENT_NIC> 为客户端网卡名）
sudo ./wfb_tx -g -p 0 -u 5602 -K drone.key -B 40 -k 12 -n 12 -i 0 <CLIENT_NIC> \
    > "$LOG_DIR/client1.log" 2>&1 &
WFB_TX_PID=$!
sleep 1

# 2. 启动本地 Token 调度器（向同机的 5602.token Unix socket 下发 Token）
./wfb_token_scheduler -n 1 -d 1000 -g 100 -s 5602 \
    > "$LOG_DIR/scheduler.log" 2>&1 &
SCHED_PID=$!
sleep 2

# 3. 生成测试文件并执行 KCP 上传
dd if=/dev/urandom of="$LOG_DIR/source.bin" bs=65536 count=1 status=none
sha256sum "$LOG_DIR/source.bin" | tee "$LOG_DIR/source.sha256"

./kcp_small_sender --host 127.0.0.1 --port 5602 --input "$LOG_DIR/source.bin" --timeout-ms 60000 \
    > "$LOG_DIR/kcp_sender.log" 2>&1
SENDER_RC=$?

echo "KCP sender 退出码: $SENDER_RC"
echo "日志目录: $LOG_DIR"
```

### 步骤 4 — 验证结果

在服务端机器上等待 `kcp_small_receiver` 退出后：

```bash
# 查看接收结果
grep 'PASS\|FAIL' tests/logs/cross_machine_server/kcp_receiver.log

# 校验接收文件 SHA256
sha256sum tests/logs/cross_machine_server/received.bin
```

在客户端机器上查看源文件 SHA256：

```bash
cat tests/logs/cross_machine_client1/source.sha256
```

服务端 `received.bin` 的 SHA256 应与客户端 `source.bin` 一致。

### 成功条件

- 客户端：`client1.log` 中 `authorized_sends > 0`
- 客户端：`scheduler.log` 有 `grant seq=`
- 服务端：`server.log` 中 `PKT count_p_data` 增长
- 服务端：`kcp_receiver.log` 显示 `[PASS] KCP 小文件接收完成`
- 服务端接收文件与客户端源文件 SHA256 一致

> **关于 sender 报告 `[FAIL]`**：单向传输场景下，`kcp_small_sender` 可能报告 `[FAIL] KCP 小文件发送未收到 ACK`。这是预期行为——服务端只有 `wfb_rx` 接收数据，没有 `wfb_tx` 发送 ACK 回复。数据是否真正传到，应以服务端 `kcp_receiver.log` 的 `[PASS]` 与最终 SHA256 一致为准。

---

## 参考资料

- 项目上下文：`CONTEXT.md`
- 版本路线图：`docs/后续版本路线图.md`
- `v5` 基线规划：`docs/v5迁移基线规划.md`
- 真实硬件主线脚本：`tests/real_hardware/test_token_gated_uplink.sh`
- `v1` monitor 主线脚本：`tests/real_hardware/v1_mainline_monitor.sh`

**最后更新**：2026-06-04
