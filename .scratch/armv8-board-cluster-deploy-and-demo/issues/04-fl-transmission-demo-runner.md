# 04: 面向现场演示的上下行大文件传输总控脚本与多维指标看板

**What to build:** 交付面向现场汇报的轻量级演示总控脚本 `tests/real_hardware/run_fl_demo.sh`。支持单步独立下行（`downlink`）、单步独立上行（`uplink`）与全流程（`all`）；支持自定义下行大文件（缺省时自动现场生成 40MB 测试文件）；下行采用 UFTP 组播广播，各 Client 自动落盘并终端打印绝对路径；上行自动复用该文件发起受控 HTTP PUT 回传；解析底座带内遥测与系统网络状态，在终端分别渲染详尽的下行与上行指标看板。

**Reference & Prior Art:** 深度参考 Stage 1 / Stage 2 真实硬件多机基线脚本（`tests/real_hardware/issue41_fl_runtime_loop.sh` 及配套 `issue41_gate.py`）。
- **深度复用**：网卡 `find_wlx` 探测与 monitor 模式切换、UFTP 组播参数与端口绑定、HTTP PUT 受控上行通道与时隙参数、分源物理丢包、FEC 恢复包、TUN 反压与 TCP 重传解析器、残留进程与 TUN 清理逻辑。
- **坚决剔除**：剔除 git commit/branch 与工作区干净度断言；剔除 3 周期 smoke-gate 探针；剔除冗长 systemd 启停审计；放开硬编码固定 4MB/40MB 模板文件，改造为支持自定义任意 `--file` 路径及现场动态在线节点容错。

**Blocked by:** 02, 03

**Status:** ready-for-agent

- [ ] 编写总控脚本 `tests/real_hardware/run_fl_demo.sh`，支持子命令 `all`、`downlink`、`uplink`，支持选项 `--file <path>`、`--config <path>`。
- [ ] 动态探测与容错：读取 `cluster_nodes.conf`，通过 ping/ssh 快速确认在线 Client 集合，若存在离线节点输出警告并自动以在线节点集合开跑。
- [ ] 空口网卡准备：在 Server 和所有在线 Client 上探测唯一的 `wlx*` 网卡，执行 down/up、设为 monitor 模式、锁定到指定信道与频宽。
- [ ] 下行传输实现：
  - 校验源文件（或现场自动生成 40MB 确定性文件），计算并记录初始 SHA-256 与开始时间；
  - 远程在各 Client 拉起 `uftpd` 监听组播组，指定落盘目录 `/var/lib/wfb-ng/fl_demo/received/`；
  - Server 启动底座组播通道并调用 `uftp` 广播下发文件；
  - 等待各 Client 完成接收，远程计算各 Client 接收文件的 SHA-256 并核对无损一致性；
  - 终端渲染【下行传输指标看板】（耗时、平均速率、各 Client 接收绝对路径、哈希校验）。
- [ ] 上行传输实现：
  - 检查各 Client 端已接收的模型文件；
  - Server 端启动受控 HTTP PUT 接收端与 `wfb_v6_uplink` 调度服务（开启令牌与反压监测）；
  - 各 Client 端并发/轮流发起 HTTP PUT 将接收到的模型文件上传回 Server；
  - Server 端接收落盘到 `/var/lib/wfb-ng/fl_demo/server_received/`；
  - 解析 WFB 底座日志（`\tPKT\t`, `\tTOKEN_AUTH\t`, `TUN_PAUSE`, `TUN_RESUME`）与 Linux TCP 重传指标（`tcpi_retrans` / snmp）；
  - 计算 Server 最终接收文件 SHA-256 并与原文件严格核对；
  - 终端渲染【上行传输指标看板】（耗时、吞吐、空口收包、物理丢包率、FEC 恢复率、反压流控次数、TCP 重传次数、一致性校验）。
