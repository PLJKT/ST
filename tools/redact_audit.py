# -*- coding: utf-8 -*-
r"""发布前硬校验：docs/ 里不许出现任何可定位到本人的信息。

用法（仓库根目录）：
    python tools/redact_audit.py          # 检查 docs/
    python tools/redact_audit.py --path  docs

退出码 0 = 干净，1 = 有命中（命中即禁止部署）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from redact import assert_clean  # noqa: E402

CHECK_SUFFIX = {".json", ".html", ".js", ".css", ".svg", ".txt", ".md"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="docs", help="要检查的目录（默认 docs）")
    args = ap.parse_args()

    target = (ROOT / args.path).resolve()
    if not target.exists():
        print(f"目录不存在：{target}")
        return 1

    pairs: list[tuple[str, str]] = []
    for p in sorted(target.rglob("*")):
        if p.is_file() and p.suffix.lower() in CHECK_SUFFIX:
            try:
                pairs.append((str(p.relative_to(target)), p.read_text(encoding="utf-8", errors="ignore")))
            except OSError as exc:
                print(f"  读取失败 {p}: {exc}")

    problems = assert_clean(pairs)
    for name, label, frag in problems:
        print(f"  [{label}] {name} -> {frag}")

    print(f"{'PASS' if not problems else 'FAIL'}：检查 {len(pairs)} 个文件，命中 {len(problems)} 处")
    if problems:
        print("命中即禁止发布：先修 tools/redact.py 的规则，再重跑 export_static.py。")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
