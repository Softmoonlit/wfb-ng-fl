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

已统一发布`66f15358d309d646d7dca887624a66c46099a54a`至全部十个目标。发布冻结目录为`.scratch/heartbeat-restart/heartbeat-1cd6264d5d6b-20261108T124420Z/heartbeat-66f15358d309-20261110T114225Z/`；单一native工件保持`44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`，没有重新编译或改变无线参数。

十板独立只读清单确认40个安装源码文件匹配冻结manifest、运行native摘要相同、实际15 W策略限制正确。见`final-source-native-power-inventory.json`。原发布运行审计未通过实际149信道条件：客户端在寻频，服务端未形成九端入网。没有进入文件执行、157换频或模型发送，模型执行数为零。

服务端接收路径出现额外阻断：

1. 首次12秒原始AF_PACKET监控采样中，fl00为零帧；同期fl01捕获144帧，其中98帧为数据帧。采样末两端信道不同，该比较不能据此直接计算链路丢包率，见`post-release-passive.json`。
2. 标准停止fl00后，对明确MAC`fc:a3:86:b3:86:72`、USB接口`1-2.4:1.0`执行一次驱动解绑/重绑。接口正常重新出现；内核的USB reset为这次主动操作产生，未记录为自发故障。短暂收到部分IDLE请求，但未完成持续入网。见`server-rx-rebind.json`。
3. 对九客户端执行一次标准重启，未恢复九READY。随后停止完整目标集，仅重新加载绑定fl00专用网卡的`88XXau_wfb`模块；磁盘与原加载源码版本同为`08BBAE7FE71F25B7F0ED54C`，模块SHA为`1a845230125dab762649f80da85ee53bfd494ebaf1a8eb68d43372477a604a13`。按服务端先、客户端后启动，仍未入网。见`server-module-reload.json`与`module-reload-readiness.json`。
4. 模块重载后的另一12秒原始监控采样仍为零帧，见`post-module-raw-capture.json`。这是接收交付路径异常证据，不证明永久网卡损坏，也不能把采样窗口外缺失报文直接算作物理丢包。

远程受控恢复未完成，停止继续重启和测试。十个参与目标的WFB服务均已标准停止，native PID全部退出，见`controlled-stop.json`；管理网保持可达，fl04未操作。现场需要重插fl00当前专用WFB网卡，保持当前USB口与天线，再从完整十板准入重新验证。软件回归成功不替代这一实机验收。后续阻断另列[问题18](../../issues/18-server-rx-path-after-restart.md)。
