#!/usr/bin/env python3
"""生成全新的 Figure 1（跨栏全宽，端到端通信流程为主体，左侧引申五层架构）。"""
from pathlib import Path
import json
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SKILL_SCRIPTS = ROOT / '.agents/skills/figgenie-paper-diagram/scripts'
sys.path.insert(0, str(SKILL_SCRIPTS))
from place_text import place, svg_text
import cairosvg

# 严谨蓝灰工程风调色板 (IEEE Style)
NAVY = '#1a365d'        # 墨蓝/深海军蓝，用于核心标题、重要卡片边框、步骤底色
BLUE = '#2b6cb0'        # 主冷蓝，用于主数据面、广播与上行流向、重点标签
LIGHT_BLUE = '#ebf8ff'  # 浅冰蓝底色，用于解耦提示、强调卡片
BG_BLUE = '#edf3fa'     # 浅蓝灰底色，用于服务端图标、运行层与适配层
INK = '#2d3748'         # 深炭灰，用于主体文字与正文标签
MUTED = '#4a5568'       # 石板灰，用于次要说明、边界标签
GRAY = '#718096'        # 冷中灰，用于辅助线、控制旁路、省略号
LIGHT_GRAY = '#edf2f7'  # 极浅灰，用于次要背景底条
BORDER_GRAY = '#cbd5e0' # 边框灰，用于卡片与容器边框
WHITE = '#ffffff'       # 纯白

def text(label, box, size=6.5, bold=False, fill=INK, rotate=0):
    p = place(label, box, size, 'helvetica', bold, rotate, cjk='source-han-sans')
    if not p['fits']:
        raise ValueError(f"Text does not fit box: label={label!r}, short={p['short']}")
    return svg_text(p, f'fill="{fill}"' + (' font-weight="bold"' if bold else ''))

def generate():
    width = 504
    height = 240

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}pt" height="{height}pt" viewBox="0 0 {width} {height}" font-family="\'TeX Gyre Heros\', \'Noto Sans CJK SC\', sans-serif" font-size="6.5" data-spec="wfb-fl-figure1" data-spec-version="4">',
        f'''<defs>
<marker id="arrow-blue" viewBox="0 0 5 5" refX="4.5" refY="2.5" markerWidth="4.5" markerHeight="4.5" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0.5 L4.5 2.5 L0 4.5 Z" fill="{BLUE}"/></marker>
<marker id="arrow-navy" viewBox="0 0 5 5" refX="4.5" refY="2.5" markerWidth="4.5" markerHeight="4.5" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0.5 L4.5 2.5 L0 4.5 Z" fill="{NAVY}"/></marker>
<marker id="arrow-gray" viewBox="0 0 4 4" refX="3.5" refY="2" markerWidth="3.5" markerHeight="3.5" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0.5 L3.5 2 L0 3.5 Z" fill="{GRAY}"/></marker>
</defs>
<rect width="100%" height="100%" fill="{WHITE}"/>
'''
    ]

    # =========================================================================
    # 1. 左侧：五层架构引申卡片 (Architecture Callout)
    # =========================================================================
    parts.append(f'''<g id="c:stack" data-kind="container" data-role="novel">
<rect x="8" y="10" width="130" height="220" rx="3" fill="#fafbfc" stroke="{BORDER_GRAY}" stroke-width="0.7"/>
{text("WFB-FL 五层架构", (8, 12, 130, 14), size=7.5, bold=True, fill=NAVY)}
</g>''')

    # 五层定义：(id, title, desc, y, h, role, fill)
    arch_layers = [
        ('algorithm', '联邦学习算法层', '模型准备与聚合 / 本地训练', 29, 22, 'existing', '#f7fafc'),
        ('runtime', '联邦学习运行层', '轮次编排 / 下行基线 / 更新收齐', 53, 22, 'novel', BG_BLUE),
        ('transport', '传输适配层', '下行 UFTP / 上行 HTTP+TCP', 77, 25, 'novel', BG_BLUE),
        ('link', '链路控制层', '出站队列 / TUN反压 / 轮转调度', 115, 28, 'novel', '#deebf7'),
        ('radio', '无线收发层', '原始帧注入 (TX) / Monitor 捕获', 146, 26, 'existing', '#f7fafc'),
    ]

    for id_, title, desc, y, h, role, fill in arch_layers:
        parts.append(f'<g id="n:{id_}" data-kind="node" data-role="{role}" data-parent="stack">')
        parts.append(f'<rect x="14" y="{y}" width="94" height="{h}" rx="1.5" fill="{fill}" stroke="{BORDER_GRAY}" stroke-width="0.7"/>')
        parts.append(text(title, (14, y + 1.5, 94, 10), size=6.5, bold=True, fill=NAVY if role == "novel" else INK))
        parts.append(text(desc, (14, y + 11.5, 94, 10), size=6.0, fill=MUTED))
        parts.append('</g>')

    # TUN 网卡边界横切线与标签 (虚线不穿出边框)
    parts.append(f'''<g id="a:tun-boundary" data-kind="annotation" data-anchor="transport">
<line x1="14" y1="105.5" x2="108" y2="105.5" stroke="{GRAY}" stroke-width="0.7" stroke-dasharray="3,2"/>
<rect x="15" y="100" width="92" height="12" rx="2" fill="{WHITE}" stroke="{GRAY}" stroke-width="0.7"/>
{text("TUN 虚拟网卡边界", (15, 100, 92, 12), size=6.0, bold=True, fill=MUTED)}
</g>''')

    # 独立调度控制通道 (旁路直接穿透)
    parts.append(f'''<g id="e:control-bypass" data-kind="edge" data-from="link" data-to="radio" data-edge-kind="control">
<path d="M108 126 H118 V158 H108" fill="none" stroke="{GRAY}" stroke-width="0.7" stroke-dasharray="2,2" marker-end="url(#arrow-gray)"/>
<rect x="108" y="132" width="28" height="20" fill="#fafbfc"/>
{text("调度控制\\n不经TUN", (108, 132, 28, 20), size=6.0, fill=GRAY)}
</g>''')

    # 底部说明卡片
    parts.append(f'''<g id="a:stack-footer" data-kind="annotation">
<rect x="14" y="178" width="118" height="44" rx="2" fill="{WHITE}" stroke="{BORDER_GRAY}" stroke-width="0.7"/>
{text("两端节点均内嵌此协同栈\\n协同收发与调度闭环", (14, 180, 118, 24), size=6.0, fill=GRAY)}
</g>''')

    # 引申呼出细虚线：从架构框右上角连向服务端，生动展示引申关系
    parts.append(f'''<g id="a:expansion-lines" data-kind="annotation" data-anchor="server">
<path d="M138 34 H158" fill="none" stroke="{BORDER_GRAY}" stroke-width="0.8" stroke-dasharray="3,2" marker-end="url(#arrow-gray)"/>
{text("全栈引申", (133, 23, 28, 10), size=6.0, fill=GRAY)}
</g>''')

    # =========================================================================
    # 2. 右侧主体：服务端 (Server)
    # =========================================================================
    parts.append(f'''<g id="n:server" data-kind="node" data-role="novel" data-label="outside">
<!-- 服务器机架矢量图标 -->
<g data-symbol="server-rack" fill="{BG_BLUE}" stroke="{NAVY}" stroke-width="0.7">
  <rect x="160" y="16" width="32" height="36" rx="2"/>
  <rect x="164" y="20.5" width="24" height="6.5" rx="0.8" fill="{WHITE}"/>
  <rect x="164" y="29.5" width="24" height="6.5" rx="0.8" fill="{WHITE}"/>
  <line x1="164" y1="42" x2="188" y2="42" stroke="{GRAY}" stroke-width="0.7"/>
  <circle cx="168" cy="23.5" r="0.9" fill="{BLUE}"/>
  <circle cx="171.5" cy="23.5" r="0.9" fill="#38a169"/>
  <circle cx="168" cy="32.5" r="0.9" fill="{BLUE}"/>
  <circle cx="171.5" cy="32.5" r="0.9" fill="#38a169"/>
  <line x1="176" y1="23.5" x2="185" y2="23.5" stroke="{BORDER_GRAY}" stroke-width="0.7"/>
  <line x1="176" y1="32.5" x2="185" y2="32.5" stroke="{BORDER_GRAY}" stroke-width="0.7"/>
</g>
{text("参数服务端 (Server)", (140, 54, 72, 11), size=6.5, bold=True, fill=NAVY)}
</g>''')

    # 服务端内部步骤 ①：准备全局模型与清单
    parts.append(f'''<g id="s:1-box">
<rect x="144" y="70" width="72" height="24" rx="2" fill="{WHITE}" stroke="{BORDER_GRAY}" stroke-width="0.7"/>
{text("① 准备全局模型\\n生成清单与托管文件", (144, 70, 72, 24), size=6.0, bold=True, fill=INK)}
<path d="M176 65 V70" fill="none" stroke="{GRAY}" stroke-width="0.7" marker-end="url(#arrow-gray)"/>
</g>''')

    # 服务端步骤 ⑥：越过下行屏障 & 全局模型聚合
    parts.append(f'''<g id="s:6-box">
<rect x="142" y="174" width="76" height="26" rx="2" fill="{BG_BLUE}" stroke="{BLUE}" stroke-width="0.7"/>
{text("⑥ 越过下行屏障\\n收齐更新 & 全局聚合", (142, 174, 76, 26), size=6.0, bold=True, fill=NAVY)}
</g>''')

    # =========================================================================
    # 3. 右侧主体：边缘客户端集群 (Clients)
    # =========================================================================
    client_y = [18, 74, 130]
    for i, y in enumerate(client_y, 1):
        parts.append(f'''<g id="n:client{i}" data-kind="node" data-role="novel" data-label="outside">
<!-- 边缘单板计算设备带天线图标 -->
<g data-symbol="edge-device" fill="{WHITE}" stroke="{NAVY}" stroke-width="0.7">
  <rect x="444" y="{y}" width="26" height="18" rx="1.5"/>
  <rect x="448" y="{y+3}" width="18" height="10" rx="0.5" fill="{LIGHT_GRAY}"/>
  <!-- 天线与天线球 -->
  <line x1="465" y1="{y}" x2="472" y2="{y-8}" stroke="{NAVY}" stroke-width="0.7"/>
  <circle cx="472" cy="{y-8}" r="1.1" fill="{NAVY}"/>
  <!-- 接口示意 -->
  <line x1="450" y1="{y+15}" x2="456" y2="{y+15}" stroke="{BLUE}" stroke-width="0.7"/>
  <line x1="458" y1="{y+15}" x2="464" y2="{y+15}" stroke="{GRAY}" stroke-width="0.7"/>
</g>
{text(f"边缘客户端 {i}", (430, y+20, 54, 10), size=6.5, bold=True, fill=INK)}
</g>''')

    # 客户端省略号与 Client ··· N
    parts.append(f'''<g id="a:more-clients" data-kind="annotation" data-anchor="client3">
<circle cx="457" cy="168" r="1.2" fill="{GRAY}"/>
<circle cx="457" cy="174" r="1.2" fill="{GRAY}"/>
<circle cx="457" cy="180" r="1.2" fill="{GRAY}"/>
{text("边缘客户端 ··· N", (430, 186, 54, 10), size=6.0, bold=True, fill=GRAY)}
</g>''')

    # =========================================================================
    # 4. 中间通信流：下行广播 (Downlink Broadcast)
    # =========================================================================
    # 下行广播由 Server (192, 28) 向右一直延伸至 x=428，再在右端分流到各个 Client，绝不穿透任何卡片
    parts.append(f'''<g id="e:downlink" data-kind="edge" data-from="server" data-to="client3" data-edge-kind="data">
<path d="M192 28 H428 V24 H444" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>
<path d="M428 28 V80 H444" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>
<path d="M428 80 V136 H444" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>

<!-- 下行说明卡片 (置于上方空白区) -->
<rect x="218" y="11" width="200" height="21" rx="2" fill="{WHITE}" stroke="{BLUE}" stroke-width="0.7"/>
{text("② 模型下行广播（UFTP 一对多可靠交付）", (218, 11, 200, 11), size=6.5, bold=True, fill=BLUE)}
{text("802.11 原始帧高阶速率注入 | 链路层 FEC 冗余", (218, 21, 200, 10), size=6.0, fill=MUTED)}
</g>''')

    # 步骤 ③ 客户端本地校验与即刻训练 (解耦特性)
    # 卡片置于 x: 296, w: 118，右边界在 414，完全避开 x=428 的垂直分流线！
    parts.append(f'''<g id="a:decouple-note" data-kind="annotation" data-anchor="client1">
<rect x="296" y="44" width="118" height="22" rx="2" fill="{LIGHT_BLUE}" stroke="{BLUE}" stroke-width="0.7"/>
{text("③ 本地校验通过即刻启动训练\\n【解耦】无需等待其余节点", (296, 44, 118, 22), size=6.0, bold=True, fill=BLUE)}
<!-- 指示箭头从卡片右端指向 Client 1 -->
<path d="M414 55 H444" fill="none" stroke="{BLUE}" stroke-width="0.7" marker-end="url(#arrow-blue)"/>
</g>''')

    # =========================================================================
    # 5. 中间计算流程：步骤 ④ 本地模型训练
    # =========================================================================
    parts.append(f'''<g id="e:train" data-kind="edge" data-from="client1" data-to="client1" data-edge-kind="data">
<rect x="236" y="84" width="170" height="23" rx="2" fill="{LIGHT_GRAY}" stroke="{GRAY}" stroke-width="0.7"/>
{text("④ 各客户端本地模型训练（ResNet-18）", (236, 85, 170, 11), size=6.5, bold=True, fill=INK)}
{text("私有数据并行计算，训练耗时允许差异", (236, 96, 170, 10), size=6.0, fill=MUTED)}
<!-- Client 1 旁边的本地计算回环箭头 -->
<path d="M470 23 C482 23 482 31 470 31" fill="none" stroke="{GRAY}" stroke-width="0.7" marker-end="url(#arrow-gray)"/>
</g>''')

    # =========================================================================
    # 6. 中间通信流：上行交替回传 (Uplink Transmission)
    # =========================================================================
    # 各 Client 回传汇聚在 x=434，然后在 y=154 水平向左返回 Server (192, 154)
    parts.append(f'''<g id="e:uplink" data-kind="edge" data-from="client3" data-to="server" data-edge-kind="data">
<path d="M444 146 H434 V154 H192" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>
<path d="M444 34 H434" fill="none" stroke="{BLUE}" stroke-width="1.2"/>
<path d="M444 90 H434" fill="none" stroke="{BLUE}" stroke-width="1.2"/>

<!-- 上行说明卡片 -->
<rect x="218" y="117" width="200" height="22" rx="2" fill="{WHITE}" stroke="{BLUE}" stroke-width="0.7"/>
{text("⑤ 更新有序交替回传（HTTP PUT / TCP 字节流）", (218, 117, 200, 11), size=6.5, bold=True, fill=BLUE)}
{text("轮转调度限时窗口防碰撞 | 跨窗口持续推进", (218, 128, 200, 10), size=6.0, fill=MUTED)}

<!-- 反向 TCP ACK 辅助流：虚线在 y=162，文字在 y=165，错开不重叠 -->
<path d="M192 162 H410" fill="none" stroke="{GRAY}" stroke-width="0.7" stroke-dasharray="2,2" marker-end="url(#arrow-gray)"/>
{text("服务端反向 TCP ACK (维持双向流控)", (220, 165, 180, 10), size=6.0, fill=GRAY)}
</g>''')

    # =========================================================================
    # 7. 全局聚合边 (e:aggregate)
    # =========================================================================
    parts.append(f'''<g id="e:aggregate" data-kind="edge" data-from="server" data-to="server" data-edge-kind="data">
<path d="M192 154 H180 V174" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrow-blue)"/>
</g>''')

    # =========================================================================
    # 8. 步骤标牌圆圈 (Circled Numbers ① ~ ⑥)
    # =========================================================================
    step_positions = [
        (1, 150, 74, 'server'),
        (2, 226, 15, 'downlink'),
        (3, 304, 48, 'client1'),
        (4, 244, 89, 'train'),
        (5, 226, 122, 'uplink'),
        (6, 148, 178, 'aggregate')
    ]
    for n, x, y, target in step_positions:
        parts.append(f'''<g id="s:{n}" data-kind="step" data-attached-to="{target}">
<circle cx="{x}" cy="{y}" r="4" fill="{NAVY}" stroke="{WHITE}" stroke-width="0.7"/>
{text(str(n), (x - 4, y - 4, 8, 8), size=6.0, bold=True, fill=WHITE)}
</g>''')

    parts.append('</svg>')

    svg_text_out = '\n'.join(parts)
    svg_path = HERE / 'fig.svg'
    svg_path.write_text(svg_text_out, encoding='utf-8')
    print(f"Generated SVG: {svg_path} ({len(svg_text_out)} bytes)")

    # 导出 PNG 预览
    png_path = HERE / 'fig.png'
    cairosvg.svg2png(bytestring=svg_text_out.encode('utf-8'), write_to=str(png_path), scale=3.0)
    print(f"Generated PNG: {png_path}")

    # 导出 PDF
    pdf_path = HERE / 'fig.final.pdf'
    cairosvg.svg2pdf(bytestring=svg_text_out.encode('utf-8'), write_to=str(pdf_path))
    print(f"Generated PDF: {pdf_path}")

if __name__ == '__main__':
    generate()
