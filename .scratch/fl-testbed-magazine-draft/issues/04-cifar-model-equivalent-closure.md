# 04：冻结CIFAR-10 ResNet-18制品并执行四端等量闭环

Status: resolved
Type: task

## 目标

继续固定fl00与四客户端[1,2,4,5]。基于已通过的1MiB冒烟，生成具体CIFAR版ResNet-18初始制品，记录精确序列化字节数与SHA；使用相同真实字节做四端纯通信闭环，不训练、聚合或评估，不重启或重新部署常驻服务。

## 模型与公共接口

- 复用已安装torchvision的ResNet/BasicBlock：[2,2,2,2]，输入3通道32×32，3×3 stride1/padding1 stem、无初始maxpool、十类logits。保留标准残差stage、全局平均池化及BN统计。禁用预训练权重下载。
- 所有浮点权重和buffer为CPU FP32；BN num_batches_tracked为模型结构声明的非负int64 buffer，不参与浮点转换。模型制品完整保存state_dict，不删除BN状态。
- 格式`cifar10-resnet18-fp32-v1`，固定seed和显式初始化来源；复用通用原子写入、weights_only读取、精确结构与数值校验、SHA。模型上限64MiB，原MNIST限额与格式保持。
- 现有公共接缝initialize_model/load_model/write_model_atomic/validate_state_dict、CLI与forward。本轮只实现初始化制品，不宣称CIFAR训练/聚合/评估入口已完成。

## 通信执行

- 四端当前卡、电源、USB和物理位置保持；fl06～fl10不连接。生产模块仍从已核验c665948安装加载；新模型只在fl00本机生成文件，通信过程按不透明字节传输，不导入工作树新模型到守护服务。
- 在已提交代码与通过审查的bundle上执行。完整四端prepare后正常重配157/HT40/Long GI/索引18/MCS3和4/RS(8,14)/15000kbps，UFTP下行真实制品，每台校验后并发HTTP上传同一等量文件，服务端独立校验四份。
- 每项传输I/O硬预算300秒。编排690秒，节点临时unit705秒及8秒停止预算，独立回收60秒，总预算750秒。预算在执行前固定，不因当次低速进展延长。
- 首次闭环失败即保存并回收，不自动调参或重试。首次成功、全员与资源均恢复后，再执行一次相同基线闭环；总共最多两次。这只能证明当前157/HT40基线的本次等量可用性，不代表另外两组已选定。
- 无超时、最终传输失败或hash错误才记通过；传输、服务端验证、cleanup与radio恢复分别记事实。普通重配不持有作业准入租约，本轮不并发其他FL任务。
- 只查询文件、状态和实际radio，不重开心跳关联捕获、不修改超时/寻频规则、不清registry。结束恢复原149配置并逐台读回、核验常驻PID与15W。

## 后续边界

第三组角色独立功率、HT20受控标定以及正式CIFAR训练/聚合入口不混入本票。它们在三组筛选和正式训练各自阶段实施。本票不宣称全九端或完整FL生命周期验收。

- 真实制品已由提交`f15de65`实现并验证；seed7制品格式为`cifar10-resnet18-fp32-v1`，序列化大小44,770,302字节，SHA与manifest见`evidence/model-sized-four-20261106T173222Z/model-manifest.json`。
- 149/HT40/MCS3/Long GI/索引18/12,000kbps在固定四端[1,2,4,5]上连续两次完成完整下行、四份等量HTTP上行、服务端独立hash校验，并通过清理、恢复和15W实际审计；证据见`evidence/model-sized-ht40-149-12-20261107T0220Z/`。
- 157/HT40/MCS3/12,000kbps、157/HT20/MCS3/6,000kbps及149/HT20/MCS3/6,000kbps的真实模型下行均触发fl00同一USB SuperSpeed设备reset并以UFTP退出码9失败；三次均未进入完整HTTP上传闭环，详见`evidence/model-sized-failures-20261107T0304Z/README.md`。
- 每次失败后均标准重启五台服务并恢复149/HT40/15,000kbps和四端READY/IDLE。带宽受限组和正式157均未通过通信验收，不能继续重复长流尝试。
