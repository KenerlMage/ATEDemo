# -*- coding: utf-8 -*-
"""半导体 ATE 控制架构：图 A（三形态）与图 B（TestStand→LabVIEW 调用链 + multi-site 时序）"""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 2400, 1350
HERE = os.path.dirname(os.path.abspath(__file__))
BG = (251, 251, 249)
INK = (24, 24, 26)
MUT = (110, 108, 104)
RED = (139, 26, 26)
BLUE = (15, 76, 129)
GREEN = (47, 107, 87)
AMBER = (176, 118, 28)
PURPLE = (92, 58, 122)
LINE = (216, 214, 208)
WHITE = (255, 255, 255)
SOFT = (246, 246, 244)

_c = {}


def f(s, b=False):
    k = (s, b)
    if k not in _c:
        p = r"C:\Windows\Fonts\msyhbd.ttc" if b else r"C:\Windows\Fonts\msyh.ttc"
        _c[k] = ImageFont.truetype(p, s)
    return _c[k]


def canvas():
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)


def put(d, x, y, t, size, color, bold=False, limit=None, tag="", issues=None, align="l"):
    fnt = f(size, bold)
    for k, ln in enumerate(str(t).split("\n")):
        w = d.textlength(ln, font=fnt)
        if limit and w > limit and issues is not None:
            issues.append(f"{tag}: {w:.0f}>{limit:.0f} 「{ln[:14]}」")
        xx = x
        if align == "c":
            xx = x - w / 2
        elif align == "r":
            xx = x - w
        d.text((xx, y + k * int(size * 1.5)), ln, font=fnt, fill=color)
    return


def box(d, x, y, w, h, fill=WHITE, outline=LINE, width=2, r=12):
    d.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=fill,
                        outline=outline, width=width if outline else 0)


def arrow_v(d, x, y1, y2, color=MUT, w=3):
    d.line([x, y1, x, y2 - 10], fill=color, width=w)
    d.polygon([(x - 9, y2 - 12), (x + 9, y2 - 12), (x, y2)], fill=color)


def arrow_h(d, y, x1, x2, color=MUT, w=3):
    d.line([x1, y, x2 - 10, y], fill=color, width=w)
    d.polygon([(x2 - 12, y - 9), (x2 - 12, y + 9), (x2, y)], fill=color)


# ───────────────────────────────── 图 A
def fig_a():
    img, d = canvas()
    it = []
    put(d, 90, 46, "半导体 ATE 的三种控制形态：调度放在哪一层", 52, INK, True, 2200, "标题", it)
    put(d, 92, 118, "决定形态的不是算力，而是「实时回路」与「任务下发」是否在同一个进程里", 26, MUT, False, 2200, "副标题", it)

    cols = [
        dict(x=90, tag="形态 A", name="单站集中", col=BLUE,
             center="上位机 / 单台工控机\n（TestStand 与 LabVIEW 同机）",
             field=["一个机箱、一个控制器", "调度与实时执行同进程"],
             note="1 台机箱 · 1 个 DUT 接口\n延迟最低，扩展只能加机箱",
             rep="小型产线、实验室、\n单工位设备"),
        dict(x=845, tag="形态 B", name="中心下发 + 多站实时", col=RED,
             center="中心调度服务 / MES\n（下发任务令，管理程序与数据）",
             field=["工控机箱 1", "工控机箱 2", "工控机箱 N"],
             note="N 台机箱 · 每台强实时\n控制面集中，数据面本地",
             rep="NI STS 多工位产线、\nOSAT 量产线（主流）"),
        dict(x=1600, tag="形态 C", name="站点自治", col=GREEN,
             center="无中心调度\n（各站独立，仅向上报结果）",
             field=["测试站 1", "测试站 2", "测试站 N"],
             note="N 台设备 · 完全自治\n最耐故障，但难统一换型",
             rep="分散车间、旧线改造、\n外协厂"),
    ]

    for c in cols:
        x, col = c["x"], c["col"]
        box(d, x, 178, 710, 700, WHITE, LINE, 2, 16)
        box(d, x, 178, 710, 54, col, None, 0, 16)
        d.rectangle([x, 214, x + 710, 232], fill=col)
        put(d, x + 24, 190, c["tag"], 22, (252, 250, 246), True, 120, "形态标", it)
        put(d, x + 130, 190, c["name"], 22, WHITE, True, 420, "形态名", it)

        box(d, x + 55, 252, 600, 92, SOFT, LINE, 2, 10)
        for k, ln in enumerate(c["center"].split("\n")):
            put(d, x + 355, 268 + k * 30, ln, 18, INK, k == 0, 560, "中心层", it, align="c")
        arrow_v(d, x + 355, 348, 386, col)

        if len(c["field"]) == 1:
            box(d, x + 175, 392, 360, 96, WHITE, col, 3, 10)
            put(d, x + 355, 424, c["field"][0], 18, col, True, 330, "现场层", it, align="c")
        else:
            for j, name in enumerate(c["field"]):
                bw = 200
                bx = x + 55 + j * 240
                box(d, bx, 392, bw, 96, WHITE, col, 3, 10)
                put(d, bx + bw / 2, 424, name, 17, col, True, bw - 20, "现场层", it, align="c")
            if "独立" in c["note"]:
                for j in range(2):
                    d.line([x + 55 + 210 + j * 226, 402, x + 55 + 210 + j * 226, 478],
                           fill=(198, 194, 186), width=2)

        arrow_v(d, x + 355, 492, 530, col)
        box(d, x + 90, 536, 530, 84, (250, 249, 246), LINE, 2, 10)
        put(d, x + 355, 556, "仪器 / 夹具 / DUT 接口", 19, INK, True, 500, "设备层", it, align="c")
        put(d, x + 355, 588, "PXI 模块 · LXI 仪器 · 针床 · Handler", 15, MUT, False, 500, "设备层副", it, align="c")

        for k, ln in enumerate(c["note"].split("\n")):
            put(d, x + 355, 646 + k * 30, ln, 16, (72, 70, 66), False, 640, "注", it, align="c")
        box(d, x + 55, 726, 600, 118, (250, 247, 242), (226, 220, 210), 2, 10)
        put(d, x + 355, 740, "典型", 14, MUT, True, 80, "典型标", it, align="c")
        for k, ln in enumerate(c["rep"].split("\n")):
            put(d, x + 355, 764 + k * 28, ln, 16, (64, 60, 54), False, 560, "代表", it, align="c")

    # 底部对照表
    ty = 906
    cols_t = [90, 470, 900, 1330, 1760, 2310]
    heads = ["", "调度在哪", "实时在哪", "故障域", "扩展粒度"]
    box(d, 90, ty, 2220, 54, (238, 236, 230), None, 0, 10)
    for j, h in enumerate(heads):
        if not h:
            continue
        put(d, cols_t[j] + 24, ty + 14, h, 18, MUT, True, 400, "表头", it)
    rows = [
        ("形态 A", BLUE, "上位机自身", "同一进程", "整站停", "加机箱（纵向）"),
        ("形态 B", RED, "中心服务 / MES", "各站工控机", "单站停，其余继续", "加站点（横向）"),
        ("形态 C", GREEN, "无", "各站自身", "互不影响", "加设备 / 加人"),
    ]
    for i, (nm, col, a, b, cc, dd) in enumerate(rows):
        ry = ty + 54 + i * 62
        box(d, 90, ry, 2220, 60, WHITE, LINE, 1, 8)
        d.rectangle([90, ry, 100, ry + 60], fill=col)
        put(d, 118, ry + 18, nm, 18, col, True, 100, "行名", it)
        put(d, cols_t[1] + 24, ry + 20, a, 17, INK, False, 400, "格", it)
        put(d, cols_t[2] + 24, ry + 20, b, 17, INK, False, 400, "格", it)
        put(d, cols_t[3] + 24, ry + 20, cc, 17, INK, False, 400, "格", it)
        put(d, cols_t[4] + 24, ry + 20, dd, 17, INK, False, 480, "格", it)

    box(d, 90, 1104, 2220, 96, (247, 244, 240), (226, 220, 210), 2, 12)
    put(d, 114, 1118, "结论", 18, INK, True, 70, "结论", it)
    put(d, 114, 1148,
        "量产半导体线的真实做法是形态 B：中心只做「下发什么任务、用哪版程序、结果收到哪」，实时回路永远留在工控机箱里。"
        "中心不下发每一条指令——那会把微秒级的时序交给以太网。", 19, (74, 70, 64), False, 2160, "结论正文", it)

    put(d, 90, 1296, "依据：NI TestStand 并行/批处理模型与 TSM 文档、Teradyne IG-XL 多站说明、Advantest V93000 每卡独立计算与接口架构　｜　形态划分为本文归纳",
        16, (150, 148, 142), False, 2260, "页脚", it)

    out = os.path.join(HERE, "ATE三种控制形态.png")
    img.save(out)
    print("A:", os.path.getsize(out), "B ｜自检：", it or "无溢出")


# ───────────────────────────────── 图 B
def fig_b():
    img, d = canvas()
    it = []
    put(d, 90, 44, "TestStand → LabVIEW：一次多工位测试到底怎么跑起来", 50, INK, True, 2200, "标题", it)
    put(d, 92, 114, "左：软件分层与进程边界　｜　右：三个 site 的线程与共享资源时序", 25, MUT, False, 2200, "副标题", it)

    # —— 左：分层
    lx, lw = 90, 780
    box(d, lx, 170, lw, 900, WHITE, LINE, 2, 14)
    put(d, lx + 24, 186, "① 分层与进程边界", 22, INK, True, 400, "左标", it)

    layers = [
        ("操作界面 Operator Interface", "独立进程：加载 .seq、显示步骤与结果", BLUE, 232, 96),
        ("TestStand Engine", "序列引擎：解释执行 step、管理流程与结果", RED, 350, 108),
        ("Process Model（Test UUTs / Batch / Parallel）", "入口/收尾、循环 UUT、binning 判定", AMBER, 0, 0),
        ("LabVIEW Adapter → VI", "每个 step 调一个 VI：采集、计算、判定", GREEN, 500, 96),
        ("仪器驱动（IVI / VISA / 厂商 DLL）", "把 VI 的参数翻译成 SCPI 或寄存器访问", PURPLE, 622, 96),
        ("PXI 模块 / LXI 仪器 / 针床", "真实电气动作与测量", (74, 74, 78), 744, 96),
    ]
    y = 232
    boxes = []
    for i, (t, s, col, yy, hh) in enumerate(layers):
        if i == 2:
            continue
        boxes.append((y, hh, t, s, col))
        y += hh + 30
    y = 232
    for i, (t, s, col) in enumerate([(l[0], l[1], l[2]) for l in layers if l[3]]):
        hh = [96, 108, 96, 96, 96][i]
        box(d, lx + 40, y, lw - 80, hh, SOFT, col, 3, 10)
        d.rectangle([lx + 40, y, lx + 48, y + hh], fill=col)
        put(d, lx + 64, y + 14, t, 19, INK, True, lw - 120, "层名", it)
        put(d, lx + 64, y + 48, s, 16, MUT, False, lw - 120, "层说明", it)
        if i < 4:
            arrow_v(d, lx + lw / 2, y + hh, y + hh + 28, (150, 146, 138), 3)
        y += hh + 30

    # 中间插入 Process Model 说明卡
    box(d, lx + 40, 470, lw - 80, 96, (252, 248, 242), AMBER, 2, 10)
    put(d, lx + 64, 484, "Process Model（Test UUTs / Batch / Parallel）", 19, INK, True, lw - 130, "PM名", it)
    put(d, lx + 64, 518, "入口与收尾、UUT 循环、binning 判定；Batch 与 Parallel 各自复制一份序列到新线程", 15,
        (98, 82, 52), False, lw - 130, "PM说明", it)

    # 右侧结果链
    box(d, lx + 40, 872, 340, 120, (250, 250, 248), LINE, 2, 10)
    put(d, lx + 58, 886, "结果记录", 17, INK, True, 200, "结果", it)
    put(d, lx + 58, 916, "TestStand 结果容器\n→ ODBC / ATML / STDF", 15, MUT, False, 300, "结果说明", it)
    box(d, lx + 400, 872, 340, 120, (250, 250, 248), LINE, 2, 10)
    put(d, lx + 418, 886, "数据去向", 17, INK, True, 200, "去向", it)
    put(d, lx + 418, 916, "每 site 独立 bin\n→ 汇总到 Lot / Wafer", 15, MUT, False, 300, "去向说明", it)

    # —— 右：时序
    rx, rw = 910, 1400
    box(d, rx, 170, rw, 900, WHITE, LINE, 2, 14)
    put(d, rx + 24, 186, "② 三个 site 并行时，线程与共享资源怎么排", 22, INK, True, 700, "右标", it)

    stages = ["PreUUT", "Site Setup", "Test Steps", "PostUUT · Bin"]
    sx, sw = rx + 250, 260
    for j, s in enumerate(stages):
        bx = sx + j * sw
        box(d, bx + 4, 228, sw - 12, 42, (238, 236, 230), None, 0, 8)
        put(d, bx + (sw - 12) / 2, 240, s, 17, (86, 84, 80), True, sw - 30, "阶段", it, align="c")

    lanes = [("Main 线程", (74, 74, 78), 0), ("Site 0", BLUE, 1), ("Site 1", BLUE, 1),
             ("Site 2", BLUE, 1), ("仪器层（共享）", RED, 2)]
    ly = 290
    for name, col, kind in lanes:
        d.line([rx + 40, ly + 52, rx + rw - 40, ly + 52], fill=(240, 238, 232), width=1)
        put(d, rx + 40, ly + 40, name, 17, col, True, 200, "泳道", it)
        ly += 104

    # Main：贯穿
    y0 = 290 + 52 - 18
    box(d, sx, y0, 4 * sw - 12, 36, (236, 236, 232), (190, 188, 182), 1, 8)
    put(d, sx + 14, y0 + 9, "加载序列 → 逐 site 建 socket/线程 → 等待全部结束 → 汇总结果", 14, (70, 68, 64), False,
        4 * sw - 40, "主线", it)

    site_colors = [(15, 76, 129), (36, 104, 158), (62, 132, 186)]
    for i in range(3):
        ly = 290 + 104 * (i + 1) + 52 - 18
        # PreUUT 短块
        box(d, sx + 8, ly, sw - 30, 36, (240, 244, 248), site_colors[i], 1, 8)
        put(d, sx + 20, ly + 9, f"DUT{i} 到位/上电", 13, site_colors[i], False, sw - 60, "pre", it)
        # Test Steps 长块
        box(d, sx + sw + 40, ly, sw * 2 - 20, 36, site_colors[i], None, 0, 8)
        put(d, sx + sw + 56, ly + 9, "顺序执行 step（各自的 pin map）", 13, WHITE, True, sw * 2 - 60, "steps", it)
        # Post
        box(d, sx + 3 * sw + 8, ly, sw - 30, 36, (240, 244, 248), site_colors[i], 1, 8)
        put(d, sx + 3 * sw + 20, ly + 9, "判 bin / 记录", 13, site_colors[i], False, sw - 60, "post", it)

    # 仪器层：共享
    ly = 290 + 104 * 4 + 52 - 18
    box(d, sx + sw + 40, ly, sw * 2 - 20, 36, (250, 240, 238), RED, 2, 8)
    put(d, sx + sw + 56, ly + 9, "共享资源：开关矩阵 / 电源 / 激励 —— 需互斥或分时", 13, RED, True, sw * 2 - 60, "共享", it)

    # 注释
    notes = [
        ("socket / 线程", "每个 site 一份序列副本，变量空间独立——TSM 多工位的基础", 1),
        ("共享仪器", "同一台电源被三个 site 共用时必须串行化，否则测值互相污染", 2),
        ("binning", "按 site 独立判 bin，再汇总成 Lot 统计，与 STDF 落盘口径一致", 3),
    ]
    ny = 850
    for i, (t, s, _) in enumerate(notes):
        bx = rx + 40 + (i % 3) * 452
        by = ny + (i // 3) * 100
        box(d, bx, by, 428, 82, SOFT, LINE, 2, 10)
        put(d, bx + 16, by + 10, t, 16, RED if i == 1 else BLUE, True, 380, "注标", it)
        put(d, bx + 16, by + 38, s, 14, (72, 70, 66), False, 396, "注文", it)

    put(d, 90, 1296, "依据：NI TestStand 文档（Batch/Parallel 模型为每个 site 复制序列到新线程）、TSM 多工位 pin map 与 binning、Teradyne IG-XL 单站到多站转换说明",
        16, (150, 148, 142), False, 2260, "页脚", it)

    out = os.path.join(HERE, "TestStand到LabVIEW调用链.png")
    img.save(out)
    print("B:", os.path.getsize(out), "B ｜自检：", it or "无溢出")


fig_a()
fig_b()
