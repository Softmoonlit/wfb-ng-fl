# 换频最终确认早于节点就绪门禁

Status: resolved
Type: task

## Scope

排查换频事务 API `finalized` 返回后，`verify_radio` 立即读取 registry 时目标节点仍为 `HUNTING/CONNECTING` 的协议时间线。测试边界使用 `ControlPlaneClient` 真实动态 loopback UDP 心跳/换频入口与现有公开 radio transaction 编排接口；不得使用生产 UDP 端口，不修改 registry READY/IDLE 门槛，不以 sleep 或扩大 timeout 掩盖问题。

## Current Finding

旧版 `ControlPlaneServer.reconfigure_radio()` 在 `RADIO_SWITCH_CONFIRMED_ACK` 屏障完成后立即返回；旧版客户端先发送该 ACK，再发送真实心跳。可控动态 UDP 回归已连续三次证明：目标节点仍为 `HUNTING/CONNECTING` 时，事务可以提前返回成功。现场事务约18.70秒，超过10秒活性预算；收尾后的真实心跳未获有效确认时，旧活性锚耗尽会使客户端立即寻频。尚无该次回执丢失的包级证据，因此不把丢包机制或硬件状态写成已确定根因。

修复将最终确认绑定到目标信道、真实当前状态的有效心跳回执。失败保留 pending、旧配置与原租约；成功且租约仍有效后才保存幂等收据、清 pending、发送最终确认。底层不强制所有作业状态为 IDLE；作业持有的 RUNNING/PREPARING 仍按真实状态报告。

## Ranked Falsifiable Hypotheses

1. 最可能：最终 ACK 先于真实 IDLE 心跳，server 返回后读取到此前的 HUNTING/CONNECTING 快照；若将最终 ACK 放到真实心跳有效回执之后，动态 loopback 应在 transaction 返回时直接观察到 READY/IDLE。
2. 客户端真实心跳在确认处理时发送失败或被身份/信道校验拒绝；若如此，最终 ACK 必须不发送，transaction 应在原确认截止时间内失败，而不是伪造 finalized。
3. 事务 finalized 只证明射频配置屏障，不证明节点就绪屏障；即使心跳发送顺序修正，必须验证持续失活、错误身份/错误信道、错误码仍使公开 gate 拒绝。
4. 旧 HUNTING 心跳可能在 IDLE 心跳之后覆盖 registry；同一动态 loopback socket 的序列和 server registry 时间线应能排除该假说。
5. `verify_radio` 可能读取陈旧或错误 registry 视图；使用真实 `ControlPlaneServer.registry` 与公开 status 边界的同一回环证据验证，若修正协议前后顺序不变则保留该假说。

## Acceptance

- 回归测试先在当前实现下红，复现 transaction 返回时节点不是 READY/IDLE。
- 最小修复只改变最终确认协议的完成边界：真实当前信道心跳得到有效 ACK 后才发最终确认 ACK；失败则不确认。
- 保持 230ms 单次心跳预算、10s 活性锚、READY/IDLE registry 门槛和原 transaction deadline；不新增固定 sleep、轮询 gate、fallback 或兼容旧协议。
- 动态 loopback 及既有控制/换频回归通过；错误身份、错误信道、持续丢包仍 fail closed。

## Comments

- 2026-11-10：最终公开 API 回归在冻结旧源码下连续3次失败；修复后125项心跳、重启身份、换频及事务回归通过。独立只读审查无阻塞项；代码修复与审查完成，实机发布和验证结果待下方追加。证据见 `evidence/diagnosis-radio-confirmed-ready-20261110T111512Z/`。
- 2026-11-10T11:15:12Z：建立诊断工作项。现场证据 `fresh-30-d5-recovered-20261110T103150Z` 显示 fl02 在 transaction finalized 后 `heartbeat_ago=0.01` 但仍 HUNTING/CONNECTING，尚不能仅凭快照断言持续失活；先做公共协议时间线回归。
