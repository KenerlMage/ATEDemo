# -*- coding: utf-8 -*-
"""生成装备助手货架的演示数据：catalog.json + packages/<id>/{assistant.json,start.bat,index.html,icon.png}

货架清单在设计上来自远端；本次演示由本脚本在本地生成，程序启动时读取同一份文件。
安装包为可运行的演示件：start.bat 打开同目录 index.html（浏览器页面），
用于验证「下载 -> 出现在装备助手页 -> 启动脚本拉起」的完整链路。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGES = ROOT / "packages"

ASSISTANTS = [
    {
        "id": "micro-mark",
        "name": "显微镜标记检测助手",
        "version": "1.2.0",
        "vendor": "ATE 装备组",
        "summary": "相机取图 + 标记识别，输出坐标与偏差",
        "description": "面向显微镜相机的标记检测：枚举相机、设定曝光与增益、单帧取图后做模板匹配，"
                       "输出标记中心坐标与相对偏差。不进入 TPS 主流程，供现场复核与抽检使用。",
        "tags": ["机器视觉", "Basler pylon", "标记检测"],
        "requires": "Windows 10/11 · Basler pylon 8.0 运行时 · 相机 USB3/GigE",
        "entry": "start.bat",
        "icon": "icon.png",
        "published_at": "2026-10-20 09:30:00",
        "release_note": "新增多标记批量识别；曝光自动收敛改为按帧统计；修复大分辨率下坐标偏移。",
        "icon_kind": "microscope",
        "pages": [
            "相机枚举与型号派发（按序列号优先）",
            "曝光 / 增益 / 触发方式设定",
            "单帧取图与标记模板匹配",
            "坐标与偏差输出（CSV 导出）",
        ],
    },
    {
        "id": "scope-live",
        "name": "示波器实时观测助手",
        "version": "1.0.3",
        "vendor": "ATE 装备组",
        "summary": "SCPI 采集波形，长时间连续观测与测量",
        "description": "绑定已注册测试台的示波器，按设定时基连续采集并绘制波形，"
                       "支持峰值、频率、上升时间等测量项。断连自动重试，无硬件时可切内置仿真信号源。",
        "tags": ["SCPI", "波形", "连续采集"],
        "requires": "Windows 10/11 · 示波器开放 SCPI socket（默认 4000）",
        "entry": "start.bat",
        "icon": "icon.png",
        "published_at": "2026-09-28 14:10:00",
        "release_note": "连续采集增加丢帧统计；测量项支持批量勾选；修复长时运行内存增长。",
        "icon_kind": "scope",
        "pages": [
            "绑定测试台设备与连接识别",
            "时基 / 通道 / 触发设定",
            "连续采集与波形绘制",
            "测量项与统计导出",
        ],
    },
    {
        "id": "therm-log",
        "name": "温控曲线记录助手",
        "version": "0.9.1",
        "vendor": "ATE 装备组",
        "summary": "多路温度曲线记录与超温告警",
        "description": "按固定周期读取温控表温度，绘制多路曲线并记录超温事件；"
                       "支持导出与测试任务令关联，便于定位热漂移导致的测试异常。",
        "tags": ["温控", "曲线记录", "告警"],
        "requires": "Windows 10/11 · 温控表支持 Modbus TCP/RTU",
        "entry": "start.bat",
        "icon": "icon.png",
        "published_at": "2026-10-08 11:45:00",
        "release_note": "首个可试用版本；支持 8 路采集；告警阈值可按通道设置。",
        "icon_kind": "thermo",
        "pages": [
            "温控表连接（Modbus TCP / RTU）",
            "通道与采样周期设定",
            "曲线绘制与超温事件记录",
            "数据导出（CSV）",
        ],
    },
    {
        "id": "calib-trim",
        "name": "常数标定计算助手",
        "version": "2.0.0",
        "vendor": "ATE 装备组",
        "summary": "由标定测量值推算机器常数并校验",
        "description": "读取标定工步的测量结果，按常数模型的字段定义推算标定值，"
                       "做量程与单调性校验后导出，可直接回填到机器常数模型。",
        "tags": ["机器常数", "标定", "计算"],
        "requires": "Windows 10/11 · 可访问机器常数库",
        "entry": "start.bat",
        "icon": "icon.png",
        "published_at": "2026-10-26 16:20:00",
        "release_note": "支持按 PN 批量推算；新增量程越界与单调性校验；导出格式对齐常数导入包。",
        "icon_kind": "gauge",
        "pages": [
            "选择 PN 与常数模型",
            "导入标定测量结果",
            "推算标定值并做校验",
            "导出为常数导入包片段",
        ],
    },
    {
        "id": "log-pack",
        "name": "测试日志归集助手",
        "version": "1.1.4",
        "vendor": "ATE 装备组",
        "summary": "按任务令归集日志与报告，打包留档",
        "description": "按任务令号扫描执行日志、报告与常数 XML，统一命名后打包，"
                       "便于问题追溯与产线归档。",
        "tags": ["日志", "归档", "追溯"],
        "requires": "Windows 10/11 · 可读写 reports 目录",
        "entry": "start.bat",
        "icon": "icon.png",
        "published_at": "2026-10-15 10:05:00",
        "release_note": "新增常数 XML 一并归集；打包支持分卷；修复中文路径下压缩失败。",
        "icon_kind": "docs",
        "pages": [
            "选择任务令与时间范围",
            "扫描日志 / 报告 / 常数 XML",
            "统一命名并打包",
            "归档清单导出",
        ],
    },
]

START_BAT = """@echo off
chcp 65001 >nul
title {title}
echo {title}
echo.
echo 正在启动本地页面...
start "" "%~dp0index.html"
echo.
echo 已打开助手页面；关闭本窗口不影响助手运行。
timeout /t 3 >nul
exit
"""

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{name} {version}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 40px 48px; background: #F7F2EA; color: #1A1A1A;
         font-family: "Microsoft YaHei", "微软雅黑", sans-serif; }}
  .wrap {{ max-width: 900px; margin: 0 auto; background: #fff; border: 1px solid #E2D8C8; }}
  .head {{ display: flex; align-items: center; gap: 18px; padding: 24px 28px; border-bottom: 1px solid #E2D8C8; }}
  .head img {{ width: 64px; height: 64px; }}
  h1 {{ font-size: 21px; margin: 0 0 6px; }}
  .meta {{ font-size: 13px; color: #6B6B6B; }}
  .body {{ padding: 24px 28px 30px; }}
  .lead {{ font-size: 14px; line-height: 1.9; color: #333; margin: 0 0 18px; }}
  h2 {{ font-size: 14px; color: #8B1A1A; margin: 0 0 10px; }}
  ul {{ margin: 0 0 20px; padding-left: 22px; font-size: 14px; line-height: 1.9; }}
  .note {{ border-left: 3px solid #8B1A1A; padding: 10px 14px; background: #FBF7F1;
           font-size: 13px; line-height: 1.8; color: #444; }}
  .foot {{ margin-top: 22px; font-size: 12px; color: #8A8A8A; }}
  code {{ background: #F2EDE4; padding: 1px 5px; font-size: 12px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <img src="icon.png" alt="" />
    <div>
      <h1>{name}</h1>
      <div class="meta">版本 {version} · {vendor} · 发布于 {published_at}</div>
    </div>
  </div>
  <div class="body">
    <p class="lead">{description}</p>
    <h2>主要功能</h2>
    <ul>{pages}</ul>
    <h2>版本说明</h2>
    <p class="lead">{release_note}</p>
    <div class="note">
      本助手由 ATE Runner 的装备货架下载并启动，当前为演示件：页面内容用于验证
      「下载 → 出现在装备助手页 → 启动脚本拉起」的完整链路。正式发布时该目录内为
      可执行程序，<code>start.bat</code> 负责以约定参数拉起主程序。
    </div>
    <div class="foot">运行环境要求：{requires}</div>
  </div>
</div>
</body>
</html>
"""

PALETTE = {
    "line": (31, 63, 102),
    "accent": (139, 26, 26),
    "bg": (247, 240, 230),
    "border": (226, 216, 200),
}


def draw_icon(kind: str, out: Path) -> None:
    from PIL import Image, ImageDraw

    S = 4                      # 超采样倍数
    W = H = 256
    img = Image.new("RGB", (W * S, H * S), PALETTE["bg"])
    d = ImageDraw.Draw(img)
    line = PALETTE["line"]
    accent = PALETTE["accent"]
    lw = 7 * S

    def L(pts, color=line, width=lw):
        d.line([(x * S, y * S) for x, y in pts], fill=color, width=width, joint="curve")

    def C(cx, cy, r, color=line, width=lw):
        d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S], outline=color, width=width)

    def R(x0, y0, x1, y1, color=line, width=lw):
        d.rectangle([x0 * S, y0 * S, x1 * S, y1 * S], outline=color, width=width)

    if kind == "microscope":
        L([(96, 176), (96, 150)])
        R(78, 148, 132, 178)                     # 载物台
        L([(96, 122), (128, 92)])                # 镜筒
        R(120, 74, 150, 100)
        C(150, 62, 16, accent)                   # 目镜
        L([(66, 196), (170, 196)])               # 底座
        L([(86, 176), (86, 196)])
        C(104, 138, 7, accent)                   # 物镜
    elif kind == "scope":
        R(46, 62, 210, 150)
        pts = []
        for i in range(0, 129):
            x = 58 + i * 1.35
            import math
            y = 106 + 26 * math.sin(i / 128 * 4 * math.pi)
            pts.append((x, y))
        L(pts, accent, 6 * S)
        L([(96, 150), (96, 186)])
        L([(160, 150), (160, 186)])
        L([(70, 186), (186, 186)])
        C(196, 176, 9)
    elif kind == "thermo":
        R(112, 54, 144, 158)
        C(128, 176, 26, accent)
        L([(128, 120), (128, 170)], accent, 12 * S)
        for i, y in enumerate((78, 100, 122)):
            L([(144, y), (166, y)], line, 5 * S)
        L([(180, 60), (180, 196)])
        L([(172, 196), (188, 196)])
    elif kind == "gauge":
        d.arc([(66 * S, 66 * S), (190 * S, 190 * S)], start=160, end=380, fill=line, width=lw)
        C(128, 128, 8, accent)
        L([(128, 128), (168, 96)], accent, 8 * S)
        for ang in (200, 250, 300, 340):
            import math
            a = math.radians(ang)
            x1, y1 = 128 + 54 * math.cos(a), 132 + 54 * math.sin(a)
            x2, y2 = 128 + 42 * math.cos(a), 132 + 42 * math.sin(a)
            L([(x1, y1), (x2, y2)], line, 5 * S)
        L([(86, 196), (170, 196)])
    else:  # docs
        R(76, 62, 168, 150)
        R(90, 78, 182, 166)
        R(104, 94, 196, 182, accent)
        for y in (116, 134, 152):
            L([(118, y), (182, y)], line, 5 * S)
        L([(196, 96), (196, 96)], line, 5 * S)

    d.rectangle([0, 0, W * S - 1, H * S - 1], outline=PALETTE["border"], width=4 * S)
    img = img.resize((W, H), Image.LANCZOS)
    img.save(out, "PNG", optimize=True)


def main() -> int:
    PACKAGES.mkdir(parents=True, exist_ok=True)
    items = []
    for spec in ASSISTANTS:
        pkg = PACKAGES / spec["id"]
        pkg.mkdir(parents=True, exist_ok=True)
        draw_icon(spec["icon_kind"], pkg / "icon.png")
        manifest = {
            "id": spec["id"],
            "name": spec["name"],
            "version": spec["version"],
            "vendor": spec["vendor"],
            "summary": spec["summary"],
            "description": spec["description"],
            "tags": spec["tags"],
            "requires": spec["requires"],
            "entry": spec["entry"],
            "icon": spec["icon"],
            "published_at": spec["published_at"],
        }
        (pkg / "assistant.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (pkg / "start.bat").write_text(
            START_BAT.format(title=f"{spec['name']} {spec['version']}"), encoding="utf-8")
        (pkg / "index.html").write_text(
            PAGE.format(
                name=spec["name"], version=spec["version"], vendor=spec["vendor"],
                published_at=spec["published_at"], description=spec["description"],
                release_note=spec["release_note"], requires=spec["requires"],
                pages="".join(f"<li>{x}</li>" for x in spec["pages"]),
            ),
            encoding="utf-8",
        )
        size = sum(f.stat().st_size for f in pkg.iterdir() if f.is_file())
        items.append({
            "id": spec["id"],
            "name": spec["name"],
            "version": spec["version"],
            "vendor": spec["vendor"],
            "summary": spec["summary"],
            "description": spec["description"],
            "tags": spec["tags"],
            "requires": spec["requires"],
            "release_note": spec["release_note"],
            "published_at": spec["published_at"],
            "size_hint": f"{max(size, 1) / 1024:.0f} KB",
        })
        print(f"  [ok] {spec['id']:<14} {spec['version']:<7} {size/1024:6.1f} KB")

    catalog = {
        "source": "local",
        "channel": "demo",
        "remote_url": "",
        "updated_at": "2026-10-31 18:00:00",
        "note": "本次为演示：货架清单与安装包均随程序本地持有（assistant_shelf/catalog.json 与 packages/），"
                "等价于远端服务下发的结果；接入远端时把 catalog 的来源替换为接口返回即可。",
        "items": items,
    }
    (ROOT / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] catalog.json  {len(items)} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
