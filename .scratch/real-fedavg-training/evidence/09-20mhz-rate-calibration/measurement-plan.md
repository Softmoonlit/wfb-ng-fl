# 工单09：HT20三客户端测量计划与执行手册（待明确授权）

本文只准备工单09测量，不是实测结果。当前阶段不访问四板、不设置射频、不停止服务、不部署、不修改代码、不提交、不改工单状态；05主线继续。用户最新确认：08只读已结束，四台管理在线，当前配置为157/HT40/14dBm/下行MCS3/上行MCS4；08worker已获准并行安装三个客户端GPU PyTorch及四端MNIST，不改变无线、不停服务。09等待安装完成且用户明确通知开始实测，避免安装CPU/磁盘负载影响可比性；管理在线和已知配置不是测量授权。所有示例均为未来执行模板。

## 1. 现有能力与证据边界

- 任务依据：[09](../../issues/09-20mhz-rate-calibration.md)、[spec](../../spec.md)的受控无线配置和部署标定要求、[03证据](../03-radio-online/README.md)、[操作说明](../../../../docs/deployment/空闲无线在线配置与HT20标定.md)。
- 本机IPC：fl00 `http://127.0.0.1:9090/api/v1/radio/calibrate`，POST只接受 `confirmed`、`target_nodes`、`patch`。patch须显式包含 `bandwidth_mhz=20`、`downlink_mcs`、正整数 `uftp_rate_kbps`；复用守护进程两阶段在线更新，不另起底座或TUN。源码为 `wfb_ng/fl/server_daemon.py` 的 `calibrate_radio`、`_reserve_radio_reconfiguration` 与 `wfb_ng/fl/radio.py` 的 `validate_radio_calibration_patch`。
- 前提为Server IDLE、无活动作业、全部在线客户端 IDLE/READY，受理冻结集合。本次必须恰为 `[1,2,4]`；少一台或多一台都暂停，不剔除节点。普通作业在calibration模式拒绝启动。
- `GET /api/v1/state` 与 `/api/v1/status` 可取权威快照；结果必须 `status=finalized`、`calibration=true`、有效配置匹配，无未响应节点。实际响应字段是 `session_id`、`unresponsive_nodes` 等，不假设存在规格目标中的全部字段。保存原始响应及HTTP状态。
- 文件能力直接复用已安装的 `uftp/uftpd`（03实测版本5.0.3），在现有TUN上使用独立UDP 1144，三个固定UID组成closed group。无需模型库、算法、GPU、训练作业或新增后端接口。
- `tests/demo/run_fl_demo.sh` 第519～558行展示独立接收器、`-H`收件集合、`-L/-S`日志状态参数，可参考参数；**不可运行整个脚本**：它会重建底座、固定40MHz，且包含广域pkill。`tests/demo/fl_demo_metrics.py` 的 `generate-file` 可单独生成确定性文件，但最简方案用系统dd生成一次随机文件，冻结大小与SHA-256。
- 03只测了 HT20/MCS3/4000 Kbps、1 MiB、一次全组成功。原始 `ht20-uftp.log` 有三端文件完成、组级三端COMPLETE及Group complete，含NAK与第二pass；这不证明40 MiB重复成功、其他MCS或速率范围。4000只是有过一次短文件成功的候选。

### 现有40MHz表来源及适用限制

当前 `wfb_ng/fl/radio.py::_DOWNLINK_RATE_BOUNDS` 只有40MHz四条：

| 下行MCS | min Kbps | max Kbps | default Kbps |
| --- | ---: | ---: | ---: |
| 3 | 12000 | 18000 | 15000 |
| 4 | 18000 | 26000 | 22000 |
| 5 | 24000 | 35000 | 28000 |
| 6 | 32000 | 45000 | 38000 |

数字的文档来源是 [ADR-0014](../../../../docs/adr/0014-废除射频预设改用直配模型与界面动态速率约束.md)；`git log -S 'max_rate_kbps=45000' -- wfb_ng/fl/radio.py` 定位最初引入提交 `5dfa0f5`。03提交 `99865ef` 将查表维度改为带宽与下行MCS，未提供HT20条目。

可追溯的历史运行证据包括 `.scratch/stage2-6node-40mib-rf-and-token-tuning/issues/02-six-client-topology-and-single-node-downlink-probe.md` 的HT40+/MCS3/15000、40MiB单客户端21～24s；该特性spec与后续工单另记录6客户端、近距12dBm、FEC8/14等条件。Stage1的MCS扫测还涉及旧拓扑、不同注入方案；不能把这些吞吐结果当作当前四档区间端点的重复验证。03普通MCS4/22000只证明重配确认，未证明该组合40MiB分发。

在本次阅读范围内未找到当前四板对四档全部端点与推荐值的完整重复证据。旧表保持历史适用条件，其完整区间不能称为本次已验证；也不据此产生任何HT20数字。若执行时40MHz恢复/对照失败，如实保留冲突，另安排必要的40MHz测量，不擅自扩本轮矩阵。

### 原始证据中的差异

03 README概括初始/结束上下行MCS3/4，但 `ht20-calibration.json` 的测量有效配置实际为下行3、上行3；因此本次上行MCS必须由执行前快照明确冻结，不能直接沿用摘要。`ht20-fl01.txt` 的 `iw` 功率读回为 `-14.00 dBm`，与请求正14dBm不同；原样记录请求、驱动读回和日志，核对驱动功率表示，不将负值改写成正值，也不声称测得辐射功率。若无法解释且影响跨轮一致性，先暂停标定结论。

## 2. 固定条件、矩阵和最简探索策略

拓扑固定：fl00/NODE_ID255/TUN `fl-s`/10.80.0.1；fl01/1/`fl-c1`/10.80.0.11；fl02/2/`fl-c2`/10.80.0.12；fl04/4/`fl-c4`/10.80.0.14。实际接口由执行时确认，空口接口不能假设与TUN同名。

固定操作者当前已确认、同时支持20/40MHz的信道；03曾用157，仅作历史参考。固定功率、上行MCS、GI short、FEC8/14、底座版本、天线位置与朝向、距离、USB连接和现场活动。只改变下行MCS与候选注入速率。功率和上行MCS采用执行前保存并核实的正常配置；不另行“优化”。不扫频、不移动设备，不启动其他无线负载。

正式文件默认 **41943040字节（40MiB）**，一次生成，全矩阵复用内容。每次使用独立接收目录，无旧文件。每个MCS至少两个不同候选，每候选 **3次独立整组会话**；最低正式矩阵24次。

| 下行MCS | 首轮保守候选Kbps（均未标定） | 每候选重复 | 目标 |
| --- | --- | ---: | --- |
| 3 | 2000、4000 | 3 | 1、2、4全部 |
| 4 | 2000、4000 | 3 | 1、2、4全部 |
| 5 | 2000、4000 | 3 | 1、2、4全部 |
| 6 | 2000、4000 | 3 | 1、2、4全部 |

2000是人为选择的保守起点，4000是03短文件曾测输入，两者均不是HT20有效范围或默认值。顺序每个MCS按低、高、低、高、低、高交错，避免连续三次只反映短时间环境。若2000失败，先诊断全组与射频证据；条件稳定后可加入1000候选，不以降低速率掩盖硬件/事务问题。若两个点成功，已满足最简测量覆盖；不必找到失败边界才产出保守测量结果。

可选第二阶段：按2000 Kbps步进试更高候选，每个新候选仍3次；出现第一次全组失败即停止向上探索，记录并在状态正常后复测该候选和前一成功点。可在两者间加一个中点，各3次，最多做这一轮细化。预算到达就报告“未找到失败边界”；不能写成理论上限。任一失败都保留，复测成功不能抹去失败。若实际射频不符、节点失联或PID变更，暂停整组并调查，不继续调速。

如40MiB导致单次失败等待过长，可在开始正式矩阵前统一选择8MiB（8388608字节），理由和期限提前冻结，所有候选同大小；小文件仅支持该载荷条件，正式推荐点及区间端点仍各补3次40MiB验证后才能用于40MiB配置依据。1MiB仅可作不计正式次数的连通探针，探针也归档。

## 3. 授权后的操作步骤

### A. 预检与恢复基线（10～20分钟）

1. 确认用户已明确授权开始实测，08worker的PyTorch/MNIST安装已完成，05不在执行硬件作业。读取四端状态，若部署版本缺少03入口则停止并交回部署问题；本计划不包含部署/重启。执行前及每次attempt记录 `/proc/loadavg`、`/proc/stat`、`/proc/diskstats` 和进程CPU占用（可用现有ps，避免安装监测软件），确认无pip/apt/解压/下载/数据校验等安装活动；不终止08进程。若测量中又出现安装或明显额外负载，记录条件变化并暂停，待相同条件重新冻结，不把不同负载结果混为同一重复组。
2. 在09证据路径建立唯一运行目录，例如 `runs/<UTC批次>/`；各端使用 `/var/tmp/wfb-ticket09/<批次>/`，持久备份位置由执行时确定并记录。记录git提交、脏工作区差异身份（05并行期间不能仅记HEAD）、已安装Python代码与底座二进制SHA-256、UFTP版本及参数帮助、内核/驱动/网卡MAC、USB速率、位置照片或文字尺寸、现场干扰描述、时间同步状态。
3. 保存fl00两种API快照、四端服务状态、底座及Daemon PID/启动时间/完整命令行、TUN ifindex/IP/路由/组播路由/MTU、空口 `iw dev <接口> info`、驱动信息。对照实际角色服务/1144端口占用，不能仅凭Web空闲。保留完整正常配置为 `before-config.json`，构造 `restore-request.json`，显式含原信道、40MHz、功率、上下行MCS和UFTP速率；若基线不是已确认可恢复的正常40MHz配置，先停止，由操作者明确基线。
4. 使用 `curl --noproxy '*'` 读取本机API；保留stderr、HTTP状态和原始JSON。`ss -lunp` 检查1144未占用；不杀已有接收器。核对三端磁盘余量及所有在线节点恰为1、2、4。
5. 在fl00生成一次文件，记录生成耗时、字节数、源hash及hash耗时，复制到每个attempt独立source目录并保留相同basename `model.bin`，或引用冻结的绝对源路径。

```bash
mkdir -p /var/tmp/wfb-ticket09/BATCH/source
dd if=/dev/urandom of=/var/tmp/wfb-ticket09/BATCH/source/model.bin bs=1M count=40
stat -c '%s' /var/tmp/wfb-ticket09/BATCH/source/model.bin
/usr/bin/time -f 'hash_seconds=%e' sha256sum /var/tmp/wfb-ticket09/BATCH/source/model.bin
```

`BATCH`及下文 `ATTEMPT`、`MCS`、`RATE`、`NODE_ID`均为执行前替换的值，不直接运行占位符；所有输出写入对应证据文件。

### B. 每次候选事务与射频证明

先建立attempt目录并写入拟测参数，**发出任何请求之前就记一条尝试**。即使受理拒绝、超时或接收器启动失败，也留下结果，不编号覆盖。

```bash
curl --noproxy '*' --fail-with-body \
  -X POST http://127.0.0.1:9090/api/v1/radio/calibrate \
  -H 'Content-Type: application/json' \
  --data-binary @calibration-request.json
```

请求文件示例（MCS3、候选2000）：

```json
{"confirmed":true,"target_nodes":[1,2,4],"patch":{"bandwidth_mhz":20,"downlink_mcs":3,"uftp_rate_kbps":2000}}
```

请求成功后保存事务响应、耗时、四端新快照。四端 `iw dev <空口接口> info` 必须为固定信道20MHz；底座在线更新日志须有本次generation、MCS/BW、GI/FEC及数据帧和控制帧一致的证据。四端底座PID/启动时间及TUN ifindex与基线一致。客户端在**本attempt时间窗内**观测到Server源255的 `RX_ANT <固定频率>:<目标MCS>:20`，必要时联用 `PKT_SRC`、日志时间与接口MAC关联，不能用历史RX_ANT或其他发送端代替。Server侧保存各客户端反馈接收观测及实际上行MCS/BW（下行和上行MCS不同是允许的）。

测量期间继续保存各端底座遥测/日志，不另启会争用网卡的抓包或底座实例。若现有日志无法证明来源或实际帧配置，标为证据不足，停止填表；另行准备被动观测方案，不能用API缓存补证。

### C. 三端接收器、单次全组发送、落盘校验

每个attempt在fl01/fl02/fl04各创建全新目录，NODE_ID分别1、2、4，UID分别 `0x00000001`、`0x00000002`、`0x00000004`。接收进程使用 `-d` 前台模式，后台化后捕获专属PID（示例中的UID也须替换）：

```bash
mkdir -p /var/tmp/wfb-ticket09/BATCH/ATTEMPT/received /var/tmp/wfb-ticket09/BATCH/ATTEMPT/tmp
nohup uftpd -d -p 1144 -U 0x00000001 \
  -D /var/tmp/wfb-ticket09/BATCH/ATTEMPT/received \
  -T /var/tmp/wfb-ticket09/BATCH/ATTEMPT/tmp \
  -I fl-c1 -M 239.255.42.1 \
  -L /var/tmp/wfb-ticket09/BATCH/ATTEMPT/uftpd.log \
  -F /var/tmp/wfb-ticket09/BATCH/ATTEMPT/uftpd.status \
  > /var/tmp/wfb-ticket09/BATCH/ATTEMPT/receiver-console.log 2>&1 &
echo $! > /var/tmp/wfb-ticket09/BATCH/ATTEMPT/uftpd.pid
```

其余两端对应替换UID和TUN。保存PID、启动时间、命令行与1144监听证明，三端全部就绪才发送。使用管理网SSH启动这些专属测量进程和取证是允许的准备/测量步骤，文件本体必须走无线TUN；不执行生产作业，也不经管理网代发模型。

fl00发送模板，RATE须与已finalized的候选完全一致，TIMEOUT按下面规则预先计算：

```bash
/usr/bin/time -f 'sender_wall_seconds=%e' \
  timeout --signal=TERM --kill-after=10s TIMEOUT \
  uftp -Y none -p 1144 -R RATE -I fl-s \
  -M 239.255.42.1 -P 239.255.42.2 \
  -H 0x00000001,0x00000002,0x00000004 \
  -L /var/tmp/wfb-ticket09/BATCH/ATTEMPT/uftp.log \
  -S /var/tmp/wfb-ticket09/BATCH/ATTEMPT/uftp.status \
  /var/tmp/wfb-ticket09/BATCH/source/model.bin \
  > /var/tmp/wfb-ticket09/BATCH/ATTEMPT/sender-console.log \
  2> /var/tmp/wfb-ticket09/BATCH/ATTEMPT/sender-time-and-stderr.log
sender_rc=$?
```

执行前确认已安装版本支持上述参数。外层命令不要使用会在失败时直接跳过归档/恢复的 `set -e`。TIMEOUT秒数固定为 `max(180, ceil(3 × 文件字节数 × 8 / (RATE × 1000) + 60))`；40MiB在2000/4000时分别564/312秒。这只是注入时长加重传/握手预算，并非预测射频吞吐或HT20上限。保留sender_rc，124/137等按超时/强制终止记失败；不得自动延长本次预算到成功。外层timeout只管理本次发送器。

发送器退出后等待本次进程确实结束；保存完整sender日志/status。必须同时具备：退出码0、三UID均注册且无缺失、**组级**三UID的COMPLETE和Group complete、三端最终received文件存在且大小41943040与源SHA-256一致、实际射频与PID证据完整，才能计入“完整证据成功”。文件级Completed、单端hash通过或进程退出码0均不能单独算成功。

三端均执行以下操作并分别保存时间及结果，失败端也采集最终/临时文件状态，不只采成功端：

```bash
stat -c '%s' /var/tmp/wfb-ticket09/BATCH/ATTEMPT/received/model.bin
/usr/bin/time -f 'hash_seconds=%e' sha256sum /var/tmp/wfb-ticket09/BATCH/ATTEMPT/received/model.bin
```

结束后按本轮记录的PID及启动时间/命令行核对身份，再TERM该接收器、等待退出并确认1144释放；不能 `pkill uftpd`、不能停Daemon。保留文件和日志，不自动清理原证据。上一轮全部发送/接收操作结束且状态正常，才能改变下一轮候选。进程不能退出时暂停，记录资源恢复未完成。

### D. 测量收尾与恢复（10～20分钟）

不论成功或失败，都归档当前尝试并回收仅本次专属发送/接收器。按预存 `restore-request.json` 调普通 `POST /api/v1/radio/reconfigure` 恢复原40MHz完整配置，不使用 `/radio/save`，不写 `/etc`、systemd或寻频缓存文件，不重启服务。必须确认finalized、calibration=false、四端真实带宽/信道/发射日志匹配、PID/TUN保持、Server IDLE、节点1/2/4 READY且服务active。

若事务失败进入RADIO_ERROR/确认不全，普通恢复可能被拒绝：保存错误、未确认集合、各端实际参数与进程状态，停止后续测量和作业，报告恢复未完成；不通过重启/部署/手工网卡命令绕过。硬件窗口授权只覆盖本计划的在线测量和正常恢复，不包含额外修复。

## 4. 逐次记录与汇总口径

每个attempt保存一行JSONL/CSV及原始附件，人工表格即可，不必新增通用测试框架：

| 类别 | 必记字段 |
| --- | --- |
| 身份 | batch/attempt_id、UTC始末、重复序号、探针或正式、计划参数、冻结节点集合、代码/底座/hash身份 |
| 事务 | 请求JSON、HTTP状态/错误、事务session_id、API耗时、有效配置、未确认节点、实际恢复状态 |
| 射频与资源 | 每端接口/MAC、实际iw原文、上下行MCS/BW帧观测及来源、generation、GI/FEC、功率请求/读回、PID/启动时间/TUN ifindex前后 |
| 文件 | 实际bytes、源SHA-256、源hash耗时；三端received路径/bytes/SHA-256/hash耗时，缺文件显式null及原因 |
| 会话 | UFTP group ID、sender_rc、超时预算与是否触发、三UID注册/文件完成/组级完成、Group complete、NAK/pass/重传的原始可得数据；不可得写null而非0 |
| 时间 | sender进程wall、UFTP文件阶段/组阶段耗时、各端完成时间、校验完成总wall；单机monotonic计时优先，跨机未同步不相减 |
| 结论 | transport组成功、三端完整性、射频证据、总体可采纳成功分别记录；失败阶段/原因、操作员中断、接收器清理结果、关联证据路径 |

建议执行时用已有Python标准库 `time.monotonic()` 包装单次子进程测wall与hash耗时，或以上 `/usr/bin/time` 获取elapsed；不需要新增源代码。最简重复入口就是上述B/C的单attempt命令，外层只按固定MCS/候选/重复表顺序调用，保留独立目录和退出码；不把 `/tmp` 或 `/var/tmp/wfb-ticket03/` 的临时脚本当作正式框架，不加入部署、服务控制、自动修复或复杂调度。当前授权仅写09路径Markdown，因此命令以可审阅模板交付；任何落地脚本文件另按明确授权创建。发送wall从启动前到退出后，包含组建立与组收尾；hash耗时单列。组有效吞吐取 `bytes×8 / sender_wall_seconds / 1e6` Mbps，成功时一份文件只计一次，不能乘3冒充无线吞吐；如需三端交付总量另列并明确为累计交付。失败不填“成功吞吐”，可列观察到的部分进展。保留UFTP自报吞吐的原单位/阶段，不能与wall口径混用。

最低24次的分母包括所有已发出会话；事务失败等未发送尝试另外计数。接收器启动失败、缺目标、超时、全组失败、hash错、射频证据不足等均保留独立attempt。某客户端已收到也不能把该整组失败拆成两次成功。

## 5. 如何形成可交付范围（本阶段不填数字、不接表）

按每个MCS列出所有已测离散速率及成功次数/总尝试数、文件大小、耗时分布、重传与失败情况。候选至少3/3完整成功才可进入“可重复成功点”；出现过失败的相同条件候选不因挑选后续3次成功就抹去历史，须解释原因并重新冻结条件、完整重测。

最简保守交付可以只圈定两个实测可重复成功点之间的范围；min/max只能取这些点，推荐值也从实测3/3成功点中选择，优先较低且耗时/重传稳定者，不能取未测中值。必须写明**端点及已测内部点的经验包络，未逐Kbps穷举**；不能声称区间内每一个整数都被验证。若较宽区间内部存在失败点，就收缩到没有已知失败点的局部范围，必要时补测中点。若不足两个可重复成功点，该MCS不交付范围，缺表门禁继续拒绝。

本次下行固定上行MCS、现场位置、功率、文件大小与三客户端条件；不宣称所有上行MCS/距离/环境都验证通过。成功的最高已测点只叫“最高已测可重复成功速率”，失败最小点只叫“观察失败点”，均非理论/安全极限。

未来另经授权接表时，才修改 `wfb_ng/fl/radio.py::_DOWNLINK_RATE_BOUNDS` 的(20,mcs)，并验证普通入口推荐值、显式合法值、越界拒绝、仅改功率/上行MCS保留速率等规则。本轮只写计划，09仍未完成、状态不变。

## 6. 所需操作与预计窗口

| 阶段 | 需要的操作 | 预计时长 |
| --- | --- | --- |
| 准备 | 明确授权/排除08冲突、四端只读取证、冻结恢复配置、确认1144、生成文件 | 10～20分钟 |
| 最低正式矩阵 | 4个MCS × 2候选 × 3次，在线HT20事务、三接收器、40MiB无线发送、日志/hash/清理 | 约60～110分钟；实际重传可能更长 |
| 正常恢复 | 原40MHz完整配置及四端实际读回、READY核验、归档摘要 | 10～20分钟 |
| 计划预留 | 不含高边界探索，建议申请连续独占硬件测量窗口 | **约90～150分钟，预留3小时** |
| 可选边界探索 | 每新增一个候选做3次，含事务与取证，失败等待依预算 | 通常每候选5～15分钟；事先限制总窗口 |

24次在2000/4000的理想注入时长合计约50.3分钟，仅用于排期，未含协议/重传/验证；不是HT20预测性能。若全部跑到预设外层期限，单发送等待可到175.2分钟（4×3×(564+312)秒），加准备/取证/恢复总计约3.5～4小时。实际进展达到预留窗口仍未完成时，停止扩展、恢复并报告已完成/未完成矩阵，不以删掉失败或缩减客户端赶进度。

本计划没有执行任何硬件命令。下一步等待08安装完成及用户明确通知可进行实测；通知前仅继续09目录内的方案及本地只读核对，不因08只读结束或四板在线而提前发送无线探针。
