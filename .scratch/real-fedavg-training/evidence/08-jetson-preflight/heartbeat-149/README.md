# 149 基线常驻心跳诊断与修复验证

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
