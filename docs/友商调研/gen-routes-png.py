# -*- coding: utf-8 -*-
"""三条路线对照图（2400×1350）——含文本宽度自检"""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 2400, 1350
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "三条路线对照.png")

BG = (251, 251, 249)
INK = (24, 24, 26)
MUT = (110, 108, 104)
RED = (139, 26, 26)
BLUE = (15, 76, 129)
GREEN = (47, 107, 87)
WHITE = (255, 255, 255)

_c: dict = {}


def fnt(size, bold=False):
    k = (size, bold)
    if k not in _c:
        p = r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"
        _c[k] = ImageFont.truetype(p, size)
    return _c[k]


img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)
issues = []


def text(x, y, t, size, color, bold=False, limit=None, tag=""):
    f = fnt(size, bold)
    w = d.textlength(t, font=f)
    if limit and w > limit:
        issues.append(f"溢出 {tag}：{w:.0f} > {limit:.0f} 「{t[:18]}」")
    d.text((x, y), t, font=f, fill=color)
    return w


def card(x, y, w, h, accent):
    d.rounded_rectangle([x, y, x + w, y + h], radius=18, fill=WHITE,
                        outline=(214, 212, 206), width=2)
    d.rounded_rectangle([x, y, x + w, y + 96], radius=18, fill=accent)
    d.rectangle([x, y + 78, x + w, y + 96], fill=accent)


def row(x, y, w, h, tag, label, desc, accent):
    d.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=(248, 248, 246),
                        outline=(226, 224, 218), width=2)
    d.rounded_rectangle([x, y, x + 8, y + h], radius=4, fill=accent)
    text(x + 28, y + 10, tag, 20, accent, True, w - 44, f"row-tag/{tag}")
    text(x + 28, y + 38, label, 26, INK, True, w - 44, f"row-label/{label}")
    text(x + 28, y + 74, desc, 20, MUT, False, w - 44, f"row-desc/{desc}")


text(90, 52, "业界 ATE 实现的三条路线", 54, INK, True, 2200, "主标题")
text(92, 128, "专用机、模块化平台、设备内建量测 —— 三条路线解决的不是同一个问题", 26, MUT,
     False, 2200, "副标题")

cols = [
    dict(accent=RED, tag="路线 A", title="专用 per-pin ATE", note="Advantest · Teradyne",
         rows=[("硬件", "专用机柜 + 通道板", "每引脚独立时序与电平"),
               ("软件", "专有测试语言 + 编译器", "向量与时序编译成通道微码"),
               ("水平", "ps 级偏斜、Gbps 级速率", "高引脚数 SoC 的量产主力"),
               ("扩展", "加通道板，受机柜限制", "换型需重新编译与验证")],
         pros="强项：引脚密度、时序精度、量产节拍",
         cons="弱项：封闭生态、单价高、改型慢"),
    dict(accent=BLUE, tag="路线 B", title="模块化平台（NI）", note="STS T1 / T2 / T4",
         rows=[("硬件", "PXI 机箱 + 模块仪器", "18 槽机箱，1/2/4 台可选"),
               ("软件", "执行器 + 驱动 + 配置", "TestStand + TSM：site 与 pin map"),
               ("水平", "32 通道 / 100 MVector/s", "边沿 39.0625 ps，256 通道同步"),
               ("扩展", "加模块、加机箱", "同一套软件覆盖 T1 到 T4")],
         pros="强项：开放、可裁剪、代码可复用",
         cons="弱项：单通道性能不及专用机"),
    dict(accent=GREEN, tag="路线 C", title="设备内建量测（ASML）", note="TWINSCAN · YieldStar · HMI",
         rows=[("硬件", "设备本体 + 内建传感器", "对准、调平、量测机台"),
               ("软件", "控制软件 + 计算模型", "先建模预测，再用测量校正"),
               ("水平", "overlay / focus 纳米级", "9 束电子束做单 die 缺陷检测"),
               ("扩展", "加传感器、改模型", "投入大，且需整机标定")],
         pros="强项：精度可追溯、参数持续收敛",
         cons="弱项：投入规模大、只适用可测物理量"),
]

CW = 700
for i, c in enumerate(cols):
    x = 90 + i * (CW + 60)
    card(x, 196, CW, 856, c["accent"])
    text(x + 30, 206, c["tag"], 20, (255, 255, 255), True, 120, f"card-tag/{i}")
    text(x + 30, 230, c["title"], 31, (255, 255, 255), True, CW - 60, f"card-title/{i}")
    text(x + 30, 272, c["note"], 19, (232, 232, 232), False, CW - 60, f"card-note/{i}")
    for j, (tag, label, desc) in enumerate(c["rows"]):
        row(x + 26, 314 + j * 118, CW - 52, 104, tag, label, desc, c["accent"])
    d.rounded_rectangle([x + 26, 800, x + CW - 26, 862], radius=10, fill=(246, 247, 245),
                        outline=(206, 224, 214), width=2)
    text(x + 42, 818, c["pros"], 21, c["accent"], True, CW - 92, f"pros/{i}")
    d.rounded_rectangle([x + 26, 876, x + CW - 26, 938], radius=10, fill=(250, 248, 246),
                        outline=(228, 214, 210), width=2)
    text(x + 42, 894, c["cons"], 21, (120, 92, 88), True, CW - 92, f"cons/{i}")

# 底部：我们的位置
d.rounded_rectangle([90, 1086, W - 90, 1272], radius=18, fill=(247, 244, 240),
                    outline=(224, 218, 208), width=2)
text(120, 1106, "我们的位置", 28, INK, True, 300, "位置标题")
text(120, 1148, "既不在 A（重资产专用机），也不是 C（自研量测硬件）", 23, MUT, False, 2100, "位置1")
text(120, 1186, "走的是 B 的轻量形态 —— 用现有测试台里的仪器，配自己的执行器与适配层", 23, MUT,
     False, 2100, "位置2")

chips = [("借 NI：分层与 site 抽象", BLUE), ("借 ASML：闭环与偏差归因", GREEN), ("不学 A：性能竞赛", RED)]
cx = 120
for t, col in chips:
    w = d.textlength(t, font=fnt(22, True)) + 44
    d.rounded_rectangle([cx, 1226, cx + w, 1260], radius=17, fill=WHITE, outline=col, width=2)
    text(cx + 22, 1231, t, 22, col, True, w - 30, f"chip/{t}")
    cx += w + 18

text(90, 1296, "来源：NI 官方产品与文档、ASML 产品页、Advantest 产品页；引用清单见 docs/友商调研/README.md",
     19, (152, 150, 144), False, 2200, "页脚")

img.save(OUT)
print("saved", OUT, os.path.getsize(OUT), "B")
print("自检：", "无文本溢出" if not issues else "\n".join(issues))
