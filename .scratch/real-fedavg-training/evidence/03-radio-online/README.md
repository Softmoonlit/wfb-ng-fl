# 工单 03：四板无线在线配置验收记录

日期：2026-10-26。实施基准：`d5709e74a087445f95268eb386676fc6397340be`，该基准上的工单03实现工作区。正式底座产物 SHA-256：`8ddb419be0c99e174eeb91239092f266398ea248e0c3c71eff643e28bbcc08e0`，四台安装值一致。软件检查及双轴审查包含实机后补齐的恢复确认屏障和最终原子保存实现；四板正常路径检查针对相同在线更新协议与底座产物，完整故障矩阵留给票12。

## 拓扑与初始条件

| 角色 | 主机 | NODE_ID | TUN | 空口 MAC |
| --- | --- | --- | --- | --- |
| Server | fl00 | 255 | fl-s / 10.80.0.1 | fc:22:1c:10:01:57 |
| Client | fl01 | 1 | fl-c1 / 10.80.0.11 | fc:22:1c:30:0e:99 |
| Client | fl02 | 2 | fl-c2 / 10.80.0.12 | fc:22:1c:30:0c:bb |
| Client | fl04 | 4 | fl-c4 / 10.80.0.14 | fc:22:1c:50:0a:88 |

初始与结束配置均为信道157、HT40+、14 dBm、下行MCS3、上行MCS4、UFTP 15000 Kbps，GI short、FEC 8/14。使用正式常驻服务及现有TUN，不运行会重建底座的演示脚本。客户端测试启动用 `/run/systemd/system/wfb-fl-client-daemon.service.d/radio.conf` 显式指定上行MCS4与14 dBm，避免旧默认MCS6在发现阶段影响通信；这是易失测试配置，未写入源代码默认值或 `/etc` 启动配置。

## 实机步骤与结果

1. 普通入口只提交下行MCS4、上行MCS3，冻结全部节点 `[1,2,4]`。返回 `finalized`，省略速率后自动采用22000 Kbps。见 [normal-mcs4.json](normal-mcs4.json)。
2. 四端底座PID分别为51597、16693、7460、16959；客户端TUN ifindex分别49、21、47。MCS变化后不变，HT20变化及恢复后客户端PID仍不变。
3. 专用本机 `/api/v1/radio/calibrate` 显式提交HT20、下行MCS3、候选4000 Kbps。返回 `finalized` 与 `calibration=true`。见 [ht20-calibration.json](ht20-calibration.json)。4000仅为本轮候选，不是标定后的推荐值。
4. 四端 `iw dev ... info` 实际读回20 MHz、信道157。三台客户端对端接收日志均出现 `RX_ANT 5785:3:20`。见 [fl00网卡读回](ht20-fl00.txt)、[fl01记录](ht20-fl01.txt)、[fl02记录](ht20-fl02.txt)、[fl04记录](ht20-fl04.txt)。这项结论有网卡读回和对端观测支撑，不依赖配置缓存。
5. 原TUN上使用独立1144端口的uftpd/uftp进行1 MiB文件下行，三台最终目录文件均为1048576字节，SHA-256均为 `aab766f901ca57e7fddce2928ca474aa769317e8de2f398a882c18c67bdceee0`。Server收到三台会话级COMPLETE并记录Group complete。见 [uftp日志](ht20-uftp.log) 和 [状态文件](ht20-uftp.status)。接收进程随后按本轮专用PID停止。
6. 普通入口恢复40 MHz、14 dBm、MCS3/4、15000 Kbps，收到全员 `finalized`、`calibration=false`。见 [restore40.json](restore40.json)。客户端PID不变，网卡读回40 MHz，见 [fl01恢复](restored-fl01.txt)、[fl02恢复](restored-fl02.txt)、[fl04恢复](restored-fl04.txt)。
7. 显式保存正常启动配置；在线调整本身不自动保存。随后检查结束时Server为IDLE，节点1、2、4全部READY，三台客户端服务active。最终原子保存helper修改另以公开保存／重新加载检查验证。

Server的实际发送日志分别记录generation1的40/MCS4、generation2的20/MCS3、generation3的40/MCS3；每代都记录data和control帧使用同一配置，GI=1、FEC=8/14。完整底座日志、安装日志和当次工作区diff另保存在本机 `/var/tmp/wfb-ticket03/`；本目录保留可随Git交付的关键原始证据，避免仅依赖 `/tmp`。

## 软件检查与审查

- 受影响无线模型、REST、事务／租约、客户端、运行时应用、Console、同源HTTP、页面及Server Daemon检查：289项通过，见 [完整输出](ticket03-tests-complete.txt)。两条警告来自现有第三方依赖。
- 最后复用原子JSON保存helper后，REST单文件13项再次全部通过，Server Daemon单文件mypy通过；此前六个受影响Python实现文件mypy均通过。
- `make test_v6_radio_update` 通过，见 [输出](cpp-check.txt)；构建阶段另外通过session recovery及encrypted session检查。
- 双轴审查基准为d5709e7。Standards轴和Spec轴提出的问题均已修复并复核闭合，最终无剩余可行动发现。
- `git diff --check` 通过。未恢复已停止维护的全仓旧自动回归或namespace验收。

## 后续工单责任

这不是20 MHz速率表交付，也不是GPU训练验收。票09须对MCS3/4/5/6重复测量并交付组合表；正常HT20入口在缺表时继续拒绝。票12执行实际失联、停止、租约与故障矩阵；票10实现作业内初始／轮次间算法重配。操作及标定命令见[部署说明](../../../../docs/deployment/空闲无线在线配置与HT20标定.md)。
