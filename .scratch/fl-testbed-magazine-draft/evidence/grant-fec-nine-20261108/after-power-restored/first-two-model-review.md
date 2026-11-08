# 首两档模型容量证据独立复核

只读独立复核，不在测量窗口内编译／运行测试，不操作远端。

- 10/10、FEC8/14：固定九端下行均receive_verified，44770302字节，SHA `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`；九端上传完成，服务器独立读取九份update.bin并校验同一字节数与SHA。DL115.991941418秒、UL559.409249964秒，均小于600秒，原始transfer_verified／cleanup_ok／restore_ok均true。证据足以认定该次完整通过，尚不等于入选连续两次验证。
- 20/10、FEC8/14：UL600.04057822秒截止，七份完整提交，2号和5号分别仅29294592／11730944字节且committed=false；原始restore_ok=false、独立independent_restore_ok=true分字段保留，execute_count=1，无重跑。
- 失败候选并非一律禁止继续不同候选：约定是完成清理且独立完整恢复通过才可继续，实际管理掉线／USBreset／无法恢复才停矩阵。g20之后全部十板独立管理、运行身份、实际射频／GET_FEC、15W、临时unit清理及USB日志审计通过；g30执行前再次完整九端READY/IDLE且实际149/40MHz。因此继续g30符合约定。
- HUNT／注册表OFFLINE是本轮无线测量的待观察行为，不等同管理SSH掉线，不在600秒截止前仅因一次该状态终止当前传输。原始失败不改成功。

原始证据：两个目录中的progress.jsonl、run-summary.json、after-audit.json及模型matrix。源码1cd6264d、native SHA44d6bbf5、bundle SHAab8697fe一致；native平台ALL10槽位未被当作九端参与集合。

未发现阻塞P1／P2。证据完整性建议：内核日志否定结论目前由正则／kernel_error与审计汇总承载，后续如审计工具改动应显式保留journalctl退出码；未观察到本轮内核日志读取实际失败，不据此虚构USB异常。
