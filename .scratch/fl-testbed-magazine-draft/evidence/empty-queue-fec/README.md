# 空队列 partial FEC 授权恢复：RED / GREEN 证据

本次仅本地测试、构建和提交，未部署、未操作远端节点。生产修改仅 `src/v6_uplink.cpp`；测试复用既有真实 run_client、UDP 和 TUN socketpair 边界。

## RED

原生产代码下，`make v6_radio_update_test` 构建成功，连续五次运行都在同一 READY 断言失败。原始输出见 `empty-fec-red-0.log` 至 `empty-fec-red-4.log`。

每次实际观察到了 1ms GRANT 发出的唯一 TUN DATA，再开始观察授权到期；无新 TUN 输入，150ms 内没有 READY，也没有越权 DATA。手工补第二个 GRANT 后，旧 block 的 filler 和 parity 在 0–1ms 内输出。零超时轮询计数均为 0，CPU 为 1830–2070 微秒。失败不是 payload 未发出的前置竞态，而是空队列 partial FEC 缺少授权申请。

RED 版本日志间隔为 20ms。另行运行原有全部回归通过，见 `empty-fec-baseline.log`。

## GREEN

最终测试改用 1000ms 日志间隔，完整执行两轮生命周期。每轮仅一个 TUN payload，经 1ms GRANT 发出后，在无新 TUN 输入的条件下要求 150ms 内 READY；等待期间不允许 DATA。随后 100ms GRANT 内验证旧 block 的 filler/parity nonce 和 FEC_ONLY 标记，跨过授权到期检查空队列静默。第二轮起始 READY 同时验证上一轮 seal 已清除 declaration_in_flight。最后等待日志样本，检查零超时计数和 CPU 的非零上、下界。

最终 `make test_v6_radio_update` 通过，包括全部原有回归。两轮 READY 均为 3ms，zero_timeout_polls=0，CPU=2046 微秒，获取两个日志样本。完整构建及测试输出见 `empty-fec-green-final.log`。`make build_v6` 成功，见 `empty-fec-green-native-build.log`。`git diff --check` 通过。

修复把 partial FEC 纳入 READY 重试工作；未来 FEC deadline 可独立于日志唤醒；已到期且无授权的 FEC 不再作为零超时唤醒源；空队列到期时申请授权，授权内封块实际成功后清除声明状态。没有修改协议、优先级、heartbeat 超时或 TX 授权条件。

## 复核

实现完成后，单独重新检查生产差异、flush 返回值、READY 状态和授权检查路径；发现应按实际封块成功清除声明，已改成检查 flush_data_block_if_due 的返回值，随后重跑完整目标并构建 native。当前工具没有独立 reviewer / 子代理调用能力，此处是同一代理的单独复核轮次，不能视作第二人的独立审查。

本次验证覆盖发送端旧 block seal 和 nonce 顺序，未新增接收端 ordered delivery 端到端测试；未做远端或真实无线链路验证。
