"""校订 AutoFigure 输出的文字、字体和图符，并生成一致的交付文件。"""
from pathlib import Path
from lxml import etree as ET
import cairosvg
from PIL import Image
import json

base = Path(__file__).resolve().parent
ns = 'http://www.w3.org/2000/svg'
root = ET.parse(str(base / 'generated/template.svg')).getroot()

def element(tag, **attributes):
    return ET.Element(f'{{{ns}}}{tag}', {k.replace('_', '-'): str(v) for k, v in attributes.items()})

def add(parent, tag, **attrs):
    child = element(tag, **attrs)
    parent.append(child)
    return child

style = root.find(f'.//{{{ns}}}style')
style.text = '''
text { font-family: "Noto Sans CJK SC", sans-serif; fill: #1A365D; }
.title-text { font-size: 26px; font-weight: 500; }
.label-text, .status-text, .annot-text, .tick-label { font-size: 24px; font-weight: 400; }
.axis-label { font-size: 25px; font-weight: 400; }
'''

replacements = {
    '可靠 TUN 虚拟接口': 'TUN 虚拟接口',
    '出端传输队列': '出站发送队列',
    '可靠无线传输': '无线发送',
    '队列水位高, 暂停TUN读读入': '达到高水位：暂停读入',
    '队列水位低, 灰复TUN读读入': '降至低水位：恢复读入',
    '队列积压 (字节)': '队列积压（字节）',
    'TUN读入状态': 'TUN 读入状态',
}
for text in root.iter(f'{{{ns}}}text'):
    if text.text in replacements:
        text.text = replacements[text.text]
    if text.get('class') != 'placeholder-text':
        text.attrib.pop('font-size', None)
    if text.text == 'A':
        text.set('style', 'fill: #E67E22; font-size: 28px; font-weight: 500')
    if text.text == 'B':
        text.set('style', 'fill: #159D89; font-size: 28px; font-weight: 500')
    if text.text == '时间':
        text.set('x', '1394')
        text.set('y', '640')
    if text.text == 'TUN 读入状态':
        text.set('x', '112')
    if text.text == '机制示意；高、低水位为固定预设阈值':
        text.set('style', 'font-size: 21px; fill: #52657A')

for group in list(root.iter(f'{{{ns}}}g')):
    ident = group.get('id')
    if not ident or not ident.startswith('AF'):
        continue
    if ident in ('AF01', 'AF02'):
        root.remove(group)
        continue
    rect = group.find(f'{{{ns}}}rect')
    x, y, w, h = (float(rect.get(k)) for k in ('x', 'y', 'width', 'height'))
    group.clear()
    group.set('id', 'tun-interface' if ident == 'AF03' else 'queue-payload-' + ident[-2:])
    if ident == 'AF03':
        add(group, 'rect', x=x, y=y, width=w, height=h, rx=7,
            fill='#EDF3FA', stroke='#1A365D', stroke_width=2.5)
        add(group, 'line', x1=x, y1=y+23, x2=x+w, y2=y+23,
            stroke='#1A365D', stroke_width=2)
        for dot in range(3):
            add(group, 'circle', cx=x+13+dot*12, cy=y+12, r=3, fill='#718096')
        # 网络接口图符：一条主干连接三个端点。
        add(group, 'path', d=f'M {x+23} {y+43} H {x+71} M {x+47} {y+43} V {y+66} M {x+23} {y+43} V {y+66} M {x+71} {y+43} V {y+66}',
            fill='none', stroke='#2B6CB0', stroke_width=2.5)
        for offset in (23, 47, 71):
            add(group, 'rect', x=x+offset-5, y=y+64, width=10, height=10,
                fill='white', stroke='#2B6CB0', stroke_width=2)
    else:
        add(group, 'rect', x=x+3, y=y+5, width=w-6, height=h-10, rx=3,
            fill='#C8DBEE', stroke='#1A365D', stroke_width=2)
        for offset in (23, 34, 45):
            add(group, 'line', x1=x+11, y1=y+offset, x2=x+w-11, y2=y+offset,
                stroke='#2B6CB0', stroke_width=2)

wireless = add(root, 'g', id='wireless-transmitter', fill='none', stroke='#1A365D', stroke_width=3)
add(wireless, 'circle', cx=1280, cy=139, r=8, fill='#C8DBEE')
add(wireless, 'path', d='M 1274 151 L 1260 197 H 1300 L 1286 151 Z', fill='#C8DBEE')
add(wireless, 'path', d='M 1260 121 Q 1240 139 1260 157 M 1250 110 Q 1218 139 1250 168 M 1300 121 Q 1320 139 1300 157 M 1310 110 Q 1342 139 1310 168', stroke_linecap='round')

# 反馈起点落在队列内部，避免误画成无线发送端的反馈。
for path in root.iter(f'{{{ns}}}path'):
    if path.get('d') == 'M 1120,155 L 1120,290 L 435,290 L 435,225':
        path.set('d', 'M 1008,201 L 1008,290 L 435,290 L 435,225')
    if path.get('d') == 'M 1120,250 L 478,250 L 478,225':
        path.set('d', 'M 1008,250 L 478,250 L 478,225')
# 背景带只覆盖 H-L 区间。
for rect in root.iter(f'{{{ns}}}rect'):
    if rect.get('fill') == '#E9EFF5':
        rect.set('height', '115')
# 补足 B 触发点到状态条的对齐线。
line = element('line', x1=1048, y1=537, x2=1048, y2=652,
               stroke='#7B8D9E', stroke_width=2, stroke_dasharray='6,4')
# 从低水位至底部的引导线，放在标签与曲线下层。
root.insert(2, line)

# 补充机器可读的图意，方便编辑器与辅助阅读。
title = element('title', id='figure-title')
title.text = '出站队列水位与 TUN 反压机制'
description = element('desc', id='figure-description')
description.text = '队列积压达到固定高水位 H 时暂停 TUN 读入，已有数据仍可发送；降至固定低水位 L 后恢复读入。两个阈值之间保持此前读入状态。曲线为机制示意，无实测数据。'
root.insert(0, description)
root.insert(0, title)
root.set('role', 'img')
root.set('aria-labelledby', 'figure-title figure-description')

svg = base / 'final.svg'
ET.ElementTree(root).write(str(svg), encoding='utf-8', xml_declaration=True, pretty_print=True)
cairosvg.svg2png(url=str(svg), write_to=str(base / 'preview.png'))
cairosvg.svg2png(url=str(svg), write_to=str(base / 'figure.png'), output_width=2912, output_height=1440)
cairosvg.svg2pdf(url=str(svg), write_to=str(base / 'figure.pdf'))
for name in ('preview.png', 'figure.png'):
    with Image.open(base / name) as img:
        img.verify()
parsed = ET.parse(str(svg))
assert not parsed.findall('.//{http://www.w3.org/2000/svg}image')
assert not parsed.findall('.//{http://www.w3.org/2000/svg}g[@id="AF03"]')
report = {
    'svg_parse': '通过',
    'png_decode': '通过',
    'generated_pipeline_removed_background_icons': 7,
    'final_svg_embedded_raster_images': 0,
    'finalization': '在 AutoFigure 重建模板上修正标签、中文字体、反馈起点和 H-L 背景带；图符替换为可编辑矢量元素。',
    'font': 'Noto Sans CJK SC',
    'viewBox': root.get('viewBox'),
    'png_dimensions': [2912, 1440],
    'artifacts': ['final.svg', 'figure.png', 'preview.png', 'figure.pdf'],
}
(base / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
