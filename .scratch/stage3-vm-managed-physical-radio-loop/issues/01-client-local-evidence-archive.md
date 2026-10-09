# 01: Client 作业终态前持久化节点本地 evidence

Type: task
Status: ready-for-agent
Blocked by: None

## What to build

按 ADR-0015 修改 Client `JobSandbox` 终态路径，在删除 `job_<job_id>` 沙箱前，把白名单内的小型日志、配置、结果、observation、Runtime 状态和 manifest 原子归档到 `/var/lib/wfb-ng-fl/evidence/<job_id>/`。禁止复制模型/update 大文件本体，不允许覆盖已有归档或跟随越界符号链接。

## Acceptance criteria

- 正常完成、中止、子进程异常退出三条终态路径都尝试形成相同 schema 的 evidence。
- 通用归档器只复制白名单内实际存在的文件；按正常完成、启动失败、中止和异常退出定义确定的 lifecycle outcome 与必选文件矩阵。
- Stage 3 正常成功场景必须包含 RoleService/UFTP 日志、两份配置、算法结果、observation JSONL、两轮状态及模型/update manifest；缺失由 validator 判为证据失败。
- `evidence_manifest.json` 记录 `run_id`、`job_id`、`node_id`、运行时提交、lifecycle outcome 及每个相对文件的大小和 SHA-256。
- 构建流程生成由包直接携带、不会经过运行配置合并的 build identity 资源，至少包含目标完整 commit；evidence 读取该资源并记录其文件摘要。
- `run_id` 来自严格校验并随任务贯通的字段；运行时提交直接读取上述 build identity，禁止信任请求自报提交或合并后的 `settings.common.commit`。
- 最终目录只在全部白名单文件和 manifest 完成后原子出现。
- 已有目标目录、摘要失败、落盘失败和非法文件类型均 fail closed，不覆盖原证据。
- 归档失败保留原沙箱和错误记录，但仍回收进程组、释放任务 TUN、恢复 Daemon 待命资格。
- 归档成功后按原生命周期删除任务沙箱。
- 单元测试断言 `model.bin`、`update.bin`、未知文件和越界 symlink 永不进入 evidence。
- 不新增第三方依赖，优先复用 `wfb_ng.fl.artifacts` 的原子文件和 SHA-256 能力。
- 更新 Stage 2 验收指南：实现落地后以持久 evidence 为事后取证来源，删除“终态后只能依赖沙箱抢救”的现行限制说明，并补充显式清理流程。
- 相关 Client Daemon、生命周期、Runtime 测试及 Mypy 通过。

## Comments
