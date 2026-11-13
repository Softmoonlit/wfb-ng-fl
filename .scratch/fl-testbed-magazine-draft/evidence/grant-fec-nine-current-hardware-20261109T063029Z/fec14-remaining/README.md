# 当前硬件 FEC14 剩余 40/10、60/10 阶段

固定 fl00+[1,2,3,5,6,7,8,9,10]，04排除；每档一次1MiB smoke，通过且独立恢复后一次44,770,302字节模型。最多4 execute，不重试；方向600s/overall1290s；阶段55min，metadata.json记录原始截止。冻结source/native/runner bundle见matrix.json。仅本阶段worker负责远端。

metadata.json、checkpoint.json、matrix.json供父进程跟踪。每次runner日志、原始progress、after独立审计、exact清理、exactrun UFTP/kernel/文件SHA均保存在本目录。small若瞬时157，保留原始after与STOP，一轮settled及必要一次标准恢复后独立continuation准入，只执行尚未执行的model。

初次本地新增审计误将native MCS index要求18，实际该参数应DL3/UL4；驱动功率索引18由iw txpower -18.00核验。零execute原始失败和收尾证据完整保留zero-execute-criteria-error/；该次收尾因错误判据造成一轮不必要的标准resident重启，详见其final-closeout/single-standard-recovery.json。此后仅修正本地判据，独立重新准入，未改功耗、冻结源码/native/tool bundle、库或管理网。新准入全十通过。

UFTP与uftpd为相同5.0.3程序，libcrypto仍为已记录的现有两组，不声称库全同。硬件00 fc:a3:86:b3:86:72 USB480、10 fc:22:1c:10:01:19、05 fc:22:1c:10:01:57固定，其他MAC与上阶段快照一致。旧A硬件结果仅历史，不能跨硬件直接公平排名。此阶段不会切FEC12。


## 阶段停止与存储闭环

四次execute已结束：40/10与60/10 small均九端闭环及独立恢复通过，DL/UL分别8.640/27.365s、14.189/17.863s；两次model均UFTP8，fl01明确Not enough disk space，未送模型主体，没有上传，不是有效吞吐/调度性能样本。原始model false/true/false完整保留。最终server10/10、全十149/FEC14/有效15W/source/native及九READY独立通过，worker退出；父紧急停止通知到达时已执行完四次，此后无测试。具体原始时刻见stage-result.json、worker-completed-*-before-storage-stop.json。matrix/checkpoint现标记stopped_infrastructure_storage。

随后父授权仅01/02精确完整重复模型缓存清理，前提两端可用>=1.5GiB。只读inventory已证明01容量不足：35份尺寸匹配中2份在uftp-tmp，排除临时文件并保留一份最近成功B2后最多32份可候选，尚未全部证明SHA匹配；即使全匹配也达不到1.5GiB。因此触发“候选不足不删除”分支，cleanup-manifest.json deletions为空。两端均零unlink，未动任何日志/manifest/status/json/partial、安装缓存、正式训练、源码或系统用户目录。额外只读du保存storage-closure/insufficient-capacity-additional-readonly-du.json，不构成扩大删除授权。最终再次全十baseline和九READY通过（storage-closure/post-no-delete-*）。父需评估存储容量缺口；本操作者不启动新批测试/FEC12。
