# CIFAR 等量载荷失败记录

- 参与集合：fl01、fl02、fl04、fl05，服务端 fl00。
- 载荷：CIFAR-10 ResNet-18 seed7，44,770,302 字节，SHA-256 为 `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`。
- 固定条件：Long GI、驱动功率索引 18、RS(8,14)、15W 板级功耗。

三次等量传输均在服务端下行阶段失败，均出现 UFTP 退出码 9，未进入完整 HTTP 上传闭环：

| 运行 | 信道 | 带宽 | 下行 MCS | UFTP 速率 | 结果 |
|---|---:|---:|---:|---:|---|
| `model-sized-ht40-157-12-1` | 157 | 40 MHz | 3 | 12,000 Kbps | fl00 USB 网卡 reset，失败 |
| `model-sized-ht20-157-6-1` | 157 | 20 MHz | 3 | 6,000 Kbps | fl00 USB 网卡 reset，失败 |
| `model-sized-ht20-149-6-1` | 149 | 20 MHz | 3 | 6,000 Kbps | fl00 USB 网卡 reset，失败 |

每次失败后均通过标准 systemd 服务重启恢复 fl00 及四个客户端，最终回到 149/HT40/15,000 Kbps，四客户端 READY/IDLE。失败记录中的临时角色、路由和恢复事实保留在对应 JSONL 中；失败样本不计入候选成功次数。

内核记录在无线接口离开混杂模式后出现：

```text
Disable of device-initiated U1 failed
Disable of device-initiated U2 failed
reset SuperSpeed USB device number 3 using tegra-xusb
```

同一 fl00 USB 设备 `2-1.1` 在三种长流条件下复现。当前不能把这些结果解释为客户端无线参数失败；149/HT40/12,000 Kbps 的两次闭环仍是唯一通过的等量候选。带宽受限组和正式信道 157 均暂未通过两次通信验收。
