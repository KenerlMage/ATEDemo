# -*- coding: utf-8 -*-
"""图标总览图：12 个节点图标 + 名称，用于正文配图"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "ni_img", "icons")
OUT = os.path.join(HERE, "NI-节点图标总览.png")

NAMES = [("mes.png", "MES 产线系统"), ("lib.png", "程序库"), ("db.png", "结果库"),
         ("chart.png", "分析平台"), ("switch.png", "以太网交换机"), ("pc.png", "工控机 Host"),
         ("vi.png", "测量程序 LabVIEW"), ("driver.png", "仪器驱动"), ("crossbar.png", "开关与路径"),
         ("instr.png", "外挂仪器"), ("fixture.png", "夹具针床"), ("dut.png", "被测件 DUT")]

CW, CH = 420, 300
COLS, ROWS = 4, 3
PAD = 26
W = COLS * CW + PAD * 2
H = ROWS * CH + PAD * 2 + 70

im = Image.new("RGB", (W, H), (255, 255, 255))
d = ImageDraw.Draw(im)

font_path = r"C:\Windows\Fonts\msyh.ttc"
try:
    f = ImageFont.truetype(font_path, 22)
    ft = ImageFont.truetype(font_path, 34)
except Exception:
    f = ft = ImageFont.load_default()

d.text((PAD, 18), "拓扑节点图标集（12 枚，线稿；PPT 中按 0.5 英寸宽嵌入）",
       fill=(26, 26, 26), font=ft)

for i, (fn, nm) in enumerate(NAMES):
    c = i % COLS
    r = i // COLS
    x0 = PAD + c * CW
    y0 = PAD + 70 + r * CH
    d.rectangle([x0 + 8, y0 + 8, x0 + CW - 8, y0 + CH - 8], outline=(214, 210, 202), width=2)
    ic = Image.open(os.path.join(ICON, fn)).convert("RGB")
    ic.thumbnail((240, 150))
    im.paste(ic, (x0 + (CW - ic.size[0]) // 2, y0 + 40))
    tw = d.textlength(nm, font=f)
    d.text((x0 + (CW - tw) / 2, y0 + CH - 78), nm, fill=(46, 46, 46), font=f)

im.save(OUT, "PNG", optimize=True)
print("总览图", im.size, os.path.getsize(OUT) // 1024, "K")
