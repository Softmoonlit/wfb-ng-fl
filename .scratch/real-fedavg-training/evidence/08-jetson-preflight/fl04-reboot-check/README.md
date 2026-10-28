# fl04 整机重启核验

2026-10-28 18:24（CST）按用户要求通过管理网执行 `sudo systemctl reboot`，命令返回 0。boot ID 已变化，确认完成整机重启。

- 重启前：客户端服务 active，但多次报告连续两次未收到 ACK；Server 为 IDLE，fl04 已被判定 OFFLINE。
- 重启后：客户端服务自动启动，网卡仍为 `fc:22:1c:30:0f:04`，驱动 `rtl88xxau_wfb`，USB 5000M。
- `iw` 启动后及观察末尾均读回信道 157、40 MHz、中心频率 5795 MHz、功率 `-14.00 dBm`。
- Server 连续 10 次采样、间隔 5 秒：fl04 一直 OFFLINE，最后心跳的单调时间未推进。
- 重启后客户端日志再次出现“连续 2 次未收到 ACK，判定失联，退回寻频自愈”。

结论：本次整机重启未恢复 fl04 的无线心跳。尚未重跑独立文件传输，不能由本次结果判定网卡损坏或锁定物理根因。未修改软件、射频配置或 Server 服务状态。

证据：`before-fl04.txt`、`before-server-api.json`、`reboot-command.txt`、`after-fl04.txt`、`after-server-api-samples.json`、`after-observation-fl04.txt`。`before-server-api.txt` 是首次使用错误 API 端口的失败请求，正式重启前状态以 JSON 文件为准。
