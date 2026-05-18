# 问题追踪器：GitHub

本仓库的问题和 PRD 使用 GitHub Issues 管理。相关读写操作统一通过 `gh` CLI 执行。

## 约定

- 创建问题：`gh issue create --title "..." --body "..."`
- 读取问题：`gh issue view <编号> --comments`
- 列出问题：使用 `gh issue list`，按需要附加 `--label`、`--state` 等过滤条件
- 评论问题：`gh issue comment <编号> --body "..."`
- 增删标签：`gh issue edit <编号> --add-label "..."` / `--remove-label "..."`
- 关闭问题：`gh issue close <编号> --comment "..."`

在仓库克隆目录内执行时，`gh` 会根据 `git remote -v` 自动推断当前仓库。

## 当技能说“发布到问题追踪器”时

创建一个 GitHub issue。

## 当技能说“获取相关工单”时

运行 `gh issue view <编号> --comments` 读取问题正文、标签和评论历史。
