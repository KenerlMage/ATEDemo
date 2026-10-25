# -*- coding: utf-8 -*-
"""NI ATE 软件栈一览（聊天配图，2000×1125）"""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 2000, 1125
HERE = os.path.dirname(os.path.abspath(__file__))
BG = (251, 251, 249)
INK = (24, 24, 26)
MUT = (108, 106, 102)
RED = (139, 26, 26)
BLUE = (15, 76, 129)
GREEN = (47, 107, 87)
AMBER = (176, 118, 28)
PURPLE = (92, 58, 122)
TEAL = (32, 96, 106)
LINE = (216, 214, 208)
WHITE = (255, 255, 255)
SOFT = (247, 247, 245)

_c = {}


def f(s, b=False):
    k = (s, b)
    if k not in _c:
        p = r"C:\Windows\Fonts\msyhbd.ttc" if b else r"C:\Windows\Fonts\msyh.ttc"
        _c[k] = ImageFont.truetype(p, s)
    return _c[k]


def put(d, x, y, t, size, color, bold=False, limit=None, tag="", issues=None, align="l"):
    fnt = f(size, bold)
    for k, ln in enumerate(str(t).split("\n")):
        w = d.textlength(ln, font=fnt)
        if limit and w > limit and issues is not None:
            issues.append(f"{tag}:{w:.0f}>{limit:.0f}「{ln[:12]}」")
        xx = x - w / 2 if align == "c" else (x - w if align == "r" else x)
        d.text((xx, y + k * int(size * 1.5)), ln, font=fnt, fill=color)


def box(d, x, y, w, h, fill=WHITE, outline=LINE, width=2, r=10):
    d.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=fill,
                        outline=outline, width=width if outline else 0)


img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)
it = []

put(d, 70, 40, "NI 的 ATE 软件栈：五层分工，各管一件事", 42, INK, True, 1860, "标题", it)
put(d, 72, 104, "从机箱配置到结果归档，每一层都有专门的软件；ATE 的能力来自叠层，而不是某个万能工具", 23, MUT, False, 1860, "副标题", it)

layers = [
    ("L5 数据与运维", ["SystemLink", "DIAdem"], "机群与测试数据管理、离线分析与报告", PURPLE),
    ("L4 执行与调度", ["TestStand", "TSM（半导体模块）"], "序列引擎、流程模型、site/pin map、binning", RED),
    ("L3 测量与交互", ["LabVIEW", "InstrumentStudio"], "测量代码本体；仪器软面板与交互调试", BLUE),
    ("L2 路由与驱动", ["Switch Executive", "NI-VISA / NI-488.2", "NI-DAQmx"], "开关矩阵路径；会话层与仪器/传感器驱动", GREEN),
    ("L1 平台与硬件", ["PXI 机箱与模块", "PXI Platform Services", "NI MAX"], "机箱、模块、时钟与触发的发现、配置与自检", TEAL),
]

y = 152
for name, chips, role, col in layers:
    box(d, 70, y, 1180, 148, WHITE, LINE, 2, 12)
    box(d, 70, y, 8, 148, col, None, 0, 0)
    put(d, 96, y + 16, name, 23, col, True, 300, "层名", it)
    put(d, 96, y + 58, role, 17, MUT, False, 352, "层职责", it)
    cx = 478
    for c in chips:
        wch = d.textlength(c, font=f(18, True)) + 34
        box(d, cx, y + 46, wch, 52, (250, 249, 246), col, 2, 10)
        put(d, cx + 17, y + 56, c, 18, col, True, 400, "芯片", it)
        cx += wch + 14
    y += 162

# 右侧：一次执行链
put(d, 1300, 152, "一次多工位测试的交接顺序", 23, INK, True, 620, "链标题", it)
chain = [("① 配置", "NI MAX / PXI Services", TEAL),
         ("② 序列", "TestStand", RED),
         ("③ 并行", "TSM：site / pin map", RED),
         ("④ 测量", "LabVIEW VI", BLUE),
         ("⑤ 路由与驱动", "Switch Exec / VISA", GREEN),
         ("⑥ 归档", "SystemLink / DIAdem", PURPLE)]
cy = 196
for i, (a, b, col) in enumerate(chain):
    box(d, 1300, cy, 620, 88, WHITE, LINE, 2, 10)
    d.rectangle([1300, cy, 1308, cy + 88], fill=col)
    put(d, 1326, cy + 12, a, 20, col, True, 200, "链", it)
    put(d, 1326, cy + 46, b, 18, (58, 56, 52), False, 560, "链副", it)
    if i < len(chain) - 1:
        d.line([1610, cy + 88, 1610, cy + 96], fill=(176, 172, 164), width=3)
    cy += 98

box(d, 1300, 196 + 6 * 98 + 6, 620, 76, (252, 248, 242), AMBER, 2, 10)
put(d, 1318, 196 + 6 * 98 + 20, "关键：② 与 ③ 是「执行器」，④ 才是「测量」。\n把业务逻辑写进 ④ 会让并行与换型都变难。",
    16, (92, 72, 40), False, 590, "提示", it)

put(d, 70, 1060, "来源：NI 官网产品资料（TestStand / TSM / LabVIEW / Switch Executive / InstrumentStudio / SystemLink / DIAdem / NI-VISA / DAQmx / MAX）；层与链路划分为本文归纳",
    16, (150, 148, 142), False, 1880, "页脚", it)

out = os.path.join(HERE, "NI-ATE软件栈.png")
img.save(out)
print("PNG:", os.path.getsize(out), "B ｜自检：", it or "无溢出")
