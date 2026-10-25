# -*- coding: utf-8 -*-
"""友商调研 · 两页/三页原生 PPT（无位图）"""
import os
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt
from pptx.oxml.ns import qn

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "友商调研-两页.pptx")

SW, SH = 13.333, 7.5
INK = RGBColor(0x18, 0x18, 0x1A)
MUT = RGBColor(0x6E, 0x6C, 0x68)
RED = RGBColor(0x8B, 0x1A, 0x1A)
BLUE = RGBColor(0x0F, 0x4C, 0x81)
GREEN = RGBColor(0x2F, 0x6B, 0x57)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
CARD = RGBColor(0xFF, 0xFF, 0xFF)
LINE = RGBColor(0xD6, 0xD4, 0xCE)
SOFT = RGBColor(0xF8, 0xF8, 0xF6)
BAND = RGBColor(0xF7, 0xF4, 0xF0)

prs = Presentation()
prs.slide_width = Inches(SW)
prs.slide_height = Inches(SH)
BLANK = prs.slide_layouts[6]


def _ea(run, size_pt, bold, color):
    f = run.font
    f.size = Pt(size_pt)
    f.bold = bold
    f.color.rgb = color
    f.name = "微软雅黑"
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", "微软雅黑")


def txt(sl, x, y, w, h, text, size=11, bold=False, color=INK,
        align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.0):
    tb = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        r = p.add_run()
        r.text = ln
        _ea(r, size, bold, color)
    return tb


def box(sl, x, y, w, h, fill, line=None, radius=0.10, lw=1.1):
    shp = sl.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y),
                              Inches(w), Inches(h))
    shp.adjustments[0] = radius
    shp.shadow.inherit = False
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(lw)
    shp.text_frame.text = ""
    return shp


def arrow(sl, x, y, w, h, color, shape=MSO_SHAPE.RIGHT_ARROW):
    shp = sl.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.shadow.inherit = False
    shp.fill.solid()
    shp.fill.fore_color.rgb = color
    shp.line.fill.background()
    shp.text_frame.text = ""
    return shp


def header(sl, title, sub):
    box(sl, 0, 0, SW, 0.10, RED, None, 0.0)
    txt(sl, 0.55, 0.34, 11.2, 0.52, title, 25, True, INK)
    txt(sl, 0.57, 0.94, 11.6, 0.34, sub, 11.5, False, MUT)


def footer(sl, s):
    txt(sl, 0.55, 7.10, 11.8, 0.26, s, 8, False, RGBColor(0x96, 0x94, 0x8E))


# ───────────────────────────── P1 三条路线
s1 = prs.slides.add_slide(BLANK)
header(s1, "业界 ATE 实现的三条路线",
       "专用机、模块化平台、设备内建量测 —— 三条路线解决的不是同一个问题")

cols = [
    dict(c=RED, tag="路线 A", t="专用 per-pin ATE", n="Advantest · Teradyne",
         rows=[("硬件", "专用机柜 + 通道板", "每引脚独立时序与电平"),
               ("软件", "专有测试语言 + 编译器", "向量与时序编译成通道微码"),
               ("水平", "ps 级偏斜、Gbps 级速率", "高引脚数 SoC 的量产主力"),
               ("扩展", "加通道板，受机柜限制", "换型需重新编译与验证")],
         pro="强项：引脚密度、时序精度", con="弱项：封闭、单价高、改型慢"),
    dict(c=BLUE, tag="路线 B", t="模块化平台（NI）", n="STS T1 / T2 / T4",
         rows=[("硬件", "PXI 机箱 + 模块仪器", "18 槽机箱，1/2/4 台可选"),
               ("软件", "执行器 + 驱动 + 配置", "TestStand + TSM：site 与 pin map"),
               ("水平", "32 通道 / 100 MVector/s", "边沿 39.0625 ps，256 通道同步"),
               ("扩展", "加模块、加机箱", "同一套软件覆盖 T1 到 T4")],
         pro="强项：开放、可裁剪、可复用", con="弱项：单通道性能不及专用机"),
    dict(c=GREEN, tag="路线 C", t="设备内建量测（ASML）", n="TWINSCAN · YieldStar · HMI",
         rows=[("硬件", "设备本体 + 内建传感器", "对准、调平、量测机台"),
               ("软件", "控制软件 + 计算模型", "先建模预测，再用测量校正"),
               ("水平", "overlay / focus 纳米级", "9 束电子束做单 die 检测"),
               ("扩展", "加传感器、改模型", "投入大，且需整机标定")],
         pro="强项：精度可追溯、参数收敛", con="弱项：投入大、只适用可测物理量"),
]

CW, GAP = 3.96, 0.22
for i, c in enumerate(cols):
    x = 0.55 + i * (CW + GAP)
    box(s1, x, 1.44, CW, 4.60, CARD, LINE, 0.06, 1.0)
    box(s1, x, 1.44, CW, 0.82, c["c"], None, 0.06)
    box(s1, x, 1.90, CW, 0.36, c["c"], None, 0.0)
    txt(s1, x + 0.18, 1.52, 1.2, 0.24, c["tag"], 9, True, WHITE)
    txt(s1, x + 0.18, 1.74, CW - 0.36, 0.30, c["t"], 14.5, True, WHITE)
    txt(s1, x + 0.18, 2.00, CW - 0.36, 0.22, c["n"], 8.5, False, RGBColor(0xE8, 0xE8, 0xE8))
    for j, (tag, label, desc) in enumerate(c["rows"]):
        ry = 2.40 + j * 0.72
        box(s1, x + 0.16, ry, CW - 0.32, 0.64, SOFT, RGBColor(0xE2, 0xE0, 0xDA), 0.10, 1.0)
        box(s1, x + 0.16, ry, 0.06, 0.64, c["c"], None, 0.0)
        txt(s1, x + 0.30, ry + 0.05, 0.50, 0.20, tag, 8, True, c["c"])
        txt(s1, x + 0.86, ry + 0.04, CW - 1.12, 0.22, label, 10.5, True, INK)
        txt(s1, x + 0.30, ry + 0.34, CW - 0.50, 0.24, desc, 8.5, False, MUT)
    box(s1, x + 0.16, 5.30, CW - 0.32, 0.33, RGBColor(0xF6, 0xF7, 0xF5),
        RGBColor(0xCE, 0xE0, 0xD6), 0.14, 0.9)
    txt(s1, x + 0.28, 5.36, CW - 0.50, 0.22, c["pro"], 9, True, c["c"])
    box(s1, x + 0.16, 5.66, CW - 0.32, 0.33, RGBColor(0xFA, 0xF8, 0xF6),
        RGBColor(0xE4, 0xD6, 0xD2), 0.14, 0.9)
    txt(s1, x + 0.28, 5.72, CW - 0.50, 0.22, c["con"], 9, True, RGBColor(0x78, 0x5C, 0x58))

box(s1, 0.55, 6.16, 12.23, 0.80, BAND, RGBColor(0xE0, 0xDA, 0xD0), 0.10, 1.0)
txt(s1, 0.75, 6.24, 1.5, 0.26, "我们的位置", 11.5, True, INK)
txt(s1, 0.75, 6.52, 11.6, 0.30,
    "既不在 A（重资产专用机），也不是 C（自研量测硬件）；走 B 的轻量形态 —— 用既有测试台仪器，配自研执行器与适配层",
    10, False, MUT)
footer(s1, "来源：NI 官方产品与文档、ASML 产品页、Advantest 产品页　｜　引用清单见 docs/友商调研/README.md")

# ───────────────────────────── P2 两条骨架
s2 = prs.slides.add_slide(BLANK)
header(s2, "两条路线的软件骨架",
       "NI 用分层解耦吸收硬件差异，ASML 用测量闭环收敛参数")

txt(s2, 0.55, 1.42, 5.6, 0.28, "① NI：执行器与仪器解耦", 12, True, BLUE)
layers = [
    ("L4", "报告与数据", "结果标准化、MES 对接、追溯"),
    ("L3", "执行器 TestStand", "序列、步骤类型、流程模型、并行"),
    ("L3.5", "半导体扩展 TSM", "site 划分、pin map、半导体专用步骤"),
    ("L2", "驱动层", "模块 API（IVI 风格），测量抽象成函数"),
    ("L1", "PXI 机箱与模块", "背板同步、DUT 接口板、模块仪器"),
]
for i, (code, name, desc) in enumerate(layers):
    ly = 1.78 + i * 0.90
    hot = code in ("L3.5",)
    box(s2, 0.55, ly, 6.05, 0.78, CARD if not hot else RGBColor(0xF3, 0xF7, 0xFA),
        BLUE if hot else LINE, 0.08, 1.6 if hot else 1.0)
    txt(s2, 0.70, ly + 0.10, 0.62, 0.24, code, 8.5, True, BLUE if hot else MUT)
    txt(s2, 1.34, ly + 0.09, 2.40, 0.26, name, 11.5, True, INK)
    txt(s2, 1.34, ly + 0.40, 4.95, 0.26, desc, 9, False, MUT)
    if i < len(layers) - 1:
        arrow(s2, 1.02, ly + 0.80, 0.20, 0.08, RGBColor(0xB6, 0xBE, 0xC6), MSO_SHAPE.DOWN_ARROW)
txt(s2, 0.55, 6.34, 6.05, 0.50,
    "关键：TestStand 本身不懂 site，是 TSM 把 site / pin 概念注入进去的 —— 通用执行器保持通用，行业语义靠插件挂载。",
    9, False, RGBColor(0x44, 0x52, 0x5E))

txt(s2, 7.05, 1.42, 5.6, 0.28, "② ASML：测量结果反哺参数", 12, True, GREEN)
loop = [("算", "计算光刻给出掩模与工艺窗口", 7.15, 1.84),
        ("曝", "扫描机曝光，内建对准与调平", 9.90, 1.84),
        ("测", "YieldStar 测 overlay，HMI 测缺陷", 9.90, 3.86),
        ("修", "偏差归因，生成补偿参数", 7.15, 3.86)]
for name, desc, bx, by in loop:
    box(s2, bx, by, 2.60, 1.02, CARD, GREEN, 0.08, 1.2)
    txt(s2, bx + 0.16, by + 0.10, 0.50, 0.30, name, 13, True, GREEN)
    txt(s2, bx + 0.16, by + 0.46, 2.30, 0.46, desc, 9, False, MUT)
arrow(s2, 9.86, 2.26, 0.42, 0.18, GREEN)
arrow(s2, 11.06, 3.00, 0.18, 0.72, GREEN, MSO_SHAPE.DOWN_ARROW)
arrow(s2, 7.42, 4.20, 0.42, 0.18, GREEN, MSO_SHAPE.LEFT_ARROW)
arrow(s2, 7.30, 3.00, 0.18, 0.72, GREEN, MSO_SHAPE.UP_ARROW)
txt(s2, 8.62, 2.94, 1.10, 0.24, "闭环", 9.5, True, GREEN, PP_ALIGN.CENTER)
txt(s2, 7.05, 5.12, 5.70, 0.28, "与 NI 的互补关系", 10.5, True, INK)
box(s2, 7.05, 5.42, 5.70, 1.10, BAND, RGBColor(0xE0, 0xDA, 0xD0), 0.08, 1.0)
txt(s2, 7.22, 5.52, 5.36, 0.90,
    "NI 解决「接得进来、换得动」——降低接入成本；\nASML 解决「跑得稳、越跑越准」——降低参数漂移。\n一个改软件边界，一个改数据模型。",
    9.5, False, RGBColor(0x4A, 0x46, 0x40), spacing=1.25)
footer(s2, "来源：NI TestStand / TSM 手册与产品页；ASML 产品页与 Measuring accuracy 页　｜　详见 docs/友商调研/02、03")

# ───────────────────────────── P3 四项落点
s3 = prs.slides.add_slide(BLANK)
header(s3, "可落地的四项改造", "两项来自 NI，两项来自 ASML；均不动测试台硬件")

heads = [("", 0.55, 0.55), ("改造项", 1.30, 2.55), ("来源", 3.95, 1.00),
         ("我们现在的状况", 5.05, 3.35), ("改造后的目标", 8.50, 4.20)]
for t, hx, hw in heads:
    if t:
        txt(s3, hx, 1.36, hw, 0.24, t, 9, True, MUT)
box(s3, 0.55, 1.60, 12.23, 0.02, RGBColor(0xD8, 0xD4, 0xCC), None, 0.0)

items = [
    ("1", "引入 site 抽象", "NI / TSM", "无。一台 Runner 管多个台位时靠人工约定",
     "台位逻辑隔离，资源不冲突，可并行编排"),
    ("2", "逻辑资源名 → 物理地址", "NI / pin map", "地址写死在驱动配置里，换仪器要改代码",
     "只改映射表，TPS 与驱动都不动"),
    ("3", "固化生命周期六段", "NI / 流程模型", "用例作者可绕过预检与释放，直接跑断言",
     "预检→建连→执行→判定→释放→归档，不可跳"),
    ("4", "偏差归因字段", "ASML / 闭环", "结果偏 pass/fail，失败原因留在日志里",
     "设备/夹具/环境/程序四类归因，随结果回传"),
]
for i, (no, name, src, now, goal) in enumerate(items):
    ry = 1.78 + i * 1.14
    box(s3, 0.55, ry, 12.23, 1.02, CARD, LINE, 0.08, 1.0)
    box(s3, 0.55, ry, 0.06, 1.02, BLUE if i < 3 else GREEN, None, 0.0)
    box(s3, 0.72, ry + 0.30, 0.42, 0.42, BLUE if i < 3 else GREEN, None, 0.5)
    txt(s3, 0.72, ry + 0.36, 0.42, 0.30, no, 12, True, WHITE, PP_ALIGN.CENTER)
    txt(s3, 1.30, ry + 0.30, 2.55, 0.42, name, 12, True, INK, anchor=MSO_ANCHOR.MIDDLE)
    txt(s3, 3.95, ry + 0.34, 1.00, 0.36, src, 8.5, False, MUT, anchor=MSO_ANCHOR.MIDDLE)
    txt(s3, 5.05, ry + 0.30, 3.35, 0.44, now, 9, False, MUT, anchor=MSO_ANCHOR.MIDDLE)
    txt(s3, 8.50, ry + 0.30, 4.20, 0.44, goal, 9, False, RGBColor(0x2C, 0x40, 0x34),
        anchor=MSO_ANCHOR.MIDDLE)

box(s3, 0.55, 6.42, 12.23, 0.60, BAND, RGBColor(0xE0, 0xDA, 0xD0), 0.10, 1.0)
txt(s3, 0.75, 6.54, 11.9, 0.34,
    "四项都不改硬件，只改软件边界与数据模型；最急的是第 1 项 —— site 抽象是「一拖多」能否成立的分水岭。",
    10, False, RGBColor(0x4A, 0x46, 0x40))
footer(s3, "判断依据：NI 的 site / pin map 抽象与 ASML 的偏差归因闭环；对应改造点在 docs/友商调研/04")

prs.save(OUT)

# ── 自检
def audit(path):
    p = Presentation(path)
    pics = oob = overlaps = 0
    for si, sl in enumerate(p.slides):
        boxes = []
        for sh in sl.shapes:
            if sh.shape_type == 13:  # PICTURE
                pics += 1
            try:
                l, t = sh.left.inches, sh.top.inches
                w, h = sh.width.inches, sh.height.inches
            except Exception:
                continue
            if l < -0.01 or t < -0.01 or l + w > SW + 0.01 or t + h > SH + 0.01:
                oob += 1
                print(f"  越界 p{si+1}: {sh.shape_type} ({l:.2f},{t:.2f},{w:.2f}x{h:.2f})")
            if sh.has_text_frame and sh.text_frame.text.strip():
                boxes.append((l, t, w, h, sh.text_frame.text[:14]))
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
                oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
                if ox > 0.06 and oy > 0.06:
                    overlaps += 1
                    print(f"  文本重叠 p{si+1}: 「{a[4]}」×「{b[4]}」 {ox:.2f}x{oy:.2f}")
    print(f"页数 {len(p.slides)}｜图片 {pics}｜越界 {oob}｜文本重叠 {overlaps}｜"
          f"{os.path.getsize(path)} B")
    return pics, oob, overlaps


print("输出：", OUT)
audit(OUT)
