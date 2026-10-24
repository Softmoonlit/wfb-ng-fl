Status: wontfix
Type: grilling
Blocked by:

# 定义插件清单、参数 Schema 与全节点就绪门禁

## Question

固定 manifest 目录中的插件身份、版本、实现摘要、平台兼容版本、训练与聚合参数 Schema、本地私有配置就绪状态应如何表达；Server 如何从 Client 能力上报中建立插件目录，并在启动作业前机械验证全部目标节点与 Server 的插件实现一致且可执行？

## Comments

### 范围关闭

用户确认首版直接执行大文件下发与 update 上传，跳过训练和聚合，同时将算法插件机制延期。本票超出当前地图目的地，设为 `wontfix` 并移出依赖链，不作为已解决的路线决策。将来接入真实算法时应另立工作，重新评审插件需求与契约。范围依据见[定义文件仿真作业运行契约](01-algorithm-plugin-runtime-contract.md#comments)。
