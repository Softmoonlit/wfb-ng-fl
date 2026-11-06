# 四客户端纯通信：首次基础冒烟

## 结果

按用户修订停止心跳关联诊断，以fl00和固定四客户端fl01/fl02/fl04/fl05进入文件通信。首次1MiB闭环完成：四台下行全部接收并SHA-256正确，四份正式HTTP上传完成，服务端独立核验全部文件正确；无超时、最终传输失败或完整性失败。

`transfer_verified=true`、`cleanup_ok=true`、`restore_ok=true`分别记录，不以清理状态覆盖传输事实。结束后五台临时unit均inactive、两个测试组播路由均不存在、UFTP1144/HTTP18080无遗留监听；原daemon/native PID及启动标识未变，服务端IDLE、四台READY。

这是一次基础transport冒烟，**不计入正式ResNet等量载荷选参，不表示三组配置已完成验收，也未验证正式FL任务的准备、链路接管、训练、聚合和恢复生命周期**。没有额外做心跳关联捕获。

## 固定条件

- 工具提交：`5d2fc70`，runner/node_agent/recover bundle SHA256：`a4638926dae73388fcc3db61b241369db335459c2568d53669abd6f0cad3caf1`。
- 生产安装：`c6659488f64976185ffe02a7c856f7fd8c821ee0`；native SHA256：`ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56`。本轮没有替换生产代码或重启常驻daemon/native。
- 测试信道157、40MHz、Long GI、驱动功率索引18、下行MCS3/上行MCS4、RS(8,14)、UFTP注入15000kbps。
- 全五节点15W；fl01保留fl08供体卡，fl05保留fl10供体卡及fl06电源，位置与USB口不变。fl06～fl10不连接、不参与。
- 使用现有正常reconfigure事务切换到157；calibrate仅支持HT20，不用于本轮。普通重配不持有守护进程作业准入租约，执行前确认无活动作业，本轮未并行启动FL任务。
- 测试结束通过正常事务恢复原149/40MHz/Long GI/索引18及其他六项配置；不持久化候选参数。五台末尾GET_RADIO和iw读回已核验。

## 文件和观测时间

每份文件1,048,576字节，内容为`bytes(range(256))`重复4096次，SHA256：

`fbbab289f7f94b25736c58be46a994c441fd02552cc6022352e3d86d2fab7c83`

| 客户端 | UFTP下行hash | HTTP上传完成 | 服务端独立size/hash |
| --- | --- | --- | --- |
| fl01 | 通过 | 通过 | 通过 |
| fl02 | 通过 | 通过 | 通过 |
| fl04 | 通过 | 通过 | 通过 |
| fl05 | 通过 | 通过 | 通过 |

管理端使用同一主机单调时间：完成角色启动到服务端最终校验约9.05秒，其中下行接收及屏障约5.57秒、上行及最终验证约3.48秒。含准备、切频、回收、恢复的执行器全流程22.54秒。这些时间包含管理RPC和屏障开销，不当作纯空口吞吐，也不是FL轮次耗时。

## 原始证据

- `status-before.json`和五台`*-prepare-readonly.json`：只读身份、radio及供电盘点。
- `calibrate-request.json` / `calibrate-response.json`：首次工具准备请求被400拒绝，缺显式参数，未改变射频，不算无线传输失败。随后代码审查确认40MHz应走普通reconfigure，未再次运行不适用的calibrate。
- `local-tests.txt`：7项本地测试通过，覆盖准备屏障、并发阶段、拒绝未经审查的执行、完整radio请求和所有权回收。
- `runner-process.json`：冻结源码、命令、bundle SHA与本次父进程；`runner.log`：控制台全记录。
- `runs/smoke-157-1m/progress.jsonl`：各阶段原始结果，包含实际切频与恢复响应、四端接收hash、四份上传路径和服务端重新计算hash、独立回收。
- 五台`*-final-audit-and-facts.json`：末尾iw、GET_RADIO、空路由、15W模式，以及节点facts、UFTP日志/status、收发manifest和round-state等文本原件。1MiB二进制可按上述规则重建，未提交重复载荷。
- `status-final.json`：结束快照。READY仅表示该时刻状态，不重新宣称全程无寻频。

## 下一步

先冻结CIFAR版ResNet-18具体结构与制品格式并生成精确序列化载荷。当前仓库正式入口只有MNIST；ResNet-18尚未实现，不能用ImageNet预训练权重的44.661MB元数据或预算44.7MB代替。

现有模型制品校验要求全部tensor为FP32、默认最大1MiB，而标准ResNet含BN int64计数buffer；新模型必须明确处理这些结构状态并设置相应制品上限，不能静默转型或跳过严格校验。角色独立功率控制尚未实现，仍是第三组候选的前提。

之后扩展等量载荷闭环，按同一四客户端集合筛三组；每组最终连续两次全员完成且hash正确，无超时/最终传输失败。现有寻频记录保留，不恢复已停止的关联诊断。
