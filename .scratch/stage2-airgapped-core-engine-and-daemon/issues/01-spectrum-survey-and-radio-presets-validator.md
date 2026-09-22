# 01: 5GHz 扫频探路模块、网卡预设引擎与速率区间校验器

**What to build:** 交付纯 Python 独立射频工具库（`wfb_ng.fl.radio`）与独立 CLI。扫描合法 5 GHz UNII-3 信道池 `[149, 153, 157, 165]` 输出干净度排行；常量级硬编码永久禁选与屏蔽已知驱动越界崩溃缺陷的 Channel 161；解析 `robust`（MCS 2 HT20）、`standard`（MCS 3 HT40+ 基准）与 `performance`（MCS 5 HT40+）三档纯网卡参数预设；实现 UFTP 下行注入速率安全区间校验与 `--force` 越界门禁拦截。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 扫频探路扫描范围严格限制在合法 5 GHz UNII-3 信道池 `[149, 153, 157, 165]`。
- [ ] 常量级硬编码永久禁选与剔除已知驱动越界崩溃缺陷的 Channel 161，扫频与配置解析中绝对不出现该信道。
- [ ] 扫频结果按外来 802.11 帧密度输出干净程度排行，并在全频段拥堵时给出显著警告并联动建议 `robust` 预设。
- [ ] 实现纯底层网卡参数预设解析器，支持 `robust` (MCS 2 HT20 Long GI 20dBm)、`standard` (MCS 3 HT40+ Short GI 20dBm，系统基准) 与 `performance` (MCS 5 HT40+ Short GI 20dBm)，FEC 固定为 8/14。
- [ ] 实现 UFTP 下行注入速率安全区间校验器（`standard` 默认 15 Mbps，区间 12~18 Mbps；`robust` 6 Mbps，区间 5~8 Mbps；`performance` 28 Mbps，区间 24~35 Mbps）。
- [ ] 手动输入超出安全区间时输出强风险告警；非交互式/自动化脚本必须传入 `--force` 标记，否则严格 fail-closed 拒绝启动。
- [ ] 配齐完整的单元测试与独立 CLI 调用验证。
