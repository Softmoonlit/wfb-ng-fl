# 完整九客户端冒烟与单次预实验

Status: ready-for-agent

## 规格

用户最新授权完整拓扑：fl00服务端，fl01、fl02、fl04～fl10共九客户端，fl03排除，后续正式实验也固定该集合。旧四端批次仅保留历史证据。

1. 十台身份、版本、专用驱动与接口、实际radio和15W审计，修复混版和错误接口绑定。
2. 从提交源码统一c665948通信基线，native SHA保持一致；部署后读回149/HT40/Long GI/索引18。
3. 在正式目标157/HT40/MCS3下行与MCS4上行/索引18/RS(8,14)/12Mbps完成一次1MiB UFTP+HTTP冒烟，全部九端必须完成并通过独立SHA和恢复审计。
4. 冒烟通过后，以同样参数完成一次44,770,302字节CIFAR模型等量闭环。仍使用现有300秒单操作硬截止，不运行训练。
5. 任一失败保留事实，停止进入下一阶段；不得自动减少客户端，不把管理可达或READY替代文件闭环。
6. 一次预实验不宣称三组配置或连续两次验收完成。恢复后实际RF、PID、15W及残留unit/路由均须记录。

## Comments

- 换卡后补测：已按六卡实际MAC重绑接口并重启六台。短暂出现全九READY，但冒烟prepare实读fl01信道153且连续失确认，故在切频与文件发送前停止。十台临时unit回收均通过，未启动模型预实验。实际映射及结果见`../evidence/card-swap-full-nine-20261108T070828Z/RESULT.md`。
- 工具纠正：旧runner的ServerRole实际用默认15Mbps，不能沿用历史12/6Mbps速率标记；已显式传参、核验实际transport速率，30项本地测试通过。

- 初盘十台全部SSH可达、15W实际限制通过。fl07～fl10仍为旧Python版本，fl05绑定旧接口且native未运行，已统一修复。
- 本轮十台已统一加载c665948，native摘要一致，Long GI/40MHz/索引18启动和GET审计通过；部分客户端持续寻频，完整信道审计不通过。
- 30秒七次快照：fl07/fl09始终OFFLINE，fl06反复READY/OFFLINE，其余六客户端READY。冒烟入口在prepare以`online target set mismatch`拒绝；未切频、未启动UFTP/HTTP或模型预实验。全十台临时unit均回收。见`../evidence/full-nine-current-inventory/README.md`。
- 首次工具尝试因错误拒绝manifest中的build_identity.json停止，已修复并保留原记录，不计作无线失败。
