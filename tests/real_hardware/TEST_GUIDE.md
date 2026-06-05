# v5 real-hardware 测试指南

本文档只服务于 `v5` 阶段的 real-hardware 迁移基线，不再覆盖旧的 `v1` monitor 主线、通用 Token 教学场景或跨机器 KCP 小文件试验。

对应约束以以下文档为准：

- `docs/后续版本路线图.md`
- `docs/v5迁移基线规划.md`

`v5` real-hardware 的职责只有两件事：

1. 形成 **双客户端 Token-gated 上行长稳主场景** 的正式迁移证据
2. 形成 **下行补充证据场景** 的正式迁移证据

它不是 CI 硬 gate，也不是 `v6` 的实现设计文档。

如需现场执行顺序、成功跑数命令、已知失败模式与避坑建议，请同时阅读：`tests/real_hardware/真实硬件正式跑数复盘与避坑手册.md`。

---

## 1. `v5` real-hardware 基线定义

### 主场景

使用 `tests/real_hardware/test_token_gated_uplink.sh --scenario dual-long-run` 固定双客户端 Token-gated 上行长稳运行，观察：

- 多客户端轮换是否持续推进
- `grant` / `guard` / `join` / `rejoin` / `remove` / `evict` 是否留痕且可解释
- 控制面是否长期可用
- 是否出现长时间停滞或明显抖动

### 补充场景

使用 `tests/real_hardware/test_full_transfer.sh` 固定下行补充证据：

- `single`：大文件下行完整性证据
- `shared`：共享下行 / 分发相关证据

### 验收口径

- `v5` 关注 **可见堵塞现象**，不验证内部队列占用
- 运行、采集、摘要尽量脚本化
- 最终是否构成正式 `v5` 迁移证据，必须由人工按固定模板判读
- 证据必须完成 **双层归档**，否则 `v5` tracking issue 不得关闭

---

## 2. 前置条件

### 硬件拓扑

#### 上行主场景：`dual-long-run`

至少需要 3 个无线角色：

| 角色 | 进程 | 说明 |
| --- | --- | --- |
| server | `wfb_rx` | 接收双客户端上行数据 |
| client1 | `wfb_tx -g` | 接收本机 Token 授权并发送 |
| client2 | `wfb_tx -g` | 接收本机 Token 授权并发送 |

#### 下行补充场景：`single` / `shared`

| 场景 | 最少接收端数量 | 目标 |
| --- | --- | --- |
| `single` | 1 | 大文件下行完整性 |
| `shared` | 2 | 共享下行 / 分发证据 |

角色可以同机多网卡，也可以跨机器分布；但日志、抓包、截图或等价证据必须最终回收到执行归档的人手里。

### 环境要求

参与测试的机器至少应具备：

- 支持 `monitor/injection` 的 Wi-Fi 网卡
- `wfb_rx`、`wfb_tx`、`wfb_token_scheduler`
- 下行场景需要 `uftp` / `uftpd`
- `gs.key`、`drone.key`
- `ip`、`iw`、`timeout`、`sha256sum`
- 可选：`tcpdump`（用于抓包留痕）

### 无线环境要求

所有参与测试的网卡都必须：

- 预先切换为 `monitor` 模式
- 固定到同一信道
- 在整个测试期间保持配置稳定

示例：

```bash
sudo ip link set <iface> down
sudo iw dev <iface> set type monitor
sudo ip link set <iface> up
sudo iw dev <iface> set channel 157 HT40+
```

### 关键约束

- `wfb_tx -g` 才启用 Token gate
- `wfb_token_scheduler -s <base_socket>` 通过 `<base_socket>.token` 向同机 `wfb_tx` 下发授权
- 双客户端映射使用 `-s node:base_socket,node:base_socket`，例如 `-s 1:5602,2:5603`
- `v5` 不要求在 split-process 主线上补做 `v6` 级别的内部队列观测
- 若现场调整默认门槛，必须在正式证据结论里写明原因与新口径

---

## 3. 执行前准备

建议在每次正式跑数前先执行固定入口脚本，先校正环境，再复检：

```bash
sudo bash tests/real_hardware/preflight_checklist.sh --apply
```

仅做现场检查、不修改系统状态时：

```bash
bash tests/real_hardware/preflight_checklist.sh --check-only
```

如果仍需要手工清理，优先使用更窄的进程名匹配，避免 `pkill -9 -f ...` 误杀编排链路或系统常驻 `uftpd`：

```bash
sudo pkill -x wfb_tx || true
sudo pkill -x wfb_rx || true
sudo pkill -x wfb_tun || true
sudo pkill -x wfb_token_scheduler || true
sudo pkill -x uftp || true
# uftpd 先通过 ps 确认来源，再决定是否结束

sudo ip link del rhsrvtun || true
sudo ip link del rhc1tun || true
sudo ip link del rhc2tun || true
```

若采用跨机部署，还应确认：

- 调度器与对应 `wfb_tx` 在同一台机器上
- 远端启动命令已封装为 `ssh ...` 或等价方式
- 日志目录、抓包、截图能被回收


---

## 4. 上行主场景：`dual-long-run`

统一入口：

- `tests/real_hardware/test_token_gated_uplink.sh --scenario dual-long-run`

### 固定口径

`v5` 首批冻结口径如下：

- 目标时长：`180` 秒
- 探测周期：`1` 秒
- 采样周期：`15` 秒
- 最小总 `grant` 数：`60`
- 每个 client 最小 `authorized_sends`：`10`
- 最大连续无推进窗口：`45` 秒
- `remove` / `evict` / 额外 `rejoin` 默认门槛：`0`

### 最小运行模板

```bash
sudo env \
  TOKEN_SERVER_START_CMD='<server 上的 wfb_rx 启动命令>' \
  TOKEN_CLIENT1_START_CMD='<client1 上的 wfb_tx -g 启动命令>' \
  TOKEN_CLIENT2_START_CMD='<client2 上的 wfb_tx -g 启动命令>' \
  TOKEN_SCHEDULER_CMD='<wfb_token_scheduler -n 1,2 -s 1:5602,2:5603 ...>' \
  TOKEN_CLIENT1_PROBE_CMD='<向 client1 对应 base_socket 注入探测流量>' \
  TOKEN_CLIENT2_PROBE_CMD='<向 client2 对应 base_socket 注入探测流量>' \
  bash tests/real_hardware/test_token_gated_uplink.sh --scenario dual-long-run
```

如果需要覆盖默认门槛，可显式设置：

- `TOKEN_LONGRUN_DURATION_SEC`
- `TOKEN_LONGRUN_PROBE_INTERVAL_SEC`
- `TOKEN_LONGRUN_SAMPLE_INTERVAL_SEC`
- `TOKEN_LONGRUN_MIN_GRANTS`
- `TOKEN_LONGRUN_MIN_AUTHORIZED_SENDS`
- `TOKEN_LONGRUN_MAX_IDLE_SEC`
- `TOKEN_LONGRUN_MAX_REMOVE_COUNT`
- `TOKEN_LONGRUN_MAX_EVICT_COUNT`
- `TOKEN_LONGRUN_MAX_REJOIN_COUNT`

### 自动产物

脚本至少生成：

- `token_results.tsv`
- `token_results.md`
- `token_context.txt`
- `dual_long_run_samples.tsv`
- `scheduler.log`
- `server.log`
- `client1.log`
- `client2.log`

### 自动判定关注点

自动结论至少应覆盖：

- `grant` 与 `guard` 持续出现
- 两个 client 的 `authorized_sends` 都有增长
- `server.log` 中接收侧数据包计数持续增长
- 没有超过门槛的 `remove` / `evict` / 额外 `rejoin`
- 没有超过 `45` 秒的连续无推进窗口

### 人工判读关注点

即使自动结论为 PASS，仍要检查：

- 是否存在接近门槛的长平台期
- `join` / `rejoin` / `remove` / `evict` 是否能被现场变化解释
- 是否出现明显的时延抬升、恢复变慢或长期失活
- 是否存在无法解释的异常；若有，不能作为正式基线证据

---

## 5. 下行补充场景：`single` / `shared`

统一入口：

- `tests/real_hardware/test_full_transfer.sh --scenario single`
- `tests/real_hardware/test_full_transfer.sh --scenario shared`

### 场景分工

- `single`：证明大文件下行完整性仍成立
- `shared`：证明共享下行 / 分发语义仍成立
- 两者都用于采集可见堵塞现象的外部结果

### 最小运行模板

```bash
sudo bash tests/real_hardware/test_full_transfer.sh --scenario single
sudo bash tests/real_hardware/test_full_transfer.sh --scenario shared
```

如果只需对已有日志目录重建摘要，不重新跑传输：

```bash
LOG_DIR=<已有日志目录> \
  bash tests/real_hardware/test_full_transfer.sh --analyze-only
```

### 常用环境变量

- `LOG_DIR`
- `DOWNLINK_PAYLOAD_SIZE`（默认 `41943040` 字节）
- `DOWNLINK_TRANSFER_TIMEOUT_SEC`（默认 `180` 秒）
- `DOWNLINK_SAMPLE_INTERVAL_SEC`（默认 `5` 秒）
- `DOWNLINK_MAX_IDLE_SEC`（默认 `45` 秒）
- `DOWNLINK_UFTP_SEND_CMD`
- `DOWNLINK_CLIENT1_RECEIVE_CMD`
- `DOWNLINK_CLIENT2_RECEIVE_CMD`
- `DOWNLINK_SERVER_TUN_CMD` / `DOWNLINK_CLIENT1_TUN_CMD` / `DOWNLINK_CLIENT2_TUN_CMD`
- `DOWNLINK_SERVER_TX_CMD` / `DOWNLINK_SERVER_RX_CMD` / `DOWNLINK_CLIENT1_TX_CMD` / `DOWNLINK_CLIENT1_RX_CMD` / `DOWNLINK_CLIENT2_TX_CMD` / `DOWNLINK_CLIENT2_RX_CMD`
- `PCAP_CAPTURE_CMD`

### 自动产物

脚本至少生成：

- `downlink_results.md`
- `downlink_context.txt`
- `downlink_samples.tsv`
- `metrics.json`
- `summary.txt`
- `uftp_server.log`
- 接收端日志
- 关键底层日志

### 自动判定关注点

- 源 payload 与接收 payload 的 SHA256 一致
- `shared` 场景下多个接收端收到相同 payload
- `downlink_samples.tsv` 中无超过门槛的连续无推进窗口
- 自动摘要已正确落盘到 `metrics.json` 与 `summary.txt`

### 人工判读关注点

- 是否存在接近门槛的平台期
- 时延抬升、重传增加、恢复变慢是否可解释
- 若启用抓包，是否已将抓包与摘要一起纳入证据引用

---

## 6. 正式证据包与双层归档

### 原始产物层

原始产物层不进入仓库版本历史，但必须可定位引用。

最少保留：

- 上行主场景：`token_results.md`、`token_context.txt`、`dual_long_run_samples.tsv`、`scheduler.log`、`server.log`、`client1.log`、`client2.log`
- 下行补充场景：`downlink_results.md`、`downlink_context.txt`、`downlink_samples.tsv`、`metrics.json`、`summary.txt`、`uftp_server.log`、接收端日志、关键底层日志
- 可选但推荐：`capture.pcap`、`pcap_capture.log`、现场截图、网卡模式/信道留痕、命令快照

### 正式结论层

正式结论层必须回填到对应 GitHub issue / tracking issue。最小回填单元不是“日志目录已存在”，而是一份人工判读后的正式结论。

必须至少包含：

- 运行场景与前置条件
- 原始产物层引用
- 运行时长与关键事件计数
- 关键异常与风险信号
- 是否可作为正式 `v5` 基线证据
- 是否需要重跑
- 对 `v6` 的风险提示

### 固定回填模板

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
- 关键启动命令或配置偏差：<若与默认口径不同，必须写明>

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

`v5` tracking issue 只有在以下条件同时满足后才能关闭：

- 上行主场景与下行补充场景都已有可定位的原始产物层引用
- 对应 issue / tracking issue 已按固定模板回填正式结论层
- 正式结论明确写出“可作为正式 `v5` 基线证据”或“需要重跑”
- 若存在抓包、共享存储或外部日志目录，issue 中已附上可追溯引用

反例：

- 只有本地日志目录，没有 issue 回填
- 只有自动摘要，没有人工判读结论
- 只有口头结论，没有固定字段和证据引用

---

## 7. 不再纳入本指南的内容

以下内容不再作为本文件维护范围：

- `v1` monitor 主线验收
- 通用 `no-token` / `single` / `expiry` / `dual` 教学式 Token 场景说明
- 跨机器 KCP 小文件上传试验
- `v6` 的内部实现设计、线程模型、公式或调参细节

如果确需使用这些旧入口，请直接查看对应脚本的 `--help` 或历史提交，不要继续把它们堆回 `v5` real-hardware 指南。
