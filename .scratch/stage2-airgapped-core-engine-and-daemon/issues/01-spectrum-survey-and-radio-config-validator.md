# 01: 5GHz 扫频探路模块、扁平射频参数直配与速率约束生成器

**What to build:** 交付纯 Python 独立射频工具库（`wfb_ng.fl.radio`）与独立 CLI。扫描合法 5 GHz UNII-3 信道池 `[149, 153, 157, 165]` 输出干净度排行；常量级硬编码永久禁选与屏蔽已知驱动越界崩溃缺陷的 Channel 161；解析与校验扁平射频参数模型（`channel` 149/153/157/165，独立发射功率 `radio_txpower_dbm` 10~20 dBm 近距推荐 12 dBm，解耦的 `downlink_mcs` 3~6 默认 3 与 `uplink_mcs` 3~6 推荐 6）；提供基于 `downlink_mcs` 的动态 UFTP 下行注入速率约束区间与默认推荐值生成器（MCS 3: 12~18 Mbps / 15 Mbps; MCS 4: 18~26 / 22; MCS 5: 24~35 / 28; MCS 6: 32~45 / 38），供 UI 与 CLI 交互输入直接施加约束。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 扫频探路扫描范围严格限制在合法 5 GHz UNII-3 信道池 `[149, 153, 157, 165]`。
- [ ] 常量级硬编码永久禁选与剔除已知驱动越界崩溃缺陷的 Channel 161，扫频与配置解析中绝对不出现该信道。
- [ ] 扫频结果按外来 802.11 帧密度输出干净程度排行，并在全频段拥堵时给出显著风险警告。
- [ ] 实现扁平射频参数直配模型校验器，支持独立的发射功率 `radio_txpower_dbm`（10~20 dBm，近距密集台面推荐 12 dBm）、合法信道 `channel`，以及解耦独立的 `downlink_mcs`（3~6）与 `uplink_mcs`（3~6），底层 FEC 固化为 8/14。
- [ ] 实现基于 `downlink_mcs` 的 UFTP 下行注入速率安全区间与推荐默认值生成函数（供 UI / CLI 表单绑定，无需后端 `--force` 门禁）。
- [ ] 配齐完整的单元测试与独立 CLI 调用验证。
