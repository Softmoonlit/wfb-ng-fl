# Claude Code 项目指令

## 遵循规则

不要引用上级目录wfb-ng里的内容，因为他已弃用。

需要 root 权限的命令可直接使用 `sudo` 执行，不要仅因当前用户不是 root 就停止。

## 常用命令

### 开发工具

- `make` - 编译项目
- `make test` - 运行单元测试
- `make clean` - 清理编译产物
- `make install` - 安装到系统

## Agent skills

### Issue tracker

问题和 PRD 使用 GitHub Issues 管理，相关操作通过 `gh` CLI 执行。详见 `docs/agents/issue-tracker.md`。

### Triage labels

使用五个标准分流标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

单一上下文布局，`CONTEXT.md` 和 `docs/adr/` 在仓库根目录。详见 `docs/agents/domain.md`。
