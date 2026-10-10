"""对 AutoFigure PNG 做局部清理，保留生成原图作为来源。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps

root = Path(__file__).resolve().parent
source = root / 'figure-generated.png'
if not source.exists():
    source.write_bytes((root / 'figure.png').read_bytes())
image = Image.open(source).convert('RGB')
scale = 3
canvas = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
draw = ImageDraw.Draw(canvas)

def box(bounds, fill='white'):
    draw.rectangle(tuple(round(v * scale) for v in bounds), fill=fill)

def line(points, fill, width):
    draw.line([(round(x * scale), round(y * scale)) for x, y in points], fill=fill, width=round(width * scale))

border = image.getpixel((600, 70))
loop = image.getpixel((462, 480))

# 去掉错误的“模型发布 → 更新收齐”内部箭头。
box((581, 327, 616, 395))

# 清除服务端上方的人物，并恢复被遮挡的容器边框。
box((664, 12, 770, 142))
# 模型模块被人物遮挡的右上部分，利用模块左侧的对称边缘恢复。
patch = ImageOps.mirror(image.crop((512, 117, 535, 143)))
canvas.paste(patch.resize((patch.width * scale, patch.height * scale), Image.Resampling.NEAREST), (664 * scale, 117 * scale))
# 精确重画服务端外边框的局部，避免叠加其他完好边框。
line([(664, 70), (726, 70)], border, 4)
draw.arc((708 * scale, 70 * scale, 744 * scale, 106 * scale), 270, 360, fill=border, width=4 * scale)
line([(744, 88), (744, 142)], border, 4)

# 清除聚合回路上的人物和多余箭头，恢复左边框及回路直线。
box((400, 298, 510, 442))
line([(431, 298), (431, 442)], border, 4)
line([(462, 298), (462, 442)], loop, 6)

# 清除三个客户端上方的人物，分别恢复容器顶边。
# 框内接收、训练、提交模块和全部通信箭头均保留。
box((1340, 35, 1438, 126))
line([(1340, 70), (1425, 70)], border, 4)
draw.arc((1407 * scale, 70 * scale, 1443 * scale, 106 * scale), 270, 360, fill=border, width=4 * scale)
line([(1443, 88), (1443, 126)], border, 4)

box((1340, 257, 1435, 331))
line([(1340, 271), (1425, 271)], border, 4)
draw.arc((1407 * scale, 271 * scale, 1443 * scale, 307 * scale), 270, 360, fill=border, width=4 * scale)
line([(1443, 289), (1443, 331)], border, 4)

box((1338, 477, 1437, 575))
line([(1338, 513), (1425, 513)], border, 4)
draw.arc((1407 * scale, 513 * scale, 1443 * scale, 549 * scale), 270, 360, fill=border, width=4 * scale)
line([(1443, 531), (1443, 575)], border, 4)

# 输出与原图相同的分辨率，抗锯齿只作用于局部修补边缘。
result = canvas.resize(image.size, Image.Resampling.LANCZOS)
result.save(root / 'figure.png')
with Image.open(root / 'figure.png') as check:
    check.verify()
print(f'PNG 已校验：{root / "figure.png"}，尺寸 {result.width} × {result.height}')
