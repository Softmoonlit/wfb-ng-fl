# 换频最终确认与真实心跳完成边界

本记录对应[问题17](../../issues/17-radio-final-confirmation-ready-barrier.md)。固定拓扑为fl00与九客户端`[1,2,3,5,6,7,8,9,10]`，排除fl04。现场基线为cccac23、RS(8,12)、30/5、149/HT40/Long GI、驱动功率索引18及板级15 W。230 ms单次回执等待和10秒失活阈值均保持不变。

## 已确认的问题

此前157换频返回`finalized`后，准入立即读取到fl02为`HUNTING/CONNECTING`、信道157、最近心跳约0.01秒。该次换频编排约18.70秒。旧客户端先发最终确认ACK，再执行真实心跳，因此事务成功并不能证明当前信道的真实心跳已获有效确认。若真实确认失败，事务前的活性锚已经耗尽，会进入寻频。现场尚无该次心跳回执失败的包级证据；本次确定的是可复现的软件完成顺序缺陷，不据此判定NIC或USB损坏。

## 修复

仅最终确认处理可以在事务隔离期间发送一次真实当前状态心跳。必须收到严格匹配来源、boot、session、timestamp、channel的有效回执，且原租约仍有效，才保存幂等收据、解除租约、清除pending及发送最终确认ACK。失败保留旧配置和pending，允许原期限内重复确认重新探测，或由原租约触发恢复。成功后的重复确认只补发收据。

底层完成边界适用于真实当前状态；作业持有的RUNNING/PREPARING保持原状态，不强制成为IDLE。普通周期、状态跃迁等心跳入口仍受射频事务门禁约束。没有新增固定等待、扩大期限、放宽准入或改写注册表。

## 本地验证

- 使用动态loopback UDP和传输事件门控，在真实目标心跳未到达时观察公开事务返回及HUNTING/CONNECTING快照，放行后验证真实来源端口、boot及目标信道。
- 父代理在独立临时目录归档HEAD旧源码，复制最终回归文件，连续3次运行`python3 -m pytest -q wfb_ng/tests/test_fl_radio_switch.py -k gated_current_channel_heartbeat`，均为`1 failed, 30 deselected`。见`baseline-red.json`和三个`baseline-red-*.log`。
- 修复后的相关完整命令：`python3 -m pytest -q wfb_ng/tests/test_fl_heartbeat_liveness.py wfb_ng/tests/test_fl_radio_transactions.py wfb_ng/tests/test_fl_radio_switch.py wfb_ng/tests/test_fl_heartbeat_ack_identity.py wfb_ng/tests/test_fl_heartbeat_restart.py`；结果125通过，耗时27.34秒。
- 覆盖错身份、错信道、旧时间戳、错来源、回执丢失、重复确认与作业持有状态；独立只读审查无阻塞项。
- 仅本地动态端口，没有访问生产UDP端点或管理服务。

## 实机验证

待统一冻结发布后追加：十板源码及运行native摘要、实际射频/FEC/15 W审计，九端小文件闭环与一次模型等量闭环。每个新任务使用唯一run_id；每方向600秒，不自动重试。
