"""为生成的 SVG 指定可用中文字体，并验证最终预览。"""
from pathlib import Path
from lxml import etree
from PIL import Image
import cairosvg

root = Path(__file__).resolve().parent
svg = root / 'final.svg'
original = root / 'final-generated.svg'
if not original.exists():
    original.write_bytes(svg.read_bytes())
doc = etree.parse(str(original))
ns = {'svg': 'http://www.w3.org/2000/svg'}
texts = doc.xpath('//svg:text', namespaces=ns)
for element in texts:
    element.set('font-family', "'Noto Sans CJK SC', 'Microsoft YaHei', 'PingFang SC', sans-serif")
doc.write(str(svg), encoding='utf-8', xml_declaration=True)
cairosvg.svg2png(url=str(svg), write_to=str(root / 'preview.png'))
with Image.open(root / 'preview.png') as image:
    image.verify()
print(f'已设置 {len(texts)} 个文字对象的中文字体，SVG 与预览校验通过。')
