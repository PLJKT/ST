# -*- coding: utf-8 -*-
r"""校验 data/ 是否与 data/manifest.json 完全一致。

任何人 clone 仓库后跑一次，就能确认"仓库里的数据 == 生成清单时的数据"，
不需要回到原始邮箱或券商门户重新取证。

用法（仓库根目录）：
    python tools/verify_data.py

输出：逐条差异（缺失 / 哈希不符 / 多余文件），最后一行 PASS 或 FAIL。
退出码：0 = 全部一致；1 = 有差异。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MANIFEST = DATA / "manifest.json"

TEXT_SUFFIX = {".csv", ".txt", ".json", ".md", ".py", ".js", ".html", ".css", ".bat", ".log", ".yaml", ".yml"}
EXCLUDE_FILES = {"manifest.json"}
EXCLUDE_SUFFIX = (".db-wal", ".db-shm")

sys.path.insert(0, str(ROOT / "tools"))
from manifest_data import sha256_file  # 同一套哈希口径，避免两边实现漂移


def main() -> int:
    if not MANIFEST.exists():
        print(f"缺少清单：{MANIFEST}")
        return 1
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    problems = []

    for e in doc["files"]:
        p = ROOT / e["path"]
        if not p.exists():
            problems.append(("missing", e["path"], ""))
            continue
        digest, _ = sha256_file(p)
        if digest != e["sha256"]:
            problems.append(("sha256", e["path"], f"expect {e['sha256'][:12]} got {digest[:12]}"))

    listed = {e["path"] for e in doc["files"]}
    for p in DATA.rglob("*"):
        if not p.is_file() or p.name in EXCLUDE_FILES or p.name.endswith(EXCLUDE_SUFFIX):
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel not in listed:
            problems.append(("extra", rel, "不在清单内（新增文件请重跑 manifest_data.py）"))

    for kind, path, detail in problems:
        print(f"  [{kind}] {path} {detail}")

    total = doc["total"]
    ok = not problems
    print(f"{'PASS' if ok else 'FAIL'}: {total['files']} files / {total['bytes']/1024:.1f} KB, "
          f"差异 {len(problems)} 条")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
