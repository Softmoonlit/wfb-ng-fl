# 01：统一功率索引与固定 Long GI 基线

Status: resolved
Type: task

## 规格

用户最终确认网卡使用驱动索引18；25W/40W是Jetson板级功耗，不能混同。svpcom/rtl8812au v5.2.20固定索引1..63，索引18对应`iw dev <专用接口> set txpower fixed -1800`，未经标定不能称为物理18dBm。

GI固定Long GI，不增加配置入口；贯穿常驻链路、作业角色、在线重配和控制台投影。默认值、安装模板与实验诊断配置统一索引18，界面/日志显示驱动索引。本轮沿用现有字段名radio_txpower_dbm，不新增旧字段适配或fallback，不开展全仓协议重命名。

集群候选为fl01/fl02/fl04/fl05/fl06/fl07/fl08/fl09/fl10，固定node_id与10.80.0.(10+id)映射。诊断信道149/HT40+，正式目标157须后续单独验收。

## 公开验证接缝

- RadioConfig/patch/calibration：索引1、18、63合法，0/64及错误类型拒绝。
- LinuxNetworkAdapter：索引18转换为-1800。
- 服务端/客户端native常驻命令、角色配置：不携带Short GI开关。
- 在线重配编码Short GI位为0；console保留Long GI配置投影。
- 集群校验：节点槽位1..10、固定TUN映射、新管理拓扑与索引18。
- 用回环动态端口和现有mock隔离；禁止本机验证触碰生产UDP/TUN或启动真实链路。

## 执行边界

先完成代码/文档收口和针对验证、双轴审查，按目的提交；仅从提交源码发布。本轮使用`.scratch/heartbeat-restart/release-online.py`的显式目标分阶段入口，不使用通用`deploy_cluster.sh`的跳过离线节点路径。用户确认fl02/fl07/fl08/fl09/fl10断电暂不处理，本轮可发布目标为fl00/fl01/fl04/fl05/fl06，发布前须重新核验全部目标身份和空闲状态。任一目标不可达则在停服前中止，不静默跳过。

角色功率独立配置与真实CIFAR/ResNet训练入口是后续任务，不混入本票。完整九客户端待命和通信闭环未验收，当前小集合诊断不替代正式参与集合。

## Answer

代码收口与双轴审查完成，相关258项通过、1项浏览器入口未运行；详见`../evidence/baseline-close/README.md`。当前状态指实现和审查完成，不表示已通过完整九客户端实机门槛；本轮发布结果另存release目录。

## Comments

- 早期误将板级25W理解为无线索引25，120秒观察失败：fl04无READY，fl07未入网；证据`../evidence/power25-heartbeat/20261106T074347Z/`保留，不作为当前目标。
- 被动收包曾发现fl04 retired_session、fl07本机心跳服务端未见；未据此宣布网卡硬件故障。
- FEC修复d898d21和心跳跨重启修复c7d99b6已单独提交发布，本票不重复实现。
- 当前已发布版本仍Short GI；本票新代码未发布前不能声称Long GI已实机生效。
