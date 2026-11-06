# 修复客户端重启后的心跳代际判定

Status: need-for-review
Type: task

## 问题与证据

fl01 换回原卡并断电重启后，19份独立心跳均配对ACK，但服务端24/24样本仍OFFLINE。原始抓包位于 `../evidence/fl01-card-b-20261106T114401Z/`（重启前请求timestamp约9038344）和 `../evidence/fl01-card-a-return-20261106T115822Z/`（重启后62406..101038）。

`ControlPlaneClient._next_timestamp_ms` 使用本机单调时钟；主机重启后数值下降。`NodeRegistry.update_heartbeat` 将所有较小值当旧包，返回ACK却不更新last_seen。

注意：`get_status_report.last_heartbeat_timestamp_ms` 是服务端接收的墙上时钟投影，不是线上请求排序值；不得以其1.79e12与请求值比较作为证据。

## 修复约束

- 心跳顺序与客户端启动身份分离，不比较跨主机/跨重启时钟。
- 新启动必须重新入网确认，不能继承旧READY。
- 旧启动的迟到/重复心跳、ACK不能恢复旧状态或续期。
- 不保留旧协议回退，不通过手改注册表或伪造READY恢复。
- 不放宽230ms单次ACK预算、10秒持续失活门限。
- 先通过公共心跳编解码、注册表更新及loopback UDP接缝复现，再修复。
- 不绑定生产9000/9001/9002/9200，不访问真实TUN。

## 验证边界

主机重启、守护进程重启、旧代际迟到、同代际乱序/重复、ACK身份、信道/来源校验、正常HUNTING到IDLE闭环。实机统一发布仅使用已提交源码，先核对准确运行身份。fl08/fl10当前断电，不能宣称全10台发布或全9客户端验收。

## Comments

- 已核对 fl00/fl01 安装control.py与HEAD摘要一致。
- 离线红复现已确认9038344/9038345后收到62406/62407仍ACK而不更新记录。
- 实现使用客户端启动身份、会话内序列与服务端握手分离；新代必须HUNTING→IDLE。ACK绑定boot/session/request/channel。
- 两轮审查发现单个pending challenge槽会被重复/迟到请求扰动，改用标准库HMAC将挑战绑定当前已接纳会话与新请求，不共享可覆盖的pending槽。该机制确认往返，不提供客户端认证。
- 用户确认fl08、fl09、fl10断电，不能跳过固定目标宣称全体发布；本轮先完成代码、隔离验证和发布准备。
- 最终独立HEAD工作区260项测试通过，代码审查原P2已关闭；详见`../../heartbeat-restart/README.md`。代码完成，统一发布及真实重启恢复、九客户端待命与通信闭环仍待硬件恢复后验证。
