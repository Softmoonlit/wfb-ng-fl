# 01/02 完整重复模型缓存回收 continuation

父明确将预留阈值从1.5GiB调整为1GiB；原storage-closure/ no-delete记录保持不变。单模型预算3×44,770,302+64MiB=201,419,770字节；后续FEC12每模型前须statvfs可用>=512MiB，必要仅回收自己已归档重复完整模型。本continuation只清理，无测试。

本地canonical模型尺寸/SHA核验通过。每个待删文件都位于具名/srv/wfb-ng-fl/comm-*/role目录，文件名model.bin/update.bin；尺寸44,770,302且SHA4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96；逐一先归档对应comm内全部.log/.status/.json/.jsonl字节（base64及SHA），将path/runid/size/SHA/日志归档对应写cleanup-manifest.json，再独立重读stat/inode/device/mtime/hash和活动测试unit检查后精确Path.unlink。失败partial/uftp-tmp/unknown hash/其他目录全部保留；未删除目录。此为磁盘空间回收，不是删除失败样本；此前40/60 UFTP8基础设施失败及日志仍保留。

01删除24份，02删除10份；每端均保留最近成功B2 role/rounds/81f1fdf3-d962-48d1-890e-c4ffb6583d86/model.bin并在前后核验size/SHA。最终df01可用1,079,459,840字节，02可用1,098,469,376字节，均>=1GiB。全十10/10调度/实际149HT40/FEC14/有效15W/source/native/冻结文件及九端READY/IDLE通过，无activejob或活动comm测试unit。worker退出，未启动新测试或FEC12；父接后续after-storage-models批。
