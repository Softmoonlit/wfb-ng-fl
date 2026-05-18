Title: 收敛 CONTEXT.md 为纯术语表
Status: done

## What to build

把当前项目的 `CONTEXT.md` 收敛为只承载领域术语的 glossary 文档。完成后，协作者在阅读 `CONTEXT.md` 时应只看到核心术语、统一叫法和少量高层语境说明，而不会再把它当作架构规范、实现草案、流程说明或决策记录来使用。

## Acceptance criteria

- [ ] `CONTEXT.md` 只保留术语定义、统一命名和少量不含实现细节的上下文说明
- [ ] 当前文档中的架构图、流程、接口、参数、伪代码、状态清单等非 glossary 内容不再留在 `CONTEXT.md`
- [ ] 保留下来的术语能够覆盖当前项目讨论中反复出现的核心领域语言，如 `Token Passing`、`FL Runtime / SDK`、`Transport Backend`、`反向控制窗口`、`动态逻辑水位`

## Blocked by

None - can start immediately
