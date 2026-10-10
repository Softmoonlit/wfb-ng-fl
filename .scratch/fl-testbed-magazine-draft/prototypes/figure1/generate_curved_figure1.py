#!/usr/bin/env python3
"""生成以图为主、弯曲短箭头、左上角小架构图的全新 Figure 1（精雕细琢版）。"""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SKILL_SCRIPTS = ROOT / '.agents/skills/figgenie-paper-diagram/scripts'
sys.path.insert(0, str(SKILL_SCRIPTS))
from place_text import place, svg_text
from playwright.sync_api import sync_playwright

# 调色板：IEEE 严谨蓝灰工程风
NAVY = '#1a365d'        # 墨蓝，重要标题与序号
BLUE = '#2b6cb0'        # 主冷蓝，主通信流与核心模块
LIGHT_BLUE = '#ebf8ff'  # 浅冰蓝底
BG_BLUE = '#edf3fa'     # 浅蓝灰底
INK = '#2d3748'         # 深炭灰，正文
MUTED = '#4a5568'       # 石板灰
GRAY = '#718096'        # 冷中灰，辅助线
BORDER_GRAY = '#cbd5e0' # 边框灰
LIGHT_GRAY = '#edf2f7'  # 浅灰底
WHITE = '#ffffff'

def text(label, box, size=6.0, bold=False, fill=INK, rotate=0):
    p = place(label, box, size, 'helvetica', bold, rotate, cjk='source-han-sans')
    if not p['fits']:
        raise ValueError(f"Text does not fit box: label={label!r}, short={p['short']}")
    return svg_text(p, f'fill="{fill}"' + (' font-weight="bold"' if bold else ''))

def generate():
    width = 504
    height = 240

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}pt" height="{height}pt" viewBox="0 0 {width} {height}" font-family="\'TeX Gyre Heros\', \'Noto Sans CJK SC\', sans-serif" font-size="6.0">',
        f'''<defs>
<marker id="arrow-blue" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4.5" markerHeight="4.5" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 1 L5 3 L0 5 Z" fill="{BLUE}"/></marker>
<marker id="arrow-navy" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4.5" markerHeight="4.5" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 1 L5 3 L0 5 Z" fill="{NAVY}"/></marker>
<marker id="arrow-gray" viewBox="0 0 5 5" refX="4" refY="2.5" markerWidth="3.5" markerHeight="3.5" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0.8 L4 2.5 L0 4.2 Z" fill="{GRAY}"/></marker>
</defs>
<rect width="100%" height="100%" fill="{WHITE}"/>
'''
    ]

    # =========================================================================
    # 1. 左上角：小型五层架构栈 (Small Inset Card) - 仅占约 88 × 106 pt
    # =========================================================================
    parts.append(f'''<g id="architecture-inset">
<rect x="8" y="8" width="88" height="106" rx="2.5" fill="#fafbfc" stroke="{BORDER_GRAY}" stroke-width="0.7"/>
{text("五层协同架构", (8, 10, 88, 11), size=6.2, bold=True, fill=NAVY)}

<!-- 算法层 -->
<rect x="12" y="23" width="65" height="11" rx="1.2" fill="#f7fafc" stroke="{BORDER_GRAY}" stroke-width="0.6"/>
{text("联邦学习算法层", (12, 23.5, 65, 10), size=5.4, bold=True, fill=INK)}

<!-- 运行层 -->
<rect x="12" y="36.5" width="65" height="11" rx="1.2" fill="{BG_BLUE}" stroke="{BORDER_GRAY}" stroke-width="0.6"/>
{text("联邦学习运行层", (12, 37, 65, 10), size=5.4, bold=True, fill=NAVY)}

<!-- 传输适配层 -->
<rect x="12" y="50" width="65" height="11" rx="1.2" fill="{BG_BLUE}" stroke="{BORDER_GRAY}" stroke-width="0.6"/>
{text("传输适配层 (UFTP/TCP)", (12, 50.5, 65, 10), size=5.0, bold=True, fill=NAVY)}

<!-- TUN 虚拟网卡边界 -->
<line x1="12" y1="64.5" x2="77" y2="64.5" stroke="{GRAY}" stroke-width="0.6" stroke-dasharray="2,1.5"/>
<rect x="17" y="60" width="55" height="9" rx="1" fill="{WHITE}" stroke="{GRAY}" stroke-width="0.5"/>
{text("TUN 虚拟网卡边界", (17, 60, 55, 9), size=4.8, fill=MUTED)}

<!-- 链路控制层 -->
<rect x="12" y="70.5" width="65" height="11" rx="1.2" fill="#deebf7" stroke="{BORDER_GRAY}" stroke-width="0.6"/>
{text("链路控制层 (调度/反压)", (12, 71, 65, 10), size=5.0, bold=True, fill=NAVY)}

<!-- 无线收发层 -->
<rect x="12" y="84" width="65" height="11" rx="1.2" fill="#f7fafc" stroke="{BORDER_GRAY}" stroke-width="0.6"/>
{text("无线收发层 (注入/捕获)", (12, 84.5, 65, 10), size=5.0, bold=True, fill=INK)}

<!-- 控制旁路通道 -->
<path d="M77 75 H84 V89 H77" fill="none" stroke="{GRAY}" stroke-width="0.6" stroke-dasharray="2,1.5" marker-end="url(#arrow-gray)"/>
{text("控制\\n旁路", (76, 75.5, 16, 13), size=4.0, fill=GRAY)}

<!-- 底部微注 -->
{text("节点内嵌全栈协同", (8, 97, 88, 9), size=4.8, fill=GRAY)}

<!-- 优雅的短虚弧线指向服务端 -->
<path d="M96 46 C110 46, 120 54, 134 60" fill="none" stroke="{BORDER_GRAY}" stroke-width="0.8" stroke-dasharray="3,2" marker-end="url(#arrow-gray)"/>
</g>''')

    # =========================================================================
    # 2. 参数服务端节点 (Server Node)
    # =========================================================================
    parts.append(f'''<g id="server-node">
<g fill="{BG_BLUE}" stroke="{NAVY}" stroke-width="0.8">
  <rect x="138" y="52" width="34" height="40" rx="2"/>
  <rect x="142" y="57" width="26" height="7" rx="1" fill="{WHITE}"/>
  <rect x="142" y="67" width="26" height="7" rx="1" fill="{WHITE}"/>
  <rect x="142" y="77" width="26" height="7" rx="1" fill="{WHITE}"/>
  <circle cx="146" cy="60.5" r="1" fill="{BLUE}"/>
  <circle cx="150" cy="60.5" r="1" fill="#38a169"/>
  <circle cx="146" cy="70.5" r="1" fill="{BLUE}"/>
  <circle cx="150" cy="70.5" r="1" fill="#38a169"/>
  <circle cx="146" cy="80.5" r="1" fill="{BLUE}"/>
  <circle cx="150" cy="80.5" r="1" fill="#38a169"/>
</g>
{text("参数服务端", (128, 94, 54, 11), size=6.8, bold=True, fill=NAVY)}
</g>''')

    # 步骤 ①：准备全局模型 (短弯曲弧线连接)
    parts.append(f'''<g id="step1-prep">
<!-- 弯曲短弧线：Server -> 模型图符 -->
<path d="M155 52 C155 36, 168 32, 182 32" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>
<circle cx="166" cy="27" r="3.6" fill="{NAVY}"/>
{text("1", (162.4, 23.4, 7.2, 7.2), size=5.5, bold=True, fill=WHITE)}

<!-- 模型清单小图符卡片 -->
<g transform="translate(184, 20)">
  <rect x="0" y="0" width="42" height="24" rx="2" fill="{WHITE}" stroke="{BORDER_GRAY}" stroke-width="0.6"/>
  <path d="M4 4 H11 L14 7 V20 H4 Z" fill="{BG_BLUE}" stroke="{NAVY}" stroke-width="0.5"/>
  <line x1="6" y1="10" x2="12" y2="10" stroke="{GRAY}" stroke-width="0.5"/>
  <line x1="6" y1="13" x2="12" y2="13" stroke="{GRAY}" stroke-width="0.5"/>
  {text("全局模型\\n与清单", (16, 3, 24, 18), size=5.0, bold=True, fill=INK)}
</g>
</g>''')

    # =========================================================================
    # 3. 步骤 ②：UFTP 下行可靠广播枢纽
    # =========================================================================
    parts.append(f'''<g id="step2-downlink">
<!-- 从模型图符向右短弯曲弧线进入 UFTP 广播卡片 -->
<path d="M226 32 C238 32, 246 30, 256 30" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>
<circle cx="241" cy="25" r="3.6" fill="{NAVY}"/>
{text("2", (237.4, 21.4, 7.2, 7.2), size=5.5, bold=True, fill=WHITE)}

<!-- UFTP 广播卡片 (带天线发射图符) -->
<rect x="258" y="15" width="84" height="32" rx="2.5" fill="{WHITE}" stroke="{BLUE}" stroke-width="0.8"/>
<path d="M268 36 V25 M265 25 H271" stroke="{NAVY}" stroke-width="0.8"/>
<path d="M272 22 A4 4 0 0 1 275 29" fill="none" stroke="{BLUE}" stroke-width="0.7"/>
<path d="M275 19 A7 7 0 0 1 280 32" fill="none" stroke="{BLUE}" stroke-width="0.7"/>

{text("UFTP 下行可靠广播", (278, 17, 62, 11), size=6.0, bold=True, fill=BLUE)}
{text("原始帧高阶注入 | FEC", (278, 29, 62, 9), size=5.0, fill=MUTED)}
</g>''')

    # =========================================================================
    # 4. 右侧边缘客户端集群 (Clients)
    # =========================================================================
    clients = [
        (1, 40),
        (2, 95),
        (3, 150),
    ]

    for i, cy in clients:
        parts.append(f'''<g id="client-node-{i}">
<g fill="{WHITE}" stroke="{NAVY}" stroke-width="0.7">
  <rect x="424" y="{cy}" width="28" height="20" rx="1.5"/>
  <rect x="428" y="{cy+3}" width="20" height="11" rx="0.8" fill="{LIGHT_GRAY}"/>
  <!-- 外置天线 -->
  <line x1="447" y1="{cy}" x2="453" y2="{cy-8}" stroke="{NAVY}" stroke-width="0.8"/>
  <circle cx="453" cy="{cy-8}" r="1.1" fill="{NAVY}"/>
  <!-- 接口 -->
  <line x1="431" y1="{cy+16}" x2="437" y2="{cy+16}" stroke="{BLUE}" stroke-width="0.7"/>
  <line x1="439" y1="{cy+16}" x2="445" y2="{cy+16}" stroke="{GRAY}" stroke-width="0.7"/>
</g>
{text(f"客户端 {i}", (414, cy+21, 48, 10), size=6.2, bold=True, fill=INK)}
</g>''')

    # 客户端省略号与 Client ··· N
    parts.append(f'''<g id="client-ellipsis">
<circle cx="438" cy="186" r="1.2" fill="{GRAY}"/>
<circle cx="438" cy="192" r="1.2" fill="{GRAY}"/>
<circle cx="438" cy="198" r="1.2" fill="{GRAY}"/>
{text("客户端 ··· N", (414, 204, 48, 10), size=5.8, bold=True, fill=GRAY)}
</g>''')

    # 【优美短弯曲扇出弧线】：从 UFTP 广播右侧优雅发散至 Client 1, 2, 3，绝无长线贯通！
    # 枢纽分发主干短弧：
    parts.append(f'''<path d="M342 31 C360 31, 370 38, 382 45" fill="none" stroke="{BLUE}" stroke-width="1.2"/>''')
    # 分支短弧 1：至 Client 1
    parts.append(f'''<path d="M382 45 C394 40, 408 42, 422 46" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>''')
    # 分支短弧 2：至 Client 2
    parts.append(f'''<path d="M382 45 C390 60, 404 85, 422 98" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>''')
    # 分支短弧 3：至 Client 3
    parts.append(f'''<path d="M382 45 C388 80, 400 130, 422 153" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>''')

    # 步骤 ③：解耦独立校验 (紧贴 Client 1 下方的小指示卡片)
    parts.append(f'''<g id="step3-decouple">
<rect x="336" y="68" width="68" height="20" rx="2" fill="{LIGHT_BLUE}" stroke="{BLUE}" stroke-width="0.6"/>
<circle cx="341" cy="78" r="3.6" fill="{NAVY}"/>
{text("3", (337.4, 74.4, 7.2, 7.2), size=5.5, bold=True, fill=WHITE)}
{text("校验即刻启动训练\\n【解耦不等待其余】", (346, 69, 56, 18), size=5.0, bold=True, fill=BLUE)}
<!-- 短弯曲弧线指向训练 -->
<path d="M404 78 C414 78, 418 84, 422 88" fill="none" stroke="{BLUE}" stroke-width="0.7" marker-end="url(#arrow-blue)"/>
</g>''')

    # =========================================================================
    # 5. 步骤 ④：本地模型训练 (右下方紧凑卡片)
    # =========================================================================
    parts.append(f'''<g id="step4-train">
<!-- 弯曲短弧线从客户端向下汇聚进入训练卡片 -->
<path d="M424 165 C404 180, 392 188, 375 194" fill="none" stroke="{GRAY}" stroke-width="0.8" marker-end="url(#arrow-gray)"/>
<circle cx="394" cy="180" r="3.6" fill="{NAVY}"/>
{text("4", (390.4, 176.4, 7.2, 7.2), size=5.5, bold=True, fill=WHITE)}

<!-- 本地训练卡片 (带神经网络小图符) -->
<rect x="296" y="184" width="76" height="30" rx="2" fill="{LIGHT_GRAY}" stroke="{GRAY}" stroke-width="0.6"/>
<circle cx="304" cy="193" r="1.3" fill="{BLUE}"/>
<circle cx="304" cy="205" r="1.3" fill="{BLUE}"/>
<circle cx="313" cy="199" r="1.3" fill="{NAVY}"/>
<line x1="305" y1="193" x2="312" y2="199" stroke="{GRAY}" stroke-width="0.5"/>
<line x1="305" y1="205" x2="312" y2="199" stroke="{GRAY}" stroke-width="0.5"/>
{text("本地模型训练", (317, 187, 53, 10), size=6.0, bold=True, fill=INK)}
{text("私有数据并行计算", (317, 198, 53, 9), size=5.0, fill=MUTED)}
</g>''')

    # =========================================================================
    # 6. 步骤 ⑤：发送权轮转有序交替回传 (中下方枢纽)
    # =========================================================================
    parts.append(f'''<g id="step5-uplink">
<!-- 从本地训练向左短弯曲弧线流入有序回传卡片 -->
<path d="M296 199 C284 199, 276 199, 260 199" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>
<circle cx="278" cy="192" r="3.6" fill="{NAVY}"/>
{text("5", (274.4, 188.4, 7.2, 7.2), size=5.5, bold=True, fill=WHITE)}

<!-- 有序回传卡片 (带时隙/时钟图符) -->
<rect x="174" y="184" width="84" height="32" rx="2.5" fill="{WHITE}" stroke="{BLUE}" stroke-width="0.8"/>
<rect x="180" y="191" width="6" height="6" rx="0.6" fill="{BG_BLUE}" stroke="{BLUE}" stroke-width="0.5"/>
<rect x="188" y="191" width="6" height="6" rx="0.6" fill="{WHITE}" stroke="{GRAY}" stroke-width="0.5"/>
<path d="M197 192 A4 4 0 1 1 193 190" fill="none" stroke="{NAVY}" stroke-width="0.6" marker-end="url(#arrow-gray)"/>

{text("轮转调度有序回传", (174, 199, 84, 9), size=5.8, bold=True, fill=BLUE)}
{text("HTTP PUT / TCP 跨窗口", (174, 207, 84, 9), size=4.8, fill=MUTED)}

<!-- 反向 TCP ACK 辅助流：在回传卡片右上方设置一条干净轻柔的短弧虚线 -->
<path d="M210 178 C240 166, 280 164, 310 170" fill="none" stroke="{GRAY}" stroke-width="0.6" stroke-dasharray="2,2" marker-end="url(#arrow-gray)"/>
{text("反向 TCP ACK (维持流控)", (222, 160, 78, 8), size=4.8, fill=GRAY)}
</g>''')

    # =========================================================================
    # 7. 步骤 ⑥：越过屏障与全局聚合 (闭环回 Server)
    # =========================================================================
    parts.append(f'''<g id="step6-aggregate">
<!-- 从有序回传向左上方弯曲短弧流入聚合阶段 -->
<path d="M174 196 C155 196, 142 188, 136 174" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>
<circle cx="150" cy="184" r="3.6" fill="{NAVY}"/>
{text("6", (146.4, 180.4, 7.2, 7.2), size=5.5, bold=True, fill=WHITE)}

<!-- 聚合卡片 (紧凑放在 Server 下方) -->
<rect x="108" y="142" width="58" height="28" rx="2" fill="{BG_BLUE}" stroke="{BLUE}" stroke-width="0.7"/>
{text("越过下行屏障", (108, 144, 58, 9), size=5.2, bold=True, fill=NAVY)}
{text("收齐更新与全局聚合", (108, 153, 58, 9), size=5.0, fill=INK)}

<!-- 闭环短弯曲弧线：从聚合卡片向上弯曲回 Server 底部 -->
<path d="M137 142 C137 125, 148 115, 155 96" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>
</g>''')

    parts.append('</svg>')

    svg_out = '\n'.join(parts)
    (HERE / 'fig.svg').write_text(svg_out, encoding='utf-8')
    print("fig.svg updated successfully.")

    # 导出预览图
    svg_uri = (HERE / 'fig.svg').resolve().as_uri()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(device_scale_factor=2)
        page.goto(svg_uri)
        page.locator('svg').screenshot(path=str(HERE / 'temp_preview.png'))
        browser.close()
    print("temp_preview.png generated.")

if __name__ == '__main__':
    generate()
