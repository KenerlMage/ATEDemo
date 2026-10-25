# -*- coding: utf-8 -*-
"""业界方案洞察 · NI（单页版）：拓扑为主，合入软件沿革，标注三大组件位置，补仪器虚拟化"""
import os
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt
from pptx.oxml.ns import qn
from lxml import etree
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
IMGD = os.path.join(HERE, "ni_img")
OUT = os.path.join(HERE, "业界方案洞察-NI-单页.pptx")

SW, SH = 13.333, 7.5
INK = RGBColor(0x1A, 0x1A, 0x1A)
BODY = RGBColor(0x2E, 0x2E, 0x2E)
MUT = RGBColor(0x6E, 0x6C, 0x68)
DIM = RGBColor(0x93, 0x91, 0x8C)
RED = RGBColor(0x8B, 0x1A, 0x1A)
BLUE = RGBColor(0x1F, 0x3F, 0x66)
RULE = RGBColor(0xBF, 0xBB, 0xB1)
FILL = RGBColor(0xF7, 0xF6, 0xF3)
FILL2 = RGBColor(0xF1, 0xF3, 0xF6)
GRAY = RGBColor(0xED, 0xED, 0xED)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
WIRE = RGBColor(0x7A, 0x76, 0x6E)


def crop(src, dst, ar, focus=0.5, vfocus=0.5):
    im = Image.open(os.path.join(IMGD, src)).convert("RGB")
    w, h = im.size
    tw, th = w, w / ar
    if th > h:
        th, tw = h, h * ar
    left = max(0, min(w - tw, w * focus - tw / 2))
    top = max(0, min(h - th, h * vfocus - th / 2))
    im.crop((int(left), int(top), int(left + tw), int(top + th))).save(
        os.path.join(IMGD, dst), "JPEG", quality=90, optimize=True)


crop("pxi_config.png", "node_chassis.jpg", 1.65, 0.45, 0.5)

prs = Presentation()
prs.slide_width = Inches(SW)
prs.slide_height = Inches(SH)
sl = prs.slides.add_slide(prs.slide_layouts[6])


def _font(run, size, bold, color):
    f = run.font
    f.size = Pt(size)
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


def txt(x, y, w, h, text, size=8, bold=False, color=BODY,
        align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, ls=1.14):
    tb = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, ln in enumerate(str(text).split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = ls
        r = p.add_run()
        r.text = ln
        _font(r, size, bold, color)
    return tb


def rect(x, y, w, h, fill=WHITE, line=RULE, lw=0.75):
    s = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    s.shadow.inherit = False
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(lw)
    s.text_frame.text = ""
    return s


def hline(x, y, w, color=RULE, lw=0.012):
    s = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(lw))
    s.shadow.inherit = False
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    return s


def vrule(x, y, h, color=RED, w=0.045):
    s = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    s.shadow.inherit = False
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    return s


def wire(pts, color=WIRE, lw=1.0, arrow=True):
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        c = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1),
                                    Inches(x2), Inches(y2))
        c.line.color.rgb = color
        c.line.width = Pt(lw)
        ln = c.line._get_or_add_ln()
        if arrow and i == len(pts) - 2:
            t = etree.SubElement(ln, qn("a:tailEnd"))
            t.set("type", "triangle")
            t.set("w", "med")
            t.set("len", "med")


def badge(x, y, num):
    d = 0.21
    s = sl.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    s.shadow.inherit = False
    s.fill.solid()
    s.fill.fore_color.rgb = RED
    s.line.fill.background()
    tf = s.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = num
    _font(r, 6.4, True, WHITE)


def node(x, y, w, h, title, sub="", accent=BLUE, fill=WHITE, img=None,
         imgw=0.60, num=None):
    rect(x, y, w, h, fill, accent, 0.9)
    vrule(x, y, h, accent, 0.045)
    tx = x + 0.11
    if img:
        ih = min(h - 0.12, imgw / 1.65)
        sl.shapes.add_picture(os.path.join(IMGD, img), Inches(x + 0.12),
                              Inches(y + (h - ih) / 2), width=Inches(imgw), height=Inches(ih))
        rect(x + 0.12, y + (h - ih) / 2, imgw, ih, None, RULE, 0.6)
        tx = x + 0.12 + imgw + 0.09
    tw = x + w - tx - 0.10 - (0.30 if num else 0.0)
    txt(tx, y + 0.07, tw, 0.18, title, 7.2, True, INK)
    if sub:
        txt(tx, y + 0.26, tw, h - 0.30, sub, 5.9, False, MUT, ls=1.1)
    if num:
        badge(x + w - 0.29, y + 0.07, num)


def ltag(x, y, text, w=1.40, size=5.6):
    txt(x, y, w, 0.13, text, size, False, RGBColor(0x86, 0x82, 0x7A))


def shead(x, y, w, text):
    txt(x, y, w, 0.17, text, 8.0, True, RED)
    hline(x, y + 0.21, w, RULE)


# ── 页头
txt(0.56, 0.22, 8.0, 0.38, "业界方案洞察", 21, True, INK)
txt(8.90, 0.34, 3.86, 0.22, "NI 方案的拓扑、组件与仪器虚拟化", 8.4, False, DIM, PP_ALIGN.RIGHT)
rect(0.56, 0.66, 12.20, 0.70, GRAY, None, 0)
vrule(0.56, 0.66, 0.70, RED, 0.05)
txt(0.74, 0.75, 11.85, 0.54,
    "美国国家仪器（National Instruments）成立于 1976 年，总部位于美国得克萨斯州奥斯汀，2023 年并入 Emerson，"
    "作为其测试与测量业务运营；软件体系以 1986 年发布的 LabVIEW 为起点，向上延伸出 TestStand 测试执行软件"
    "与面向半导体测试的 TestStand Semiconductor Module，硬件侧以 PXI 模块化平台为主。"
    "本页说明该方案的硬件拓扑、三大组件的落点与仪器虚拟化相关做法。",
    7.3, False, RGBColor(0x2E, 0x2E, 0x2E), ls=1.26)

TX, TW = 0.56, 6.74
RX, RW = 7.52, 5.24

# ── 中心层
node(0.56, 1.46, 1.62, 0.52, "MES 产线系统", "任务令 · 结果回传", RED, FILL2)
node(2.34, 1.46, 1.44, 0.52, "程序库", "序列与配置版本", RED, WHITE)
node(3.94, 1.46, 1.44, 0.52, "结果库", "原始数据 · 追溯", RED, FILL2)
node(5.54, 1.46, 1.76, 0.52, "分析平台", "报表 · 良率趋势", RED, WHITE)

for xc in (1.37, 3.06, 4.66, 6.42):
    wire([(xc, 1.98), (xc, 2.12)], arrow=False)
wire([(1.37, 2.12), (6.42, 2.12)], arrow=False)

node(2.10, 2.24, 2.90, 0.48, "工业以太网交换机", "千兆 · 环网冗余 · VLAN 隔离", BLUE, FILL2)
wire([(3.55, 2.12), (3.55, 2.24)], arrow=False)
wire([(3.55, 2.72), (3.55, 3.02)])
ltag(3.63, 2.76, "TCP/IP 千兆", 1.10)

# ── 控制层：TestStand ①、TSM ③
node(1.10, 3.04, 4.90, 0.66, "工业控制计算机（Host）",
     "测试执行引擎 · 工位扩展 · 操作界面（HMI）· 设备驱动", BLUE, WHITE, num="1")

wire([(3.55, 3.70), (3.55, 3.86)], arrow=False)
wire([(1.71, 3.86), (6.23, 3.86)], arrow=False)
for xc in (1.71, 4.01, 6.23):
    wire([(xc, 3.86), (xc, 3.98)])
ltag(4.10, 3.72, "PCIe / 以太网 · 背板触发", 1.90)

# ── 测量层：LabVIEW ②
node(0.56, 3.98, 2.30, 0.60, "测量程序", "激励 · 采集 · 换算 · 比较", BLUE, WHITE, num="2")
node(3.06, 3.98, 1.90, 0.60, "仪器驱动", "会话管理 · SCPI / IVI", BLUE, FILL2)
node(5.16, 3.98, 2.14, 0.60, "开关与路径", "通路互斥 · 资源仲裁", BLUE, WHITE)

wire([(1.71, 4.58), (1.71, 4.72)], arrow=False)
wire([(4.01, 4.58), (4.01, 4.72)], arrow=False)
wire([(6.23, 4.58), (6.23, 4.72)], arrow=False)
wire([(1.71, 4.72), (6.23, 4.72)], arrow=False)
for xc in (1.71, 4.01, 6.23):
    wire([(xc, 4.72), (xc, 4.86)])

# ── 仪器层
node(0.56, 4.88, 2.30, 0.72, "模块化机箱", "数字 / 模拟 / 射频模块", BLUE, WHITE,
     img="node_chassis.jpg", imgw=0.62)
node(3.06, 4.88, 1.90, 0.72, "开关与矩阵", "通道扩展\n路径映射", BLUE, FILL2)
node(5.16, 4.88, 2.14, 0.72, "外挂仪器", "LXI / GPIB / USB\n统一会话访问", BLUE, WHITE)

wire([(1.71, 5.60), (1.71, 5.70)], arrow=False)
wire([(4.01, 5.60), (4.01, 5.70)], arrow=False)
wire([(6.23, 5.60), (6.23, 5.70)], arrow=False)
wire([(1.71, 5.70), (6.23, 5.70)], arrow=False)
wire([(2.26, 5.70), (2.26, 5.82)])
wire([(5.75, 5.70), (5.75, 5.82)])
ltag(2.36, 5.705, "信号电缆 / 背板触发总线", 1.90, 5.4)

# ── 被测层
node(0.56, 5.84, 3.40, 0.46, "夹具与针床", "机械定位 · 信号引出", RED, FILL2)
node(4.20, 5.84, 3.10, 0.46, "被测件工位 DUT × N", "并行执行 · 上下料", RED, WHITE)
wire([(3.96, 6.07), (4.20, 6.07)])
ltag(3.90, 5.88, "接触", 0.34, 5.4)

# ── 右栏：三大组件
shead(RX, 1.44, RW, "三大组件及其在拓扑中的位置")
comps = [
    ("1", "TestStand", "控制层 · Host 节点内部", "组织测试流程、调度步骤、汇总与归档结果，是站点的执行中枢。"),
    ("2", "LabVIEW", "测量层 · 测量程序", "编写具体测量逻辑，经仪器驱动完成激励施加、信号采集与数据换算。"),
    ("3", "TSM", "控制层 · 执行引擎之上", "为半导体测试提供工位与引脚映射、多工位复用与分档输出。"),
]
cy = 1.66
for num, nm, pos, role in comps:
    badge(RX, cy + 0.04, num)
    txt(RX + 0.28, cy + 0.02, 1.55, 0.18, nm, 7.6, True, INK)
    txt(RX + 0.28, cy + 0.21, 1.62, 0.18, pos, 5.9, False, RED)
    txt(RX + 1.94, cy + 0.02, RW - 1.94, 0.50, role, 6.8, False, BODY, ls=1.16)
    hline(RX, cy + 0.50, RW, RULE, 0.008)
    cy += 0.54

# ── 右栏：仪器虚拟化
shead(RX, 3.34, RW, "仪器虚拟化方面的做法")
vitems = [
    ("理念", "以软件定义仪器功能与前面板，虚拟仪器概念自 1986 年 LabVIEW 发布起沿用。"),
    ("硬件", "PXI 模块按测量类型组合，换模块即改变仪器构成；FPGA 模块可将部分测量逻辑下放。"),
    ("抽象", "NI-VISA 与 IVI 驱动将不同总线的仪器抽象为统一会话，更换总线不改上层调用。"),
    ("面板", "InstrumentStudio 以台式仪器面板呈现模块化仪器，调试期可直接操作通道与量程。"),
    ("仿真", "NI MAX 可创建仿真设备，无实物硬件亦可开发与验证测试流程。"),
]
vy = 3.56
for tag, t in vitems:
    txt(RX, vy + 0.01, 0.46, 0.16, tag, 6.2, True, BLUE)
    txt(RX + 0.48, vy, RW - 0.48, 0.34, t, 6.8, False, BODY, ls=1.16)
    vy += 0.36

# ── 右栏：适用场景
shead(RX, 5.42, RW, "适用场景")
rows = [("多品种、中小批量、引脚数中等以上", "适用"),
        ("机箱内 4 至 16 工位并行", "适用"),
        ("成本敏感的单品种大批量", "受限")]
ry = 5.66
for a, b in rows:
    txt(RX, ry, RW - 0.90, 0.16, a, 6.6, False, BODY)
    txt(RX + RW - 0.76, ry, 0.76, 0.16, b, 6.6, True, RED if b == "受限" else INK, PP_ALIGN.RIGHT)
    ry += 0.21

# ── 小结
hline(0.56, 6.44, 12.20, RULE, 0.010)
txt(0.56, 6.50, 0.52, 0.20, "小结", 8.0, True, RED)
txt(1.12, 6.50, 11.60, 0.44,
    "组件分工清晰：执行（TestStand）、测量（LabVIEW）、工位扩展（TSM）各归其位；仪器虚拟化使其在硬件不变的前提下"
    "通过软件调整测量能力，适配中等规模、品种较多的量产线；成本敏感的单品种大批量场景相对受限。",
    7.6, False, RGBColor(0x25, 0x25, 0x25), ls=1.2)

txt(0.56, 7.09, 11.5, 0.20,
    "资料来源：NI 公开产品资料与公司信息；节点划分、组件落点与适用性判断为本文归纳。"
    "节点内配图为 ni.com 机箱与仪器配置界面。", 6.4, False, DIM)
txt(12.30, 7.14, 0.50, 0.20, "1 / 1", 8, False, DIM, PP_ALIGN.RIGHT)

prs.save(OUT)

p = Presentation(OUT)
pics = oob = ov = 0
for si, s in enumerate(p.slides):
    bx = []
    for sh in s.shapes:
        if sh.shape_type == 13:
            pics += 1
        try:
            l, t = sh.left.inches, sh.top.inches
            w, h = sh.width.inches, sh.height.inches
        except Exception:
            continue
        if l < -0.01 or t < -0.01 or l + w > SW + 0.01 or t + h > SH + 0.01:
            oob += 1
            print(f"  越界: ({l:.2f},{t:.2f},{w:.2f}x{h:.2f})")
        if sh.has_text_frame and sh.text_frame.text.strip():
            bx.append((l, t, w, h, sh.text_frame.text[:12]))
    for i in range(len(bx)):
        for j in range(i + 1, len(bx)):
            a, b = bx[i], bx[j]
            ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            if ox > 0.08 and oy > 0.08:
                ov += 1
                print(f"  重叠: 「{a[4]}」×「{b[4]}」{ox:.2f}x{oy:.2f}")
print(f"页数 {len(p.slides)}｜图片 {pics}｜越界 {oob}｜文本重叠 {ov}｜{os.path.getsize(OUT)} B")
