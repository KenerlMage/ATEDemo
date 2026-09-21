"""生成 Ed25519 密钥对（一次性工具）

输出:
  private_key.pem  签发私钥（仅签发方持有，不随产品部署）
  控制台打印公钥  ->  粘贴到 backend/license_utils.py 的 PUBLIC_KEY 常量
"""
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

priv = Ed25519PrivateKey.generate()
priv_pem = priv.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
with open("private_key.pem", "wb") as f:
    f.write(priv_pem)

pub = priv.public_key()
pub_b64 = pub.public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
import base64
print("PRIVATE_KEY saved -> private_key.pem")
print("PUBLIC_KEY_B64 =", base64.b64encode(pub_b64).decode())
