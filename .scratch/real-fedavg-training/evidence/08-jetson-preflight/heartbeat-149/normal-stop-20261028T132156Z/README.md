# 工单08：运行期间正常停止验收

## 冻结条件

四端实际运行提交 `c62cde266ef509d605c8c923b0f4fa35e42fc4dd`，沿用前一轮统一发布的构建核验与120秒稳定门禁。149/HT40+/驱动14档、方向数据MCS3/4、空口READY/GRANT MCS0，固定参与节点[1,2,4]。模型为种子7的官方参考初始制品，SHA-256 `b1a1c1734bec69acd26df1ed6c6aaafecd0b7a6b5b3c2f9b594043d7fca453d1`；准备/轮次/I/O预算分别300/1800/120秒。仅使用正常网页API启动与停止，不依赖SSH推进作业，不修改服务或射频配置。

## 执行与结果

执行器为上一级 `stop-web.py`。启动两轮短作业 `web-4c8de13bb3e64e93ac913520f4ab793c`；观察Server `waiting_updates` 且三个客户端RUNNING后请求一次停止，不通过固定sleep推断作业阶段。该条件证明已进入作业运行阶段，不单独证明停止瞬间每个客户端都在执行CUDA内核。

| 本机记录时间（UTC） | 事实 |
| --- | --- |
| 13:22:02.850 | 启动响应accepted，目标[1,2,4]、两轮 |
| 13:22:05.445 | 停止响应accepted，execution_result=pending；此时不认定完成 |
| 13:22:07.557 | 终态aborted，保留停止原因；恢复ready、全员确认、Server IDLE |

停止原因 `issue08_normal_stop_during_running` 未被训练效果或正常完成覆盖。本次完成轮数为0，不计为成功训练；`additional_errors` 中的同一 aborted 原因是停止收尾事实，不当作额外硬件故障。无liveness事件。完整HTTP请求/响应与快照见 `events.jsonl`，终态见 `terminal.json`。

终态之后执行 `verify-stop-runtime.py` 只读核验：

- 三端日志均记录作业RoleService进程组终止、退出码0、资源回收、带专用report_id的JOB_RECOVERY_ACK，返回IDLE。服务日志保留本机单调时间，不以不同板墙钟先后推断因果。
- 四端/proc检查无残留 `wfb_ng.fl.service` 作业计算进程；每端仅一个原生空闲链路进程，实际运行native摘要等于统一发布制品。
- 四端iw均读回149、40MHz、-14.00 dBm，Server IDLE、无活动作业、三个客户端READY。见 `post-stop-runtime.json`。

## 验收范围

与前一作业 `web-7383444963c54f558b721675d592b43b` 的两轮成功、六份CUDA更新、独立聚合/评估复核一起，覆盖工单08的少轮训练、正常完成、末轮确认、正常停止和待命恢复。它不覆盖工单12的无线不可逆提交前后停止故障矩阵，不替代20MHz标定或三次五轮正式验收。
