# 当前只读盘点（2026-11-08 06:40 UTC）

范围：fl00、fl01、fl02、fl04、fl05、fl06、fl07、fl08、fl09、fl10。未连接 fl03。fl00 本机读取，其余并行 SSH，每会话总超时 10 秒，远程 timeout 8 秒。未停服或改配置。

结论：部署混版；完整九客户端拓扑尚不具备统一冒烟前提。fl05 服务 active 但无 native，配置指定网卡与实际插卡不符。fl07–fl10 为旧入口/旧磁盘版本，无法提供与 MainPID 绑定的 loaded manifest。9 个已运行 native 的实际 exe SHA256 全相同。

|主机|service/MainPID|loaded commit / 版本证据|native PID|专用接口|iw 实际状态|
|---|---|---|---|---|---|
|fl00|active / 1730|c6659488f649（loaded PID匹配；磁盘manifest一致）|2301|wlxfc221c100119|monitor / ch149 HT40 / -18.00 dBm|
|fl01|active / 858|c6659488f649（loaded PID匹配；磁盘manifest一致）|1467|wlxfc221c300e99|monitor / ch149 HT40 / -18.00 dBm|
|fl02|active / 857|c6659488f649（loaded PID匹配；磁盘manifest一致）|1488|wlx88e62852aae4|monitor / ch149 HT40 / -18.00 dBm|
|fl04|active / 855|c6659488f649（loaded PID匹配；磁盘manifest一致）|1457|wlxfc221c300cbb|monitor / ch149 HT40 / -18.00 dBm|
|fl05|active / 851|c6659488f649（loaded PID匹配；磁盘manifest一致）|无|wlxfc221c100157|managed / 无信道 / 18.00 dBm|
|fl06|active / 868|c6659488f649（loaded PID匹配；磁盘manifest一致）|1492|wlxfc221c500bfe|monitor / ch153 HT40 / -18.00 dBm|
|fl07|active / 856|loaded 未证实；磁盘5443ca226582|1451|wlxfc221c300cbc|monitor / ch149 HT40 / -14.00 dBm|
|fl08|active / 859|loaded 未证实；磁盘5443ca226582|1451|wlxfc221c500a88|monitor / ch157 HT40 / -14.00 dBm|
|fl09|active / 851|loaded 未证实；磁盘5443ca226582|1416|wlxbcec23372588|monitor / ch153 HT40 / -14.00 dBm|
|fl10|active / 906|loaded 未证实；磁盘5443ca226582|1521|wlxfca386b38672|monitor / ch153 HT40 / -14.00 dBm|

native 实际 `/proc/PID/exe` SHA256：`ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56`。

新版 loaded commit：`c6659488f64976185ffe02a7c856f7fd8c821ee0`（fl00/01/02/04/05/06）。旧版磁盘 build identity：`5443ca22658202805317a9a974c261cde7c751b9`（fl07/08/09/10），相对新版 manifest 有 8 个文件不同；这些节点没有 release wrapper 的 loaded 证明，不能仅凭磁盘声明运行中 Python 的精确 commit。

fl05 cmdline 指定 `wlxfc221c500a88`，实际专用接口为 `wlxfc221c100157`，managed、无信道，未发现 native。fl08 当前持有 `wlxfc221c500a88`。fl06/09/10 当前主信道153、中心5755，fl08主信道157、中心5795；属于本次快照，未验证稳定性或收发连通。

10 台 nvpmodel 均 15W / mode0，配置均 `/etc/nvpmodel/nvpmodel_p3767_0003_super.conf`。CPU online 0–5，实际 min/max 729600/1497600 kHz；GPU min/max 306000000/612000000 Hz；EMC nvpmodel cap 2133000000 Hz，所有 EMC mrq_rate_locked=0。BPMP debug max_rate 3199000000 是时钟能力上界，与 nvpmodel cap 分开记录。所有 nvpmodel.service Result=success / ExecMainStatus=0。

证据：每主机 `.json` 含 service、cmdline、完整 manifest/loaded（存在时）、native exe hash、iw、nvpmodel 查询及 sysfs；`*-transport.json` 保留原始 stdout/stderr/耗时。`*-supplement.json` 保留异常/旧节点磁盘哈希及最近日志。`scope.json`、probe/collector 脚本记录采集方法。

本子任务仅盘点，不执行完整拓扑冒烟或预实验。
