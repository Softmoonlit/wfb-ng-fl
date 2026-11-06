# 在线六节点发布与fl01/fl05验证

本轮按用户明确调整的目标集合执行：fl00、fl01、fl02、fl04、fl05、fl06。fl07/fl08/fl09/fl10断电排除，不将其离线计作无线故障。

## 发布身份

- Python冻结源码：c7d99b691f1cebae0af9ea5f695fab95351b51c1。
- 源码归档SHA256：a4b82b746f2ae123b52e4dda19a5b413aeb455c4ad83af250ef53c6a76bae7b6。
- native沿用d898d21构建，运行摘要：ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56；native源码相对此提交无变更。
- control.py实际安装摘要：9db50568ebeccc3e8c8e0417e485611c01acb090c0fe1c4e354732be08b15864。
- 节点端完整Python manifest、启动时加载模块路径和摘要、daemon PID、native /proc/exe摘要见audit-*.json。
- 六目标发布前核验、停服、安装、启动与运行审计全部通过。旧配置/安装/native保存在各板`/srv/wfb-ng-fl/releases/heartbeat-c7d99b691f1c-20261106T131409Z/backup/`。
- 新启动入口为同目录daemon.py，drop-in为zzzzz-heartbeat-release.conf；启动时校验manifest并保存实际已加载模块身份。

无线参数六节点统一为149/HT40、下行MCS3/上行MCS4、FEC8/14、下行注入15000kbps、驱动功率索引18。索引18写入本次启动参数，重启不再恢复14；iw实际读回校验通过。沿用提交版本Short GI，未混入未提交Long GI/功率范围/前端变更。板级维持15W。

### 发布中的异常

fl00暂存源目录出现零长度文件，安装后校验未通过即停止，未启动部分混装集合。重新从上列正确摘要的冻结归档展开源码，重新保存仍为5443ca的旧安装备份后，六目标重新执行安装与全文件摘要校验均通过，才统一启动。暂存损坏原因未确定；不能把空文件生成的安装退出0当作成功。早期stage/prepare空输出记录保留，不用其代替最终audit证明。

## 实机结果

服务端在以下客户端重启及断电恢复测试中始终维持PID4270，未清注册表。所有RTT由客户端本机单调时间计算；请求/确认ACK通过boot_id、session、timestamp配对。40秒抓包窗口未匹配ACK不自动等于最终丢包，可能落在窗口外；超过230ms的确认也不等于客户端认领成功。

| 阶段 | 五客户端状态采样 | fl01请求/确认/230ms内 | fl05请求/确认/230ms内 |
|---|---|---|---|
| 发布后基线2分钟 | 各24/24 READY | 18/17/16 | 18/17/15 |
| fl01/fl05服务重启后2分钟 | 各24/24 READY | 18/18/18 | 19/18/18 |
| fl01/fl05主机重启后2分钟 | fl01先9 READY后15 OFFLINE，其余各24 READY | 本机抓包SSH中断，不计算RTT | 18/18/13 |
| 用户恢复fl01供电后2分钟 | 各24/24 READY | 19/19/16 | 18/17/15 |

服务重启后两台约4.4秒恢复READY，内核boot ID保持不变、daemon PID及心跳boot_id变化。受控主机重启后约67秒恢复READY，两台内核boot ID都变化；fl01线上序列由约5405005降为136929、fl05由5697429降为139916，服务端仍正常接纳并更新节点。

随后fl01管理网与无线双失联，用户明确确认电池断电并恢复供电。这段保留为电源中断，不用于指认无线故障，也不删除中断样本。fl01再次开机内核boot ID变为e110eb8b-fd46-425b-9364-33267a06113d，心跳boot_id为9405c29453394d2a80c21978bf11b28a，服务端仍PID4270，自动恢复READY；补测完整24样本均READY。

最终fl01 RTT中位191.74ms、最大1363.69ms；fl05中位189.46ms、最大321.58ms。fl05主机重启后上一窗口最大1221.47ms。因此“重启后新心跳永久被旧序列拒绝”的问题已在本轮两目标得到实机通过证据，但偶发尾延迟未消除，不宣称无线长期稳定或更换网卡已被证明必要。

fl01/fl05最后功耗和射频读回见fl01-final-power-radio.txt、fl05-final-power-radio.txt：功耗服务success/exit0，CPU上限1497600kHz、GPU612000000Hz、EMC通过`/sys/kernel/nvpmodel_clk_cap/emc`读回2133000000Hz；149/40MHz/索引18。after-reboot-power.json里较早读取BPMP max_rate=3199MHz是硬件时钟范围，不是nvpmodel有效EMC cap，不能据此判定15W失效。

## 收尾状态变化

最后2分钟窗口五客户端均24/24 READY；之后收尾快照fl02转OFFLINE，心跳年龄约202秒，管理SSH超时且ping无响应，原因待确认（未直接认定断电）。fl01/fl04/fl05/fl06仍READY。证据fl02-final-reachability.json及final-status.json。本报告的2分钟通过结论仅针对采样窗口，不能推断收尾全体稳定。

## 边界

完成本轮指定在线集合的发布和fl01/fl05重启恢复验证。九客户端10分钟待命、两次全员纯通信闭环、正式FL训练尚未验收；本轮2分钟窗口不替代上述门槛。断电四板重返前必须安装相同新协议版本，不保留旧心跳fallback。
