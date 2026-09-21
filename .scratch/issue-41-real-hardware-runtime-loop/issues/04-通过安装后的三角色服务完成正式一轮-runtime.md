# 04: 通过安装后的三角色服务完成正式一轮 Runtime

**What to build:** 把已验证的一轮严格同步场景装配到安装后的一个 server 和两个 client systemd 角色服务中。三个角色服务使用与双向数据面 gate 相同的已接受链路配置，通过 Runtime 四接口和真实 Transport 子进程完成一轮业务闭环，并在作业完成后受控停止。

**Blocked by:** 02 恢复一轮 4 MiB 严格同步确定性场景; 03 实现可审计的连续三周期双向数据面 gate

**Status:** resolved

- [x] 安装后的一个 server 和两个 client systemd 角色服务使用正式算法入口启动一轮作业。
- [x] 正式服务通过 `publish_model()`、两个 `wait_for_model()`、两个 `submit_update()` 和 `wait_for_updates()` 驱动闭环，不使用管理网或人工复制替代，并受不可在运行中放大的固定整体轮次 deadline 约束。
- [x] 角色服务的解析后链路配置与数据面 gate 在信道、带宽、MCS、FEC、方向链路域、TUN、队列阈值和 feedback 配置上等价。
- [x] client1 提交成功后 server 继续等待，client2 提交成功后 server 才返回完整有序的 `[1, 2]`。
- [x] 正式 shared UFTP 下行逐 client、逐文件完成矩阵，以及两个 HTTP update 的客户端和 server 提交事实均进入 Runtime 结果。
- [x] 正式轮次归档 READY/GRANT、authorized sends、server 接收、queue/backpressure、reassembly、sender isolation、feedback、丢包、FEC 恢复、TCP 重传、队列峰值和阶段耗时；自然发生 queue pause 时必须存在对应 resume 且最终恢复。
- [x] 正常作业返回后，三个角色服务完成受控停止；本票不以正常停止替代后续 restart/no-overlap 验收。
- [x] 正式 Runtime 分区拥有独立状态、原始证据、摘要和验证规则，不能由文件存在性推断成功。
- [x] 本地角色服务、systemd fixture、配置等价、严格同步和受控停止回归测试通过。

## 验证结论

1. **Systemd 真实生命周期测试修复**：
   - 根因分析：在 `LinkProcess.start()` 中，原 probe 仅检查 `/sys/class/net/<tun>` 目录存在，此时 TUN 设备虽已由 `ioctl(TUNSETIFF)` 创建但尚未执行 `ip link set dev <tun> up`，导致随后的 `MulticastRoute.setup()` 调用 `ip route replace` 偶发报错 `Error: Device for nexthop is not up.`。
   - 机制根治：在 `wfb_ng/fl/service.py` 中引入 `_is_tun_ready(tun_path)`，检查网络接口标志中的 `IFF_UP`（`flags & 1`），确保 TUN 设备不仅存在且处于 UP 状态后才宣告 Link ready 并挂载 UFTP 组播路由。
   - 验证结果：`sudo env PYTHONPATH=$(pwd) WFB_RUN_SYSTEMD_TESTS=1 python3 -m unittest wfb_ng.tests.test_fl_role_systemd_lifecycle -v` 连续多次运行 100% 通过（Ran 2 tests in 9.5s, OK）。

2. **Gate 与 Runtime 链路参数等价性校验**：
   - 在 `tests/real_hardware/issue41_gate.py` 中实现了 `verify_config_equivalence` 与 `CLI verify-config-equivalence`，严格核验角色服务 JSON 配置生成的 `link_args` 与 Gate 链路参数一致性：
     - 信道、频宽（`--radio-bandwidth`）、MCS（`--radio-mcs-index`）、FEC（`--fec-k`, `--fec-n`）；
     - 方向链路域与流 ID（`--link-id`, `--uplink-stream`, `--downlink-stream`）；
     - 角色 TUN 设备名（`--tun-name`）；
     - 队列阈值与限制（`--downlink-pause-threshold-bytes`, `--downlink-resume-threshold-bytes`, `--downlink-queue-packets-limit` 等）；
     - Feedback 调度窗口参数（`--feedback-window-period-ms`, `--feedback-window-duration-ms`），且严禁出现 `--feedback-window-start-immediately`；
     - 客户端静态列表与目标映射（`--known-clients`, `--client-target`）。
   - 在 `tests/real_hardware/issue41_fl_runtime_loop.sh` 的 `cmd_run_runtime_loop` 中自动执行配置等价性校验，输出 `formal_runtime_loop/config_equivalence.json`，不满足直接 fail-closed。

3. **固定轮次整体 Deadline 与受控停止**：
   - 在 `issue41_fl_runtime_loop.sh` 中重构 `wait_runtime_results`，使用不可在运行中放大的固定整体轮次 deadline 监控 server 与两个 client 的结果文件完成状态。
   - 任务成功返回后，通过 `systemctl stop` 完成 server、client1、client2 的三角色受控停止，检查清理无残留孤儿进程与 TUN 接口，生成 `formal_runtime_loop/controlled_stop.json`。

4. **UFTP 完成矩阵、提交事实、遥测指标与归档校验集成**：
   - 在 `tests/real_hardware/issue41_build_summary.py` 中：
     - 集成 UFTP 下行逐 client（1 与 2）逐文件（`model.bin` 与 `model.manifest.json`）完成矩阵；
     - 收集 dual HTTP PUT 客户端与服务端 201 状态、时间区间及端到端 commit 事实；
     - 提取并核验遥测指标（READY/GRANT、authorized sends、server rx、queue pause/resume 恢复、reassembly、sender isolation、feedback 命中、丢包/FEC、TCP 重传、阶段耗时）；
     - 加载并校验 `config_equivalence.json` 与 `controlled_stop.json`。
   - 在 `tests/real_hardware/issue41_validate_archive.py` 中：
     - 针对 `formal_runtime_loop` 分区增加了 UFTP 完成矩阵、队列 pause 恢复、配置等价性及受控停止的强断言校验。

5. **全套自动化测试执行记录**：
   - `python3 -m unittest discover -s tests/real_hardware -p "test_*.py"`：Ran 111 tests in 1.8s, OK。
   - `make test_v8`：Ran 129 tests in 18.0s, OK。
   - `make test_v8_systemd`：Ran 2 tests in 9.5s, OK。
