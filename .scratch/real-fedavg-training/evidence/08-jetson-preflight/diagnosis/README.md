# 08 两次失败只读诊断与启动时序核验

本轮仅执行读取：四端 systemd 有效配置、进程/线程、socket、网卡/USB、内核和角色日志、失败作业白名单归档、主 API GET，以及用户明确要求的全新 Python 进程导入计时。没有发训练/停止请求，没有 stop/restart、改射频、部署或改期限。全部依据已安装 e53de00 与 `/srv/wfb-ng-fl/deploy-e53de00/source` 冻结源码；未部署 07/10/11 未提交代码。

结论：第一次是已定位并修正的部署 drop-in 覆盖错误。第二次由服务器确切观察到 node 2 连续 10.000101632 秒没有有效心跳；现有证据支持“导入算法之前拆除 idle link，而新任务 link 在 torch 导入之后才启动”是需要优先复现的软件启动风险，但不能把 participant_offline 单独判为硬件故障。当前也观测到纯空闲阶段的心跳波动，可能存在独立的控制链问题，不能用一个解释包办两者。

**目前无需用户拔插、换线、重启或调整天线。**尚无故障窗口内 USB reset/-71/掉枚举的证据，且任务 native 链路实际收到过 node 2 grant。下一步优先做无硬件副作用的本地公开启动顺序复现，再做被动对齐观测；不通过放宽超时或重发作业掩盖问题。

## 第一次配置冲突是否修复

四端仍运行 e53de0076c6ed118d824a2d31261e75de3223333，安装文件摘要、实际包路径记录在 `flXX-readonly.txt`。当前 server PID 168778，客户端 PID 24102/15784/23591；NRestarts 均为0。这只能证明当前服务没有自动重启，不能据此证明过去每一条线程都一直无阻塞。

三个客户端 `DropInPaths` 现在只有 `/etc/systemd/system/wfb-fl-client-daemon.service.d/08-mnist.conf`。之前覆盖它的 `/run/.../radio.conf` 当前不存在；读取了 /etc、/run、/usr/lib 三处 drop-in 目录，实际 ExecStart 均包含：

```text
--mnist-data-dir /srv/wfb-ng-fl/mnist --work-dir /srv/wfb-ng-fl/client
```

首次作业 `web-6727da200d4e49b992134ea35989a4a1` 的各端归档 job_context 中 `local_config.mnist_data_dir=null`；第二次 `web-f317d54238864f64ab66164f151d3622` 已全部为 `/srv/wfb-ng-fl/mnist`，artifact_dir 也在 /srv。因此第一次问题不仅是配置文件看起来已修改，第二次实际子进程上下文亦确认修复。

部署脚本漏查 `/run` drop-in 是此前执行缺陷；本轮未再修改任何生效配置。不能保证未来其他维护工具不会重建同名 radio.conf，后续启动验证必须持续检查最终有效 ExecStart。

## 时钟问题：不能直接横向比较 wall time

fl00 当前 `NTPSynchronized=no`，三个客户端为 yes。通过先建立 SSH 会话、再在同一连接往返取时，避开 SSH 握手耗时，所得“客户端时间减服务器时间”当前最窄区间：

| 节点 | 当前时差区间 ms | 测量 RTT ms |
| --- | ---: | ---: |
| fl01 | 3146.554635～3148.654731 | 2.100096 |
| fl02 | 3063.762204～3065.758172 | 1.995968 |
| fl04 | 3076.009458～3077.631442 | 1.621984 |

原始五次采样保存在 `current-clock-offset-bounds.json`。这不是对故障时偏移量的回溯测量，不能拿当前精确3.064秒机械修正数小时前的事件。

历史恢复记录同样显示时差：server 的 RECOVERY_COMPLETED 为03:30:42.871，而 fl02 本机日志到03:30:45.672记录 recovery ACK。这些是不同机器/不同协议阶段，不能直接算成同一事件的精确时差，但足以警示不能假定同步。

因此，先前将 server 40.061 与 client 40.224 直接比较并据此说“裁决早于子进程、不是导入触发”，证据不足，已纠正。以下时间线分别按各自机器时钟列出；精确区间优先使用同机单调时钟或同机内核事件。

## 第二次故障精确事实

服务器 IPC 仍保留完整 recent_job，包括 liveness_events，补足了之前 Web 窄快照没有的单调时钟字段。见 `ipc-initial.json` / `timeline-facts.json`。

| 服务端事实 | 数值 |
| --- | --- |
| node2 最后有效 HB，server monotonic | 57333.316221696 |
| offline 被观察，server monotonic | 57343.316323328 |
| 同机 HB 静默长度 | 10.000101632 秒 |
| 配置离线阈值 | 10.0 秒 |
| node2 离线事件，server wall | 03:30:40.061 UTC |
| 作业 accepted started_at，server wall | 03:30:37.407416 UTC |
| 服务端恢复完成事件，server wall | 03:30:42.871092 UTC |

将最后心跳按同一服务端事件 wall-minus-monotonic 差换算，约为03:30:30.061 UTC；该换算不是独立记录的最后HB wall时间，精确证据是上面两个单调时钟值。不能用未来的心跳更新时间覆盖这条故障记录。

fl02 自身时间轴：

| fl02 本机 UTC / epoch记录 | 事件 |
| --- | --- |
| 03:30:40.224 | daemon IDLE → PREPARING，代码立即发送状态心跳 |
| 03:30:40.470882 | 内核记录旧 native socket 离开 promiscuous，支持 idle link 被拆除 |
| 03:30:40.632 | 派生 RoleService PID/PGID 15838 |
| 03:30:42.914832 | 内核记录新 native socket 进入 promiscuous |
| 03:30:42.939136 | uftpd启动，表明任务链路与 transport 已推进到启动阶段 |
| 03:30:43.140 | daemon terminate PGID15838；同刻uftpd收SIGTERM |
| 03:30:45.374～45.394864 | 角色回收，重建idle link，新native进入promiscuous |
| 03:30:45.672～45.673 | JOB_RECOVERY_ACK、ABORTING → IDLE |

fl02旧、新native的同机内核事件间隔为 **2.443950秒**。promiscuous事件表示native无线socket生命周期，不直接等同TUN创建成功时间；但与源码和RoleService日志一起，支持存在拆旧建新的实际通信空窗。子进程派生到新native进入promiscuous为2.282832秒；派生到uftpd启动为2.307136秒。原始同机journal为 `fl02-failure-window.txt`。

不能把“03:30:43.140被abort”视为服务器在该时刻才判离线：裁决、下行终态广播、客户端处理和进程终止是不同步骤，且有跨板时差。

## 冷进程导入的现场计时

用户授权后四端各启动一个全新 `/usr/bin/python3 -B` 进程；导入前验证torch与mnist不在sys.modules，直接执行 `importlib.import_module('wfb_ng.fl.mnist')`。从/tmp导入安装包，线程环境与服务一致，未初始化CUDA、未读取IDX、未跑GPU、未写字节码、未清系统page cache。

| 节点 | import mnist（含torch传递导入）秒 |
| --- | ---: |
| fl00 | 2.4364867199910805 |
| fl01 | 2.179403957008617 |
| fl02 | 2.2034487150012865 |
| fl04 | 2.15945407601248 |

四端 `cuda_initialized=false`；mnist安装文件SHA-256均为 `28c5e1a545f5bf5333f8e52b1bdbeb89a5eee8ac7614f334c71a037c3b904dd7`。见 `cold-process-import-results.json` 和逐端输出。

这是**进程冷导入**，不是保证磁盘冷缓存。现场fl02 2.203秒与历史子进程到native约2.283秒相符，但不能声称已精确测出历史torch耗时。当前实测均不超过10秒；这不排除旧心跳已经接近过期，额外2秒断链使其越过阈值。

历史日志没有torch import begin/end、GPU准备begin/end或IDX读取begin/end打点，也未取得client TASK_READY成功报告。因此无法报出历史torch/GPU/数据检查的各自精确耗时，不能将缺日志理解为数据已坏。`client_entry` 顺序是GPU准备后读取IDX，然后report_prepared；本轮没有重新执行该准备。

## 启动顺序、控制线程与端口

e53de00冻结源码确认：

1. ClientDaemon.start_job 调 `_stop_idle_link()`，终止旧native并teardown TUN，再sandbox.start派生子进程。
2. RoleService `_main` 先load_algorithm（`service.py:528`），导入mnist和torch；然后load_role_service，`service.start()`在531行才启动任务native/TUN、组播路由、uftpd。
3. 算法线程在service.start之后才启动。GPU/IDX预检不是创建新链路的前置，但torch模块导入是。
4. 控制面广播线程将作业启动交给startup线程，不应由算法直接占用唯一控制接收线程；控制面daemon进程保持存在。可是心跳即使由独立线程发送，也依赖同一TUN/无线native，拆链期间无法因为线程还活着就保证报文送达。
5. `_send_heartbeat` 在client控制锁内sendto/recvfrom，ACK等待默认0.23秒，IDLE间隔5秒，ACTIVE间隔2秒；仅看等待状态不能证明长期死锁。当前客户端daemon每个3个线程，一条poll、其余futex或poll；均未观察到进程退出。故障时的逐线程Python堆栈没有采集，不能事后补造。

端口证据：当前client UDP9000由对应daemon独占，idle native loopback9200由对应native独占，控制上行使用daemon临时源端口发往server9001；没有当前双daemon占用。第二次任务native日志显示 fl01/02/04 的loopback端口分别51501/51407/43047，没有占idle9200；原始任务link_args不固定radio-control-port，走native动态端口。uftpd使用1044，区别于控制9000/9001。没有 EADDRINUSE 或 bind失败日志，因此尚无同端口冲突证据。

角色日志至少证明fl02任务native曾收到 `grant_accept sequence=5192 duration_ms=120`，并记录control/data frame；fl01/fl04有first_declare。故不能把整个失败解释成“USB/网卡完全不工作”。它们均不证明心跳上下行端到端在整个准备窗口连续成功。

## 无作业时的实时稳定性

以1秒间隔只读GET IPC，采集61个样本，实际采样窗口为 **07:06:10.072～07:07:10.327 UTC**。期间无active job，也不发控制POST。

| 节点 | OFFLINE样本数/61 | 最大最后HB年龄秒 |
| --- | ---: | ---: |
| 1 | 60 | 69.28 |
| 2 | 0 | 9.36 |
| 4 | 37 | 134.38 |

node2在窗口内还从READY短暂到CONNECTING再回READY；node4从OFFLINE到CONNECTING再回READY。证明空闲控制链当前也会波动，与训练/GPU无关。当前native遥测出现grant调度和部分节点等待grant，不能仅由这些累计计数归因为调度bug或天线问题。

最后一次GET：server IDLE、无active job、上一作业recovery ready；node1 READY、node2 OFFLINE（HB年龄53.17秒）、node4 READY。此为采样瞬间，不能再宣称全员持续READY。原始 `ipc-final.json` 保存完整事实。

## 射频、USB与内核窗口

四端读取空口均为monitor、157、40MHz、中心5795MHz、驱动报告-14dBm；RTL网卡均在tegra-xusb的5000M分支。管理无线接口在另一信道，不当作空口读回。

故障窗口只读内核日志以epoch精确限定03:29:30～03:31:40 UTC；fl02只看到上述promiscuous离开/进入，没有该窗口内USB reset、disconnect、error -71或U1/U2错误。其他板亦未发现该窗口的USB故障；初次读取尾部的更早启动/掉插记录明确是历史记录，不拿它们证明本次硬件坏了。服务器故障时间窗口daemon journal无新增事件，API中的liveness事实仍保留且更精确。当前NTPSynchronized状态不能证明故障时的偏移稳定。

## 最简下一隔离实验（本轮未执行）

首选与 diagnose08-startup 子代理的公开本地复现合并：用e53de00 RoleService启动路径，stub掉native/TUN和算法业务，只在load_algorithm与link.start边界记录单调时钟，真实新进程导入mnist；断言link可用之前是否存在先拆后导入窗口。该复现可证实启动顺序因果，无需再使用无线或修改生产timeout，也不会用另一个轮次掩盖现场失败。

随后最小现场隔离应是**纯空闲的双端心跳被动观测**：固定当前服务/40MHz配置，分别在client TUN发送侧与server TUN接收侧记录9001心跳、回包时间及序列，和native grant/declaration日志关联，使用同机单调时钟+可匹配报文序列跨端对齐。不发送探活数据、不启动任务、不清缓存。当前设备未发现tcpdump/tshark/dumpcap，不能假装已经取得pcap；先由主线选择现有只读捕获能力，再执行该独立观测。判据：client没产生HB→控制线程/状态；client有发server没收→native/调度/无线方向；server收但client无ACK→回程；两端都通但liveness判离线→注册表/时间状态逻辑。

若本地复现确认启动顺序问题，修复应由主线提交08代码，保持通信路径的生命周期与算法导入隔离；不能私改安装源、扩大离线期限或复用旧TUN绕过生命周期检查。只有纯空闲链路稳定且修复提交被明确冻结后，才另行恢复正式两轮及训练中stop验证。

本轮不存在必须依赖用户实体操作才能继续诊断的事项。只有今后证据出现USB掉枚举/总线复位/供电异常，或软件路径排除后需现场物理对照时，再说明具体节点、设备和操作目的。

## 原始证据

- `flXX-readonly.txt`：当前unit/drop-in、进程线程、socket、iw/USB、安装身份及两次失败作业小文件原文。
- `flXX-failure-window.txt`：限时窗口内核/journal及当前native日志尾部。
- `ipc-initial.json`、`ipc-final.json`：完整recent_job、liveness最后HB、恢复报告与当前节点事实。
- `timeline-facts.json`：同机精确时序及跨机时钟限制。
- `cold-process-import*.py/json`、`flXX-cold-process-import.txt`：新进程导入测量。
- `current-clock-offset-bounds.json`：当前跨板wall clock差值区间。
- `idle-heartbeat-61s.json`、`idle-heartbeat-summary.json`：纯GET空闲采样。
- `committed-code-order.txt`：所审e53de00启动顺序源码行及摘要。

只读脚本没有覆盖历史故障，既有formal-e53de00证据保持不变；本目录仅小型文本，SHA256SUMS用于复核。
