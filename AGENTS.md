# Open Code 项目指令

## 遵循规则

需要 root 权限的命令可直接使用 `sudo` 执行，不要仅因当前用户不是 root 就停止。

## Agent skills

### Issue tracker

问题和 PRD 使用 GitHub Issues 管理，相关操作通过 `gh` CLI 执行。详见 `docs/agents/issue-tracker.md`。

### Triage labels

使用五个标准分流标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

单一上下文布局，`CONTEXT.md` 和 `docs/adr/` 在仓库根目录。详见 `docs/agents/domain.md`。
