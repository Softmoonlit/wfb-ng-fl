# 05: 验证三角色服务 restart 与资源生命周期

**What to build:** 在三个角色服务成功完成一轮作业并停止后，独立验证 restart/no-overlap 和资源清理。重新启动不得复用旧作业状态或与旧进程重叠；再次停止后，角色服务、cgroup、TUN、链路子进程和 UFTP 子进程必须全部清理。

**Blocked by:** 04 通过安装后的三角色服务完成正式一轮 Runtime

**Status:** resolved

- [x] 首次正常停止后，三个 unit 均为 inactive，相关 cgroup 中没有进程，三个 TUN 均已消失。
- [x] 首次正常停止后，不存在本次运行所属的角色进程、链路进程、`uftp` 或 `uftpd` 孤儿进程。
- [x] restart 使用新的角色服务生命周期，不复用未完成轮次，不与旧角色或子进程重叠。
- [x] restart 后服务能够达到可运行状态，并能再次受控停止。
- [x] 第二次停止后再次满足 unit、cgroup、TUN 和全部相关进程清理条件。
- [x] lifecycle 拥有独立状态和原始证据，不从正式 Runtime 状态复制或推导。
- [x] 任一残留资源、进程重叠、旧状态复用或证据缺失都会生成 failed lifecycle 结论。
- [x] lifecycle 摘要、验证规则及正常与失败路径自动化测试随本行为交付。

## 验证结论

1. **生命周期审计核心模块实现 (`tests/real_hardware/issue41_lifecycle.py`)**：
   - 实现了三阶段独立审计流程：
     - `first_stop`：受控停止后，核验 server、client1、client2 的 unit 为 inactive、cgroup 无残留进程 (`TasksCurrent=0` 且 `cgroup.procs` 为空)、TUN 设备已消失、无 `wfb-fl-server`/`wfb-fl-client`/`wfb_v6_uplink`/`uftp`/`uftpd` 孤儿进程。
     - `restart`：重置清理旧工作区防止复用未完成轮次；重新启动各角色服务；等待 unit 进入 active 且 TUN 处于 UP 状态；比对新旧 MainPID 确保 `pid_reused == False` 且旧 PID 已销毁退出，实现强 no-overlap 验证。
     - `second_stop`：再次执行受控停止，对三个节点的 unit、cgroup、TUN 和全部孤儿进程执行第二次严格清理审计。
   - 输出结构化、独立保存的 `$ARCHIVE_DIR/lifecycle/lifecycle_summary.json` 以及各阶段原始文本证据 (`first_stop_<role>.txt`, `restart_<role>.txt`, `second_stop_<role>.txt`) 和 JSON 证据 (`first_stop_evidence.json`, `restart_evidence.json`, `second_stop_evidence.json`)，生命周期状态拥有完全独立的判定来源，拒绝从 Runtime 状态推导。

2. **归档验证器规则升级 (`tests/real_hardware/issue41_validate_archive.py`)**：
   - 将原先仅检查 status 字段的形式化校验，升级为委托 `validate_lifecycle_summary` 进行强语义判定：
     - 当 `lifecycle.status == 'passed'` 时，严格断言 `first_stop`、`restart`、`second_stop` 三个阶段的角色细粒度事实；
     - 校验磁盘上 `evidence_files` 列表中的原始证据文件真实存在且非空；
     - 任一残留资源、PID 重叠或证据缺失均报错阻断，且确保 `conclusion.status` 在 lifecycle 失败时必定为 `failed`。

3. **运行脚本与包络集成 (`tests/real_hardware/issue41_fl_runtime_loop.sh`)**：
   - 重构 `cmd_lifecycle_stop_restart`：调用 `issue41_lifecycle.py run` 针对 server 本机及 client1/client2 远端执行带超时的三阶段生命周期审计，并将生成的独立摘要追加至包络分区；
   - 在 `cmd_summary` 中，移除对 formal runtime status 的复用，严格从 `lifecycle/lifecycle_summary.json` 读取独立结果参与顶层 conclusion 判定。

4. **自动化测试覆盖与验证记录**：
   - `tests/real_hardware/test_issue41_lifecycle.py`：新增 17 个单元测试，覆盖正常三阶段通过、cgroup 脏残留、TUN 残留、孤儿进程残留、PID 重叠、旧 PID 存活、TUN 未 UP、证据文件缺失等场景（Ran 17 tests in 4.0s, OK）。
   - `tests/real_hardware/test_issue41_validate_archive.py`：新增针对 lifecycle 各阶段及失败路径的 9 个测试用例，总计 31 个测试（Ran 31 tests in 0.06s, OK）。
   - `tests/real_hardware/test_issue41_smoke_script.py`：验证脚本对独立 lifecycle 审计器及 summary 的调用契约（Ran 23 tests, OK）。
   - `tests/real_hardware` 模块全量单测：Ran 134 tests in 5.8s, OK。
   - `make test_v8` FL 协议与运行时测试套件：Ran 129 tests in 18.1s, OK。
   - `sudo make test_v8_systemd` 真实 systemd 角色生命周期测试：Ran 2 tests in 9.2s, OK。
