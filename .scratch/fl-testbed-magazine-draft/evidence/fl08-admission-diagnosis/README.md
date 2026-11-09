# fl08 换频后准入失败诊断

## 结论与当前进度

fl08已成功应用157并收到CONFIRMED，约2.234秒后因旧活性窗口到期进入HUNTING。原实现在COMMIT至CONFIRMED间隔离周期心跳，收尾后没有立即恢复真实心跳；原十秒活性锚已在暂停期间老化，随后周期探测未得到及时有效ACK便进入寻频。已通过固定基线原代码复现“确认结束未立即发送心跳”，并完成本地修复。尚未统一发布和实机复验，不能声称解释了全部ACK尾延迟或已解决板端问题。

固定集合为fl00和客户端[1,2,3,5,6,7,8,9,10]，fl04排除。现场诊断只有只读取证；新验收批次另存`../fl08-heartbeat-fix-validation/`。

## 原始现场证据

| fl08节点日志原始时间 | 事件 |
| --- | --- |
| 01:44:42.992 | LEASE_ARM，事务 `switch_69d1a0b37f2647a9a9f8ee7df19fd5f6` |
| 01:44:45.371 | COMMIT已应用157 |
| 01:44:48.635 | CONFIRMED，157 |
| 01:44:50.868 | 十秒未确认有效心跳，累计缺失2次ACK，进入寻频 |

CONFIRMED至告警差值由fl08同一单调时钟计算：`2999.893147 - 2997.659173 = 2.233974秒`，并非跨节点比较monotonic。原通信执行器随后在`verify_radio`记录`target registry gate failed: 8`。当时完整registry快照未落盘，不能回溯断言具体违反的是哪一个字段。

对照fl02在恢复149后也出现同类告警：CONFIRMED至告警约4.464秒。原日志见`fl08-incident-journal.log`、`fl02-incident-journal.log`和`fl00-incident-journal.log`。服务端日志由本机读取，未绕过SSH host-key校验。

fl08故障前后100秒的内核日志没有事件记录，未见USB重置证据。诊断时服务MainPID仍为860，实际`iw`读回149/HT40、驱动索引18，native GET_RADIO读回Long GI、40 MHz和MCS4；九客户端均READY/IDLE，无活动作业。见`fl08-kernel-and-current.json`、`fl08-current-radio.json`和`current-server-status.json`。这些记录不能全面排除间歇性硬件问题，也不替代发布后的重新准入。

fl08安装的`control.py`摘要为`6012d8eea0d1f9e6ccdda975e802e55ef8f8006d99216fd2fb64f6cc00b44b7b`，取证见`fl08-installed-source.json`。

## 最终修复契约

1. 保持COMMIT至事务收尾期间的射频隔离。在`_send_heartbeat()`统一检查`_pre_switch_config`，覆盖周期、状态跃迁和寻频恢复入口，避免监督等待期间发生COMMIT或回退失败后仍按旧判断发送心跳。
2. CONFIRMED成功解除租约、清理pending并恢复状态后，先发`RADIO_SWITCH_CONFIRMED_ACK`，再立即发送一次带当前boot、session、请求和信道身份的真实心跳。心跳等待不延误确认屏障，也不与事务租约刷新争锁。
3. 重复CONFIRMED只重复幂等回执，不重复心跳。只有真实匹配ACK可以更新活性锚；CONFIRMED本身不重置或平移锚。保留230ms单次预算、十秒失活门限和原调度参数。
4. registry检查器在拒绝前记录完整状态快照，原准入predicate不变。

## 可重复验证

最终心跳专项19项通过；心跳身份、无线事务及真实loopback换频扩大检查共101项全部通过，见`heartbeat-confirmed-green.log`、`confirmed-regression.log`。事务收据单测显式隔离新增心跳，真实往返由动态loopback专项覆盖，未向生产UDP或TUN发包。

新回归通过公开PREPARE、COMMIT、NEW_CHANNEL_PING、FINALIZED、CONFIRMED入口和真实动态loopback UDP验证：旧锚100、确认时刻111，真实有效ACK才续到111；错时间、信道、来源或boot均不续期，持续无有效ACK仍按原十秒门限HUNTING。UDP接收顺序验证CONFIRMED_ACK先于NODE_HEARTBEAT。应用成功和失败时，周期及状态迁移入口均不能绕过事务隔离。

固定原代码复现：

```bash
PYTHONPATH=. python3 .scratch/fl-testbed-magazine-draft/evidence/fl08-admission-diagnosis/check-baseline-policy.py -q 'wfb_ng/tests/test_fl_heartbeat_liveness.py::test_confirmed_radio_dispatches_identity_checked_heartbeat[valid]'
```

脚本仅在独立本地测试进程还原固定提交`c07b382f9d014972f6729293dc7770d1fa45f96e`的方法，不改工作区或安装文件。预期返回1，具体断言为“CONFIRMED收尾未立即恢复真实心跳”，见`heartbeat-confirmed-baseline-red.log`。

通信执行器本地59项隔离检查通过。较早扩大检查中的19项作业策略失败已与原监督函数对照，失败集合完全一致，见`test-comparison.json`；旧检查及初稿监督循环方案日志保留，但最终实现以本节101项和确认后心跳回归为准。

额外实机换频前检查：`make -j2 v6_radio_update_test`后真实`run_client`回归通过。部分FEC、授权已过期时，向动态loopback控制端口发送与当前一致的40MHz/MCS4/Long GI `CMD_SET_RADIO`，成功回执后没有无授权数据；新授权到达后仍封闭原块，nonce和FEC顺序正确，零超时poll为0。两轮均通过，见`native-same-radio-command.log`。本次只切149/157，不更改带宽、MCS或GI；该检查不宣称解决另存的跨配置代际无授权flush问题。生产native源码及二进制未改，预期SHA仍为`44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`。
