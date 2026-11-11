# -*- coding: utf-8 -*-
"""核对 data/evidence 下的归档文件与 README 清单里的 sha256 是否一致。

用法：python tools/verify_evidence.py
退出码 0 = 全部一致；1 = 有文件缺失或哈希不符。
"""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "data" / "evidence"
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([\d.]+) KB\s*\|\s*`([0-9a-f]{16})`\s*\|")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    readme = EV / "README.md"
    if not readme.exists():
        print("[FAIL] 缺少 data/evidence/README.md")
        return 1
    rows = [ROW.match(line) for line in readme.read_text(encoding="utf-8").splitlines()]
    rows = [m for m in rows if m]
    if not rows:
        print("[FAIL] README 清单为空")
        return 1
    bad = 0
    for m in rows:
        name, kb, short = m.group(1), m.group(2), m.group(3)
        p = EV / name
        if not p.exists():
            print("[MISS] %s" % name)
            bad += 1
            continue
        h = sha256(p)
        size_kb = round(p.stat().st_size / 1024, 1)
        ok_hash = h.startswith(short)
        ok_size = abs(size_kb - float(kb)) <= 0.15
        if not (ok_hash and ok_size):
            print("[BAD ] %-28s %s / %.1f KB（清单 %s / %s KB）" % (name, h[:16], size_kb, short, kb))
            bad += 1
    print("%s %d 个文件，%d 处不一致" % ("PASS" if not bad else "FAIL", len(rows), bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
