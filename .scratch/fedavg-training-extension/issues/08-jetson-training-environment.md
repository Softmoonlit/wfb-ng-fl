Status: ready-for-human
Type: task
Labels: wayfinder:task
Parent: ../map.md
Assignee: unassigned
Blocked by: 01

# 核实 Jetson GPU 训练环境与依赖约束

## Question

在已确认的 Jetson Orin Nano 上取得真实系统与 GPU 训练环境信息，明确能支撑参考任务的 Python／PyTorch／CUDA 组合及依赖部署条件，使训练接入设计不必猜测目标环境。

这是为接入决策提供事实的前置任务，不负责实现模型训练、安装或升级整套软件环境，不执行 FedAvg 硬件验收。

## Required findings

- 目标节点的角色、设备型号、CPU 架构、RAM、OS、Jetson Linux／JetPack、Python 版本；区分实际参与节点与仅登记在配置中的节点。
- NVIDIA 驱动、CUDA 与已安装 PyTorch 版本，以及 PyTorch 是否实际识别 GPU。未安装或不可用时如实记录，不自动安装或降级到 CPU。
- 结合实际系统版本与官方兼容资料，明确可采用的 PyTorch GPU 构建及必要附属依赖；不凭空指定最新版，也不恢复旧版本兼容层。若需要新增外部调查，创建精确的 research 票并记录依赖。
- 参考任务的数据预置、可用空间和 GPU 可用内存等环境约束；仅收集支撑接入所需的事实，不扩大成通用硬件基准工程。

## Access boundary

本会话尚无可用的目标 SSH 映射：`cluster_nodes.conf` 登记服务端为 `fl00`、用户为 `fl`，但当前主机的 `ssh -G fl00` 没有对应的有效别名映射。未连接或修改开发板，不能据此推断板端包安装情况。

领取后先检查是否已有可用的受控只读访问方式。若仍不可达，请维护人员提供已配置的管理连接，或在实际节点本机执行下列只读信息采集并返回结果：

1. 设备型号、系统版本、架构、内存和 Python 版本。
2. Jetson Linux／JetPack、CUDA、已安装 PyTorch 版本。
3. 若已安装 PyTorch，核对 `torch.cuda.is_available()`、设备名称及可用显存。

不得为取证擅自更改网络、SSH、系统软件或驱动；不要收集私钥与凭据。依赖安装属于后续实施。

## Completion

将逐节点事实、缺失项、采集方式和依赖约束记入 Answer，链接必要的小型证据，并明确哪些结论已由实际节点证明、哪些仍需补齐。只有足以约束真实训练接入的环境信息已取得时才结单；“没有访问权限”只是一项阻碍，不是成功结果。

## Comments

- 由真实 FedAvg 参考任务决策暴露的环境证据缺口创建，尚未领取。环境任务不阻塞参考任务逻辑结单，但阻塞真实训练接入契约的最终冻结。
