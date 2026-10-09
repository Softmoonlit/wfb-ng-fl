# ADR-0015: Client 作业终态前持久化小型节点审计证据

Status: accepted

## 背景

Client Daemon 为每项作业创建独立 RoleService 沙箱，并在作业完成、中止或失败后回收进程组、删除任务目录、重建空闲链路。该机制保证任务隔离，却使 `role_service.log`、`uftpd.log`、算法结果、Runtime 状态和 manifest 随沙箱立即消失。事后通过 SSH 收集 daemon journal 不能重建 RoleService 重定向日志，也不能证明客户端实际校验过哪份模型、生成过哪份 update。

可以在作业运行中通过 SSH 抢先复制文件，但这种方式存在终态竞态，使验收依赖管理平面持续介入，并把证据是否完整交给外部脚本时序。永久保留整个沙箱则会重复保存模型与 update 大文件，持续占用磁盘，并模糊 Runtime 托管文件与审计材料的边界。

因此需要在 Client 任务资源清理边界建立稳定、有限且不影响作业语义的本地证据持久化机制。

## 决策

### 1. 归档边界与位置

Client Daemon 在 RoleService 已形成终态、删除任务沙箱之前，将白名单内的小型审计文件写入独立持久目录：

```text
/var/lib/wfb-ng-fl/evidence/<job_id>/
```

每个 `job_id` 只允许形成一个归档。目标目录已存在时拒绝覆盖；重跑必须使用新的 `job_id`。

### 2. 白名单内容

允许归档以下实际存在的文件：

- RoleService 与 UFTP 文本日志；
- RoleService 基础设施配置和算法配置；
- 算法结果 JSON；
- observation JSONL；
- Runtime 轮次状态、模型 manifest 和 update manifest；
- 描述上述文件的 `evidence_manifest.json`。

禁止归档模型、update 或其他大文件本体。归档只保留其 manifest 中已经计算的大小和 SHA-256，不额外读取并复制 40 MiB 制品。实现必须按明确相对路径白名单选择文件，不递归打包整个沙箱，也不跟随指向白名单外部的符号链接。

### 3. 原子性与完整性

Daemon 先在同一文件系统的唯一临时目录中复制文件，独立计算每个归档文件的大小和 SHA-256，最后生成 `evidence_manifest.json`。manifest 至少记录 schema 版本、`run_id`、`job_id`、`node_id`、运行时提交、lifecycle outcome、每个文件的相对路径、大小和 SHA-256。`run_id` 来自经过作业入口严格校验并随带内任务贯通的字段；运行时提交必须直接读取安装包内不可被运行配置覆盖的 build identity 资源，不能信任作业请求自报提交，也不能读取可能被 `/etc/wifibroadcast.cfg` 或 `local.cfg` 覆盖的合并后 `settings.common.commit`。正式校验器把两者与 Server run envelope、唯一 `job_id`、build identity 文件摘要及已安装 Debian 包摘要交叉核对。

全部内容完成并刷写后，临时目录原子重命名为最终目录。最终目录的出现是本地证据归档完成标志；残留临时目录不构成有效证据。不同终态可能在不同生命周期阶段结束，因此通用归档器只复制白名单内实际存在的文件，并在 manifest 记录 lifecycle outcome；具体正式验收必须按该 outcome 定义必选文件矩阵，不能把本应存在的文件缺失一概视为可选。

SHA-256 只证明归档内部完整性。该机制不提供签名、加密、防恶意本机管理员篡改、不可抵赖或合规 WORM 存储，不得使用“不可篡改归档”描述它。

### 4. 失败语义

证据归档位于部署审计边界，不参与算法、Runtime 或 Transport 的成功判定：

- 已经形成的 Transport 文件提交、Runtime 轮次终态和 Coordinator 结果不得被归档失败追溯改写；
- 归档失败时保留原任务沙箱和明确错误记录，供后续诊断；
- 归档失败不能阻止 RoleService 进程组回收、任务链路释放和空闲链路恢复；
- 正式验收必须独立检查 evidence，缺失、不完整或摘要错误时以“证据不足”判定整次验收失败，即使 Coordinator 已报告成功。

### 5. 收集与保留

本地 evidence 由管理平面在作业终态后收集。作业成功路径不得依赖 SSH 抢救即将删除的沙箱文件。Daemon 不自动执行过期删除；容量管理与显式清理由部署或验收工具负责，且只能删除已确认不再使用的终态归档。

## 考虑过的方案

### 仅依赖 journald 和 Server 协调摘要

否决。它们不能覆盖被重定向到 Client 沙箱文件的 RoleService/UFTP 日志，也不能提供客户端 Runtime manifest 的本地事实。

### 作业期间通过 SSH 持续复制

否决。它引入终态竞态和管理平面运行时依赖，复制结果还可能是正在写入的半成品。

### 永久保留完整任务沙箱

否决。它会重复保存大文件、无限增长磁盘占用，并把运行工作区误作长期证据格式。

### 归档失败直接阻止节点恢复空闲

否决。审计存储故障不应制造无线链路与节点可用性故障。正式验收通过独立 validator 严格失败即可。

## 后果

- 作业结束后可以稳定取得 Client 侧关键证据，不再依赖运行中 SSH 时序；
- 大文件本体不重复保存，证据容量由小型日志和元数据决定；
- Runtime/Transport 业务结果与验收证据充分性保持清晰分离；
- 实现需要处理原子目录提交、白名单、防符号链接越界、同名冲突和失败沙箱保留；
- 部署与验收工具必须提供显式 evidence 清理，并把 evidence manifest 纳入 run envelope 的交叉校验。
