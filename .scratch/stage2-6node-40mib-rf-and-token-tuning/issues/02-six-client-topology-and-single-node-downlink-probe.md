# 02: 6 客户端拓扑收敛与单机 MCS 3 / 15000 Kbps 下行提速探路

**What to build:**
收敛测试拓扑并突破下行大文件传输瓶颈。接入 `ISSUE41_CLIENT_ROLES="client1 client2 client3 client4 client6 client7"`，正式在编排执行与结果审计中排除硬件存在缺陷的 Client 5，将拓扑收敛为 1 Server + 6 Clients；在当前超近距 12 dBm 发射功率下，仅对 Server 与 Client 1 执行 40 MiB 单节点下行探路，将 Server 配置为 MCS 3 (16-QAM) 且 UFTP 速率设为安全中值 15000 Kbps，验证单次 40 MiB 下行耗时缩短至约 22 秒，无内核 TUN 缓冲溢出丢包，UFTP 零重发一次性收敛。

**Blocked by:** 01: 环境清理、全集群代码一致性核验与发射功率配置支持

**Status:** resolved

- [x] 验证编排脚本与校验器在 `client1 client2 client3 client4 client6 client7` 拓扑下正确推导 6 个客户端
- [x] 在超近距台面 12 dBm 功率下，单机启动 Server 与 Client 1 数据面链路
- [x] Server 下行射频参数配置为 MCS 3、HT40+、Short GI，UFTP 速率配置为 15000 Kbps
- [x] 执行 40 MiB 单节点 shared UFTP 下行传输探路
- [x] 验证 Client 1 完整接收 40 MiB 模型文件，SHA-256 校验完全一致
- [x] 验证下行总耗时稳定在 21 ~ 24 秒区间，无多轮 NAK 重发，底层遥测丢包完全被 FEC 8/14 吸收
