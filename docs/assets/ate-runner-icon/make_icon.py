# -*- coding: utf-8 -*-
"""ATE Runner 应用图标生成器（v2）

v1 缺陷：wafer()/play() 收到已 sc() 过的坐标后又 sc() 一次，图形被二次放大推出画布。
v2 约定：**传入的坐标与尺寸一律是 1024 逻辑单位**，只在绘制调用处 sc() 一次。

母题：晶圆（半导体）× 大写 A（ATE）
母版 1024×1024，绘图在 2× 画布（2048）上完成，再 LANCZOS 降采样。
小尺寸（≤32px）自动简化：去网格、去电路、加粗环、放大三角。
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

INK_D = (7, 18, 30)
INK_L = (20, 49, 74)
CYAN = (79, 216, 238)
RED = (224, 69, 90)
PAPER = (244, 245, 242)

CX, CY, R = 512.0, 504.0, 300.0


def sc(v: float) -> int:
    return int(round(v * S))


def linear_bg(c0, c1) -> Image.Image:
    y, x = np.mgrid[0:U, 0:U].astype(np.float32)
    t = (x + y) / (2.0 * (U - 1))
    arr = np.zeros((U, U, 3), np.uint8)
    for i in range(3):
        arr[:, :, i] = np.clip(c0[i] + (c1[i] - c0[i]) * t, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB").convert("RGBA")


def radial_disc(center, radius, c_in, c_out) -> tuple:
    cx, cy = center
    y, x = np.mgrid[0:U, 0:U].astype(np.float32)
    d = np.sqrt((x - sc(cx)) ** 2 + (y - sc(cy)) ** 2) / float(sc(radius))
    t = np.clip(d, 0.0, 1.0) ** 0.9
    arr = np.zeros((U, U, 3), np.uint8)
    for i in range(3):
        arr[:, :, i] = np.clip(c_in[i] + (c_out[i] - c_in[i]) * t, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr, "RGB").convert("RGBA")
    mask = Image.new("L", (U, U), 0)
    ImageDraw.Draw(mask).ellipse(
        [sc(cx - radius), sc(cy - radius), sc(cx + radius), sc(cy + radius)], fill=255)
    return img, mask


def squircle_mask(radius: float) -> Image.Image:
    mask = Image.new("L", (U, U), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, U - 1, U - 1], radius=sc(radius), fill=255)
    return mask


def circuit(layer: Image.Image) -> None:
    """外围细走线 + 焊盘：只作氛围，避开母题"""
    d = ImageDraw.Draw(layer, "RGBA")
    col = (34, 84, 112, 96)
    pad = (79, 216, 238, 46)
    paths = [
        [(74, 236), (146, 236), (192, 282), (192, 356)],
        [(74, 764), (146, 764), (202, 708), (202, 636)],
        [(950, 236), (878, 236), (832, 282), (832, 356)],
        [(950, 764), (878, 764), (822, 708), (822, 636)],
    ]
    for p in paths:
        d.line([(sc(x), sc(y)) for x, y in p], fill=col, width=sc(2.2), joint="curve")
    for cx, cy in [(192, 356), (202, 636), (832, 356), (822, 636)]:
        r = sc(6.5)
        d.ellipse([sc(cx) - r, sc(cy) - r, sc(cx) + r, sc(cy) + r], fill=pad)


def wafer(layer: Image.Image, c_in, c_out, rim, grid_col, simple: bool, grid: bool = True) -> None:
    disc, mask = radial_disc((CX, CY), R, c_in, c_out)
    layer.paste(disc, (0, 0), mask)
    d = ImageDraw.Draw(layer, "RGBA")

    if not simple and grid:
        gmask = Image.new("RGBA", (U, U), (0, 0, 0, 0))
        gd = ImageDraw.Draw(gmask, "RGBA")
        step = 75.0
        n = int(R // step)
        for k in range(-n, n + 1):
            x = sc(CX + k * step)
            gd.line([(x, 0), (x, U)], fill=grid_col, width=sc(1.5))
            y = sc(CY + k * step)
            gd.line([(0, y), (U, y)], fill=grid_col, width=sc(1.5))
        gmask.putalpha(ImageChops.multiply(gmask.getchannel("A"), mask))
        layer.alpha_composite(gmask)

    w = sc(108 if simple else 7.0)
    d.ellipse([sc(CX - R), sc(CY - R), sc(CX + R), sc(CY + R)], outline=rim, width=w)

    # 晶圆定位边（wafer flat）：以水平弦切除底冠，断口为几何直线（非残缺感）
    if not simple:
        dy = R * 0.955
        cut = Image.new("L", (U, U), 255)
        ImageDraw.Draw(cut).rectangle([0, sc(CY + dy), U, U], fill=0)
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), cut))


def letter_a(layer: Image.Image, scale: float, fill_a, fill_b, glow_col) -> None:
    """大写 A：两笔斜腿 + 横杠，几何体块式，收笔为圆头。

    笔画宽度按 scale 缩放，小尺寸下自动加粗，保证 16px 仍是可读的 A 剪影。
    """
    h = 330.0 * scale
    half = 140.0 * scale
    stroke = (68.0 if scale <= 1.05 else 78.0) * scale
    bar_stroke = stroke * 0.92

    apex = (CX, CY + 4.0 - h / 2)
    yb = CY + 4.0 + h / 2

    shape = Image.new("L", (U, U), 0)
    sd = ImageDraw.Draw(shape)
    sd.line([(sc(CX - half), sc(yb)), (sc(apex[0]), sc(apex[1])), (sc(CX + half), sc(yb))],
            fill=255, width=sc(stroke), joint="curve")
    # 顶点补圆，避免斜接处出现缺口
    r = sc(stroke / 2)
    sd.ellipse([sc(apex[0]) - r, sc(apex[1]) - r, sc(apex[0]) + r, sc(apex[1]) + r], fill=255)

    # 横杠：位置与腿的外缘对齐
    bar_y = CY + 4.0 + h * 0.115
    t = (bar_y - apex[1]) / h
    x_half = half * t + stroke * 0.30
    sd.line([(sc(CX - x_half), sc(bar_y)), (sc(CX + x_half), sc(bar_y))],
            fill=255, width=sc(bar_stroke))

    y, x = np.mgrid[0:U, 0:U].astype(np.float32)
    top, bot = sc(CY + 4.0 - h / 2), sc(CY + 4.0 + h / 2)
    tt = np.clip((y - top) / max(1.0, float(bot - top)), 0, 1)
    arr = np.zeros((U, U, 3), np.uint8)
    for i in range(3):
        arr[:, :, i] = np.clip(fill_a[i] + (fill_b[i] - fill_a[i]) * tt, 0, 255).astype(np.uint8)
    grad = Image.fromarray(arr, "RGB").convert("RGBA")
    grad.putalpha(shape)
    layer.alpha_composite(grad)

    if glow_col[3] > 0:
        glow = shape.filter(ImageFilter.GaussianBlur(sc(22)))
        gimg = Image.new("RGBA", (U, U), glow_col)
        gimg.putalpha(ImageChops.multiply(glow, Image.new("L", (U, U), glow_col[3])))
        layer.alpha_composite(gimg)


def play(layer: Image.Image, scale: float, fill_a, fill_b, glow_col) -> None:
    """保留：圆角播放三角（备用母题，供历史版本复现）"""
    h = 300.0 * scale
    w = 250.0 * scale
    left = CX - 0.50 * w
    tip = CX + 0.50 * w
    p1 = (left, CY - h / 2)
    p2 = (left, CY + h / 2)
    p3 = (tip, CY)
    pad = 12.0 * scale

    shape = Image.new("L", (U, U), 0)
    sd = ImageDraw.Draw(shape)
    sd.polygon([(sc(x), sc(y)) for x, y in (p1, p2, p3)], fill=255)
    for x, y in (p1, p2, p3):
        sd.ellipse([sc(x - pad), sc(y - pad), sc(x + pad), sc(y + pad)], fill=255)

    y, x = np.mgrid[0:U, 0:U].astype(np.float32)
    top, bot = sc(CY - h / 2), sc(CY + h / 2)
    t = np.clip((y - top) / max(1.0, float(bot - top)), 0, 1)
    arr = np.zeros((U, U, 3), np.uint8)
    for i in range(3):
        arr[:, :, i] = np.clip(fill_a[i] + (fill_b[i] - fill_a[i]) * t, 0, 255).astype(np.uint8)
    grad = Image.fromarray(arr, "RGB").convert("RGBA")
    grad.putalpha(shape)
    layer.alpha_composite(grad)

    if glow_col[3] > 0:
        glow = Image.new("RGBA", (U, U), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow, "RGBA")
        gd.polygon([(sc(x), sc(y)) for x, y in (p1, p2, p3)], fill=glow_col)
        glow = glow.filter(ImageFilter.GaussianBlur(sc(24)))
        layer.alpha_composite(glow)


PALETTE = {
    "dark": dict(bg=(INK_D, INK_L), cin=(24, 58, 78), cout=(10, 31, 46), rim=CYAN,
                 grid=(44, 110, 140, 72), pf=(255, 255, 255), pb=(112, 226, 244),
                 glow=(79, 216, 238, 80), deco=True),
    "red": dict(bg=((84, 11, 19), (150, 30, 38)), cin=(122, 24, 32), cout=(74, 10, 16),
                rim=(246, 225, 225), grid=(255, 255, 255, 26),
                pf=(255, 255, 255), pb=(255, 212, 212),
                glow=(255, 255, 255, 55), deco=True),
    "light": dict(bg=(PAPER, (232, 233, 227)), cin=(26, 62, 94), cout=(10, 32, 50),
                  rim=(15, 76, 129), grid=(120, 190, 220, 70),
                  pf=(255, 255, 255), pb=(140, 220, 240),
                  glow=(15, 76, 129, 60), deco=False),
}


def build(variant: str, simple: bool = False, grid: bool = True, tiny: bool = False) -> Image.Image:
    if variant == "mark":
        layer = Image.new("RGBA", (U, U), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer, "RGBA")
        rr = R + 6
        d.ellipse([sc(CX - rr), sc(CY - rr), sc(CX + rr), sc(CY + rr)],
                  outline=(15, 76, 129, 255), width=sc(52))
        cut = Image.new("L", (U, U), 255)
        ImageDraw.Draw(cut).rectangle([0, sc(CY + rr * 0.955), U, U], fill=0)
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), cut))
        letter_a(layer, 0.94, (15, 76, 129), (15, 76, 129), (0, 0, 0, 0))
        return layer

    p = PALETTE[variant]
    layer = Image.new("RGBA", (U, U), (0, 0, 0, 0))
    if tiny:
        # 极小尺寸：省去圆环与网格，只留放大的 A，保证 16px 剪影可辨
        letter_a(layer, 1.88, p["pf"], p["pb"], p["glow"])
    else:
        if p["deco"] and not simple and grid:
            circuit(layer)
        wafer(layer, p["cin"], p["cout"], p["rim"] + (255,), p["grid"], simple, grid)

        if variant == "dark" and not simple and grid:
            d = ImageDraw.Draw(layer, "RGBA")
            rr = R + 50
            d.ellipse([sc(CX - rr), sc(CY - rr), sc(CX + rr), sc(CY + rr)],
                      outline=RED + (105,), width=sc(3.0))

        letter_a(layer, 1.10 if simple else 1.0, p["pf"], p["pb"], p["glow"])

    tile = linear_bg(*p["bg"])
    tile.alpha_composite(layer)

    if not simple and not tiny:
        hl = Image.new("RGBA", (U, U), (0, 0, 0, 0))
        ImageDraw.Draw(hl, "RGBA").ellipse(
            [sc(-150), sc(-320), sc(720), sc(440)], fill=(255, 255, 255, 22))
        tile.alpha_composite(hl.filter(ImageFilter.GaussianBlur(sc(95))))
        ImageDraw.Draw(tile, "RGBA").rounded_rectangle(
            [sc(1.2), sc(1.2), U - sc(1.2), U - sc(1.2)], radius=sc(218),
            outline=(255, 255, 255, 30), width=sc(2.2))

    out = Image.new("RGBA", (U, U), (0, 0, 0, 0))
    out.paste(tile, (0, 0), squircle_mask(218))
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


def montage(dark, red, light, mark, ladder: dict) -> None:
    try:
        f_t = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 40)
        f_s = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 26)
        f_x = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 20)
    except Exception:
        f_t = f_s = f_x = ImageFont.load_default()

    W, H = 1600, 1010
    sheet = Image.new("RGB", (W, H), (250, 250, 248))
    d = ImageDraw.Draw(sheet)
    d.text((60, 42), "ATE Runner · 应用图标", font=f_t, fill=(24, 24, 26))
    d.text((60, 100), "母题：晶圆（半导体）× 大写 A（ATE）", font=f_s,
           fill=(110, 110, 116))

    big = dark.resize((340, 340), Image.LANCZOS)
    sheet.paste(big, (60, 150), big)
    d.text((60, 508), "母版 · 深色（主用）", font=f_x, fill=(72, 72, 78))

    for i, (img, label) in enumerate([(red, "深红变体 · 品牌色"), (light, "浅色变体 · 文档/打印")]):
        x = 450 + i * 250
        im = img.resize((212, 212), Image.LANCZOS)
        sheet.paste(im, (x, 150), im)
        d.text((x, 380), label, font=f_x, fill=(72, 72, 78))

    mk = mark.resize((212, 212), Image.LANCZOS)
    sheet.paste(mk, (960, 150), mk)
    d.text((960, 380), "透明单色标记 · 界面内嵌", font=f_x, fill=(72, 72, 78))

    d.text((60, 560), "尺寸阶梯（真实像素）", font=f_s, fill=(24, 24, 26))
    x = 60
    for s in (256, 128, 64, 48, 32, 24, 16):
        im = ladder[s].convert("RGBA")
        sheet.paste(im, (x, 620 + (256 - s) // 2), im)
        d.text((x, 900), f"{s}px", font=f_x, fill=(110, 110, 116))
        x += s + 38
    sheet.save(os.path.join(OUT, "ate-runner-preview.png"))


def diagnose(img: Image.Image, tag: str) -> None:
    a = np.asarray(img.convert("RGBA")).astype(np.int16)
    al = a[:, :, 3]
    bright = (a[:, :, :3].mean(axis=2) > 190) & (al > 128)
    cyan = (a[:, :, 1] > 150) & (a[:, :, 2] > 170) & (a[:, :, 0] < 150) & (al > 128)
    out = [tag, f"  size={img.size}"]
    for name, m in (("亮部(三角)", bright), ("青色(晶圆环)", cyan)):
        ys, xs = np.nonzero(m)
        if len(xs):
            out.append(f"  {name}: {len(xs)} px, bbox x[{xs.min()}..{xs.max()}] "
                       f"y[{ys.min()}..{ys.max()}], 质心 ({xs.mean():.0f},{ys.mean():.0f})")
        else:
            out.append(f"  {name}: 无")
    print("\n".join(out))


def main() -> None:
    dark, red, light, mark = (build(k) for k in ("dark", "red", "light", "mark"))
    small = build("dark", simple=True)
    mid = build("dark", grid=False)
    tiny = build("dark", tiny=True)

    big = {k: v.resize((BASE, BASE), Image.LANCZOS)
           for k, v in (("dark", dark), ("red", red), ("light", light), ("mark", mark))}
    for k, v in big.items():
        v.save(os.path.join(OUT, f"ate-runner-{k}-1024.png"))

    mid_big = mid.resize((BASE, BASE), Image.LANCZOS)
    small_big = small.resize((BASE, BASE), Image.LANCZOS)
    tiny_big = tiny.resize((BASE, BASE), Image.LANCZOS)

    ladder = {}
    for size in (512, 256, 128, 64, 48, 32, 24, 16):
        if size >= 128:
            src = big["dark"]
        elif size >= 48:
            src = mid_big
        elif size >= 32:
            src = small_big
        else:
            src = tiny_big
        ladder[size] = src.resize((size, size), Image.LANCZOS)
        ladder[size].save(os.path.join(OUT, f"ate-runner-{size}.png"))

    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    write_ico(os.path.join(OUT, "ate-runner.ico"),
              [ladder[s].resize((s, s), Image.LANCZOS) for s in ico_sizes])

    montage(big["dark"], big["red"], big["light"], big["mark"], ladder)
    diagnose(big["dark"], "[母版 1024]")

    print("\n输出：")
    for f in sorted(os.listdir(OUT)):
        if f.endswith((".png", ".ico")):
            print(f"  {f:34s} {os.path.getsize(os.path.join(OUT, f)):>8} B")


if __name__ == "__main__":
    main()
