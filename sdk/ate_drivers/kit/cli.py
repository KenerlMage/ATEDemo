# -*- coding: utf-8 -*-
"""驱动开发 CLI

    python -m ate_drivers.kit.cli new    acme-scope-3000 --dir samples --model ACME-3000
    python -m ate_drivers.kit.cli check  samples/acme-scope-3000      # 一致性检查（上架门禁）
    python -m ate_drivers.kit.cli list   <ATE>/drivers                # 列出已装驱动包
    python -m ate_drivers.kit.cli models                              # 看平台已登记的型号（型号派发用）
    python -m ate_drivers.kit.cli contract                            # 打印契约版本与兼容规则

模板用 `__TOKEN__` 占位（不用 str.format：驱动代码里全是 `{scale:g}` 这类花括号）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from ..contract import API_VERSION, MANIFEST_SCHEMA, describe_contract
from .conformance import format_report, run_conformance
from .registry import load_manifest, resolve_entry, scan_dir

DRIVER_TEMPLATE = '''# -*- coding: utf-8 -*-
"""__LABEL__（__KEY__）—— 由 `ate-driver new` 生成

型号驱动只需要填三处差异（其余语义由家族契约保证）：
  ① COMMANDS 命令表    ② PREAMBLE_FIELDS 应答字段顺序    ③ _sim_signal() 仿真模型
"""

from __future__ import annotations

from ate_drivers import ScopeDriver


class __CLS__(ScopeDriver):
    driver_key = "__KEY__"
    family = "scope"
    vendor = "__VENDOR__"
    label = "__LABEL__"
    driver_version = "0.1.0"
    INTERFACES = (__INTERFACES__)   # 连接方式：LAN（网口）/ SERIAL（串口），与 driver.json 一致
    BACKENDS = (__BACKENDS__)       # 传输后端：native（标准库）/ visa（PyVISA），与 driver.json 一致
    MODELS = (__MODELS__)      # 型号派发唯一依据（与 driver.json 的 models 一致）
    sim_idn = "__IDN__"

    # ① 命令表：只覆盖与家族默认不同的键（键名固定，不可改名）
    COMMANDS = dict(ScopeDriver.COMMANDS, **{
        # "timebase_set": ":TIM:SCAL {scale:g}",
        # "waveform_data": ":WAV:DATA?",
    })

    # ② 应答字段顺序：按型号手册填写（填了它就按名字取值，不靠位置猜）
    PREAMBLE_FIELDS: tuple[str, ...] = tuple()

    # ③ 仿真模型：没有实机时用它让用例跑通（参数相同必须结果相同）
    def _sim_signal(self, points: int, window_s: float) -> list[float]:
        return super()._sim_signal(points, window_s)
'''

INIT_TEMPLATE = '''# -*- coding: utf-8 -*-
"""__LABEL__"""
from .driver import __CLS__  # noqa: F401

__all__ = ["__CLS__"]
'''

TEST_TEMPLATE = '''# -*- coding: utf-8 -*-
"""一致性检查（CI 里跑它；本地等同 `python -m ate_drivers.kit.cli check .`）"""
from ate_drivers.kit import run_conformance

from .driver import __CLS__


def test_conformance_all_pass():
    report = run_conformance(__CLS__)
    assert report["ok"], report
'''

MANIFEST_TEMPLATE = {
    "schema": MANIFEST_SCHEMA,
    "key": "",
    "version": "0.1.0",
    "family": "scope",
    "api": "",
    "label": "",
    "vendor": "",
    "entry": "",
    "interfaces": ["LAN"],
    "backends": ["native", "visa"],
    "models": [],
    "capabilities": [],
    "simulate": True,
    "min_platform": "1.0.0",
    "depends": [],
    "signature": "",
}

SCOPE_CAPABILITIES = [
    "identify", "reset", "state",
    "scope.autoset", "scope.timebase", "scope.channel",
    "scope.acquire_waveform", "scope.measure",
    "scope.run", "scope.stop", "scope.single",
]

README_TEMPLATE = '''# __LABEL__（__KEY__）

由 `ate-driver new` 生成的驱动包骨架。

## 三处要填的差异
1. `driver.py` 的 `COMMANDS`：按型号手册填/覆盖命令（键名固定）。
2. `PREAMBLE_FIELDS`：波形前导字段顺序（填了就按名字取值）。
3. `_sim_signal()`：仿真模型（无实机时用例靠它跑通；参数相同必须结果一致）。

## 验收
```bash
python -m ate_drivers.kit.cli check .
```

## 上架前还要补
`interfaces`（连接方式数组：网口 `LAN` / 串口 `SERIAL`）与 `backends`（传输后端：`native` / `visa`）
- 一台实机的验证记录（型号 / 固件版本 / 序列号 / 实测波形与测量值）
'''


def build_manifest(key: str, label: str, mod: str, cls: str, model: str, vendor: str,
                   interface: str = "LAN", backends: str = "native,visa") -> dict:
    manifest = dict(MANIFEST_TEMPLATE)
    manifest.update({
        "key": key,
        "api": API_VERSION,
        "label": label,
        "vendor": vendor,
        "entry": f"{mod}.driver:{cls}",
        "interfaces": [x.strip().upper() for x in str(interface or "LAN").split(",") if x.strip()] or ["LAN"],
        "backends": [x.strip().lower() for x in str(backends or "native").split(",") if x.strip()] or ["native"],
        "models": [model],
        "capabilities": list(SCOPE_CAPABILITIES),
    })
    return manifest


def cmd_new(args) -> int:
    key = args.key.strip().lower().replace("_", "-")
    if not re.match(r"^[a-z0-9][a-z0-9._-]*$", key):
        print(f"驱动 key 非法: {key}（应为小写字母数字与 - . _）")
        return 2
    cls = args.cls or "".join(p.capitalize() for p in re.split(r"[-._]", key)) + "Scope"
    label = args.label or f"{key} 示波器驱动"
    vendor = args.vendor or key.split("-")[0]
    model = (args.model or key.split("-")[-1]).upper()
    interfaces = [x.strip().upper() for x in str(getattr(args, "interface", "LAN") or "LAN").split(",")
                  if x.strip()] or ["LAN"]
    backends = [x.strip().lower() for x in str(getattr(args, "backends", "native,visa") or "native").split(",")
                if x.strip()] or ["native"]
    mod = key.replace("-", "_")
    # 目录名用合法模块名（Python 包名不能带 -），驱动 key 仍保留连字符形式
    pkg_dir = os.path.abspath(os.path.join(args.dir, mod))
    os.makedirs(pkg_dir, exist_ok=True)

    def fill(text: str) -> str:
        return (text.replace("__LABEL__", label).replace("__KEY__", key)
                    .replace("__CLS__", cls).replace("__IDN__", f"{vendor.upper()},{model},SIM,0,0.1.0")
                    .replace("__VENDOR__", vendor).replace("__MODELS__", f'"{model}",')
                    .replace("__INTERFACES__", ", ".join('"%s"' % k for k in interfaces))
                    .replace("__BACKENDS__", ", ".join('"%s"' % k for k in backends)))

    files = {
        "__init__.py": fill(INIT_TEMPLATE),
        "driver.py": fill(DRIVER_TEMPLATE),
        "test_driver.py": fill(TEST_TEMPLATE),
        "README.md": fill(README_TEMPLATE),
        "driver.json": json.dumps(build_manifest(key, label, mod, cls, model, vendor,
                                          getattr(args, "interface", "LAN"),
                                          getattr(args, "backends", "native,visa")),
                                  ensure_ascii=False, indent=2) + "\n",
    }
    for name, content in files.items():
        path = os.path.join(pkg_dir, name)
        if os.path.exists(path) and not args.force:
            print(f"已存在，跳过（--force 覆盖）：{path}")
            continue
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  写入 {path}")
    print(f"\n驱动包骨架已生成：{pkg_dir}（导入名 {mod}，驱动 key {key}）\n下一步：\n  python -m ate_drivers.kit.cli check {pkg_dir}")
    return 0


def cmd_check(args) -> int:
    target = os.path.abspath(args.path)
    base = os.path.dirname(target) if os.path.isdir(target) else os.path.dirname(target)
    try:
        manifest = load_manifest(target)
        cls = resolve_entry(manifest, base_dir=base)
    except Exception as e:
        print(f"加载失败：{e}")
        return 2
    report = run_conformance(cls, root_package=str(manifest["entry"]).split(":")[0].split(".")[0])
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
        print(f"\n清单：{manifest['key']} v{manifest['version']} · 型号 {manifest['models']} · "
              f"连接方式 {_iface_text(manifest)} · 后端 {_backend_text(manifest)} · "
              f"{manifest['compatible']}")
        if manifest["missing_capabilities"]:
            print(f"清单声明的家族基线能力缺失：{manifest['missing_capabilities']}")
    return 0 if report["ok"] else 1


def _iface_text(row: dict) -> str:
    """连接方式文本：优先 interfaces 数组，兼容单值 interface"""
    kinds = row.get("interfaces")
    if not kinds and row.get("interface"):
        kinds = [row.get("interface")]
    return "/".join(str(k) for k in (kinds or [])) or "-"


def _backend_text(row: dict) -> str:
    """传输后端文本：优先 backends 数组，兼容单值 backend"""
    backends = row.get("backends")
    if not backends and row.get("backend"):
        backends = [row.get("backend")]
    return "/".join(str(k) for k in (backends or [])) or "-"


def cmd_list(args) -> int:
    manifests = scan_dir(args.dir)
    if not manifests:
        print(f"{os.path.abspath(args.dir)} 下没有驱动包（找 driver.json）")
        return 0
    print(f"{'key':<24}{'version':<10}{'api':<6}{'连接方式':<14}{'后端':<16}{'型号':<22}状态")
    for m in manifests:
        state = "可加载" if m.get("loadable", True) else f"坏包: {m.get('error')}"
        models = ",".join(m.get("models") or []) or "-"
        print(f"{m.get('key', ''):<24}{m.get('version', '-'):<10}{m.get('api', '-'):<6}"
              f"{_iface_text(m):<14}{_backend_text(m):<16}{models:<22}{state}")
    return 0


def cmd_models(args) -> int:
    """打印平台已登记型号（型号派发依据），可选按目录合并已装驱动包"""
    from .. import factory

    print(factory.describe())
    check = factory.self_check()
    print("注册表自检：%s（型号 %d · 别名 %d）"
          % ("通过" if check["ok"] else "不通过", check["models"], check["aliases"]))
    for p in check["problems"]:
        print("  - " + p)
    if args.dir:
        manifests = [m for m in scan_dir(args.dir) if m.get("loadable", True)]
        if manifests:
            from .registry import describe_registry, merge_specs, model_conflicts

            specs = merge_specs([], manifests)
            print("\n已装驱动包（%s）：" % os.path.abspath(args.dir))
            for row in describe_registry(specs):
                print("  %-24s %-10s 连接方式 %-12s 后端 %-14s 型号 %s"
% (row["key"], row["version"], _iface_text(row), _backend_text(row), row["models"]))
            conflicts = model_conflicts(specs)
            if conflicts:
                print("型号冲突：%s" % conflicts)
                return 1
    return 0 if check["ok"] else 1


def cmd_contract(args) -> int:
    print(json.dumps(describe_contract(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ate-driver", description="ATE Runner 驱动开发工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="生成驱动包骨架")
    n.add_argument("key", help="驱动 key，如 acme-scope-3000")
    n.add_argument("--dir", default=".", help="输出目录")
    n.add_argument("--cls", default="", help="类名（默认由 key 推导）")
    n.add_argument("--label", default="", help="中文名（报告与工具卡片显示）")
    n.add_argument("--vendor", default="", help="厂商名（写入 vendor）")
    n.add_argument("--model", default="", help="设备型号，如 MSO54（写入 models，型号派发用）")
    n.add_argument("--interface", default="LAN",
                   help="连接方式，逗号分隔：LAN（网口）/ SERIAL（串口），如 LAN,SERIAL")
    n.add_argument("--backends", default="native,visa",
                   help="传输后端，逗号分隔：native（标准库）/ visa（PyVISA）")
    n.add_argument("--force", action="store_true", help="覆盖同名文件")
    n.set_defaults(func=cmd_new)

    c = sub.add_parser("check", help="一致性检查（上架门禁）")
    c.add_argument("path", help="驱动包目录或 driver.json 路径")
    c.add_argument("--json", action="store_true", help="输出 JSON 报告")
    c.set_defaults(func=cmd_check)

    l = sub.add_parser("list", help="列出目录下已装驱动包")
    l.add_argument("dir", nargs="?", default=".", help="驱动包根目录")
    l.set_defaults(func=cmd_list)

    k = sub.add_parser("contract", help="打印契约版本与兼容规则")
    k.set_defaults(func=cmd_contract)

    m = sub.add_parser("models", help="列出平台已登记型号（型号派发依据）")
    m.add_argument("--dir", default="", help="额外合并一个已装驱动包目录一起看")
    m.set_defaults(func=cmd_models)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
