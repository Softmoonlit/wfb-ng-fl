# ACK channel identity：retune 完成前本机接收残留窄诊断

结论：本次指定边界 **未复现 red（0/3）**。当前实现没有因为该旧 157 ACK 将 149 判为 IDLE。不能据此声称存在已复现的跨频道误锁，也不能把 ACK 无身份认定为现场积压主因。

## 范围和方法

仅运行本目录 `ack-channel-loop.py`，调用真实 `ControlPlaneClient.hunt_once(ladder=[157,149])` 和 `send_heartbeat_once()`。使用真实 IPv4 UDP、loopback 动态 server 端口及系统分配的 client 源端口；client 目的地始终是 127.0.0.1。未启动 client 的后台 loop/broadcast listener。NetworkAdapter mock 只实现频道设置与频道设置的 Event 屏障。未改 production、未访问生产服务端口、未触及 TUN/RF、未运行大组测试。

保留默认 230ms/attempt 与每频道两次；未更改 hunting 时间预算。Event 的 2 秒超时只是诊断线程故障保护，并非 client 请求期限。没有 sleep 或延迟概率竞态。

每次严格顺序：

1. server 收到两次 157 HUNT HB，均暂不 ACK。
2. 第二次 157 等待自然超时，client 进入 mock `set_channel(149)`，adapter 通过 Event 挂起，尚未完成切换。
3. server 在收到 entered149 Event 后，仅对第二次旧 157 HB 发送一次真实 `HeartbeatAck`，等待内核 `sendto` 返回。
4. 主诊断线程等到 sendto-complete Event，再对 client 原始 socket 用 `MSG_PEEK | MSG_DONTWAIT` 验证完整旧 ACK 已经在接收队列、来源是动态 loopback server，且 client.current_channel 仍为 157。peek 不消费 ACK。
5. 主线程放行 adapter 完成 149；真实公开 hunt 继续执行。server 对所有 149 HB 均不 ACK。
6. hunt 返回后再调用一次公开 send 方法；同样不提供 149 ACK。

仅为无消费队列观测及资源清理读取 client 私有 socket 引用；没有替换 socket、调用私有 drain/send、mock 接收或修改 client 状态。该模型明确模拟 retune 完成前已进入本机 socket 的残留，不模拟 retune 完成后跨 RF 频道送达 ACK。

## 结果与原因

完整逐事件输出见 `output.json`，其中记录所加载 control.py 的 SHA-256、动态端口和每次时序。

| run | 收到 HB 频道序列 | 发送旧 ACK | hunt 返回 | hunt 后状态 | locked_channel | 公开 send 收到 ACK |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 157,157,149,149,149 | 1 | false | HUNTING | null | false |
| 2 | 157,157,149,149,149 | 1 | false | HUNTING | null | false |
| 3 | 157,157,149,149,149 | 1 | false | HUNTING | null | false |

首次运行关键单调时刻（毫秒，自本次启动）：set_channel149 进入 462.117；旧 ACK sendto 完成 462.342；peek 确认已入队 462.529；set_channel149 返回 462.564；server 收到首个 149 HUNT HB 462.806；hunt 返回 false 923.639。

原因可由必要源码直接解释：`wfb_ng/fl/control.py:2640` 附近的 `hunt_once` 在 **每次发送 HUNT HB 之前** 调用 `_drain_sock(sock)`；因此在 set_channel 返回前已经入队的 ACK 会在发送 149 HB 前被消费丢弃。`_send_heartbeat` 也在发送前 drain。本实验没有 monkeypatch drain 或对它打桩；结论依据队列入队证据、公开行为以及现有源码一致性。

这仅证伪所指定的残留时间边界。现有 ACK 的确没有 request identity，客户端只检查 ack 布尔值；本实验没有测试“drain 后到达的旧 ACK”等其他边界，未将这些未测边界扩展为新的 red，也未建立现场积压因果关系。即使其他独立 loop 复现了无身份 ACK 的错误接受，也只能先证明独立控制协议缺陷，不能直接推定为现场主因。

## 最简身份方案建议（仅建议，未实现）

HB 增加逐请求递增的 uint32 `s`（每一次重试都用新值），server 将它回显为紧凑 ACK `{"a":4294967295}`，最坏 16 个 ASCII 字节，满足 ACK <20 byte。`a` 的存在与匹配值同时表示肯定应答，省去独立布尔字段。client 仅接受等于当前等待请求序号的 ACK；过期、不匹配 ACK 继续丢弃，仍遵守原来的单次 230ms 总截止时间。

request identity 要覆盖频道切换与同频道重试；仅回显 channel 无法区分同频道旧尝试。序号不得在可能存在旧包的生命周期内复用；重启初值、回绕及其接受窗口需要正式实现时明确。这是请求关联方案，不是认证方案。本诊断未改变 ACK 格式、兼容行为或生产代码。

## 复跑

从仓库根目录执行：

```sh
PYTHONPATH=. python3 .scratch/real-fedavg-training/evidence/08-jetson-preflight/diagnosis/ack-channel-identity/ack-channel-loop.py
```

脚本固定三次，输出 JSON；屏障失败、异常流量等作为诊断失败抛出，不能算作 red。`red_runs` 单独报告误锁是否出现，当前为 0；未人为让脚本以红色退出制造缺陷证据。
