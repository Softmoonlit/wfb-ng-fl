# 02: 客户端常驻守护进程、网卡挂起轮询自愈与 RoleService 子进程沙箱

**What to build:** 交付 `wfb-fl-client-daemon` 可执行程序与 systemd 服务单元。开机自动读取本地固化身份文件 `/etc/wfb-ng-fl/node.json`（支持 1~10 号节点身份与静态 TUN IP 映射）；提供内部安全网卡轮询机制，USB 网卡拔掉或后插入时不崩溃并自动热接管；支持依据配置设置网卡发射功率（如近距台面 12 dBm）与上行调制（MCS 3~6）；收到任务触发时，通过 `subprocess.Popen` 动态派生独立 `RoleService(role='client')` 执行闭环；任务结束或中止时安全回收子进程、清理临时接收区并回滚至 `IDLE`。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 交付 `wfb-fl-client-daemon` 可执行程序与 systemd 服务单元。
- [ ] 开机自动读取本地固化身份文件 `/etc/wfb-ng-fl/node.json`，正确获取 `node_id`（支持 1~10 号节点）与 `tun_ip`。
- [ ] 实现网卡安全挂起轮询机制：开机若未检测到 `wlx*` USB 无线网卡，进程在内部以 3 秒周期挂起重试不崩溃，一旦检测到网卡插入即自动接管。
- [ ] 自动将纳管的 `wlx*` 网卡配置为 Monitor 模式，信道设为默认 Channel 157，应用独立配置的发射功率（如 12 dBm），配置 TUN 网卡与 IP。
- [ ] 实现基于 `subprocess.Popen` 的短生命周期算法作业沙箱：收到任务时动态派生独立 `RoleService(role='client')` 执行闭环。
- [ ] 任务正常完成或异常中止（如收到中止信号）时，守护进程负责彻底 kill/terminate 子进程并回收资源，清理临时工作区，安全恢复至 `IDLE` 待命。
- [ ] 进程与资源审计测试证明无任何孤儿进程残留、TUN 设备安全回收。
