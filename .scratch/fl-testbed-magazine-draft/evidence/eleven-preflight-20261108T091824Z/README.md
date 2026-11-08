# fl03/fl04 十一板基线准备完成

用户续授权接受实际MAC分配，原映射偏离已记录，不再作为阻塞。初次只读阻塞报告保留于 INITIAL-BLOCKED.md；以下为最终状态。完成时间约2026-11-08 09:25 UTC。

| 节点 | 显式绑定接口 | daemon/native PID | node/TUN | 实际RF | 电源 |
|---|---|---|---|---|---|
| fl03 | wlx5cffffaf6d8c | 852 / 1478 | 3 / fl-c3 / 10.80.0.13/24 | 149/HT40/LGI/idx18，MCS4 | 15W/mode0 |
| fl04 | wlxfc221c300f04 | 13618 / 13645 | 4 / fl-c4 / 10.80.0.14/24 | 149/HT40/LGI/idx18，MCS4 | 15W/mode0 |

两台 final-audit-*.json 均 exit0：完整manifest文件集合及SHA、loaded路径/SHA/PID/commit、唯一native实际exe SHA、native接口与实时本机UDP radio query、实际iw均通过。commit 98dda459fb9a1cecf64fd03e224427b8b7f689fe；native SHA ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56。接口仍为最初核验的rtl88xxau_wfb/USB5000M。

fl04仅备份原dropin、更换显式interface和release/interface、标准systemctl stop/start；没有更新软件。fl03使用原98dda发布source.tar（SHA cae0ced8ebbb13b2567d84ed820820a1d2ed6e46897bbc65c7f46cdd5c7c07dd），旧软件/config/native/UFTP已备份至该release/backup；安装仍调用原install-v8.sh和原helper install/start代码。适配helper仅将目标主机限定为fl03，未放宽安装或audit断言；原全十目标发布入口未修改。continue-install.py保存stage/备份/安装执行细节，所有返回码已留档。

UFTP/uftpd两台均5.0.3，读取二进制内版本横幅，SHA与本机fl00实际artifact及既有UFTP统一记录一致：uftp=71d9c15497b3bd4286a2c60c9d37871a50b7032793eae8d4bf7b3b8977e015e3；uftpd=0d7a31c6cbf783ad5ecced0bdce6705c30530a258fdf605d555c748827aa85b9。只从本机只读复制artifact，没有修改fl00。早期不支持的-V检查保留，最终verified-power用内嵌横幅正确核验。

fl03只读确认设备树p3767-0003-super、六核，官方profile和nvpmodel.conf链接正确，15W为mode0。旧nvpmodel.service退出234、jtop启用，旧daemon存在Too many open files。停用禁用jtop后nvpmodel -m0明确要求Golden context必要重启；经用户明确授权确认yes，仅fl03重启一次。boot从c65dc463-dc5c-4223-9e78-568228c720f6变为6a6663d5-5dc1-4d42-9a95-dd098c8dedc0，SSH有界恢复，最终uptime超过60秒。两台nvpmodel.service Result=success/ExecMainStatus=0，jtop disabled/inactive，TPC mask240、CPU1497600kHz、GPU612000000Hz、EMC2133000000Hz均匹配，EMC mrq_rate_locked=0。profile和电源原始结果见power-profile/power-apply/power-confirm-reboot/verified-power。未更改官方profile。

没有发起通信实验、训练或文件传输实验；没有修改registry、timeout或验收门限。该结果只证明两个节点恢复基线，不代表长期链路稳定性或通信实验成功。final-resident保存最后service/PID、运行PATH、manifest/loaded、节点配置与有界journal。所有原始失败及重启期间SSH断连均保留。
