# fl03/fl04 十一板基线准备：阻塞

执行窗口：2026-11-08 09:18 UTC；仅 SSH fl03/fl04，只读核验，未安装、修改绑定、stop/start、设置电源或 reboot；未发起通信实验，未修改 registry 或 timeout。

已先阅读根 AGENTS.md、CONTEXT.md、.scratch/heartbeat-restart/release-online*.py 和 heartbeat-98dda459fb9a-20261108T073921Z/{README.md,state.json}。原发布明确排除 fl03，不能直接调用十目标发布入口或绕过 helper 的主机断言。

| 项目 | fl03 | fl04 |
|---|---|---|
| 预期 MAC | fc:22:1c:30:0f:04 | 5c:ff:ff:af:6d:8c |
| 实际 MAC | 5c:ff:ff:af:6d:8c | fc:22:1c:30:0f:04 |
| 实际接口 | wlx5cffffaf6d8c | wlxfc221c300f04 |
| driver | rtl88xxau_wfb | rtl88xxau_wfb |
| USB | 2-1.1 / 5000M | 2-1.3 / 5000M |
| daemon PID | 879 | 6871 |
| node_id / TUN配置 | 3 / 10.80.0.13/24 | 4 / 10.80.0.14/24 |
| 实际 iw | monitor,149,HT40,idx12 (-12.00 dBm) | managed,无信道,idx18 (-18.00 dBm) |
| 功耗 | power mode is not set，不能验收15W | 15W，mode 0 |
| 磁盘native SHA | 195fda112cf647d4e16d8158bc9fcc45975fe63777bd870faa6a6dd36a9e706d | ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56 |
| 服务入口 | /usr/bin/wfb-fl-client-daemon --config /etc/wfb-ng-fl/node.json | 98dda release daemon.py，仍绑定已拔接口 wlxfc221c300cbc |

结论：两张实际卡与用户确认分配相反，按指令保留核验、报告阻塞，不根据实际插卡擅自重绑。149/HT40/LGI/idx18基线未通过。两个节点精确以 argv[0] 路径结尾 /wfb_v6_uplink 查找时均无 native；完整 ps 保存于 inventory 中供复核。fl03无 release wrapper，因此没有 loaded.json；runtime 保存实际入口、exe、安装文件路径和SHA，不能据此宣称旧daemon进程的Python模块已通过loaded核验。fl04的原loaded.json/manifest和完整安装文件SHA已保存；其loaded记录只是wrapper启动时证据，不代表native当前存活。

UFTP磁盘摘要也保留于inventory：fl03与fl04不同；本次因硬件分配阻塞未进一步安装或确认fl00的UFTP版本/SHA，未访问其它节点。未尝试设置fl03电源模式，故没有触发重启判断或重启。

证据：inventory-fl03.json、inventory-fl04.json保存实际执行命令、UTC、返回码、stdout/stderr，包含service、配置、USB、iw、nvpmodel、UFTP路径/SHA及完整ps；runtime-*.json补充运行入口、loaded、manifest、安装文件SHA和journal。run.py保存完整只读探针。fl03首次连接使用StrictHostKeyChecking=accept-new，未禁用changed-host验证。
