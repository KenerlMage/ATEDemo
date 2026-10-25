# -*- coding: utf-8 -*-
"""抓取 NI 官网三大组件的官方功能描述"""
import re
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

PAGES = [
    ("TestStand",
     "https://www.ni.com/en/shop/electronic-test-instrumentation/application-software-for-electronic-test-and-instrumentation-category/what-is-teststand.html"),
    ("LabVIEW",
     "https://www.ni.com/en/shop/electronic-test-instrumentation/application-software-for-electronic-test-and-instrumentation-category/what-is-labview.html"),
    ("TSM",
     "https://www.ni.com/en/shop/electronic-test-instrumentation/application-software-for-electronic-test-and-instrumentation-category/what-is-teststand-semiconductor-module.html"),
    ("TSM2",
     "https://www.ni.com/en/shop/electronic-test-instrumentation/application-software-for-electronic-test-and-instrumentation-category/teststand-semiconductor-module.html"),
]


def clean(h):
    h = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", "\n", h)
    h = h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'")
    h = h.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
    lines = [x.strip() for x in h.split("\n")]
    return [x for x in lines if len(x) > 40]


for tag, url in PAGES:
    try:
        html = urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": UA}), timeout=30).read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"[{tag}] FAIL {type(e).__name__} {e}")
        continue
    md = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]{40,})"', html, re.I)
    print(f"=== {tag}  {url.split('/')[-1][:40]}  len={len(html)}")
    if md:
        print("META:", md.group(1)[:400])
    for ln in clean(html)[:6]:
        if any(k in ln.lower() for k in ("teststand", "labview", "semiconductor", "automate", "test")):
            print("  •", ln[:300])
