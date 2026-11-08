# f376a1f 全11节点统一发布与 runner CLI 交接

冻结源码：`f376a1f1ab4896a00163938784fe36d664058550`，已验证88099f0为其祖先，包含FEC startup与HT40 calibration能力。只使用现有release-online helper，未修改helper、业务代码或consumer源码。

全11目标fl00..fl10包含fl03。执行前全11无active/activating/deactivating的comm-*.service；server preflight要求无active_job且IDLE。preflight全11通过后按stage/prepare/stop/install/start/audit推进，七阶段全11均exit=0，见state.json与各阶段证据。

## 最终实际状态

final-status.json：server IDLE、无active_job，客户端1..10全READY。

final-verification.json和audit-fl*.json：全11实际native argv K/N=8/14，GET_FEC实际响应8/14；server native实际grant18/guard2，source JSON与state.expected同为18/2/FEC14。所有native实际exe SHA为ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56，UFTP与prepare记录摘要一致，nvpmodel为15W，radio实际LGI/149/HT40/功率索引18，server MCS3/client MCS4。

硬件映射严格保留：preflight iface=prepare iface=实际Python --interface=实际native --air-interface。identity-fl*.json全11核验配置JSON字节不变，全部客户端startup before/after完全相等（既有显式--fec-n 14保留）。标准Python模块入口不变，未添加wrapper；审计证据为实际proc身份、隔离解释器模块解析与安装/冻结源码摘要，不声称读取resident sys.modules。

本轮仅部署与审计，无模型或合成载荷传输、无通信实验、无calibration执行、无切157或MCS重配。

## Runner直接传参（不需改源码）

```text
--release-root /srv/wfb-ng-fl/releases/heartbeat-f376a1f1ab48-20261108T104539Z
--release-commit f376a1f1ab4896a00163938784fe36d664058550
```

每节点manifest：`/srv/wfb-ng-fl/releases/heartbeat-f376a1f1ab48-20261108T104539Z/manifest.json`。
冻结source：`/srv/wfb-ng-fl/releases/heartbeat-f376a1f1ab48-20261108T104539Z/source/wfb_ng/fl`。
安装目录：`/usr/local/lib/python3.10/dist-packages/wfb_ng/fl`。
机器可读交接为runner-identity.json；各节点完整manifest、loaded与配置核验为identity-fl00.json..identity-fl10.json。

source.tar按已有.gitignore规则不入Git，SHA保存在state.json，可由冻结commit与现有CLI archive路径列表重建。此次仅以中文提交本轮证据，不混入父agent其它工作。
