# 基线收口验证

## 范围与结果

- 固定Long GI：常驻server/client、作业角色、在线重配和console配置投影一致；不提供GI配置项。
- 驱动索引1..63、默认18：启动默认、安装模板、UI及本批cluster一致，不称为物理dBm。
- 旧patch别名target_channel/txpower_dbm不再转译，只接受channel/radio_txpower_dbm；现有schema字段名保留。
- 通用集群校验支持1..10客户端及固定TUN映射；本批配置九候选，不将暂时断电当作减员。
- 15W为独立板级限制；诊断149与正式目标157明确区分。

## 检查

最终命令：

```bash
python3 -m pytest -q \
 wfb_ng/tests/test_fl_radio.py \
 wfb_ng/tests/test_fl_client_daemon.py \
 wfb_ng/tests/test_fl_server_daemon.py \
 wfb_ng/tests/test_fl_console.py \
 wfb_ng/tests/test_fl_client_jobradio.py \
 wfb_ng/tests/test_fl_radio_runtime_application.py \
 wfb_ng/tests/test_fl_client_radio_application.py \
 wfb_ng/tests/test_fl_link_radio.py \
 wfb_ng/tests/test_fl_radio_switch.py \
 wfb_ng/tests/test_fl_radio_page.py -k 'not real_chromium'
```

结果：258 passed, 1 deselected，75.09秒。未运行Chromium入口；页面现有Node VM行为测试已通过。两项pyroute2弃用警告不影响通过。

相关测试使用动态回环端口、mock网卡和出站隔离；未通过暂停生产服务来运行测试。

- shell语法、集群配置验证、发布脚本编译、git diff --check通过。
- 三处fixture失败已修：硬件重建fake peer使用公共challenge/confirmed ACK，readiness并发测试从实际配置取索引避免额外alignment报文。保留首次失败输出entry-tests.log。
- 移除旧patch别名后，重配测试同步改用canonical；保留中间失败entry-final.log与最终switch-final.log。
- 双轴审查最终均无相关P1/P2。历史DBM字段名保留，不新增兼容层。

## 发布边界

本轮显式目标fl00/fl01/fl04/fl05/fl06。fl02/fl07/fl08/fl09/fl10为用户确认断电，暂不连接。发布入口`.scratch/heartbeat-restart/release-online.py`要求完整commit和精确目标集，逐阶段全员通过；不使用通用部署器跳过离线节点路径。

审计同时核验加载文件、native proc摘要、iw实际149/HT40/索引18，以及只读CMD_GET_RADIO返回short_gi=0。候选源码的native部分须与d898d21一致，复用已核验native，不重建。

本文件记录提交前验证；实际发布结果另存对应release目录。当前小集合结果不替代九客户端待命、两次文件闭环或正式157验收。

## 原始证据范围

实验目录保留此前盘点、板级功耗、网卡A/B、索引14/25/18试验和脚本失败证据。历史记录中的参数不覆盖当前规格。source.tar/bootstrap-source.tar可从对应提交重建，只保留本地文件与已记录摘要；临时心跳迁移脚本和中间日志不混入当前软件发布。
