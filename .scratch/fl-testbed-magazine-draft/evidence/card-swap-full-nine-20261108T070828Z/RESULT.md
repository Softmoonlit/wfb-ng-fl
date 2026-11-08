# 换卡后完整拓扑复测结果

## 实际交换关系

用户未指定精确两两配对，以六卡MAC实读为准：fl01装原fl06卡、fl02装原fl09卡、fl04装原fl07卡、fl06装原fl04卡、fl07装原fl01卡、fl09装原fl02卡。详细MAC、USB路径与服务备份见本目录SUMMARY.md及原始JSON。六卡集合完整唯一，服务仅更新接口并标准重启。

## 复测事实

- 九客户端版本、native实际SHA、15W均通过，初次射频实读149/HT40/LongGI/索引18。
- 首轮58秒中fl04持续OFFLINE，fl08末尾OFFLINE；不能据此认定原fl07卡损坏。
- 父补测随后出现连续4次全九READY（见parent-final-status.jsonl）。
- 启动full-nine-swap-smoke-20261108-1时，fl00 prepare通过，fl01 prepare因iw mismatch失败。后续实读fl01信道153，当前进程连续报告十秒未确认心跳而寻频（见fl01-final-iw-log.txt）。
- 未调用重配157，未创建传输角色，未发UFTP或HTTP；本次是冒烟准备失败，不是文件传输失败，模型等量预实验未启动。
- 十台独立临时unit回收均ok，radio attempted=false，常驻服务保持。

## 工具与解释纠正

本轮发现并修正旧runner未向ServerRole传UFTP速率的问题，新增真实start_receive mock验证12000/6000构造参数及实际transport速率；共30项本地测试通过。本次bundle为d16bbbed6c3f298c54e2e2e62a5b874bb07a59eb290e1ba6f364a55752bcad69。历史标12/6Mbps的传输实际使用默认15Mbps，其原始成功/失败保留但速率结论撤销。

原fl06卡移至fl01后出现失确认值得关注，但节点、位置和调度状态均影响结果，本次不能宣称硬件根因已确证。完整九客户端验收仍未通过。
