# 硬件互换前准备完成

当前30/5旧硬件模型已按600秒上行期限完成失败及清理，原始false/true/false保留，最后缺02；05在484.755秒完整上传。本次结束后十板独立恢复通过、server10/10且九端READY/IDLE，再采集本目录身份与标准停服证据。

fl00 server、fl10 client、fl05 client均已systemctl stop，实际ActiveState=inactive、SubState=dead、MainPID=0且无wfb_v6_uplink，可通知用户拔卡。没有关机、没有改绑定；SSH宿主运行，其余客户端不动。物理完成前不重新启动三端或新试验。

实读当前NIC：00 fc:22:1c:10:01:19，USB2/2-1/2-1.1，5000Mbps；10 fc:a3:86:b3:86:72，USB1/1-2/1-2.3，480Mbps；05 fc:22:1c:10:01:57，USB2/2-1/2-1.3，5000Mbps。完整sysfs路径、vendor/product、驱动、参数、功耗限制与源码native摘要在before-nic-identities.json。天线必须用户物理确认；04闲置B卡历史MAC不作为已确认身份。

本次计划00/10互换与05更换同时发生，此后属于新多设备硬件基线，不是严格单变量05 A/B，不能把改善单归因05卡。
