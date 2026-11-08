# Candidate C：同 f376a1f 全11节点 FEC12 冷启动交接

已按本轮授权完成全11节点（fl00..fl10，含fl03）统一切换 RS8/12。标准Python模块载入机制、硬件映射、server grant18/guard2、149/HT40/LGI/idx18与初始DL3/UL4保持不变。没有执行DL4/UL6无线切换、smoke、模型传输、calibration或切157。

## 唯一发布身份

```text
--release-root /srv/wfb-ng-fl/releases/heartbeat-f376a1f1ab48-20261108T105344Z
--release-commit f376a1f1ab4896a00163938784fe36d664058550
```

该root在每个节点均存在标准manifest.json、source/wfb_ng/fl、actualargs.json和loaded.json。源码归档SHA与上一批同f376a1f FEC14发布严格相等，未改源码commit或标准入口。runner-identity.json为机器可读交接。

## 最窄helper修改与本地测试

server仅在install阶段（CLI必须已完成全11stop阶段，node另断言MainPID=0）调用install_server_fec。读取原server.json，先验证18/2不变，只更新fec_n为state.expected.fec_n=12；同目录临时文件保留owner/group/mode，fsync后os.replace原子替换并fsync目录。before/after/expected记入server-fec-configuration-change.json。本批客户端原有--fec-n 14改为12，其它选项保留，逐端记录startup-configuration-change.json。

local-tests.txt为11项通过测试，其中新增FEC12原子替换/只改FEC/保留权限与其它嵌套JSON，以及调度不符不写入测试。没有修改其它业务或consumer代码。

## 阶段屏障与审计

comm-units-gate.json确认全11没有active/activating/deactivating的comm unit。全11preflight/stage/prepare通过后才进入stop；全11stop通过后才进入install；全11install通过后才进入start。state.json记录七阶段完成，各阶段每节点exit=0。没有原地单台重启或在部分native仍运行时切另一端FEC。

全11audit已验证实际native argv及GET_FEC均8/12，server source JSON与实际native调度18/2；installed文件与冻结source/manifest一致，native实际exe SHA仍为ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56，UFTP与备份摘要相同，nvpmodel15W，LGI/149/40MHz/idx18与server MCS3/client MCS4通过。

identity-fl*.json逐端确认：server JSON除fec_n外与before完全相同，client node.json字节不变，client启动参数除--fec-n值外完全相同。final-verification.json核对preflight/prepare iface与实际Python/native绑定相同。Python证据是实际proc入口与隔离解释器模块映射、源码摘要，不声称resident sys.modules。

最终final-status.json：server IDLE、无active_job，10个client全READY。状态收尾期间fl04曾短暂CONNECTING，后续只读复核已恢复READY，无追加重启或参数修改。

父agent可使用以上CLI身份继续Candidate C的真正DL4/UL6无线切换及smoke；本任务止于全11FEC12冷启动与审计。
