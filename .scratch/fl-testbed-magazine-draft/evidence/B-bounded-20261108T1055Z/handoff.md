B12 属于 UL6 候选准入 COMMIT 失败，不是吞吐失败。原始 transfer=false/cleanup=true/restore=false 保留。

终态独立11板 final-audit-fl*.json：GET_FEC8/14、149/HT40/LGI、server3/client4、server18/2，unit inactive，无专有路由。power-source-audit-fl*.json：11板15W mode0，安装manifest全文件SHA及冻结source全部Python SHA通过，build commit f376a1f。

匹配session switch_6a870c1ec81b480cbeea917189a6e138：fl04 18:49:23.720+0800 COMMIT channel157；server 18:49:33.516 COMMIT超时缺4；fl04 18:49:48.567 watchdog回149。COMMIT日志在物理应用调用及radio_applied通知成功后记录，但没有本轮MCS6的独立GET读回样本；没有server有效收到COMMIT_SUCCESS4的正证据。后续恢复session switch_39780c0d3bf94a8ea204e989a05e2d61 已在fl04 18:50:00.288 CONFIRMED149。无抓包、无硬件写入、无更高档或模型。已释放硬件操作权供父C组。
