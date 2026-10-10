# 中文 LaTeX 排版稿

本目录是《面向真实物理无线链路的联邦学习实验平台设计与性能评估》的中文双栏 LaTeX 工程。以 IEEE 认可的 `IEEEtran` journal 双栏模板为基础，按 IEEE Communications Magazine / ComSoc 风格关闭节与小节编号，并调整图表位置与题注。使用 XeLaTeX 编译，中文采用 TeX Live 随附的 Fandol 字体。当前为中文工作稿；摘要、作者信息、作者简介与参考文献尚未补齐，最终英文稿仍须核对总词数和页数。

## 文件与编辑入口

- `main.tex`：文档设置、文章标题、摘要占位及正文入口。
- `sections/01-introduction.tex`：引言。
- `sections/02-platform-comparison.tex`：无线实验平台的选择与比较，以及 Table 1。
- `sections/03-architecture.tex`：WFB-FL 体系架构与端到端运作机制，以及 Figure 1、2、3。
- `sections/04-experiments.tex`：真实硬件实验与结果评估，以及 Table 2、Figure 4。
- `sections/05-discussion.tex`：讨论与后续研究。
- `figures/figure1.pdf` 至 `figure4.pdf`：四张图的独立副本，工程不依赖外部目录。
- `latexmkrc`：指定 XeLaTeX 的编译配置。
- `main.pdf`：本机编译的阅读稿。

此后直接编辑 LaTeX。上一级的 `IEEE-Communications-Magazine中文初稿.md` 保留为本次转换的来源版本，不维护 Markdown 与 LaTeX 的自动同步。

## 本机编译

本机的用户级 TeX Live 安装在 `~/.local/share/texlive/2026`。在本目录执行：

```bash
export PATH="$HOME/.local/share/texlive/2026/bin/x86_64-linux:$PATH"
latexmk main.tex
```

其他机器若已将 TeX Live 命令加入 `PATH`，直接执行 `latexmk main.tex` 即可。所需宏包为 `ctex`、`fandol`、`ieeetran`、`graphics`、`booktabs`、`tools`、`hyperref`、`tex-gyre`、`times`、`placeins`、`stfloats`，以及 XeLaTeX 和 latexmk。TeX Live 中 `stfloats` 由 `sttools` 包提供。

生成 `main.pdf`。`latexmk` 会按需要重复编译以解析图表引用。清理辅助文件并保留 PDF：

```bash
latexmk -c main.tex
```

## Overleaf

将本目录的源码及 `figures/`、`sections/` 目录一并上传，或导入交付的 `latex-overleaf.zip`。

1. 将主文档设为 `main.tex`。
2. 将编译器设为 **XeLaTeX**。
3. 使用提供 `IEEEtran` 和 `ctex` 的近期 TeX Live 版本，重新编译。

工程使用相对路径和 TeX Live 自带字体，不需要系统字体目录、外部图稿目录、Pandoc、Python 或 shell escape。Overleaf 账户内的实际编译未在本次转换中执行。

## 图稿来源

以下路径均相对于仓库根目录。`prototypes/` 按用户给定路径解析至本稿所属的原型目录。

| 编号 | 用户指定版本中采用的 PDF | 工程内文件 |
| --- | --- | --- |
| Figure 1 | `outputs/autofigure/20261016-214825-svg/figure.pdf` | `figures/figure1.pdf` |
| Figure 2 | `.scratch/fl-testbed-magazine-draft/prototypes/figure2/fig.final.pdf` | `figures/figure2.pdf` |
| Figure 3 | `outputs/autofigure/20261016-202143/figure.pdf` | `figures/figure3.pdf` |
| Figure 4 | `outputs/autofigure/20261016-210648/svg/figure4.pdf` | `figures/figure4.pdf` |

四份图稿均直接复制使用，不改写图内内容。Figure 1、Figure 3 在 `includegraphics` 中裁去上下的纯白外边距，所有图内元素完整保留，工程内 PDF 本身不变；替换为边距不同的新图稿时，应同步检查 `trim` 设置。后续替换工程内的对应 PDF 即可更新图像。

## 转换边界

本次保留五节正文、第四节的三个小节、全部段落和两张表的所有单元格。原稿的六条图表占位标记和原始题注保留在对应 LaTeX 注释中；显示版采用实际图表与精简后的正式题注。Figure 4 的题注保留面板、独立重复次数与 IQR 的含义。正文仅补充必要的图表引用，将句中图引用规范为 `Fig.`；编号和引用仍使用 `label` / `ref` 维护。

两张表及四张图保留跨双栏尺寸，以保证图内文字可读。`stfloats` 支持页顶和页底排版，`placeins` 在章节边界收束待排图表。Figure 2 置于相关正文页底，Figure 3 置于后续页顶，避免图表集中堆叠或延迟到后续章节。维护时应结合首次引用和完整 PDF 检查位置，不以当前 6 页为硬性排版目标。

## ComMag 版式核对

依据 [官方投稿规范](https://www.comsoc.org/publications/magazines/ieee-communications-magazine/policies-guidelines)及其链接的 [ComSoc Style Guide](https://www.comsoc.org/media/801/download)：

- 采用双栏；节与小节标题不编号。
- 官方推荐 Times New Roman 12 pt / 20 pica 栏宽，同时允许使用 IEEE 认可的双栏模板。本工程保留 `IEEEtran` 10 pt 版式，并使用 TeX Live 随附字体支持中文。
- 常规文章图表合计不超过 6 个；本稿为 4 图、2 表。
- 当前交付 PDF 共 6 页。Table 1 在第 2 页，Figure 1、2 在第 3 页且以正文分隔，Figure 3 在第 4 页，Table 2 在第 5 页，Figure 4 在第 6 页。
- 正式投稿的 7 页与 5500 英文单词上限包括题注、参考文献及作者简介等全部内容；参考文献上限为 15 条。当前中文页数不代替最终英文稿的篇幅检查。
- 本机编译日志无缺字、字体替代、未解析引用或溢出警告；对源码包另行解压编译，检查工程独立性。

以上为排版核对，不代表摘要、参考文献、作者资料或实验一手证据已经完成。

摘要仍为原稿中的 `[待定稿后撰写]`。源稿未提供作者、单位或参考文献，本次不补写。本次完成格式转换和编译检查，不改变实验设定、数字、结论或图稿内容，也不代替对实验结果的一手证据核实。
