# 十台统一发布：audit 未通过

本轮固定源码 `c6659488f64976185ffe02a7c856f7fd8c821ee0`，工具提交 `b5754a7`。仅部署一轮；十台 preflight/stage/prepare/stop/install/start 均成功，最终 audit 失败即停止推进，state.json 保留未完成 audit 的原始结果。

十台已使用本轮 release wrapper，loaded commit 与 MainPID 一致；完整安装 manifest 校验、唯一 native、native SHA、当前唯一 rtl88xxau_wfb 接口绑定、LGI、40 MHz、MCS 及 idx18 均通过。十台 daemon 在失败后的只读核查中均为 active/running，无遗留停服节点，无需追加启动恢复。没有回退旧版或另开部署轮次。

首次 audit：fl01/fl02/fl06/fl07/fl09 实际 iw 信道不为149，其余五台通过。后续 diagnostic 仅增加断言前原始输出，不改正式 helper/state、不替代发布验收：fl01/fl02已回到149，fl06/fl07/fl09仍处于寻频。fl06日志有“十秒内未确认有效心跳，退回寻频自愈，累计缺失5次ACK”。服务器实际固定149 HT40+；API快照中fl07/fl09 OFFLINE，fl06曾READY后再次寻频，不能宣称完整九客户端稳定。未做额外RF事务或手工iw强制调谐，未改运行源码，未发起通信冒烟。未发现需要物理操作的确定证据。后续需父任务判断心跳/链路阻断；本轮已结束。

fl05旧服务指定 `wlxfc221c500a88`，实际唯一接口 `wlxfc221c100157`；preflight精确验证“仅fl05、无native、指定旧接口已消失、新唯一专用接口存在”的换卡状态，随后新接口绑定成功。fl07–fl10原direct服务通过覆盖同名unit的ExecStart升级，无需旧loaded manifest；最终均取得与MainPID匹配的新loaded manifest。

目标严格为fl00/fl01/fl02/fl04/fl05/fl06/fl07/fl08/fl09/fl10；fl03未连接。通信目录与实验计划未修改。

|主机|daemon PID|native PID|当前绑定接口|只读诊断信道|首次audit|
|---|---:|---:|---|---:|---|
|fl00|12081|12197|wlxfc221c100119|149|通过|
|fl01|3912|3934|wlxfc221c300e99|149|失败：实际信道|
|fl02|4369|4388|wlx88e62852aae4|149|失败：实际信道|
|fl04|3652|3668|wlxfc221c300cbb|149|通过|
|fl05|3456|3475|wlxfc221c100157|149|通过|
|fl06|7295|7312|wlxfc221c500bfe|157|失败：实际信道|
|fl07|7549|7569|wlxfc221c300cbc|157|失败：实际信道|
|fl08|8552|8569|wlxfc221c500a88|149|通过|
|fl09|7792|7814|wlxbcec23372588|153|失败：实际信道|
|fl10|6293|6312|wlxfca386b38672|149|通过|

证据：每阶段每目标的原始stdout/stderr保存在 `<phase>-<host>.json`，控制台汇总在 `<phase>-console.log`；postcheck包含systemd状态、iw、完整本轮journal、服务端API快照；diagnostic保留loaded模块路径/摘要、native参数/实时控制响应及iw。诊断脚本同时留存。source.tar保留本地（按既有gitignore排除），archive_sha见state.json。远端源码及逐机备份在 `/srv/wfb-ng-fl/releases/heartbeat-c6659488f649-20261108T064511Z/`。SHA256SUMS记录本目录证据摘要。

发布前验证：两脚本compile通过；AST核验固定目标集合通过；缺少目标的init被拒绝。init确认native源码相对既有基线无差异。
