# 2026-10-29 UFTP 注册失败诊断

## 结论

用户手动五轮作业 `web-849186770bbc4c9daa1c32a6fb26a146` 在第三轮下发模型前，因 node1 未完成注册而 UFTP 返回 7。前两轮训练/聚合已完成，终态失败且资源恢复为 ready。

闭组 UFTP 的退出码 7 可以表示部分参与者未注册；不要求所有节点均无响应。本次 node2 已注册，node1 接收日志持续记录 REGISTER sent，但 Server 未在公告预算内收到 node1 注册。

native 调度记录表明 node1 在 GRANT seq=17490 后因两次静默授权被移除，随后授权不再发给 node1。后续原条件诊断在 seq=22291 后复现相同路径：只给 node2 发放 seq=22293..22333，直到 READY 被接收后重新入队，seq=22335 才恢复 node1，七份 REGISTER 随后集中到达。这支持“无授权期间排队，重新声明恢复过慢”的解释。

底层首次静默/控制帧丢失的具体 RF、驱动或空口原因尚未确定；本修复针对已确认的恢复放大机制，不声称消除无线丢包。

## 原条件诊断

固定线上版本 `77fa8fe`、目标 `[1,2]`，149 / HT40+ / 驱动14档 / 下行MCS3 / 上行MCS4 / 控制MCS0 / UFTP15000 Kbps。没有通过重启、清注册表、调整功率或扩大超时制造成功。

| 作业 | 五轮结果 | 限制 |
|---|---|---|
| `web-918f9949a8e94fd7be7b0dd8b1ae2a8d` | succeeded / ready | 第五轮 node1 多次重发 REGISTER 后恢复，原被动过滤错误使用1144，未获得UFTP包对照 |
| `web-6ddf43cff5544620aeed0ceb87cdc4b7` | succeeded / ready | 第四轮 node1 首次注册被Server接受耗时约4.413秒（同一Server日志时钟），多份注册突发到达；客户端绑定旧TUN索引的取证进程随作业重建接口退出 |
| `web-cd6a0f9a1c0d4be48a343da8437b260a` | succeeded / ready | 最终按接口名读取、不绑定ifindex的被动抓包覆盖两端全部10次注册，逐字节关联payload；这次未复现长注册间断 |

上述三次均为修复前诊断，不能算作修复验收。

## 证据口径

- `server-role/` 保存原始失败作业的 Server 传输日志及状态；不包含模型或update二进制。
- `fl01-original-receiver.log`、`fl02-original-receiver.log` 来自 `/var/lib/wfb-ng-fl/evidence/<job>/uftpd.log`。原目录清理后日志已归档，不是丢失。最初仅检查工作目录而称“接收日志删除”不准确，已纠正。
- `*-original-role.log`、`*-trace-role.log` 为客户端持久归档的 native/RoleService 日志。
- `server-native-windows.log` 是当前 Server native 会话按 GRANT sequence 窗口提取的上下文。原 append 文件含不同启动周期的单调时间重复值，不能只按数值区间混合旧会话。
- `*-stable.jsonl` 为最终被动包观测；UFTP source UID 在头部4..7字节，group ID在8..11字节。旧09脚本把source UID标为group ID的解析未用于这里。
- 不能用跨板墙钟差推算单程延迟；只在同板单调时间或同一日志中计算间隔，跨板通过payload及UID/group/端口关联。
- 所有观察器只读取现有网络包，不加入组播、不设置混杂模式、不发送探针；正式传输端口为1044。

## 修复与验收

待隔离 RED/GREEN、双轴审查及冻结发布后补齐。曲线部署在问题处理期间暂停。
