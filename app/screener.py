# -*- coding: utf-8 -*-
"""条件选股：本地股票池 + 基本面缓存 + 覆盖率护栏。"""
import csv
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import config, db, market

_warm_state = {"running": False, "done": 0, "total": 0, "started": None, "finished": None,
               "errors": 0}
_warm_lock = threading.Lock()


def load_universe() -> list[dict]:
    if not config.UNIVERSE_PATH.exists():
        return []
    rows = []
    with config.UNIVERSE_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if not r.get("symbol"):
                continue
            rows.append({"symbol": r["symbol"].strip(), "name": (r.get("name") or "").strip(),
                         "market": (r.get("market") or "US").strip().upper()})
    return rows


def _warm_one(item: dict) -> bool:
    try:
        p = market.fundamentals(item["symbol"], item["market"], use_cache=False)
        return bool(p.get("ok"))
    except Exception:
        return False


def warm(limit: int | None = None) -> dict:
    with _warm_lock:
        if _warm_state["running"]:
            return dict(_warm_state)
        universe = load_universe()
        _warm_state.update({"running": True, "done": 0, "total": len(universe),
                            "started": market.now_iso(), "finished": None, "errors": 0})

    def _run():
        try:
            with ThreadPoolExecutor(max_workers=6) as ex:
                for ok in ex.map(_warm_one, universe):
                    with _warm_lock:
                        _warm_state["done"] += 1
                        if not ok:
                            _warm_state["errors"] += 1
        finally:
            with _warm_lock:
                _warm_state["running"] = False
                _warm_state["finished"] = market.now_iso()

    threading.Thread(target=_run, daemon=True).start()
    return dict(_warm_state)


def warm_status() -> dict:
    return dict(_warm_state)


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _normalize(ft: dict) -> dict:
    """把 yfinance 的字段口径统一：涨跌幅/比率类统一成百分数，并剔除明显坏值。"""
    roe = _num(ft.get("returnOnEquity"))
    gm = _num(ft.get("grossMargins"))
    pm = _num(ft.get("profitMargins"))
    dy = _num(ft.get("dividendYield"))
    de = _num(ft.get("debtToEquity"))
    rg = _num(ft.get("revenueGrowth"))
    eg = _num(ft.get("earningsGrowth"))
    pe = _num(ft.get("trailingPE"))
    pb = _num(ft.get("priceToBook"))
    # yfinance 对小盘印尼股常给出 5 位数 PB / 负 PE，这类值不能进筛选。
    # PE 上限放到 1000：特斯拉（TSLA）这类真实高估值股 TTM PE 已到 390，
    # 用 200 会把真数据误杀；负 PE（亏损）与 4 位数垃圾值仍然剔除。
    if pe is not None and (pe <= 0 or pe > 1000):
        pe = None
    if pb is not None and (pb <= 0 or pb > 100):
        pb = None
    # 被剔除的原因留在字段里，界面可据此解释「为什么这只没有 PE/PB」。
    # 注意穿透股经常是 trailingPE 直接给 None（而不是给负数），要用 EPS 才能判亏损。
    raw_pe = _num(ft.get("trailingPE"))
    eps = _num(ft.get("trailingEps"))
    pe_note = None
    if pe is None:
        if raw_pe is not None and raw_pe <= 0:
            pe_note = "亏损（EPS<0）"
        elif eps is not None and eps <= 0:
            pe_note = "亏损（EPS<0，数据源未给 PE）"
        elif raw_pe is not None:
            pe_note = "数据源给出异常 PE"
        elif eps is not None and eps > 0:
            pe_note = f"数据源未给 PE（EPS {eps:g}）"
    pb_note = None
    raw_pb = _num(ft.get("priceToBook"))
    bv = _num(ft.get("bookValue"))
    if pb is None:
        if bv is not None and bv <= 0:
            pb_note = "净资产为负"
        elif raw_pb is not None:
            pb_note = "数据源口径异常（每股净资产量级错误）"
    return {
        "price": _num(ft.get("currentPrice")),
        "mcap": _num(ft.get("marketCap")),
        "pe": pe,
        "pb": pb,
        "pe_note": pe_note,
        "pb_note": pb_note,
        "ps": _num(ft.get("priceToSalesTrailing12Months")),
        "div_yield": dy,
        "roe": round(roe * 100, 2) if roe is not None and abs(roe) < 2 else roe,
        "gross_margin": round(gm * 100, 2) if gm is not None else None,
        "profit_margin": round(pm * 100, 2) if pm is not None else None,
        "debt_to_equity": de,
        "rev_growth": round(rg * 100, 2) if rg is not None else None,
        "eps_growth": round(eg * 100, 2) if eg is not None else None,
        "high52": _num(ft.get("fiftyTwoWeekHigh")),
        "low52": _num(ft.get("fiftyTwoWeekLow")),
        "beta": _num(ft.get("beta")),
        "sector": ft.get("sector"),
        "industry": ft.get("industry"),
        "name": ft.get("longName") or ft.get("shortName"),
        "ccy": ft.get("currency"),
    }


DIMENSIONS = ["pe", "pb", "div_yield", "roe", "mcap", "price"]


def universe_rows() -> list[dict]:
    """股票池当前缓存状态 + 归一化指标。"""
    uni = load_universe()
    markets = {m["code"]: m for m in db.query("SELECT * FROM markets")}
    out = []
    for u in uni:
        ft = market.fundamentals(u["symbol"], u["market"], use_cache=True)
        cached = db.one("SELECT updated_at, ok FROM fundamentals WHERE symbol=?",
                        [market.resolve(u["symbol"], u["market"])])
        norm = _normalize(ft) if ft.get("ok") else {}
        out.append({
            "symbol": u["symbol"], "name": u["name"] or norm.get("name"),
            "market": u["market"], "market_name": markets.get(u["market"], {}).get("name", u["market"]),
            "tradable": bool(markets.get(u["market"], {}).get("tradable", 0)),
            "cached": bool(cached), "ok": bool(ft.get("ok")),
            **norm,
        })
    return out


def coverage(rows: list[dict]) -> dict:
    total = len(rows)
    field_counts = {f: sum(1 for r in rows if r.get(f) is not None) for f in DIMENSIONS}
    return {
        "total": total,
        "fields": {f: {"have": c, "pct": round(c / total * 100, 1) if total else 0} for f, c in field_counts.items()},
    }


FIELD_LABELS = {"pe": "PE", "pb": "PB", "div_yield": "股息率", "roe": "ROE", "mcap": "市值", "price": "价格"}


def screen(filters: dict[str, Any]) -> dict:
    rows = universe_rows()
    markets_sel = filters.get("markets") or ["US", "HK", "ID"]
    allow_missing = bool(filters.get("allow_missing", False))

    rules = []
    for field, key in (("pe", "pe_max"), ("pb", "pb_max"), ("div_yield", "div_min"),
                       ("roe", "roe_min"), ("price", "price_max")):
        if filters.get(key) not in (None, ""):
            rules.append((field, key, float(filters[key])))
    if filters.get("mcap_min"):
        rules.append(("mcap", "mcap_min", float(filters["mcap_min"]) * 1e9))

    results, excluded_missing = [], {f: 0 for f in DIMENSIONS}
    for r in rows:
        if r["market"] not in markets_sel:
            continue
        if not r["ok"]:
            continue
        passed, reasons, hits = True, [], 0
        for field, key, threshold in rules:
            val = r.get(field)
            if val is None:
                excluded_missing[field] += 1
                if allow_missing:
                    continue
                passed = False
                reasons.append(f"{FIELD_LABELS[field]} 缺失")
                continue
            if key.endswith("_max") and val > threshold:
                passed = False
                reasons.append(f"{FIELD_LABELS[field]} {val:g} > {threshold:g}")
            elif key.endswith("_min") and val < threshold:
                passed = False
                reasons.append(f"{FIELD_LABELS[field]} {val:g} < {threshold:g}")
            else:
                hits += 1
        r["hits"] = hits
        r["miss"] = reasons
        if passed:
            results.append(r)

    # 先按市场分组，再按 PE 从小到大，跨市场结果不会混在一起
    order = {"US": 0, "HK": 1, "ID": 2, "CN": 3}
    results.sort(key=lambda x: (order.get(x["market"], 9), x.get("pe") is None, x.get("pe") or 0))
    return {
        "rows": results,
        "matched": len(results),
        "evaluated": len([r for r in rows if r["market"] in markets_sel and r["ok"]]),
        "coverage": coverage([r for r in rows if r["market"] in markets_sel]),
        "excluded_missing": excluded_missing,
        "rules": [{"field": f, "key": k, "value": v} for f, k, v in rules],
        "allow_missing": allow_missing,
        "warm": warm_status(),
        "note": "缺失值默认排除；打开「保留缺失」会把没有该字段的标的也留下并标灰。",
    }


# ---------- 观察池 ----------
def watchlist() -> list[dict]:
    return db.query("SELECT * FROM watchlist ORDER BY created_at DESC")


def watch_add(symbol: str, name: str = "", market_code: str = "US", note: str = "",
              target: float | None = None, stop: float | None = None) -> dict:
    sym = market.resolve(symbol, market_code)
    db.replace_into("watchlist", {
        "symbol": sym, "name": name, "market": market_code, "note": note,
        "target": target, "stop": stop, "created_at": market.now_iso(),
    })
    return db.one("SELECT * FROM watchlist WHERE symbol=?", [sym]) or {}


def watch_remove(symbol: str) -> None:
    db.execute("DELETE FROM watchlist WHERE symbol=?", [symbol])
