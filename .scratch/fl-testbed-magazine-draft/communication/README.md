# 四客户端基础通信冒烟

服务端fl00，固定客户端fl01/fl02/fl04/fl05。仅复用已安装c665948的ServerRole/ClientRole和正式UFTP/HTTP传输，无训练、聚合或评估。

## 范围

- 一次1MiB确定性文件下发，全四端SHA-256通过后并发上传相同大小文件，服务端独立检查全部四份。不是ResNet等量选参，不验证正式FL作业生命周期和客户端任务链路接管。
- 常驻daemon、native和TUN保持。启动前核对完整安装身份、native SHA、全部四端READY/IDLE且无活动作业；测试过程中不并发启动其他FL任务。
- 通过现有普通reconfigure事务切到157/40MHz/Long GI/索引18，MCS3/4、RS(8,14)、UFTP15000kbps。HT20专用calibrate不用于此40MHz测试；本工具不拥有Daemon的作业准入租约。
- 使用UFTP1144、HTTP18080和239.80.41.1/239.80.42.1。测试独有组播路由标记proto186，按同一ifindex和路由快照回收。
- 每节点自有systemd临时unit，RuntimeMaxSec225秒、TimeoutStopSec8秒、KillMode=control-group，范围不包含常驻进程。编排210秒，独立回收最多60秒。
- 全角色关闭后恢复原六项无线配置。文件传输、最终服务端验证、清理、射频恢复分别记录，不能因收尾失败抹除传输事实。
- `runner.py`默认仅打印计划；执行必须明确`--execute --reviewed-sha <bundle_sha256>`。bundle包含runner、node_agent和recover三文件；改动后必须重新核验摘要。

## 本地验证

`python3 -m unittest discover -s .scratch/fl-testbed-magazine-draft/communication -p test_mock.py -v`

七项测试覆盖全员准备门槛、并发下发与上传屏障、默认不连接及拒绝未审查执行、完整radio请求、normal/calibration响应判定及接口身份改变时保留路由。

本机systemd-run标准输入/输出探针已通过，不占用生产UDP/TUN。

## 三组筛选的后续前提

仓库当前仅实现MNIST模型。需要先明确CIFAR版ResNet-18的stem、池化、初始化、BN状态及制品格式，再生成真实序列化文件并记录精确字节和SHA。不能把ImageNet模型元数据44.661MB或估算44.7MB当正式载荷。

现有model_artifact要求全部tensor为FP32且默认最大1MiB；标准ResNet包含BN int64计数buffer，当前实现不能直接通过。该问题属于模型入口实现，不能为了通信选参静默改变权重/状态定义。角色独立功率控制也尚待实现，当前不宣称三组候选已选定。
