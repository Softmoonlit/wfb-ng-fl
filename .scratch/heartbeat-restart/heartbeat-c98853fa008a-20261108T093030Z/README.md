# c98853f 全 11 节点发布证据

冻结源码 c98853fa008a945e3a87503051bae49f5d0083bd，目标 fl00..fl10（含 fl03）。preflight/stage/prepare/stop/install/start/audit 全阶段全目标通过；state.json 为阶段记录。

helper 修正：完整 11 节点约束；移除 daemon.py wrapper，保留各节点实际 --config 起的参数，以 /usr/bin/python3 -I -B -m wfb_ng.fl.{server,client}_daemon 标准入口启动；server audit 从 source JSON 读取 grant/guard，缺省 10/10。prepare 保存接口、actualargs、配置与 UFTP 摘要。audit 检查 proc Python 与 native 参数、安装源码和冻结源码、native SHA、UFTP 摘要、radio query LGI/40MHz/MCS、iw 149/索引18、nvpmodel 15W。

全 11 可用且 10 client READY 后，只改 server JSON grant_duration_ms=15、guard_interval_ms=5，只 systemctl restart wfb-fl-server-daemon.service。server-configuration-change.json 记录差异；server-loaded.json 同步最终 JSON 与 actualargs。final-audit-*.json 全 11 exit=0，after-grant-status.json 全 10 client READY。verification.json 对比 PID：只 server 变化，10 client PID 均不变，所有 actualargs 不变。

启动初期首轮 audit 有客户端仍在寻频，随后完整阶段 audit 全通过；未进行节点排除、未切换 157、未执行通信实验。原绑定接口均有效，本轮无需重新绑定。未修改 native、UFTP、15W、LGI、149/40MHz/功率索引18 或其它配置。

loaded.json 的 Python 证据是实际 /proc 启动参数与解释器，以及独立隔离解释器模块解析和文件 SHA；没有注入运行进程或使用 wrapper，因此不声称直接读取活进程 sys.modules。最终 native 实际 grant15/guard5 是配置生效证据。
