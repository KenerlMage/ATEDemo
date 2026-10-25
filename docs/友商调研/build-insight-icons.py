# -*- coding: utf-8 -*-
"""业界方案洞察 · NI（图标节点版）：拓扑节点改为图片，三大组件引官网原文，虚拟化补产业应用"""
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
ICON = os.path.join(IMGD, "icons")
OUT = os.path.join(HERE, "业界方案洞察-NI-图标版.pptx")

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


def crop(src, dst, ar, focus=0.5):
    im = Image.open(os.path.join(IMGD, src)).convert("RGB")
    w, h = im.size
    tw, th = w, w / ar
    if th > h:
        th, tw = h, h * ar
    left = max(0, min(w - tw, w * focus - tw / 2))
    top = max(0, (h - th) / 2)
    im.crop((int(left), int(top), int(left + tw), int(top + th))).save(
        os.path.join(IMGD, dst), "JPEG", quality=90, optimize=True)


crop("pxi_config.png", "node_chassis.jpg", 1.62, 0.45)

prs = Presentation()
prs.slide_width = Inches(SW)
prs.slide_height = Inches(SH)
sl = prs.slides.add_slide(prs.slide_layouts[6])


def _font(run, size, bold, color, italic=False):
    f = run.font
    f.size = Pt(size)
    f.bold = bold
    f.italic = italic
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
        align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, ls=1.14, italic=False):
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
        _font(r, size, bold, color, italic)
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
    d = 0.20
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
    _font(r, 6.2, True, WHITE)


def inode(x, y, w, h, label, icon, accent=BLUE, fill=WHITE, num=None, nums=None):
    """图标节点：上图形、下名称"""
    rect(x, y, w, h, fill, accent, 0.9)
    vrule(x, y, h, accent, 0.04)
    ih = min(0.35, h - 0.055 - 0.20)
    iw = min(0.56, ih * 1.60)
    iy = y + 0.045
    if icon.endswith(".jpg"):
        sl.shapes.add_picture(os.path.join(IMGD, icon), Inches(x + (w - iw) / 2), Inches(iy),
                              width=Inches(iw), height=Inches(ih))
    else:
        sl.shapes.add_picture(os.path.join(ICON, icon), Inches(x + (w - iw) / 2), Inches(iy),
                              width=Inches(iw), height=Inches(ih))
    txt(x + 0.04, y + h - 0.175, w - 0.08, 0.15, label, 6.3, True, INK, PP_ALIGN.CENTER)
    if nums:
        for k, n in enumerate(nums):
            badge(x + w - 0.25 - k * 0.24, y + 0.05, n)
    elif num:
        badge(x + w - 0.25, y + 0.05, num)


def shead(x, y, w, text):
    txt(x, y, w, 0.17, text, 7.9, True, RED)
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

RX, RW = 7.52, 5.24

# ── 中心层
inode(0.56, 1.46, 1.62, 0.62, "MES 产线系统", "mes.png", RED, FILL2)
inode(2.34, 1.46, 1.44, 0.62, "程序库", "lib.png", RED, WHITE)
inode(3.94, 1.46, 1.44, 0.62, "结果库", "db.png", RED, FILL2)
inode(5.54, 1.46, 1.76, 0.62, "分析平台", "chart.png", RED, WHITE)

for xc in (1.37, 3.06, 4.66, 6.42):
    wire([(xc, 2.08), (xc, 2.15)], arrow=False)
wire([(1.37, 2.15), (6.42, 2.15)], arrow=False)

inode(2.10, 2.22, 2.90, 0.58, "工业以太网交换机（千兆 · 环网 · VLAN）", "switch.png", BLUE, FILL2)
wire([(3.55, 2.15), (3.55, 2.22)], arrow=False)
wire([(3.55, 2.80), (3.55, 2.94)])

# ── 控制层
inode(1.10, 2.94, 4.90, 0.72, "工业控制计算机（Host）：测试执行 · 工位扩展 · 操作界面 · 驱动",
      "pc.png", BLUE, WHITE, nums=["1", "3"])

wire([(3.55, 3.66), (3.55, 3.73)], arrow=False)
wire([(1.71, 3.73), (6.23, 3.73)], arrow=False)
for xc in (1.71, 4.01, 6.23):
    wire([(xc, 3.73), (xc, 3.80)])

# ── 测量层
inode(0.56, 3.80, 2.30, 0.68, "测量程序（LabVIEW）", "vi.png", BLUE, WHITE, num="2")
inode(3.06, 3.80, 1.90, 0.68, "仪器驱动（SCPI / IVI）", "driver.png", BLUE, FILL2)
inode(5.16, 3.80, 2.14, 0.68, "开关与路径（资源仲裁）", "crossbar.png", BLUE, WHITE)

for xc in (1.71, 4.01, 6.23):
    wire([(xc, 4.48), (xc, 4.55)], arrow=False)
wire([(1.71, 4.55), (6.23, 4.55)], arrow=False)
for xc in (1.71, 4.01, 6.23):
    wire([(xc, 4.55), (xc, 4.62)])

# ── 仪器层
inode(0.56, 4.62, 2.30, 0.76, "模块化机箱（PXI）", "node_chassis.jpg", BLUE, WHITE)
inode(3.06, 4.62, 1.90, 0.76, "开关与矩阵", "crossbar.png", BLUE, FILL2)
inode(5.16, 4.62, 2.14, 0.76, "外挂仪器（LXI / GPIB / USB）", "instr.png", BLUE, WHITE)

for xc in (1.71, 4.01, 6.23):
    wire([(xc, 5.38), (xc, 5.45)], arrow=False)
wire([(1.71, 5.45), (6.23, 5.45)], arrow=False)
wire([(2.26, 5.45), (2.26, 5.52)])
wire([(5.75, 5.45), (5.75, 5.52)])

# ── 被测层
inode(0.56, 5.52, 3.40, 0.56, "夹具与针床", "fixture.png", RED, FILL2)
inode(4.20, 5.52, 3.10, 0.56, "被测件工位 DUT × N", "dut.png", RED, WHITE)
wire([(3.96, 5.80), (4.20, 5.80)])

txt(0.56, 6.12, 6.74, 0.18,
    "连线为通路示意：控制与数据（TCP/IP、PCIe、背板触发）、信号（电缆与针床接触）；节点图形为示意，机箱为产品图。",
    6.0, False, DIM)

# ── 右栏 A：三大组件（官方描述）
shead(RX, 1.44, RW, "三大组件：官方功能描述（引自 ni.com）")
comps = [
    ("1", "TestStand",
     "\u201cOff-the-shelf test management software for validation & production test.\u201d",
     "现成的测试管理软件，用于验证测试与生产测试。"),
    ("2", "LabVIEW",
     "\u201cA graphical programming environment engineers use to develop automated production, validation & research test systems.\u201d",
     "工程师用于开发自动化生产、验证与研究类测试系统的图形化编程环境。"),
    ("3", "TSM",
     "\u201cExtends the TestStand environment to help you develop, debug, deploy, and maintain semiconductor test systems.\u201d",
     "在 TestStand 环境之上扩展，用于开发、调试、部署与维护半导体测试系统。"),
]
cy = 1.70
for num, nm, en, cn in comps:
    badge(RX, cy + 0.02, num)
    txt(RX + 0.27, cy, 1.10, 0.17, nm, 7.4, True, INK)
    txt(RX + 1.42, cy, RW - 1.42, 0.34, en, 6.0, False, RGBColor(0x4A, 0x48, 0x44), ls=1.12, italic=True)
    txt(RX + 1.42, cy + 0.35, RW - 1.42, 0.28, cn, 6.2, False, BODY, ls=1.12)
    hline(RX, cy + 0.64, RW, RULE, 0.008)
    cy += 0.70

# ── 右栏 B：仪器虚拟化
shead(RX, 3.82, RW, "仪器虚拟化：从理念到仿真")
vitems = [
    ("理念", "软件定义仪器功能与前面板，虚拟仪器概念自 1986 年沿用。"),
    ("硬件", "PXI 模块按测量类型组合，换模块即改变仪器构成。"),
    ("抽象", "NI-VISA 与 IVI 驱动统一会话，更换总线不改上层调用。"),
    ("面板", "InstrumentStudio 以台式面板呈现模块化仪器。"),
    ("仿真", "NI MAX 可建仿真设备，无硬件亦可开发验证流程。"),
]
vy = 4.04
for tag, t in vitems:
    txt(RX, vy + 0.01, 0.42, 0.15, tag, 6.1, True, BLUE)
    txt(RX + 0.44, vy, RW - 0.44, 0.22, t, 6.5, False, BODY, ls=1.1)
    vy += 0.26

# ── 右栏 C：产业应用
shead(RX, 5.32, RW, "虚拟化在产业中的应用")
apps = ["半导体晶圆与封测", "汽车电子与 ECU", "航空航天与国防", "消费电子",
        "通信设备", "能源与电力", "医疗电子", "科研与院校"]
for i, a in enumerate(apps):
    cx = RX + (i % 4) * 1.29
    cyy = 5.54 + (i // 4) * 0.28
    rect(cx, cyy, 1.21, 0.24, FILL2, RULE, 0.6)
    txt(cx, cyy + 0.045, 1.21, 0.16, a, 6.1, False, BODY, PP_ALIGN.CENTER)

txt(RX, 6.14, RW, 0.20,
    "适用：多品种中小批量、机箱内 4 至 16 工位并行；受限：成本敏感的单品种大批量。",
    6.2, False, MUT)

# ── 小结
hline(0.56, 6.44, 12.20, RULE, 0.010)
txt(0.56, 6.50, 0.52, 0.20, "小结", 8.0, True, RED)
txt(1.12, 6.50, 11.60, 0.44,
    "组件分工清晰：执行（TestStand）、测量（LabVIEW）、工位扩展（TSM）各归其位；仪器虚拟化使其在硬件不变的前提下"
    "通过软件调整测量能力，并已在半导体、汽车、航空航天、通信等行业形成成熟应用。",
    7.6, False, RGBColor(0x25, 0x25, 0x25), ls=1.2)

txt(0.56, 7.09, 11.5, 0.20,
    "资料来源：NI 公开产品页（What Is TestStand、LabVIEW、TestStand Semiconductor Module）；"
    "三大组件描述为官方原文引用，中文为直译；节点划分、编号与适用性判断为本文归纳。", 6.2, False, DIM)
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
