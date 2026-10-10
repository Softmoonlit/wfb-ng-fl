"""保留 AutoFigure 原图，仅清理非规格要求的状态符号与悬空连线。"""
from pathlib import Path
from PIL import Image, ImageDraw

run = Path(__file__).resolve().parent
image = Image.open(run / 'figure.png').convert('RGB')
assert image.size == (1456, 720)
# 去掉三个更新文件上的对勾/叉号，逐行复制同一文件的空白底色。
for left, top, right, bottom, sample_x in [
    (765, 575, 790, 596, 750),
    (818, 575, 845, 596, 805),
    (874, 575, 897, 596, 857),
]:
    for y in range(top, bottom):
        color = image.getpixel((sample_x, y))
        for x in range(left, right):
            image.putpixel((x, y), color)
# 聚合已经有一条清楚的返回服务端箭头，删除重复的无箭头支线。
ImageDraw.Draw(image).rectangle((385, 440, 475, 570), fill='white')
output = run.parent.parent / 'figure.png'
image.save(output)
with Image.open(output) as check:
    check.verify()
print(f'已验证交付 PNG：{output}；尺寸 {image.width} × {image.height}')
