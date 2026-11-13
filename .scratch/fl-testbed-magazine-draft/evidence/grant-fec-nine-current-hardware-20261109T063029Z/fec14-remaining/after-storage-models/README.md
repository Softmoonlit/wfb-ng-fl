# After storage recovery: remaining FEC14 models only

父明确授权存储修复后的独立attempt，只执行40/10、60/10各一次44,770,302字节模型，不重发已通过small，最多2execute，不切FEC12。原批两次UFTP8/Not enough disk space、零主体传输及rawflags和独立恢复均保持原样。先前空间回收详见父目录storage-cleanup-continuation-*；本批每模型前全十statvfs可用>=512MiB、可用inode>0。

复用已修正driver/worker，冻结source/native/通信bundle不变；DL3/UL4、FEC8/14、40HT LGI、UFTP12000Kbps、157测量149恢复、方向600s/overall1290s。独立本批55分钟预算。checkpoint.json/matrix.json供父读取，所有原始失败/恢复分别保留。最终10/10+全十实际149/FEC14及九READY；管理失联、USBreset、清理失败或恢复不齐即停止。预启动py_compile和两候选及bundle核验见prelaunch-review.json。

## 已完成

两次新attempt均九端完整闭环通过，无small重发。40/10下行59.398690399s、上传380.429014567s；60/10下行77.135334168s、上传373.287430714s。各自原始transfer/cleanup/restore全true，exactcleanup、immediate全十实际基线、九READY及独立恢复全true，bundle未变。40最后上传客户端08为379.826103408s，60最后上传客户端07为372.633658028s；阶段耗时包含调度/校验等开销，不等于纯稳态吞吐时间。

最终服务端IDLE、activejob=null，标准恢复10/10；全十实际149HT40/FEC8/14/MCS3/4/LongGI/iw功率索引18、有效15W、source/native/40文件审计通过，九READY/IDLE，无活动旧测试units，worker已退出。每run final-idle exact UFTP/kernel/文件记录均保留，未匹配USBreset；model-delivery-facts.json从服务端updates/<node>/update.bin逐份独立记录两档各九份44,770,302字节/SHA4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96，实际sender日志12000Kbps。

最后只读空间回读：01可用805,785,600字节，02可用825,139,200字节；均仍>=512MiB，后续父按每模型前guard继续控制缓存累计。此阶段未做额外缓存删除，不切FEC12。旧批40/60 UFTP8基础设施阻断记录和空间清理记录保持独立，不用新成功覆盖旧失败。新硬件单次两档成功不构成最优选择或连续两次验收；旧A硬件数据仅历史，不跨硬件直接排名。
