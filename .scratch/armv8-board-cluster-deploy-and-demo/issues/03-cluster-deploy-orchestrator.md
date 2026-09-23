# 03: 集群一键批量部署与环境就绪验证编排器

**What to build:** 交付在 Server 端运行的一键集群批量部署工具（`scripts/deploy_cluster.sh`）。读取 `cluster_nodes.conf`，通过 SSH 并行/串行向所有在线 Client 分发当前代码库，远程触发各 Client 执行 `deploy_node.sh`，并在各板子上检验关键依赖与网卡状态，最后生成全集群就绪报告。

**Reference & Prior Art:** 参考项目已有真实多机编排基线脚本（`tests/real_hardware/issue41_fl_runtime_loop.sh`）中 `cmd_sync_remotes` 与 `cmd_install` 的多机远程 SSH 编排逻辑，但坚决剔除其对 Git 分支与工作区洁净度的强制锁死断言，允许现场灵活同步代码与配置。

**Blocked by:** 01, 02

**Status:** resolved

- [x] 编写 `scripts/deploy_cluster.sh`，读取 `cluster_nodes.conf` 中的 Client 列表。
- [x] 探测 Client 在线状态，遇到离线节点给出明显黄色告警并安全跳过。
- [x] 通过 SSH/rsync 将当前项目代码及 UFTP 源码包推送到各 Client 对应路径。
- [x] 远程触发各 Client 执行 `sudo ./scripts/deploy_node.sh` 并捕获执行日志。
- [x] 执行全集群安装后就绪核验：检查 `command -v wfb-fl-server wfb-fl-client wfb_v6_uplink uftp uftpd`、检查无线网卡驱动加载状态及唯一 `wlx*` 接口存在性。
- [x] 终端以表格形式输出所有节点的部署成功状态与软硬件环境汇总。

## Comments

### Code Review 与整改记录

通过双轴（Standards 与 Spec）并行子代理评审并完成全面整改：
1. **Standards 轴整改**：
   - 移除不符合项目原则（严禁向后兼容与回退适配）的 tar/scp 回退层，坚决强制统一使用原生 SSH/rsync；
   - 清理多余的双重路径同步，UFTP 源码包统一推送到远端仓库同级目录，供 `deploy_node.sh` 精确命中；
   - 提取 `sync_single_node` 与 `deploy_single_node` 复用逻辑，消除并行与串行分支重复代码；
   - 统一 Server 本机与 Client 远端就绪检测逻辑，共用确定性检测脚本 `VERIFY_SCRIPT` 与解析函数 `parse_and_judge_readiness`。
2. **Spec 轴整改**：
   - 实现代码库与资源在 `--parallel` 模式下并发向所有在线 Client 同步分发；
   - 强化就绪核验退出码判定，若在线 Client 存在硬性故障（关键命令缺失、驱动未加载、网卡冲突）或 SSH 通信故障，严格返回非 0 退出码；
   - 修复 SSH 传输中断时错误默认 5/5 OK 的漏洞，如实报告 `CHECK_FAIL`；
   - 增加 `--check-server` 选项，支持按需将 Server 本机纳入阻断门禁。
3. **测试验证**：
   - `tests/test_deploy_cluster.py` 覆盖 11 项针对性测试，涵盖演练、离线黄色告警跳过、全离线失败、并行与串行部署、日志捕获、命令缺失拦截、传输异常捕获与 check-only 模式，全部执行通过。

