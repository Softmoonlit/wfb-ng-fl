# 00与10网卡及天线互换后的独立硬件批次

00实读fc:a3:86:b3:86:72/480Mbps，10实读fc:22:1c:10:01:19/5000Mbps；05未换，仍fc:22:1c:10:01:57/5000Mbps。00/10仅更新标准service接口及actualargs相同接口项，before/after保留，05配置不改。源码1cd/native44和冻结bundleab8697不变。

30/5 FEC14 small恰一次：DL9.0035s、UL9.9859s，九份完整size/SHA验证，原始transfer/cleanup/restore均true。首次独立审计fl01短暂iw157记录false；后续只读全10实际149和九端READY通过，分别保留，不覆盖首次false、不重跑small。

30/5 model恰一次：publish约48.5879s UFTP退出9，无submit。Sender先收齐9端REGISTER与FILEINFO_ACK，发文件后Sending thread timed out，所有1/2/3/5/6/7/8/9/10均Lost connection；9端receiver均Transfer timed out。没有receive_verified事实或可复核完整inbox模型，仅留restart记录；不能凭文件声明大小认定完整。没有USBreset匹配日志，不将exit9归因于05或任何NIC损坏。

原始模型transfer=false/cleanup=true/restore=false，独立恢复HTTP409，首次afteraudit部分端不在149，均保留。已停止新试验，后用一次标准server10/10恢复149入口，随后全10 actual GETRADIO/GETFEC/native/source/pid/15W正确、所有自有unit inactive、全9 READY/IDLE且无activejob。最后恢复结果独立写model-continuation-after-settled-recovery/final-closeout/result.json，不能覆盖原始false。

没有retry、40/60/FEC12或USB/驱动/功耗调整。480Mbps不作为失败条件；保留新旧拓扑，不据伙伴bus完整路径变化推定物理插口改变。后续诊断另行确定，本批不自动测更多。
