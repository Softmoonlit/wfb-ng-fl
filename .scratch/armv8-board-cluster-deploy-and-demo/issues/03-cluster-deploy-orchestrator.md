# 03: 集群一键批量部署与环境就绪验证编排器

**What to build:** 交付在 Server 端运行的一键集群批量部署工具（`scripts/deploy_cluster.sh`）。读取 `cluster_nodes.conf`，通过 SSH 并行/串行向所有在线 Client 分发当前代码库，远程触发各 Client 执行 `deploy_node.sh`，并在各板子上检验关键依赖与网卡状态，最后生成全集群就绪报告。

**Reference & Prior Art:** 参考项目已有真实多机编排基线脚本（`tests/real_hardware/issue41_fl_runtime_loop.sh`）中 `cmd_sync_remotes` 与 `cmd_install` 的多机远程 SSH 编排逻辑，但坚决剔除其对 Git 分支与工作区洁净度的强制锁死断言，允许现场灵活同步代码与配置。

**Blocked by:** 01, 02

**Status:** need-for-review

- [x] 编写 `scripts/deploy_cluster.sh`，读取 `cluster_nodes.conf` 中的 Client 列表。
- [x] 探测 Client 在线状态，遇到离线节点给出明显黄色告警并安全跳过。
- [x] 通过 SSH/rsync 将当前项目代码及 UFTP 源码包推送到各 Client 对应路径。
- [x] 远程触发各 Client 执行 `sudo ./scripts/deploy_node.sh` 并捕获执行日志。
- [x] 执行全集群安装后就绪核验：检查 `command -v wfb-fl-server wfb-fl-client wfb_v6_uplink uftp uftpd`、检查无线网卡驱动加载状态及唯一 `wlx*` 接口存在性。
- [x] 终端以表格形式输出所有节点的部署成功状态与软硬件环境汇总。
