# -*- coding: utf-8 -*-
"""NI 方案汇报：P1 硬件拓扑 + 适用场景；P2 软件架构 + 适用场景
版式：白底、直角细线框、单一强调色，不用圆角卡片与彩色标签，全篇微软雅黑。"""
import os
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt
from pptx.oxml.ns import qn

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "NI-ATE汇报-硬件拓扑与软件架构.pptx")

SW, SH = 13.333, 7.5
INK = RGBColor(0x1A, 0x1A, 0x1A)
BODY = RGBColor(0x33, 0x33, 0x33)
MUT = RGBColor(0x6E, 0x6C, 0x68)
DIM = RGBColor(0x95, 0x93, 0x8E)
RED = RGBColor(0x8B, 0x1A, 0x1A)
BLUE = RGBColor(0x1F, 0x3F, 0x66)
RULE = RGBColor(0xC4, 0xC0, 0xB6)
FILL = RGBColor(0xF6, 0xF5, 0xF2)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

prs = Presentation()
prs.slide_width = Inches(SW)
prs.slide_height = Inches(SH)
BLANK = prs.slide_layouts[6]


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


def txt(sl, x, y, w, h, text, size=8, bold=False, color=BODY,
        align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, ls=1.18):
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


def rect(sl, x, y, w, h, fill=WHITE, line=RULE, lw=0.75):
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


def hline(sl, x, y, w, color=RULE, lw=0.75):
    s = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(0.012))
    s.shadow.inherit = False
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    return s


def vrule(sl, x, y, h, color=RED, w=0.035):
    s = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    s.shadow.inherit = False
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    return s


def varrow(sl, x, y, h=0.24, color=RGBColor(0xAD, 0xA9, 0x9F)):
    s = sl.shapes.add_shape(MSO_SHAPE.DOWN_ARROW, Inches(x), Inches(y), Inches(0.14), Inches(h))
    s.shadow.inherit = False
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    s.text_frame.text = ""
    return s


def head(sl, title, sub, page):
    txt(sl, 0.56, 0.30, 11.2, 0.40, title, 19, True, INK)
    txt(sl, 0.58, 0.76, 11.2, 0.24, sub, 9, False, MUT)
    hline(sl, 0.56, 1.06, 12.20, RED, 0.020)
    txt(sl, 12.30, 7.14, 0.50, 0.20, page, 8, False, DIM, PP_ALIGN.RIGHT)


def foot(sl, s):
    hline(sl, 0.56, 7.02, 12.20, RULE, 0.010)
    txt(sl, 0.56, 7.09, 11.6, 0.20, s, 6.8, False, DIM)


def band(sl, x, y, w, h, label, sub=""):
    """层带：白底细框 + 左侧强调竖线 + 标签"""
    rect(sl, x, y, w, h, WHITE, RULE, 0.75)
    vrule(sl, x, y, h, RED, 0.045)
    txt(sl, x + 0.12, y + 0.10, 0.94, 0.22, label, 8.2, True, RED)
    if sub:
        txt(sl, x + 0.12, y + 0.34, 0.94, h - 0.40, sub, 6.6, False, DIM)


def cell(sl, x, y, w, h, name, desc, note="", fill=WHITE):
    rect(sl, x, y, w, h, fill, RULE, 0.75)
    txt(sl, x + 0.10, y + 0.09, w - 0.20, 0.20, name, 7.8, True, INK)
    if desc:
        txt(sl, x + 0.10, y + 0.32, w - 0.20, h - 0.38, desc, 6.8, False, MUT)
    if note:
        txt(sl, x + 0.10, y + h - 0.24, w - 0.20, 0.18, note, 6.2, False, DIM)


def right_head(sl, x, y, w, text):
    txt(sl, x, y, w, 0.20, text, 8.6, True, RED)
    hline(sl, x, y + 0.25, w, RULE, 0.010)


# ═══════════════════════════════════════ P1 硬件拓扑
s1 = prs.slides.add_slide(BLANK)
head(s1, "一、NI 方案的硬件拓扑",
     "中心侧负责程序下发与数据回收，站点侧承担全部实时测试动作，两者以工业以太网连接", "1 / 2")

TX, TW = 0.56, 7.62          # 拓扑画布
CX, CW = 8.50, 4.26          # 右侧文字栏
LY, LH, LG = 1.28, 1.06, 0.34

bands = [
    ("中心与网络", "管理与数据", [
        ("工业以太网交换", "站点与中心互联\n千兆及以上", ""),
        ("中心服务器", "程序库与结果库\n部署与追溯", ""),
        ("制造执行接口", "任务令与结果回传\n按产线接口约定", ""),
    ]),
    ("站点控制", "工控计算机", [
        ("工业控制计算机", "运行测试执行软件\n连接机箱与外部仪器", ""),
        ("嵌入式控制器", "插于机箱内部\n适用于紧凑型工位", ""),
        ("操作终端", "启动、监视与处置\n不参与测量控制", ""),
    ]),
    ("仪器与开关", "测量资源", [
        ("模块化机箱", "数字、模拟、射频模块\n背板提供时钟与触发", ""),
        ("开关与矩阵", "通道扩展与路径切换\n共享资源的互斥管理", ""),
        ("外挂仪器", "以太网、总线与串口仪器\n统一会话访问", ""),
    ]),
    ("接口与被测", "被测对象", [
        ("夹具与针床", "机械定位与信号引出\n更换品种时整体替换", ""),
        ("被测件工位", "同一机箱支持多工位\n各工位并行执行", ""),
        ("上下料装置", "手动或自动上下料\n与测试节拍配合", ""),
    ]),
]

for i, (label, sub, cells) in enumerate(bands):
    y = LY + i * (LH + LG)
    band(s1, TX, y, TW, LH, label, sub)
    cw = (TW - 1.24 - 0.20) / 3
    for j, (nm, ds, nt) in enumerate(cells):
        cx = TX + 1.24 + j * (cw + 0.10)
        cell(s1, cx, y + 0.10, cw, LH - 0.20, nm, ds, nt, FILL if j % 2 == 0 else WHITE)
    if i < 3:
        varrow(s1, TX + TW / 2 - 0.07, y + LH + 0.05, LG - 0.10)

txt(s1, TX, LY + 4 * LH + 3 * LG + 0.06, TW, 0.22,
    "图中自上而下为信息流方向；仪器控制信号在机箱背板内完成，不经过网络。", 6.9, False, DIM)

right_head(s1, CX, LY, CW, "拓扑说明")
notes = [
    ("（一）", "拓扑为单站点纵向结构：中心侧只负责程序下发与结果回收，站点侧承担全部实时测试动作。"),
    ("（二）", "同一机箱内的多工位以并行执行实现，不通过增加工业控制计算机数量来扩展工位。"),
    ("（三）", "程序与结果经工业以太网传输，仪器控制信号经机箱背板传输，两条通路相互独立。"),
]
ny = LY + 0.34
for k, t in notes:
    txt(s1, CX, ny, 0.44, 0.20, k, 7.4, True, DIM)
    txt(s1, CX + 0.44, ny, CW - 0.44, 0.66, t, 7.5, False, BODY)
    ny += 0.74

right_head(s1, CX, ny + 0.12, CW, "适用场景")
rows = [
    ("多品种、中小批量、引脚数中等以上", "适用"),
    ("单一机箱内 4 至 16 工位并行", "适用"),
    ("数字、模拟与射频混合测量", "适用"),
    ("成本敏感的单品种大批量", "受限"),
    ("需与既有非 NI 仪器深度耦合", "受限"),
]
ry = ny + 0.48
for i, (a, b) in enumerate(rows):
    rect(s1, CX, ry, CW, 0.38, WHITE if i % 2 else FILL, RULE, 0.5)
    txt(s1, CX + 0.10, ry + 0.10, CW - 1.10, 0.20, a, 7.2, False, BODY)
    txt(s1, CX + CW - 0.86, ry + 0.10, 0.76, 0.20, b, 7.2, True,
        RED if b == "受限" else INK, PP_ALIGN.RIGHT)
    ry += 0.38

txt(s1, CX, ry + 0.10, CW, 0.44,
    "说明：适用性判断以功能覆盖与工程成本为依据，不含价格因素。", 6.7, False, DIM)
foot(s1, "资料来源：NI 公开产品资料与文档；分层方式与适用性判断为本文归纳。")

# ═══════════════════════════════════════ P2 软件架构
s2 = prs.slides.add_slide(BLANK)
head(s2, "二、NI 方案的软件架构",
     "软件分为应用、执行、测量、平台四层，上层组织流程，下层提供测量能力，层间以配置与调用约定衔接", "2 / 2")

layers = [
    ("应用层", "使用者视角", "操作界面　程序管理　结果查询",
     "面向操作与工艺人员，负责程序选择、运行监视与结果查阅，不直接控制仪器。"),
    ("执行层", "流程组织", "序列执行　工位管理　判定与分档",
     "按既定流程组织测试步骤，管理多工位并行，汇集测量结果并完成合格性判定与分档。"),
    ("测量层", "测量实现", "测量程序　仪器驱动　开关路径",
     "面向具体测试项，完成激励施加、信号采集与数据换算，并与仪器和开关资源对接。"),
    ("平台层", "资源与配置", "机箱与模块管理　系统配置　自检",
     "完成硬件资源发现、通道与触发配置、设备自检与固件维护，为以上各层提供统一资源视图。"),
]

for i, (label, sub, comps, desc) in enumerate(layers):
    y = LY + i * (LH + LG)
    band(s2, TX, y, TW, LH, label, sub)
    bx = TX + 1.24
    bw = TW - 1.24
    rect(s2, bx, y + 0.10, bw, LH - 0.20, WHITE, RULE, 0.75)
    txt(s2, bx + 0.14, y + 0.18, bw - 0.28, 0.20, comps, 7.6, True, BLUE)
    txt(s2, bx + 0.14, y + 0.44, bw - 0.28, LH - 0.56, desc, 7.4, False, BODY)
    if i < 3:
        varrow(s2, TX + TW / 2 - 0.07, y + LH + 0.05, LG - 0.10)

txt(s2, TX, LY + 4 * LH + 3 * LG + 0.06, TW, 0.22,
    "箭头表示调用方向：上层提出测试需求，下层提供测量与资源能力。", 6.9, False, DIM)

right_head(s2, CX, LY, CW, "架构特征")
feats = [
    ("（一）", "层次职责单一：应用层不涉及测量，测量层不涉及流程，任一层调整不影响相邻层。"),
    ("（二）", "流程与测量分离：更换被测对象主要修改测量层与流程配置，更换仪器主要修改测量层。"),
    ("（三）", "结果出口统一：各层结果按统一格式归档，供中心侧汇总、追溯与报表使用。"),
]
ny = LY + 0.34
for k, t in feats:
    txt(s2, CX, ny, 0.44, 0.20, k, 7.4, True, DIM)
    txt(s2, CX + 0.44, ny, CW - 0.44, 0.66, t, 7.5, False, BODY)
    ny += 0.78

right_head(s2, CX, ny + 0.10, CW, "适用场景")
rows2 = [
    ("流程稳定、需长期维护的量产线", "适用"),
    ("测试项由工艺定义、软件仅作组织", "适用"),
    ("多品种小批量、换型频繁", "适用"),
    ("频繁变更的研发验证阶段", "受限"),
    ("仅需轻量单工位执行的小型工位", "受限"),
]
ry = ny + 0.46
for i, (a, b) in enumerate(rows2):
    rect(s2, CX, ry, CW, 0.38, WHITE if i % 2 else FILL, RULE, 0.5)
    txt(s2, CX + 0.10, ry + 0.10, CW - 1.10, 0.20, a, 7.2, False, BODY)
    txt(s2, CX + CW - 0.86, ry + 0.10, 0.76, 0.20, b, 7.2, True,
        RED if b == "受限" else INK, PP_ALIGN.RIGHT)
    ry += 0.38

txt(s2, CX, ry + 0.08, CW, 0.42,
    "「受限」指需额外定制或授权调整，并非不能实现。", 6.7, False, DIM)
foot(s2, "资料来源：NI 公开产品资料与文档；四层划分与适用性判断为本文归纳，非厂商官方分类。")

prs.save(OUT)

p = Presentation(OUT)
pics = oob = ov = 0
for si, sl in enumerate(p.slides):
    bx = []
    for sh in sl.shapes:
        if sh.shape_type == 13:
            pics += 1
        try:
            l, t = sh.left.inches, sh.top.inches
            w, h = sh.width.inches, sh.height.inches
        except Exception:
            continue
        if l < -0.01 or t < -0.01 or l + w > SW + 0.01 or t + h > SH + 0.01:
            oob += 1
            print(f"  越界 p{si+1}: ({l:.2f},{t:.2f},{w:.2f}x{h:.2f})")
        if sh.has_text_frame and sh.text_frame.text.strip():
            bx.append((l, t, w, h, sh.text_frame.text[:12]))
    for i in range(len(bx)):
        for j in range(i + 1, len(bx)):
            a, b = bx[i], bx[j]
            ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            if ox > 0.08 and oy > 0.08:
                ov += 1
                print(f"  重叠 p{si+1}: 「{a[4]}」×「{b[4]}」{ox:.2f}x{oy:.2f}")
print(f"页数 {len(p.slides)}｜图片 {pics}｜越界 {oob}｜文本重叠 {ov}｜{os.path.getsize(OUT)} B")
