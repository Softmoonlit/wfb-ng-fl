Status: ready-for-human
Type: grilling
Blocked by:

# 定义平台编排与算法插件运行契约

## Question

在平台拥有作业生命周期、轮次推进、`sync` / `semi_async` / `async` 协同、超时与急停的前提下，首版 Python 算法插件的训练入口、聚合入口、输入输出制品、返回结果、错误码、中止协作和 Runtime 边界应采用什么精确契约；现有 ADR-0010 的完整算法入口所有权与 Stage 2 `FLCoordinator` 硬编码聚合路径应如何无兼容层地统一？
