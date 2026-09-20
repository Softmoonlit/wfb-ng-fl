# 问题追踪器：本地 Markdown

本仓库的问题、规格和执行记录使用 `.scratch/` 下的本地 Markdown 文件管理，不调用 GitHub 或 GitLab 的问题追踪 API。

## 目录约定

- 一个工作项使用一个目录：`.scratch/<feature-slug>/`
- 规格使用 `.scratch/<feature-slug>/spec.md`
- 实现任务放在 `.scratch/<feature-slug>/issues/` 下，每个任务一个 Markdown 文件，文件名按依赖顺序从 `01` 编号，例如 `issues/01-<slug>.md`
- 不使用单一合并 tickets 文件
- 任务文件顶部附近使用 `Status:` 记录状态，状态值见 `docs/agents/triage-labels.md`
- 对话和处理记录追加在文件末尾的 `## Comments` 标题下

## 发布和读取

- 技能要求“发布到问题追踪器”时，在对应的 `.scratch/<feature-slug>/` 目录创建或更新 Markdown 文件
- 技能要求“获取工单”时，读取用户提供的路径或编号对应的本地文件
- 不创建 GitHub Issue，不使用 `gh issue` 命令替代本地记录

## Wayfinder 约定

`/wayfinder` 使用以下本地文件结构：

- Map：`.scratch/<effort>/map.md`
- Child ticket：`.scratch/<effort>/issues/NN-<slug>.md`
- Child ticket 使用 `Type:` 记录 `research`、`prototype`、`grilling` 或 `task`
- `Blocked by: NN, NN` 记录前置任务
- 所有前置任务为 `resolved` 后，任务才算解除阻塞
- 领取任务时将 `Status:` 设为 `claimed` 并先保存
- 解决任务时，在 `## Answer` 下追加答案，将 `Status:` 设为 `resolved`，再把结论摘要追加到 `map.md` 的 Decisions-so-far
