# 单客户端 fl02 / 157 / HT40+ 诊断

本次用于隔离多客户端调度变量，不是训练验收。

- 四端预检：Python 提交 `701963a`，运行 native SHA-256 为 `f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1`。
- 射频：信道 157、HT40+、实际功率 `-14 dBm`、客户端上行 MCS 4、FEC 8/14。
- 活跃目标：仅 fl02（node 2）。服务端 standalone native 的 `known-clients` 和 `client-target` 均缩减为 node 2；fl01、fl04 只停服并在结束时恢复，不参与传输。
- 负载：固定 4 MiB；目标是先完成单客户端下行注册，再进行回传。
- 运行记录：[20261028T040716Z-single-fl02-157-ht40](runs/20261028T040716Z-single-fl02-157-ht40/)。

## 结果

- fl02 native 成功启动并读回 157 / HT40+，运行 native SHA 正确。
- fl02 本机能收到服务端空口包，native 记录了 `first_declare` / `redeclare`。
- fl02 的 `GRANT_SEQ` 始终为 0，`TOKEN_AUTH` 的授权发送始终为 0；本机只累计未授权发送拒绝。
- 服务端 `PKT_SRC node2` 每秒统计全为 0，没有收到 fl02 的有效上行；服务端没有为 node 2 形成稳定 grant。
- UFTP 客户端反复发送 REGISTER，最终退出码 6；未完成下行文件接收，也没有进入上行阶段。
- runner 清理无错误，但其结束时刻的部分客户端仍短暂处于寻频状态，因此按标准流程重启四端 daemon 并重新核验。
- 最终恢复：四端 157 / HT40+、`-14 dBm`、daemon active；Server `IDLE`，fl01、fl02、fl04 均 `READY`。

## 当前判断

这次单客户端失败排除了“只有三客户端同时在线才会失败”的充分解释：即使只保留 fl02，fl02 仍不能建立稳定的上行控制/授权闭环。

现有证据还不能在以下候选中定案：

1. fl02 本地网卡发射链、天线、USB/驱动或射频环境导致 READY/数据包到不了 Server；
2. fl02 所在位置的控制帧接收/发送余量不足；
3. native 控制帧会话或 READY 交付在单节点边界仍有协议问题。

由于 fl02 能收到 Server 包但 Server 收不到 node2 上行，下一步应使用同一单客户端 runner 先测 fl04 或 fl01 作对照，再比较 Server 侧 `PKT_SRC`、客户端 `GRANT_SEQ/TOKEN_AUTH` 和实际 RSSI/天线统计。不能仅凭 fl02 一次失败把问题归为软件或硬件。
