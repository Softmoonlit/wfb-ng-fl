# fl02模型上行长尾导致八端模型闭环超时

Status: ready-for-agent
Type: task

## 范围

当前八客户端物理更换批次：server `fl00`，clients `[fl01,fl02,fl03,fl05,fl06,fl07,fl08,fl09]`。不继承旧九/十端结果，不自动 retry，不主动重启或改变 230ms/10s、FEC、MCS、功率、拓扑。

## 现象

唯一 model run `eight-20261110T132500Z-model` 在 157/HT40/LGI/index18、RS(8,12)、30/5 下完成八端下行和大小/SHA 校验。上行 submit 600 秒硬截止时，fl01/fl03/fl05/fl06/fl07/fl08/fl09 已提交，fl02 仅可见 partial `15,466,496` bytes；server 报 `FLRuntimeError: 轮次等待 update 超时，未达到最小配额: 期望 8`。本次 runner 未进入 server `phase=verify`，七个 `update_sent` 只能称客户端上传完成，不能称 server 独立 SHA 已验证。失败 partial 采样、progress、runner log、manifest、raw inventory 均保留，未自动重试。

## 当前结论边界

smoke `eight-20261110T132200Z-smoke` 已八端完整通过；model 不是最终双闭环验收。model 前后 raw inventory 的 MAC、driver、USB 路径/速率、native SHA 无变化；最终恢复为 149/HT40/LGI/index18，八端 READY/IDLE，active job 为空。当前没有 USB reset、boot change、服务退出或管理面失联的已确认证据；不得把本次长尾归因于 power/USB/温度。

## 窄诊断入口

- 批次证据：`../evidence/eight-client-hardware-20261110T202200Z/model-result.json`
- progress：`../evidence/eight-client-hardware-20261110T202200Z/runs/eight-20261110T132500Z-model/progress.jsonl`
- server update 实际路径/hash 和 fl02 partial：`../evidence/eight-client-hardware-20261110T202200Z/fl02-model-server-update-audit.json`
- fl02 bounded journal/kernel/boot/management 证据：`../evidence/eight-client-hardware-20261110T202200Z/fl02-bounded-diagnosis.json`
- 九板独立最终恢复审计：`../evidence/eight-client-hardware-20261110T202200Z/final-recovery-audit.json`

后续仅限一次有界只读诊断；若需改变参与集合、重启、无线参数或再次发送模型，必须另立授权批次。

## 重启后续验

用户授权fl02整机重启并继续。独立新批`post-fl02-reboot-20261110T142107Z`完成八端1MiB和一次模型闭环；模型78.192秒下行、289.975秒上行，八份服务端尺寸/SHA及恢复审计均通过。旧失败保留，单次成功不能证明根因消除，问题保持待诊断状态；没有自动重试模型或变更参数。见相应证据README。
