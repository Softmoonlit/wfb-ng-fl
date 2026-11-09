# B2 再次互换00/10：1MiB后停止，模型0execute

身份实读符合00=fc:a3:86:b3:86:72/480Mbps，10=fc:22:1c:10:01:19/5000Mbps，05=.0157不变，均rtl88xxau_wfb。USB伙伴路径和480Mbps不判故障。仅改00/10显式接口drop-in及actualargs并标准start；before/after完整保存在explicit-interface-binding-changes.json。

复用audit-corrected-continuation，source引用release_root/source/wfb_ng/fl。旧STOP/失败/脚本未改。初始全10实际15W/source/native/UFTP5.0.3摘要/149HT40 LongGI/index18/MCS3/4/GETFEC8/14及server10/10通过；九端真实READY/IDLE且无job/本地runner。04排除，02未换卡；管理WiFi、功耗、部署及冻结bundle均未改。

恰1execute：30/5 FEC14、UFTP12000Kbps、方向600s/overall1290，1MiB DL32.702334215s/UL15.861213730s；raw transfer/cleanup/restore均true，bundle不变。IDLE独立读取server九份updates/<node>/update.bin全部1048576字节且SHA30e14955ebf1352266dc2ff8067e68104607e750abb9d3b36582b8af909fcb58，参见delivery-facts.json与exact-uftp-kernel-files.json。十端exactrun UFTP/kernel日志保存，未匹配USB reset。

即时独立after-audit中fl02仍实际channel157，故independent_restore_ok=false保留；随后READY gate虽true也没有覆盖失败、没有执行模型、没有重试。模型execute_count=0，不能对B1模型下行48.588失败与A2模型下行50.343全9成功作B2复现判断，也不判NIC损坏。

只回收本轮exactunits，然后server配置从30/5标准恢复10/10（由配置变化触发一次server标准restart），未额外重启客户端。final-closeout/result.json独立恢复全10实际149基线=true、九端READY/IDLE=true、exactcleanup=true。原始阶段失败与独立最终恢复分别记录；worker已finished/stopped，无后续测量，00/10服务保持标准运行，无需等待物理换卡。
