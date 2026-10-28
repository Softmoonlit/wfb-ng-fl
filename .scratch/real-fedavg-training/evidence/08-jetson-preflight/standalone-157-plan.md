# 固定157 standalone小文件对照：只读入口审查与执行方案（未执行）

本轮只读取仓库脚本、冻结701源码及文档，没有SSH、读取实机状态、运行demo/dry-run、发探针、停服务或改RF。当前现场保持不动。以下仅供主线协调维护/恢复窗口后审查执行；不是当前已获准执行的操作。

## run_fl_demo.sh能力与缺口

| 问题 | 静态审查结论 |
| --- | --- |
| 小文件 | 支持 `--file`，没有最小字节限制；不传时自动生成41943040字节。可先生成65536字节固定内容文件并传入 |
| 指定完整1/2/4 | 从cluster_nodes.conf读取；目前权威CLIENTS恰为client1/2/4。没有单独 `--clients` 选项 |
| 强制不skip | **没有strict/no-skip选项**。step2_probe_clients仅当全部离线才失败；有任一在线就以ONLINE_INDICES继续，UFTP-H也只包含这些节点 |
| RF固定 | 脚本会down/up网卡、set monitor、set channel、写驱动功率/sysfs和iw txpower。不是只读核对当前157 |
| 清理 | 开始和EXIT/INT/TERM均调用cleanup_processes，按进程名全局pkill native/uftp/uftpd，还用pkill-f清HTTP helper；不是自有PID清理 |
| daemon恢复 | 不停止/恢复常驻daemon。若daemon仍开着，pkill native会杀其子进程并可能与自愈竞争；EXIT只清demo，不恢复APP |
| 工作目录 | --work-dir修改server与client run，但REMOTE_RECEIVED_DIR仍固定 `/var/lib/wfb-ng/fl_demo/received`，不能保证新run目录隔离；会删除同名接收文件和tmp内容，并递归chown WORK_DIR |
| native来源 | `wfb_v6_uplink`通过PATH解析，本地sudo bash和远端sudo可能选到不同路径；没有安装SHA、/proc/exe或提交门禁 |
| Python来源 | 本地直接执行PROJECT_ROOT/tests/demo/fl_demo_metrics.py，远端直接执行REMOTE_REPO下helper；默认REMOTE_REPO是projects/wfb-ng-fl。**不是从已安装包选择helper** |
| 额外native参数 | demo LINK_ID406、streams32/33；APP为7669206、1/2。demo启用feedback-window200/20ms，APP省略该参数，native默认0/0。直接跑demo会引入不止“关闭自动寻频”这一个差分 |

因此不能给出一个“原样run_fl_demo.sh + 某strict参数”来满足要求：这个strict参数并不存在。也不能只外层先SSH探活就声称内部一定不skip，探活与内部集合选择之间仍有竞态。

## 是否会从dirty工作区导入Python

`fl_demo_metrics.py`当前只导入argparse/hashlib/http.client/http.server/json/os/re/sys/time/pathlib/typing等标准库，没有wfb_ng import，也没有sys.path/PYTHONPATH操作。因此不能无证据声称它会直接导入dirty的APP训练模块。

但helper本身就是从本地/远端源码路径执行，仍可能是不同提交或dirty helper。当前本地helper恰与冻结701一致，SHA为 `b425156d618ac219df7a5e376c6014a5b664ed18ad38e93861f09d8adf0bf162`，这不保证将来执行时仍一致。冻结目录建议明确为每端 `/srv/wfb-ng-fl/deploy-701963a/source`，从 `/` 运行 `/usr/bin/python3 -I -B /绝对冻结路径/tests/demo/fl_demo_metrics.py`，忽略PYTHONPATH和当前工作区；运行前比对helper SHA及实际文件路径。

701已统一native应明确调用 `/usr/bin/wfb_v6_uplink`，确认安装文件及运行 `/proc/PID/exe` SHA均为 `f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1`。不能仅检查PATH中的command-v结果，也不能将之后的工单文本HEAD误作新部署版本。

## 最小可执行方案选择

建议不原样执行demo，也不修改production脚本。本次方案采用一个受审的短维护runner，复用**冻结701 helper/UFTP命令**，native参数沿用APP基线；runner只负责固定全员集合、自有PID、deadline和finally恢复。runner需由主线先协调恢复边界后具体落成审查；本轮未创建或执行现场runner。

严格目标表：fl00本机server；`fl@192.168.108.201` ID1/10.80.0.11、`.202` ID2/.12、`.204` ID4/.14。任一SSH、身份、native SHA、唯一RTL空口、TUN/路由、文件摘要失败即整项失败；不降低参与集合、不跳过节点、不自动重跑。不要求APP当前假装READY才能取故障，但必须无active job、无无线事务且维护窗口已由唯一owner取得。

一次对照使用65536字节单文件：一次UFTP向三端下发，再三客户端各一次受控HTTP PUT回传同一文件。不生成训练模型、update或作业。所有待测文件走TUN；SSH只做布置进程和取证，不能scp payload代替空口传输。短文件可能尚未触发长时饥饿，结果只说明该次固定频道文件路径，不外推训练效果。

## stop/restore边界（未来维护窗口）

1. 记录四端当前commit、native SHA/BuildID、daemon/native PID、实际cwd/env/ExecStart、unit/drop-in（/etc和/run）、JSON、RF读回及初始服务active状态；备份到各端新 `/srv/wfb-ng-fl/standalone-157-<runid>/backup`，不改已有配置。
2. 确认无active job、无radio事务。**先停三个client daemon，再停server daemon**，逐端确认inactive/MainPID0、cgroup退出且无本角色native/UFTP残留；避免server先停导致仍运行的client寻频。
3. 停服后只读确认四端monitor、157/HT40+/14dBm配置读回仍正确。若任何端已自动寻频到其他频道，**本方案中止并进入恢复，不擅自写回频道**；若主线将来确需一次明确set157，应另明确该动作属于维护准备，不能藏在demo里。
4. 只启standalone native和收发器，不运行ControlPlaneClient或任何daemon，因此没有Python自动HUNTING。沿用APP的native参数，固定RF状态不做down/up或功率写入。
5. 无论成功、失败、SIGINT或deadline，finally只终止runner启动且PID+starttime一致的native、uftpd、HTTP及上传器，wait回收，不执行pkill。清理顺序为发送器/HTTP/uftpd→standalone native→本次创建的TUN和新增路由（核对ifindex和所有权）。不清空APP work_dir、模型库、evidence或锁。
6. 确认standalone全部退出，恢复原来active的daemon，server先启动、随后三client。不要更换配置/二进制；读取实际身份/RF/服务/API，等待有界恢复并明确记录READY、CONNECTING、OFFLINE。若恢复未确认，则标记恢复失败并报告，不重启到“看起来通过”。

临时standalone会停止APP服务，故运行期间管理Web可能不可用。必须通过维护runner自身finally恢复原常驻服务，而不是误用HTTP abort恢复；这与正常APP作业停止协议不同。

## 命令模板：固定来源和64KiB文件

下面模板仅展示未来runner应执行的具体命令，变量由该runner建立并严格校验，**本轮没有执行**。所有产物在/srv独占新目录，不能覆盖同名文件。

```bash
RELEASE=/srv/wfb-ng-fl/deploy-701963a/source
RUN=/srv/wfb-ng-fl/standalone-157-<本次唯一标识>
HELPER="$RELEASE/tests/demo/fl_demo_metrics.py"
# 先确认RUN不存在，再创建；在主线维护窗口中执行。
# 生成工具会覆盖输出，所以runner必须先要求SOURCE不存在。
SOURCE="$RUN/source/diagnostic-64k.bin"
cd /
sudo -n env -u PYTHONPATH /usr/bin/python3 -I -B "$HELPER" generate-file \
  --output "$SOURCE" --size 65536 --seed 08-standalone157-64k-v1
sudo -n sha256sum "$SOURCE"
```

四端数据目录新建为root可读写的本run目录，receiver用独立received/tmp，不使用demo固定公共目录，不rm/chown既有数据。file-size和SHA先冻结；helper/installed native身份在启进程前严格核验。

## 命令模板：同APP native参数，去掉寻频宿主

`SERVER_IFACE`和各端`CLIENT_IFACE`必须来自四端唯一RTL8812AU驱动绑定关系和iw只读核对，不能选管理热点接口或从历史接口名猜测。所有native由runner以新session启动，记录PID+starttime、stdout日志和退出码，不nohup后撒手、不启动第二份TUN实例。

server模板（root身份，APP daemon已停止，fl-s已不存在）：

```bash
target_args=()
for nid in {1..10}; do
  target_args+=(--client-target "$nid:10.80.0.$((10+nid)):127.0.0.1:1")
done
/usr/bin/wfb_v6_uplink --role server --tun-name fl-s --tun-addr 10.80.0.1/24 \
  --node-id 255 --link-id 7669206 --uplink-stream 1 --downlink-stream 2 \
  --fec-k 8 --fec-n 14 --radio-bandwidth 40 --radio-control-port 9200 \
  --radio-mcs-index 3 --radio-short-gi --air-interface "$SERVER_IFACE" \
  --known-clients 1,2,3,4,5,6,7,8,9,10 "${target_args[@]}" \
  --downlink-pause-threshold-bytes 131072 --downlink-resume-threshold-bytes 65536 \
  --downlink-queue-packets-limit 64 --queue-summary-file "$RUN/server-queue.json" \
  --log-interval 1000
```

保留APP全槽位白名单和target映射以避免额外差分；它不等于测试目标扩到10块板。runner必须实际启动且核验的参与集合始终严格为1/2/4。省略feedback-window参数，保持APP/native默认0/0；不沿用demo的200/20ms反馈窗口。

三个client按固定表在各自板端执行模板：

```bash
# NODE_ID只能为1、2、4，CLIENT_IP分别为10.80.0.11/.12/.14
/usr/bin/wfb_v6_uplink --role client --tun-name "fl-c$NODE_ID" --tun-addr "$CLIENT_IP/24" \
  --node-id "$NODE_ID" --link-id 7669206 --uplink-stream 1 --downlink-stream 2 \
  --fec-k 8 --fec-n 14 --radio-bandwidth 40 --radio-control-port 9200 \
  --radio-mcs-index 4 --radio-short-gi --air-interface "$CLIENT_IFACE" \
  --uplink-pause-threshold-bytes 131072 --uplink-resume-threshold-bytes 65536 \
  --uplink-queue-packets-limit 64 --queue-summary-file "$RUN/client-queue.json" \
  --log-interval 1000
```

检查PID存活和TUN地址/路由，添加本次UFTP公共组播路由 `239.80.41.1/32 dev fl-*` 前先记录原路由，仅清理自己新增的路由。不给native发送射频控制UDP，不启PythonHUNTING。

## 单次下发与三端回传模板

各client只启动一次uftpd，使用独立本run目录和UID：

```bash
/usr/bin/uftpd -d -q -I "$CLIENT_IP" -M 239.80.41.1 -p 1044 \
  -U "$CLIENT_UID_HEX" -D "$RUN/received/" -T "$RUN/tmp/" -F "$RUN/uftpd.status"
# CLIENT_UID_HEX按ID严格为0x00000001、0x00000002、0x00000004
```

server一次发送，`-H`必须完整包含三UID，不从在线子集生成：

```bash
/usr/bin/uftp -q -I 10.80.0.1 -M 239.80.41.1 -p 1044 -U 0x00000064 \
  -H 0x00000001,0x00000002,0x00000004 -Y none -R 15000 \
  -r 0.1:0.01:2.0 -s 20 -L "$RUN/uftp.log" -S "$RUN/uftp.status" "$SOURCE"
```

0x64保留APP服务端UFTP UID100；demo用0xff，不在这个对照中额外改变。每端必须实际得到65536字节和同一SHA，再停止自有uftpd；失败一端则终止整项，不能提前算下发成功。

server在TUN地址启动冻结helper接收器，记录ready-file后再启动三个sender：

```bash
cd /
env -u PYTHONPATH /usr/bin/python3 -I -B "$HELPER" http-receiver \
  --host 10.80.0.1 --port 8080 --output-dir "$RUN/server-received" --ready-file "$RUN/http.ready"
```

三个client并发各一次（实际receiver在server，source-ip强制本端TUN，不走管理网）：

```bash
cd /
env -u PYTHONPATH /usr/bin/python3 -I -B "$HELPER" http-sender \
  --host 10.80.0.1 --port 8080 --file "$RUN/received/diagnostic-64k.bin" \
  --node-id "$NODE_ID" --source-ip "$CLIENT_IP" --timeout 120
```

helper timeout120是独立收发调用预算，与原APP I/O120一致，不修改daemon配置。runner应有整项有界截止和阶段截止，超时只报失败并finally恢复，不自动换大文件/换节点/改MCS重试。helper的http-sender会输出status_code，即使非201也未必以进程非零表示，runner必须显式核对三端HTTP201和server三个落盘文件的长度/SHA，而非只看sender退出0。

## 必须保存和不能声称的结果

保留完整参与集合、唯一runid、冻结source/helper/native SHA、PID/starttime、实际native argv、无线只读读回、单机monotonic阶段边界、native RX_BATCH/GRANT_SEQ/CLIENT_LOOP/READY/silent_remove/queue summary、UFTP及HTTP结果、6次文件摘要检查、所有失败及finally恢复事实。文件和完整native日志放/srv，仓库仅存小型结果/摘要/关键窗口。

standalone无Python控制面，所以没有APP HB/READY屏障，不能用文件传输成功声称APP READY、两轮训练、官方MNIST GPU、模型链、停止或全员confirmed recovery通过。固定频道成功只支持“该次小文件在无自动寻频宿主的相同native/RF条件下可交换”；小文件失败也不能自动归因为硬件。

本轮没有执行上述命令、没有新建现场目录或触碰四板。主线协调恢复流程并审查具体runner后，才可将此方案转成一次实机对照。
