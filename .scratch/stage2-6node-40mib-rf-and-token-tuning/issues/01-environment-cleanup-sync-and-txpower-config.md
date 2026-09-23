# 01: 环境清理、全集群代码一致性核验与发射功率配置支持

**What to build:**
提供干净、可靠的实验前置环境。将全集群各节点后台残留进程彻底清理，所有测试无线网卡复位至 `managed/DOWN`；逐台核验 8 台机器（Server 及 Client 1~7）工作区干净并同步至最新提交（包含单个客户端独立 MCS 覆盖支持），重新编译底层链路底座并严格比对可执行文件二进制哈希；支持通过环境变量或配置显式指定发射功率（当前台面超近距推荐 12 dBm），并将其纳入运行包络与 Gate/Runtime 等价审计逻辑，确保 preflight 预检 100% 成功。

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] 全集群（Server 及 Client 1~7）无线网卡接口彻底复位至 `managed/DOWN` 状态，清理无残留后台进程
- [x] 逐台机器核验 git 状态，确认处于相同 HEAD 提交且工作区完全干净
- [x] 全集群统一重新编译 C++ 链路底座 `wfb_v6_uplink`，比对各端二进制 SHA-256 哈希确保绝对一致
- [x] 支持通过用户配置或环境变量指定发射功率（如 `ISSUE41_RADIO_TXPOWER_DBM=12`），将其写入 `envelope.json`
- [x] 扩展 Gate 与归档等价审计校验器，对发射功率配置实施强一致性等价校验
- [x] 执行全集群 `preflight` 预检并通过全量检查
