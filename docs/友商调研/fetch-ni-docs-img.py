# -*- coding: utf-8 -*-
"""从 NI docs 抓组件界面截图（静态文档页，图片通常可直接取）"""
import io
import os
import re
import urllib.request
from PIL import Image, ImageStat

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ni_img")

PAGES = {
    "ts_seqedit": "https://www.ni.com/docs/en-US/bundle/teststand/page/teststand-sequence-editor.html",
    "tsm_pinmap": "https://www.ni.com/docs/en-US/bundle/teststand-semiconductor-module/page/pin-map-editor.html",
    "tsm_pintab": "https://www.ni.com/docs/en-US/bundle/teststand-semiconductor-module/page/pin-map-tab.html",
    "lv_ui": "https://www.ni.com/docs/en-US/bundle/labview/page/front-panel-and-block-diagram.html",
}


def get(u):
    return urllib.request.urlopen(urllib.request.Request(
        u, headers={"User-Agent": UA, "Referer": "https://www.ni.com/"}), timeout=30).read()


for tag, page in PAGES.items():
    try:
        html = get(page).decode("utf-8", "ignore")
    except Exception as e:
        print(f"[{tag}] 页面失败 {type(e).__name__} {e}")
        continue
    srcs = re.findall(r'<img[^>]+src="([^"]+)"', html, re.I)
    print(f"=== {tag}  {len(srcs)} 张候选")
    for s in srcs[:14]:
        u = s if s.startswith("http") else "https://www.ni.com" + s
        if u.startswith("data:"):
            continue
        try:
            d = get(u)
            im = Image.open(io.BytesIO(d))
            g = im.convert("L")
            st = ImageStat.Stat(g)
            data = list(g.get_flattened_data()) if hasattr(g, "get_flattened_data") else list(g.getdata())
            ratio = sum(1 for v in data if v < 200) / len(data)
            if im.size[0] < 240:
                continue
            name = re.sub(r"[^A-Za-z0-9._-]", "_", u.split("/")[-1])[-58:]
            path = os.path.join(D, f"{tag}__{name}")
            if not path.lower().endswith(".png"):
                path += ".png"
            im.convert("RGB").save(path, "PNG")
            print(f"   {im.size[0]}x{im.size[1]} {ratio:.0%} std={st.stddev[0]:.0f} {len(d)//1024}K {name}")
        except Exception as e:
            print(f"   跳过 {type(e).__name__} {u[-70:]}")
