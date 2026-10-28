# 149 基线常驻心跳诊断与修复验证

## 最新结果：心跳容错修正后门禁通过，两轮训练成功

四端已统一运行 `c62cde266ef509d605c8c923b0f4fa35e42fc4dd`。空闲心跳默认约2秒；230ms仍只限制单次探测，客户端按至少10秒持续无有效确认才寻频，Server仍静默超过10秒判OFFLINE。回执身份和射频事务保护不放宽。控制READY/GRANT固定MCS0，方向数据MCS3/4与149/HT40+/14档不变。

[心跳容错修正](heartbeat-liveness/README.md)通过192项测试、mypy、双轴审查及四端统一制品/射频核验。120.47秒121份采样全员READY、无寻频，最大心跳年龄fl01/fl02/fl04分别6.42/6.32/9.72秒；仍不能解释为零丢包或长期稳定性保证。

随后经网页入口完成作业 `web-7383444963c54f558b721675d592b43b`，固定目标[1,2,4]、两轮真实MNIST；执行成功、完成两轮，三客户端确认回到READY，无liveness事件。报告末轮accuracy=0.9393、loss=0.2099322291；独立FP64重算两轮聚合及官方10000测试样本重评均通过，见[独立复核报告](heartbeat-liveness/gpu-smoke-two-rounds/independent-review/独立复核报告.md)。08仍需核对正常停止等完整清单，09/12/13未据此结项，不计入三次五轮正式验收。

## 历史结果：607c064发布后稳定性门禁失败

四端已部署提交 `607c064150b661d1e8a568e29a101998ac06b0b7`（包含 `c86e54a` 控制修复和发布脚本修正）。运行中 native SHA-256 均为 `1e6ac8eda88b8dcd2564ddb0b7e87629d6447a3e71e05e328a4c95f1219696b1`，实际 Python 加载身份一致；四端 `iw` 均核验 149 / HT40+ / -14.00 dBm。发布证据见 `release-607c064150b6-20261028T115651441253Z/manifest.json` 与逐端 verify-runtime 输出。平台预置白名单仍为 1..10，实际参与者为 1/2/4，两者分别记录。

发布前 fl00 的 libpcap 依赖经用户指定的网关 20112 代理，从 `.22.04.1` 升级为与其他三端一致的 `.22.04.2`，详见 `fl00-libpcap-upgrade/README.md`。首次发布因管理 SSH、locale、显式环境和产物权限中止，均在停服前；最终成功发布才进入全部停服/安装/启动屏障。依赖摘要差异不等于已经证明 ABI 不兼容。

**120 秒全员 READY 门禁失败**：`stable-120.jsonl` 在约 25 秒记录 fl04 HUNTING/CONNECTING，`stable-120-console.log` 保存失败。没有启动四机训练，没有启用 HT20。

新版本的四端 85 秒被动抓包见 `post-release-failure/`。fl01/fl02 各有 16 条心跳和 16 个对应 ACK；fl04 有 45 条心跳和 45 个对应 ACK，但其中若干 RTT 达 5.655、7.745、9.686 秒。`ack-summary.json` 用同一客户端单调时间计算 RTT，并以 payload SHA 和心跳身份配对服务端收发；Server 收包到回复约 1–26ms。两端分别计算事件间隔后取差，额外延迟集中在上行，不能将跨板单调时间直接相减作为单向时延。

进一步执行相同版本/参数的单 fl04 常驻对照：暂停 fl01/fl02 服务，120 秒采样结束后通过 finally 恢复。见 `single-fl04-resident/operations.jsonl`、`restoration.json` 及 `single-resident-fl00.jsonl` / `single-resident-fl04.jsonl`。在约 61.2 秒 fl04 被判 OFFLINE，约 62.2 秒恢复 READY；两个完整覆盖的心跳 timestamp `2612332`、`2652748` 仅见于 fl04 TUN，未见于 Server TUN。捕获末尾 `2688134` 属于边界，不计丢失。该窗口有效 GRANT 持续增长，FEC 忙循环诊断计数为零；不支持以三客户端竞争、Server ACK 软件阻塞或已假设的空队列 GRANT 边沿解释全部故障。

当前有证据支持的是 fl04 上行交付丢失/延迟以及寻频放大，尚未将其定位到具体发射、驱动、空口或接收环节，不能宣称已证明网卡损坏。两个已确定的协议/换频缺陷已修复，但现场稳定性仍未闭合；08、09、12、13 保持未完成。对照后 fl01/fl02 服务已正常启动，追加只读状态确认三参与节点均恢复 READY；该瞬时结果不替代稳定门禁。


## 历史对照：fl04 18 档常驻心跳

用户进一步指出正常丢包容忍与超时关系：当前空闲心跳约5秒、Server静默10秒判OFFLINE、单次ACK等待230ms、连续两次miss进入HUNTING。一次上行丢失即可使两次有效心跳间隔超过10秒，属于全节点共用的低容错配置；近期三个客户端并行的窗口仅fl04反复失稳，不能据此认定该设计对其他节点无风险。下一次先单独将空口READY/GRANT固定MCS0，保留TUN心跳/ACK方向数据MCS和所有期限，四端统一发布对照，以免混淆MCS与时间参数的收益。该实验未完成前不声称问题解决。


随后按用户要求，仅临时提高 fl04 的实际驱动功率到18档，观察120秒；约36秒仍进入HUNTING/CONNECTING并恢复。22次iw读回均为-18.00 dBm；结束已恢复14档。85秒抓包25条请求最终都有对应ACK，但8条超过230ms，最大约4.349秒。单窗口不能确认相对14档的统计收益，确定的是仍未通过稳定门禁。详见 [18档实验](power18-20261028T122857Z/README.md)。当时控制帧与同方向数据帧共用MCS：上行4、下行3，并非MCS0；随后已单独将READY/GRANT固定MCS0，见[对照结果](control-mcs0/README.md)。

## 现场与配置

用户确认四板固定采用 `149 / HT40+ / 14 档 / 下行 MCS3 / 上行 MCS4 / UFTP 15000 Kbps`。四端已通过标准停服、修改启动配置和重启服务切换至该基线；没有修改注册表或伪造 READY。切换时运行的 Python 提交仍为 `701963ac254261c31c56a074d06966a9217346f6`，canonical native 仍为既有统一版本。`apply_baseline.py`、各端 `*-config-before.json` 和 `baseline-identities.json` 保存变更脚本、原始配置与运行制品身份；实际射频采样见被动捕获中的 `snapshot.radio`。

149 上仍复现 fl04 由短暂就绪进入 HUNTING/失联，不能仅靠换信道宣布修复。此次测试不涉及训练，不能作为工单 08/13 通过证据。

## 85 秒被动证据

`capture-tun.py` 仅通过 AF_PACKET 克隆 TUN 数据，未设置混杂模式、绑定业务端口或发送测试心跳。四端 `*-passive.jsonl` 均有正常结束记录，socket drop 为 0；`passive-summary.json` 保存摘要。

- Server：18 个状态快照中，fl01/fl02 均 READY，fl04 均 OFFLINE；未收到 node4 TUN 心跳。
- fl04：在本机 TUN 观察到 252 条心跳，未看到发给 `10.80.0.14` 的心跳 ACK。它抓到的 ACK 目的 IP/端口属于 fl01/fl02，不能当作自身回执。
- native：fl04 的有效授权计数在该窗口保持 `GRANT_SEQ=3:8`；后续日志可见约 126 秒后才重新获得授权。`fl00-native-current.log`、`fl04-native-current.log` 保存截段。
- 21 次 READY 重声明与约 2 秒寻频形成相位耦合。按最近心跳的信道估计，16 次在 157 相位、5 次在 149/retune 邻域；`ready-channel-correlation.json` 只表示相关性，后 5 次不能当成确切物理信道，不能宣称每个 READY 都发错频道。

## 确认的代码机制

1. native 在发出 READY 后本地锁存“声明中”，未获 GRANT 时固定 4 秒重试；Python 换频不通知 native。这限制了每个候选信道上的重新入队机会，不保证寻频能覆盖恢复所需的上行声明。
2. 旧 ACK 没有请求身份。公开 `hunt_once([149,157])` 的真实 loopback UDP 用收包顺序门控，在 157 探测发出后才返回 149 的旧回执；旧实现实际得到 `(True,157,IDLE)`，预期为 `(False,None,HUNTING)`。`red-ack-identity.txt` 保存确定性 RED。这证明误锁机制，不证明它是每次现场失联的唯一触发原因。
3. 相关回归还暴露了 liveness 停止通知可能先于条件等待、从而令同步 stop 卡住的竞态。早期超时与栈保存在 `control-plane-timeout.txt`、`control-plane-stop-stack.txt`，不以将协议测试移出后台服务类掩盖生产停止问题。

## 实施与证据边界

现行设计与决策见 `docs/design/链路层空口调度与反压.md`、`docs/adr/0017-寻频回执身份与换频上行重新声明.md`。

修复方向分别为：心跳回执绑定请求时间戳和服务端信道；换频成功、native 发射配置就绪后，通过独立 loopback 命令重新武装 READY；保留 GRANT 会话和序号去重，撤销旧授权；寻频遵守作业状态切换；停止条件检查与等待使用同一条件锁。无旧协议兼容、扩大超时或伪造就绪路径。

`red-native-rearm.txt` / `green-native-rearm.txt` 是实际 `run_client` 的隔离验证；`red-daemon-rearm.txt` / `green-daemon-rearm.txt` 是宿主公开入口、物理配置边界和真实本机 UDP 的集成验证。其余 `green-*`、`type-*` 保存受影响入口回归与类型检查。

软件测试不代表硬件问题已解决。发布须从明确已提交源码归档构建，四端使用同一 canonical native，并核对运行中的 `/proc/<pid>/exe` 摘要、实际 Python 加载位置、构建身份和 `iw` 读回。随后持续验证三个客户端的常驻心跳，成功后再执行四机真实 GPU/MNIST 两轮冒烟；08、09、12、13 当前仍不能结单。
