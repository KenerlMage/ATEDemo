# -*- coding: utf-8 -*-
"""生成拓扑节点图标（线稿，深蓝），供 PPT 节点图片使用"""
import os
from PIL import Image, ImageDraw

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ni_img", "icons")
os.makedirs(D, exist_ok=True)
W, H = 300, 188
C = (31, 63, 102)
LW = 6


def canvas():
    im = Image.new("RGB", (W, H), (255, 255, 255))
    return im, ImageDraw.Draw(im)


def save(im, name):
    im.save(os.path.join(D, name), "PNG", optimize=True)


# 1 工厂 / MES
im, d = canvas()
d.rectangle([40, 96, 260, 160], outline=C, width=LW)
d.line([40, 96, 76, 62, 112, 96], fill=C, width=LW, joint="curve")
d.line([112, 96, 148, 62, 184, 96], fill=C, width=LW, joint="curve")
d.line([184, 96, 220, 62, 256, 96], fill=C, width=LW, joint="curve")
d.rectangle([210, 40, 232, 96], outline=C, width=LW)
d.rectangle([72, 120, 104, 160], outline=C, width=LW)
d.rectangle([128, 120, 160, 160], outline=C, width=LW)
d.rectangle([184, 120, 216, 160], outline=C, width=LW)
save(im, "mes.png")

# 2 程序库（层叠）
im, d = canvas()
for i, (x, y) in enumerate([(52, 46), (66, 82), (80, 118)]):
    d.rectangle([x, y, x + 168, y + 40], outline=C, width=LW)
for k in range(3):
    d.line([100 + k * 32, 128, 100 + k * 32, 146], fill=C, width=LW)
save(im, "lib.png")

# 3 结果库（圆柱）
im, d = canvas()
d.ellipse([70, 34, 230, 76], outline=C, width=LW)
d.line([70, 55, 70, 140], fill=C, width=LW)
d.line([230, 55, 230, 140], fill=C, width=LW)
d.arc([70, 118, 230, 162], start=0, end=180, fill=C, width=LW)
d.arc([70, 78, 230, 118], start=0, end=180, fill=C, width=LW)
save(im, "db.png")

# 4 分析平台（柱状 + 折线）
im, d = canvas()
d.line([50, 32, 50, 154], fill=C, width=LW)
d.line([50, 154, 262, 154], fill=C, width=LW)
for i, h in enumerate([34, 58, 44, 78]):
    x = 74 + i * 46
    d.rectangle([x, 154 - h, x + 30, 150], outline=C, width=LW)
d.line([74, 96, 120, 74, 166, 88, 212, 52], fill=C, width=LW, joint="curve")
save(im, "chart.png")

# 5 交换机
im, d = canvas()
d.rectangle([44, 56, 256, 132], outline=C, width=LW)
for i in range(4):
    x = 66 + i * 30
    d.rectangle([x, 96, x + 20, 116], outline=C, width=LW)
d.line([64, 76, 120, 76], fill=C, width=LW)
d.polygon([(120, 70), (120, 82), (132, 76)], fill=C)
d.line([236, 76, 180, 76], fill=C, width=LW)
d.polygon([(180, 70), (180, 82), (168, 76)], fill=C)
save(im, "switch.png")

# 6 工控机（塔式）
im, d = canvas()
d.rectangle([78, 28, 222, 158], outline=C, width=LW)
for i in range(3):
    d.line([96, 54 + i * 18, 150, 54 + i * 18], fill=C, width=LW)
d.ellipse([190, 46, 206, 62], outline=C, width=5)
d.rectangle([104, 116, 196, 140], outline=C, width=5)
save(im, "pc.png")

# 7 板卡 / 驱动
im, d = canvas()
d.rectangle([40, 48, 236, 132], outline=C, width=LW)
for i in range(9):
    x = 46 + i * 20
    d.line([x, 132, x, 150], fill=C, width=5)
d.rectangle([60, 66, 108, 100], outline=C, width=5)
d.rectangle([150, 66, 214, 100], outline=C, width=5)
d.line([232, 40, 232, 140], fill=C, width=LW)
save(im, "driver.png")

# 8 开关矩阵（交叉点）
im, d = canvas()
for i in range(4):
    y = 52 + i * 30
    d.line([46, y, 254, y], fill=C, width=4)
for i in range(4):
    x = 68 + i * 46
    d.line([x, 34, x, 152], fill=C, width=4)
for i in range(4):
    for j in range(4):
        if (i + j) % 2 == 0:
            cx, cy = 68 + j * 46, 52 + i * 30
            d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=C)
save(im, "crossbar.png")

# 9 台式仪器
im, d = canvas()
d.rectangle([40, 44, 260, 148], outline=C, width=LW)
d.rectangle([58, 62, 152, 108], outline=C, width=5)
d.line([66, 96, 88, 76, 108, 92, 128, 70, 144, 84], fill=C, width=4, joint="curve")
d.ellipse([176, 60, 210, 94], outline=C, width=5)
d.ellipse([220, 60, 246, 86], outline=C, width=5)
d.line([176, 112, 246, 112], fill=C, width=5)
d.ellipse([62, 122, 76, 136], fill=C)
d.ellipse([86, 122, 100, 136], fill=C)
save(im, "instr.png")

# 10 LabVIEW 前面板
im, d = canvas()
d.rectangle([44, 36, 256, 152], outline=C, width=LW)
d.line([44, 60, 256, 60], fill=C, width=5)
d.ellipse([56, 42, 68, 54], fill=C)
d.ellipse([74, 42, 86, 54], fill=C)
d.line([62, 126, 92, 98, 116, 116, 146, 78, 176, 100, 208, 74, 240, 92],
       fill=C, width=5, joint="curve")
save(im, "vi.png")

# 11 夹具针床
im, d = canvas()
d.rectangle([40, 36, 260, 74], outline=C, width=LW)
for i in range(6):
    x = 58 + i * 36
    d.line([x, 74, x, 116], fill=C, width=5)
    d.ellipse([x - 6, 116, x + 6, 128], outline=C, width=4)
d.rectangle([40, 140, 260, 164], outline=C, width=LW)
save(im, "fixture.png")

# 12 被测件（芯片）
im, d = canvas()
d.rectangle([86, 56, 214, 132], outline=C, width=LW)
for i in range(5):
    y = 66 + i * 15
    d.line([64, y, 86, y], fill=C, width=4)
    d.line([214, y, 236, y], fill=C, width=4)
for i in range(3):
    x = 104 + i * 34
    d.line([x, 34, x, 56], fill=C, width=4)
    d.line([x, 132, x, 154], fill=C, width=4)
d.ellipse([140, 84, 160, 104], outline=C, width=4)
save(im, "dut.png")

print("icons:", len(os.listdir(D)))
for f in sorted(os.listdir(D)):
    print("  ", f, os.path.getsize(os.path.join(D, f)) // 1024, "K")
