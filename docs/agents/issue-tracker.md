# 问题追踪器：本地 Markdown

本仓库的问题和 PRD 以 markdown 文件形式存放在 `.scratch/` 目录下。

## 约定

- 一个功能一个目录：`.scratch/<feature-slug>/`
- 规格文件：`.scratch/<feature-slug>/spec.md`
- 实现问题：每个工单一个文件，位于 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`，编号从 `01` 开始——不要合并成单个工单文件
- 分流状态记录在工单文件顶部附近的 `Status:` 行（角色字符串见 `triage-labels.md`）
- 评论和历史对话追加到文件底部的 `## Comments` 标题之下

## 当技能说"发布到问题追踪器"时

在 `.scratch/<feature-slug>/` 下创建新文件（必要时先创建目录）。

## 当技能说"获取相关工单"时

读取引用路径对应的文件。用户通常会直接给出路径或工单编号。

## Wayfinding 操作

由 `/wayfinder` 使用。**map** 是一个文件，每个 **child** 工单一个文件。

- **Map**：`.scratch/<effort>/map.md`——Notes / Decisions-so-far / Fog 正文所在
- **Child 工单**：`.scratch/<effort>/issues/NN-<slug>.md`，编号从 `01` 开始，正文包含问题本身。`Type:` 行记录工单类型（`research`/`prototype`/`grilling`/`task`）；`Status:` 行记录 `claimed`/`resolved`
- **阻塞**：顶部附近的 `Blocked by: NN, NN` 行。当它列出的每个文件都处于 `resolved` 状态时，工单解除阻塞
- **边界（frontier）**：扫描 `.scratch/<effort>/issues/` 下开放、未阻塞且未被认领的文件；编号最小者优先
- **认领**：任何工作开始前先设置 `Status: claimed` 并保存
- **解决**：在 `## Answer` 标题下追加答案，设置 `Status: resolved`，然后在 `map.md` 的 Decisions-so-far 中追加上下文指针（gist + 链接）
