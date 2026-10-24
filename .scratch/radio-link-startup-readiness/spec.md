# 射频重配底座启动就绪屏障

Status: resolved

## Problem Statement

固定 12 dBm、157 信道的 MCS-only 重配在 Server 重建 TUN 后立即报 E101 并回滚全部节点。现有守护等待仅检查接口名称存在，早于地址/路由建立和无线收发器初始化完成。

## Solution

守护进程等待当前链路子进程明确通知本地初始化完成，随后才继续带内控制事务；接口存在不再作为底座就绪依据。

## User Stories

1. 作为 Web 操作者，我希望 MCS 变更在真实底座就绪后才继续上线屏障，避免启动竞态造成回滚。
2. 作为维护者，我希望启动失败、超时和就绪通知异常明确失败并释放资源。
3. 作为集群操作者，我希望 Server 和 Client 在初次启动、射频重建、回退恢复时采用同一就绪契约。

## Implementation Decisions

- LinuxNetworkAdapter 使用 IPRoute 检查接口 IFF_UP、配置 IPv4 地址和前缀，以及控制目的地址实际路由的出口 ifindex。
- Server 检查广播目的地址，Client 检查服务端控制地址；接口存在不再作为启动成功依据。
- 使用现有 3 秒启动预算和条件轮询，不增加固定等待，不延长事务 timeout。
- 没有设备、地址或路由尚未就绪时继续等待；预算耗尽或子进程退出失败并清理资源。
- 保持控制 socket 绑定、GRANT 会话契约、射频参数和最终确认屏障语义。

## Testing Decisions

- 在现有守护与射频事务测试接缝制造“接口已存在、子进程尚未完成地址配置”，断言不提前发送新配置探针。
- 地址/路由就绪后启动继续；启动超时或子进程退出失败并清理资源。
- 测试 IPRoute 地址、UP 和路由出口判定，读取现场已恢复基线验证实际查询。
- 执行相关 Server/Client 射频应用测试及 C++ 必需检查。

## Out of Scope

- 延长 timeout、固定 sleep、自动实机重试、现场部署。
- 本次 E101 修复不声称已解释另一会话中 Client2 裸 READY 丢失的最终空口原因。

## Further Notes

证据归档：tests/logs/stage4-radio-20261010T041811Z-e7b420f3/control-plane/。用户要求先报告精确根因再改，已报告接口存在与地址/收发就绪间的竞态。


## Validation

- 修改前两条红测试：Server 复现 NEW_CHANNEL_PING E101 后 rolled_back，Client 未执行真实就绪检查即返回。
- 修改后 Server/Client 守护、射频应用、事务和控制路由回归 101 条通过。
- IPRoute 判定与隔离真实内核 TUN 生命周期测试 18 条通过。真实实验保留同一个绑定 socket，删除/重建 TUN：UP 但地址/路由缺失时 sendto 报 E101；建立地址/路由后原 socket 可正常广播，无需 rebind。
- 三端基线只读执行新 IPRoute 判定均为 True。
- 没有延长现有 3 秒启动预算或 10 秒 COMMIT timeout，没有新增固定 sleep。现有 50 ms 条件轮询现在以 UP/地址/路由为条件。
- 未部署或进行新的实机射频重试；另一会话的裸 READY 交付问题仍需独立验收。
