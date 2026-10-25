# -*- coding: utf-8 -*-
"""从 NI 官网采集图片：兼容 /is/image/ni/ 动态媒体与 /content/dam/ 静态资源"""
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ni_img")
os.makedirs(OUT, exist_ok=True)

PAGES = {
    "teststand": ["https://www.ni.com/en/shop/product/teststand.html",
                  "https://www.ni.com/en/shop/electronic-test-instrumentation/application-software-for-electronic-test-and-instrumentation-category/what-is-teststand.html"],
    "tsm": ["https://www.ni.com/en/shop/product/teststand-semiconductor-module.html",
            "https://www.ni.com/en-au/shop/product/teststand-semiconductor-module.html"],
    "labview": ["https://www.ni.com/en/shop/product/labview.html"],
    "switch": ["https://www.ni.com/en/shop/product/switch-executive.html"],
    "instrumentstudio": ["https://www.ni.com/en/shop/product/instrumentstudio.html"],
    "systemlink": ["https://www.ni.com/en/shop/product/systemlink.html"],
    "diadem": ["https://www.ni.com/en/shop/product/diadem.html"],
    "sts": ["https://www.ni.com/en/shop/systems/semiconductor-test-system.html",
            "https://www.ni.com/en/shop/semiconductor/semiconductor-test-system.html"],
    "pxi": ["https://www.ni.com/en/shop/pxi.html"],
    "visa": ["https://www.ni.com/en/shop/product/ni-visa.html"],
    "daqmx": ["https://www.ni.com/en/shop/product/ni-daqmx.html"],
}

DAM_RE = re.compile(r'/content/dam/[^"\'\s)]+?\.(?:png|jpe?g|webp)', re.I)
IS_RE = re.compile(r'/is/image/ni/[^"\'\s)?]+', re.I)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def dl(url, tag):
    base = url.split("?")[0]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", base.split("/")[-1])[-70:]
    if not re.search(r"\.(png|jpe?g|webp)$", name, re.I):
        name += ".png"
    path = os.path.join(OUT, f"{tag}__{name}")
    if os.path.exists(path) and os.path.getsize(path) > 4000:
        return path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.ni.com/"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if len(data) < 8000:
            return None
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception:
        return None


found = {}
for tag, urls in PAGES.items():
    got, ok = [], False
    for u in urls:
        try:
            h = fetch(u)
        except Exception as e:
            print(f"[{tag}] 失败 {type(e).__name__} :: {u[-60:]}")
            continue
        ok = True
        for m in DAM_RE.findall(h) + IS_RE.findall(h):
            if m not in got:
                got.append(m)
        if got:
            break
    print(f"{tag}: {'OK' if ok else 'ALL FAIL'} 资源 {len(got)}")
    found[tag] = got[:6]

jobs = []
for tag, lst in found.items():
    for rel in lst:
        url = rel if rel.startswith("http") else "https://www.ni.com" + rel
        jobs.append((url, tag))

print("待下载", len(jobs))
with ThreadPoolExecutor(max_workers=6) as ex:
    res = list(ex.map(lambda j: (j[0], j[1], dl(j[0], j[1])), jobs))

rows = []
for u, tag, p in res:
    if not p:
        continue
    try:
        im = Image.open(p)
        w, h = im.size
    except Exception:
        continue
    if w < 300 or h < 150:
        continue
    rows.append((tag, w, h, round(os.path.getsize(p) / 1024), os.path.basename(p)))

rows.sort(key=lambda r: (r[0], -r[1] * r[2]))
print(f"\n可用 {len(rows)} 张")
for tag, w, h, kb, nm in rows:
    print(f"{tag:16s} {w:5d}×{h:<5d} {kb:5d}K  {nm}")
