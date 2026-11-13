# 冷切 audit 有界恢复后的 FEC14 有效矩阵

父已明确授权一次有界恢复、最后 audit 一次及四档续跑。首次零执行失败完整保存在 ../zero-execute-cold-audit-failure/，原 root STOP/matrix/checkpoint 不覆盖。本轮实际复核 fl03/fl06 均为 channel149、40MHz、FEC8/14，全十端 actual 与九 READY 通过，无需 settled 或服务恢复；最后 audit 一次重跑十板全通过，原 profile 已七阶段完成。

worker.py 仅适配原已审监督器的启动门：载入原 driver/helpers/storage，重定向 d.ROOT/h.ROOT 与八个 candidate.rawpath 至本目录；storage.ROOT 保持原 matchedroot，三证据源递归覆盖本次新目录。原 profile/helper/driver 均未修改，不重复 stop/install。所有 input 摘要在 review-manifest.json；原已审源码 SHA、原 stage 起点与原截止时间在 continuation-plan.json。总预算沿原 worker 的 deadline_monotonic，不延长原110分钟。

固定10/10、20/10、30/10、20/5，各小文件通过收发size/SHA、cleanup、full10 actual、九READY才单次模型，最多八次 execute。方向600秒、overall1290秒、12000kbps、157测试/149恢复、FEC14、HT40/LGI、15W等全部沿原审阅脚本。保留首次和最终独立恢复字段；仅iw基线异常允许一次settled和必要一次异常节点标准恢复，身份/native/FEC等非iw异常停止。

每模型前实时全10 statvfs硬门512MiB及inode，只有01/02授权精确完整重复文件回收，保留B2、失败partial、日志/JSON/source/release/install-cache/evidence/训练数据，逐文件SHA/re-stat/unlink，不归档二进制。基础设施监督器复用FEC12机制，精确UFTP状态8、容量拒绝即STOP，不进下一候选。worker及监督器后台运行，本地监督快照单次等待最多25秒。结束full10/九READY、server10/10、FEC14/149交父，不额外选优测试。
