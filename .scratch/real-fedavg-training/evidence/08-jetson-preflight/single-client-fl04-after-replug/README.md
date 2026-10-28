# fl04 重新插拔后单节点复测

运行：`20261028T103052Z-single-fl04-after-replug`，2026-10-28 UTC。复用此前单 fl04 执行器，bundle SHA-256 `e92accdd9cce9425545264c220deae8078bd2bcf0c22e58d54af51298723bbc3`，未改脚本或生产配置。

## 条件与结果

- 四端准备阶段核验安装 Python 文件、加载路径和运行中 native 摘要，均通过冻结提交 701963a 的门禁。
- fl04 网卡 MAC `fc:22:1c:30:0f:04`，USB 位于 Port 4，驱动 rtl88xxau_wfb，速率 5000M。实际射频读回信道 157 / 40 MHz / 中心频率 5795 MHz / `-14.00 dBm`；Server MCS 3，fl04 MCS 4，FEC 8/14。
- Server 成功收到 fl04 REGISTER、FILEINFO_ACK、NAK、COMPLETE；fl04 持续收到有效 grant，原先无上行声明/无授权的失败模式未在本次固定信道复测中出现。
- 4 MiB 下行成功，客户端实际文件大小 4194304，SHA-256 `b7f03e8ff310274b7bd7e9b6f67a3a6cc538251d793684ce6b3a0f3d5457e869` 与源文件一致。UFTP 文件阶段耗时 3.961 秒（不含前置注册等待）。
- HTTP 上行失败：发送端在 `sock.sendall` 处 `TimeoutError: timed out`，未取得 HTTP 201。runner 随后将 traceback 当作 JSON 解析，外层错误显示 JSONDecodeError；真实失败原因应以 `fl04-collected.json` 内 `http-send.log` 为准。
- 独立核验 Server 遗留文件：`client_4_update.bin` 仅 2949120 字节，约 2.81 MiB，小于目标 4 MiB；SHA 不匹配，确认未完整回传。详见 `server-upload-files.json`。

## 恢复与判断

runner 已完成自有进程清理并恢复四端常驻服务。runner 的 60 秒恢复观察未确认全员 READY，因此其 recovery_errors 如实保留。随后追加采样显示 fl04 从 CONNECTING 转 READY，连续四次 READY，心跳年龄约 3.5～4.4 秒；Server IDLE，fl01/fl02 READY。追加采样不能覆盖原始恢复超时事实，也不能证明长期稳定。

本次相比前次有明确改善：有效上行控制、授权和完整下行均已恢复，但 4 MiB 上行未通过。目前不能据此认定网卡损坏，也不能锁定为 USB、端口或驱动根因；用户重新插拔前后还观察到了启动时长及 USB 端口变化，不能将恢复效果唯一归因于单一动作。没有继续调整信道、功率或 MCS。

完整结果、RPC、native/UFTP/HTTP 日志、四端快照及追加状态均在 `runs/20261028T103052Z-single-fl04-after-replug/`。
