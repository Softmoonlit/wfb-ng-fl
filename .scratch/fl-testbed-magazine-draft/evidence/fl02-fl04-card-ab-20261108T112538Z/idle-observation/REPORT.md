# 固定十客户端互换后只读观察

主采集耗时 118.111s，24/24 样本；每5秒从固定 monotonic epoch 调度。全部完整 stdout/stderr、registry_snapshot 已保留。

READY 为现有检查脚本输出，actual149 单独依据 iw channel149+40MHz；combined 为脚本综合通过。HUNT 按当前 daemon PID 日志中 HUNT 或“退回寻频自愈”消息计数。CPU 从观测前后 /proc/PID/stat 增量计算，100%=一个CPU核。

|node|daemon/native PID|READY/24|actual149/24|combined/24|management error|HUNT 启动至结束/窗口|USB Mbps|CPU daemon/native %|
|---|---|---:|---:|---:|---:|---:|---|---|
|fl00|77712/77897|—|—|—|—|0/0|5000|1.77/0.88|
|fl01|12870/12919|23|24|23|0|9/3|5000|3.92/0.41|
|fl02|14181/14237|23|23|22|0|9/2|5000|4.02/0.39|
|fl03|856/1444|24|21|21|0|9/2|5000|3.94/0.27|
|fl04|22394/22452|0|24|0|0|1/0|480|4.75/0.09|
|fl05|11852/11898|20|21|19|0|8/2|5000|3.85/0.49|
|fl06|17104/17150|23|22|21|0|8/3|5000|3.92/0.41|
|fl07|17407/17453|22|22|20|0|13/3|5000|3.9/0.26|
|fl08|16546/16593|13|14|12|0|9/3|5000|3.78/0.32|
|fl09|18013/18057|21|22|19|0|9/2|5000|3.99/0.34|
|fl10|16229/16275|23|22|22|0|9/4|480|3.94/0.41|

11端 daemon/native PID 观测前后均未改变。fl00 SSH 主机密钥失败，原失败记录保留；fl00 用本机 sudo 只读补采 initial/final，initial 补采晚于窗口起点，具体 monotonic 见对应文件，不能称所有11端均在首样本前完成基线。fl00 final 本机补采晚于主 capture-end，具体时刻独立记录；客户端最终采集位于主118.111秒范围内。

native 日志为追加文件，原始尾部含旧进程记录；current-native-rx-ant 文件已按当前 native /proc starttime 过滤。RX_ANT 尾部可能陈旧，summary.json 的 age_seconds 给出新鲜度，不能当作当前 RSSI。

仅描述观察：fl02、fl03、fl08 均有实际偏离149的样本及窗口内 HUNT；fl04 实际149不等于通信READY。此窗口不能据此推断症状是否由原04卡转到02。

fl02 当前native最后RX_ANT：{"1": {"monotonic_ms": 18207296, "radio": "5745:3:40", "values": "99:-54:-52:-52:8:13:18", "age_seconds": -0.055}, "0": {"monotonic_ms": 18207296, "radio": "5745:3:40", "values": "99:-50:-49:-48:10:13:19", "age_seconds": -0.055}}
fl03 当前native最后RX_ANT：{"1": {"monotonic_ms": 808978, "radio": "5745:3:40", "values": "88:-70:-54:-52:-6:11:15", "age_seconds": -0.008}, "0": {"monotonic_ms": 808978, "radio": "5745:3:40", "values": "88:-56:-50:-50:3:12:16", "age_seconds": -0.008}}
fl04 当前native最后RX_ANT：{"1": {"monotonic_ms": 17794126, "radio": "5745:3:40", "values": "39:-56:-55:-54:13:22:28", "age_seconds": 471.696}, "0": {"monotonic_ms": 17794126, "radio": "5745:3:40", "values": "39:-56:-55:-52:14:22:27", "age_seconds": 471.696}}
fl08 当前native最后RX_ANT：{"1": {"monotonic_ms": 18291955, "radio": "5765:3:40", "values": "5:-50:-50:-50:9:12:15", "age_seconds": 0.361}, "0": {"monotonic_ms": 18291955, "radio": "5765:3:40", "values": "5:-50:-50:-50:10:12:15", "age_seconds": 0.361}}

fl00 本机最终补采完成距 capture-start 129.053s，仍小于150s硬上限。RX age 以远端最终采集开始时刻为参考，极小负值表示日志在采集过程中继续追加。
