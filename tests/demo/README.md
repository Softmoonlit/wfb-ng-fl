# 通信演示

`run_fl_demo.sh` 在 server 本机通过 SSH 管理客户端，展示 UFTP 共享下行文件分发、受控 HTTP PUT 上行回传和终端指标看板。`fl_demo_metrics.py` 提供源文件生成、摘要、HTTP 收发、遥测解析及看板渲染。

这是通信演示入口，不调用完整 FL Runtime 四接口，也不包含正式 systemd 生命周期审计。完整 Runtime 验收见 [../fl_runtime/](../fl_runtime/README.md)，无 SSH 手动上行底座验收见 [../link_uplink/](../link_uplink/README.md)。本目录不提供本机回归或 namespace 验收入口。

现场需要 server 和客户端角色机器各自接入脚本可识别的 RTL8812AU 无线网卡及匹配驱动，支持 monitor 模式、原始帧发送和 TUN；安装 `wfb_v6_uplink`、`uftp`/`uftpd`、Python 3、`ip`、`iw`、SSH/SCP 等依赖，并具备所需 sudo 权限。准备仓库根的 `cluster_nodes.conf`、可用的 SSH 连接，以及所有客户端上的同版本仓库。远端仓库路径使用 `--remote-dir` 指定。

从仓库根执行最简命令：

```bash
bash tests/demo/run_fl_demo.sh all
```

默认自动生成演示源文件；指定已有文件、配置和远端仓库时执行：

```bash
bash tests/demo/run_fl_demo.sh all --file /path/model.bin \
  --config cluster_nodes.conf --remote-dir /path/to/wfb-ng-fl
```

`downlink`、`uplink` 可单独展示对应方向，`clean` 清理相关进程和 TUN。`--dry-run` 只模拟多机交互与看板，不产生可用于硬件验收的结果。

默认工作目录为 `/var/lib/wfb-ng/fl_demo/`，`run/` 内保存 `downlink_summary.json`、`uplink_summary.json`、WFB/UFTP/HTTP 日志和队列摘要；客户端下行文件位于 `received/`，server 回传文件位于 `server_received/`。`--work-dir` 可指定工作目录，指标与文件 SHA-256 用于解释本次通信演示结果。
