# -*- coding: utf-8 -*-
r"""为 data/ 目录生成清单（路径、字节、sha256、最后修改时间）。

用途：让仓库里的数据自证完整——任何人 clone 后跑 `tools/verify_data.py`，
就能确认仓库里的每份文件与生成清单时一致，无需再回到原始邮箱/券商门户核对。

哈希口径（重要）：
  - 二进制（PDF / .db / 图片等）按**原始字节**哈希；
  - 文本（CSV / TXT / JSON / MD / PY 等）先做 **CRLF → LF 归一化**再哈希。
    原因：Windows 上 `core.autocrlf=true` 会在 checkout 时改写换行，
    原始字节哈希在不同机器/不同 clone 之间必然对不上；归一化后跨平台一致。

用法：
    python tools/manifest_data.py

输出：
    data/manifest.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MANIFEST = DATA / "manifest.json"

# 不进清单的：清单自身、以及运行期临时文件
EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_FILES = {"manifest.json"}
EXCLUDE_SUFFIX = (".db-wal", ".db-shm")

TEXT_SUFFIX = {".csv", ".txt", ".json", ".md", ".py", ".js", ".html", ".css", ".bat", ".log", ".yaml", ".yml"}


def sha256_file(path: Path) -> tuple[str, str]:
    """返回 (sha256, mode)；mode 为 raw 或 text-lf。"""
    h = hashlib.sha256()
    if path.suffix.lower() in TEXT_SUFFIX:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            h.update(raw)
            return h.hexdigest(), "raw"
        h.update(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))
        return h.hexdigest(), "text-lf"
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest(), "raw"


def main() -> int:
    entries = []
    groups: dict[str, dict[str, float]] = {}
    for path in sorted(DATA.rglob("*")):
        if not path.is_file():
            continue
        if path.name in EXCLUDE_FILES or path.name.endswith(EXCLUDE_SUFFIX):
            continue
        rel = path.relative_to(DATA).as_posix()
        top = rel.split("/")[0] if "/" in rel else rel
        if top in EXCLUDE_DIRS:
            continue
        size = path.stat().st_size
        digest, mode = sha256_file(path)
        entries.append({
            "path": f"data/{rel}",
            "bytes": size,
            "sha256": digest,
            "hash_mode": mode,
            "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            .astimezone()
            .isoformat(timespec="seconds"),
        })
        g = groups.setdefault(top, {"files": 0, "bytes": 0})
        g["files"] += 1
        g["bytes"] += size

    doc = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "note": "data/ 全量清单。sha256 口径：二进制按原始字节；文本先 CRLF→LF 归一化（跨平台稳定）。"
                "用 `python tools/verify_data.py` 复核。",
        "total": {"files": len(entries), "bytes": sum(e["bytes"] for e in entries)},
        "groups": {k: {"files": v["files"], "bytes": v["bytes"]} for k, v in sorted(groups.items())},
        "files": entries,
    }
    MANIFEST.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"清单已生成：{MANIFEST.relative_to(ROOT)}")
    for k, v in doc["groups"].items():
        print(f"  {k:<16} {v['files']:>4} files  {v['bytes']/1024:>9.1f} KB")
    print(f"  合计             {doc['total']['files']:>4} files  {doc['total']['bytes']/1024:>9.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
