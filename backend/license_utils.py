"""License 校验模块 (Ed25519 非对称签名)

方案说明:
  - 签发方持有私钥 (backend/tools/private_key.pem, 不随产品部署)
  - 部署端后端仅内置公钥, 离线验证 License 文件:
      1. JSON 格式合法
      2. Ed25519 签名有效 (防篡改/防伪造)
      3. 未过期
      4. 可选: 绑定设备 (device_id 与当前机器码一致; 为空表示不绑定)
  - License 文件放置: backend/license.dat (单机), 或导入时保存到 LICENSE_PATH
"""
from __future__ import annotations

import base64
import hashlib
import json
import platform
import socket
import uuid
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

BASE_DIR = Path(__file__).resolve().parent
LICENSE_PATH = BASE_DIR / "license.dat"

PRODUCT_NAME = "ATE Runner"

# 签发方公钥 (Ed25519, Base64 Raw 编码) — 与 backend/tools/private_key.pem 对应
PUBLIC_KEY_B64 = "XgXn/91gBuiGrlF7+fSb7wVQ0pkvdqsuA/ZYRiWHiy4="

_public_key: Ed25519PublicKey | None = None


def _get_public_key() -> Ed25519PublicKey:
    global _public_key
    if _public_key is None:
        _public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64))
    return _public_key


def get_machine_id() -> str:
    """生成当前机器标识 (用于设备绑定): MAC + 主机名 哈希前 16 位大写"""
    raw = f"{uuid.getnode()}|{platform.node()}|{socket.gethostname()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16].upper()


def _canonical_payload(data: dict) -> bytes:
    """规范化待签名内容: 排除 signature 字段, 键排序, 紧凑 JSON"""
    payload = {k: v for k, v in data.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_license(data: dict, private_key_pem_path: Path) -> str:
    """签发方使用: 对 payload 签名, 返回 hex 签名 (需私钥文件)"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = serialization.load_pem_private_key(private_key_pem_path.read_bytes(), password=None)
    assert isinstance(priv, Ed25519PrivateKey)
    sig = priv.sign(_canonical_payload(data))
    return sig.hex()


def validate_license_data(data: dict) -> tuple[bool, str]:
    """验证已解析的 license dict; 返回 (是否有效, 描述)"""
    try:
        if not isinstance(data, dict):
            return False, "License 格式错误"
        if data.get("product") != PRODUCT_NAME:
            return False, f"License 产品不匹配 (需要 {PRODUCT_NAME})"
        signature = data.get("signature")
        if not signature or not isinstance(signature, str):
            return False, "License 缺少签名"
        # 验签
        try:
            _get_public_key().verify(bytes.fromhex(signature), _canonical_payload(data))
        except (InvalidSignature, ValueError):
            return False, "License 签名无效（文件被篡改或非官方签发）"
        # 有效期
        expires_at = data.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                if datetime.now(exp.tzinfo) > exp:
                    return False, f"License 已过期 ({expires_at})"
            except ValueError:
                return False, "License 到期时间格式错误"
        # 设备绑定 (可选)
        device_id = data.get("device_id")
        if device_id:
            if str(device_id).upper() != get_machine_id():
                return False, f"License 绑定的设备不匹配 (当前机器: {get_machine_id()})"
        return True, "License 有效"
    except Exception as e:
        return False, f"License 校验异常: {e}"


def load_license() -> dict | None:
    """读取本地 license 文件"""
    if not LICENSE_PATH.exists():
        return None
    try:
        return json.loads(LICENSE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def license_status() -> dict:
    """汇总 License 状态 (供 /api/license/status 使用)"""
    lic = load_license()
    if lic is None:
        return {"valid": False, "state": "not_found", "message": "未检测到 License，请导入授权文件", "license": None}
    ok, msg = validate_license_data(lic)
    return {
        "valid": ok,
        "state": "valid" if ok else "invalid",
        "message": msg,
        "license": {
            "customer": lic.get("customer", ""),
            "product": lic.get("product", ""),
            "version": lic.get("version", ""),
            "issued_at": lic.get("issued_at", ""),
            "expires_at": lic.get("expires_at", ""),
            "device_id": lic.get("device_id", ""),
            "machine_id": get_machine_id(),
        } if ok else None,
    }


def import_license(content: str) -> tuple[bool, str]:
    """导入 License: 解析 -> 校验 -> 写入本地文件"""
    try:
        data = json.loads(content)
    except Exception:
        return False, "License 文件不是有效的 JSON 格式"
    ok, msg = validate_license_data(data)
    if not ok:
        return False, msg
    LICENSE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, "License 导入成功，已生效"
