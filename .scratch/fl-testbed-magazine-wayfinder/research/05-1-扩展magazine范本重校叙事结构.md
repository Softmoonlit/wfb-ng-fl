# 扩展 magazine 范本并校准叙事结构

## 研究问题与口径

工单 05-1 的三部分任务：

1. 深读工单 01 选出的四篇体裁范本，记录可核验的章节标题、章节内论法、图表组织、贡献声明形态、段落衔接与章节呼应；
2. 在 IEEE Communications Magazine / IEEE Network / IEEE Internet of Things Magazine 三刊内补充数篇 2024–2026 年、主题接近 FL testbed / 无线 FL 部署 / 跨层 FL 系统 / 真实硬件 FL 实验 / 网络与通信实验平台的正式发表文章；
3. 把 1+2 合并为参考集合，对照工单 05 现行结构（6 节 + 6 图表骨架）逐项给出校准方向。

体裁组口径与 research/01 一致：只有 IEEE Xplore `Published in` 字段实际为上述三种 magazine 的文章进体裁组；IEEE Access、IEEE Internet of Things Journal、会议论文一律不进。所有联网检索与抓取通过 exa MCP 工具完成。

**证据等级定义**（全文可得 > Xplore 元数据核验 > 仅摘要可见 > 未核验）。凡无法核验的内容均显式标注。

## 结论先行

- 四篇原有范本全部完成全文级深读（Liu/Shen/Farajzadeh/Victor），章节标题、图表链、贡献声明形态均已核验，见第一节。
- 新增四篇近年 magazine 参考（2024-11 至 2026-03），全部满足 `Published in` 三刊核验，见第二节。其中 **Chu et al.（ComMag 2025-06，标题即 "Testbed Development:"）是本参考集合中唯一一篇"testbed 文章在 ComMag 的完整形态"样本**；**Amadeo et al.（ComMag 2024-11）提供了动机量化图与"轮次截止期"机制论法**。
- 对工单 05 的校准输入共 16 条，按影响排序前三位是：**C-01 第 5 节去数据化设计在体裁内无先例，投稿时必须转为证据节**；**B-02 第 4 节契约论法应从条款罗列改为"一轮穿过契约层"叙事**；**E-01 全文缺 Shen 式中心句的重复设计**。全部校准输入见第三节。
- 三条 map Notes 裁决（投稿证据边界、发射功率行保留、技术对手不画对比表）在校准中全部被尊重，相关条目已标注。

---

## 一、范本深读（工单 01 选出的四篇）

### 1. Liu et al.：Federated Learning on 5G Edge for Industrial Internet of Things（首要范本）

**元数据**（证据等级：全文可得 + Xplore 元数据核验）

- 作者：Xiaoli Liu, Xiang Su, Guillermo del Campo, Jacky Cao, Boyu Fan, Edgar Saavedra, Asunción Santamaría, Juha Röning, Pan Hui, Sasu Tarkoma。
- 刊物：*IEEE Network*, Vol. 39, No. 1, January 2025, pp. 289–297。在线出版 2024-10-03，Open Access（CC-BY）。
- DOI：10.1109/MNET.2024.3469988。
- 全文来源：UPM 机构库作者版全文（oa.upm.es/88633/4/EDGAR_SAAVEDRA_DARRIBA_2.pdf，博士论文附录中的作者版）；Xplore doc 10705021 元数据交叉核验。

**可核验章节标题**

1. `Introduction`
2. `FL on 5G Edge for IIoT: Challenges and Enabling Technologies`（`Challenges` / `Enabling Technologies`）
3. `Edge Intelligence Algorithm: A Case Study of Anomaly Detection`（`FL Architecture for Anomaly Detection` / `Model` / `Experiments and Results Analysis`）
4. `5G Edge Experimentation and Analytics`（`Testbed` / `Results and Analytics`）
5. `Discussion and Open Challenges`

**章节内论法**

- 第 I 节：Industry 4.0 感知/数据分析问题导入，贡献明确写成 twofold——"First, we propose an FL-based 5G edge architecture for IIoT ... develop FL with an LSTM autoencoder algorithm ... Second, we further demonstrate the feasibility of deploying FL with AI algorithms on a real-world 5G Test Network (5GTN) and conduct comprehensive scalability analytics ..."。每项贡献都是"做了 X + 用 Y 验证"的完整句。
- 第 II 节：Challenges 压缩为三条 bullet（无处不在的高速可靠连接 / 大数据流处理 / 分布式时延敏感且隐私保护的训推）；Enabling Technologies 用 **Table I 对七种 IoT 网络技术做实测对比**（latency/stability/energy），据此选出 6LoWPAN + 5G + FL on edge 三个使能件，Fig. 1 给出领域级概念架构。选择由实测数据背书，不是断言。
- 第 III 节：异常检测作为贯穿案例。A 小节给 FL 架构（Fig. 2，C/E/B 三方角色）；B 小节给模型（LSTM autoencoder、MAE、Adam、70-30 划分、阈值机制）；C 小节给模型质量实验（Fig. 3a-f，precision 0.954 / recall 0.983 / F1 0.968 / accuracy 0.950）。
- 第 IV 节：A 小节 Testbed——CeDInt 楼宇 IoT 网络 + Oulu 5GTN（Fig. 4a-b），边缘服务器配置（i7-8700/32GB/RTX2080Ti）与云侧 EC2 对照逐条列出；**"大量设备"由 PC 并行线程转发真实采集数据来扩展，该边界在正文明示**。B 小节 Results——按三类研究问题组织：数据传输（latency/throughput/error rate/jitter）、可扩展性（10→50 传感器）、计算资源；Fig. 5a-j 十个子图聚合报告，edge 10.5±2.0ms vs cloud 17.6±0.8ms，云多用 12.7% 内存，edge 多执行 9.62% 完整时钟周期。
- 第 V 节：开篇几乎逐字重述 twofold 贡献，随后给三个开放挑战（连续数据流处理、能耗、FL 攻击：gradient leakage / model poisoning），挑战是方向而非展开。

**图表组织与出场顺序**

| 顺序 | 图/表 | 论证角色 |
|---|---|---|
| 1 | Fig. 1（第 II 节） | 领域级概念架构：设备—网关—边缘—云 |
| 2 | Table I（第 II 节） | 载体选择依据：七种 IoT 网络技术实测对比 |
| 3 | Fig. 2（第 III 节） | 收窄到一轮联邦异常检测的数据/模型流 |
| 4 | Fig. 3（第 III 节） | 模型可用：结构/阈值/样例/四项指标 |
| 5 | Fig. 4（第 IV 节） | testbed 真实性：楼宇传感器平面图 + 5GTN/MEC 实物 |
| 6 | Fig. 5（第 IV 节） | 系统可扩展：通信与计算指标十子图聚合 |

证据链顺序：**为何这样设计 → FL 如何工作 → 模型可用 → testbed 真实 → 系统可扩展**。共 5 图 1 表，编号严格按出场顺序递增，且每张图/表都在正文先被引用后出场。

**贡献声明形态**：twofold 编号列表，引言声明、第 V 节重述，首尾呼应。

**段落衔接与呼应**：节间因果链为"挑战分类 → 使能技术选择 → 案例研究 → testbed 验证 → 讨论"；无 roadmap 段（"The rest of the paper is organized as follows" 式）；twofold 贡献在首尾两处近似逐字重复。

### 2. Shen et al.：Resource Rationing for Wireless Federated Learning（主范本）

**元数据**（证据等级：全文可得 + Xplore 元数据核验）

- 作者：Cong Shen, Jie Xu, Sihui Zheng, Xiang Chen。
- 刊物：*IEEE Communications Magazine*, Vol. 59, No. 5, May 2021, pp. 82–87。在线出版 2021-06-03。
- DOI：10.1109/MCOM.001.2000744。
- 全文来源：ar5iv 2104.06990；Xplore doc 9446688。

**可核验章节标题**

1. `Introduction`
2. `Resource Rationing for Wireless FL`（`Motivation` / `Impact of Resource` / `"Later-Is-Better"`）
3. `Benefits of Resource Rationing`（`Bandwidth Rationing` / `Clients Rationing` / `Joint Design`）
4. `Challenges and Opportunities`（`Theoretical Foundation` / `Temporal Variation` / `Generalization and Extension` / `Complexity and Scalability` / `Beyond Communication Resources`）
5. `Conclusions`

**章节内论法**

- 全文围绕一个反直觉、可复述的中心命题推进：FL 是跨多轮共同决定结果的新型通信服务，资源应跨轮分配，且后期轮次更需要精确资源（"later-is-better"）。
- 第 I 节指出逐轮静态分配忽视了 FL 的长期多轮过程，提出 resource rationing；第 II-A 给动机，II-B 用 MNIST 量化带宽实验给出量化证据（约 9.4% 带宽即达 99.6% 基线准确率），II-C 用 SGD 梯度方向容差给机制直觉；第 III 节用带宽、客户端参与、联合设计三个实例逐层扩大论点；第 IV 节收束为五个研究方向；第 V 节结论。
- **贡献形态不是系统也不是编号列表，而是"新视角 + 原则 + 三个证据实例 + 研究议程"**。
- 实验全部为数值实验，无 testbed、无真实硬件。

**图表组织与出场顺序**

| 顺序 | 图 | 论证角色 |
|---|---|---|
| 1 | Fig. 1（第 II 节） | **一图双任**：标准无线 FL 四步轮次流程 + resource rationing 概念 |
| 2 | Fig. 2（第 II 节） | 带宽后置优于前置的量化证据（MNIST、固定总预算） |
| 3 | Fig. 3（第 II 节） | 机制直觉：SGD 梯度方向容差 |
| 4 | Fig. 4（第 III 节） | 客户端 rationing：Shakespeare 非 IID，30 次平均含标准差带 |
| 5 | Fig. 5（第 III 节） | 联合设计：三方案能耗对比（约省 50%） |
| 6 | Fig. 6（第 IV 节） | 挑战可视化：功率控制叠加 later-is-better |

每图服务一个推理节点；Fig. 2 的实验在 III-A 被再次引用（图不重复画，论证复用）。

**中心句贯穿设计**：later-is-better 出现在摘要、第 I 节、第 II-C 小节标题、第 III 节各小节、第 V 节——同一句式的可记忆命题至少重复五处。这是本参考集合中最强的"记忆点工程"样本。

### 3. Farajzadeh et al.：Federated Learning in NTN: Design, Architecture, and Challenges（架构型范本）

**元数据**（证据等级：全文可得（arXiv 作者版）+ Xplore 元数据核验；**期刊 DOI 未核验**——Xplore 可访问元数据未展示 DOI，沿用 research/01 结论，以 Xplore doc 11018294 为正式链接，不以 arXiv DOI 冒充）

- 作者：Amin Farajzadeh, Animesh Yadav, Halim Yanikomeroglu。
- 刊物：*IEEE Communications Magazine*, Vol. 63, No. 6, June 2025, pp. 26–33。在线出版 2025-05-29。
- 全文来源：arXiv 2503.07272v1。

**可核验章节标题**

1. `What Drives NTN for 6G and Beyond?`
2. `Compatibility of FL and NTN`（FL for NTN / NTN for FL 双向适配，Fig. 1）
3. `NTN Architecture: An Overview`（`Satellite Tier` / `Aerial Tier` / `Terrestrial Tier` / `Why is HAPS Constellation Ideal for Distributed FL Systems?`，TABLE I）
4. `State-of-the-Art FL in NTN`（A-E 小节按网络层组合分类文献）
5. `Distributed Hierarchical FL`（A-I 九小节：Client Selection / Resource Allocation / Broadcast & Local Training / Intermediate Aggregation / HAPS Constellation Exchange / Global Model Generation / Leveraging FL Data for NTN Management / Communication Model / Computation Model，Fig. 2 流程图）
6. `Simulation: A Case Study`（TABLE II 四场景参数；Fig. 3a-c accuracy/loss/latency）
7. `Open Challenges and Future Directions`（6 段）
8. `Conclusion`

**章节内论法**：驱动在前（为什么 NTN）→ 双向适配逻辑 → 分层架构 + 角色表压缩 → 按维度分类的叙事文献地图 → 九小节框架流程 → 数值案例 → 开放挑战。**贡献声明在摘要内以 (i)-(iv) 四条收益列表形态出现**，正文不设独立贡献列表。实验为仿真研究，无实测。

**图表组织**：Fig. 1 三层物理/逻辑架构 → TABLE I 各层 FL 角色/链路/功能（**角色表压缩大量文字**）→ Fig. 2 训练流程 → TABLE II 仿真参数 → Fig. 3 结果。架构型写法的两步压缩（架构 + 角色表）是其核心手艺。

**文献地图论法**：第 IV 节按"网络层组合"维度把 state-of-the-art 分类叙述，每篇文献一句话定位，**不画对比表**。这是"系统定位不用表"的体裁内先例。

### 4. Victor et al.：Federated Learning for IoUT（次级范本）

**元数据**（证据等级：全文可得 + Xplore 元数据核验）

- 作者：Nancy Victor, Rajeswari Chengoden, Mamoun Alazab, Sweta Bhattacharya, Sindri Magnusson, Praveen Kumar Reddy Maddikunta, Kadiyala Ramana, Thippa Reddy Gadekallu。
- 刊物：*IEEE Internet of Things Magazine*, Vol. 5, No. 4, December 2022, pp. 36–41。在线出版 2023-01-09。
- DOI：10.1109/IOTM.001.2200067。
- 全文来源：ar5iv 2207.13976；Xplore doc 10012478。

**可核验章节标题**：`Introduction`（含 Table I 文献对比表：Ref/Contributions/Limitations，以及 "The rest of the paper is organized as follows" 路线段）；`Background`（IoUT + AI in Sensing/Transmission + FL + Motivation，Fig. 2）；`Applications`（五个应用域，Fig. 3 垂直域图）；`Challenges, Open Issues and Future Directions`（六挑战段 + TABLE II 挑战-解决方案对照表）；`Conclusion`。

**使用范围**：纯综述，无 testbed、无实验。只借鉴场景导入（Fig. 1 网络架构 + 三大挑战 + FL 角色段的开场节奏）、文献对比表与挑战-解决方案对照表的组织方式、挑战收束。注意其 Table I 文献对比表与工单 05"不画对比表"的裁决相反，只作记录不作借鉴。

---

## 二、新增近年 magazine 参考（2024–2026，`Published in` 核验）

背景：IEEE ComSoC 为 *IEEE Internet of Things Magazine* 发布的 FL-IIoT 专题 CFP（2024 年第三季度出版）明确鼓励 "real-life experiments, and testbeds on FL-IIoT networks and applications"（来源：comsoc.org CFP 页）——说明三刊对 FL testbed 类文章有明确需求。

### 5. Amadeo et al.：Mitigating the Communication Straggler Effect in Federated Learning via Named Data Networking

**元数据**（证据等级：全文可得，作者 accepted 版 PDF）

- 作者：Marica Amadeo, Claudia Campolo, Giuseppe Ruggeri, Gurtaj Singh（Univ. degli Studi Mediterranea di Reggio Calabria + CNIT），Antonella Molinaro（同校 + CNIT + Univ. Paris-Saclay）。
- 刊物：*IEEE Communications Magazine*, November 2024, pp. 92–98。页眉标注 "ACCEPTED FROM OPEN CALL"。
- DOI：10.1109/MCOM.001.2300419。
- 全文来源：iris.unirc.it 机构库 PDF。

**可核验章节标题**：`Introduction`（两条 bullet 贡献：eNDN-FL 设计含异构客户端与 straggler 处理；ndnSIM + 真实数据集验证）；`Federated Learning`（Basics 三步 / The Straggler Issue 含 **Fig. 1 动机量化图** / Related Work）；`Named Data Networking`（Basics / Stateful Forwarding / Caching）；`Our Proposal`（Main Assumptions 含 Fig. 2 参考场景 + 转发面 / naming convention / FIB Configuration / Global Model Retrieval / Model Updates Retrieval 含 Fig. 3）；`Performance Evaluation`（Fig. 4 accuracy、Fig. 5 轮次时长）；`Conclusion`。

**对本项目的关键借鉴**

- **Fig. 1 动机量化图**：用 TCP 轮次时长 vs 丢包率（3G/4G/WiFi 链路 × Tmax 100–350s 截止期）把"straggler 问题真实且可量化"画成一张图，放在背景节而非引言——05 第 2 节 A 段缺的正是这种量化锚点。
- **机制作为设计对象**：命名约定（FL_app/global/round_i）、FIB 配置、round-limited cache reservation（缓存保留期 = 轮次截止期 Tmax）都是把"协议机制"当设计变量展开，论法顺序是"假设 → 机制 → 一轮中如何运转"。
- **轮次截止期语义**：Tmax 作为轮次级时间契约，与本项目的完成屏障/收齐边界在语义上呼应，可作为第 4 节契约论法的外部参照（引用预算内）。
- 局限：ndnSIM 仿真 + CIFAR-10/ResNet-50（98MB）模型、10 客户端、95% 置信区间 20 次重复，无 testbed；其统计口径（重复次数 + 置信区间标注）可直接效仿。

### 6. Chu et al.：Testbed Development: An Intelligent O-RAN-Based Cell-Free MIMO Network

**元数据**（证据等级：Xplore 元数据经 White Rose 机构库 / York Research Database / ADS 交叉核验；期刊 DOI 经 arXiv 记录 Related DOI 字段与 White Rose 确认为 10.1109/MCOM.001.2400574，Xplore 文档页本身抓取失败未直接核验；全文为 arXiv 2502.08529 作者版）

- 作者：Yi Chu, Mostafa Rahmani, Josh Shackleton, David Grace, Kanapathippillai Cumanan, Hamed Ahmadi, Alister G. Burr（University of York 等）。
- 刊物：*IEEE Communications Magazine*, Vol. 63, No. 6, June 2025, pp. 74–81。在线出版 2025-05-29。与 Farajzadeh 同期。
- DOI：10.1109/MCOM.001.2400574。

**可核验章节标题**（arXiv 作者版）：`Introduction`（`Available Cell-Free Network Testbeds` 含 **Table I 已有实现对比**：Reference × O-RAN Architecture × UE type × Synchronization × MAC Scheduler × Protocol Stack × Modulation/Coding Scheme，末行 "Ours"；`Contributions`，"Our contributions are threefold" + mid-TRL 定位句）；第 II 节 testbed 架构（**小节标题措辞未核验**，内容已核验：CU/DU（srsRAN 24.04 改造）、Near-RT RIC、xApp、RU（USRP X310 ×4，Octoclock 同步，4T8R）、Open5GS 核心、COTS UE 清单——逐组件"是什么 + 改了什么 + 为什么"）；`Implementation Details`（`MAC Scheduler for MU-MIMO Support` Fig. 2 / `Upper PHY for MU-MIMO Support` / `E2 Agent for CF-MIMO Architecture` / `Intelligent Antenna Association xApp`）；`Testbed Demonstration`（`Experiment Configurations` 含 Table III 配置表——**RU output power 15 dBm 作为配置项报告**；`Demonstration Walkthrough`：视频演示 + 实测 DL 127 Mbps / UL 19 Mbps / MU-MIMO 双 UE 各 16 Mbps + 手动拔天线注入异常演示 xApp 反应；`Performance Measurements`：能耗/吞吐/处理时间，30 秒平均）；挑战节（**标题措辞未核验**，内容为编号式具体挑战：移动性切换、密集 UE 复杂度、正交 DMRS 受限、MU-MIMO 处理时间、E2 可配置面扩展）；`Conclusions`。

**对本项目的关键借鉴**

- **体裁合法性样本**：标题即 "Testbed Development:"，证明 ComMag（含 Design and Implementation 取向）正式接受实机平台文章；开源组件 + 服务商用终端 + 视频演示均可作为证据形态。
- **三条贡献**：完整 O-RAN 兼容 5G 协议栈 / 智能内嵌 RIC（xApp）/ MAC 调度器改造——每条对应一个可演示能力。
- **gap 表**：Table I 用已有原型对比找出空白（RIC+E2 无人做过）——注意此写法与工单 05"技术对手不画对比表"裁决相反，**05 维持既有裁决，不效仿其表，只记录该惯例存在**。
- **配置表先于结果**：Table III 把所有配置（含发射功率 15 dBm）先行报告——发射功率作为配置项（而非性能主张）报告，正是 05 发射功率行在补上 txpower 旋钮后应有的呈现方式。
- **演示 + 异常注入**：手动拔天线制造异常、观察系统反应——"故障语义可演示"的证据形态，与本项目故障路径叙事（F2）呼应。
- 局限：射频层是 O-RAN/5G 而非 FL；无 FL 轮次概念。主题贴合度中等，体裁贴合度最高。

### 7. Wu, Huang, Duan：Real-Time Intelligent Healthcare Enabled by Federated Digital Twins With AoI Optimization

**元数据**（证据等级：Xplore 元数据核验（doc 10980319，前一轮抓取确认卷期页）+ 作者/DOI 经 NSF par 作者版 PDF、作者主页、ORCID 交叉核验；**全文未读，仅摘要可见**）

- 作者：Beining Wu, Jun Huang, Qiang Duan。
- 刊物：*IEEE Network*, Vol. 40, No. 2, March 2026, pp. 184–191。在线出版 2025-04-30。
- DOI：10.1109/MNET.2025.3565977。
- 摘要内容：federated digital twin 框架（split learning + AIGC）+ AoI 作为实时性度量 + DRL 优化；四个医学数据集准确率（80–93%）+ AoI 降至平均 0.6s，与 Rainbow DQN / greedy 对比。

**使用范围**：主题贴合度弱（医疗应用导向），其价值是展示 *IEEE Network* 2025–2026 年接收 FL 文章的形态：框架提出 + 优化算法 + 实验验证的三段式，Open Call 渠道。结构细节未核验，不借鉴具体论法。

### 8. da Silva et al.：Distributed Learning Methodologies for Massive Machine Type Communication

**元数据**（证据等级：Xplore 元数据核验 + 作者页核验；**全文未读，仅摘要与引言开头可见**，Open Access）

- 作者：Matheus Valente da Silva, Eslam Eldeeb, Mohammad Shehab, Hirley Alves, Richard Demo Souza（Xplore 作者页核验：Oulu CWC / KAUST / UFSC）。
- 刊物：*IEEE Internet of Things Magazine*, Vol. 8, No. 1, January 2025, pp. 102–108。在线出版 2024-12-24。
- 摘要内容：面向 massive MTC 的分布式学习方法论综述 + 案例：FL 用于密集 MTC 手写数字分类、FL vs Federated Distillation、split/transfer/meta-learning、UAV 群 MARL 智慧农业场景；挑战与未来方向收尾。

**使用范围**：主题贴合度中等（无线 MTC + FL，案例为数字分类级实验），展示 IoT Magazine 2025 年"方法论 + 案例"形态；章节细节未核验，只作刊物近况参照，不借鉴具体论法。

### 检索过但排除的记录（不进体裁组）

| 工作 | 排除原因 |
|---|---|
| Hayek et al. "Beyond Assumptions: Measuring FL over Real 5G Networks" 等（arXiv 2504.04678） | 内容高度相关（真实 5G/Wi-Fi/Ethernet 上测 FL），但未核验到三刊发表记录 |
| Pradhan & Chowdhury OTA-FL（DOI 10.1109/ICMLCN64995.2025.11140494） | ICMLCN 2025 会议论文 |
| Feng et al. DFL testbed demo（arXiv 2505.08033） | 会议 demo |
| X5G | IEEE Transactions on Mobile Computing |
| Colosseum Open RAN Digital Twin | IEEE Communications Standards Magazine，不在三刊之列 |

---

## 三、对工单 05 的校准输入

参考集合 = 第一节四篇范本 + 第二节四篇新增（下引简称 Liu / Shen / Farajzadeh / Victor / Amadeo / Chu / Wu / da Silva）。逐条按「05 现状 → 参考实践 → 校准方向」给出；标注【尊重裁决】处表示该条以 map Notes 三条裁决（投稿证据边界、发射功率行保留、技术对手不画对比表）为硬约束。

### A. 章节切分

**A-01 第 4 节独立方法学节无体裁先例，须以机制叙事承重**
- 05 现状：第 4 节"基于契约的验证方法"独立成节（~450 词），去数据化、瘦身。
- 参考实践：参考集合八篇中没有一篇设独立"验证方法学"节。Liu 把 testbed 与验证并入第 IV 节（Testbed + Results）；Shen/Farajzadeh 无方法节；Amadeo 的机制设计在 `Our Proposal`、验证在 `Performance Evaluation`；Chu 的实现细节与验证并入 Implementation Details + Demonstration。
- 校准方向：第 4 节承载贡献 2，独立可维持，但必须写成"方法学"（不变式 + 验证层级）而非组件清单，论法效仿 **Amadeo `Our Proposal`** 的"假设 → 机制 → 一轮中如何运转"顺序；同时以 F2 承重（见 C-03），避免 450 词无图无数字的空心节。

**A-02 第 6 节合并经验/挑战/结论可行，内部顺序效仿 Liu V**
- 05 现状：第 6 节"经验、开放挑战与结论"合并（~700 词）。
- 参考实践：两种形态并存。Liu 第 V 节合并 Discussion and Open Challenges（先重述 twofold 贡献，再三个开放挑战，无独立结论节）；Shen 第 IV 节 Challenges（5 小节研究议程）与第 V 节 Concluding Remarks 分开；Farajzadeh 第 VII 节（6 段）与第 VIII 节 Conclusion 分开。
- 校准方向：05 合并式跟随 Liu 形态，可维持；写法定型为 Liu V 的三步——先重述贡献（近似逐字呼应引言）→ 开放挑战列表（3 项左右，每项一个方向，不展开困难叙事）→ 1-2 句收尾。

**A-03 第 2 节三段骨架成立，但载体选择需要可引用事实锚点**
- 05 现状：第 2 节 A 抽象缺口 / B 三候选载体对比 / C 系统定位段（~900 词）。
- 参考实践：Liu 第 II 节把挑战与使能技术合并一节，并用 **Table I 七种 IoT 网络技术实测对比**（latency/stability/energy）背书载体选择；Amadeo 用 Fig. 1 量化动机图把问题钉死；Chu 用 Table I 原型对比找 gap 并以一句 mid-TRL 定位收束。
- 校准方向：骨架与 Liu 实践一致（动机与选型同节），维持。需要补强的是：05 的 B 段目前纯设计推理，而 Liu 的选型有实测背书——B 段对每个候选载体的关键事实断言（普通 Wi-Fi 广播速率受限、SDR 5G 成本/复杂度）应各挂至少一个可引用文献锚点（并入引用预算第 2 组），否则成为无支撑断言。

**A-04 第 5 节"实验设计"去数据化在体裁内无先例（最大体裁风险）**
- 05 现状：第 5 节定位为实验设计节，F4/F5 占位，开发期数字不作投稿证据【尊重裁决：投稿证据边界】。
- 参考实践：参考集合八篇的实验节全部有实质数据——Liu Fig. 5（edge 10.5±2.0ms 等）、Shen Fig. 2-6、Farajzadeh Fig. 3 + TABLE II、Amadeo Fig. 4-5（95% CI × 20 次重复）、Chu 演示实测（127/19/16 Mbps + 能耗表）。纯"设计陈述"无数据节零先例。
- 校准方向：蓝图中必须明示——**投稿时第 5 节必须转为证据节**，F3/F4/F5 由正式实验数据回填（与 map Notes"投稿正文结果数据待正式实验跑出后回填"一致）；"实验设计"措辞仅在蓝图期有效。同时直接移植 **Liu 第 IV-B 的组织法**：每个实验开头一句显式研究问题（Liu 用三类：传输指标 / 可扩展性 / 计算资源），05 的能力矩阵冒烟、实验一、实验二各挂一句研究问题。

### B. 章节内论法

**B-01 第 3 节架构渲染效仿"架构图 + 角色表 + 逐组件论证"两步压缩**
- 05 现状：第 3 节渲染五层架构 + 完成屏障/收齐边界 + 能力面旋钮清单（T1 承载），不贴代码。
- 参考实践：Farajzadeh 第 III 节先逐层架构、再用 **TABLE I 固化 tier × 角色/链路/功能**，然后才逐层展开；Chu 第 II 节逐组件描述（CU/DU/RIC/RU/Core/UE），每个组件"是什么 + 改了什么 + 为什么"。
- 校准方向：第 3 节用 F1 + T1 完成两步压缩（架构 + 角色），正文只展开完成屏障与收齐边界两个语义点；旋钮描述效仿 Chu 的组件式写法，每个旋钮"控制什么 + 影响哪层 + 如何被观测"，避免裸参数列表的平台说明书感。

**B-02 第 4 节契约论法从条款罗列改为"一轮穿过契约层"叙事**
- 05 现状：Transport 契约条款清单（UFTP closed group / HTTP PUT / Content-Digest / 100 Continue / 201 Created）+ 分层验证拓扑。
- 参考实践：Amadeo `Our Proposal` 以一轮 FL 模型检索/更新回传为叙事线，命名约定、FIB 配置、round-limited cache reservation（= Tmax 截止期）、NACK 恢复各机制都在轮次流程需要它的时刻出场；Shen 第 II-C 先给 SGD 梯度方向容差的直觉，再给原则。
- 校准方向：第 4 节不罗列契约条款，而以"一次 FL 轮次穿过契约层"为叙事线，让每个契约不变式在轮次遇到对应失败模式（乱序 / 损坏 / 超时 / 缺员聚合）的时刻出场；Amadeo 的 Tmax 轮次截止期语义可作外部参照（呼应完成屏障）。分层验证拓扑（契约层 → netns → 真实空口）以"每层能证明什么、不能证明什么"为论点，效仿 Liu 第 IV-A 明示验证边界的先例。

**B-03 真实/模拟边界要写进正文，不只写进图注**
- 05 现状：F3 要求标"真实/模拟边界"，正文论法未定。
- 参考实践：Liu 第 IV-A 正文明示"PC 并行线程转发真实采集数据以模拟大量设备"，把真实 IoT/5GTN 与线程扩展分开陈述；Chu 明示 DQN 先在简单 simulator 预训练再上实系统。
- 校准方向：第 5 节效仿 Liu 第 IV-A：正文（不只 F3 图注）写清哪些节点真实硬件、哪些链路真实空口、哪些部分 netns/emulation 扩展；并与第 4 节验证层级呼应（netns 层验流程、不验物理行为）。

**B-04 系统定位段维持裁决，叙事效仿 Farajzadeh 第 IV 节维度分类**【尊重裁决：技术对手不画对比表】
- 05 现状：第 2 节 C 段 ~120 词，不画对比表，只做生态位置区分。
- 参考实践：Farajzadeh 第 IV 节按"网络层组合"维度把文献分类叙述，每篇一两句定位、不画对比表——这是"不用表的系统定位"的体裁内先例；反向惯例也存在（Victor Table I、Chu Table I 均为对比表），说明不画表是合法但需写得更用力的选择。
- 校准方向：维持 05 裁决。C 段效仿 Farajzadeh 第 IV 节的按维度叙事——选一个维度（"把真实无线空口做成实验变量的程度"），把 FedScale/Flower（trace/仿真无线）、FedComm（协议层、不到空口物理层）、跨国 MQTT testbed（真实但 Internet 链路、不可控无线）各一句定位，而非平列罗列。四个引用保留（与引用预算第 3 组一致）。

### C. 图表角色

**C-01 F1 位置成立，但形态应从概念对比图改为"一图双任"**
- 05 现状：F1 = 钩子/架构对比图（传统可靠单播 FL vs 本项目广播空口，标跨层契约 + 可观测点），放第 3 节开篇。
- 参考实践：首图都不放引言——Liu Fig. 1 在第 II 节（领域架构），Shen Fig. 1 在第 II 节 Motivation 且**一图双任**（标准 FL 四步轮次流程 + rationing 概念同图），Amadeo Fig. 1 在背景节（量化动机）。
- 校准方向：F1 放第 3 节开篇与范本实践一致，维持位置；形态校准——效仿 Shen Fig. 1：先画本项目真实广播空口上的 FL 轮次（系统事实），再在同图上标注主流平台隐藏的管道抽象与契约/观测点（对比），让钩子从系统事实中长出来，而非纯概念对比图。

**C-02 T1 角色成立，冒烟结果列改名以避开投稿证据边界**【尊重裁决：投稿证据边界 + 发射功率行保留】
- 05 现状：T1 = 可编程空口能力矩阵（旋钮 × 语义/取值/影响层/RX 观测 + 冒烟结果列），第 3 节。
- 参考实践：Farajzadeh TABLE I 是角色表压缩的成立先例；Chu Table III 把 **RU output power 15 dBm 作为配置项**报告（发射功率的体裁内呈现先例：配置值，不是性能主张）。
- 校准方向：T1 效仿 Farajzadeh TABLE I 的角色表用法，维持。两处微调：(i) "冒烟结果列"改为"空口可观测性"（该旋钮是否有 RX_ANT 观测可验证），避免引用开发期回归数字之嫌；(ii) 发射功率行保留，待 txpower 旋钮实现补齐后，效仿 Chu Table III 以配置值形态报告。

**C-03 F2 放第 4 节无先例，必须双层语义承重（或换节）**
- 05 现状：F2 = 完整轮次时序图（含故障路径 + 分层验证标注），第 4 节唯一图。
- 参考实践：轮次/流程图都放在系统/框架节——Liu Fig. 2（一轮流程）在第 III 节案例节，Farajzadeh Fig. 2（框架流程）在第 V 节框架节。时序图放"验证方法学"节在参考集合中无先例。
- 校准方向：F2 是第 4 节唯一支撑（该节无数字），必须同时承载两层语义——轮次流程（系统事实）+ 验证层级标注（方法学），否则第 4 节空心。备选是把 F2 移入第 3 节作轮次语义事实、第 4 节只引用 F2 讲验证层级——代价是第 3 节承载 F1+T1+F2 偏重。两条路径都列出，取舍留给后续工单。

**C-04 F3/F4/F5 位置成立，补两条写作纪律**
- 05 现状：F3 实物/拓扑图（第 5 节开篇）、F4 链路层结果占位、F5 端到端结果占位（round latency + accuracy vs wall-clock 双子图）。
- 参考实践：Liu Fig. 4（testbed 实物/环境）在第 IV-A Testbed 小节——与 05 一致；Liu Fig. 5 十子图聚合且带 ±2.0ms 方差标注；Amadeo Fig. 4-5 标注 "95% confidence interval, 20 runs"；Chu 用演示 + 测量表（30 秒平均）。
- 校准方向：位置维持。两条纪律写入蓝图：(i) F4/F5 的图注与正文必须带统计重复口径（中位数/IQR 或置信区间），直接效仿 Liu 的 ± 标注与 Amadeo 的"20 runs / 95% CI"写法——与工单 03 重复性口径（5-10 次重复、中位数/IQR）对接；(ii) 每张结果图对应一句研究问题（见 A-04），图不单独报数字。

**C-05 图名额已满：量化动机的两条出路**
- 05 现状：6 图表名额全满（F1/T1/F2/F3/F4/F5）。
- 参考实践：Amadeo Fig. 1（TCP 轮次时长 vs 丢包率 × Tmax）是动机节的高效手段，但 05 已无名额。
- 校准方向：两条出路——(i) 并入 F1 作为子图或标注（推荐，不增名额，与 C-01 的一图双任合并实施）；(ii) 放弃图，第 2 节 A 段正文只引用已发表数字。不建议为动机图挤掉现有图表——F3/F4/F5 都承载证据链不可让位。

### D. 贡献表达与引用

**D-01 三项贡献维持，措辞压缩为 Liu 式"做了 + 验证了"句**
- 05 现状：三项贡献（可编程真实广播空口 / 跨层 testbed 与验证方法 / 代表性实验洞察）。
- 参考实践：贡献项数 2–4 均常见——Liu twofold、Amadeo 两条 bullet、Chu threefold、Farajzadeh 摘要 (i)-(iv)；Shen 不用编号列表（新视角 + 原则 + 实例 + 议程）。
- 校准方向：三项在常见区间内，且与工单 03 贡献层级一致，维持；措辞效仿 Liu——每项必须同时含"做了什么 + 用什么验证"。第三项（实验洞察）目前无证据（工单 04 审计结论），蓝图阶段标注"依赖正式实验回填"，投稿时按 Liu 第二项的 "demonstrate the feasibility ... and conduct comprehensive scalability analytics" 句式落笔。

**D-02 贡献回声：第 6 节近似逐字重述**
- 05 现状：贡献 × 章节对账表存在，但回声措辞无设计。
- 参考实践：Liu 第 V 节开篇几乎逐字重述引言的 twofold；Shen 中心句在摘要/第 I/II-C/III/V 反复出现。
- 校准方向：第 6 节开头以与引言近似逐字的句式重述三项贡献，再转入开放挑战（与 A-02 合并实施）。

**D-03 引用预算两处微调**
- 05 现状：15 篇粗分配 3+3+4+3+2（FL 基础 / 载体缺口 / 系统定位 / testbed 方法 / 体裁范本+约定）。
- 参考实践：参考集合中引用集中投放的惯例——Amadeo 把相关文献集中在背景节的 Related Work 小节；Chu 的原型引用集中在 Table I 及前后；Liu 的使能技术引用集中在第 II-B。
- 校准方向：(i) 第 5 组"体裁范本（Liu/Shen/Farajzadeh）+ ComMag 约定"实际必要性低——体裁范本是结构参照不是技术参照，通常不引用；若不引用，省出 2 个名额补给第 2 组（第 2 节 B 段载体选择事实锚点，见 A-03）。此项是研究发现，是否采纳留给后续工单。(ii) 系统定位四引用（FedScale/Flower/FedComm/MQTT testbed）集中在第 2 节 C 段投放，效仿 Chu/Farajzadeh 引用集中惯例。

### E. 过渡与呼应

**E-01 中心句重复设计缺失（最低成本、最高收益的校准）**
- 05 现状：有中心论点候选（"真实无线空口在主流 FL 平台里被抽象成不可见、不可控的可靠管道；我们把它解放出来……"）与记忆点候选，但无重复出现设计。
- 参考实践：Shen 的 later-is-better 出现在摘要、第 I 节、第 II-C 小节标题、第 III 节各小节、第 V 节，至少五处；Farajzadeh 的 "HAPS 星座作分布式 FL 服务器" 同样贯穿。
- 校准方向：把 05 候选中心论点压缩成一个可重复单句（如"把真实无线空口从 FL 抽象层下面拿出来，做成可控制、可测量、可重复的实验载体"），并固定至少四个出现位：摘要、引言贡献句、第 3 节开篇、第 6 节重述。

**E-02 节间因果链与过渡句**
- 05 现状：节序已定，过渡句设计未记录。
- 参考实践：Liu 的节间链"挑战分类 → 使能技术选择 → 案例研究 → testbed 验证 → 讨论"，每节开头 1-2 句重述前节结论并引出本节问题；Victor 用 roadmap 段（"The rest of the paper is organized as follows"），参考集合中仅此一例，Liu/Shen/Farajzadeh 均不用。
- 校准方向：按 Liu 链式设计 05 的过渡句——第 1 节收在"研究者缺一个可控可测可重复的载体" → 第 2 节证明缺口真实并完成载体选择 → 第 3 节"载体选定后，testbed 如何构成" → 第 4 节"如何验证它工作正确且可复现" → 第 5 节"在这个载体上能回答哪些研究问题" → 第 6 节收束。roadmap 段可选，不用可省词数（05 词数预算紧，倾向不用）。

**E-03 图表编号与出场顺序纪律**
- 05 现状：图表对账表出场顺序 F1→T1→F2→F3→F4→F5。
- 参考实践：所有范本图表编号按出场顺序严格递增（Liu Fig.1→Table 1→Fig.2→…→Fig.5；Amadeo Fig.1→Fig.2→…→Fig.5；Farajzadeh Fig.1→TABLE I→Fig.2→TABLE II→Fig.3），且每张图/表都在正文先引用后出场。
- 校准方向：05 现有顺序满足递增，维持；写作期须逐张核对"正文引用先于出场"。

### 校准输入按影响排序（供工单 Answer 直接使用）

1. **A-04 / C-04**：第 5 节去数据化设计无体裁先例，投稿时必须转为证据节，F4/F5 回填 + Liu 式研究问题组织 + 统计口径图注。
2. **B-02 / A-01**：第 4 节无先例的独立方法学节，须以 Amadeo 式"一轮穿过机制"叙事 + F2 双层语义承重，否则空心。
3. **E-01**：补 Shen 式中心句重复设计（至少四处出现位）。
4. **C-01**：F1 从概念对比图改为 Shen 式一图双任（系统事实 + 对比标注）。
5. **A-03 / D-03**：第 2 节 B 段载体选择补可引用事实锚点；引用预算第 5 组可省出名额。
6. **D-01 / D-02 / A-02**：贡献句压缩为 Liu 式"做了 + 验证了"，第 6 节逐字回声 + 三步内部顺序。
7. **B-01 / B-03 / C-02**：第 3 节两步压缩 + 组件式旋钮写法；真实/模拟边界进正文；T1 冒烟列改"空口可观测性"。
8. **B-04 / C-03 / C-05 / E-02 / E-03**：定位段维度化叙事、F2 换节备选、量化动机并入 F1、过渡句链、图表出场纪律。

---

## 四、未核验清单与证据等级

| 项 | 状态 | 证据等级 |
|---|---|---|
| Liu et al. 全部章节/图表/数字 | 已核验 | 全文可得（UPM 作者版）+ Xplore 元数据 |
| Shen et al. 全部章节/图表 | 已核验 | 全文可得（ar5iv）+ Xplore 元数据 |
| Farajzadeh et al. 章节/图表 | 已核验（arXiv 作者版）；**期刊 DOI 未核验**（Xplore 元数据未展示，以 doc 11018294 为正式链接） | 全文可得 + Xplore 元数据（DOI 缺） |
| Victor et al. 章节/图表 | 已核验 | 全文可得（ar5iv）+ Xplore 元数据 |
| Amadeo et al. 章节/图表 | 已核验（iris.unirc.it accepted 版 PDF）；**出版版最终排版/图号未逐一对勘** | 全文可得（作者版） |
| Chu et al. 元数据 | 卷期页经 White Rose/York/ADS 交叉核验；期刊 DOI 经 arXiv Related DOI 与 White Rose 确认；**Xplore 文档页抓取失败未直接核验** | Xplore 元数据（间接核验） |
| Chu et al. 章节 | Introduction / Implementation Details / Testbed Demonstration / Conclusions 及子节已核验；**第 II 节（testbed 架构）与第 V 节（挑战）的小节标题措辞未核验**（内容已核验） | 全文可得（arXiv 作者版） |
| Wu et al.（Network 2026） | 卷期页 Xplore 核验（前一轮）；作者/DOI 经 NSF par PDF、作者主页、ORCID 核验；**全文未读，章节结构未核验** | Xplore 元数据核验 + 仅摘要可见 |
| da Silva et al.（IoT Magazine 2025） | 卷期页 + 作者经 Xplore 元数据/作者页核验；**全文未读，章节结构未核验** | Xplore 元数据核验 + 仅摘要可见 |
| Shen/Farajzadeh 段落衔接的精确措辞 | 本节对衔接手法的概括基于全文通读，未逐句逐字引用 | 全文可得（概括性结论） |

## 来源索引

1. UPM 机构库作者版全文（Liu et al.）：oa.upm.es/88633/4/EDGAR_SAAVEDRA_DARRIBA_2.pdf；Xplore doc 10705021。
2. ar5iv 2104.06990（Shen et al.）；Xplore doc 9446688。
3. arXiv 2503.07272v1（Farajzadeh et al.）；Xplore doc 11018294。
4. ar5iv 2207.13976（Victor et al.）；Xplore doc 10012478。
5. iris.unirc.it 机构库 PDF（Amadeo et al.）；DOI 10.1109/MCOM.001.2300419。
6. arXiv 2502.08529（Chu et al.）；White Rose（eprints.whiterose.ac.uk/id/eprint/222193/）；York Research Database；ADS；DOI 10.1109/MCOM.001.2400574。
7. Xplore doc 10980319（Wu et al.）；NSF par 10612144；beining1008.github.io；ORCID 0000-0001-7832-1937；DOI 10.1109/MNET.2025.3565977。
8. Xplore doc 10815050 及 /authors 页（da Silva et al.）。
9. comsoc.org IoT Magazine FL-IIoT 专题 CFP 页。
