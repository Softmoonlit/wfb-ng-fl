# 发布后固定十客户端空闲观察

24×5秒计划，完成 24 样本；主采集 119.015s（150s硬上限）。fl00初始身份先以本机sudo采集，随后10客户端初始身份并行采集，全部先于首样本。

原始检查stdout/stderr与server registry snapshot逐样本保存；READY与actual149+40MHz分别计数。HUNT口径与前次一致：当前daemon PID journal中 HUNT 或“退回寻频自愈”消息；窗口从各端初始monotonic至最终journal采集。管理失败或无actual输出记unknown，不称RF失败。

|node|daemon/native PID|READY /24|actual149+40 /24|combined /24|管理异常/unknown|HUNT启动至结束|窗口HUNT 前→后|USB Mbps|
|---|---|---:|---:|---:|---|---:|---:|---:|
|fl00|91372/91533|—|—|—|—/—|0|0→0|5000|
|fl01|15066/15118|23|22|22|0/0|4|3→1|5000|
|fl02|16734/16786|23|24|23|0/0|7|2→2|5000|
|fl03|5383/5434|18|19|16|0/0|12|2→3|5000|
|fl04|29737/29784|0|24|0|0/0|0|0→0|480|
|fl05|14384/14431|23|23|23|0/0|8|2→0|5000|
|fl06|19426/19477|23|23|22|0/0|7|3→1|5000|
|fl07|19700/19750|22|22|22|0/0|6|3→1|5000|
|fl08|20058/20109|19|19|19|0/0|10|3→3|5000|
|fl09|20796/20848|21|20|19|0/0|9|2→2|5000|
|fl10|18410/18456|24|22|22|0/0|6|4→1|480|

除fl04以外9客户端窗口HUNT合计：前次 24，本次 14，变化 -10。逐端减少 5，持平 3，增加 1。此为两个短窗口的描述性对比，不能据此作统计显著性或软件/硬件因果结论。

11端PID前后均一致：True。初始11端运行native SHA256均为44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2。无法解析的检查样本：[]。

fl02 当前native最新RX_ANT（先按当前进程starttime排除追加文件中旧PID日志；age为最终采集结束时刻减记录monotonic，within_observation表示是否窗口内新记录）：{"1": {"monotonic_ms": 20100266, "radio": "5765:3:40", "values": "23:-76:-66:-64:1:11:17", "age_at_capture_end_seconds": 0.025, "within_observation": true}, "0": {"monotonic_ms": 20100266, "radio": "5765:3:40", "values": "23:-56:-55:-54:5:15:23", "age_at_capture_end_seconds": 0.025, "within_observation": true}}
fl04 当前native最新RX_ANT（先按当前进程starttime排除追加文件中旧PID日志；age为最终采集结束时刻减记录monotonic，within_observation表示是否窗口内新记录）：{}

未改软件、service、参数或硬件；未向节点发送文件，未commit。各端初始/最终cursor、PID与原始日志均保留。
