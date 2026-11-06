# Long GI基线发布：停服前中止

## 冻结版本与目标

- 候选提交：c6659488f649（完整SHA见state.json）。
- 代码提交a63037a：固定Long GI、驱动索引默认18及规范字段契约，相关258项通过。
- 集群配置提交ef81dd3：九候选地址和节点槽位/TUN校验。
- 目标严格固定fl00/fl01/fl04/fl05/fl06；不连接用户确认断电的fl02/fl07/fl08/fl09/fl10。
- 源码归档和helper摘要均记录于state.json；source.tar可重建，不纳入Git。

## 实际结果

preflight：fl00、fl01、fl05、fl06通过；fl04 SSH连接超时。按完整目标门槛中止，state.completed仍为空。

有界复核：fl04 ping三次无响应，邻居解析INCOMPLETE，SSH No route to host；服务端fl04记录OFFLINE、心跳年龄约1131秒。不能据此自行认定电池断电或网卡故障，需要现场确认供电及管理网络。

没有执行stage/prepare/stop/install/start/audit任何后续阶段，没有停止任何节点服务，没有混合发布。当前运行仍是已发布c7d99b6，GI仍Short；本轮未宣称Long GI实机验收成功。

## 后续恢复

用户恢复fl02/fl04供电后，该五目标preflight已重新通过，state记录仅完成preflight。因为在线集合增加fl02，本轮改建完整六目标发布批次，本目录不再推进后续阶段；没有在此批次停服或安装。初次失败输出保留在Git历史及fl04-reachability.json中。

## 恢复入口

以下命令是当时五目标批次的恢复入口；当前发布器已显式冻结新六目标，不再用它继续本批次。

```bash
python3 .scratch/heartbeat-restart/release-online.py preflight \
 .scratch/heartbeat-restart/heartbeat-c6659488f649-20261106T154235Z
```

仅当全员通过后依次stage、prepare、stop、install、start、audit；不能一条链无视返回码推进。audit须读回实际native GET_RADIO short_gi=0及iw149/HT40/索引18。发布后再做有界待命观察；当前小集合仍不能代替九客户端正式门槛。
