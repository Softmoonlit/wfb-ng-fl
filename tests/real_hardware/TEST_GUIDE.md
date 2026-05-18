# 真实硬件测试指南

本文档指导开发者使用 split-process (`wfb_rx` / `wfb_tx`) 架构进行真实硬件上行链路测试，包括 Token-gated 上行测试。命令以 2026-05-08 实测通过的三网卡环境为例，后续开发者可按自己的网卡名替换。

## 目录

- [前置条件](#前置条件)
- [通用环境准备](#通用环境准备)
- [v1-mainline 真实硬件 monitor 主线验收](#v1-mainline-真实硬件-monitor-主线验收)
- [Token-gated split-process 测试](#token-gated-split-process-测试)
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

#### 双客户端 holder-only 场景

至少需要 3 张支持 monitor/injection 的 Wi-Fi 网卡：

| 角色 | 实测网卡 | 说明 |
| --- | --- | --- |
| server / receiver | `wlxbcec23372588` | 运行 `wfb_rx` |
| client1 / sender | `wlxfca386b38672` | 运行 `wfb_tx -g`，监听 UDP `5602` |
| client2 / sender | `wlx08107a083815` | 运行 `wfb_tx -g`，监听 UDP `5603` |

三张网卡可以插在同一台开发机上，也可以分布在多台机器上；如果分布运行，需要把 `TOKEN_*_CMD` 包成对应的 SSH 启动命令，并确保日志能回收到执行脚本的机器。

### 软件要求

```bash
command -v nc && echo "✓ nc 已安装" || echo "✗ nc 未安装"
command -v tcpdump && echo "✓ tcpdump 已安装" || echo "✗ tcpdump 未安装"
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

> 适用范围：Issue 16 真实硬件上行测试。主线是 `wfb_tx / wfb_rx` 拆分架构，不是 `wfb_core` 单进程实验路径。

统一编排脚本：

- `tests/real_hardware/test_token_gated_uplink.sh`
- `tests/real_hardware/one_click_test.sh --token`

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

**目的**：验证单客户端在 Token 授权窗口内能正常上行。scheduler 下发 grant 后，client 的 `authorized_sends` 应增长，server 应收到数据包。

**必要性**：这是 Token gate 正常工作路径的最小验证 — 授权-发送-接收的闭环。如果此项失败，说明 Token 授权链路本身不通。

scheduler 向 client1 的 `5602.token` 下发授权，client1 应在窗口内上行成功，server 应收到数据包。

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

scheduler 停止后等待 Token 过期，再探测应被 gate 拒绝；脚本会比较过期前后 `authorized_sends`、`denied_sends` 和 server 收包计数。

```bash
sudo env \
  TOKEN_SERVER_START_CMD='./wfb_rx -p 0 -u 5600 -K gs.key -R 16777216 -l 1000 -i 0 wlxbcec23372588' \
  TOKEN_CLIENT1_START_CMD='./wfb_tx -g -p 0 -u 5602 -K drone.key -B 40 -k 12 -n 12 -i 0 wlxfca386b38672' \
  TOKEN_SCHEDULER_CMD='./wfb_token_scheduler -n 1 -d 40 -g 5 -s 5602' \
  TOKEN_PROBE_CMD='echo "TOKEN_TEST_PACKET" | nc -w 1 -u 127.0.0.1 5602' \
  TOKEN_POST_EXPIRY_PROBE_CMD='echo "TOKEN_TEST_PACKET_AFTER_EXPIRY" | nc -w 1 -u 127.0.0.1 5602' \
  bash tests/real_hardware/test_token_gated_uplink.sh --scenario expiry
```

#### dual holder-only 测试

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

#### KCP 小文件上传测试

**目的**：验证 Token 授权通过后，端到端数据通道（KCP 层）能完整闭环 — 发送端注入文件，接收端落盘并校验 SHA256 一致。

**必要性**：前四项测试只验证 Token 门控逻辑本身，不涉及实际数据载荷。KCP 小文件上传是端到端数据通道的集成验证，确保 Token 授权链路与数据传输链路协同正常。如果跳过此项，无法确认数据是否真正可达。

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

如果你已经把环境变量固化，也可以通过 `one_click_test.sh` 快速执行：

```bash
sudo bash tests/real_hardware/one_click_test.sh --token --token-scenario single
sudo bash tests/real_hardware/one_click_test.sh --token --token-scenario expiry
sudo bash tests/real_hardware/one_click_test.sh --token --token-scenario dual
```

---

## 跨机器 KCP 小文件上传测试

> 适用场景：三张网卡分布在三台机器上，需要跨机器完成 KCP 文件上传闭环。

### 确定机器角色和网卡

在开始测试前，需要先确定每台机器的角色及其网卡名称：

```bash
# 在每台机器上执行，查看本机的无线网卡
iw dev | grep Interface
```

根据网卡数量确定角色：
- **1 张网卡的机器** → 客户端（运行 `wfb_tx`）
- **1 张网卡的机器** → 客户端（运行 `wfb_tx`）
- **1 张网卡的机器** → 服务端（运行 `wfb_rx`）

记录每台机器的 IP 和网卡名，后续步骤中用 `<SERVER_NIC>`、`<CLIENT1_NIC>`、`<CLIENT2_NIC>` 占位符表示，请替换为实际网卡名。

### 部署布局示例

以下以实测环境为例，请根据实际情况替换：

| 机器 | 角色 | 网卡示例 | 运行的进程 |
| --- | --- | --- | --- |
| 机器 A (服务端) | server | `wlx08107a083815` | `wfb_rx`、`kcp_small_receiver` |
| 机器 B (客户端1) | client1 | `wlxfca386b38672` | `wfb_tx -u 5602`、`wfb_token_scheduler`、`kcp_small_sender` |
| 机器 C (客户端2) | client2 | `wlxbcec23372588` | `wfb_tx -u 5603`、`wfb_token_scheduler`、`kcp_small_sender` |

> Token 调度器 (`wfb_token_scheduler`) 通过 Unix domain socket (`<port>.token`) 向同机的 `wfb_tx` 下发 Token，因此调度器必须和 `wfb_tx` 运行在同一台机器上。跨机器测试时，每台 client 机器独立运行自己的 scheduler。

### 前置条件

每台机器都需要：
- 已编译的 `wfb_rx`、`wfb_tx`、`wfb_token_scheduler`、`kcp_small_receiver`、`kcp_small_sender`
- `gs.key`（server 端）和 `drone.key`（client 端）
- 网卡已切换为 monitor 模式并设置信道 157 HT40+（参见[无线网卡配置](#无线网卡配置)）
- 三台机器之间网络互通（用于分发文件和对齐日志）

### 步骤 1 — 环境准备

所有三台机器执行：

```bash
# 清理残留
sudo pkill -9 -f "wfb_tx" || true
sudo pkill -9 -f "wfb_rx" || true
sudo pkill -9 -f "wfb_token_scheduler" || true

# 验证网卡 monitor 模式（替换为实际网卡名）
iw dev <SERVER_NIC> info   # 机器 A
iw dev <CLIENT1_NIC> info  # 机器 B
iw dev <CLIENT2_NIC> info  # 机器 C
```

### 步骤 2 — 机器 A（服务端）：启动接收端

> **注意启动顺序**：`kcp_small_receiver` 必须先于 `wfb_rx` 启动。两者都需要绑定 UDP 5600 端口，但只有 receiver 需要 listen，wfb_rx 只是向该端口发送数据。若顺序颠倒，receiver 将因端口被占用而无法启动。

```bash
# 在机器 A（服务端）上执行，cd 到项目根目录
LOG_DIR="tests/logs/cross_machine_server"
mkdir -p "$LOG_DIR"

# 1. 先启动 kcp_small_receiver（监听本地端口 5600，等待 KCP 数据）
./kcp_small_receiver --port 5600 --output "$LOG_DIR/received.bin" --timeout-ms 120000 \
    > "$LOG_DIR/kcp_receiver.log" 2>&1 &
KCP_RX_PID=$!
sleep 1

# 2. 再启动 wfb_rx（无线接收，向 receiver 监听的端口输出数据）
# 替换 <SERVER_NIC> 为服务端网卡名
sudo ./wfb_rx -p 0 -u 5600 -K gs.key -R 16777216 -l 1000 -i 0 <SERVER_NIC> \
    > "$LOG_DIR/server.log" 2>&1 &
WFB_RX_PID=$!

echo "服务端就绪: kcp_rx=$KCP_RX_PID wfb_rx=$WFB_RX_PID"
echo "日志目录: $LOG_DIR"
```

### 步骤 3 — 机器 B（客户端1）：启动发送端

```bash
# 在机器 B（客户端1）上执行，cd 到项目根目录
LOG_DIR="tests/logs/cross_machine_client1"
mkdir -p "$LOG_DIR"

# 1. 启动 wfb_tx（替换 <CLIENT1_NIC> 为客户端1网卡名）
sudo ./wfb_tx -g -p 0 -u 5602 -K drone.key -B 40 -k 12 -n 12 -i 0 <CLIENT1_NIC> \
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

### 步骤 4 — 机器 C（客户端2）：启动发送端

```bash
# 在机器 C（客户端2）上执行，cd 到项目根目录
LOG_DIR="tests/logs/cross_machine_client2"
mkdir -p "$LOG_DIR"

# 1. 启动 wfb_tx（替换 <CLIENT2_NIC> 为客户端2网卡名）
sudo ./wfb_tx -g -p 0 -u 5603 -K drone.key -B 40 -k 12 -n 12 -i 0 <CLIENT2_NIC> \
    > "$LOG_DIR/client2.log" 2>&1 &
WFB_TX_PID=$!
sleep 1

# 2. 启动本地 Token 调度器
./wfb_token_scheduler -n 2 -d 1000 -g 100 -s 5603 \
    > "$LOG_DIR/scheduler.log" 2>&1 &
SCHED_PID=$!
sleep 2

# 3. 生成测试文件并执行 KCP 上传
dd if=/dev/urandom of="$LOG_DIR/source.bin" bs=65536 count=1 status=none
sha256sum "$LOG_DIR/source.bin" | tee "$LOG_DIR/source.sha256"

./kcp_small_sender --host 127.0.0.1 --port 5603 --input "$LOG_DIR/source.bin" --timeout-ms 60000 \
    > "$LOG_DIR/kcp_sender.log" 2>&1
SENDER_RC=$?

echo "KCP sender 退出码: $SENDER_RC"
echo "日志目录: $LOG_DIR"
```

### 步骤 5 — 验证结果

在服务端（机器 A）等待 `kcp_small_receiver` 退出后：

```bash
# 查看接收结果
grep 'PASS\|FAIL' tests/logs/cross_machine_server/kcp_receiver.log
# 校验接收文件 SHA256
sha256sum tests/logs/cross_machine_server/received.bin
```

在客户端（机器 B/C）上查看源文件 SHA256：

```bash
# 客户端1
cat tests/logs/cross_machine_client1/source.sha256
# 客户端2
cat tests/logs/cross_machine_client2/source.sha256
```

与服务端接收文件 SHA256 对比，应一致。

### 成功条件

- 客户端：`client*.log` 中 `authorized_sends > 0`
- 客户端：`scheduler.log` 有 `grant seq=`
- 服务端：`kcp_receiver.log` 显示 `[PASS] KCP 小文件接收完成`
- 服务端接收文件与客户端源文件 SHA256 一致

> **关于 sender 报告 `[FAIL]`**：单向传输场景下，`kcp_small_sender` 会报告 `[FAIL] KCP 小文件发送未收到 ACK`。这是预期行为 — 服务端只有 `wfb_rx` 接收数据，没有 `wfb_tx` 发送 ACK 回复。数据实际已成功传输，应以服务端 `kcp_receiver.log` 显示 `[PASS]` 为准。

---

## 参考资料

- 项目文档：`CLAUDE.md`
- 测试配置：`tests/config/test_config.sh`
- Issue 16 进度：`docs/issues/ISSUE-16-在真实硬件上验证-Token-gated-split-process-上行.md`

**最后更新**：2026-05-10
