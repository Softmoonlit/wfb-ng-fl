#!/usr/bin/env python3
"""
生成符合最新方案的 IEEE Communications Magazine Figure 1:
- 双栏全宽 (504 pt x 230 pt)
- 左上角小型微五层架构栈 (Micro-stack callout)
- 整体以图为主，文字精练，去除冗长大段文字卡片
- 绝不用长直线与生硬折线，全面采用平滑贝塞尔曲线 (Cubic Bezier) 与短弯曲弧线
- 精致的机架服务器、无线电磁辐射波、边缘单板机群 (Client 1/2/3/···/N)、自回环训练圆弧
- 完全符合规则：无独立监听节点，标注 UFTP 下行、HTTP PUT / TCP 上行，TUN 边界与调度旁路
- 严格使用系统已有的 'Noto Sans CJK SC', 'WenQuanYi Zen Hei' 字体，彻底杜绝豆腐块
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 经典 IEEE 蓝灰工程风调色板
NAVY = '#1a365d'        # 墨蓝：主外框、核心标题、步骤圆圈底色
BLUE = '#2b6cb0'        # 主冷蓝：主数据面流向、广播弧线、高亮元素
LIGHT_BLUE = '#ebf8ff'  # 浅冰蓝底色：解耦小胶囊
BG_BLUE = '#edf3fa'     # 浅蓝灰：服务端机箱底色、微栈底色
INK = '#2d3748'         # 深炭灰：主体标签
MUTED = '#4a5568'       # 石板灰：次要说明
GRAY = '#718096'        # 冷中灰：辅助虚线、ACK流
LIGHT_GRAY = '#f7fafc'  # 极浅灰：板卡底色
BORDER_GRAY = '#cbd5e0' # 边框灰
WHITE = '#ffffff'       # 纯白
FONT = "'Noto Sans CJK SC', 'WenQuanYi Zen Hei', 'Helvetica Neue', Arial, sans-serif"

def generate_figure1_svg():
    width = 504
    height = 230

    svg = []
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}pt" height="{height}pt" viewBox="0 0 {width} {height}" font-family="{FONT}">')
    
    # 滤镜与箭头定义
    svg.append(f'''<defs>
<marker id="arr-blue" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4.5" markerHeight="4.5" orient="auto-start-reverse">
  <path d="M0 0.5 L6 3 L0 5.5 Z" fill="{BLUE}"/>
</marker>
<marker id="arr-navy" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4.5" markerHeight="4.5" orient="auto-start-reverse">
  <path d="M0 0.5 L6 3 L0 5.5 Z" fill="{NAVY}"/>
</marker>
<marker id="arr-gray" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4" markerHeight="4" orient="auto-start-reverse">
  <path d="M0 0.5 L6 3 L0 5.5 Z" fill="{GRAY}"/>
</marker>
</defs>
<rect width="100%" height="100%" fill="{WHITE}"/>
''')

    # =========================================================================
    # 1. 左上角：小型微五层架构栈 (Micro-stack Callout)
    # =========================================================================
    svg.append(f'''<!-- 左上角微架构栈 -->
<g id="micro-arch-stack">
  <rect x="12" y="10" width="94" height="88" rx="2.5" fill="{BG_BLUE}" stroke="{BORDER_GRAY}" stroke-width="0.7"/>
  <text x="59" y="21" font-size="6.8" font-weight="bold" fill="{NAVY}" text-anchor="middle">WFB-FL 协同栈</text>
  
  <!-- 1. 算法层 -->
  <rect x="16" y="26" width="86" height="10" rx="1.2" fill="{WHITE}" stroke="{BORDER_GRAY}" stroke-width="0.5"/>
  <text x="59" y="33.5" font-size="5.2" font-weight="bold" fill="{INK}" text-anchor="middle">FL Algorithm Layer</text>
  
  <!-- 2. 运行层 -->
  <rect x="16" y="38" width="86" height="10" rx="1.2" fill="{WHITE}" stroke="{BORDER_GRAY}" stroke-width="0.5"/>
  <text x="59" y="45.5" font-size="5.2" font-weight="bold" fill="{INK}" text-anchor="middle">FL Runtime Layer</text>

  <!-- 3. 传输适配层 -->
  <rect x="16" y="50" width="86" height="10" rx="1.2" fill="{LIGHT_BLUE}" stroke="{BLUE}" stroke-width="0.6"/>
  <text x="59" y="57.5" font-size="5.2" font-weight="bold" fill="{BLUE}" text-anchor="middle">Transport (UFTP / TCP)</text>

  <!-- TUN 边界虚线 -->
  <line x1="16" y1="62.5" x2="102" y2="62.5" stroke="{GRAY}" stroke-width="0.6" stroke-dasharray="2.5,1.5"/>
  <rect x="37" y="60" width="44" height="5.5" rx="1" fill="{WHITE}" stroke="{GRAY}" stroke-width="0.4"/>
  <text x="59" y="64.2" font-size="4.2" font-weight="bold" fill="{MUTED}" text-anchor="middle">TUN 边界 (数据面)</text>

  <!-- 4. 链路控制层 -->
  <rect x="16" y="66" width="86" height="10" rx="1.2" fill="{WHITE}" stroke="{BORDER_GRAY}" stroke-width="0.5"/>
  <text x="59" y="73.5" font-size="5.2" font-weight="bold" fill="{INK}" text-anchor="middle">Link Control (队列/调度)</text>

  <!-- 5. 无线收发层 -->
  <rect x="16" y="78" width="86" height="10" rx="1.2" fill="{WHITE}" stroke="{BORDER_GRAY}" stroke-width="0.5"/>
  <text x="59" y="85.5" font-size="5.2" font-weight="bold" fill="{INK}" text-anchor="middle">Radio (帧注入 / Monitor)</text>

  <!-- 调度控制旁路短弯弧 -->
  <path d="M 102 71 C 108 71 108 83 103 83" fill="none" stroke="{GRAY}" stroke-width="0.6" stroke-dasharray="1.5,1" marker-end="url(#arr-gray)"/>
</g>''')

    # 从微栈右下角引出平滑弯曲向下的短虚线弧，指向服务端机柜
    svg.append(f'''<!-- 微栈引申短弧 -->
<g id="callout-connector">
  <path d="M 106 55 C 122 55 130 80 135 108" fill="none" stroke="{BORDER_GRAY}" stroke-width="0.8" stroke-dasharray="3,2" marker-end="url(#arr-gray)"/>
  <text x="122" y="80" font-size="5.0" fill="{GRAY}" text-anchor="middle">全栈引申</text>
</g>''')

    # =========================================================================
    # 2. 左侧中下：参数服务端 (Server)
    # =========================================================================
    svg.append(f'''<!-- 参数服务端 -->
<g id="server-node">
  <!-- 机柜外壳 -->
  <rect x="116" y="116" width="42" height="60" rx="2.5" fill="{BG_BLUE}" stroke="{NAVY}" stroke-width="0.9"/>
  <!-- 机架顶部天线 -->
  <line x1="137" y1="116" x2="137" y2="105" stroke="{NAVY}" stroke-width="0.9"/>
  <circle cx="137" cy="105" r="1.4" fill="{NAVY}"/>
  
  <!-- 刀片单元 1 -->
  <rect x="120" y="121" width="34" height="10" rx="1" fill="{WHITE}" stroke="{BORDER_GRAY}" stroke-width="0.6"/>
  <circle cx="124" cy="126" r="1" fill="{BLUE}"/>
  <circle cx="128" cy="126" r="1" fill="#38a169"/>
  <line x1="133" y1="126" x2="150" y2="126" stroke="{BORDER_GRAY}" stroke-width="0.6" stroke-dasharray="2,1"/>
  
  <!-- 刀片单元 2 -->
  <rect x="120" y="134" width="34" height="10" rx="1" fill="{WHITE}" stroke="{BORDER_GRAY}" stroke-width="0.6"/>
  <circle cx="124" cy="139" r="1" fill="{BLUE}"/>
  <circle cx="128" cy="139" r="1" fill="#38a169"/>
  <line x1="133" y1="139" x2="150" y2="139" stroke="{BORDER_GRAY}" stroke-width="0.6" stroke-dasharray="2,1"/>

  <!-- 刀片单元 3 -->
  <rect x="120" y="147" width="34" height="10" rx="1" fill="{WHITE}" stroke="{BORDER_GRAY}" stroke-width="0.6"/>
  <circle cx="124" cy="152" r="1" fill="{BLUE}"/>
  <circle cx="128" cy="152" r="1" fill="#38a169"/>
  <line x1="133" y1="152" x2="150" y2="152" stroke="{BORDER_GRAY}" stroke-width="0.6" stroke-dasharray="2,1"/>

  <!-- 底部通风网格 -->
  <line x1="122" y1="164" x2="152" y2="164" stroke="{BORDER_GRAY}" stroke-width="0.8"/>
  <line x1="122" y1="168" x2="152" y2="168" stroke="{BORDER_GRAY}" stroke-width="0.8"/>

  <text x="137" y="185" font-size="6.5" font-weight="bold" fill="{NAVY}" text-anchor="middle">参数服务端 (Server)</text>
</g>''')

    # 步骤 ①：准备全局模型 (机架左下方微弯弧)
    svg.append(f'''<!-- 步骤 ① 准备模型 -->
<g id="step-1">
  <circle cx="98" cy="138" r="4.2" fill="{NAVY}"/>
  <text x="98" y="140.2" font-size="5.5" font-weight="bold" fill="{WHITE}" text-anchor="middle">1</text>
  <text x="98" y="149" font-size="5.5" font-weight="bold" fill="{INK}" text-anchor="middle">① 准备模型</text>
  <path d="M 98 132 C 98 122 108 122 115 124" fill="none" stroke="{GRAY}" stroke-width="0.7" marker-end="url(#arr-gray)"/>
</g>''')

    # 步骤 ⑥：越过屏障，收齐并全局聚合 (服务端机柜下方，向下内扣平滑短弧)
    svg.append(f'''<!-- 步骤 ⑥ 收齐并聚合 -->
<g id="step-6">
  <!-- 向下平滑短弯弧 -->
  <path d="M 137 188 C 137 198 128 205 116 205" fill="none" stroke="{BLUE}" stroke-width="1.1" marker-end="url(#arr-blue)"/>
  
  <rect x="36" y="196" width="76" height="16" rx="2" fill="{BG_BLUE}" stroke="{BLUE}" stroke-width="0.7"/>
  <circle cx="45" cy="204" r="3.8" fill="{NAVY}"/>
  <text x="45" y="206.2" font-size="5.2" font-weight="bold" fill="{WHITE}" text-anchor="middle">6</text>
  <text x="79" y="206.5" font-size="5.6" font-weight="bold" fill="{NAVY}" text-anchor="middle">⑥ 收齐更新并聚合</text>
</g>''')

    # =========================================================================
    # 3. 右侧：边缘客户端集群 (Clients)
    # =========================================================================
    clients = [
        ("Client 1", 34),
        ("Client 2", 86),
        ("Client 3", 138),
    ]

    for name, cy in clients:
        top_y = cy - 9
        svg.append(f'''<!-- 客户端节点 {name} -->
<g id="node-{name.lower().replace(' ', '')}">
  <rect x="444" y="{top_y}" width="28" height="18" rx="1.5" fill="{LIGHT_GRAY}" stroke="{NAVY}" stroke-width="0.8"/>
  <!-- 芯片与金手指接口 -->
  <rect x="448" y="{top_y + 3}" width="12" height="12" rx="0.8" fill="{BORDER_GRAY}" stroke="{GRAY}" stroke-width="0.5"/>
  <circle cx="454" cy="{top_y + 9}" r="1.5" fill="{INK}"/>
  <line x1="463" y1="{top_y + 4}" x2="469" y2="{top_y + 4}" stroke="{GRAY}" stroke-width="0.6"/>
  <line x1="463" y1="{top_y + 8}" x2="469" y2="{top_y + 8}" stroke="{GRAY}" stroke-width="0.6"/>
  <line x1="463" y1="{top_y + 12}" x2="469" y2="{top_y + 12}" stroke="{GRAY}" stroke-width="0.6"/>
  <!-- Wi-Fi 偶极子天线 -->
  <line x1="468" y1="{top_y}" x2="475" y2="{top_y - 8}" stroke="{NAVY}" stroke-width="0.8"/>
  <circle cx="475" cy="{top_y - 8}" r="1.2" fill="{NAVY}"/>

  <text x="458" y="{top_y + 26}" font-size="6.0" font-weight="bold" fill="{INK}" text-anchor="middle">{name}</text>

  <!-- 本地训练自回环微弯圆弧 (步骤 ④) -->
  <g transform="translate(420, {cy - 4})">
    <path d="M 6 0 C 13 -6 13 6 5 5" fill="none" stroke="{GRAY}" stroke-width="0.7" marker-end="url(#arr-gray)"/>
    <!-- 微型神经网络节点 -->
    <circle cx="-1" cy="-2" r="0.8" fill="{MUTED}"/>
    <circle cx="-1" cy="4" r="0.8" fill="{MUTED}"/>
    <circle cx="4" cy="1" r="0.8" fill="{BLUE}"/>
    <line x1="-1" y1="-2" x2="4" y2="1" stroke="{GRAY}" stroke-width="0.4"/>
    <line x1="-1" y1="4" x2="4" y2="1" stroke="{GRAY}" stroke-width="0.4"/>
  </g>
</g>''')

    # Client 1 旁边的 ④ 本地训练文字标注 (避开天线弧线)
    svg.append(f'''<text x="408" y="16" font-size="5.2" font-weight="bold" fill="{MUTED}" text-anchor="end">④ 本地训练 (ResNet-18)</text>''')

    # 客户端垂直省略号与 Client N
    svg.append(f'''<!-- 客户端省略号与 Client N -->
<g id="clients-more">
  <circle cx="458" cy="169" r="1.1" fill="{GRAY}"/>
  <circle cx="458" cy="174" r="1.1" fill="{GRAY}"/>
  <circle cx="458" cy="179" r="1.1" fill="{GRAY}"/>
  
  <!-- Client N 简练外框 -->
  <rect x="444" y="186" width="28" height="15" rx="1.5" fill="{LIGHT_GRAY}" stroke="{GRAY}" stroke-width="0.7" stroke-dasharray="2,1"/>
  <text x="458" y="196" font-size="5.5" font-weight="bold" fill="{MUTED}" text-anchor="middle">Client ··· N</text>
  <line x1="468" y1="186" x2="474" y2="180" stroke="{GRAY}" stroke-width="0.7"/>
  <circle cx="474" cy="180" r="1" fill="{GRAY}"/>
</g>''')

    # =========================================================================
    # 4. 中间上部：下行广播电磁波与短弯曲接入弧 (Step ② & ③)
    # =========================================================================
    # 服务端天线发射端向上微仰弯曲短弧 -> 激发无线电波
    svg.append(f'''<!-- 下行广播发射弧 -->
<g id="downlink-transmit-arc">
  <path d="M 137 105 C 137 72 165 42 190 38" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arr-blue)"/>
</g>''')

    # 无线电磁辐射波同心圆弧 (在 x: 195 ~ 330 扇区展开)
    svg.append(f'''<!-- 无线辐射波浪 -->
<g id="rf-waves">
  <!-- 弧浪 1 -->
  <path d="M 215 16 A 55 55 0 0 1 228 75" fill="none" stroke="{BLUE}" stroke-width="0.9" stroke-opacity="0.4"/>
  <!-- 弧浪 2 -->
  <path d="M 245 8 A 85 85 0 0 1 268 95" fill="none" stroke="{BLUE}" stroke-width="1.1" stroke-opacity="0.6"/>
  <!-- 弧浪 3 (波前外缘) -->
  <path d="M 285 2 A 125 125 0 0 1 315 115" fill="none" stroke="{BLUE}" stroke-width="1.3" stroke-opacity="0.8"/>
  <!-- 弧浪 4 -->
  <path d="M 320 0 A 160 160 0 0 1 348 130" fill="none" stroke="{BLUE}" stroke-width="0.9" stroke-opacity="0.3" stroke-dasharray="4,2"/>
</g>''')

    # 步骤 ② 下行广播紧凑标牌 (嵌在电磁波上方)
    svg.append(f'''<!-- 步骤 ② 标牌 -->
<g id="step-2-label">
  <rect x="200" y="12" width="138" height="15" rx="2" fill="{WHITE}" stroke="{BLUE}" stroke-width="0.7"/>
  <circle cx="208" cy="19.5" r="3.8" fill="{NAVY}"/>
  <text x="208" y="21.7" font-size="5.2" font-weight="bold" fill="{WHITE}" text-anchor="middle">2</text>
  <text x="274" y="22" font-size="5.8" font-weight="bold" fill="{BLUE}" text-anchor="middle">② 模型下行广播 (UFTP / 40 MiB)</text>
</g>''')

    # 从电磁波前沿平滑弯曲分流至各 Client 天线顶部的【优美平滑短弧】
    # Client 1 天线球位置: (475, 17)
    # Client 2 天线球位置: (475, 69)
    # Client 3 天线球位置: (475, 121)
    svg.append(f'''<!-- 接入各客户端的平滑短弧 -->
<g id="downlink-arrival-arcs">
  <!-- 接入 Client 1 天线 (475, 17) -->
  <path d="M 330 36 C 370 26 420 18 471 17" fill="none" stroke="{BLUE}" stroke-width="1.1" marker-end="url(#arr-blue)"/>
  
  <!-- 接入 Client 2 天线 (475, 69) -->
  <path d="M 325 58 C 370 60 420 68 471 69" fill="none" stroke="{BLUE}" stroke-width="1.1" marker-end="url(#arr-blue)"/>

  <!-- 接入 Client 3 天线 (475, 121) -->
  <path d="M 310 88 C 355 102 415 118 471 121" fill="none" stroke="{BLUE}" stroke-width="1.1" marker-end="url(#arr-blue)"/>
</g>''')

    # 步骤 ③：本地校验解耦即刻启动 (短弯小指针指向 Client 1)
    svg.append(f'''<!-- 步骤 ③ 解耦指示 -->
<g id="step-3-decouple">
  <rect x="315" y="52" width="90" height="15" rx="2" fill="{LIGHT_BLUE}" stroke="{BLUE}" stroke-width="0.6"/>
  <circle cx="323" cy="59.5" r="3.5" fill="{NAVY}"/>
  <text x="323" y="61.5" font-size="5.0" font-weight="bold" fill="{WHITE}" text-anchor="middle">3</text>
  <text x="366" y="61.7" font-size="5.2" font-weight="bold" fill="{BLUE}" text-anchor="middle">③ 校验解耦即刻训练</text>
  <!-- 指向 Client 1 短弯弧 -->
  <path d="M 405 59 C 420 59 430 46 441 38" fill="none" stroke="{BLUE}" stroke-width="0.7" marker-end="url(#arr-blue)"/>
</g>''')

    # =========================================================================
    # 5. 中间下部：上行轮转回传 (Step ⑤) 与反向 ACK 辅助流
    # =========================================================================
    # 各客户端汇出的平滑短弧
    svg.append(f'''<!-- 上行客户端汇出弧 -->
<g id="uplink-gather-arcs">
  <path d="M 444 38 C 428 42 415 65 405 115" fill="none" stroke="{BLUE}" stroke-width="0.8"/>
  <path d="M 444 92 C 428 96 415 110 405 125" fill="none" stroke="{BLUE}" stroke-width="0.8"/>
  <path d="M 444 144 C 428 148 418 152 405 156" fill="none" stroke="{BLUE}" stroke-width="0.8"/>
</g>''')

    # 主上行流：优美起伏的平滑三次贝塞尔波浪曲线 (S-curve spline)
    # 从 (405, 156) 平滑弯曲向左返回至服务端机架右侧 (158, 148)
    svg.append(f'''<!-- 主上行平滑波浪曲线 -->
<g id="step-5-uplink">
  <path d="M 405 156 C 335 168 250 140 160 148" fill="none" stroke="{BLUE}" stroke-width="1.3" marker-end="url(#arr-blue)"/>
  
  <!-- 步骤 ⑤ 紧凑标牌 -->
  <rect x="230" y="138" width="138" height="15" rx="2" fill="{WHITE}" stroke="{BLUE}" stroke-width="0.7"/>
  <circle cx="238" cy="145.5" r="3.8" fill="{NAVY}"/>
  <text x="238" y="147.7" font-size="5.2" font-weight="bold" fill="{WHITE}" text-anchor="middle">5</text>
  <text x="302" y="148" font-size="5.8" font-weight="bold" fill="{BLUE}" text-anchor="middle">⑤ 轮转上行 (HTTP PUT / TCP)</text>

  <!-- 反向 TCP ACK 辅助流平滑微弯虚线 -->
  <path d="M 160 158 C 240 152 320 180 395 170" fill="none" stroke="{GRAY}" stroke-width="0.7" stroke-dasharray="2.5,1.5" marker-end="url(#arr-gray)"/>
  <text x="278" y="172" font-size="5.0" fill="{GRAY}" text-anchor="middle">服务端反向 TCP ACK (维持流控)</text>
</g>''')

    svg.append('</svg>')
    return '\n'.join(svg)

def main():
    svg_content = generate_figure1_svg()
    
    # 保存正式产物到 prototypes/figure1/
    svg_path = HERE / 'fig.svg'
    svg_path.write_text(svg_content, encoding='utf-8')
    print(f"Written: {svg_path} ({len(svg_content)} bytes)")

    final_svg_path = HERE / 'final.svg'
    final_svg_path.write_text(svg_content, encoding='utf-8')
    print(f"Written: {final_svg_path}")

    # 同时保存一份到 autofigure_out 方便集中归档
    auto_out_svg = HERE / 'autofigure_out' / 'final.svg'
    auto_out_svg.write_text(svg_content, encoding='utf-8')
    print(f"Written: {auto_out_svg}")

if __name__ == '__main__':
    main()
