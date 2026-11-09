# -*- coding: utf-8 -*-
"""把 data/st.db 的全部业务表导出成 CSV 快照，并生成 MANIFEST.json。

为什么需要：
- st.db 是二进制，git diff 看不到内容变化；
- 把每张表导成 UTF-8 CSV，任何一次数据变动在 GitHub 上都能逐行 diff；
- MANIFEST.json 记录导出行数、每份文件的 sha256，便于核对"仓库里的数据 == 本地数据"。

用法：
    uv run --with fastapi --with uvicorn --with yfinance python tools\export_data_pack.py

输出：
    data/snapshot/<table>.csv
    data/snapshot/MANIFEST.json
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "st.db"
OUT = ROOT / "data" / "snapshot"

# 纯缓存/可再生表不进入仓库快照（行情、汇率、基本面），避免把第三方行情无限堆积。
SKIP_TABLES = {"quotes", "fundamentals", "fx_cache", "daily_closes", "symbol_stats", "watchlist"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not DB.exists():
        print(f"数据库不存在：{DB}")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    manifest = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "db_file": "data/st.db",
        "db_bytes": DB.stat().st_size,
        "db_sha256": sha256_file(DB),
        "tables": {},
        "skipped": sorted(SKIP_TABLES),
    }

    total_rows = 0
    for name in tables:
        if name in SKIP_TABLES:
            continue
        rows = cur.execute(f"SELECT * FROM {name}").fetchall()
        cols = [d[0] for d in cur.execute(f"SELECT * FROM {name} LIMIT 1").description] if rows else []
        if not cols:  # 空表也要保留表头，否则后续不知结构
            cols = [r[1] for r in cur.execute(f"PRAGMA table_info({name})")]
        path = OUT / f"{name}.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for row in rows:
                w.writerow(["" if row[c] is None else row[c] for c in cols])
        manifest["tables"][name] = {
            "csv": f"data/snapshot/{name}.csv",
            "rows": len(rows),
            "columns": len(cols),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        total_rows += len(rows)
        print(f"  {name:<18} {len(rows):>7} rows -> {path.name}")

    manifest["table_count"] = len(manifest["tables"])
    manifest["total_rows"] = total_rows
    (OUT / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n完成：{manifest['table_count']} 张表 / {total_rows} 行；数据库 {manifest['db_bytes']/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
