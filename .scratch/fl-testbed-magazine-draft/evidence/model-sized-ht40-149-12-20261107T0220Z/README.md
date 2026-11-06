# CIFAR10 ResNet-18 等量通信筛选：149/HT40/MCS3/12Mbps

- 固定客户端：fl01、fl02、fl04、fl05；服务端 fl00。
- 制品格式：`cifar10-resnet18-fp32-v1`。
- 序列化大小：44,770,302 字节。
- 制品 SHA-256：见 `model-manifest.json`。
- 无训练、聚合或正式 FL 作业生命周期；只验证等量文件通信闭环。

## 结果

`run-1-progress.jsonl` 和 `run-2-progress.jsonl` 分别记录两次完整闭环。两次均满足：四端下行接收、SHA-256 校验、四端等量 HTTP 上传、服务端独立大小和 SHA-256 校验、临时 unit 和专用路由回收、恢复审计通过。

两次均无超时、最终传输失败或完整性失败。

恢复审计覆盖信道 149、HT40、Long GI、服务端 MCS3、客户端 MCS4、索引18、15W、六核在线、CPU/GPU/EMC 限制、EMC 未锁频及 native/PID 状态。

## 边界

这是诊断信道 149 的两次通过记录，不是正式信道 157 的验收。此前在 157/HT40/MCS3/15Mbps 下发生过 fl00 USB reset；随后 157 切换又因 fl02 未上线而回退 149，均独立保留在通信运行记录中。
