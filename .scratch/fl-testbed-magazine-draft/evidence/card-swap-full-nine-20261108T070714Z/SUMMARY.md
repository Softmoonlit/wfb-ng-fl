# 换卡后 inventory：MAC 门禁失败，已停止

基线：`evidence/full-nine-current-inventory/20261108T064041Z`。按用户声明 01↔06、02↔07、04↔09 计算预期 MAC，并并行 SSH 读取六台。六台均唯一 rtl88xxau_wfb，但五台 MAC 不符，因此严格执行“任一多卡或MAC不符停止并报告”。

|主机|预期来源 / MAC|实际接口 / MAC|结果|
|---|---|---|---|
|fl01|fl06 / fc:22:1c:50:0b:fe|wlxfc221c500bfe / fc:22:1c:50:0b:fe|匹配|
|fl02|fl07 / fc:22:1c:30:0c:bc|wlxbcec23372588 / bc:ec:23:37:25:88|不匹配|
|fl04|fl09 / bc:ec:23:37:25:88|wlxfc221c300cbc / fc:22:1c:30:0c:bc|不匹配|
|fl06|fl01 / fc:22:1c:30:0e:99|wlxfc221c300cbb / fc:22:1c:30:0c:bb|不匹配|
|fl07|fl02 / 88:e6:28:52:aa:e4|wlxfc221c300e99 / fc:22:1c:30:0e:99|不匹配|
|fl09|fl04 / fc:22:1c:30:0c:bb|wlx88e62852aae4 / 88:e6:28:52:aa:e4|不匹配|

实际来源映射：fl01←旧fl06，fl02←旧fl09，fl04←旧fl07，fl06←旧fl04，fl07←旧fl01，fl09←旧fl02。这与声明的三组互换不符，未按实际发现擅自接受其他换卡方案。

六台当前服务仍配置原接口，实际新卡均为 managed、未显示信道；iw 显示 txpower -18.00 dBm。六台运行 release manifest 均 c6659488f64976185ffe02a7c856f7fd8c821ee0；对应 service/loaded/native/iw/nvpmodel 原始信息见各 `flXX-before.json`。USB 设备路径见其中 `interfaces[].usb_path`，drop-in 原文见 `dropins`。

未修改 drop-in，无需修改前备份；未重启服务，未执行 GET_RADIO 或后续九客户端采样，未发送通信、未连接 fl03、未修改 registry、未修改源码、未提交。不能报告全九 READY。后续完整复测须先解决声明与实际 MAC 映射不一致。
