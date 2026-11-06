# 统一 15W 执行记录

用户授权全10台统一板级15W；无线参数未在本步骤改变，fl01修复实现尚未开始。

## 配置纠正

设备树表明客户端为p3767-0003-super，fl00为p3767-0005-super。NVIDIA `/etc/systemd/nvpower.sh`将两者映射到`nvpmodel_p3767_0003_super.conf`，其中15W是mode0。原先fl00链接0000_super，八客户端链接0001_super，fl04使用自定义0003_super_40w，不能用同名15W掩盖SKU错误。

全体已保留旧链接`/etc/nvpmodel.conf.before-magazine15w`，改用官方0003_super配置。九客户端执行过必要重启，boot ID与服务状态已存证；fl00应用mode0未重启。

## 核验边界

`*-final.json`保存verbose查询。全体名称为15W/mode0、6CPU在线、daemon active，均无缺失CPU/PVA路径错误。

- fl01/02/05/06/07/08/09/10：CPU上限1497600kHz、GPU上限612000000Hz、EMC上限2133000000Hz，实际读回与配置相符。
- fl00：已完成。用户补充定位：旧8核SKU配置导致启动时功耗服务失败；jtop自动执行jetson_clocks又导致EMC锁频。禁用停止jtop并重启后，2026-11-06 09:57 UTC核验开机已267秒（超过原60秒自动锁频触发窗口），nvpmodel.service为Result=success/ExecMainStatus=0，CPU1497600kHz、GPU612000000Hz、EMC2133000000Hz均匹配，EMC `mrq_rate_locked=0`。无需重装系统。
- fl04：已完成。用户补充的启动顺序证据指出，独有的 `jtop.service` 抢先初始化GPU，导致功耗服务退出234。已执行 `systemctl disable --now jtop.service` 并重启；新boot ID为 `c17003c3-ab9d-46c7-a4f9-527f55ee48f9`。复核 `nvpmodel.service` 为 `Result=success`、`ExecMainStatus=0`；15W/mode0，CPU上限1497600kHz、GPU上限612000000Hz、EMC上限2133000000Hz均匹配，TPC mask240亦匹配。jtop已disabled/inactive，FL客户端服务active。原始证据：`fl04-jtop-disabled-verified.json`。此前重复重启未恢复的记录保留在 `fl04-second-reboot.txt`。

当前全10台均已核验15W及CPU/GPU/EMC上限，功耗服务本次启动全部成功。全体EMC `mrq_rate_locked=0`。fl01/02/05/06/07/08/09/10均不存在jtop.service及/usr/local/jtop/config.json；fl00/fl04的jtop为disabled/inactive，但配置仍有boot=true，若恢复jtop长期运行，须先关闭自动jetson_clocks并明确排序在nvpmodel成功应用之后。原始全体核验：`../jtop-audit-20261106T095719Z/*.json`。

不能用模式名称作为全员完整应用成功的证据。旧25W/40W模式并非此前已正确套用对应SKU功耗限制。
