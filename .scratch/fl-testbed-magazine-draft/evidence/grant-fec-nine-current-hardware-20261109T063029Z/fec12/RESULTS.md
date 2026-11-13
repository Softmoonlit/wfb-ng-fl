# FEC12 七档完成交接

七档 smoke＋model 全部通过，合计 14 次 execute。worker 与基础设施监督器均已退出；已停止远端操作，不追加测试、不自行回 FEC14。最终全十端 actual FEC8/12、149、server 10/10、九 READY 均为 true，十端无活动旧测试 unit。总阶段耗时约 59.6 分钟。

| grant/guard | smoke DL/UL（秒） | 模型 DL/UL（秒） | 模型 DL＋UL（秒） |
| --- | --- | --- | --- |
| 10/10 | 6.373 / 8.693 | 52.263 / 416.058 | 468.321 |
| 20/10 | 4.773 / 8.361 | 55.566 / 375.851 | 431.417 |
| 30/10 | 10.851 / 9.380 | 47.048 / 341.329 | 388.377 |
| 40/10 | 11.266 / 10.219 | 49.518 / 384.655 | 434.173 |
| 60/10 | 8.601 / 22.514 | 49.579 / 330.583 | 380.163 |
| 20/5 | 7.810 / 7.321 | 46.503 / 372.569 | 419.072 |
| 30/5 | 5.194 / 9.602 | 46.883 / 298.469 | 345.352 |

14 轮 runner 原始 transfer_verified、cleanup_ok、restore_ok 全为 true。各轮独立精确清理、最终全十端 actual 恢复与九 READY 全通过。60/10 模型首次独立审计 fl09 出现 iw baseline mismatch，READY 当时仍为 true，一次 settled 后 actual 全 true；未使用标准服务恢复。首次 false 与 settled true 的独立文件均保留，未覆盖。

模型前全十端 512MiB guard 每次通过；两次授权精确缓存回收均仅涉及 01/02 有本地完整日志和 size/SHA 证据的旧完整重复 payload，各端累计回收 24 份，各端 B2 原件回收前后 SHA 均一致。最后一次模型前实测 01/02 可用 1,026,740,224／1,039,294,464 字节。此为执行前读数，不声称完成后的当前余量；按父指令不再远端查询。未命中已证实的容量拒绝，未触发 STOP。

详细结果见 handoff.json、fec12-results.csv、matrix.json 和 final-closeout/result.json；实际十端 FEC、网卡与 unit 状态见 final-closeout/final-fec12-baseline-audit.json。缓存 manifest 与日志元数据归档保留于对应模型目录的 exact-cache-reclamation。选优与连续两次模型验收交父处理。
