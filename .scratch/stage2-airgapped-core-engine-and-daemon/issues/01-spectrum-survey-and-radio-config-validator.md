# 01: 5GHz 扫频探路模块、扁平射频参数直配与速率约束生成器

**What to build:** 交付纯 Python 独立射频工具库（`wfb_ng.fl.radio`）与独立 CLI。扫描合法 5 GHz UNII-3 信道池 `[149, 153, 157, 165]` 输出干净度排行；常量级硬编码永久禁选与屏蔽已知驱动越界崩溃缺陷的 Channel 161；解析与校验扁平射频参数模型（`channel` 149/153/157/165，独立发射功率 `radio_txpower_dbm` 10~20 dBm 近距推荐 12 dBm，解耦的 `downlink_mcs` 3~6 默认 3 与 `uplink_mcs` 3~6 推荐 6）；提供基于 `downlink_mcs` 的动态 UFTP 下行注入速率约束区间与默认推荐值生成器（MCS 3: 12~18 Mbps / 15 Mbps; MCS 4: 18~26 / 22; MCS 5: 24~35 / 28; MCS 6: 32~45 / 38），供 UI 与 CLI 交互输入直接施加约束。

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] 扫频探路扫描范围严格限制在合法 5 GHz UNII-3 信道池 `[149, 153, 157, 165]`。
- [x] 常量级硬编码永久禁选与剔除已知驱动越界崩溃缺陷的 Channel 161，扫频与配置解析中绝对不出现该信道。
- [x] 扫频结果按外来 802.11 帧密度输出干净程度排行，并在全频段拥堵时给出显著风险警告。
- [x] 实现扁平射频参数直配模型校验器，支持独立的发射功率 `radio_txpower_dbm`（10~20 dBm，近距密集台面推荐 12 dBm）、合法信道 `channel`，以及解耦独立的 `downlink_mcs`（3~6）与 `uplink_mcs`（3~6），底层 FEC 固化为 8/14。
- [x] 实现基于 `downlink_mcs` 的 UFTP 下行注入速率安全区间与推荐默认值生成函数（供 UI / CLI 表单绑定，无需后端 `--force` 门禁）。
- [x] 配齐完整的单元测试与独立 CLI 调用验证。

## Comments

- 已交付 `wfb_ng.fl.radio` 模块与独立 CLI（可通过 `python3 -m wfb_ng.fl.radio` 或 `wfb-fl-radio` 控制台脚本调用）。
- 严格遵循 ADR-0014 规范与核心原则：
  - 合法信道池严格限定为 5GHz UNII-3 `[149, 153, 157, 165]`，默认 157；
  - 常量级硬编码 `FORBIDDEN_CHANNELS = (161,)`，在校验与扫频中坚决屏蔽已知 `rtl88xxau_wfb` 驱动越界崩溃缺陷的 Channel 161；
  - 动态下行注入速率安全约束生成器与默认值映射（MCS 3: 12~18 Mbps / 默认 15 Mbps; MCS 4: 18~26 / 22; MCS 5: 24~35 / 28; MCS 6: 32~45 / 38），并在 CLI 交互校验层强制实施速率安全区间拦截；
  - 扁平射频参数模型 `RadioConfig` 校验，底层物理属性（FEC 8/14, HT40+, Short GI）固化且未知参数严格 Fail-closed；
  - 增量重配补丁 `validate_radio_patch` 遵循 ADR-0014 契约，强校验必填基底状态并保证未提及属性绝对不被隐式覆盖；
  - 扫频探路具备精确的外来 802.11 帧（管理帧、控制帧如 ACK/CTS/RTS、数据帧）解析过滤机制，排除本地网卡与集群内 WFB 帧；按外来帧率智能排序输出，全信道拥堵时发出显著告警；扫频前后具备确定性信道状态捕捉与自动恢复机制；
- 编写 47 项单元测试（涵盖速率约束生成器、非法调制拦截、Channel 161 驱动崩溃禁选、功率范围校验、CLI 动态区间校验与外部进程调用、增量 PATCH、802.11 外来帧精准过滤与信道恢复状态机），全量测试 100% 通过。
