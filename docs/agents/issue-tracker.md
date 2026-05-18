# 问题追踪器：本地 Markdown

本仓库的问题和 PRD 存放在 `.scratch/` 目录下的 Markdown 文件中。

## 约定

- 每个功能一个目录：`.scratch/<feature-slug>/`
- PRD 文件路径：`.scratch/<feature-slug>/PRD.md`
- 实现问题路径：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，编号从 `01` 开始
- 分流状态记录在问题文件顶部附近的 `Status:` 行中，取值见 `triage-labels.md`
- 评论和讨论历史追加在文件底部的 `## Comments` 小节下
- 当问题被标记为 `ready-for-agent` 时，正文至少应包含 `## What to build`、`## Agent brief`、`## Acceptance criteria`、`## Blocked by` 四个小节；`## Out of scope` 按需添加

## 当技能说“发布到问题追踪器”时

在 `.scratch/<feature-slug>/` 下创建新文件；如果目录不存在，则一并创建。

## 当技能说“获取相关工单”时

直接读取对应路径的文件；用户通常会提供文件路径或问题编号。
