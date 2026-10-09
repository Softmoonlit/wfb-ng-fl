# 01: Client 作业终态前持久化节点本地 evidence

Type: task
Status: resolved
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

### 实现与验证

- 前置工单 03 已完成；从 `71ae7d50936786df1e8d504613e4f9bb25eb6963` 开始实施。
- 新增 `wfb_ng.fl.evidence`，复用 artifacts 原子复制与摘要能力。根文件与 canonical UUID 轮次路径使用明确白名单，拒绝非普通文件、符号链接、超出 16 MiB 文件和同名归档；Linux `renameat2(RENAME_NOREPLACE)` 在 manifest 与目录刷写完成后原子发布。
- 完成、启动失败、中止与异常退出共用归档路径；归档失败保留沙箱和错误记录，任务资源正常回收后恢复待命。TUN 自身清理失败则 fail closed 进入 STOPPED；沙箱删除失败记录 journal 和保留路径。
- 安装包独立携带 build identity，要求完整 commit；归档原资源及其摘要，不读取合并配置。修复 setuptools 增量构建相同大小/秒级时间戳导致 identity 未更新的情况。
- ADR-0015 明确四种 outcome、Stage 3 正常成功/失败的必选文件矩阵；更新 Stage 2 事后取证与显式清理指南。工单 04 的 validator 仍按该矩阵后续实现。
- 单元测试覆盖真实 ClientRuntime 两轮归档、排除大文件及未知文件、非法文件类型、目录冲突（包括并发空目录）、摘要/落盘失败、真实启动退出码、TUN/删除失败及包构建身份。
- 最终全量 `python3 -m pytest -q`：201 passed；control/client daemon/server daemon/runtime/evidence 的 Mypy 通过，`git diff --check` 通过。
- 双轴审查发现的资源清理、退出码、沙箱删除及构建缓存问题已修复并复验。本工单未执行实体射频验收或完整 Debian 打包；包资源验证使用真实 wheel/sdist 构建。

## Answer

Client 终态小型 evidence 持久归档已完成；运行身份来自包内 build identity，成功归档后删除沙箱，证据失败保留现场而不阻塞正常资源回收，硬件资源清理失败停止接单。后续 Stage 3 执行器可在终态后直接收集 evidence，并按 ADR-0015 的矩阵严格裁决证据充分性。
