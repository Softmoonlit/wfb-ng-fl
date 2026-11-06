# 六在线节点统一发布Long GI基线

## 批次与范围

用户恢复fl02和fl04供电后，新建六目标批次，未缩减目标或绕过失败：fl00/fl01/fl02/fl04/fl05/fl06。fl07/fl08/fl09/fl10按用户确认仍断电，本轮未连接。

冻结源码提交：c6659488f64976185ffe02a7c856f7fd8c821ee0（包含a63037a的Long GI/索引契约、ef81dd3的集群配置及此前心跳/FEC修复）。源码归档摘要、helper摘要和阶段完成状态见state.json。原五目标批次保留历史记录，不再继续。

目标集合变更已单独提交后才初始化发布；客户端与服务端一同停服安装，逐阶段全员通过后推进。preflight/stage/prepare/stop/install/start/audit七阶段全部六台成功。

## 运行态核验

- 六台实际加载Python模块路径和摘要一致，build_identity均为冻结提交。
- native未重建，运行中/proc/PID/exe均为SHA256 `ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56`。
- 运行中native参数不含--radio-short-gi；只读CMD_GET_RADIO逐台实读short_gi=0、bandwidth=40，server MCS3/client MCS4；请求和响应原始十六进制保存在audit文件中。
- iw逐台实读channel149、40MHz、txpower -18.00 dBm（驱动索引18的标签，不代表标定物理输出）。
- 六台重新核验15W mode0、CPU上限1497600kHz、GPU上限612000000Hz、EMC上限2133000000Hz，全通过；详见*-power-limits.json。
- 原安装、配置及drop-in备份位于各端本release目录backup；未改驱动或依赖。

## 启动与稳定窗口

after-long-gi是启动观察，保留全部24样本：fl01为1 OFFLINE、2 CONNECTING、21 READY，其余四台24/24 READY。该窗口不能称为全员稳定通过。

确认全员READY后，保存stable-entry-status.json并新开stable-long-gi窗口：五客户端服务端投影均24/24 READY，所有抓包进程正常结束。

但该窗口中，fl01抓到16份IDLE及14份HUNTING请求，fl05抓到18份IDLE及2份HUNTING请求；当前daemon PID日志也确认存在十秒无有效确认后寻频。服务端READY采样不能代替客户端无寻频验证，**完整待命稳定门槛未通过**。

| 客户端 | 窗口内请求数（按boot/session/时间戳） | 配对确认ACK | 230ms内 | 配对RTT中位 | 最大 |
| --- | ---: | ---: | ---: | ---: | ---: |
| fl01 | 30 | 8 | 6 | 182ms | 2732ms |
| fl05 | 20 | 15 | 5 | 376ms | 3321ms |

上述请求包含握手探测，未配对不能全部当作丢包。进一步按boot/时间戳对齐两端：fl01的16份IDLE中服务端捕获8份，fl05的18份IDLE中捕获14份；服务端对各自已捕获IDLE全部发送confirmed ACK。服务端收包到发ACK中位约3ms，最大约12ms，说明本窗口的主要尾延迟不在服务端处理函数。结果见stable-long-gi/server-correlation.json。

journal文件包含最近140行，可能含旧PID；判定本轮寻频只使用audit对应当前daemon PID（fl01=5011，fl05=4746）。未据此修改心跳期限、功率或寻频规则。

抓包均使用本机单调时钟配对boot_id、session及请求时间戳，不跨板比较墙上时间。确认ACK配对与230ms预算分开统计；存在迟到或未配对不能直接推断网卡故障。抓包窗口40秒、状态窗口120秒，边界以外的回执可能不在捕获范围。

## 结论边界与后续

本批确认Long GI和索引18在六在线节点实机生效。当前诊断信道149，不是正式目标157验收。未运行FL训练、聚合、评估或等量文件通信闭环；不替代九客户端10分钟待命及三组各两次纯通信闭环。

fl07..10恢复稳定供电后须统一发布并核验，不能仅凭进程active判断同版本。待固定候选全员完成基础通信检查，再冻结正式参与集合并进入载荷核验、角色独立功率实现和通信选参。
