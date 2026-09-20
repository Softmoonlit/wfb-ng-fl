# 05: 验证三角色服务 restart 与资源生命周期

**What to build:** 在三个角色服务成功完成一轮作业并停止后，独立验证 restart/no-overlap 和资源清理。重新启动不得复用旧作业状态或与旧进程重叠；再次停止后，角色服务、cgroup、TUN、链路子进程和 UFTP 子进程必须全部清理。

**Blocked by:** 04 通过安装后的三角色服务完成正式一轮 Runtime

**Status:** ready-for-agent

- [ ] 首次正常停止后，三个 unit 均为 inactive，相关 cgroup 中没有进程，三个 TUN 均已消失。
- [ ] 首次正常停止后，不存在本次运行所属的角色进程、链路进程、`uftp` 或 `uftpd` 孤儿进程。
- [ ] restart 使用新的角色服务生命周期，不复用未完成轮次，不与旧角色或子进程重叠。
- [ ] restart 后服务能够达到可运行状态，并能再次受控停止。
- [ ] 第二次停止后再次满足 unit、cgroup、TUN 和全部相关进程清理条件。
- [ ] lifecycle 拥有独立状态和原始证据，不从正式 Runtime 状态复制或推导。
- [ ] 任一残留资源、进程重叠、旧状态复用或证据缺失都会生成 failed lifecycle 结论。
- [ ] lifecycle 摘要、验证规则及正常与失败路径自动化测试随本行为交付。
