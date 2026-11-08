# 98dda459 十节点统一发布证据

本轮仅一批，固定源码 `98dda459fb9a1cecf64fd03e224427b8b7f689fe`，使用现有 `release-online.py` 的完整目标 fl00/fl01/fl02/fl04/fl05/fl06/fl07/fl08/fl09/fl10，未缩目标。七阶段 preflight/stage/prepare/stop/install/start/audit 各十台全部 exit=0，state.json 记录全部完成。

结论：**allRF=true，10 台 daemon active/running**。正式 audit 核验了运行 commit/MainPID、loaded 模块路径与摘要、完整安装 manifest、唯一 native 与 native SHA、当前服务绑定接口、实时控制返回的 HT40/LGI/MCS，以及 iw 实际 149/HT40/idx18。服务端实际 native argv 明确 `--grant-duration-ms 10 --guard-interval-ms 10`，直接执行 `/usr/bin/wfb_v6_uplink`。

后续只读快照窗口为 2026-11-08 07:42:34–07:43:04 UTC，间隔15秒共三次：十台均 active/running、MainPID 保持，实际 RF 均149/HT40/idx18，九个目标客户端均 READY，服务端 IDLE、无 active_job。fl03 不在发布目标中。十台 nvpmodel 均为15W。native SHA 始终为 `ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56`。没有任务、通信冒烟、门限调整、额外RF设置、换卡或第二批发布。

fl04 的独立弱链路风险保留：本轮 PID6871 在07:41:38 UTC记录一次“十秒内未确认有效心跳，累计缺失5次ACK，退回寻频自愈”，后续三次快照恢复 READY。原始日志还包含旧PID6305在发布前的一次告警，summary只统计本轮PID。既有约-82 RSSI为任务输入背景，本轮未重新测量RSSI。短窗口通过不证明长期无丢ACK；未降低任何门限。

|主机|当前绑定接口|daemon PID|native PID|audit|最终状态|
|---|---|---:|---:|---|---|
|fl00|wlxfc221c100119|30460|30514|通过|active / RF149|
|fl01|wlxfc221c500bfe|6987|7003|通过|active / READY|
|fl02|wlxbcec23372588|7355|7371|通过|active / READY|
|fl04|wlxfc221c300cbc|6871|6887|通过|active / READY，有恢复告警|
|fl05|wlxfc221c100157|6156|6172|通过|active / READY|
|fl06|wlxfc221c300cbb|12434|12450|通过|active / READY|
|fl07|wlxfc221c300e99|12582|12598|通过|active / READY|
|fl08|wlxfc221c500a88|11781|11798|通过|active / READY|
|fl09|wlx88e62852aae4|13434|13451|通过|active / READY|
|fl10|wlxfca386b38672|9722|9738|通过|active / READY|

发布前删除 server 临时 `/etc/systemd/system/wfb-fl-server-daemon.service.d/90-grant-diagnosis.conf`（文件只有 PATH override），执行 daemon-reload，删除前后PID23597一致；直到本轮stop/start才重启，详见 remove-temporary-path.json。最终 systemd Environment 中没有 diagnosis PATH。

helper 自动从运行服务 `/proc/MainPID/cmdline --interface` 获取 inventory，核对专用驱动接口存在，prepare冻结该绑定供install/start使用。删除历史fl05换卡特例。本轮未改变任何网卡分配。

installer 原有 `wfb_ng/fl/*.py` 已覆盖新增 cifar10_model.py，无须修改源码安装器。prepare 在停止前核验 uftp/uftpd/make、setuptools、全部Python语法和真实源码daemon导入；借助与安装器空白 wfb_ng/__init__.py 等价的 package path，避免源码旧顶层Twisted初始化。十台导入 server/client daemon 后 sys.modules 均无torch/torchvision；cifar10_model.py自身的torch导入只发生于显式调用模型模块。运行文件集合共39个（Python与静态html/css/js），安装后与源码逐项集合和SHA一致；加build_identity.json后manifest40个文件，wrapper启动与audit再次核验。

模型依赖仅使用 find_spec 检查可发现性，没有导入、安装或版本/ABI验证：fl00 torch/torchvision均存在；fl01/fl02/fl04/fl05/fl06/fl07/fl08/fl09仅torch存在；fl10两者均不存在。setup.py已声明cifar10 extras依赖torch/torchvision；这些缺项不影响daemon发布，不代表这些客户端具备执行CIFAR模型任务的能力。本轮没有执行模型。

工具审计：release入口固定精确十目标集合，阶段必须按顺序推进、任一目标非零即阻止下一阶段，helperSHA在每阶段验证；init使用git archive固定提交并核验native源码相对基线无差异。节点helper在prepare验证归档SHA/安全路径并备份旧安装、配置、dropins/native；全部prepare通过后才stop。报告生成器 verify-evidence.py独立核对七阶段70条记录、冻结工具摘要、归档SHA、运行身份、完整文件集合、实测RF/电源/服务与三次READY快照。两脚本py_compile和git diff --check通过。此次未修改冻结后的helper，也未修改runtime门限。

证据：`<phase>-<host>.json` 为每目标原始执行结果；postcheck为三次服务/API/iw快照及有界journal；summary.json为机器可读结论；本目录保留冻结工具、postcheck与证据核验脚本。source.tar按既有gitignore仅留本地，可由目标commit重建，摘要见state.json。远端源码、备份、manifest、loaded记录在 `/srv/wfb-ng-fl/releases/heartbeat-98dda459fb9a-20261108T073921Z/`。SHA256SUMS覆盖本目录证据，包含本地归档摘要；自身不计入。
