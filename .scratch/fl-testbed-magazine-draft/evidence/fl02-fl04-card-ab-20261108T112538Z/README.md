# fl02／fl04整套网卡与天线互换

## 操作与统一条件

用户确认已完成整套互换。实际映射：fl02使用`fc:22:1c:30:0f:04`（原fl01卡、互换前在fl04）；fl04使用`bc:ec:23:37:25:88`（互换前在fl02）。两端标准停服后仅修改显式接口绑定再启动，node/TUN配置、软件、149/HT40/Long GI/索引18、MCS4、FEC8/14及15W不变。完整当前进程与接口唯一差异见`post-swap-manifest.json`。

本轮fl02的USB速率5000Mbit/s，fl04为480Mbit/s；记录实际USB路径，不伪称两者相同。USB速率不能直接等同故障，fl10同样480Mbit/s仍有通信。软件版本f376a1f，native摘要ad3340e5；全十一端严格运行身份快照`runtime-snapshot-20261108T113232Z/summary.json`只更新运行接口身份，不重新部署软件，历史release文件未改。

## 两分钟空闲观察

`idle-observation/REPORT.md`：24个固定节拍样本，所有客户端管理SSH成功；没有一次全十端综合通过。fl02两次新增寻频，fl04的实际149/40MHz为24/24但READY为0/24；其余多端也有寻频。fl04最后RX_ANT陈旧约472秒，不能拿它声称当前RSSI正常。

观察没有证明异常清楚地跟随旧fl04组件转移，不能据此确诊原卡损坏。换入此前可用卡后fl04仍不能持续通信，板端接收路径需要单独排查。

## 单次20秒被动窗口

`capture-20261108T114044Z/METHOD.txt`及原始pcap：使用与native一致的linkid／下行stream过滤，fl02匹配WFB下行1790帧，fl04为0；服务端TUN收到node2心跳9条、node4心跳0条。fl04本机TUN仍生成心跳，其reported channel不可代替实际iw。只记录本机monotonic窗口，不依赖跨板墙钟对时；只有前后信道快照，未统计pcap drop，因此定性为接收硬件／USB／驱动路径候选，不直接认定物理网卡损坏。

fl04内核记录旧卡拔出及新卡以USB High Speed接入，之后没有找到同类USB重置／断开正证据。NetworkManager对该专用接口为unmanaged，接口UP，管理Wi-Fi独立且正常。native采样线程在poll等待，不能将其称作死锁。

## 唯一一次受控驱动恢复

`driver-rebind-20261108T114825Z/结果说明.md`：只停止fl04客户端、对精确专用接口`1-2.3:1.0`解绑／绑定`rtl88xxau_wfb`驱动各一次，再标准启动。驱动操作成功，但持续通信验收失败；30秒sysfs RX增加479，native仅前约4秒有接收、随后零包，未READY。未重启其他节点、管理网、整个USB hub或板子。

USB power/control=on、runtime_status=active、rtw_power_mgnt=0、rtw_enusbss=0，不支持已触发autosuspend的判断；没有force USB3或改变省电参数。

## 独立软件缺陷

排查期间发现并五次真实run_client回归复现：TUN队列为空但partial FEC过期、授权已失时，不再次申请授权，旧块须等新业务或人为授权才封闭。修复5e6ff30和1cd6264将partial FEC当作待处理工作；包括首次检查时已过期的确定性分支、1000ms日志间隔下及时唤醒、多轮生命周期、无越权TX、空无FEC不申请授权及无忙转。独立review通过，冻结源码构建和全部native回归通过。证据见`../empty-queue-fec/README.md`。

此软件缺陷不能解释为已经证实了fl04独立monitor零帧的原因。1cd6264及同一新native已统一发布十一板，实际进程SHA均为`44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`，15W、FEC8/14及射频审计通过。发布后同条件24×5秒观察见`post-empty-fec-idle/REPORT.md`：fl04仍0/24 READY、无当前PID fresh RX，其余九端窗口HUNT合计24→14，仍未稳定；仅描述短窗口差异，不作统计或因果结论。

用户随后授权在仍不可用时剔除fl04，固定其余九客户端继续。本轮已标准停止fl04客户端服务（inactive/MainPID=0），不再继续驱动重绑、拔换或购买网卡。新集合是fl01/fl02/fl03/fl05～fl10，含03排04；新九端须重新验收，不能承接旧十端或旧“排03含04”九端的通过。新批次见`../nine-without-fl04-20261108/`。
