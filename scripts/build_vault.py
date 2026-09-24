# -*- coding: utf-8 -*-
"""生成加密数据保险库：用登录密码派生 AES-GCM 密钥加密 analytics.json。
用法: python build_vault.py --password 'admin@123'
输出: ST/site/vault.json  (salt / verifier / iv / 密文, base64)
明文 analytics.json 仅存在于本地 data/processed/，不会进入仓库 site 目录。
"""
import argparse, base64, hashlib, json, os
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "processed" / "analytics.json"
DST = ROOT / "site" / "vault.json"

ITER = 210_000

def derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITER, dklen=32)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--username", default="admin")
    args = ap.parse_args()
    if AESGCM is None:
        raise SystemExit("需要 cryptography 库: pip install cryptography")

    plaintext = SRC.read_bytes()
    salt = os.urandom(16)
    key = derive_key(args.password, salt)
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plaintext, associated_data=b"ST-vault-v1")
    vault = {
        "v": 1, "kdf": "PBKDF2-SHA256", "iter": ITER, "cipher": "AES-256-GCM",
        "username_sha256": hashlib.sha256(args.username.encode("utf-8")).hexdigest(),
        "salt": base64.b64encode(salt).decode(),
        "iv": base64.b64encode(iv).decode(),
        "data": base64.b64encode(ct).decode(),
        "verifier": base64.b64encode(hashlib.sha256(key).digest()).decode(),
    }
    DST.parent.mkdir(exist_ok=True)
    DST.write_text(json.dumps(vault), encoding="utf-8")
    (DST.with_suffix(".js")).write_text(
        "window.__VAULT__ = " + json.dumps(vault) + ";\n", encoding="utf-8")
    print("vault written:", DST, DST.stat().st_size, "bytes; plaintext:", len(plaintext))

if __name__ == "__main__":
    main()
