# 扩展 magazine 范本并校准叙事结构

Type: research
Status: resolved
Blocked by:

## Question

工单 05 已 resolved，但其 6 节叙事结构与图表骨架虽有“对齐 01 工单范本骨架”的声称，实质论法基于设计常识拼接——只引用了范本的可核验章节标题，**并未引入范本文章的内部论法、图表论证角色、贡献表达与段落衔接如何具体应用到本项目**，缺乏真正的范本佐证。本工单补这个缺口，并为重新优化工单 05 提供参考依据。

1. **引入 01 工单选出的范本**：把工单 01 选出的最适合本项目体裁与叙事范本的几篇 magazine 文章作为参考依据完整引入——
   - Liu et al. *Federated Learning on 5G Edge for Industrial Internet of Things*（*IEEE Network*, 2025，首要范本）
   - Shen et al. *Resource Rationing for Wireless Federated Learning*（*IEEE Communications Magazine*, 2021，主范本）
   - Farajzadeh et al. *Federated Learning in NTN*（*IEEE Communications Magazine*, 2025，架构型范本）
   - Victor et al. *FL for IoUT*（*IEEE Internet of Things Magazine*, 2022，次级范本，仅场景导入/挑战收束）

   除可核验章节标题外，记录每篇的：章节内论法（每节具体怎么论证）、图表组织（每张图/表承担什么论证角色、顺序如何完成论证链）、贡献声明形态、段落衔接手法、章节间呼应——使后续可以把本项目章节论法逐项派生自范本，而非凭经验拼。
2. **扩展近几年 magazine**：在目标刊物 *IEEE Communications Magazine* / *IEEE Network* / *IEEE Internet of Things Magazine* 范围内，再找几篇近 2-3 年（约 2024-2026）发表、主题接近"FL testbed / 无线 FL 部署 / 跨层 FL 系统 / 真实硬件 FL 实验 / 网络实验平台"的 magazine 文章，清晰列出（题名、作者、刊物卷期年、DOI/正式 URL、可核验章节标题、图表与实验角色、可借鉴的叙事/图表手法、不可借鉴处）。
   必须严格区分真实 magazine 刊物与 IEEE Xplore "Journals & Magazine" 页面分类——只取 Xplore `Published in` 字段实际为上述三种 magazine 的文章；*IEEE Access*、*IEEE Internet of Things Journal*、会议论文均不进体裁组。
3. **输出校准输入**：把 1 与 2 合并作为参考集合，回答"工单 05 的叙事结构应如何重新校准"——逐项指出 05 现结构中哪些章节切分、章节内论法、图表角色与范本实践不一致，并提出具体校准方向（对应到范本的哪一篇、哪一处实践）。校准输入本身不直接修改工单 05 的 Answer；由后续工单或会话据此决定如何重做 05。

研究优先使用期刊官网、IEEE Xplore 元数据、DOI、作者公开稿等一手来源，并记录无法核验之处。研究材料与研究结论写入 `research/05-1-扩展magazine范本重校叙事结构.md`，本工单 Answer 给出参考集合要点 + 校准输入要点。

## Answer

完整研究材料（全文级深读记录、逐条校准输入、未核验清单与来源索引）见 [`research/05-1-扩展magazine范本重校叙事结构.md`](../research/05-1-扩展magazine范本重校叙事结构.md)。以下为题面要求的两部分要点。

### 参考集合要点

**01 工单四篇范本（全文级深读，补齐章节内论法/图表论证链/贡献形态/衔接手法）**

1. **Liu et al.**（首要范本，*IEEE Network* 39(1) Jan 2025, pp.289–297, DOI 10.1109/MNET.2024.3469988）：twofold 贡献首尾呼应；Table I 用七种 IoT 网络技术**实测对比**背书载体选择；图表链"为何设计→如何工作→模型可用→testbed 真实→系统可扩展"（5 图 1 表）；第 IV-A 正文明示 PC 线程扩展的真实/模拟边界；第 V 节重述贡献 + 3 项开放挑战，每实验开头挂一句研究问题。
2. **Shen et al.**（主范本，*ComMag* 59(5) May 2021, pp.82–87, DOI 10.1109/MCOM.001.2000744）：later-is-better 中心句在摘要/引言/小节标题/各节/结论至少五处重复；Fig.1 一图双任（轮次流程 + rationing 概念同图）；贡献形态 = 新视角 + 原则 + 三证据实例 + 研究议程；纯数值实验。
3. **Farajzadeh et al.**（架构型范本，*ComMag* 63(6) June 2025, pp.26–33, Xplore doc 11018294，期刊 DOI 未核验）：驱动在前 → 双向适配 → 架构图 + TABLE I 角色表两步压缩 → 按维度分类的叙事文献地图（**不画对比表的系统定位先例**）；贡献 = 摘要 (i)–(iv) 收益列表。
4. **Victor et al.**（次级范本，*IoT Magazine* 5(4) Dec 2022, pp.36–41, DOI 10.1109/IOTM.001.2200067）：纯综述，只借鉴场景导入与挑战收束；其文献对比表与 05 裁决相反，只记录不借鉴。

**新增 2024–2026 magazine 文章（全部 `Published in` 核验）**

5. **Amadeo et al.**（*ComMag* Nov 2024, pp.92–98, DOI 10.1109/MCOM.001.2300419）：Fig.1 量化动机图；"假设→机制→一轮中如何运转"论法（各机制在轮次需要它的时刻出场）；Tmax 轮次截止期语义与本项目完成屏障呼应；20 次重复 + 95% CI 统计口径可效仿。
6. **Chu et al.**（*ComMag* 63(6) June 2025, pp.74–81, DOI 10.1109/MCOM.001.2400574）：**标题即 "Testbed Development:"——ComMag 接受实机平台文章的直接证据**；三条贡献 + gap 表 + mid-TRL 定位；配置表先于结果（发射功率 15 dBm 作配置项报告——发射功率的体裁内呈现先例）；演示 + 拔天线异常注入作证据。
7. **Wu et al.**（*IEEE Network* 40(2) March 2026, pp.184–191, DOI 10.1109/MNET.2025.3565977）：FL 医疗数字孪生，主题贴合弱，仅示 Network 刊近况；仅摘要可见。
8. **da Silva et al.**（*IoT Magazine* 8(1) Jan 2025, pp.102–108）：massive MTC 分布式学习方法论 + 案例，主题贴合中等；仅摘要可见。另核验到 ComSoC FL-IIoT 专题 CFP 明确鼓励 "real-life experiments, and testbeds"。

**排除记录**（不进体裁组）：Hayek（未见三刊发表）、Pradhan & Chowdhury（会议）、Feng demo（会议）、X5G（TMC）、Colosseum（CSM 非三刊）。

### 校准输入要点（对工单 05，按影响排序）

1. **A-04/C-04（最大体裁风险）**：第 5 节"实验设计去数据化"在参考集合八篇中零先例——投稿时必须转为证据节，F4/F5 由正式实验回填（与 map Notes 一致）；移植 Liu IV-B"每实验挂一句研究问题"组织法；图注带统计口径（Liu ± 标注 / Amadeo "20 runs, 95% CI"，对接 03 工单重复性口径）。
2. **B-02/A-01**：第 4 节独立方法学节同样无先例，须以 Amadeo 式"一轮穿过契约层"叙事（契约不变式在遇到对应失败模式时出场，不罗列条款）+ F2 双层语义承重，否则 450 词空心。
3. **E-01（低成本高收益）**：全文缺 Shen 式中心句重复设计——把候选中心论点压缩为可重复单句，固定至少四个出现位（摘要/引言贡献句/第 3 节开篇/第 6 节重述）。
4. **C-01**：F1 从纯概念对比图改为 Shen 式一图双任（先画系统事实，再标注被隐藏的管道抽象与观测点）；位置在第 3 节开篇与范本一致，维持。
5. **A-03/D-03**：第 2 节 B 段载体选择目前是纯推理，须为每个候选载体的事实断言补可引用锚点（Liu 用实测表背书选型）；引用预算第 5 组（体裁范本）实际不必引用，可省 2 名额补给载体锚点。
6. **D-01/D-02/A-02**：三项贡献维持（2–4 项均在体裁常见区间），措辞压缩为 Liu 式"做了 + 验证了"句，第三项标注依赖正式实验回填；第 6 节按 Liu V 三步（近似逐字重述贡献→挑战列表→收尾）。
7. **B-01/B-03/C-02**：第 3 节效仿 Farajzadeh 两步压缩（F1+T1）+ Chu 组件式旋钮写法（控制什么 + 影响哪层 + 如何被观测）；真实/模拟边界写进正文不只图注（Liu 先例）；T1 冒烟结果列改名"空口可观测性"以避开投稿证据边界，发射功率行保留、补上 txpower 后按 Chu 配置值形态报告。
8. **B-04/C-03/C-05/E-02/E-03**：系统定位段维持 05 裁决（不画表），效仿 Farajzadeh IV 按"把真实空口做成实验变量的程度"维度叙事；F2 放第 4 节无先例，给出"双层语义承重"与"移入第 3 节"两条备选；量化动机图并入 F1（名额已满）；节间过渡按 Liu 因果链设计、不用 roadmap 段省词数；图表出场顺序已合规，写作期核对"先引用后出场"。

三条 map Notes 裁决（投稿证据边界、发射功率行保留、技术对手不画对比表）在校准中全部被尊重并逐条显式标注。

### 未核验要点

- Farajzadeh 期刊 DOI 未核验（以 Xplore doc 11018294 为正式链接）；Chu 期刊 DOI 经 arXiv Related DOI + White Rose 间接核验，Xplore 文档页抓取失败未直接核验。
- Wu、da Silva 仅摘要可见，章节结构未核验。
- 衔接手法概括基于全文通读，未逐字引用原句。

### 交接说明

按题面约定，本校准输入**不直接修改工单 05 的 Answer**；如何据此重做叙事结构由后续工单（05-2）裁决。

## Comments