## Core Principles

1. Do not preserve backward compatibility Remove obsolete paths instead of adding compatibility layers, fallbacks, or migrations.
2. Choose the simplest implementation that fully meets the current requirements Avoid speculative abstractions, configuration, and indirection.
3. Grow the system in layers Start from the smallest version that works end to end, and add each new capability on top of a product that already works. Never trade a working product for unfinished complexity.
4. Keep components modular and concerns clearly separated
5. Prefer established, well-maintained libraries when they reduce overall complexity or improve reliability Do not reimplement common functionality without a clear reason.
6. Lean on the dependencies already in the project before writing your own implementation or adding packages Do not assume a library lacks a capability without checking its documentation and types.
7. Make architectural decisions for the long term Do not accept a stopgap that only works for now and is meant to be replaced later.

## Agent skills

### Issue tracker

问题和 PRD 使用本地 markdown 文件管理，存放在 `.scratch/<feature>/` 目录下。详见 `docs/agents/issue-tracker.md`。

### Triage labels

使用五个标准分流标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

单一上下文布局，`CONTEXT.md` 和 `docs/adr/` 在仓库根目录。详见 `docs/agents/domain.md`。
