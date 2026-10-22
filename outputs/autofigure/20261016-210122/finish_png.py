"""修正 AutoFigure 第一版 PNG 的局部配置配色，并生成高分辨率排版预览。"""
from pathlib import Path
import colorsys
from PIL import Image

root = Path(__file__).resolve().parent
source = root / 'png' / 'figure.png'
with Image.open(source) as image:
    image.load()
    image = image.convert('RGB')

# 只修正 (d) 第三行成功区段。原始 AutoFigure PNG 保留，便于追溯。
# 图中原始尺寸为 1408×768，第三行成功区间为 x=876..1272, y=600..652。
pixels = image.load()
changed = 0
orange_hue, _, _ = colorsys.rgb_to_hsv(216 / 255, 137 / 255, 54 / 255)
for y in range(600, 653):
    for x in range(876, 1273):
        r, g, b = pixels[x, y]
        hue, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if 0.52 < hue < 0.64 and saturation > 0.12:
            rgb = colorsys.hsv_to_rgb(orange_hue, saturation, value)
            pixels[x, y] = tuple(round(channel * 255) for channel in rgb)
            changed += 1
if not changed:
    raise RuntimeError('未找到预期的蓝色像素，请重新检查原图坐标。')
image.save(root / 'figure-native.png')
width = 3000
height = round(image.height * width / image.width)
image.resize((width, height), Image.Resampling.LANCZOS).save(root / 'figure.png', dpi=(300, 300))
with Image.open(root / 'figure.png') as output:
    output.verify()
print(f'已修正 {changed} 个像素；最终 PNG：{width}×{height}。原始生成图保留于 {source}')
