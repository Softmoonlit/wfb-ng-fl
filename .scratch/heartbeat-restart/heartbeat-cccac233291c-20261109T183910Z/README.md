# fl08确认后即时心跳修复：当前十板统一发布

源码为`cccac233291c5a79ca7a66bb2ebb210497ab4071`。参与者固定为服务端fl00及九客户端fl01、fl02、fl03、fl05、fl06、fl07、fl08、fl09、fl10；fl04排除，不启动其服务。

`state.json`记录十个目标在preflight、stage、prepare、stop、install、start、audit七阶段全部成功；每阶段全组通过后才进入下一阶段。远端冻结目录为`/srv/wfb-ng-fl/releases/heartbeat-cccac233291c-20261109T183910Z`，备份、安装manifest及实际进程参数均保存在各节点该目录。

本次只改变控制面源码及对应诊断工具；生产native源码未变，复用同一已验证native产物，十个运行中`/proc/<pid>/exe` SHA-256均为：

```text
44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2
```

逐端`audit-*.json`核验标准模块启动参数、隔离解释器安装路径、完整安装文件与冻结源码摘要、build identity、运行native摘要及参数、GET_RADIO、GET_FEC和实际iw读回。发布基线为149/HT40、Long GI、驱动索引18、下行MCS3／上行MCS4、RS(8,12)、授权30ms／保护间隔5ms；UFTP程序摘要保持原样。`iw`的负数txpower读数仅作为该驱动索引读回，不当作物理18dBm证明。

原代码回归复现、修复契约、101项本地回归与同配置CMD_SET_RADIO授权检查见[fl08诊断记录](../../fl-testbed-magazine-draft/evidence/fl08-admission-diagnosis/README.md)。核心行为为事务内统一禁止心跳发送，CONFIRMED完成后先发送确认ACK，再立即发送带身份的真实心跳；只有匹配心跳ACK更新活性锚。230ms和十秒门限不变。

发布成功不代表两次模型闭环验收通过。后续验收独立保存在[修复后验收根](../../fl-testbed-magazine-draft/evidence/fl08-heartbeat-fix-validation/)。首个执行器因漏带`check-all-ready.py`在模型前停止，零模型执行；当时十板实际审计通过、九端均READY/IDLE149，不将该工具错误归为无线或参数失败。原STOP与原始记录保留，补齐依赖后的连续两轮另立continuation批次，最终结果以其收尾记录为准。
