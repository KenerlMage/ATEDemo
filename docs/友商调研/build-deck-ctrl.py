# -*- coding: utf-8 -*-
"""半导体 ATE 控制架构（6 页，含两张自制图）"""
import os
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt
from pptx.oxml.ns import qn

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "半导体ATE控制架构.pptx")

SW, SH = 13.333, 7.5
INK = RGBColor(0x18, 0x18, 0x1A)
MUT = RGBColor(0x6E, 0x6C, 0x68)
RED = RGBColor(0x8B, 0x1A, 0x1A)
BLUE = RGBColor(0x0F, 0x4C, 0x81)
GREEN = RGBColor(0x2F, 0x6B, 0x57)
AMBER = RGBColor(0xB0, 0x76, 0x1C)
PURPLE = RGBColor(0x5C, 0x3A, 0x7A)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LINE = RGBColor(0xD6, 0xD4, 0xCE)
SOFT = RGBColor(0xF8, 0xF8, 0xF6)
BAND = RGBColor(0xF7, 0xF4, 0xF0)
CREAM = RGBColor(0xFF, 0xF8, 0xEC)

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
    for i, ln in enumerate(str(text).split("\n")):
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


def header(sl, title, sub, page=None):
    box(sl, 0, 0, SW, 0.10, RED, None, 0.0)
    txt(sl, 0.55, 0.32, 11.6, 0.52, title, 24, True, INK)
    txt(sl, 0.57, 0.92, 11.9, 0.32, sub, 11.5, False, MUT)
    if page:
        txt(sl, 12.40, 7.12, 0.50, 0.24, f"{page}", 9, False, MUT, PP_ALIGN.RIGHT)


def footer(sl, s):
    txt(sl, 0.55, 7.10, 11.7, 0.26, s, 8, False, RGBColor(0x96, 0x94, 0x8E))


IW = 5.60 * 16 / 9

# ─────────────────────────── P1 结论先行
s1 = prs.slides.add_slide(BLANK)
header(s1, "半导体 ATE 的控制架构：不是一个问题，是两个问题",
       "「几台分布式工控机箱」与「中心下发任务」不是二选一——它们分别管实时与调度", 1)

box(s1, 0.55, 1.44, 6.05, 2.30, WHITE, LINE, 0.06, 1.0)
txt(s1, 0.75, 1.58, 5.6, 0.26, "直觉 A：分布式机箱为主", 11.5, True, BLUE)
txt(s1, 0.75, 1.92, 5.6, 1.66,
    "多台工控机箱各自带控制器与仪器，\n"
    "并行跑各自的被测件。\n\n"
    "对的部分：实时回路必须在机箱里。\n"
    "错的部分：它不是「主导」，只是执行面。",
    9.5, False, RGBColor(0x3A, 0x42, 0x4C), spacing=1.25)

box(s1, 6.75, 1.44, 6.05, 2.30, WHITE, LINE, 0.06, 1.0)
txt(s1, 6.95, 1.58, 5.6, 0.26, "直觉 B：中心下发任务为主", 11.5, True, AMBER)
txt(s1, 6.95, 1.92, 5.6, 1.66,
    "调度服务/MES 决定测什么、用哪版程序、\n"
    "结果收到哪，再分给各站。\n\n"
    "对的部分：换型、追溯、版本管理靠它。\n"
    "错的部分：它不能下发每一条指令。",
    9.5, False, RGBColor(0x4A, 0x40, 0x30), spacing=1.25)

box(s1, 0.55, 3.94, 12.25, 1.02, CREAM, AMBER, 0.06, 1.4)
txt(s1, 0.78, 4.06, 11.8, 0.80,
    "结论：控制面集中、数据面本地。\n"
    "中心只说「做什么」，工控机决定「每微秒怎么做」——量产线（NI STS、OSAT、Advantest 机台）无一例外。",
    11.5, True, RGBColor(0x5A, 0x42, 0x1E), spacing=1.25)

txt(s1, 0.55, 5.14, 12.25, 0.26, "两种职责的分界", 11.5, True, INK)
rows1 = [("任务令与程序版本", "中心：谁测什么、用哪版程序", "工控机：只接收，不自行改版本"),
         ("实时执行", "中心：不参与", "工控机：序列解释、仪器时序、超时与联锁"),
         ("结果与追溯", "中心：汇总、归档、对 MES", "工控机：本地落盘，断网可续"),
         ("换型与并发", "中心：排队与分配", "工控机：单站内多 site 并行")]
box(s1, 0.55, 5.44, 12.25, 0.30, RGBColor(0xEE, 0xEC, 0xE6), None, 0.05)
for j, h in enumerate(["职责", "中心 / 调度层", "工控机 / 实时层"]):
    txt(s1, 0.70 + j * 4.05, 5.49, 3.9, 0.22, h, 8.5, True, MUT)
for i, (a, b, c) in enumerate(rows1):
    ry = 5.74 + i * 0.30
    txt(s1, 0.70, ry + 0.04, 3.4, 0.22, a, 8.5, True, INK)
    txt(s1, 4.75, ry + 0.04, 3.9, 0.22, b, 8.5, False, MUT)
    txt(s1, 8.80, ry + 0.04, 3.9, 0.22, c, 8.5, False, RGBColor(0x2C, 0x40, 0x34))
footer(s1, "口径：本节讨论「产线怎么组织测试」，不涉及 per-pin 时序精度等硬件指标")

# ─────────────────────────── P2 三形态（贴图）
s2 = prs.slides.add_slide(BLANK)
header(s2, "三种控制形态：调度放在哪一层",
       "区别不在算力，而在实时回路与任务下发是否同一个进程", 2)
s2.shapes.add_picture(os.path.join(HERE, "ATE三种控制形态.png"),
                      Inches((SW - IW) / 2), Inches(1.20), width=Inches(IW))
txt(s2, 0.55, 6.86, 12.25, 0.26,
    "形态 B 是量产主流：中心只下发任务与程序版本，实时回路留在各站工控机箱，单站故障不扩散。", 9, False, MUT)
footer(s2, "图：ATE三种控制形态.png（自制，2400×1350）")

# ─────────────────────────── P3 NI 的分工
s3 = prs.slides.add_slide(BLANK)
header(s3, "NI 路线拆解：TestStand 管调度，LabVIEW 管测量",
       "四个组件各司其职——把「通用执行器」变成「半导体执行器」的是 TSM", 3)

rows3 = [("TestStand", "序列引擎：解释执行 step、管理流程与结果容器", "每站一份；主线程 + 每 site 一条线程", RED),
         ("Process Model", "Test UUTs / Batch / Parallel：初始化、UUT 循环、收尾、binning", "决定并发模型；Batch 要求各 site 同步", AMBER),
         ("TSM 半导体模块", "site 与 pin map 抽象、多工位程序复用、STDF 落盘", "把通用引擎接上半导体语义", BLUE),
         ("LabVIEW", "测量代码：一个 step 对应一个 VI", "被引擎调用，自己不调度", GREEN),
         ("Switch Executive", "开关/矩阵布线与路径互斥", "共享资源仲裁，避免串扰", PURPLE),
         ("Operator Interface", "操作界面、结果查询、权限", "独立进程，可断开重连", MUT)]
for i, (a, b, c, col) in enumerate(rows3):
    ry = 1.42 + i * 0.86
    box(s3, 0.55, ry, 12.25, 0.76, WHITE, LINE, 0.07, 0.9)
    box(s3, 0.55, ry, 0.06, 0.76, col, None, 0.0)
    txt(s3, 0.74, ry + 0.10, 2.5, 0.24, a, 10, True, col)
    txt(s3, 3.30, ry + 0.10, 4.6, 0.56, b, 8.5, False, RGBColor(0x36, 0x34, 0x30), spacing=1.15)
    txt(s3, 8.05, ry + 0.10, 4.6, 0.56, c, 8.5, False, MUT, spacing=1.15)

box(s3, 0.55, 6.62, 12.25, 0.44, BAND, RGBColor(0xE0, 0xDA, 0xD0), 0.08, 1.0)
txt(s3, 0.75, 6.72, 11.9, 0.26,
    "关键：Batch / Parallel 模型会为每个 site 复制一份序列到新线程——多工位是「同一程序复制执行」，不是「一个程序轮流跑」。",
    9, False, RGBColor(0x4A, 0x46, 0x40))
footer(s3, "依据：NI TestStand 文档（Testing Multiple Sites in Parallel）、TSM 用户手册（多工位 pin map、binning、STDF）")

# ─────────────────────────── P4 调用链（贴图）
s4 = prs.slides.add_slide(BLANK)
header(s4, "TestStand → LabVIEW：一次多工位测试怎么跑起来",
       "左边是分层与进程边界，右边是三个 site 的线程与共享资源时序", 4)
s4.shapes.add_picture(os.path.join(HERE, "TestStand到LabVIEW调用链.png"),
                      Inches((SW - IW) / 2), Inches(1.20), width=Inches(IW))
txt(s4, 0.55, 6.86, 12.25, 0.26,
    "共享电源与开关矩阵是并行的唯一硬约束：分时或互斥没做好，多站测值会互相污染。", 9, False, MUT)
footer(s4, "图：TestStand到LabVIEW调用链.png（自制，2400×1350）")

# ─────────────────────────── P5 其他厂商
s5 = prs.slides.add_slide(BLANK)
header(s5, "其他厂商怎么组织：机台在内，调度在外",
       "专用 ATE 把实时做到每引脚，但任务下发与数据上送同样走中心", 5)

heads5 = ["", "控制形态", "程序与执行", "多站方式", "对外接口"]
colx = [0.55, 2.55, 5.15, 7.95, 10.35]
box(s5, 0.55, 1.42, 12.25, 0.34, RGBColor(0xEE, 0xEC, 0xE6), None, 0.05)
for j, h in enumerate(heads5):
    if h:
        txt(s5, colx[j] + 0.10, 1.47, 2.4, 0.22, h, 8.5, True, MUT)
rows5 = [
    ("Advantest V93000", "测试头 + 工作站；每引脚自带测试处理器，水冷，计算与接口随卡走",
     "SmarTest 程序；编译后下发到卡上执行", "同测数由卡与通道数决定，硬件并行",
     "SECS/GEM 对接线控；STDF 落盘"),
    ("Teradyne UltraFLEX", "测试头 + 控制器；通用插槽，任意仪器插任意槽",
     "IG-XL（表格驱动）；单站程序可自动转多站", "同测数随 UltraFLEXplus 提升",
     "SECS/GEM、STDF、离线数据分析"),
    ("NI STS（T1/T2/T4）", "1/2/4 个 18 槽 PXI 机箱，各带嵌入式控制器",
     "TestStand + TSM；同一套程序按 site 数缩放", "Batch / Parallel 复制序列到多线程",
     "TestStand 结果库、STDF 导出"),
    ("OSAT 量产线", "Tester + Handler + 线控 EAP；机台不直接连 MES",
     "机台程序由 EAP 下发与版本管理", "多工位由 Handler 决定，机台按 site 判 bin",
     "SECS/GEM（E5/E30/E37/E40）、Map 文件"),
]
for i, row in enumerate(rows5):
    ry = 1.80 + i * 1.20
    box(s5, 0.55, ry, 12.25, 1.10, WHITE, LINE, 0.06, 0.9)
    txt(s5, 0.66, ry + 0.12, 1.85, 0.86, row[0], 9.5, True, INK, spacing=1.15)
    for j in range(1, 5):
        txt(s5, colx[j] + 0.10, ry + 0.12, (colx[j + 1] - colx[j] - 0.20) if j < 4 else 2.4,
            0.86, row[j], 8.3, False, RGBColor(0x3A, 0x38, 0x34), spacing=1.18)

box(s5, 0.55, 6.62, 12.25, 0.44, BAND, RGBColor(0xE0, 0xDA, 0xD0), 0.08, 1.0)
txt(s5, 0.75, 6.72, 11.9, 0.26,
    "共同点：机台内部做实时（硬时序、并行同测），机台外部做调度（程序版本、任务排队、结果归档、与 MES 对接）。",
    9, False, RGBColor(0x4A, 0x46, 0x40))
footer(s5, "依据：Advantest V93000 产品页、Teradyne UltraFLEX / IG-XL 产品页、NI STS 与 TSM 文档、SEMI SECS/GEM 与 STDF 标准")

# ─────────────────────────── P6 对我们的取舍
s6 = prs.slides.add_slide(BLANK)
header(s6, "对我们的取舍：控制面集中，数据面本地",
       "不追求专用 ATE 的时序能力，但要把「中心—站点」的分界划清楚", 6)

txt(s6, 0.55, 1.40, 6.05, 0.26, "四条设计约束", 11.5, True, RED)
cons = [("① 控制面集中", "任务令、程序版本、结果归档只从中心下发与回收；中心不参与任何实时步骤。"),
        ("② 数据面本地", "序列执行、仪器时序、超时与安全联锁全部在工控机；断网不得停线。"),
        ("③ 站点身份", "中心只认 site id，不认机器名或 IP；换机不改任务定义。"),
        ("④ 断网可运行", "中心不可达时按已缓存程序继续跑，结果本地排队补传，恢复后对账。")]
for i, (t, s) in enumerate(cons):
    cy = 1.70 + i * 1.02
    box(s6, 0.55, cy, 6.05, 0.90, WHITE, LINE, 0.07, 0.9)
    box(s6, 0.55, cy, 0.06, 0.90, RED, None, 0.0)
    txt(s6, 0.74, cy + 0.10, 5.6, 0.24, t, 9.5, True, INK)
    txt(s6, 0.74, cy + 0.40, 5.65, 0.44, s, 8.5, False, MUT, spacing=1.15)

txt(s6, 6.95, 1.40, 5.85, 0.26, "映射到现有 ATE Runner", 11.5, True, BLUE)
maps = [("TASKS / 任务令", "已是中心下发，保留", GREEN),
        ("TPS 用例集", "对应「程序版本」，需加版本号与灰度", AMBER),
        ("ate_db / 结果归档", "已具备，需补断网补传与对账", AMBER),
        ("site 抽象", "缺失，一拖多的前提", RED),
        ("共享仪器仲裁", "缺失，多站并行时会互相污染", RED)]
for i, (a, b, col) in enumerate(maps):
    my = 1.70 + i * 0.74
    box(s6, 6.95, my, 5.85, 0.64, WHITE, LINE, 0.07, 0.9)
    d = box(s6, 6.95, my, 0.05, 0.64, col, None, 0.0)
    txt(s6, 7.12, my + 0.08, 2.1, 0.22, a, 9, True, INK)
    txt(s6, 7.12, my + 0.34, 5.5, 0.24, b, 8.3, False, MUT)

box(s6, 6.95, 5.50, 5.85, 1.52, CREAM, AMBER, 0.06, 1.2)
txt(s6, 7.15, 5.62, 5.5, 0.26, "一句话", 10, True, AMBER)
txt(s6, 7.15, 5.92, 5.5, 1.00,
    "我们不需要复刻 TestStand，但需要复刻它的分界：\n"
    "引擎在站点、编排在中心、语义（site / bin / STDF）夹在中间。",
    9.5, False, RGBColor(0x4A, 0x3A, 0x1E), spacing=1.3)
footer(s6, "结论基于前五页对比；「缺失」项与 06 篇的四个能力缺口一致")

prs.save(OUT)

p = Presentation(OUT)
pics = oob = ov = 0
for si, sl in enumerate(p.slides):
    boxes = []
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
            boxes.append((l, t, w, h, sh.text_frame.text[:14]))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            if ox > 0.06 and oy > 0.06:
                ov += 1
                print(f"  重叠 p{si+1}: 「{a[4]}」×「{b[4]}」{ox:.2f}x{oy:.2f}")
print(f"页数 {len(p.slides)}｜图片 {pics}｜越界 {oob}｜文本重叠 {ov}｜{os.path.getsize(OUT)} B")
