# -*- coding: utf-8 -*-
"""装备助手（一）：显微镜相机下的 AI 标记检测 —— 三页方案 PPT"""
import os
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt
from pptx.oxml.ns import qn
from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "装备助手-显微镜AI标记检测.pptx")

SW, SH = 13.333, 7.5
INK = RGBColor(0x1A, 0x1A, 0x1A)
BODY = RGBColor(0x2E, 0x2E, 0x2E)
MUT = RGBColor(0x6E, 0x6C, 0x68)
DIM = RGBColor(0x93, 0x91, 0x8C)
RED = RGBColor(0x8B, 0x1A, 0x1A)
BLUE = RGBColor(0x1F, 0x3F, 0x66)
BLUE2 = RGBColor(0x1B, 0x5E, 0xA8)
TEAL = RGBColor(0x0F, 0x5E, 0x5A)
RULE = RGBColor(0xAD, 0xA8, 0x9C)
GRID = RGBColor(0xC6, 0xC1, 0xB5)
FILL2 = RGBColor(0xF1, 0xF3, 0xF6)
GRAY = RGBColor(0xED, 0xED, 0xED)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
HDR = RGBColor(0xEE, 0xEC, 0xE6)
WIRE = RGBColor(0x6E, 0x6A, 0x62)

prs = Presentation()
prs.slide_width = Inches(SW)
prs.slide_height = Inches(SH)


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


class Page:
    def __init__(self, no, total, title, subtitle):
        self.sl = prs.slides.add_slide(prs.slide_layouts[6])
        self.txt(0.56, 0.16, 7.90, 0.34, title, 19, True, INK)
        self.txt(8.56, 0.24, 4.20, 0.22, subtitle, 8.0, False, DIM, PP_ALIGN.RIGHT)
        self.hline(0.56, 0.54, 12.20, RULE, 0.014)
        self.txt(12.30, 7.15, 0.50, 0.20, f"{no} / {total}", 7.6, False, DIM, PP_ALIGN.RIGHT)

    # ── 基础元件
    def txt(self, x, y, w, h, text, size=8, bold=False, color=BODY,
            align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, ls=1.14):
        tb = self.sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
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

    def hi(self, x, y, w, h, text, size=6.2, color=BODY, accent=BLUE2,
           bold=True, ls=1.20, align=PP_ALIGN.LEFT):
        tb = self.sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        p = tf.paragraphs[0]
        p.alignment = align
        p.line_spacing = ls
        for i, seg in enumerate(str(text).split("**")):
            if not seg:
                continue
            on = (i % 2 == 1)
            r = p.add_run()
            r.text = seg
            _font(r, size, bold if on else False, accent if on else color)
        return tb

    def rect(self, x, y, w, h, fill=WHITE, line=RULE, lw=0.75):
        s = self.sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y),
                                     Inches(w), Inches(h))
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

    def hline(self, x, y, w, color=RULE, lw=0.010):
        s = self.sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y),
                                     Inches(w), Inches(lw))
        s.shadow.inherit = False
        s.fill.solid()
        s.fill.fore_color.rgb = color
        s.line.fill.background()
        return s

    def vrule(self, x, y, h, color=RED, w=0.040):
        s = self.sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y),
                                     Inches(w), Inches(h))
        s.shadow.inherit = False
        s.fill.solid()
        s.fill.fore_color.rgb = color
        s.line.fill.background()
        return s

    def wire(self, pts, color=WIRE, lw=1.0, arrow=True, dash=False):
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            c = self.sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1),
                                             Inches(x2), Inches(y2))
            c.line.color.rgb = color
            c.line.width = Pt(lw)
            ln = c.line._get_or_add_ln()
            if dash:
                d = etree.SubElement(ln, qn("a:prstDash"))
                d.set("val", "dash")
            if arrow and i == len(pts) - 2:
                t = etree.SubElement(ln, qn("a:tailEnd"))
                t.set("type", "triangle")
                t.set("w", "med")
                t.set("len", "med")

    def head(self, x, y, w, text, color=RED, size=8.2, rule=True):
        self.txt(x, y, w, 0.20, text, size, True, color)
        if rule:
            self.hline(x, y + 0.225, w, GRID, 0.010)

    def table(self, x, y, w, cols, rows, rh=0.28, hh=0.24, fs=6.1, hfs=6.0,
              colw=None, color=BODY):
        """cols: 表头列表；rows: 二维列表；colw: 各列宽度"""
        if colw is None:
            colw = [w / len(cols)] * len(cols)
        self.rect(x, y, w, hh, HDR, None, 0)
        cx = x
        for i, c in enumerate(cols):
            self.txt(cx + 0.09, y + 0.045, colw[i] - 0.12, 0.16, c, hfs, True, MUT)
            cx += colw[i]
        ry = y + hh
        for j, row in enumerate(rows):
            if j % 2 == 1:
                self.rect(x, ry, w, rh, RGBColor(0xFA, 0xF9, 0xF6), None, 0)
            cx = x
            for i, cell in enumerate(row):
                self.hi(cx + 0.09, ry + (rh - 0.17) / 2, colw[i] - 0.12, 0.17,
                        cell, fs, color, BLUE2, True, 1.10)
                cx += colw[i]
            ry += rh
            self.hline(x, ry, w, RGBColor(0xDD, 0xD9, 0xD0), 0.006)
        return ry


# ══════════════════ 第 1 页：概念与货架 ══════════════════
p1 = Page(1, 3, "装备助手（一）：概念、货架与设计预期",
          "首个实例：显微镜相机下的 AI 标记检测")

# 概念
p1.rect(0.56, 0.64, 12.20, 0.92, GRAY, None, 0)
p1.vrule(0.56, 0.64, 0.92, RED, 0.045)
p1.hi(0.74, 0.73, 11.90, 0.78,
      "装备助手是依附于测试装备运行的轻量应用，以独立安装包分发，在设备侧完成单一而明确的增强功能；"
      "**不进入 TPS 主流程，不改动测试基线**。首个实例为显微镜相机下的标记检测：相机采集图像，"
      "**AI 识别标记的位置与状态**，输出判定与偏差量，供操作者复核或供后续工序引用；"
      "其目标是在**实际生产中先行提效**，以运行数据验证识别能力，再评估是否并入 TPS。",
      6.6, RGBColor(0x2B, 0x2B, 0x2B), BLUE2, True, 1.30)

# 左：与 TPS 的边界
p1.head(0.56, 1.66, 6.10, "与 TPS 的边界")
p1.table(0.56, 1.92, 6.10, ["维度", "装备助手", "TPS"],
         [["运行方式", "设备侧独立进程，随工位启动", "由测试执行引擎调度"],
          ["版本变更", "独立版本，自主下载与发布", "随基线发布，需回归"],
          ["失效影响", "不阻断测试流程，可即时停用", "影响测试结论与判定"]],
         rh=0.30, hh=0.24, fs=6.0, colw=[1.10, 2.70, 2.30])

# 左：为何先做助手
p1.head(0.56, 3.14, 6.10, "为何先做助手而非 TPS")
bullets = [
    ("不触碰基线", "测试程序与判定逻辑保持不变，装备助手只提供增强信息。"),
    ("迭代可独立", "算法与界面独立发布，无需触发整线回归与产线停机。"),
    ("风险可回退", "异常时停用或卸载即可，不影响工位正常测试。"),
    ("数据可积累", "先在生产中累积样本与判定记录，为是否并入 TPS 提供依据。"),
]
for i, (ti, de) in enumerate(bullets):
    y = 3.40 + i * 0.40
    p1.txt(0.56, y, 0.26, 0.16, "0" + str(i + 1), 6.4, True, RGBColor(0xC9, 0xC4, 0xB8))
    p1.txt(0.86, y - 0.005, 1.30, 0.17, ti, 6.6, True, INK)
    p1.txt(2.20, y, 4.46, 0.34, de, 6.1, False, BODY, ls=1.16)

# 左：设计预期
p1.head(0.56, 5.10, 6.10, "设计预期（初版目标，现场基线测定后校准）")
metrics = [
    ("单件检测节拍", "≤ 8 s／件", "含采图、识别与判定"),
    ("人工复核率", "≤ 10 %", "仅异常与低置信度进入复核"),
    ("漏检率 / 误检率", "≤ 0.5 % / ≤ 3 %", "以现场样本统计"),
    ("单班节省工时", "≈ 2 人·时", "按现有目检方式折算"),
]
for i, (k, v, n) in enumerate(metrics):
    cx = 0.56 + (i % 2) * 3.12
    cy = 5.38 + (i // 2) * 0.50
    p1.rect(cx, cy, 3.00, 0.44, WHITE, GRID, 0.7)
    p1.rect(cx, cy, 0.030, 0.44, BLUE, None, 0)
    p1.txt(cx + 0.12, cy + 0.055, 1.30, 0.15, k, 5.8, False, MUT)
    p1.txt(cx + 0.12, cy + 0.215, 1.30, 0.17, n, 5.4, False, DIM)
    p1.txt(cx + 1.62, cy + 0.10, 1.28, 0.20, v, 7.4, True, BLUE2, PP_ALIGN.RIGHT)

# 右：货架设计
RX, RW = 6.90, 5.86
p1.head(RX, 1.66, RW, "货架设计：条目字段与检索方式")
p1.table(RX, 1.92, RW, ["字段", "说明"],
         [["名称／能力", "标记检测、计数、位置偏差"],
          ["版本与变更", "语义化版本，附变更说明与发布日期"],
          ["适用装备", "装备型号、工位类型、相机型号"],
          ["校验与签名", "SHA-256 校验值，安装前强制校验"],
          ["发布者", "发布者标识与联系方式，便于追溯"]],
         rh=0.26, hh=0.24, fs=5.9, colw=[1.30, 4.56])
p1.txt(RX, 3.42, RW, 0.18,
       "检索：按装备型号与相机型号筛选，条目内展示版本历史与能力说明。", 5.9, False, MUT)

# 右：下载方式
p1.head(RX, 3.72, RW, "下载方式")
steps = ["浏览货架", "按装备筛选", "下载安装包", "校验并安装", "生成运行记录"]
sw = 1.02
for i, s in enumerate(steps):
    x = RX + i * (sw + 0.19)
    p1.rect(x, 3.98, sw, 0.40, FILL2, GRID, 0.7)
    p1.txt(x, 4.08, sw, 0.20, s, 5.9, True, BODY, PP_ALIGN.CENTER)
    if i < 4:
        p1.wire([(x + sw, 4.18), (x + sw + 0.19, 4.18)], lw=0.9)

# 右：发布与更新
p1.head(RX, 4.52, RW, "发布与更新：用户自主下载")
pub = [
    ("发布", "发布者上传安装包与说明，填写适用装备与校验值后即上架；货架不设权限控制。"),
    ("下载", "用户按需检索并自主下载安装，不依赖推送通道，离线工位可用拷贝安装。"),
    ("更新", "助手启动时检查货架版本并提示；由用户确认后执行，保留上一版本可回滚。"),
    ("留痕", "本地记录版本、校验值与安装时间，供产线版本一致性核查。"),
]
for i, (ti, de) in enumerate(pub):
    y = 4.78 + i * 0.40
    p1.txt(RX, y - 0.005, 0.44, 0.17, ti, 6.3, True, TEAL)
    p1.txt(RX + 0.48, y, 5.36, 0.36, de, 6.0, False, BODY, ls=1.16)

# ══════════════════ 第 2 页：设计方法与逻辑视图 ══════════════════
p2 = Page(2, 3, "装备助手（一）：设计方法、构建与组件接口",
          "PyInstaller 加密构建 · 四组件逻辑视图 · 接口契约")

p2.head(0.56, 0.64, 12.20, "构建与发布流程（PyInstaller 加密构建）")
build = [
    ("① 依赖锁定", "锁定 Python、pylon 运行时与算法库版本，形成可复现清单。"),
    ("② 打包策略", "采用 one-folder，保留算法动态库与模型的加载路径。"),
    ("③ 加密构建", "对字节码加密与符号混淆，密钥与产物分离保管。"),
    ("④ 完整性校验", "生成校验值与签名，安装前强制验证，防止被替换。"),
    ("⑤ 上架发布", "写入货架条目（版本、适用装备、变更说明）并归档产物。"),
]
for i, (ti, de) in enumerate(build):
    x = 0.56 + i * 2.46
    p2.rect(x, 0.92, 2.32, 0.86, WHITE, GRID, 0.8)
    p2.rect(x, 0.92, 2.32, 0.040, BLUE, None, 0)
    p2.txt(x + 0.12, 1.01, 2.08, 0.16, ti, 6.4, True, INK)
    p2.txt(x + 0.12, 1.21, 2.08, 0.52, de, 5.7, False, MUT, ls=1.18)

# 逻辑视图
p2.head(0.56, 1.94, 12.20, "逻辑视图：四组件与交互接口")
NY, NH = 2.24, 1.06
nodes = [
    (0.56, 2.50, "相机驱动", "Basler pylon 8.0", ["设备枚举与打开", "曝光、增益、触发、ROI", "软触发采图与时间戳"]),
    (3.50, 2.50, "编排控制", "Controller", ["流程编排与超时", "配方与参数管理", "结果落库与埋点"]),
    (6.44, 2.50, "算法服务", "机器视觉算法库", ["预处理与标定校正", "标记定位与状态判定", "置信度与偏差量输出"]),
    (9.38, 3.38, "应用前端", "桌面界面", ["实时预览与叠层", "参数面板与配方切换", "判定、复核与导出"]),
]
for x, w, ti, sub, items in nodes:
    p2.rect(x, NY, w, NH, WHITE, BLUE, 0.9)
    p2.vrule(x, NY, NH, BLUE, 0.04)
    p2.txt(x + 0.14, NY + 0.09, w - 0.26, 0.18, ti, 7.6, True, INK)
    p2.txt(x + 0.14, NY + 0.29, w - 0.26, 0.15, sub, 5.6, False, BLUE2)
    p2.txt(x + 0.14, NY + 0.48, w - 0.26, 0.52, "· " + "\n· ".join(items), 5.6, False, MUT, ls=1.20)

# 结果存储（分支）
p2.rect(3.50, 3.58, 2.50, 0.60, FILL2, GRID, 0.7)
p2.vrule(3.50, 3.58, 0.60, TEAL, 0.04)
p2.txt(3.64, 3.65, 2.24, 0.16, "结果存储与导出", 6.2, True, INK)
p2.txt(3.64, 3.84, 2.24, 0.16, "本地库 + 图像留存 + 统计", 5.5, False, MUT)

# 连线与接口标注
p2.wire([(3.06, 2.77), (3.50, 2.77)])
p2.wire([(6.00, 2.77), (6.44, 2.77)])
p2.wire([(8.94, 2.77), (9.38, 2.77)])
p2.wire([(4.75, 3.30), (4.75, 3.58)])
p2.wire([(9.38, 3.10), (9.00, 3.10), (9.00, 3.88), (6.00, 3.88)], dash=True, arrow=False)

for x, txt_ in [(3.08, "IF-1 相机帧"), (6.02, "IF-2 检测请求"), (8.96, "IF-3 检测结果")]:
    p2.rect(x, 2.58, 0.40, 0.14, WHITE, None, 0)
p2.txt(2.70, 2.44, 1.20, 0.14, "IF-1 Frame", 5.2, True, RED, PP_ALIGN.CENTER)
p2.txt(5.62, 2.44, 1.20, 0.14, "IF-2 detect()", 5.2, True, RED, PP_ALIGN.CENTER)
p2.txt(8.54, 2.44, 1.24, 0.14, "IF-3 Result", 5.2, True, RED, PP_ALIGN.CENTER)
p2.txt(6.10, 3.92, 2.40, 0.14, "IF-4 复核与回写（异步）", 5.2, True, TEAL)

# 接口契约
p2.head(0.56, 4.34, 12.20, "接口契约")
p2.table(0.56, 4.60, 12.20, ["接口", "方法", "输入", "输出", "约束"],
         [["IF-1 相机", "enumerate / open / apply / grab",
           "相机序列号、参数集", "Frame（图像、时间戳、参数快照）", "曝光与增益变更后需回读校验"],
          ["IF-2 检测", "detect(frame, recipe)",
           "Frame、配方（阈值、ROI）", "DetectionResult（标记列表、置信度、偏差）", "单帧超时 300 ms，超时按失败处理"],
          ["IF-3 结果", "save / export / stats",
           "DetectionResult、图像引用", "记录 ID、导出文件", "本地留存不少于 30 天"],
          ["IF-4 前端", "onPreview / onResult / command",
           "预览帧、判定结果、操作指令", "状态与提示", "预览降帧不影响检测节拍"]],
         rh=0.40, hh=0.24, fs=5.8, colw=[1.10, 2.50, 2.30, 3.30, 3.00])

p2.txt(0.56, 6.86, 11.4, 0.18,
       "时序：枚举并打开相机 → 下发参数并回读 → 软触发采图 → 检测请求 → 结果叠层与判定 → 落库与统计；"
       "任一环节异常按超时终止并记录，不阻断工位测试。", 5.9, False, DIM)

# ══════════════════ 第 3 页：联调路标（倒排至 11/15）══════════════════
p3 = Page(3, 3, "联调路标：倒排至 11 月 15 日",
          "五阶段 · 五个里程碑 · 四项风险对策")

GX, GW = 2.52, 6.00
DAYS = 21
DX = GW / DAYS


def dx(off):
    return GX + off * DX


p3.head(0.56, 0.64, 8.40, "阶段排期（按工作日，单人全职投入估算）")

# 日期刻度
ticks = [(0, "10/26"), (5, "10/31"), (10, "11/05"), (15, "11/10"), (20, "11/15")]
for off, lb in ticks:
    p3.rect(dx(off) - 0.004, 1.02, 0.008, 3.60, RGBColor(0xE2, 0xDE, 0xD4), None, 0)
    p3.txt(dx(off) - 0.30, 0.96, 0.60, 0.14, lb, 5.4, False, DIM, PP_ALIGN.CENTER)

stages = [
    ("S1", "环境与骨架", "10/26—10/29", 0, 4),
    ("S2", "组件实现", "10/30—11/04", 4, 10),
    ("S3", "全链路联调", "11/05—11/09", 10, 15),
    ("S4", "现场试运行", "11/10—11/12", 15, 18),
    ("S5", "验收与发布", "11/13—11/15", 18, 21),
]
for i, (no, nm, dr, a, b) in enumerate(stages):
    y = 1.14 + i * 0.62
    p3.txt(0.56, y + 0.02, 0.30, 0.16, no, 6.2, True, RED)
    p3.txt(0.88, y, 1.56, 0.17, nm, 6.4, True, INK)
    p3.txt(0.88, y + 0.19, 1.56, 0.15, dr, 5.4, False, MUT)
    w = (b - a) * DX
    p3.rect(dx(a) + 0.01, y + 0.02, w - 0.02, 0.30, FILL2 if i % 2 == 0 else WHITE, BLUE, 0.7)
    p3.rect(dx(a) + 0.01, y + 0.02, 0.028, 0.30, RED if i >= 4 else BLUE, None, 0)

# 里程碑（菱形）
ms = [(4, "M1"), (10, "M2"), (15, "M3"), (18, "M4"), (21, "M5")]
p3.rect(GX, 4.716, dx(20) + DX - GX, 0.008, GRID, None, 0)
for off, lb in ms:
    cx = dx(min(off, 20.4))
    s = p3.sl.shapes.add_shape(MSO_SHAPE.DIAMOND, Inches(cx - 0.075), Inches(4.645),
                               Inches(0.15), Inches(0.15))
    s.shadow.inherit = False
    s.fill.solid()
    s.fill.fore_color.rgb = RED
    s.line.color.rgb = WHITE
    s.line.width = Pt(1.0)
    p3.txt(cx - 0.28, 4.80, 0.56, 0.14, lb, 5.4, True, RED, PP_ALIGN.CENTER)

# 交付物
p3.head(0.56, 5.06, 8.40, "阶段交付物")
p3.table(0.56, 5.32, 8.40, ["阶段", "交付物", "验收口径"],
         [["S1", "pylon 运行时、构建管线、货架条目与下载链路、界面骨架", "骨架可启动并完成一次采图"],
          ["S2", "相机参数与曝光控制、算法接口封装、结果落库、参数面板", "组件自测通过，接口契约稳定"],
          ["S3", "全链路联调、配方标定、节拍与异常路径测量", "连续 200 件无中断，节拍达标"],
          ["S4", "现场试运行、误检漏检统计、参数固化", "连续 3 天跟线，指标达标"],
          ["S5", "发布 v1.0 上架、回滚包、操作与部署说明", "验收通过，产线可用"]],
         rh=0.26, hh=0.24, fs=5.7, colw=[0.60, 4.90, 2.90])

# 右栏：里程碑与风险
p3.head(9.10, 0.64, 3.66, "里程碑", size=8.0)
mss = [("10/29", "骨架可运行"), ("11/04", "组件自测通过"), ("11/09", "全链路联调通过"),
       ("11/12", "试运行指标达标"), ("11/15", "发布与验收")]
for i, (d, t) in enumerate(mss):
    y = 0.92 + i * 0.36
    p3.txt(9.10, y, 0.62, 0.16, d, 6.0, True, RED)
    p3.txt(9.76, y, 3.00, 0.16, t, 6.2, False, INK)

p3.head(9.10, 2.86, 3.66, "风险与对策", color=BLUE, size=8.0)
risks = [
    ("现场光环境波动", "固定补光与曝光标定，参数随配方保存并版本化。"),
    ("样本不足致误检", "首版限定标记类型，低置信度转人工复核，样本累积后迭代。"),
    ("加密构建影响加载", "先行构建验证，动态库与模型走白名单，保留非加密调试包。"),
    ("版本一致性", "启动检查货架版本，安装留痕记录版本与校验值，便于核查。"),
]
for i, (ti, de) in enumerate(risks):
    y = 3.12 + i * 0.62
    p3.txt(9.10, y, 3.62, 0.16, "· " + ti, 6.2, True, INK)
    p3.txt(9.22, y + 0.19, 3.50, 0.38, de, 5.8, False, MUT, ls=1.16)

p3.txt(9.10, 5.72, 3.66, 0.60,
       "关键前提：pylon 运行时版本与相机固件需在 S1 内确认；算法样本由现场提供，缺口部分以人工复核兜底。",
       5.8, False, DIM, ls=1.20)

p3.hline(9.10, 5.60, 3.66, GRID, 0.008)

txt_foot = ("说明：日期按工作日计，S1 自 10 月 26 日起算；若人力投入或现场可用时间变化，S3 与 S4 需同步顺延，"
            "S5 保持不低于 2 个工作日的验收窗口。")
p3.txt(0.56, 6.92, 11.9, 0.18, txt_foot, 5.9, False, DIM)

prs.save(OUT)

p = Presentation(OUT)
oob = ov = 0
for si, s in enumerate(p.slides):
    bxs = []
    for sh in s.shapes:
        try:
            l, t = sh.left.inches, sh.top.inches
            w, h = sh.width.inches, sh.height.inches
        except Exception:
            continue
        if l < -0.01 or t < -0.01 or l + w > SW + 0.01 or t + h > SH + 0.01:
            oob += 1
            print(f"  越界 p{si+1}: ({l:.2f},{t:.2f},{w:.2f}x{h:.2f})")
        if sh.has_text_frame and sh.text_frame.text.strip():
            bxs.append((l, t, w, h, sh.text_frame.text[:12]))
    for i in range(len(bxs)):
        for j in range(i + 1, len(bxs)):
            a, b = bxs[i], bxs[j]
            ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            if ox > 0.08 and oy > 0.08:
                ov += 1
                print(f"  重叠 p{si+1}: 「{a[4]}」×「{b[4]}」{ox:.2f}x{oy:.2f}")
print(f"页数 {len(p.slides)}｜越界 {oob}｜文本重叠 {ov}｜{os.path.getsize(OUT)} B")
