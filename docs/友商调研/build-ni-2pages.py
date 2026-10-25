# -*- coding: utf-8 -*-
"""NI 方案两页：顶部小组件图 + 系统拓扑 + 组件作用 + 友商特点"""
import os
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt
from pptx.oxml.ns import qn
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(HERE, "ni_img")
OUT = os.path.join(HERE, "NI-ATE方案两页.pptx")

SW, SH = 13.333, 7.5
INK = RGBColor(0x18, 0x18, 0x1A)
MUT = RGBColor(0x6C, 0x6A, 0x66)
RED = RGBColor(0x8B, 0x1A, 0x1A)
BLUE = RGBColor(0x0F, 0x4C, 0x81)
GREEN = RGBColor(0x2F, 0x6B, 0x57)
AMBER = RGBColor(0xB0, 0x76, 0x1C)
PURPLE = RGBColor(0x5C, 0x3A, 0x7A)
TEAL = RGBColor(0x20, 0x60, 0x6A)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LINE = RGBColor(0xD8, 0xD6, 0xD0)
SOFT = RGBColor(0xFA, 0xFA, 0xF8)
CREAM = RGBColor(0xFF, 0xF9, 0xEE)

# ── 小图块：统一裁成 1.86:1，避免比例不一
TILE_AR = 1.86


def tile(src, dst, focus=0.5, zoom=1.0):
    im = Image.open(os.path.join(IMG, src)).convert("RGB")
    w, h = im.size
    tw, th = w / zoom, w / zoom / TILE_AR
    if th > h:
        th = h / zoom
        tw = th * TILE_AR
    left = max(0, min(w - tw, w * focus - tw / 2))
    top = max(0, (h - th) * 0.5)
    out = os.path.join(IMG, dst)
    crop = im.crop((int(left), int(top), int(left + tw), int(top + th)))
    if dst.lower().endswith(".jpg"):
        crop.save(out, "JPEG", quality=88, optimize=True)
    else:
        crop.save(out, "PNG", optimize=True)


tile("teststand_hero.png", "tile_teststand.jpg", 0.45, 1.15)
tile("pxi_config.png", "tile_pxi.jpg", 0.55, 1.10)
tile("labview_hero.png", "tile_labview.jpg", 0.50, 1.05)
tile("pxi_config.png", "tile_pxi2.jpg", 0.30, 1.25)
tile("teststand_hero.png", "tile_teststand2.jpg", 0.70, 1.20)
tile("labview_hero.png", "tile_labview2.jpg", 0.28, 1.10)

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


def txt(sl, x, y, w, h, text, size=8, bold=False, color=INK,
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


def box(sl, x, y, w, h, fill, line=None, radius=0.08, lw=1.0):
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


def arrow(sl, x, y, w, h, color=MUT, shape=MSO_SHAPE.RIGHT_ARROW):
    shp = sl.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.shadow.inherit = False
    shp.fill.solid()
    shp.fill.fore_color.rgb = color
    shp.line.fill.background()
    shp.text_frame.text = ""
    return shp


def header(sl, title, sub, page=None):
    box(sl, 0, 0, SW, 0.08, RED, None, 0.0)
    txt(sl, 0.42, 0.20, 12.0, 0.38, title, 18, True, INK)
    txt(sl, 0.44, 0.62, 11.8, 0.24, sub, 9, False, MUT)
    if page:
        txt(sl, 12.55, 7.20, 0.40, 0.18, f"{page}", 7.5, False, MUT, PP_ALIGN.RIGHT)


def footer(sl, s):
    txt(sl, 0.42, 7.18, 11.9, 0.20, s, 6.8, False, RGBColor(0x9A, 0x98, 0x92))


def strip(sl, items, y=1.00, h=1.02, gap=0.16, x0=0.42, cap=0.22):
    """顶部组件小图条：items = [(图片或 None, 标题, 说明, 颜色)]"""
    n = len(items)
    w = (SW - x0 * 2 - gap * (n - 1)) / n
    for i, (pic, name, note, col) in enumerate(items):
        x = x0 + i * (w + gap)
        if pic:
            p = os.path.join(IMG, pic)
            sl.shapes.add_picture(p, Inches(x), Inches(y), width=Inches(w), height=Inches(h))
            box(sl, x, y, w, h, None, LINE, 0.03, 0.9)
        else:
            box(sl, x, y, w, h, SOFT, col, 0.05, 1.2)
            txt(sl, x + 0.08, y + 0.08, w - 0.16, 0.20, name, 7.6, True, col)
            # 迷你 pin map 表（示意）
            rows, cols = 4, 3
            tx, ty = x + 0.10, y + 0.34
            cw, rh = (w - 0.20) / cols, 0.14
            for r in range(rows):
                for c in range(cols):
                    box(sl, tx + c * cw, ty + r * rh, cw - 0.02, rh - 0.02,
                        WHITE if (r + c) % 2 else RGBColor(0xEE, 0xF2, 0xF6), LINE, 0.02, 0.5)
            txt(sl, x + 0.08, y + h - 0.16, w - 0.16, 0.14, "Site × Pin 表", 6.2, False, MUT)
        txt(sl, x, y + h + 0.02, w, 0.18,
            f"{name}　{note}" if note else name, 6.8, False, RGBColor(0x8A, 0x88, 0x82))


# ═══════════════════════════ P1：拓扑
s1 = prs.slides.add_slide(BLANK)
header(s1, "NI 的 ATE 方案：一个机箱、一套软件、两条回路",
       "软件全部围绕「序列」组织：TestStand 调度、LabVIEW 测量、TSM 把 site 与引脚接进来、PXI 提供仪器", 1)

strip(s1, [
    ("tile_teststand.jpg", "TestStand", "序列编辑器", RED),
    ("tile_labview.jpg", "LabVIEW", "图形化测量代码", BLUE),
    (None, "TSM", "多工位 pin map（示意）", AMBER),
    ("tile_pxi.jpg", "PXI + InstrumentStudio", "机箱与仪器面板", TEAL),
])

# ── 拓扑
top = 2.44
box(s1, 0.42, top, 12.49, 3.34, WHITE, LINE, 0.05, 1.0)
txt(s1, 0.56, top + 0.08, 3.0, 0.20, "系统拓扑（单站点，多 site 在同一机箱内并行）", 7.6, True, MUT)

# 中心层
box(s1, 0.60, top + 0.34, 4.45, 0.52, RGBColor(0xF6, 0xF1, 0xEA), AMBER, 0.06, 1.0)
txt(s1, 0.72, top + 0.40, 4.2, 0.18, "中心层：程序库与数据", 7.4, True, AMBER)
txt(s1, 0.72, top + 0.60, 4.2, 0.18, "SystemLink（部署/资产/结果）· DIAdem（分析报告）· MES", 6.6, False, MUT)

box(s1, 5.25, top + 0.34, 3.55, 0.52, RGBColor(0xF6, 0xF1, 0xEA), AMBER, 0.06, 1.0)
txt(s1, 5.37, top + 0.40, 3.3, 0.18, "程序与配置下发", 7.4, True, AMBER)
txt(s1, 5.37, top + 0.60, 3.3, 0.18, ".seq + pin map + 限值表", 6.6, False, MUT)

box(s1, 9.00, top + 0.34, 3.72, 0.52, RGBColor(0xF6, 0xF1, 0xEA), AMBER, 0.06, 1.0)
txt(s1, 9.12, top + 0.40, 3.5, 0.18, "结果回流", 7.4, True, AMBER)
txt(s1, 9.12, top + 0.60, 3.5, 0.18, "结果库 / STDF → 看板与追溯", 6.6, False, MUT)

arrow(s1, 2.55, top + 0.90, 0.22, 0.16, RGBColor(0xC8, 0xC2, 0xB6), MSO_SHAPE.DOWN_ARROW)
arrow(s1, 10.85, top + 0.90, 0.22, 0.16, RGBColor(0xC8, 0xC2, 0xB6), MSO_SHAPE.UP_ARROW)

# 站点层
box(s1, 0.60, top + 1.08, 6.60, 0.74, RGBColor(0xF2, 0xF5, 0xF8), BLUE, 0.05, 1.3)
txt(s1, 0.74, top + 1.14, 6.2, 0.20, "站点层：工控机 / PXI 嵌入式控制器", 7.8, True, BLUE)
txt(s1, 0.74, top + 1.36, 6.2, 0.18,
    "TestStand Engine（主线程 + 每 site 一线程）· TSM（site / pin map / binning）· LabVIEW Runtime", 6.5, False, MUT)
txt(s1, 0.74, top + 1.56, 6.2, 0.18, "NI MAX（发现与配置）· TestStand Operator Interface（操作界面）", 6.5, False, MUT)

box(s1, 7.40, top + 1.08, 5.32, 0.74, RGBColor(0xF2, 0xF5, 0xF8), BLUE, 0.05, 1.3)
txt(s1, 7.54, top + 1.14, 5.0, 0.20, "并行模型", 7.8, True, BLUE)
txt(s1, 7.54, top + 1.36, 5.0, 0.18, "Batch：各 site 同步推进；Parallel：各 site 独立走完", 6.5, False, MUT)
txt(s1, 7.54, top + 1.56, 5.0, 0.18, "每个 site 复制一份序列副本 → 变量空间互不干扰", 6.5, False, MUT)

arrow(s1, 3.85, top + 1.84, 0.22, 0.16, RGBColor(0xC8, 0xC2, 0xB6), MSO_SHAPE.DOWN_ARROW)

# 仪器层
box(s1, 0.60, top + 2.02, 4.10, 0.70, WHITE, TEAL, 0.05, 1.2)
txt(s1, 0.72, top + 2.08, 3.9, 0.20, "PXIe 机箱（18 槽）", 7.6, True, TEAL)
txt(s1, 0.72, top + 2.30, 3.9, 0.36, "数字 / 模拟 / RF 模块\n时钟与触发背板路由", 6.4, False, MUT)

box(s1, 4.86, top + 2.02, 3.60, 0.70, WHITE, TEAL, 0.05, 1.2)
txt(s1, 4.98, top + 2.08, 3.4, 0.20, "开关与矩阵", 7.6, True, TEAL)
txt(s1, 4.98, top + 2.30, 3.4, 0.36, "Switch Executive 配路径\n共享资源互斥", 6.4, False, MUT)

box(s1, 8.62, top + 2.02, 4.10, 0.70, WHITE, TEAL, 0.05, 1.2)
txt(s1, 8.74, top + 2.08, 3.9, 0.20, "外挂仪器", 7.6, True, TEAL)
txt(s1, 8.74, top + 2.30, 3.9, 0.36, "LXI / GPIB / USB\n统一走 NI-VISA 会话", 6.4, False, MUT)

arrow(s1, 2.60, top + 2.76, 0.22, 0.16, RGBColor(0xC8, 0xC2, 0xB6), MSO_SHAPE.DOWN_ARROW)

# 被测层
box(s1, 0.60, top + 2.94, 5.90, 0.30, RGBColor(0xEF, 0xF4, 0xEF), GREEN, 0.10, 1.2)
txt(s1, 0.72, top + 2.98, 5.6, 0.22, "夹具 / 针床 / DUT × N（site 0…n）　·　Handler 上下料", 6.8, True, GREEN)
box(s1, 6.72, top + 2.94, 6.00, 0.30, RGBColor(0xF7, 0xF2, 0xF2), RED, 0.10, 1.2)
txt(s1, 6.84, top + 2.98, 5.7, 0.22, "两条回路：程序与结果走网络（秒级）；仪器动作走背板（微秒级）", 6.8, True, RED)

txt(s1, 0.42, 5.86, 12.49, 0.24,
    "拓扑要点：① 一站点一机箱，多 site 靠同一机箱内的并行线程，不靠多台 PC；② 共享仪器是并行的唯一硬约束；"
    "③ 中心只发程序与收结果，不参与实时回路。", 7.2, False, RGBColor(0x44, 0x42, 0x3E))
footer(s1, "图注：TestStand / LabVIEW / PXI 图为 ni.com 产品页配图；TSM 图块为 pin map 结构示意，非软件截图")

# ═══════════════════════════ P2：组件作用 + 友商特点
s2 = prs.slides.add_slide(BLANK)
header(s2, "组件各管一段，边界清楚才是可换型的前提",
       "九个组件、三段责任：执行（TestStand / TSM）、测量（LabVIEW / 仪器）、数据（SystemLink / DIAdem）", 2)

strip(s2, [
    ("tile_pxi2.jpg", "InstrumentStudio", "通道与量程试探", TEAL),
    ("tile_teststand2.jpg", "TestStand", "结果与流程模型", RED),
    ("tile_labview2.jpg", "LabVIEW", "VI 与前面板", BLUE),
    (None, "NI MAX / VISA", "发现与驱动（示意）", GREEN),
], y=0.96, h=0.84)

rows = [
    ("TestStand", "序列引擎与执行器：解释 step、跑流程模型、收结果", "执行", RED),
    ("TSM", "半导体扩展：site 抽象、pin map、多工位复用、binning/STDF", "执行", RED),
    ("LabVIEW", "测量代码本体：一个 step 一个 VI，采集与判定", "测量", BLUE),
    ("InstrumentStudio", "仪器软面板：调试期试探参数、确认通道，量产不用", "测量", BLUE),
    ("Switch Executive", "开关与矩阵路径配置、共享资源互斥", "测量", BLUE),
    ("NI-VISA / 488.2", "统一会话层：LAN / GPIB / USB 同一套 API", "测量", BLUE),
    ("NI-DAQmx", "传感器与模拟量采集、定时与触发", "测量", BLUE),
    ("PXI Platform Services / MAX", "机箱模块发现、时钟触发路由、自检与固件", "平台", TEAL),
    ("SystemLink / DIAdem", "机群与数据管理、离线分析与报告生成", "数据", PURPLE),
]
ty = 2.26
txt(s2, 0.42, 2.06, 6.0, 0.18, "组件作用（按责任段排列）", 7.6, True, MUT)
box(s2, 0.42, ty, 8.15, 0.26, RGBColor(0xEE, 0xEC, 0xE6), None, 0.04)
for j, (h, xx, ww) in enumerate([("组件", 0.54, 2.10), ("作用", 2.62, 4.70), ("段", 7.42, 0.90)]):
    txt(s2, xx, ty + 0.04, ww, 0.18, h, 6.8, True, MUT)
for i, (a, b, c, col) in enumerate(rows):
    ry = ty + 0.26 + i * 0.385
    box(s2, 0.42, ry, 8.15, 0.36, WHITE if i % 2 == 0 else SOFT, LINE, 0.05, 0.7)
    box(s2, 0.42, ry, 0.035, 0.36, col, None, 0.0)
    txt(s2, 0.54, ry + 0.09, 2.02, 0.20, a, 6.9, True, INK)
    txt(s2, 2.62, ry + 0.09, 4.66, 0.20, b, 6.6, False, RGBColor(0x3A, 0x38, 0x34))
    txt(s2, 7.42, ry + 0.09, 0.90, 0.20, c, 6.9, True, col)

# 右侧：责任链 + 边界
txt(s2, 8.80, 2.06, 4.1, 0.18, "一次运行的责任链", 7.6, True, MUT)
chain = [("① MAX 配置", "机箱 / 模块 / 触发", TEAL),
         ("② TestStand 起序", "选模型，进 PreUUT", RED),
         ("③ TSM 分 site", "复制序列到线程", RED),
         ("④ LabVIEW + 驱动", "VI 调 VISA / DAQmx", BLUE),
         ("⑤ 判 bin 归档", "结果库 / STDF", PURPLE)]
cy = 2.30
for a, b, col in chain:
    box(s2, 8.80, cy, 4.10, 0.50, WHITE, LINE, 0.06, 0.8)
    box(s2, 8.80, cy, 0.035, 0.50, col, None, 0.0)
    txt(s2, 8.94, cy + 0.06, 3.9, 0.18, a, 7.2, True, col)
    txt(s2, 8.94, cy + 0.26, 3.9, 0.18, b, 6.4, False, MUT)
    cy += 0.56

box(s2, 8.80, cy + 0.02, 4.10, 0.62, CREAM, AMBER, 0.06, 1.1)
txt(s2, 8.93, cy + 0.08, 3.85, 0.50,
    "边界：调试工具（InstrumentStudio）与量产执行（TestStand 序列）是两套东西，混用会成为隐患。",
    6.5, False, RGBColor(0x4A, 0x3A, 0x1E), spacing=1.15)

# 底部：友商特点
py = 6.02
box(s2, 0.42, py, 12.49, 1.06, RGBColor(0xF7, 0xF4, 0xF0), RGBColor(0xDE, 0xD8, 0xCE), 0.05, 1.0)
txt(s2, 0.56, py + 0.08, 2.2, 0.20, "友商特点（NI）", 7.8, True, RED)
feats = [
    ("分层卖", "仪器、驱动、执行、数据分四段，任一段可换"),
    ("以序列为中心", "流程模型固化 UUT 循环与 binning，测试者只写 step"),
    ("site 是一等公民", "TSM 把多工位做成配置，而不是改代码"),
    ("生态即护城河", "模块 + 驱动 + 认证工程师，替换成本在生态不在代码"),
]
fw = 12.49 / 4
for i, (h, s) in enumerate(feats):
    fx = 0.56 + i * (fw + 0.02)
    txt(s2, fx, py + 0.30, fw - 0.10, 0.18, h, 7.2, True, INK)
    txt(s2, fx, py + 0.50, fw - 0.10, 0.48, s, 6.4, False, RGBColor(0x4C, 0x4A, 0x46), spacing=1.15)

footer(s2, "组件作用依据 NI 官方文档与产品页；「友商特点」为本文归纳，用于选型对照而非评价优劣")

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
            boxes.append((l, t, w, h, sh.text_frame.text[:10]))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            if ox > 0.08 and oy > 0.08:
                ov += 1
                print(f"  重叠 p{si+1}: 「{a[4]}」×「{b[4]}」{ox:.2f}x{oy:.2f}")
print(f"页数 {len(p.slides)}｜图片 {pics}｜越界 {oob}｜文本重叠 {ov}｜{os.path.getsize(OUT)} B")
