# 08 正式部署与入口核查计划（尚未执行）

本计划于 2026-10-26 基于本地源码只读审查编写。05 已提交 `d9386f9`；06 正在工作区实现，尚未提交。本轮未 SSH 连接四板，未运行部署、服务、无线或作业命令，也未修改生产脚本及工单状态。09 正在占用无线，以下含写入、停服、启动和 HTTP POST 的命令均为后续命令草案，不能现在执行。必须在 06 提交、09 交还设备且收到正式部署授权后，以最终完整提交重新核对本计划，特别是 06 的期限、停止和恢复行为。

此前环境准备结果见 [README.md](README.md)：四端 NVIDIA torch/MNIST 已通过；这些是历史证据，正式部署前应重新检查，不能假定期间配置或文件未变。

## 核查结论

`deploy_cluster.sh` 不是现成的 08 正式部署入口：它会同步当前整个工作区，允许跳过离线节点，调用包含无线及系统调优副作用的 `deploy_node.sh`，没有部署真实训练配置、持久工作目录或 daemon drop-in，也不验证 MNIST、GPU、算法导入、提交身份或停止恢复。`--skip-driver --skip-apt` 仍会执行网络及射频配置步骤，不能将其当成只更新应用的安全选项。

当前可复用的最小安装入口是 `make build_v6` 加 `scripts/install/install-v8.sh`；后者只安装文件，既不停止服务也不启动或重载服务。它直接覆盖运行层和二进制，故正式部署必须先在维护窗口停止对应 daemon，确认其整个 cgroup、角色子进程及 UFTP 已退出，再安装，不能边运行边热覆盖。SSH 只用于这些部署动作及事后取证，作业推进必须由现有 daemon、控制面和 Runtime 完成。

## 脚本逐项副作用

| 入口 | 实际行为 | 08 处理 |
| --- | --- | --- |
| deploy_cluster.sh 的 rsync | `rsync -az --delete` 同步整个 `PROJECT_ROOT`，排除 .git/编译物/logs；未排除 .scratch、outputs、env 等，也未限制为提交文件 | 不从当前脏工作区同步；仅发布冻结提交的源码归档 |
| 节点选择 | 探测 SSH，离线客户端警告后跳过；可在少于三客户端时返回成功 | 08 必须固定 1、2、4 三客户端，任一失败即停止部署验收 |
| 服务端 | 本机只做就绪检查，未执行本机安装；`--check-server` 是就绪门禁 | 必须单独安装 fl00 |
| deploy_node.sh | 默认 apt、驱动安装、UFTP 构建；即使 skip-driver/skip-apt，仍覆盖 NM unmanaged 配置，reload 或 restart NetworkManager、`rfkill unblock all`、sysctl、modprobe、sudoers | 现有底座已具备，不执行该全量装配入口 |
| 角色服务 stop | cluster/node/install-v8 本身均未实现应用停服及退出确认 | 后续显式停止当前 daemon、确认退出后安装 |
| install-v8.sh 的配置 | 每次覆盖 `/etc/wfb-ng/fl-server.json`、`fl-client.json`；`/etc/wfb-ng-fl/node.json`、`server.json` 仅在不存在时创建 | 正式安装前备份全部配置；保留现行 daemon 配置，显式合并新字段；角色示例 JSON 不当部署权威 |
| install-v8.sh 的 unit | 覆盖四个基础 unit（角色及 daemon）；不修改 `/etc/systemd/system/*.service.d/` drop-in，不 daemon-reload | 安装前备份有效 unit/drop-in；明确写完整 daemon ExecStart 并 reload 后审查 |
| 无线 JSON/drop-in | cluster 不按 cluster_nodes 自动生成 daemon 配置；原有 JSON/drop-in 中射频参数会继续存在，可能覆盖 CLI/默认值 | 重新核对权威配置、09 交还状态和最终生效命令，不能照搬样例默认 12 dBm / MCS6 |
| 旧 issue41 | cluster/node/install-v8 没有直接调用 issue41；但旧验收脚本会写 issue41 role JSON、algorithm drop-in、启停角色服务 | 不执行 issue41 脚本；正式部署前只读列出旧 role unit/drop-in，确认旧角色不活动、不启用第二套服务 |
| 当前手册 | 《部署与运行基准》仍引用 issue41_algorithm；真实 MNIST 手册明确该入口已退役 | 采用真实 MNIST 文档与现行源码，不使用模板 update、issue41 run-all 或旧 role algorithm.conf 来装配 daemon |
| 就绪核验 | 仅检查五个底座命令、驱动、唯一网卡，未检查 daemon 命令、训练实现或数据 | 增补逐端构建身份、三个入口、torch GPU、官方数据和 daemon 状态核验 |

本轮只列最小修复，不修改脚本。若后续要求继续使用一键脚本，至少需要：发布输入限定为冻结提交；严格全部目标成功；显式包含 server；新增仅应用安装路径以避开网络/驱动步骤；停服/退出/安装/配置/reload/启动有序执行；合并现行 JSON 与 daemon drop-in，逐端核验训练环境、期限及身份。无需为 08 重跑驱动/APT/射频装配。也可暂不修改生产脚本，按本计划用已有底层安装器完成正式部署。

## 真实入口与新配置字段

权威配置：`cluster_nodes.conf` 声明 fl00 服务端，客户端 `fl@192.168.108.201/.202/.204`，ID 1、2、4，TUN 10.80.0.11/.12/.14；服务器 10.80.0.1/24。信道 157、HT40+、14 dBm、下行 MCS3、上行 MCS4、UFTP 15000 Kbps。以上来自本地文件，正式部署时重新核实 09 交还状态，不能现在写回硬件。

服务端使用 `/usr/bin/wfb-fl-server-daemon --config /etc/wfb-ng-fl/server.json`。三个算法入口、算法配置、三类期限、本地 MNIST 目录及 work_dir 目前均由 JSON 装配；服务器 CLI 没有 `--mnist-data-dir`、`--work-dir` 或三期限参数，不能把客户端 CLI 选项传给服务端。

| 服务端 JSON 字段 | 拟部署值 | 语义与核查 |
| --- | --- | --- |
| client_entry | `wfb_ng.fl.mnist:client_entry` | 客户端 Runtime 算法入口，三参数；部署在各客户端，同时服务端导入验证 |
| aggregate_entry | `wfb_ng.fl.mnist:aggregate_entry` | 服务端 CPU 聚合，五参数 |
| evaluate_entry | `wfb_ng.fl.mnist:evaluate_entry` | 服务端 CPU 初始及逐轮评估，三参数 |
| algorithm_config | `{"data_seed":2026}` | 恰一个正整数种子，不允许夹带目录或期限 |
| mnist_data_dir | `/srv/wfb-ng-fl/mnist` | 服务端本地路径，只用于本机读取；不通过任务下发目录 |
| work_dir | `/srv/wfb-ng-fl/server` | 持久 job/artifacts、角色工作区及日志；替换默认 /tmp 路径 |
| preparation_timeout_seconds | `300.0`，拟值 | 覆盖模型验证、初始 CPU 评估及全员准备；不是每个子步骤重置预算 |
| round_timeout_seconds | `1800.0`，拟值 | 覆盖整轮发布、三个完整 epoch、提交/聚合/评估，不是单独训练时间 |
| io_timeout_seconds | `120.0`，拟值 | I/O/报告及恢复相关有界预算；最终以 06 提交实现确认 |
| model_library_dir | 保留现行 `/var/lib/wfb-ng-fl/models` | 不迁移已有模型库，避免丢失已选摘要；独立于持久 work_dir |

三期限必须显式写成 JSON 数值，建议保留 `.0`，最终解析后应是有限、正的 float，拒绝 bool/NaN/Inf/零/负值。当前待提交 06 的 `ServerDaemonConfig.__post_init__` 会转 float 并校验；`start_job` 使用服务器配置覆盖请求期限，再冻结进作业。300/1800/120 秒是首轮测量前的拟定上限，不是假造实测耗时；06 合入后再次检查传播、取消和恢复使用点，并在正式作业记录中冻结实际采用数值。单批次 GPU 耗时不能外推为完整 epoch 期限证明。

客户端节点 JSON 仍仅保存身份，例如 fl02 为 `{"node_id":2,"tun_ip":"10.80.0.12/24"}`。`load_node_identity` 不装配通用 daemon 运行参数；向 node.json 填 work_dir 或 mnist_data_dir 不会替代 CLI 参数。各端要在 daemon ExecStart 添加：

```text
--config /etc/wfb-ng-fl/node.json
--mnist-data-dir /srv/wfb-ng-fl/mnist
--work-dir /srv/wfb-ng-fl/client
--channel 157 --bandwidth-mhz 40 --radio-txpower-dbm 14 --uplink-mcs 4
```

现行客户端没有本地三期限 CLI；任务中已冻结的准备和 I/O 等预算由服务器控制面提供。入口字符串由服务器已部署配置选择，客户端没有 `--algorithm` 选项。客户端禁止从网络任务接收 `mnist_data_dir`/`local_config`，每端配置只注入本机上下文。

`known_clients` 保持平台预置 1～10，不要改为 [1,2,4]：当前 `ServerDaemonConfig` 要求全槽位，loader 也固定加载全槽位。真正作业目标在创建请求中冻结为 `[1,2,4]`。服务器启动时 `enable_link_process` / `enable_control_plane` 均保留 true。

## 持久目录、owner 与权限

现行 daemon 基础 unit 未声明 User/Group，因此由 systemd 以 root:root 执行。此前有效 unit 也没有观测到非 root User；正式授权后必须重新 `systemctl show -p User -p Group` 核实。08 采用现行 root 模式，不在本票引入非 root 服务权限重构。

| 路径 | owner / mode 计划 | 用途 |
| --- | --- | --- |
| /srv/wfb-ng-fl/server（fl00） | root:root / 0700 | 持久作业工作目录和服务端实际制品 |
| /srv/wfb-ng-fl/client（三客户端） | root:root / 0700 | 沙箱根及 channel_cache；job 子目录由实现以 0700 创建 |
| /var/lib/wfb-ng-fl/models（fl00） | 现有 owner/mode 先记录；root 服务须可读写；新建用 root:root / 0700 | 模型库，保留现有数据，不递归改 owner 或清空 |
| /var/lib/wfb-ng-fl/evidence（三客户端） | root:root / 0700（新建时） | 当前 client 小文件白名单归档默认根，CLI 没有 evidence-dir 开关 |
| /srv/wfb-ng-fl/mnist | 已预置 root:root / 0755；四 IDX root:root / 0644 | 只读数据，继续核对来源清单，不重新下载或覆盖 |
| /etc/wfb-ng-fl/*.json | root:root / 0644 | 本机身份及服务器部署配置 |
| /etc/systemd/system/*-daemon.service.d/08-mnist.conf | root:root / 0644 | 本次明确的有效命令 |
| /srv/wfb-ng-fl/install-cache、mnist-downloads | 保留缓存及实际 owner，无须递归改 | 供离线复用，不复制到 git |
| 仓库 evidence/08-jetson-preflight | fl 可写，仅小型文件 | 配置/版本/命令/日志/manifest，不收 wheel、gz、IDX、模型或 update 大文件 |

新目录示例命令（仅未来执行）：

```bash
# fl00
sudo install -d -o root -g root -m 0700 /srv/wfb-ng-fl/server
# 客户端各自执行
sudo install -d -o root -g root -m 0700 /srv/wfb-ng-fl/client
# models/evidence 仅在不存在时创建；已有目录先检查、不递归变更
```

持久 work_dir 不会自动将客户端模型/update 纳入小文件审计归档；当前成功归档后实现会删除沙箱。08 如需六份 update 作独立复算，应先核实服务端保留制品位置并持久备份到 /srv，不能为了取模型把客户端 audit 白名单改成全量目录复制。

## 冻结提交和构建发布命令草案

不能发布当前脏工作区。06 完成后明确一个含 05、06 的完整 40 位 SHA，验证祖先及提交身份；不把当前 HEAD 的身份贴到未提交源码上。归档目录和实际制品放 /srv 或 /tmp。下面 `DEPLOY_SHA` 必须由最终提交替换，不是现在的 d9386f9。

```bash
DEPLOY_SHA='<06完成后冻结的40位提交>'
git cat-file -e "$DEPLOY_SHA^{commit}"
git merge-base --is-ancestor d9386f9 "$DEPLOY_SHA"
# 生成只包含提交树的发布包，排除所有未提交06/09/11/12及证据缓存
install -d /tmp/wfb08-release
# 输出归档采用新文件名，部署时先确认同名文件不存在
git archive --format=tar "$DEPLOY_SHA" -o "/tmp/wfb08-release/source-$DEPLOY_SHA.tar"
sha256sum "/tmp/wfb08-release/source-$DEPLOY_SHA.tar"
```

将同一包分发到固定三端，在各端新目录解包并原生构建；fl00 同样从该包构建，不能从协作工作区构建。源码目录不带 .git，故 **必须显式传 COMMIT、VERSION**，否则 Makefile 会使用 `release`，setup.py 拒绝非完整 SHA；普通 cluster rsync 同样排除 .git，当前未显式传身份也是其部署缺陷。

```bash
# 各端命令模板；DEPLOY_SHA在该shell中明确赋值
RELEASE_DIR="/tmp/wfb08-release/source-$DEPLOY_SHA"
mkdir "$RELEASE_DIR"
tar -xf "/tmp/wfb08-release/source-$DEPLOY_SHA.tar" -C "$RELEASE_DIR"
cd "$RELEASE_DIR"
make build_v6 COMMIT="$DEPLOY_SHA" VERSION=0.0.0 PYTHON=/usr/bin/python3
```

VERSION=0.0.0 仅用于直接安装器要求的包版本元数据，实际身份必须由完整 COMMIT 和安装文件摘要确认；正式发布若已有版本策略则用已冻结 VERSION。原生 wfb_v6_uplink 的编译标识也须与同一个 COMMIT 对齐；每端新解包构建，避免旧二进制缓存。

## 维护窗口中的安装与配置命令草案

先由 09 完成并交还空闲四板，再重新记录有效 unit、drop-in、JSON、进程、无线、磁盘、数据摘要。确认没有活动训练或标定，备份到 `/srv/wfb-ng-fl/deployment-backups/<部署标识>/`，不用仓库保存大目录。不要运行旧 issue41 stop-all 清理脚本，也不要删活动 work_dir/锁。

部署停止的是 daemon unit。示例客户端：

```bash
sudo systemctl stop wfb-fl-client-daemon.service
systemctl is-active wfb-fl-client-daemon.service
systemd-cgls --unit wfb-fl-client-daemon.service
# is-active 期望 inactive，该检查通常返回非零，应显式判断而非误当安装失败
# 核查旧 role unit 不活动及本角色的 wfb/u ftp 子进程均已退出
```

fl00 对应 `wfb-fl-server-daemon.service`。仅在所有相关子进程确认退出后，从上述发布目录安装：

```bash
sudo env COMMIT="$DEPLOY_SHA" VERSION=0.0.0 PYTHON=/usr/bin/python3 \
  PREFIX=/usr ./scripts/install/install-v8.sh
```

该平台 `sysconfig.get_path('purelib', vars={'base':'/usr','platbase':'/usr'})` 实测为 `/usr/local/lib/python3.10/dist-packages`，与当前导入位置一致；正式每端安装前重新计算确认。安装器复制 FL 模块和 build_identity、入口及四个 unit；会重写 `/etc/wfb-ng/fl-*.json` 样例。若要保留样例原内容，可在安装前后按备份恢复；不要恢复旧 Python、二进制或把训练 daemon 配置替换成样例。安装器不会自动清除废弃文件，若发现旧 issue41 等废弃模块残留，先列出准确安装文件清单再做明确删除，不能保留为兼容入口。

服务器：读取备份后的现行 server.json，合并上表训练字段及持久目录，不从默认 server.json 整体覆盖。保留管理地址（此前为 192.168.108.100）、IPC、TUN、model_library_dir；权威射频组为 157/40/14/3/4/15000。在完整 JSON 写入前，用新部署代码的 `load_server_config` 只做加载验证，输出三 float 数值；不构造/运行 ServerDaemon。写临时新文件再原子替换，保留旧配置备份。

服务端拟定 drop-in：

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/wfb-fl-server-daemon --config /etc/wfb-ng-fl/server.json
```

客户端拟定 drop-in：

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/wfb-fl-client-daemon --config /etc/wfb-ng-fl/node.json --channel 157 --bandwidth-mhz 40 --radio-txpower-dbm 14 --uplink-mcs 4 --mnist-data-dir /srv/wfb-ng-fl/mnist --work-dir /srv/wfb-ng-fl/client
```

不能假定 `08-mnist.conf` 一定覆盖其他文件：systemd 会按 drop-in 路径和文件名字典序合并。写前核对现有全部 drop-in，维护/整合旧 ExecStart 定义，移走本次不适用且已确认来源的冲突 drop-in 到备份；最后以 `systemctl cat` 和 `systemctl show -p ExecStart` 确认有效结果唯一正确。不建立 `wfb-fl-server.service.d/algorithm.conf` 或 issue41 role drop-in。

```bash
sudo systemctl daemon-reload
systemctl show wfb-fl-client-daemon.service -p ExecStart -p User -p Group -p KillMode -p TimeoutStopUSec
sudo systemctl start wfb-fl-client-daemon.service
systemctl is-active wfb-fl-client-daemon.service
```

服务端替换为 server-daemon。基础 unit 的 KillMode=control-group、TimeoutStopSec=15s；06 合入后核查关闭/恢复的最大预算与 systemd 维护停止预算是否匹配，必要时单独调整 daemon drop-in 的 TimeoutStopSec 并记录理由，不能用固定睡眠替代退出确认。作业内“停止”使用 HTTP abort，不停止 daemon。

安装后逐端从 /tmp 运行，打印实际包路径/build_identity/算法文件摘要、导入三个入口、检查 GPU/MNIST。完整构建身份与冻结 SHA 相等后才进入正式试跑。复用本目录 `gpu-fixed-model-check.py` 检查新安装固定模型类；不能用只查询 `torch.cuda.is_available` 作为准备成功。

## 正式作业入口核查：两轮、三客户端、停止与恢复

优先使用现有管理 Web API，而非 SSH 调 Runtime 或旧 issue41。Web bind 必须是服务器实际管理 IP；Host header 必须准确匹配管理地址和 8080。示例地址来自此前证据，部署时重新确认。CLI 生成初始模型可用 `python3 -B -m wfb_ng.fl.reference_model`，当前直接安装器未安装 `wfb-fl-init-model` 这个 console-script，不能假定该命令一定存在。

正式配置后，在 /srv 新路径生成一次 seed7 初始制品并冻结实际摘要，然后通过模型库入口上传；禁止覆盖已有制品，同一制品用于本票两轮和后续短作业：

```bash
# 示例 /srv 路径必须是新文件；以具备目标目录写权限的身份执行
sudo python3 -B -m wfb_ng.fl.reference_model /srv/wfb-ng-fl/server/mnist-initial-seed7.pt --seed 7
# 上传文件需当前调用者可读；可由 sudo curl 在fl00本机调用，或另准备可读的部署制品路径
sudo curl --fail-with-body --connect-timeout 5 --max-time 60 \
  -F 'file=@/srv/wfb-ng-fl/server/mnist-initial-seed7.pt' \
  http://192.168.108.100:8080/api/v1/models
```

先 GET `/api/v1/state`，确认四板常驻服务/链路 ready、目标 1/2/4 在线待命且心跳新鲜、上项恢复 ready、没有受控标定/活动无线事务。创建请求仅允许 model_sha256、target_nodes、rounds，不能在 Web payload 提交算法入口、路径、期限或 algorithm_config。将实际上传响应摘要写入以下请求文件，再执行：

```json
{"model_sha256":"<实际初始制品SHA256>","target_nodes":[1,2,4],"rounds":2}
```

```bash
sudo curl --fail-with-body --connect-timeout 5 --max-time 330 \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: <本次唯一标识>' \
  --data-binary @/srv/wfb-ng-fl/server/08-two-round-request.json \
  http://192.168.108.100:8080/api/v1/jobs
curl --fail-with-body --connect-timeout 5 --max-time 10 http://192.168.108.100:8080/api/v1/state
```

首个 POST 可能包含准备屏障等待，HTTP 超时要大于冻结准备预算并匹配 06 最终行为；超时不意味着任务未受理，先查询同一作业事实/使用同幂等键，不能无条件新发。服务端自动完成初始评估、全员准备、两轮分发/训练/上传/聚合/评估、末轮完成确认和恢复。管理 GET/SSE 可以持续观察；不以 SSH 循环发唤醒、文件传输或轮次推进。

正常任务应保存 M0/M1/M2 摘要及 M1 被第二轮三端真实加载的关联、六份 update 的 node/round/base_sha、每端每轮 60000 样本和 938 步、实际设备/损失、CPU 初始及逐轮评估、执行/效果/恢复分离结果。服务端真实制品保存在 /srv 的持久工作目录，摘要和关键结果进仓库小型证据，完整模型/update 在 /srv 持久归档。

另发一项短作业（同一初始摘要、同一目标，轮数至少2），待确实进入计算/轮次后调用：

```bash
curl --fail-with-body --connect-timeout 5 --max-time 15 \
  -H 'Content-Type: application/json' --data-binary '{"reason":"08正常停止验证"}' \
  http://192.168.108.100:8080/api/v1/jobs/<实际job_id>/abort
```

HTTP 202 仅代表停止受理；继续通过 `/api/v1/state` / events 取执行终态和恢复事实，确认不再推进后续轮次、客户端计算/角色退出、全员恢复 IDLE、服务端恢复 ready。若超时/未确认则记录未恢复并禁止新任务，不能 SSH 重启服务后把恢复判成功。事后 SSH 可核对 cgroup、日志和归档，不能把事后管理操作当作正常作业推进或恢复协议的一部分。

## 交接与仍需最终确认的点

1. 06 提交后的最终期限字段/传播/终态/恢复行为和测试结果，重新核对本计划；当前引用的是未提交实现快照，不部署。
2. 09 实际结束时的空闲状态、40 MHz 正常配置及租约恢复，需要后续实机事实；本轮没有读取设备，因此不声称09已交还。
3. 最终完整提交、VERSION、各端源码归档 SHA 和安装文件清单必须冻结，不能使用当前工作区混合版本。
4. 服务器真实管理 IP、有效 unit/drop-in、服务 owner、磁盘及模型库现状，需要在正式授权后复查，历史证据不是新实测。
5. 准备300/整轮1800/I/O120秒是拟值，正式试跑实测后记录；如不足则明确失败并在新尝试冻结修改，不续期掩盖超时。
6. 此前全局 pip check 告警及 NVIDIA 精确wheel发布表映射限制仍见 README；本票已有实际模型GPU检查通过，正式部署需再次确认，无CPU回退。

本计划只为后续可审查的操作准备命令，不宣称已完成08正式部署或验收。
