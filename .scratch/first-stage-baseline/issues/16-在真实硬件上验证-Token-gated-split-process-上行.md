# Issue #16: 在真实硬件上验证 Token-gated split-process 上行

- 原始链接: https://github.com/Softmoonlit/wfb-ng/issues/16
- 状态: open
- 标签: needs-triage
- 创建时间: 2026-05-07T14:06:38Z
- 更新时间: 2026-05-08T12:58:00Z

---

## Parent

https://github.com/Softmoonlit/wfb-ng/issues/6

## What to build

在真实 server/client 硬件上验证 Token-gated split-process 上行链路。验收重点是无 Token 时 Client 静默、单客户端 Token 窗口内 ping 或小 UDP 可通、双客户端场景中只有当前 Token holder 能上行，并记录足够日志用于问题定位。

## TDD

不适合纯 TDD：这是 HITL 真实硬件验收测试。适合作为阶段验收脚本和手动记录。

建议验收步骤（已在编排层实现自动化采集）：
- [ ] 无 Token 时 Client 上行不发射 (`no-token` 场景)
- [ ] 单客户端 Token 窗口内 ping 或小 UDP 可通 (`single` 场景)
- [ ] Token 过期后上行停止 (`expiry` 场景)
- [ ] 双客户端只有当前 Token holder 可上行 (`dual` 场景)
- [ ] 保存 scheduler、receiver、sender 和链路观测日志

## 执行入口与编排

已在 `wfb-ng-master` 下就绪专用验证脚本，用户可通过注入实际启动命令触发：

- `tests/real_hardware/test_token_gated_uplink.sh`
- `tests/real_hardware/one_click_test.sh --token --token-scenario <all|single|expiry|dual>`

说明：当前 `wfb_token_scheduler` 在 `src/token_scheduler.cpp` 中已实现 `grant/guard` 时间窗输出，并已补齐通过 `-s <base_socket>` 向单客户端 `wfb_tx` 的 `<base_socket>.token` Unix datagram socket 下发本机 Token 授权事件，以及通过 `-s node:base_socket,node:base_socket` 向多客户端按 `node_id` 分发 Token 授权事件的能力。当前真实硬件验收仍以 `TOKEN_*_CMD` 环境变量钩子注入实际进程启动命令。

2026-05-08 更新：`single` 与 `expiry` 的自动判定已改为以 `client1.log` 中的 `TOKEN_AUTH authorized_sends` 和 `server.log` 中的 `PKT count_p_data` 为准，不再把本地探测命令退出码单独作为链路通过依据。`expiry` 会先确认过期前存在授权发送和服务端收包，再停止 scheduler、等待 Token 过期，并检查后续探测没有新增授权发送或服务端数据包。

2026-05-08 更新：`dual` 的软件阻塞项已补齐，调度器支持 `-s 1:5602,2:5603` 多 socket 映射，脚本会解析 `client1.log` / `client2.log` 的 `TOKEN_AUTH authorized_sends/denied_sends` 与 `server.log` 的 `PKT count_p_data` 自动输出 PASS/FAIL；仍需真实硬件执行一次 dual 并记录结果。

## 执行记录模板

请在真实环境执行后，将生成的 `tests/logs/test_XXXX/token_results.md` 内容贴到下方：

- 测试日期：2026-05-08
- server 网卡：wlxbcec23372588
- client1 网卡：wlxfca386b38672
- client2 网卡（如有）：wlx08107a083815
- split-process 启动命令：
  - rx: `sudo ./wfb_rx -p 0 -u 5600 -K gs.key -R 16777216 -l 1000 -i 0 wlxbcec23372588`
  - client1 tx: `sudo ./wfb_tx -g -p 0 -u 5602 -K drone.key -B 40 -k 12 -n 12 -i 0 wlxfca386b38672`
  - client2 tx: `sudo ./wfb_tx -g -p 0 -u 5603 -K drone.key -B 40 -k 12 -n 12 -i 0 wlx08107a083815`
- Token 发放命令：
  - single/expiry: `sudo ./wfb_token_scheduler -n 1 -d 40 -g 5 -s 5602`
  - dual: `sudo ./wfb_token_scheduler -n 1,2 -d 1000 -g 100 -s 1:5602,2:5603`
- 探测命令：
  - client1: `echo "TOKEN_TEST_PACKET" | nc -w 1 -u 127.0.0.1 5602`
  - client2: `echo "TOKEN_TEST_PACKET" | nc -w 1 -u 127.0.0.1 5603`
- 日志目录：
  - baseline: `/home/kilome/wfb-ng/tests/logs/test_20260508_112303`
  - no-token: `/home/kilome/wfb-ng/tests/logs/test_20260508_112647`
  - single 初跑: `/home/kilome/wfb-ng/tests/logs/test_20260508_113237`
  - expiry 初跑: `/home/kilome/wfb-ng/tests/logs/test_20260508_114142`
  - single 通过: `/home/kilome/wfb-ng/tests/logs/test_20260508_125716`
  - expiry 通过: `/home/kilome/wfb-ng/tests/logs/test_20260508_125729`

### 场景结果

- [x] baseline 未回归
- [x] no-token 静默
- [x] single 窗口可通（`test_20260508_125716`：`grant=3 authorized_sends=1 server_data=1`）
- [x] expiry 后停发（`test_20260508_125729`：过期后 `authorized_sends` 未新增，`denied_sends=1`）
- [x] dual holder-only（`test_20260508_132506`：client1/client2 均在各自 grant 窗口上行成功，`server_data=1`）

### 本次执行记录（2026-05-08 12:57）

- single: PASS，日志 `/home/kilome/wfb-ng/tests/logs/test_20260508_125716/token_results.md`
  - `grant=3`
  - `authorized_sends=1`
  - `server_data=1`
- expiry: PASS，日志 `/home/kilome/wfb-ng/tests/logs/test_20260508_125729/token_results.md`
  - 过期前已形成有效授权发送
  - 停止 scheduler 并等待过期后未新增授权发送
  - `denied_sends=1`
- dual: PASS，日志 `/home/kilome/wfb-ng/tests/logs/test_20260508_132506/token_results.md`
  - `client1 authorized=1 denied=0`
  - `client2 authorized=1 denied=0`
  - `server_data=1`

### 关键日志核查点

- scheduler `grant seq=` / `guard seq=`：确认调度器输出了轮询
- client1 `TOKEN_FILTER`：确认 receiver 收包计数
- client1 `TOKEN_AUTH`：确认 sender 发包/被拒计数
- client2 `TOKEN_FILTER`（如有）：
- client2 `TOKEN_AUTH`（如有）：


- https://github.com/Softmoonlit/wfb-ng/issues/15
