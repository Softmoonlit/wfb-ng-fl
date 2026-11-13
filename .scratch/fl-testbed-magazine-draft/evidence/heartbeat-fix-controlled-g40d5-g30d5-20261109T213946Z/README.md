# 心跳修复后九客户端40/5与30/5受控比较

状态：STOP（缓存维护部分完成；fl02存在PackageKit update-packages事务，continuation未开始，零模型）。

## 目标与边界

本批只比较固定九客户端在同一发布、同一 native、同一 FEC、同一硬件和同一射频条件下的 `40/5` 与 `30/5` 调度。单次心跳 ACK 等待预算保持 `230 ms`，持续失活门限保持 `10 s`；本批不修改软件、native 制品、FEC、硬件或心跳参数。

固定服务端为 `fl00`，客户端为 `fl01`、`fl02`、`fl03`、`fl05`、`fl06`、`fl07`、`fl08`、`fl09`、`fl10`，`fl04` 排除。线上 commit 为 `cccac233291c5a79ca7a66bb2ebb210497ab4071`，native SHA-256 为 `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`。固定 FEC 为 RS(8,12)，基线信道 149，测量信道 157、HT40、Long GI、驱动功率索引18、DL MCS3、UL MCS4、UFTP 实际12000 Kbps。

旧两轮 `30/5` 的 `307.240 s` 和 `302.049 s` 仅作为历史参考，不混入本批。由于本批严格采用 `40/5` 先测、`30/5` 后测且各只执行一次，结果受顺序与时间漂移限制，不能宣称统计最优。

## 执行顺序

1. 完成完整只读准入：SSH、loaded identity、native唯一性、`iw`、GET_FEC、GET_RADIO/Long GI、九端 READY/IDLE、旧测试 unit 精确核查，以及全十端 `/srv/wfb-ng-fl` 可用空间至少512 MiB。功耗/频率事实必须逐项核验：CPU `online=0-5`、六个 `scaling_max_freq=1497600`、GPU `612000000`、EMC cap `2133000000`、`mrq_rate_locked=0`（EMC unlocked）、`nvpmodel -q` 成功、`nvpmodel.service` 成功且 `jtop inactive`；不使用“内存126”作为检查字段。
2. 在 40/5 下执行一次1MiB gate。gate失败立即STOP，不自动重试，恢复完整集合，不删除节点。
3. gate通过后，在40/5下执行一次44770302字节模型等量通信；下行和上行方向各600秒硬截止。
4. 40/5模型结束并完整恢复后，将调度切回30/5，在同一版本和同一九端集合下执行一次模型作为同期控制；最多两个模型 execute。
5. 任一模型的首个实质失败都停止后续执行并恢复完整集合；保留失败和partial事实，不以成功结果覆盖。
6. PackageKit若因维护被暂停，必须在完整 `finally` 中恢复并保存状态；不安装、不升级、不广泛删除。

## 证据与回报

每阶段保存命令、review/bundle SHA、实际 native/FEC/GI/iw、PID/start_ticks、完整进度、DL完成时间、UL每端进度与尾延迟、最终九份 size/SHA、cleanup/restore/final audit。仅在本批窗口且窗口与当前 PID 明确对应时，尝试从已有日志/状态收集 `HUNTING` 与 `validACK timeout` 计数；不追加主动抓包或改变原方案。

总执行边界为3000秒。每60秒回报 checkpoint、DL完成状态和UL进度；远端 owner 只有本 agent，父 agent 不执行 SSH。旧冻结根保持不动。

## 本次准入结论

十端 SSH/`sudo -n` 均可达，且每端 native 唯一。十端当前 loaded identity 均为目标 commit `cccac233291c5a79ca7a66bb2ebb210497ab4071`、native `44d6bbf...`，manifest 40项安装/源码校验通过；GET_FEC 全部为 `8/12`，GET_RADIO 为 HT40、Long GI (`short_gi=0`)、服务端 MCS3/客户端 MCS4；`iw` 全部为 149/HT40/monitor/索引18对应的 `-18.00 dBm` 读回。管理状态为 `fl00` IDLE、九端 READY/IDLE，`fl04` OFFLINE 且未接触；旧 `comm-smoke-*` unit 与两条专有组播路由均为空；PackageKit 全端为 `active/static`，没有被本批改变。

存储硬门槛未通过：准入时 `fl01=410955776`、`fl02=414330880`、`fl06=453849088` bytes，均低于 `536870912`。按既有授权成功证据生成的36路径/端精确候选索引并逐路径复核后，fl01/fl02 已无剩余可授权成功重复件；fl06 尚有36份，但只解决 fl06，不能补足完整十端硬门槛。唯一保留的 `comm-rsmc20261109T060537Z-g30-d5-model` 是既有证据明确保护的 `cleanup_ok=false/restore_ok=false` 不完整样本，未触碰。远端元数据只读归档写入本 root，未执行任何远端 unlink。

因此本批在存储准入处停止：未执行40/5 1MiB gate，未执行任何模型，模型 execute 次数为0；没有启动 runner、临时 unit 或远端 executor，也没有需要恢复的无线事务。`PackageKit`、常驻 daemon、native、FEC、心跳230ms/10s和硬件均未改变。canonical bundle/import及本地通信测试已离线验证，`59/59` 通过。

## 功耗与缓存 continuation 记录

新补做的十端只读功耗/频率核验全部通过，详见 `power-readonly-summary.json`：CPU online、六核上限、GPU、EMC cap、EMC unlocked、nvpmodel 服务成功和 jtop inactive 均逐项实际读取。

随后仅对 `fl01`、`fl02`、`fl06` 读取 APT 两个缓存、PackageKit 事务/包锁和文件系统占用，未执行维护。缓存读数为：`fl01` `pkgcache.bin=70968211`、`srcpkgcache.bin=70759897`；`fl02` 两文件均不存在；`fl06` `pkgcache.bin=71316893`、`srcpkgcache.bin=71108357`。PackageKit 均为 active/static，四个 APT/dpkg 锁均无持有者；缓存释放后的理论余量为 fl01 `552049004` bytes、fl06 `595602594` bytes，均低于用户要求的 `>=672 MiB`（且 fl02 无缓存可清）。缓存门槛不足且不满足 `>=672 MiB` 余量条件；因此不做 PackageKit hold、不删除两缓存、不删除 fl06 成功重复件。只读占用分类见 `cache-readonly-summary.json`：`fl01/fl02` 的 `/srv/wfb-ng-fl/install-cache` 各约 `832237568` bytes，内容为 torch/numpy 等 wheel、deb 和安装 manifest，作为非证据可再生候选仅记录、不删除未知项；`evidence`、`releases`、`client`、`mnist`、`calibration` 目录均只报告，不作可再生删除判断。缓存 continuation 保持 STOP。

## 安装缓存维护结果

完成依赖/运行路径审计后，fl01 的9个 wheel/deb已逐文件复制到新 root之外的 `.scratch/fl-testbed-magazine-draft/cache-archive/heartbeat-fix-controlled-g40d5-g30d5-20261109T213946Z/fl01/`，本地/远端 SHA 一致后精确 unlink，共释放 `832208285` bytes；保留 `install-artifact-manifest.json`、`install-client.py`，恢复命令见 `cache-archive-fl01-manifest.json`。fl06按既有36份成功证据逐文件核验，精确删除17份到 `1075736576` bytes，失败/partial/日志/B2均未触碰。

fl02在维护重试前仍有 `/usr/lib/apt/methods/https` 活跃，之后可用空间降至 `270966784` bytes；两次采样 initially 仅看到 packagekitd 子进程、无锁、无 dpkg，但 journal 随后明确记录 `get-updates /381_ddebddae` 完成并新建 `update-packages /382_aaaadbca`，持续请求 Ubuntu/NVIDIA/Docker 等大量包（约559条下载失败/不可用记录）。因此该节点被定性为真实 PackageKit update-packages 下载事务，不是只读 metadata 查询；未杀 apt/dpkg/packagekit，未归档或删除 fl02 install-cache。按授权执行过一次 PackageKit stop 尝试，服务最终保持原 `active/static`；维护结果见 `cache-maintenance-result.json` 与 `cache-maintenance-fl02-packagekit-journal.txt`。由于fl02仍不满足空间和安装空闲门槛，全组没有达到完整准入，fresh continuation、40/5 smoke/model和30/5 model均未开始。

## 心跳预算研究边界

`attempt_timeout_seconds` 生产 daemon 没有 JSON/CLI 入口，只在构造参数和 `send_heartbeat_once` 路径生效；同一值覆盖 periodic、`notify_state_change`、CONFIRMED 即时心跳和 hunt，且前三者共享状态锁等待。当前 `230 ms` 一律保持不动。调度稳定后再做有界 ACK 评估；不复测旧 `10/10`、`18/2` 矩阵，不自动复活历史捕获工单。后续若调整，必须经过正式接缝、回归和发布。详细记录见 `budget-research.json`。
