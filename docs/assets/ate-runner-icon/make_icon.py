# -*- coding: utf-8 -*-
"""ATE 应用图标家族 v10：Q 版光刻机 × 大写 A（暖底 + 底部文字）

三项变更（相对 v9）：
  ① 背景改暖：深暖褐 → 近黑（原来冷蓝），主光晕改暖琥珀，青色 A 与之形成冷暖互补；
  ② 图案整体上移并缩至 0.88，让出底部文字带；
  ③ 底部加字，按文字产出三版：Controller / Runner / Data。
     每版共用同一台机器与同一个 A，只用底部强调线的颜色区分身份（琥珀 / 青 / 青绿）。

小尺寸策略：≥64px 带文字；48 及以下不带文字（5px 高的字只会糊成噪点），
改为“机架 + A”居中放大，保证识别度。
"""
from __future__ import annotations

import io
import os
import struct

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = HERE
BASE = 1024
S = 2
U = BASE * S

INK = (11, 34, 51)
SHELL = (240, 245, 248)
SHELL_D = (196, 213, 224)
DARK = (12, 32, 48)
CYAN = (95, 224, 246)
CYAN_D = (57, 198, 224)
RED = (224, 69, 90)

# 暖色背景（上暖褐、下近黑）
BG_T = (58, 41, 29)
BG_B = (22, 16, 12)
GLOW = (232, 162, 60)          # 暖琥珀光晕
LIGHT_T = (252, 245, 235)
LIGHT_B = (238, 224, 208)

VARIANTS = (
    ("controller", "Controller", (240, 169, 59)),   # 琥珀
    ("runner", "Runner", (95, 224, 246)),           # 青
    ("data", "Data", (111, 211, 160)),              # 青绿
)


def sc(v: float) -> int:
    return int(round(v * S))


def box(x0, y0, x1, y1):
    return [sc(x0), sc(y0), sc(x1), sc(y1)]


def poly(pts):
    return [(sc(x), sc(y)) for x, y in pts]


def linear_bg(c0, c1) -> Image.Image:
    y, x = np.mgrid[0:U, 0:U].astype(np.float32)
    t = (x * 0.55 + y) / (U * 1.55)
    arr = np.zeros((U, U, 3), np.uint8)
    for i in range(3):
        arr[:, :, i] = np.clip(c0[i] + (c1[i] - c0[i]) * t, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB").convert("RGBA")


def squircle_mask(radius: float) -> Image.Image:
    m = Image.new("L", (U, U), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, U - 1, U - 1], radius=sc(radius), fill=255)
    return m


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def letter_a(layer, cx, cy, h, stroke, c_top, c_bot, glow: int = 0) -> None:
    half = h * 0.424
    apex = (cx, cy - h / 2)
    yb = cy + h / 2

    shape = Image.new("L", (U, U), 0)
    sd = ImageDraw.Draw(shape)
    sd.line([(sc(cx - half), sc(yb)), (sc(apex[0]), sc(apex[1])), (sc(cx + half), sc(yb))],
            fill=255, width=sc(stroke), joint="curve")
    r = sc(stroke / 2)
    sd.ellipse([sc(apex[0]) - r, sc(apex[1]) - r, sc(apex[0]) + r, sc(apex[1]) + r], fill=255)

    bar_y = cy + h * 0.13
    t = (bar_y - apex[1]) / h
    x_half = half * t + stroke * 0.34
    sd.line([(sc(cx - x_half), sc(bar_y)), (sc(cx + x_half), sc(bar_y))],
            fill=255, width=sc(stroke * 0.92))

    y, _ = np.mgrid[0:U, 0:U].astype(np.float32)
    top, bot = sc(cy - h / 2), sc(cy + h / 2)
    tt = np.clip((y - top) / max(1.0, float(bot - top)), 0, 1)
    arr = np.zeros((U, U, 3), np.uint8)
    for i in range(3):
        arr[:, :, i] = np.clip(c_top[i] + (c_bot[i] - c_top[i]) * tt, 0, 255).astype(np.uint8)
    grad = Image.fromarray(arr, "RGB").convert("RGBA")
    grad.putalpha(shape)

    if glow:
        g = Image.new("RGBA", (U, U), CYAN + (0,))
        g.putalpha(ImageChops.multiply(shape.filter(ImageFilter.GaussianBlur(sc(15))),
                                       Image.new("L", (U, U), glow)))
        layer.alpha_composite(g)
    layer.alpha_composite(grad)


def rrect(layer, x0, y0, x1, y1, r, fill, outline=INK, w=16) -> None:
    ImageDraw.Draw(layer, "RGBA").rounded_rectangle(
        box(x0, y0, x1, y1), radius=sc(r), fill=fill,
        outline=outline, width=sc(w) if outline else 0)


def draw_machine(layer, tier: str, light: bool) -> None:
    full = tier == "full"
    shell = (250, 252, 253) if light else SHELL
    shell_d = (222, 231, 236) if light else SHELL_D

    if tier == "tiny":
        # 16–24px：剪影化——机身 + 顶部凸起 + 极粗的 A，只求这三个形可辨
        rrect(layer, 392, 168, 632, 360, 44, shell, INK, 30)
        rrect(layer, 148, 322, 876, 862, 152, shell, INK, 32)
        letter_a(layer, 512, 598, 470, 158, INK, INK)
        return

    # 小尺寸（48/32）用更粗的墨线，抵消降采样后的糊化
    st = 16 if full else 30
    # 底座
    rrect(layer, 164, 712, 860, 804, 46, shell_d, INK, st)
    # 立柱
    rrect(layer, 190, 306, 314, 748, 58, shell, INK, st)
    # 悬臂
    rrect(layer, 244, 286, 730, 388, 56, shell, INK, st)
    # 镜筒
    rrect(layer, 584, 356, 714, 496, 40, shell_d, INK, st)
    rrect(layer, 602, 462, 696, 500, 16, CYAN_D, INK, st - 6)

    # 立柱细节
    if full:
        rrect(layer, 216, 452, 292, 522, 16, DARK, INK, 11)
        d = ImageDraw.Draw(layer, "RGBA")
        d.ellipse(box(230, 468, 250, 488), fill=CYAN_D, outline=INK, width=sc(6))
        d.ellipse(box(258, 468, 278, 488), fill=RED, outline=INK, width=sc(6))
        for i in range(3):
            y = 560 + i * 34
            d.rounded_rectangle(box(222, y, 286, y + 14), radius=sc(7),
                                fill=SHELL_D if not light else (208, 219, 227),
                                outline=INK, width=sc(7))

    # 工件台
    plate = [(474, 566), (800, 566), (834, 706), (436, 706)]
    ImageDraw.Draw(layer, "RGBA").polygon(poly(plate), fill=DARK, outline=INK,
                                          width=sc(13 if full else 26))

    # 光束
    if full:
        beam = Image.new("RGBA", (U, U), (0, 0, 0, 0))
        bd = ImageDraw.Draw(beam, "RGBA")
        bd.polygon(poly([(604, 500), (694, 500), (790, 566), (498, 566)]),
                   fill=CYAN + (26,))
        bd.polygon(poly([(626, 500), (672, 500), (738, 566), (556, 566)]),
                   fill=CYAN + (52,))
        layer.alpha_composite(beam)

    # 台面上的 A（深色描边层 + 渐变主体）
    a_layer = Image.new("RGBA", (U, U), (0, 0, 0, 0))
    a_h = 224 if full else 210
    letter_a(a_layer, 638, 636, a_h, a_h * 0.25 + 26, INK, INK)
    letter_a(a_layer, 638, 636, a_h, a_h * 0.25, (255, 255, 255), CYAN,
             glow=160 if full else 0)
    k = 0.70
    sq = a_layer.resize((U, int(U * k)), Image.LANCZOS)
    layer.alpha_composite(sq, (0, sc(636) - int(sc(636) * k)))

    # 台沿亮边
    if full:
        ImageDraw.Draw(layer, "RGBA").line(poly([(482, 578), (792, 578)]),
                                           fill=CYAN + (120,), width=sc(7))


def master(tier: str = "full", light: bool = False, label: str | None = None,
           accent=(95, 224, 246)) -> Image.Image:
    """tier=full/mid/tiny；label 为底部文字；返回 2048² 母版"""
    shell_layer = Image.new("RGBA", (U, U), (0, 0, 0, 0))

    # 暖底光晕（替代原来的青晕）
    if tier == "full":
        halo = Image.new("RGBA", (U, U), (0, 0, 0, 0))
        ImageDraw.Draw(halo, "RGBA").ellipse(box(150, 250, 880, 840), fill=GLOW + (46,))
        shell_layer.alpha_composite(halo.filter(ImageFilter.GaussianBlur(sc(64))))

    machine = Image.new("RGBA", (U, U), (0, 0, 0, 0))
    if tier == "full":
        sh = Image.new("RGBA", (U, U), (0, 0, 0, 0))
        ImageDraw.Draw(sh, "RGBA").ellipse(box(146, 790, 878, 856), fill=(0, 0, 0, 120))
        machine.alpha_composite(sh.filter(ImageFilter.GaussianBlur(sc(15))))
    draw_machine(machine, tier, light)

    if label:
        # 图案上移 + 缩到 0.88，让出底部文字带
        k, dy = 0.80, 40
        small = machine.resize((int(U * k), int(U * k)), Image.LANCZOS)
        ox = (U - small.width) // 2
        oy = int((U - small.height) / 2 + (0.5 - 0.5) * 0) + 0
        oy = int((U * (1 - k)) / 2) - sc(dy)
        shell_layer.alpha_composite(small, (ox, oy))
    else:
        shell_layer.alpha_composite(machine)

    bg = linear_bg(LIGHT_T if light else BG_T, LIGHT_B if light else BG_B)
    bg.alpha_composite(shell_layer)

    if label:
        d = ImageDraw.Draw(bg, "RGBA")
        max_w = sc(660)
        size = 240
        while size > 40:
            f = font(size)
            if f.getbbox(label)[2] <= max_w:
                break
            size -= 4
        f = font(size)
        track = int(size * 0.05)
        widths = [f.getbbox(ch)[2] for ch in label]
        total = sum(widths) + track * (len(label) - 1)
        x = (U - total) / 2
        cy = sc(872)
        for ch, w in zip(label, widths):
            d.text((x, cy), ch, font=f, fill=(247, 242, 235, 255), anchor="lm")
            x += w + track
        # 身份强调线
        bar_w, bar_h = sc(120), sc(9)
        bx = (U - bar_w) / 2
        d.rounded_rectangle([bx, sc(942), bx + bar_w, sc(942) + bar_h],
                            radius=bar_h / 2, fill=accent + (235,))

    out = Image.new("RGBA", (U, U), (0, 0, 0, 0))
    out.paste(bg, (0, 0), squircle_mask(218))
    return out


def write_ico(path: str, images: list) -> None:
    blobs = []
    for im in images:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        blobs.append(buf.getvalue())
    n = len(images)
    offset = 6 + 16 * n
    out = bytearray(struct.pack("<HHH", 0, 1, n))
    for im, data in zip(images, blobs):
        w, h = im.size
        out += struct.pack("<BBBBHHII", 0 if w >= 256 else w, 0 if h >= 256 else h,
                           0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for data in blobs:
        out += data
    open(path, "wb").write(bytes(out))


def family_preview(images: dict, ladder: dict) -> None:
    sheet = Image.new("RGB", (1620, 1080), (250, 248, 244))
    d = ImageDraw.Draw(sheet)
    d.text((60, 40), "ATE 图标家族 · Q 版光刻机 × 大写 A", font=font(42), fill=(28, 24, 20))
    d.text((60, 98), "Controller / Runner / Data —— 同一台机器，底部文字与强调线区分身份",
           font=font(26, False), fill=(120, 112, 104))

    for i, (key, label, accent) in enumerate(VARIANTS):
        x = 60 + i * 500
        im = images[key].resize((420, 420), Image.LANCZOS)
        sheet.paste(im, (x, 150), im)
        d.text((x, 586), label, font=font(26), fill=tuple(accent))

    d.text((60, 650), "Runner 尺寸阶梯（带文字 ≥64px）", font=font(26, False), fill=(28, 24, 20))
    x = 60
    for s in (256, 128, 64, 48, 32, 24, 16):
        im = ladder[s].convert("RGBA")
        sheet.paste(im, (x, 700 + (256 - s) // 2), im)
        d.text((x, 980), f"{s}px", font=font(20, False), fill=(120, 112, 104))
        x += s + 40
    sheet.save(os.path.join(OUT, "ate-family-preview.png"))


def diagnose(img: Image.Image, tag: str) -> None:
    a = np.asarray(img.convert("RGBA")).astype(np.int16)
    al = a[:, :, 3]
    lum = a[:, :, :3].mean(axis=2)
    for name, m in (("亮部(机架/文字)", (lum > 205) & (al > 128)),
                    ("青(A/光)", (a[:, :, 1] > 150) & (a[:, :, 2] > 170)
                     & (a[:, :, 0] < 170) & (al > 128))):
        ys, xs = np.nonzero(m)
        if len(xs):
            print(f"{tag} {name}: {len(xs)} px, bbox x[{xs.min()}..{xs.max()}] "
                  f"y[{ys.min()}..{ys.max()}], 质心 ({xs.mean():.0f},{ys.mean():.0f})")


def main() -> None:
    masters = {}
    for key, label, accent in VARIANTS:
        big = master("full", False, label, accent)
        masters[key] = big.resize((BASE, BASE), Image.LANCZOS)
        masters[key].save(os.path.join(OUT, f"ate-{key}-dark-1024.png"))
        if key == "runner":
            master("full", True, label, accent).resize((BASE, BASE), Image.LANCZOS).save(
                os.path.join(OUT, "ate-runner-light-1024.png"))

    plain_full = master("full", False, None).resize((BASE, BASE), Image.LANCZOS)
    plain_mid = master("mid", False, None).resize((BASE, BASE), Image.LANCZOS)
    plain_tiny = master("tiny", False, None).resize((BASE, BASE), Image.LANCZOS)

    ladder = {}
    for key, label, accent in VARIANTS:
        ladder[key] = {}
        for size in (512, 256, 128, 64, 48, 32, 24, 16):
            if size >= 64:
                src = masters[key]
            elif size == 48:
                src = plain_mid
            elif size == 32:
                src = plain_mid
            else:
                src = plain_tiny
            im = src.resize((size, size), Image.LANCZOS)
            ladder[key][size] = im
            im.save(os.path.join(OUT, f"ate-{key}-{size}.png"))
        write_ico(os.path.join(OUT, f"ate-{key}.ico"),
                  [im for s, im in sorted(ladder[key].items()) if s <= 256])

    # 兼容旧命名：Runner 主体沿用 ate-runner-*、ate-runner.ico
    for size, im in ladder["runner"].items():
        im.save(os.path.join(OUT, f"ate-runner-{size}.png"))

    family_preview(masters, ladder["runner"])
    diagnose(masters["runner"], "[Runner 完整版]")

    print("\n输出：")
    for f in sorted(os.listdir(OUT)):
        if f.endswith((".png", ".ico")):
            print(f"  {f:34s} {os.path.getsize(os.path.join(OUT, f)):>8} B")


if __name__ == "__main__":
    main()
