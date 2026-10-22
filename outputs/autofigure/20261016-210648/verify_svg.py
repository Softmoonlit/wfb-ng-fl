"""检查 Figure 4 SVG 的可编辑元素、关键文字与渲染产物。"""
from pathlib import Path
from collections import Counter
import json
import re
from lxml import etree
from PIL import Image

root = Path(__file__).resolve().parent
svg_path = root / 'svg' / 'final.svg'
svg = etree.parse(str(svg_path))
elements = Counter(etree.QName(element).localname for element in svg.iter() if isinstance(element.tag, str))
texts = [''.join(element.itertext()).strip() for element in svg.iter() if isinstance(element.tag, str) and etree.QName(element).localname == 'text']
all_text = ' '.join(texts)
expected_values = ['11.2', '22.4', '68.5', '0.6', '2.2', '6.8', '30.0', '15.0', '4.9', '39.6', '76.2', '104.5', '1.8', '96.5', '52.8', '3.5', '4.8']
missing = [value for value in expected_values if not re.search(r'(?<![\d.])' + re.escape(value) + r'(?![\d.])', all_text)]
image_rows = []
for element in svg.iter():
    if isinstance(element.tag, str) and etree.QName(element).localname == 'image':
        href = element.get('href') or element.get('{http://www.w3.org/1999/xlink}href') or ''
        image_rows.append({'width': element.get('width'), 'height': element.get('height'), 'embedded': href.startswith('data:'), 'href_prefix': href[:30]})
with Image.open(root / 'svg' / 'preview.png') as image:
    dimensions = image.size
    image.verify()
report = {
    'SVG': str(svg_path),
    '根元素': etree.QName(svg.getroot()).localname,
    '元素数量': dict(elements),
    '缺失关键数值': missing,
    '图片元素': image_rows,
    '预览尺寸': dimensions,
    '文字': texts,
}
(root / 'svg-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({k:v for k,v in report.items() if k != '文字'}, ensure_ascii=False, indent=2))
if not elements['text'] or missing or elements['image']:
    raise SystemExit('需要进一步检查可编辑性、数值或图片元素。')
