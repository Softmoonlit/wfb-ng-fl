# 02: 客户端常驻守护进程、网卡挂起轮询自愈与 RoleService 子进程沙箱

**What to build:** 交付 `wfb-fl-client-daemon` 可执行程序与 systemd 服务单元。开机自动读取本地固化身份文件 `/etc/wfb-ng-fl/node.json`（支持 1~10 号节点身份与静态 TUN IP 映射）；提供内部安全网卡轮询机制，USB 网卡拔掉或后插入时不崩溃并自动热接管；支持依据配置设置网卡发射功率（如近距台面 12 dBm）与上行调制（MCS 3~6）；收到任务触发时，通过 `subprocess.Popen` 动态派生独立 `RoleService(role='client')` 执行闭环；任务结束或中止时安全回收子进程、清理临时接收区并回滚至 `IDLE`。

**Blocked by:** None (can start immediately)

**Status:** need-for-review

- [x] 交付 `wfb-fl-client-daemon` 可执行程序与 systemd 服务单元。
- [x] 开机自动读取本地固化身份文件 `/etc/wfb-ng-fl/node.json`，正确获取 `node_id`（支持 1~10 号节点）与 `tun_ip`。
- [x] 实现网卡安全挂起轮询机制：开机若未检测到 `wlx*` USB 无线网卡，进程在内部以 3 秒周期挂起重试不崩溃，一旦检测到网卡插入即自动接管。
- [x] 自动将纳管的 `wlx*` 网卡配置为 Monitor 模式，信道设为默认 Channel 157，应用独立配置的发射功率（如 12 dBm），配置 TUN 网卡与 IP。
- [x] 实现基于 `subprocess.Popen` 的短生命周期算法作业沙箱：收到任务时动态派生独立 `RoleService(role='client')` 执行闭环。
- [x] 任务正常完成或异常中止（如收到中止信号）时，守护进程负责彻底 kill/terminate 子进程并回收资源，清理临时工作区，安全恢复至 `IDLE` 待命。
- [x] 进程与资源审计测试证明无任何孤儿进程残留、TUN 设备安全回收。

## Comments

- 交付 `wfb_ng.fl.client_daemon` 模块与可执行入口 `wfb-fl-client-daemon`（注册在 `setup.py` console_scripts，亦支持 `python3 -m wfb_ng.fl.client_daemon`）。
- 交付 systemd 服务单元文件 `scripts/systemd/wfb-fl-client-daemon.service` 与默认身份模板 `scripts/default/node.json`，并纳入打包与安装清单。
- 实现 `load_node_identity`，严格校验 1~10 号节点与 TUN IP（支持自动派生 `10.80.0.{10+node_id}/24`，fail-closed 拦截非法 IP 与越界 ID）。
- 实现 `NetworkAdapter` / `LinuxNetworkAdapter` 与安全轮询机制：开机 0 网卡时以 3 秒周期挂起轮询不崩溃，检测到网卡插入即自动接管；运行中拔卡安全自愈并回退挂起轮询。
- 自动将纳管的 `wlx*` 网卡配置为 Monitor 模式、锁定 Channel 157（HT40+）、根据 ADR-0014 与驱动协议注入负值 mBm 功率（12 dBm -> -1200），支持 TUN 接口创建与清理。
- 实现 `JobSandbox` 进程沙箱：以独立进程组（`start_new_session=True`）派生 `RoleService(role='client')`，严格符合 `service._read_config` 配置契约；作业完成或异常中止时，强杀进程组（SIGTERM -> SIGKILL）、清理 TUN 与临时工作区，安全回滚至 `IDLE`。
- 编写 24 项全覆盖单元与集成测试，零资源警告，全套 71 项测试 100% 通过。

