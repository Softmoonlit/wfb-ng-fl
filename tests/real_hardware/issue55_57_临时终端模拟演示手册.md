# Issue #55-#57 临时终端模拟演示手册

## 定位

由于 #53 的真实硬件链路问题尚未解决，本入口用于现场展示三角色的预期终端过程。三个角色通过管理网 TCP 控制通道同步，但控制消息只包含角色、轮次和阶段，不携带模型或 update 内容。

模型接收、synthetic update 上传、SHA-256、耗时、吞吐率和 HTTP 状态仍是计时模拟，不来自真实 Runtime。该输出不能用作 #53、#55、#56 或 #57 的真实硬件验收证据。

## 控制通道

默认配置：

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `DEMO_CONTROL_BIND` | `0.0.0.0` | server 监听地址 |
| `DEMO_SERVER_HOST` | `192.168.122.1` | client 连接的 server 管理网地址 |
| `DEMO_CONTROL_PORT` | `45557` | TCP 控制端口 |
| `DEMO_TRANSFER_MBPS` | `5` | 未传位置参数时使用的演示速率 |
| `DEMO_CONNECT_TIMEOUT_SECONDS` | `120` | 等待连接上限 |
| `DEMO_CONTROL_TIMEOUT_SECONDS` | 动态值 | 默认为 `max(180, 基准传输秒数 × 2)` |

如果 server 的现场管理网地址不是 `192.168.122.1`，在 client1 和 client2 上把 `DEMO_SERVER_HOST` 设置成实际地址。三台机器的 `DEMO_CONTROL_PORT` 必须一致。

控制协议按以下屏障推进：

```text
HELLO -> READY -> MODEL -> MODEL_RECEIVED
      -> UPDATE_STARTED -> UPDATE_COMPLETE -> UPDATE_RECEIVED
      -> ROUND_COMMITTED -> 第二轮 -> PASSED
```

server 必须发现两个不同的 NODE_ID `[1,2]` 才会发布第一轮模型。每个 client 发送 `UPDATE_COMPLETE` 后，server 收齐双方信号并返回带 NODE_ID 和轮次的 `UPDATE_RECEIVED`；client 只有校验该确认后才显示“控制同步成功”，随后等待统一 `ROUND_COMMITTED`。缺少节点、重复 NODE_ID、消息乱序、超时或断线都会返回非零，不会显示最终成功。

## 现场执行

建议先启动 server：

```bash
bash tests/real_hardware/issue57_server_demo.sh 5
```

然后启动 client1：

```bash
DEMO_SERVER_HOST=192.168.122.1 \
  bash tests/real_hardware/issue56_client1_demo.sh 5
```

再启动 client2：

```bash
DEMO_SERVER_HOST=192.168.122.1 \
  bash tests/real_hardware/issue56_client2_demo.sh 5
```

client 可以先于 server 启动；此时会重复显示“等待 server 控制通道”，连接成功后才继续。server 会等待两个 client 都注册，不依赖人工控制终端时序。三台机器的位置参数必须使用同一个 Mbps 值，否则 server 会拒绝继续。

## 速率和演示时长

入口的第一个位置参数表示十进制 Mbps。例如 `5` 表示 `5,000,000 bit/s`。当前模型和 update 均为 `41943040 bytes`（40 MiB），基准时间公式为：

```text
传输秒数 = 41943040 × 8 ÷ (Mbps × 1000000)
5 Mbps   = 67.108864 秒
```

model 下发按一次广播过程计算，server 与两个 client 使用 `1.02` 到 `1.08` 的小幅耗时系数，保证观测到的 effective Mbps 严格小于配置速率。update 上行由两个 client 共享信道，每个 client 的有效速率约为配置值的一半，耗时系数为 `1.93` 到 `2.08`；server 的双路接收窗口略长，系数为 `2.11` 到 `2.15`。因此三台机器配置都显示 `5 Mbps`，但每次传输的 elapsed 和 effective Mbps 不会完全相同。5 Mbps 示例：

| 角色与阶段 | 示例耗时 |
| --- | --- |
| server 第一轮 model | `69.79s` |
| client1 第一轮 model | `68.45s` |
| client2 第一轮 model | `71.14s` |
| server 第一轮 update 接收 | `144.28s` |
| client1 第一轮 update | `139.59s` |
| client2 第一轮 update | `129.52s` |

模型发布与两个 client 的模型接收并行计时，两个 client 的 update 上传也并行计时。server 的接收窗口接近较慢 client，并包含少量收尾开销，不会把两路耗时再次相加。5 Mbps 下两轮完整过程通常约需 7 到 8 分钟。

也可以不传位置参数，统一使用环境变量：

```bash
export DEMO_TRANSFER_MBPS=5
```

自动测试时可以关闭显示计时，但保留控制连接和同步屏障：

```bash
DEMO_DELAY_SECONDS=0 bash tests/real_hardware/issue57_server_demo.sh 5
```

`DEMO_WAIT_STEP_SECONDS` 控制等待状态的刷新间隔，默认 2 秒。每个传输阶段固定显示五步。设置 `NO_COLOR=1` 可关闭 ANSI 颜色。

## 路径显示

client 每轮模型接收完成时显示逻辑目标路径：

```text
model_path=/var/lib/wfb-ng/issue55-57/client1/round-1/model.bin
```

server 每个 update committed 时显示逻辑目标路径：

```text
update_path=/var/lib/wfb-ng/issue55-57/server/round-1/updates/node-1.bin
```

这些路径只用于终端过程展示，脚本不会实际创建目录或文件。

## 数据边界

会执行：

- 在管理网建立一个 TCP 控制连接。
- 传递不超过 1024 bytes 的 JSON 控制消息。
- 根据真实控制消息推进三个终端的阶段和最终退出状态。

不会执行：

- 不调用 `issue41_fl_runtime_loop.sh` 或其全流程命令。
- 不通过控制通道传输模型、update 或其他 FL 数据。
- 不执行 SSH、SCP、HTTP、UFTP 或无线数据传输。
- 不调用 systemd、sudo、`ip`、`iw` 或项目二进制。
- 不读取或生成 40 MiB 文件。
- 不创建日志、result、summary、archive 或其他验收证据文件。
