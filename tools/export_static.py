#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 ST v5 导出成可静态托管的快照站（给 GitHub Pages 用）。

前置：本机服务已启动（http://127.0.0.1:8300）。
用法（工作目录 = 仓库根 ST/）：
    uv run --with fastapi --with uvicorn --with yfinance python tools/export_static.py
    uv run ... python tools/export_static.py --universe-limit 12

产物：docs/
    index.html / app.js / style.css / echarts.min.js / static-shim.js
    snapshot-meta.json / search-index.json
    data/index.json + data/<sha1>.json（每个接口响应对应一个文件）
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"
DATA = OUT / "data"
WEB = ROOT / "web"
TOOLS = ROOT / "tools"
BASE = os.environ.get("ST_BASE", "http://127.0.0.1:8300").rstrip("/")

sys.path.insert(0, str(TOOLS))
from redact import dump as redact_dump  # noqa: E402  公开站点统一走脱敏出口

ACCOUNTS = ["all", "futu_main", "poems_sg", "poems_id"]
BASES = ["USD", "HKD", "SGD", "IDR"]
PLAIN = [
    "markets",
    "accounts",
    "recon",
    "trades",
    "meta",
    "hygiene",
    "insights",
    "discipline",
    "risk",
    "metrics",
    "poems",
    "poems/sg",
    "statement",
    "statement/rows?limit=50&offset=0",
    "watchlist",
    "universe",
    "health",
    "health/verify",
    "screen/warm/status",
    "symbols?limit=200",
]


def norm_query(query: str) -> str:
    if not query:
        return ""
    pairs = [p for p in query.split("&") if p]
    pairs.sort()
    return "&".join(pairs)


def key_of(path: str) -> str:
    path = path.lstrip("/")
    if path.startswith("api/"):
        path = path[4:]
    if "?" not in path:
        return path
    head, query = path.split("?", 1)
    query = norm_query(query)
    return f"{head}?{query}" if query else head


def fetch_json(path: str, timeout: int = 240):
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        body = resp.read()
    if "json" not in ctype:
        raise ValueError(f"non-json: {ctype}")
    return json.loads(body.decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe-limit", type=int, default=0, help="额外预取股票池前 N 只的 info/history")
    args = ap.parse_args()

    if OUT.exists():
        shutil.rmtree(OUT)
    DATA.mkdir(parents=True, exist_ok=True)

    index: dict[str, str] = {}
    payloads: dict[str, object] = {}
    failed: list[tuple[str, str]] = []

    def grab(path: str):
        key = key_of(path)
        if key in index:
            return payloads.get(key)
        try:
            payload = fetch_json("/api/" + path if not path.startswith("api/") else "/" + path)
        except Exception as exc:  # noqa: BLE001
            failed.append((key, str(exc)))
            print(f"  fail {key} -> {exc}")
            return None
        name = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12] + ".json"
        (DATA / name).write_text(redact_dump(payload), encoding="utf-8")
        index[key] = "data/" + name
        payloads[key] = payload
        print(f"  ok  {key}")
        return payload

    print("[1/6] 基础接口")
    for p in PLAIN:
        grab(p)

    print("[2/6] 组合接口（4 账户 × 4 记账币）")
    for acct in ACCOUNTS:
        for base in BASES:
            grab(f"overview?base={base}&account={acct}")
        grab(f"positions?account={acct}")
        grab(f"positions/legacy?account={acct}")
        grab(f"nav?account={acct}")

    print("[3/6] 选股快照")
    grab("screen")

    print("[4/6] 搜索索引")
    uni_rows = grab("universe") or []
    search_rows = []
    if isinstance(uni_rows, list):
        for row in uni_rows:
            if not isinstance(row, dict):
                continue
            search_rows.append({
                "symbol": row.get("symbol"),
                "shortname": row.get("name") or "",
                "name": row.get("name") or "",
                "market": row.get("market") or "",
                "market_name": row.get("market_name") or row.get("market") or "",
                "tradable": bool(row.get("tradable")),
            })

    cn_names: dict[str, str] = {}
    uni_csv = ROOT / "data" / "universe.csv"
    if uni_csv.exists():
        with uni_csv.open("r", encoding="utf-8-sig", newline="") as fh:
            for rec in csv.DictReader(fh):
                sym = (rec.get("symbol") or "").strip()
                if sym:
                    cn_names[sym] = (rec.get("name") or "").strip()
    for row in search_rows:
        row["name_cn"] = cn_names.get(str(row.get("symbol") or ""), "")

    (OUT / "search-index.json").write_text(redact_dump(search_rows), encoding="utf-8")
    print(f"  ok  search-index.json ({len(search_rows)} 条)")

    print("[5/6] 个股详情（持仓 + 观察池 + 股票池前 N）")
    targets: dict[str, str] = {}

    pos = grab("positions?account=all")
    if isinstance(pos, list):
        for row in pos:
            sym, mk = (row or {}).get("symbol"), (row or {}).get("market")
            if sym and mk and sym not in targets:
                targets[sym] = mk

    watch = grab("watchlist")
    if isinstance(watch, list):
        for row in watch:
            sym, mk = (row or {}).get("symbol"), (row or {}).get("market")
            if sym and mk and sym not in targets:
                targets[sym] = mk

    if args.universe_limit > 0:
        for row in search_rows[: args.universe_limit]:
            sym, mk = row.get("symbol"), row.get("market")
            if sym and mk and sym not in targets:
                targets[sym] = mk

    for sym, mk in targets.items():
        grab(f"info?symbol={quote(sym)}&market_code={mk}")
        grab(f"history?symbol={quote(sym)}&market_code={mk}&period=1y&interval=1d")

    print("[6/6] 静态资源")
    shutil.copy2(WEB / "style.css", OUT / "style.css")
    shutil.copy2(WEB / "app.js", OUT / "app.js")
    shutil.copy2(WEB / "echarts.min.js", OUT / "echarts.min.js")
    shutil.copy2(TOOLS / "static_shim.js", OUT / "static-shim.js")

    html = (WEB / "index.html").read_text(encoding="utf-8")
    build_tag = datetime.now().strftime("%Y%m%d%H%M")
    if "static-shim.js" not in html:
        html = html.replace(
            '<script src="echarts.min.js',
            f'<script src="static-shim.js?v={build_tag}"></script>\n<script src="echarts.min.js',
            1,
        )
    (OUT / "index.html").write_text(html, encoding="utf-8")

    meta = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "readonly": True,
        "source": "ST v5 /api/* snapshot",
        "base": BASE,
        "files": len(index),
        "failed": failed,
    }
    (OUT / "snapshot-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")

    print(f"\n完成：{len(index)} 个接口快照，失败 {len(failed)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
