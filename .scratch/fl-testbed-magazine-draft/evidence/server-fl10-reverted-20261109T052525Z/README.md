# fl00/fl10 原卡回换 A2 与再次互换停服准备

最终状态：2026-11-09 05:49:34 UTC，仅00/10 WFB标准停止，均inactive/dead、MainPID=0、无native残留。repeat-swap-preparation/standard-stop-results.json的ready_for_user_physical_swap=true；等待用户物理再互换，尚未发生新互换或B2试验。其它端未再次重启，管理WiFi/功耗/部署源码均未修改。

当前00=fc:22:1c:10:01:19（5000Mbps）、10=fc:a3:86:b3:86:72（480Mbps），均rtl88xxau_wfb；05仍fc:22:1c:10:01:57。只恢复00/10服务drop-in与actualargs的显式interface项，前后全文见explicit-interface-binding-changes.json。再次互换后预期00=.8672、10=.119，05不动；接口绑定尚未为新互换改变。USB2/3伙伴bus路径不可据字符串推断换口，480Mbps不判故障。

首次新增本地审计引用source/cifar10_model.py而真实冻结目录为source/wfb_ng/fl，因此探针FileNotFoundError。原driver-base.py、worker.py、failed JSON、STOP与0execute原样保留，不作硬件结论。唯一修正放audit-corrected-continuation/，严格使用source/wfb_ng/fl相对manifest映射、无fallback；冻结通信bundle/native/已部署源码未变。错误处理曾额外标准重启一次fl00，原记录在final-closeout/，不隐去。

正确准入全10 source1cd6264d5d6b/native44d6bb…feb2、UFTP5.0.3工具/依赖库摘要、实际15W(online0-5/scaling_max1497600/GPU612M/EMCcap2133M/mrq0/nvp service成功/jtop inactive)、149HT40 LongGI/index18/DL3UL4/GETFEC8/14/server10/10与九端READY/IDLE通过。固定参与1,2,3,5,6,7,8,9,10，04排除；平台native槽池仍1..10，与参与列表不同，未改。

本阶段恰2execute，均30/5 FEC14、UFTP12000Kbps，每方向600s/overall1290s，冻结bundle ab8697fe4573ac3c1088dd11022a1fd75a25cf13752d2717de02e6aa2e625e9e未变。1MiB下行5.1308s、上传9.6572s，九份完整size/SHA，原始transfer/cleanup/restore与独立恢复全部true。

模型44,770,302字节/SHA4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96恰一次：下行50.3435s，九端receive_verified；上传600.0451s截止，只fl03完整submit。IDLE后独立核验server rounds/.../updates/3/update.bin完整size/SHA；其它八端[1,2,5,6,7,8,9,10]没有完整server上传文件。客户端本地update.bin均不当作上传送达。model-delivery-facts.json保存事实；exact UFTP sender/九receiver日志与kernel保存在corrected目录的rv…model-exact-uftp-kernel-files.json，没有USBreset匹配。

模型原始transfer=false/cleanup=true/restore=false保持不变。独立exactunits回收全部成功，server已标准恢复10/10；首次审计部分端未回149，后续只读除fl10外九台通过，fl10仍157/OFFLINE。仅对fl10做一次必要标准服务恢复，启动成功但再次只读仍157/OFFLINE，故全10恢复false/九端gate false。所有false与独立恢复结果分别保留在final-closeout/、settled-readonly-closeout/、after-fl10-standard-recovery/。没有更多重启、重试、grant或FEC12。

父最新明确授权：不为物理停服等待9端READY，保留恢复未齐后，确认00/10可达、无job/runner且exactunits已回收、server10/10，再标准停止00/10。因此物理可交换true与全10恢复完成false是两个独立事实，不能改写成完整恢复成功。

A-B-A单次模型下行：A1原卡332.7834s/九端完整；B互换48.5879s但sender失败、零receive_verified；A2回换50.3435s/九端完整。失败整组耗时不作吞吐完成时间，单次差异不证明任何卡损坏或排除换卡影响。historical-A-B-readonly-reference.json保存旧数据引用；本轮未覆盖旧结果或STOP。
