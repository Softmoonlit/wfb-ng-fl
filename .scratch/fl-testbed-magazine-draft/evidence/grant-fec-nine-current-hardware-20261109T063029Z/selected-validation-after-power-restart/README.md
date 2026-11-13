# 供电重启后独立验收批次

本目录是与旧 `selected-validation` 平级的独立现场批次。旧 trial1 的完整通过事实和旧 trial2 的供电中断失败 partial 只作为背景审阅输入，不拼入本批次的连续通过计数。

本批次固定 `fl00+[1,2,3,5,6,7,8,9,10]`，排除且不接触 `fl04`。通信身份冻结为 commit `1cd6264d5d6be660998ce60f3a349063009ecfa7`、native SHA `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`、bundle SHA `ab8697fe4573ac3c1088dd11022a1fd75a25cf13752d2717de02e6aa2e625e9e`。模型为 `44770302` 字节，SHA `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`。

worker 先执行只读重启后实际审计，确认 `server.json`、进程 argv、native GET_FEC8/12、GET_RADIO、iw、硬件 MAC/USB、driver、15W 限制、临时 unit、路由和九 READY。实际信道不是 149 时，只在十端管理完整可达、没有身份或残留异常的条件下使用已验证的标准服务路径恢复；不改 registry，不伪造 READY，不执行 cold redeploy。当前审计发现 `fl05` 为 `No route to host`，因此本批次在模型前停止，尚未执行任何标准恢复或模型。

两轮模型 run ID 必须独立且唯一，每方向 600 秒、overall 1290 秒、全阶段 4500 秒。每轮必须同时满足 runner 三项原始 flags、九份服务端独立 size/SHA、exact cleanup、十端实际恢复、九 READY；第一轮失败不自动重试，也不以第二轮凑连续通过。每轮前十端存储须至少 512 MiB；仅允许按 storage.py 的具名 manifest、逐文件 re-stat/hash、exact unlink 机制在 fl01/fl02 回收已归档成功重复模型，保留 B2 原件、失败 partial、日志和真实训练目录。

新增注释与本文档使用中文。旧目录、旧 matrix、旧 STOP、根 README、experiment-plan.md、report-capacity.py 和 prod 源码不在本批次修改范围内。
