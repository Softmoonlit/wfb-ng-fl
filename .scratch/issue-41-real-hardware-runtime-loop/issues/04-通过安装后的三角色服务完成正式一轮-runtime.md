# 04: 通过安装后的三角色服务完成正式一轮 Runtime

**What to build:** 把已验证的一轮严格同步场景装配到安装后的一个 server 和两个 client systemd 角色服务中。三个角色服务使用与双向数据面 gate 相同的已接受链路配置，通过 Runtime 四接口和真实 Transport 子进程完成一轮业务闭环，并在作业完成后受控停止。

**Blocked by:** 02 恢复一轮 4 MiB 严格同步确定性场景; 03 实现可审计的连续三周期双向数据面 gate

**Status:** ready-for-agent

- [ ] 安装后的一个 server 和两个 client systemd 角色服务使用正式算法入口启动一轮作业。
- [ ] 正式服务通过 `publish_model()`、两个 `wait_for_model()`、两个 `submit_update()` 和 `wait_for_updates()` 驱动闭环，不使用管理网或人工复制替代，并受不可在运行中放大的固定整体轮次 deadline 约束。
- [ ] 角色服务的解析后链路配置与数据面 gate 在信道、带宽、MCS、FEC、方向链路域、TUN、队列阈值和 feedback 配置上等价。
- [ ] client1 提交成功后 server 继续等待，client2 提交成功后 server 才返回完整有序的 `[1, 2]`。
- [ ] 正式 shared UFTP 下行逐 client、逐文件完成矩阵，以及两个 HTTP update 的客户端和 server 提交事实均进入 Runtime 结果。
- [ ] 正式轮次归档 READY/GRANT、authorized sends、server 接收、queue/backpressure、reassembly、sender isolation、feedback、丢包、FEC 恢复、TCP 重传、队列峰值和阶段耗时；自然发生 queue pause 时必须存在对应 resume 且最终恢复。
- [ ] 正常作业返回后，三个角色服务完成受控停止；本票不以正常停止替代后续 restart/no-overlap 验收。
- [ ] 正式 Runtime 分区拥有独立状态、原始证据、摘要和验证规则，不能由文件存在性推断成功。
- [ ] 本地角色服务、systemd fixture、配置等价、严格同步和受控停止回归测试通过。
