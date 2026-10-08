# 无 SSH 手动上行底座验收

`v6_manual_uplink_demo.sh` 用于三台独立角色机器上的双客户端受控上行：server 接收两个客户端的 TCP 文件，结合 SHA-256、发送许可、授权发送计数、队列摘要和无线接收日志判定结果。现场各机本地操作，客户端日志通过 U 盘或现场文件共享人工带回 server，不通过 SSH 自动控制。

本流程验收真实硬件上行通信底座，不覆盖完整 FL Runtime 四接口、共享下行或 systemd 生命周期。完整 Runtime 验收见 [../fl_runtime/](../fl_runtime/README.md)，SSH 通信演示见 [../demo/](../demo/README.md)。本目录不提供本机回归或 namespace 验收入口。

三台机器均需同版本仓库、可构建并运行的 `wfb_v6_uplink`、Python 3、`ip`、`iw`、匹配驱动和支持 monitor 模式及原始帧发送的真实无线网卡，以及 TUN 和 sudo 权限。按现场确认 `SERVER_IFACE`、`CLIENT1_IFACE`、`CLIENT2_IFACE`；其他无线、FEC、队列和文件大小参数遵循 [完整手册](v6新底座无SSH手动上行演示手册.md)。手册保留历史现场参数，现役脚本默认值和显式环境变量用于本次运行。

最简操作顺序（每个命令从对应机器的仓库根执行）：

1. server 生成本轮名称，将输出的 `export DEMO_NAME=...` 复制到三机所有操作终端：

   ```bash
   bash tests/link_uplink/v6_manual_uplink_demo.sh new-name
   ```

2. 分别在 server、client1、client2 完成准备：

   ```bash
   # server
   bash tests/link_uplink/v6_manual_uplink_demo.sh server-prepare
   # client1
   bash tests/link_uplink/v6_manual_uplink_demo.sh client1-prepare
   # client2
   bash tests/link_uplink/v6_manual_uplink_demo.sh client2-prepare
   ```

3. 三机各开独立终端保持对应 WFB 面板运行；server 另开终端接收文件，两个接收器均显示就绪后，两客户端另开终端发送：

   ```bash
   # server WFB 面板终端
   bash tests/link_uplink/v6_manual_uplink_demo.sh server-wfb
   # client1 WFB 面板终端
   bash tests/link_uplink/v6_manual_uplink_demo.sh client1-wfb
   # client2 WFB 面板终端
   bash tests/link_uplink/v6_manual_uplink_demo.sh client2-wfb
   # server 的另一个终端
   bash tests/link_uplink/v6_manual_uplink_demo.sh server-recv-all
   # client1 的发送终端
   bash tests/link_uplink/v6_manual_uplink_demo.sh client1-send
   # client2 的发送终端
   bash tests/link_uplink/v6_manual_uplink_demo.sh client2-send
   ```

4. 发送和接收完成后，三机 WFB 面板按 `Ctrl-C` 正常退出并刷新队列摘要。客户端各自打包，将压缩包人工复制到 server 后解包、汇总：

   ```bash
   # client1/client2
   bash tests/link_uplink/v6_manual_uplink_demo.sh client1-pack
   bash tests/link_uplink/v6_manual_uplink_demo.sh client2-pack
   # server
   bash tests/link_uplink/v6_manual_uplink_demo.sh server-unpack /path/client1.tgz /path/client2.tgz
   bash tests/link_uplink/v6_manual_uplink_demo.sh server-summary
   ```

默认归档目录为 `tests/logs/$DEMO_NAME/`，包含原始 WFB/TCP 日志、队列摘要、源文件/接收文件与 SHA-256、`formal_2a_summary.json` 和 `result.md`；客户端打包文件位于 `/tmp/${DEMO_NAME}_clientN_uplink_logs.tgz`。只有两客户端文件完整、发送及接收证据齐备且最终摘要通过，才算双客户端底座验收通过。

`v6_manual_uplink_make_source.py` 生成确定性源文件，`v6_manual_uplink_tcp_send_progress.py` 与 `v6_manual_uplink_tcp_recv_progress.py` 显示发送/接收进度；`v6_formal_2a_summary.py` 从实际日志生成 2A 摘要，`formal_summary.py` 提供摘要字段校验与读写，二者均为现役硬件证据辅助代码。
