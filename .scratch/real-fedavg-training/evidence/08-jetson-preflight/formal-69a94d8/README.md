# 69a94d8 四端统一部署与运行时来源核验

执行提交：`69a94d84b006666d01e257155bc7a2fc969bedef`。

- git archive SHA-256：`d723b33001929f5ff4ae687ff4ad0150c75b15abc2369725511b4209de20701b`
- 部署只使用该 archive；没有同步当前 dirty 工作区，也没有包含 10/11 等未提交内容。
- e53 正式部署和 diagnosis 证据保持原路径未覆盖。

## 部署结果

四端均完成当前 JSON、systemd unit/drop-in 备份，包含 `/run/systemd/system/wfb-fl-*`，之后完成 daemon stop、cgroup/进程退出确认、同一 archive 构建、`install-v8.sh` 安装、持久配置写入和启动。原始逐端记录为 `flXX-backup-build.txt`、`flXX-stop.txt`、`flXX-install-config.txt`、`flXX-start.txt`。

实际服务配置：

- fl00：`wfb-fl-server-daemon.service` active，server JSON 使用 `/srv/wfb-ng-fl/server` 和 `/srv/wfb-ng-fl/mnist`。
- fl01/02/04：`wfb-fl-client-daemon.service` active，ExecStart 含 `--mnist-data-dir /srv/wfb-ng-fl/mnist --work-dir /srv/wfb-ng-fl/client`。
- 训练入口和 server 三期限配置按 08 计划写入；本次没有发训练请求。

安装身份逐端均为：

```json
{"commit":"69a94d84b006666d01e257155bc7a2fc969bedef","schema_version":1}
```

主 API 最终报告 server `IDLE`、无 active job，节点 1/2/4 均 `READY/IDLE`。无线读回保持原40 MHz基线：信道157、40 MHz、中心5795 MHz、空口驱动报告 -14.00 dBm（配置值14）、下行MCS3、上行MCS4、15000 Kbps。没有修改 RF 参数。

## 运行时来源核验

每端不是只检查安装文件，而是读取有效 unit、daemon PID `/proc` 和干净 Python `find_spec`：

| 节点 | daemon PID | `/proc/PID/cwd` | `PYTHONPATH` | `wfb_ng.fl.service` 来源 |
| --- | ---: | --- | --- | --- |
| fl00 | 196920 | `/` | 未设置 | `/usr/local/lib/python3.10/dist-packages/wfb_ng/fl/service.py` |
| fl01 | 28202 | `/` | 未设置 | 同上 |
| fl02 | 21249 | `/` | 未设置 | 同上 |
| fl04 | 28681 | `/` | 未设置 | 同上 |

systemd `WorkingDirectory` 四端为空，实际 `/proc/PID/cwd` 为 `/`，不是源码仓库。服务进程环境中没有 `PYTHONPATH`，也没有仓库路径。客户端有效 ExecStart 逐端包含：

```text
/usr/bin/wfb-fl-client-daemon --config /etc/wfb-ng-fl/node.json --channel 157 --bandwidth-mhz 40 --radio-txpower-dbm 14 --uplink-mcs 4 --mnist-data-dir /srv/wfb-ng-fl/mnist --work-dir /srv/wfb-ng-fl/client
```

server 有效 ExecStart 为：

```text
/usr/bin/wfb-fl-server-daemon --config /etc/wfb-ng-fl/server.json
```

从 `/tmp` 启动、`PYTHONPATH=` 清空的 system Python `find_spec` 结果四端一致，且来源均为 `/usr/local/lib/python3.10/dist-packages`：

| 模块 | SHA-256 |
| --- | --- |
| `wfb_ng.fl.service` | `c90645c37b2992093fbb6c2c5b7a94e9ec639a5e8f66d4bbf039a3850299237a` |
| `wfb_ng.fl.mnist` | `c79b1a5d36b2039ff1f12d665c68535ca083491a444f1d7f3453502f4103d5e9` |
| `wfb_ng.fl.mnist_data` | `6eb32313ab19c6c8e7e95a2527fb22ff582c184dc6addf024a70b85a9aba217c` |
| `wfb_ng.fl.server_daemon` | `2e0774b7da254580b6d29daa88125c3e4ace9d6f1d213dd235a7c34d59d440db` |
| `wfb_ng.fl.client_daemon` | `2d00588e97aed92f0983e4aad784755b1b445ba0b6c25c770f46711205b51eb3` |
| `wfb_ng.fl.console` | `7012ae4e8a5d8fa87ccfd3c26b6095c2313e7432dc8bc2f226f3e16ba402c9fc` |

实际运行来源审计原始文件为 `flXX-runtime-source-audit.json`；此前初次 fl00 身份检查从当前仓库导入 `99865ef` 的错误也保留在 `fl00-identity-69.txt`，随后以 `/tmp` clean import 重做并得到上述正确结果。该错误没有改变现场进程或配置。

## critical SHA 与 native 差异

四端 `service.py` 和 `console.py` SHA 一致。`wfb_v6_uplink`：

- fl00：`8ddb419be0c99e174eeb91239092f266398ea248e0c3c71eff643e28bbcc08e0`
- fl01/02/04：`b4f81258c910b9bcd96fa6675627aa830d6b6c14d8babf93ba6cdf894ad9489c`

native 二进制摘要不同，原报告以server/client角色解释差异没有证据，现已撤回：该程序是同一多角色程序，不能以角色不同解释构建摘要。后续只读取证确认四端编译器包修订号和ELF Build ID不同，详细ABI、编译器、ELF及object摘要保存在native-unification目录；这也不等于已证明全部字节差异的原因。用户随后授权从冻结69源码构建一次同一native分发四端，后续记录追加在该目录，不覆盖以上历史摘要。

服务、有效命令、运行时来源、critical SHA 和 iw 读回均保存在对应原始文件及 `critical-runtime-summary.json`。随后已完成all3、only-fl02、fl01+fl02、restore-all四阶段各80秒数量差分。全部capture有end记录且socket丢包为0；finally恢复三客户端，结束时全员READY。原始phase-summary及capture不重跑不覆盖。最新恢复只读审计在resume-audit，含daemon实际cwd和实际环境下find_spec，以及/proc native exe摘要。
