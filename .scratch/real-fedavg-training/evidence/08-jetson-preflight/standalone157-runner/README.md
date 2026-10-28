# 固定 157 standalone 64 KiB 对照结果

执行时间：2026-10-28 UTC，唯一 run id：`20261028T014839Z-standalone157`。

本次维护诊断在四端前置核验通过后执行：fl00、fl01、fl02、fl04 均运行 Python 提交 `701963a`，实际 native `/usr/bin/wfb_v6_uplink` SHA-256 均为 `f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1`，实际射频读回为信道 157、HT40+、14 dBm。停服、固定信道和 standalone 进程均由受限 runner 管理，未使用全局 `pkill`。

结果：

- 生成源文件 64 KiB，SHA-256：`d587ef7fd481e3477fbcfe350bebd4fc297789cd1d3c831b678603267f387c91`。
- fl01、fl02、fl04 均完成下行接收，大小和 SHA-256 全部一致。
- 三台客户端均完成一次 HTTP 回传，状态码均为 201；服务端三份文件大小和 SHA-256 全部一致。
- 回传耗时：fl01 0.198 s（2.65 Mbps）、fl02 3.857 s（0.14 Mbps）、fl04 0.031 s（17.06 Mbps）。fl02 的显著低速说明固定信道路径虽能完成短文件，但节点间链路质量仍不均衡。
- standalone 进程、TUN 和临时路由已清理；服务端及三台客户端 daemon 已恢复 active。
- 最终 API 采样为 Server `IDLE`、fl01/fl02/fl04 `READY`，恢复错误为空。

这次结果证明在固定信道、无 Python HUNTING 控制宿主的 native 路径上，三客户端可以完成一次 64 KiB 双向文件闭环。它不能证明完整 daemon 的心跳、寻频、作业控制或真实训练已经通过；也不能把 fl02 的低速解释为单一根因。

完整机器快照、实际 argv、服务配置备份、native/UFTP/HTTP 记录和恢复采样保存在本目录的 `runs/20261028T014839Z-standalone157/`。执行器与静态审查见上级 `standalone157-runner/` 和 `standalone-157-plan.md`。
