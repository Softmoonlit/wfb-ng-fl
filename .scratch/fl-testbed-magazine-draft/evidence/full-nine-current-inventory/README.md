# 完整九客户端批次：盘点、统一发布与冒烟准入

本批固定fl00 + fl01/fl02/fl04～fl10，fl03排除，正式实验也采用该集合。

## 已执行

- 十台SSH盘点全部成功，15W实际CPU/GPU/EMC限制通过。原始清单见`20261108T064041Z/SUMMARY.md`。
- 修复fl05旧网卡接口绑定，十台统一加载c6659488f64976185ffe02a7c856f7fd8c821ee0，native SHA一致。
- 发布完整记录见`../../../heartbeat-restart/heartbeat-c6659488f649-20261108T064511Z/README.md`。全部服务active，但完整运行态信道audit失败；客户端存在寻频。
- 30秒七次复核见`post-release-status.jsonl`：fl07/fl09始终OFFLINE；fl06间歇OFFLINE；fl01/02/04/05/08/10维持READY。
- 冒烟入口`full-nine-smoke-20261108-2`在prepare以`online target set mismatch`拒绝。未切频、未UFTP/HTTP，不能记作文件传输失败或冒烟通过。十台独立回收均ok，radio attempted=false。
- 第一次入口尝试错误拒绝发布清单内合法build_identity.json；工具修复后28项本地测试通过。该工具错误与无线失败分开记录。

## 当前结论与边界

统一软件和接口修复完成，全员无线入网尚未通过，故本轮一次真实模型等量预实验未启动。没有缩小集合，没有覆盖旧四客户端结果。全部常驻服务保持运行，没有遗留测试unit。未改变心跳门限或发射索引。

需先使fl07/fl09正常入网并确认fl06可参加，再按同一九客户端集合完成1MiB冒烟，之后一次44,770,302字节模型闭环。当前不具备宣布三组配置验收或正式FL开测的证据。
