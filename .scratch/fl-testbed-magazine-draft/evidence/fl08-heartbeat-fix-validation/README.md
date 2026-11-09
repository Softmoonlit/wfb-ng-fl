# fl08心跳修复实机验收证据索引

本目录保留模型前工具错误及修复执行器后的新批次证据，当前远端执行和收尾已完成。

## 子批次

- `continuation-cccac233291c-0model-gate/`：唯一实际模型执行 root。修复 canonical `check-all-ready.py` 依赖和 storage archived-success index 后，full admission 通过，恰好两轮模型闭环均通过。
- base root 原先的 STOP/raw 记录：保留在本目录及其原始文件中。该 STOP 是工具错误：`check-all-ready.py` 缺失；当时 `restart-readonly-audit` 十板全通过、`restart-ready` 九端全部 READY/IDLE/149、`fail_nodes=[]`，模型执行计数为 0。不将其视为无线准入或参数失败，也不把它并入模型成功轮。

## 冻结身份

- commit：`cccac233291c5a79ca7a66bb2ebb210497ab4071`
- release：`/srv/wfb-ng-fl/releases/heartbeat-cccac233291c-20261109T183910Z`
- native SHA：`44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`
- bundle SHA：`728896c99b36cb873962dda74d61de729c223e82e5e06b58ac5b63f87441d958`
- 模型：`44770302` bytes，SHA `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`

## 固定配置

`fl00 + fl01/fl02/fl03/fl05/fl06/fl07/fl08/fl09/fl10`，排除 `fl04`；30/5、FEC 8/12、HT40/LGI、157 测量恢复 149、MCS 3/4、驱动索引 18、12 Mbps。40/5尚未实测；230ms单次心跳回执等待、十秒失活门限在两轮中均维持原值，未做门限优选比较。本结果支持当前组合能够完成约定通信验收，不能证明未测调度或现有心跳门限的优劣。

执行计数和原始批次／续测批次对照见`execution-count-summary.json`与`index.json`。原STOP保留；完整结果入口为continuation的`final-closeout.json`、`trial1/`、`trial2/`、`wrapper-final.json`、`worker-exit.json`及精确空间回收manifest。
