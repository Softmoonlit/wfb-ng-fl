## 核心原则

1. **不保留向后兼容**：直接清理废弃路径与旧逻辑，严禁添加兼容层、回退适配（fallback）或数据迁移垫片。
2. **选择满足当前需求的最简实现**：拒绝为“未来可能的需求”做推测性抽象、过度配置和间接层，只写满足当前明确需求的最简代码。
3. **立足长远做架构设计**：拒绝“先凑合跑通、以后再改”的临时权宜之计（Stopgap / Hack）。严禁使用 `setTimeout` 规避竞态、全局临时变量等敷衍手段，必须从根本机制上保证架构的长效性。
4. **优先挖掘现有依赖，慎引新包与手写**：在手写实现或安装新包之前，必须先查阅项目已有依赖的功能与类型定义；不要在未查阅前假设现有依赖做不到，更严禁无故自造通用轮子或滥加冗余依赖。

## 遵循规则

需要 root 权限的命令可直接使用 `sudo` 执行，不要仅因当前用户不是 root 就停止。

## Agent skills

### Issue tracker

问题和 PRD 使用 `.scratch/` 下的本地 Markdown 文件管理。详见 `docs/agents/issue-tracker.md`。

### Triage labels

使用五个本地状态：`ready-for-agent`、`ready-for-human`、`wontfix`、`need-for-review`、`resolved`；不启用 `needs-triage` 和 `needs-info`。详见 `docs/agents/triage-labels.md`。

### Domain docs

采用单一上下文布局，使用根目录的 `CONTEXT.md` 和 `docs/adr/`。详见 `docs/agents/domain.md`。
