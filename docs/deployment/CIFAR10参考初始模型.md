# CIFAR-10 ResNet-18 参考初始模型

本入口生成未经训练的参考制品，供Magazine固定四客户端的等量通信预实验使用。它没有实现CIFAR数据准备、本地训练、FedAvg聚合或评估入口，不能通过生成模型宣称正式FL任务已完成。

## 固定结构和状态

格式为`cifar10-resnet18-fp32-v1`。复用torchvision的`ResNet`与`BasicBlock`，残差block数量为`[2,2,2,2]`、stage通道为64/128/256/512。输入为RGB的`N×3×32×32`；初始卷积改为3×3、stride1、padding1、64通道，无bias；移除ImageNet结构的初始最大池化。后续stage正常下采样，经自适应全局平均池化和512→10全连接输出logits。不加载或下载预训练权重。

所有可训练参数和浮点buffer均为CPU FP32。保留20个BatchNorm的running_mean、running_var及int64标量num_batches_tracked；计数必须非负，不删除或转成浮点。制品读取严格校验格式、初始化元数据、完整键集合、形状、精确dtype、CPU位置、稠密布局和有限数值。

初始化使用独立CPU生成器和显式种子：卷积Kaiming normal、fan_out、ReLU；BN的weight为1、bias和running_mean为0、running_var为1、计数为0；全连接Kaiming uniform、a=√5，bias均匀分布于±1/√512。创建、初始化和读取不消耗调用方的CPU随机序列。GPU计算输入仍须按训练入口的显式预处理定义；此处不执行数据预处理或训练。

## 生成和读取

使用已经安装且与当前PyTorch兼容的torchvision。安装元数据中单独声明`cifar10` extra，不在运行时下载或安装依赖。

```bash
python3 -m wfb_ng.fl.cifar10_model /可写目录/initial.pt --seed 7
```

安装包提供`wfb-fl-init-cifar10-resnet18-model`同等入口。命令拒绝覆盖已有文件，输出格式、初始化种子/来源、实际文件字节数、SHA-256、参数数目与各dtype的原始张量统计。

```python
from wfb_ng.fl.cifar10_model import load_model

artifact = load_model('/可写目录/initial.pt')
model = artifact.model
print(artifact.initialization_seed, artifact.sha256)
```

底层复用`model_artifact.py`的严格制品读取和原子发布；该模型上限64MiB，原MNIST定义继续保持自己的格式与1MiB上限。返回独立CPU模型，修改内存不会改写源文件。读取使用`weights_only=True`，不保存或恢复可执行模型对象。

## 字节数与验收边界

传输字节数以实际完整序列化文件的`stat`结果为准，原始张量字节数只作结构统计。记录PyTorch/torchvision版本、初始化种子与实际SHA；不承诺跨版本的序列化字节完全一致，不使用ImageNet checkpoint元数据或44.7MB估算代替。

通信预实验可以下发这个真实初始制品，客户端原样上传，测量等量完整文件交换；没有本地训练，不把上传称为真实训练update。将来正式update新增的算法元数据、精确序列化字节数以及BN整数计数的聚合策略须在训练入口实现时另外冻结。当前结果不能替代正式update载荷、三组无线候选或完整FL生命周期验收。
