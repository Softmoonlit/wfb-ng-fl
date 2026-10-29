# 两客户端三次HT40五轮阶段性流程记录

日期：2026-10-29

## 当前验收范围

按用户决定，当前 FedAvg 流程验收固定为 fl00 Server 与 fl01、fl02 两个客户端，目标集合 `[1,2]`。fl04 的 HT20 长流故障单独归档，不阻塞本阶段流程，也不从本结果推断三客户端容量。

固定配置为 149 / HT40+ / 14档、下行 MCS3、上行 MCS4、控制帧 MCS0。三次作业均从同一初始模型 SHA-256 `b1a1c1734bec69acd26df1ed6c6aaafecd0b7a6b5b3c2f9b594043d7fca453d1` 开始，每次 5 轮，共 30 份客户端 update。三次均全程HT40，未执行HT20五轮和40→20→40轮间切换，不能据此宣称工单13完整双带宽验收结项。工单09、12仍未完成，13保持未完成。

run-02的run_id已按原始[start-response.json](run-02/start-response.json)更正；原始JSON保持不变。

## 作业结果

| 次数 | job_id | run_id | 结果 | 恢复 | 末轮准确率 |
|---:|---|---|---|---|---:|
| 1 | `web-c92a06af4d454db299603786001d93c3` | `web-d8be49f9dc8646ebaae3a74f8ccd523d` | succeeded | ready | 96.66% |
| 2 | `web-5b8bee6566414598a5a40902db4989d0` | `web-22edeb19b6e04727bca5ac1198ad61fa` | succeeded | ready | 96.66% |
| 3 | `web-451fbb8010f74cb7a9c3321a4e44037e` | `web-19cf553de1c145908b7445e30e5115a0` | succeeded | ready | 96.66% |

三次作业每轮均收到 fl01、fl02 两份 update，`dropped_out_nodes=[]`，每次完成后 Server 为 IDLE、两个客户端恢复 READY。第三次曾进入 recovering，最终 recovery_state 仍为 ready。

每次的独立数值曲线均为初始准确率 8.91%，第1轮约 87.47%，第2轮约 93.93%，第3轮约 95.2%，第4轮约 95.71%～95.72%，第5轮 96.66%；末轮损失低于初始且效果判定通过。

## 独立复核

在 fl00 上对三份 Coordinator job root 分别运行 `python3 -m wfb_ng.fl.fp64_audit`，生成独立备份和报告。三次报告均为：

- `status=passed`
- `numeric_status=passed`
- 5轮、10份 update 的模型链和摘要完整
- 独立 FP64 聚合结果通过
- M0～M5、update 及 manifest 已保存到 fl00 的 `fp64-audit-13-two-client` 目录

本工具明确将无线、Web、环境和最终多次验收判定列为工具范围外。各次 `web-status-*.json` 与启动响应可佐证管理HTTP受理、作业状态和恢复报告；本README仅索引并说明这些证据，不能补足物理环境、真实浏览器交互、全网无线实际确认或持久备份介质核验的缺项。FP64通过及备份目录存在不能直接推导这些项目通过，缺失证据须另行补采。

## 限制

该记录证明用户授权目标[1,2]下三次HT40五轮的执行、效果、恢复报告和独立数值复核结果；十五轮共30份update。当前两客户端集合是正式授权范围，不因客户端数量不足而否定这些结果。工单13仍缺规定的HT20全程作业、双带宽切换和相关确认，以及必要的环境／Web／最终交付证据；09实测速率表及12完整故障／页面矩阵仍未完成。不能据此填写完整最终验收通过。fl04故障见[09执行记录及勘误](../09-20mhz-rate-calibration/20261028-execution.md)；该结果不代表三客户端容量，后续若需该结论另行补测。
