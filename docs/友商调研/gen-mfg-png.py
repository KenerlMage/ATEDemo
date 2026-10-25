# -*- coding: utf-8 -*-
"""ASML 制造链测试装备：图 A（四层制造链）与图 B（装备谱系矩阵）"""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 2400, 1350
HERE = os.path.dirname(os.path.abspath(__file__))
BG = (251, 251, 249)
INK = (24, 24, 26)
MUT = (108, 106, 102)
RED = (139, 26, 26)
BLUE = (15, 76, 129)
GREEN = (47, 107, 87)
AMBER = (176, 118, 28)
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


def make_canvas():
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)


def put(d, x, y, t, size, color, bold=False, limit=None, tag="", issues=None):
    """支持多行：按 \n 拆开逐行绘制并逐行校验宽度"""
    fnt = f(size, bold)
    w = 0.0
    for k, ln in enumerate(str(t).split("\n")):
        w = d.textlength(ln, font=fnt)
        if limit and w > limit and issues is not None:
            issues.append(f"{tag}: {w:.0f}>{limit:.0f} 「{ln[:16]}」")
        d.text((x, y + k * int(size * 1.55)), ln, font=fnt, fill=color)
    return w


# ─────────────────────────── 图 A：制造链四层
def fig_a():
    img, d = make_canvas()
    issues = []
    put(d, 90, 50, "ASML 制造链的四层，以及每层在测什么", 52, INK, True, 2200, "标题", issues)
    put(d, 92, 122, "模块化外包 + 洁净室总装：越靠上越像 ATE，越靠下越像计量", 26, MUT, False, 2200, "副标题", issues)

    cols = [("层级", 90, 210), ("被测对象", 315, 385), ("关键测试项", 715, 615),
            ("装备归属与执行方", 1345, 585), ("公开依据", 1945, 365)]
    for t, x, w in cols:
        put(d, x, 178, t, 21, MUT, True, w - 10, f"表头/{t}", issues)
    d.line([90, 208, 2310, 208], fill=LINE, width=2)

    layers = [
        dict(c=RED, tag="L1", name="材料与光学件", who="Carl Zeiss SMT 等",
             obj="微晶玻璃基板、反射镜、\n镀膜靶材",
             items="面形误差（nm 级）\n表面粗糙度（亚 nm）\n镀膜反射率与均匀性",
             gear="激光干涉仪、白光干涉仪\n原子力显微镜、分光光度计\n→ 计量级，不是 ATE",
             ref="ZEISS EUV Optics 论文\n（抛光→CCOS→离子束修形→镀膜→量测）", star=False),
        dict(c=AMBER, tag="L2", name="电子与电气模块", who="Prodrive、Neways 等",
             obj="驱动板、控制板、\n传感器接口板、线束",
             items="连通性与边界扫描（ICT）\n上电功能与时序（FCT）\n温循与老化后的参数漂移",
             gear="ICT / FCT 测试台（COTS 平台\n+ 定制夹具）、飞针、AOI、环境箱\n→ 最接近传统 ATE", star=True),
        dict(c=BLUE, tag="L3", name="机电模块与子系统", who="ASML 模块厂 + 供应商",
             obj="工件台、对准传感器、\n光源模块、片盒 Handlers",
             items="重复定位精度、行程与振动\n热漂移与长期稳定性\n通信时延与多轴同步",
             gear="自研专用性能测试台\n（激光干涉仪 + 加速度计 +\n温采）→ 定制 ATE，非标", star=True),
        dict(c=GREEN, tag="L4", name="整机集成与验收", who="ASML Veldhoven 洁净室",
             obj="整机系统（含光路、\n工件台、控制系统）",
             items="成像性能、套刻、产率\n子系统联调与故障注入\n出厂验收与客户现场复测",
             gear="整机自测程序 + 量测机台\n（YieldStar）+ 统计过程控制\n→ 系统级测试，非部件级", ref=None, star=False),
    ]

    for i, L in enumerate(layers):
        y = 222 + i * 208
        d.rounded_rectangle([90, y, 2310, y + 190], radius=14, fill=WHITE, outline=LINE, width=2)
        d.rounded_rectangle([90, y, 300, y + 190], radius=14, fill=L["c"])
        d.rectangle([284, y, 300, y + 190], fill=L["c"])
        put(d, 112, y + 24, L["tag"], 30, WHITE, True, 60, "层号", issues)
        put(d, 112, y + 70, L["name"], 19, WHITE, True, 180, "层名", issues)
        put(d, 112, y + 118, L["who"], 15, (240, 238, 232), False, 178, "执行方", issues)

        for j, ln in enumerate(L["obj"].split("\n")):
            put(d, 315, y + 34 + j * 34, ln, 21, INK, j == 0, 370, "对象", issues)
        for j, ln in enumerate(L["items"].split("\n")):
            put(d, 715, y + 26 + j * 42, "· " + ln, 20, INK if j == 0 else (60, 58, 54), False, 590, "测试项", issues)
        for j, ln in enumerate(L["gear"].split("\n")):
            put(d, 1345, y + 26 + j * 42, ln, 19, MUT if j < 2 else L["c"], j >= 2, 560, "装备", issues)
        if L.get("ref"):
            for j, ln in enumerate(L["ref"].split("\n")):
                put(d, 1945, y + 44 + j * 34, ln, 17, (140, 138, 132), False, 350, "依据", issues)
        else:
            put(d, 1945, y + 56, "ASML 招聘岗位描述\n（System Integration\n& Test Engineer）", 17,
                (140, 138, 132), False, 350, "依据", issues)
        if L["star"]:
            bx, by = 90 + 18, y + 190 - 34
            d.rounded_rectangle([bx, by, bx + 168, by + 26], radius=13, fill=(255, 248, 236), outline=AMBER, width=2)
            put(d, bx + 12, by + 3, "我们的目标场景", 15, AMBER, True, 150, "标记", issues)

    d.rounded_rectangle([90, 1064, 2310, 1160], radius=14, fill=(247, 244, 240), outline=(226, 220, 210), width=2)
    put(d, 114, 1078, "读法", 18, INK, True, 80, "读法", issues)
    put(d, 114, 1108,
        "上两层（L3/L4）测的是「装完之后还能不能达到设计指标」，下两层（L1/L2）测的是「零件与板卡本身合不合格」。"
        "前者决定交付，后者决定返修成本。", 19, (74, 70, 64), False, 2150, "读法正文", issues)

    put(d, 90, 1296, "来源：ZEISS EUV Optics（euvlitho 2022）、ZEISS SMT 产品资料、Prodrive / Neways 制造服务页、ASML 招聘岗位描述　｜　格局分类为本文判断",
        17, (152, 150, 144), False, 2250, "页脚", issues)

    out = os.path.join(HERE, "ASML制造链与测试装备分层.png")
    img.save(out)
    print("A:", out, os.path.getsize(out), "B", "｜自检：", issues or "无溢出")


# ─────────────────────────── 图 B：装备谱系矩阵
def fig_b():
    img, d = make_canvas()
    issues = []
    put(d, 90, 50, "四类测试手段 × 四层被测对象", 52, INK, True, 2200, "标题", issues)
    put(d, 92, 122, "同一个「自动测试」，在不同层由完全不同的装备承担", 26, MUT, False, 2200, "副标题", issues)

    x0, y0 = 90, 186
    cw_label, cw = 330, 470
    rh_head, rh = 62, 240
    heads = ["L1 光学件", "L2 电子模块", "L3 机电模块", "L4 整机"]
    rows = [
        ("电性测试", RED, ["不适用（非电学量）",
                          "ICT / FCT / 边界扫描\n夹具 + COTS 平台",
                          "板卡级功能与接口验证\n定制台",
                          "系统上电与联锁测试\n整机自测程序"]),
        ("精密计量", BLUE, ["面形、粗糙度、反射率\n干涉仪 / 分光光度计",
                            "不适用（尺寸由 SMT 保证）",
                            "位移与角度标定\n激光干涉仪 + 自准直仪",
                            "成像与套刻量测\nYieldStar / 传感器自校"]),
        ("功能性能", GREEN, ["镀膜均匀性抽检\n光谱扫描台",
                             "时序、功耗、温漂\n示波器 + 电子负载",
                             "重复定位、振动、热漂移\n自研性能测试台",
                             "产率与稳定性长跑\n整机 + 数据平台"]),
        ("环境可靠性", AMBER, ["高低温循环后的面形\n环境箱 + 干涉仪",
                               "温循、老化、HALT\n环境箱 + 数据采集",
                               "温漂与真空兼容性\n真空罐 + 温控台",
                               "客户现场复测\n整机 + 远程诊断"]),
    ]

    d.rounded_rectangle([x0, y0, x0 + cw_label, y0 + rh_head], radius=10, fill=(238, 236, 230))
    put(d, x0 + 20, y0 + 18, "测试类别", 21, MUT, True, cw_label - 30, "角标", issues)
    for j, h in enumerate(heads):
        cx = x0 + cw_label + j * cw
        d.rounded_rectangle([cx + 4, y0, cx + cw - 4, y0 + rh_head], radius=10, fill=(238, 236, 230))
        put(d, cx + 26, y0 + 18, h, 21, MUT, True, cw - 40, "列头", issues)

    for i, (name, col, cells) in enumerate(rows):
        ry = y0 + rh_head + 10 + i * (rh + 10)
        d.rounded_rectangle([x0, ry, x0 + cw_label, ry + rh], radius=10, fill=col)
        put(d, x0 + 22, ry + 34, name, 26, WHITE, True, cw_label - 40, "行名", issues)
        put(d, x0 + 22, ry + 78, ["电学量与连通性", "几何量与材料特性", "动态与精度指标", "耐受与寿命"][i],
            16, (245, 240, 236), False, cw_label - 40, "行副", issues)
        for j, cell in enumerate(cells):
            cx = x0 + cw_label + j * cw
            hot = (i in (0, 1, 2)) and (j in (1, 2))
            d.rounded_rectangle([cx + 4, ry, cx + cw - 4, ry + rh], radius=10,
                                fill=(255, 253, 250) if hot else WHITE,
                                outline=AMBER if hot else LINE, width=2 if hot else 1)
            lines = cell.split("\n")
            for k, ln in enumerate(lines):
                put(d, cx + 22, ry + 28 + k * 34, ln, 18 if k else 19,
                    INK if k == 0 else MUT, k == 0, cw - 44, f"格 {i}-{j}", issues)
            if hot:
                d.rounded_rectangle([cx + cw - 96, ry + rh - 32, cx + cw - 16, ry + rh - 8],
                                    radius=11, fill=(255, 248, 236), outline=AMBER, width=1)
                put(d, cx + cw - 88, ry + rh - 30, "我们做这格", 13, AMBER, True, 78, "格标", issues)

    d.rounded_rectangle([90, 1196, 2310, 1268], radius=12, fill=(247, 244, 240), outline=(226, 220, 210), width=2)
    put(d, 114, 1210, "结论", 18, INK, True, 70, "结论", issues)
    put(d, 114, 1238,
        "L2/L3 的电性、功能性能与环境可靠性测试（橙框）才是「装备制造用 ATE」的主战场；L1 与 L4 的计量与系统测试不是 ATE，别混在一页讲。",
        19, (74, 70, 64), False, 2150, "结论正文", issues)

    put(d, 90, 1296, "格局划分为本文判断；具体测试项与装备类别按半导体设备制造的通用工程实践归纳，不代表 ASML 内部工序卡",
        17, (152, 150, 144), False, 2250, "页脚", issues)

    out = os.path.join(HERE, "部件测试装备谱系矩阵.png")
    img.save(out)
    print("B:", out, os.path.getsize(out), "B", "｜自检：", issues or "无溢出")


fig_a()
fig_b()
