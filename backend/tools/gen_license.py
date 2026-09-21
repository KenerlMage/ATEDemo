"""License 签发工具 (仅签发方使用, 需 private_key.pem)

用法:
  python gen_license.py --customer "XX科技有限公司" --days 3650 --out "D:/ATE_ENV/ate_runner.lic"
  python gen_license.py --customer "XX" --device-id ABC123 --days 365

说明:
  - 私钥 private_key.pem 必须与同目录, 仅签发方持有, 不要随产品部署
  - 生成的 .lic 文件是 JSON 文本 (含签名), 部署时通过前端"导入 License"上传
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PRIVATE_KEY = BASE_DIR / "private_key.pem"

sys.path.insert(0, str(BASE_DIR.parent))
from license_utils import PRODUCT_NAME, get_machine_id, sign_license  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 ATE Runner License 文件")
    ap.add_argument("--customer", required=True, help="客户名称")
    ap.add_argument("--days", type=int, default=3650, help="有效天数 (默认 3650)")
    ap.add_argument("--device-id", default="", help="绑定设备机器码 (默认空=不绑定)")
    ap.add_argument("--out", default="ate_runner.lic", help="输出文件路径")
    args = ap.parse_args()

    if not PRIVATE_KEY.exists():
        print(f"[错误] 找不到私钥: {PRIVATE_KEY}")
        print("请先运行 gen_keypair.py 生成密钥对，并把公钥写入 backend/license_utils.py")
        return 1

    issued = datetime.now(timezone.utc)
    expires = issued + timedelta(days=args.days)
    data = {
        "product": PRODUCT_NAME,
        "version": "1.0.0",
        "customer": args.customer,
        "device_id": args.device_id,
        "issued_at": issued.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
    }
    data["signature"] = sign_license(data, PRIVATE_KEY)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] License 已生成: {out}")
    print(f"     客户: {args.customer}")
    print(f"     有效期: {issued.strftime('%Y-%m-%d')} ~ {expires.strftime('%Y-%m-%d')} ({args.days} 天)")
    print(f"     绑定设备: {args.device_id or '(不绑定)'}")
    print(f"     当前机器码: {get_machine_id()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
