# 六台 rebind 完成；九客户端未全 READY

用户追加授权以“六卡唯一且 MAC 集合与基线六卡完全相同”为门禁。重启前后集合门禁均通过。已仅对 fl01/02/04/06/07/09 更新既有 `zzzzz-heartbeat-release.conf` 中 `--interface`，并执行 `systemctl daemon-reload` 与 `systemctl restart wfb-fl-client-daemon.service`，六台全部成功。

每台原 drop-in 保存于本目录 `flXX-dropin-backup.conf`，远端备份为同路径 `.conf.card-swap-20261108T070828Z.bak`；准确路径和前后文本见 `flXX-rebind-result.json`。

## 实际卡映射

旧卡来源以 `full-nine-current-inventory/20261108T064041Z` 为准。

|现主机|旧卡来源|绑定接口|MAC|
|---|---|---|---|
|fl01|fl06|wlxfc221c500bfe|fc:22:1c:50:0b:fe|
|fl02|fl09|wlxbcec23372588|bc:ec:23:37:25:88|
|fl04|fl07|wlxfc221c300cbc|fc:22:1c:30:0c:bc|
|fl06|fl04|wlxfc221c300cbb|fc:22:1c:30:0c:bb|
|fl07|fl01|wlxfc221c300e99|fc:22:1c:30:0e:99|
|fl09|fl02|wlx88e62852aae4|88:e6:28:52:aa:e4|

## 九客户端核对与 READY 观察

九台均唯一专用驱动接口，实际服务接口与网卡一致；loaded PID/commit 与 manifest 匹配，安装文件 manifest 无差异，均为 `c6659488f64976185ffe02a7c856f7fd8c821ee0`。九台实际 `/proc/<native-pid>/exe` SHA256 均为 `ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56`，匹配 manifest。

九台 `iw` 均 monitor、channel149、40MHz、center5755MHz、txpower -18.00 dBm（driver idx18）；本机 UDP GET_RADIO 均 status0、short_gi0（Long GI）、bandwidth40、mcs4。九台 nvpmodel 均 15W。

服务端只读 `/api/v1/status` 连续观察约58秒，共29个采样；无任何一次全九 READY。fl04 全程 OFFLINE；fl07 和 fl10 曾 CONNECTING；fl08 末两次 OFFLINE。下表为整个窗口统计与末次状态：

|客户端|实际接口|末次 READY 状态|窗口内采样统计|
|---|---|---|---|
|fl01|wlxfc221c500bfe|READY|READY×29|
|fl02|wlxbcec23372588|READY|READY×29|
|fl04|wlxfc221c300cbc|OFFLINE|OFFLINE×29|
|fl05|wlxfc221c100157|READY|READY×29|
|fl06|wlxfc221c300cbb|READY|READY×29|
|fl07|wlxfc221c300e99|READY|READY×26, CONNECTING×3|
|fl08|wlxfc221c500a88|OFFLINE|READY×27, OFFLINE×2|
|fl09|wlx88e62852aae4|READY|READY×29|
|fl10|wlxfca386b38672|READY|READY×28, CONNECTING×1|

未执行通信冒烟/任务发送，未连接 fl03、未修改 registry、未修改源码或提交。GET_RADIO 仅发往各客户端本机 127.0.0.1:9200。未重启其它三台；本轮不增加额外重启或射频修改。父代理需据未全 READY 的实际结果决定后续冒烟。

原始证据：`ready-samples.json`、九台 `flXX-after.json`（含 native/iw/GET_RADIO/USB/nvpmodel/loaded）、六台 `flXX-before.json`、`actual-mapping.json`、`set-gate-before.json`、`set-gate-after.json`、`validation.json`。
