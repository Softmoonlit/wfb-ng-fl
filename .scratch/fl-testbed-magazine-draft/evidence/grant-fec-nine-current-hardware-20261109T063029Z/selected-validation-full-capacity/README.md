# 充电后两轮验收：首轮准入失败，模型未发送

固定集合为fl00与客户端[1,2,3,5,6,7,8,9,10]，fl04排除。冻结源码、native、模型、FEC 8/12、30/5调度、Long GI和硬截止保持不变。

本批十板初始实际审计、九端READY及存储准入通过。fl08空间不足时，仅按已归档成功轮的完整日志、尺寸和SHA证据，复核后精确回收13份重复模型；B2保留件重新校验，失败轮、partial、日志和训练目录未删除。清单见`trial1/exact-cache-reclamation/cleanup-manifest.json`。

仅调用一次通信执行器。切换157后，服务端`registry_gate`报`target registry gate failed: 8`，表示当时fl08的READY、IDLE、无错误条件至少一项不满足；错误未记录具体违反项，不据此判定硬件损坏、断电或固定链路丢失。publish和submit均未启动，模型有效负载发送次数为0，下行和上行时长均无数据。第二轮未启动，没有自动重试。

原始结果保留为`transfer_verified=false`、`cleanup_ok=true`、`restore_ok=false`。随后独立十板实际审计和九端READY门通过，确认恢复149/HT40/Long GI、驱动索引18、FEC 8/12、30/5及15 W限制。独立恢复通过不改写原始flags，也不构成完整模型验收通过。

fl02 PackageKit最终恢复为原active/static，执行器与监督进程均已退出。精确结论见`parent-final-closeout.json`；原始运行见`trial1/run-summary.json`和该轮`progress.jsonl`。两次连续完整模型闭环仍未完成。

## 本地工具与维护记录

此前独立零负载准备目录保留JSON写入错误、缺少审阅清单、缓存已不存在却被误判、以及PackageKit恢复参数错误的原记录。恢复参数错误发生后，父执行者立即标准启动服务，确认active/static，后续包装器传入正确service字段，最终恢复核验通过。没有混入软件部署或修改测量参数。

PackageKit原有唯一事务通过已安装GI定义确认为`get-update-detail`/`download`，允许取消，但标准Cancel因调用连接与发起者不同被服务拒绝。后续仅在没有APT/dpkg相关运行进程、四锁空闲、事务为空或仍为精确核验的只读详情查询时，使用标准临时服务停止；缓存文件不存在时不执行删除，存在时仅处理两个精确可再生缓存路径，结束时恢复服务。早期准备证据见相邻的`selected-validation-final-two-after-all-powered`、`selected-validation-after-readonly-query-service-stop`、`selected-validation-reviewed-service-hold`和`selected-validation-ready-two-model`目录，不与本批通过结论拼接。
