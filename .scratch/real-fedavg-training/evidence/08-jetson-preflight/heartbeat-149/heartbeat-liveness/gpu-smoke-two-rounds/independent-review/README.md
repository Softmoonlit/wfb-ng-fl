# 两轮 GPU smoke 独立复核证据

作业：`web-7383444963c54f558b721675d592b43b`。

`results.json` 的 `pass=true`：两轮逐张量独立样本加权、M0/M1/M2 在 MNIST test10000 上的独立 CPU 评估以及模型哈希链均通过。详细方法、误差、指标和证据限制见 `独立复核报告.md`；脚本为 `独立复核.py`。本次未调用生产 aggregate/evaluate，未重新训练。

## 六份训练更新与模型链

六份更新覆盖节点 1、2、4 的第 1、2 轮。每份更新均记录 `sample_count=60000`、`optimizer_steps=938`、`device.type=cuda`、设备名称 `Orin`，torch 为 `2.5.0a0+872d972e41.nv24.08`，CUDA 为 `12.6`。其节点、轮次、样本数、步数、训练 loss、设备和基础模型哈希与终态 training 字段逐字段一致。逐份元数据与源文件哈希见 `results.json` 的 `training`。

CUDA 和步数是留存更新中的客户端报告事实，本次没有额外取得三端 CUDA kernel 执行 trace。每节点使用完整 60000 样本；每轮样本权重总数 180000 不表示 180000 个互不重复的样本。

实际文件 SHA-256 构成以下链，且与终态记录一致：

- M0：`b1a1c1734bec69acd26df1ed6c6aaafecd0b7a6b5b3c2f9b594043d7fca453d1`
- M1：`09e03cfb1217ee7668c9cbe16f9f290eac1fa8701fdea0009326cadd1b8672c3`
- M2：`083c9e58be8652b587275a2f35c75f47eec01a5f38b9a57c33f5ac2d130f21f5`

第 1 轮三份更新的基础模型及传输 model.bin 均等于 M0；第 2 轮三份更新的基础模型及传输 model.bin 均等于 M1。每轮按实际 sample_count 独立计算 FP64 加权，与落盘输出模型逐张量比较，两轮最大绝对误差均为 `3.973643e-8`。

## 日志覆盖范围与 fl04 targeted 查询原因

客户端 daemon 日志现已覆盖三端相同作业时间窗；覆盖边界如下：

- 先前首次查询使用了父进程当时提供的错误地址：fl01 `.101`、fl02 `.102`；当前地址已更正为 `.201`、`.202`。原 SSH `No route to host` 结果已改名保留为 `fl01-wrong-address-journal.txt`、`fl02-wrong-address-journal.txt`，不能据此判断实际节点不可达。归档前将这两份 SSH 输出的原 CRLF 换行规范化为 LF，完整文本内容保留。
- 按父进程更正的 release-four.py 拓扑，使用 fl01 `192.168.108.201`、fl02 `192.168.108.202` 重新执行相同时间窗的只读查询，服务名采用已确认的 `wfb-fl-client-daemon.service`。输出覆盖 `fl01-journal.txt`、`fl02-journal.txt`，SSH 均 exit 0；两端作业目录均已回收。日志均包含本作业派生、两轮 ROUND_READY_ACK、exit_code=0、JOB_RECOVERY_ACK 和回到 IDLE。
- `fl04-journal.txt` 是首次探查结果：作业目录已不存在，最初查询的 `wfb-fl-client` 服务名不正确，返回无日志。不能据此判断作业没有日志。
- `fl04-targeted.txt` 是为纠正首次探查而进行的一次限定查询：列出 `/srv/wfb-ng-fl` 直接子目录、匹配的服务 unit，并在作业时间窗按 job ID、CUDA、步数、loss、M1 哈希过滤 journal。由此确认正确服务名为 `wfb-fl-client-daemon.service`。该文件是过滤后的摘录，不是完整 journal。
- `fl04-daemon-journal.txt` 随后保存了正确 daemon 服务在 `@1793192970` 至 `@1793193070` 时间窗内的完整查询输出，未添加 grep 过滤；它只覆盖该服务和该时间窗，不代表整机或所有训练子进程日志完整。已见作业派生、两轮 ROUND_READY_ACK、退出码 0 和资源回收 ACK，未取得数值训练明细。

因此，模型链、数值加权与测试评估的证据完整，三端 daemon 指定时间窗日志均已留存；仍未取得独立于更新元数据的 CUDA kernel 执行和数值训练明细佐证。此次仅更正地址、补回同一时间窗日志，未重跑数学复核、测试或其他取证。

## 文件与提交边界

`input-hashes.json` 保留源路径、原始字节数和 SHA-256；`inputs/` 保留读取时的输入快照；`terminal.snapshot.json` 保存终态输入；`commands.json`、`取证命令.txt` 和各 fl*.txt 保存命令及结果。

**`inputs/` 含完整 MNIST test10000 原始图像和标签 IDX 快照，不应提交这些数据文件。** 本次未执行任何 git add 或 commit。目录体积由父进程后续查看，本轮不再执行统计、测试或其他取证操作。

`evidence-hashes.json` 已在地址更正及日志、文档更新后重新生成，包含本 README 与保留的错误地址结果，不包含清单自身；原始输入哈希以 `input-hashes.json` 为准。
