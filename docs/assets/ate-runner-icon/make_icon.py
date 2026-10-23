# -*- coding: utf-8 -*-
"""ATE Runner 应用图标 v9：Q 版光刻机（龙门机架式）× 大写 A

v6–v8 的“方正机身 + 大屏”构图被读成复古电视/微波炉，属构图层问题，非配色问题。
v9 改为**非对称龙门剪影**：左侧立柱 + 顶部悬臂 + 右端下垂镜筒 + 下方工件台，
光刻机（mask aligner）的真实轮廓就是这样，一眼就是设备而不是家电。
A 不再放“屏幕”里，而是被投影在工件台上（竖向压扁，带透视）。
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
BG_T = (16, 42, 66)
BG_B = (7, 21, 35)


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


def build(tier: str = "full", light: bool = False) -> Image.Image:
    full = tier == "full"
    shell = (250, 252, 253) if light else SHELL
    shell_d = (222, 231, 236) if light else SHELL_D

    layer = Image.new("RGBA", (U, U), (0, 0, 0, 0))

    if tier == "tiny":
        # 16–24px：极简剪影 = 底座 + 立柱 + 深色大字 A
        rrect(layer, 402, 196, 622, 366, 40, shell, INK, 26)
        rrect(layer, 150, 340, 874, 850, 150, shell, INK, 28)
        letter_a(layer, 512, 612, 420, 100, INK, INK)
    else:
        if full:
            halo = Image.new("RGBA", (U, U), (0, 0, 0, 0))
            ImageDraw.Draw(halo, "RGBA").ellipse(box(150, 250, 880, 840), fill=CYAN_D + (40,))
            layer.alpha_composite(halo.filter(ImageFilter.GaussianBlur(sc(60))))

            sh = Image.new("RGBA", (U, U), (0, 0, 0, 0))
            ImageDraw.Draw(sh, "RGBA").ellipse(box(146, 790, 878, 856), fill=(0, 0, 0, 118))
            layer.alpha_composite(sh.filter(ImageFilter.GaussianBlur(sc(15))))

        # 底座（plinth）
        rrect(layer, 164, 712, 860, 804, 46, shell_d, INK, 16)

        # 立柱（左）
        rrect(layer, 190, 306, 314, 748, 58, shell, INK, 16)

        # 顶部悬臂（横梁，右端悬空）
        rrect(layer, 244, 286, 730, 388, 56, shell, INK, 16)

        # 镜筒（悬臂右端下垂，Q 版放大比例）
        rrect(layer, 584, 356, 714, 496, 40, shell_d, INK, 16)
        rrect(layer, 602, 462, 696, 500, 16, CYAN_D, INK, 12)

        # 立柱细节：控制面板 + 散热槽（Q 版小按键）
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

        # 工件台（透视盘），落在底座上
        plate = [(474, 566), (800, 566), (834, 706), (436, 706)]
        ImageDraw.Draw(layer, "RGBA").polygon(poly(plate), fill=DARK, outline=INK,
                                              width=sc(13))

        # 光束：镜筒 → 工件台（锐边）
        if full:
            beam = Image.new("RGBA", (U, U), (0, 0, 0, 0))
            bd = ImageDraw.Draw(beam, "RGBA")
            bd.polygon(poly([(604, 500), (694, 500), (790, 566), (498, 566)]),
                       fill=CYAN + (26,))
            bd.polygon(poly([(626, 500), (672, 500), (738, 566), (556, 566)]),
                       fill=CYAN + (52,))
            layer.alpha_composite(beam)

        # A 投在工件台上（竖向压扁，带透视）
        a_layer = Image.new("RGBA", (U, U), (0, 0, 0, 0))
        a_h = 150 if full else 140
        letter_a(a_layer, 638, 646, a_h, a_h * 0.25, (255, 255, 255), CYAN,
                 glow=120 if full else 0)
        k = 0.55
        sq = a_layer.resize((U, int(U * k)), Image.LANCZOS)
        layer.alpha_composite(sq, (0, sc(646) - int(sc(646) * k)))

        # 对准标记（台面刻线，强化“光刻”语义）
        if full:
            d = ImageDraw.Draw(layer, "RGBA")
            for x in (500, 776):
                d.line([(sc(x), sc(668)), (sc(x + 34), sc(668))], fill=INK, width=sc(9))
                d.line([(sc(x + 17), sc(651)), (sc(x + 17), sc(685))], fill=INK, width=sc(9))

    bg = linear_bg((243, 246, 249) if light else BG_T,
                   (222, 230, 236) if light else BG_B)
    bg.alpha_composite(layer)
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


def montage(full, mid, tiny, light, ladder) -> None:
    try:
        f_t = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 40)
        f_s = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 26)
        f_x = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 20)
    except Exception:
        f_t = f_s = f_x = ImageFont.load_default()

    sheet = Image.new("RGB", (1600, 1010), (250, 250, 248))
    d = ImageDraw.Draw(sheet)
    d.text((60, 40), "ATE Runner · 应用图标（Q 版光刻机 × 大写 A）", font=f_t, fill=(24, 24, 26))
    d.text((60, 96), "龙门机架：立柱 + 悬臂 + 下垂镜筒 + 工件台，A 被曝光在台面上",
           font=f_s, fill=(110, 110, 116))

    items = [(full, "完整版（≥48px）"), (mid, "简化版（32px）"),
             (tiny, "极小版（16–24px）"), (light, "浅色变体")]
    for i, (img, label) in enumerate(items):
        x = 60 + i * 300
        im = img.resize((240, 240), Image.LANCZOS)
        sheet.paste(im, (x, 156), im)
        d.text((x, 408), label, font=f_x, fill=(72, 72, 78))

    d.text((60, 476), "尺寸阶梯（真实像素）", font=f_s, fill=(24, 24, 26))
    x = 60
    for s in (256, 128, 64, 48, 32, 24, 16):
        im = ladder[s].convert("RGBA")
        sheet.paste(im, (x, 545 + (256 - s) // 2), im)
        d.text((x, 820), f"{s}px", font=f_x, fill=(110, 110, 116))
        x += s + 38
    sheet.save(os.path.join(OUT, "ate-runner-preview.png"))


def diagnose(img: Image.Image, tag: str) -> None:
    a = np.asarray(img.convert("RGBA")).astype(np.int16)
    al = a[:, :, 3]
    lum = a[:, :, :3].mean(axis=2)
    for name, m in (("亮部(机架)", (lum > 205) & (al > 128)),
                    ("青(A/光)", (a[:, :, 1] > 150) & (a[:, :, 2] > 170)
                     & (a[:, :, 0] < 170) & (al > 128))):
        ys, xs = np.nonzero(m)
        if len(xs):
            print(f"{tag} {name}: {len(xs)} px, bbox x[{xs.min()}..{xs.max()}] "
                  f"y[{ys.min()}..{ys.max()}], 质心 ({xs.mean():.0f},{ys.mean():.0f})")


def main() -> None:
    big = {k: build(k).resize((BASE, BASE), Image.LANCZOS) for k in ("full", "mid", "tiny")}
    big["light"] = build("full", light=True).resize((BASE, BASE), Image.LANCZOS)
    big["full"].save(os.path.join(OUT, "ate-runner-dark-1024.png"))
    big["light"].save(os.path.join(OUT, "ate-runner-light-1024.png"))

    ladder = {}
    for size in (512, 256, 128, 64, 48, 32, 24, 16):
        src = big["full"] if size >= 48 else (big["mid"] if size >= 32 else big["tiny"])
        ladder[size] = src.resize((size, size), Image.LANCZOS)
        ladder[size].save(os.path.join(OUT, f"ate-runner-{size}.png"))

    write_ico(os.path.join(OUT, "ate-runner.ico"),
              [ladder[s].resize((s, s), Image.LANCZOS)
               for s in (16, 24, 32, 48, 64, 128, 256)])

    montage(big["full"], big["mid"], big["tiny"], big["light"], ladder)
    diagnose(big["full"], "[完整版]")

    print("\n输出：")
    for f in sorted(os.listdir(OUT)):
        if f.endswith((".png", ".ico")) and f.startswith("ate-runner"):
            print(f"  {f:34s} {os.path.getsize(os.path.join(OUT, f)):>8} B")


if __name__ == "__main__":
    main()
