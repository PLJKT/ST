# -*- coding: utf-8 -*-
"""行情与基本面：yfinance 封装 + 本地缓存。所有对外数字都带 as-of 与来源。"""
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

import yfinance as yf

from . import config, db

_lock = threading.Lock()
_fx_mem: dict[str, float] = {}
_fx_at = 0.0


def now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def resolve(symbol: str, market: str | None = None) -> str:
    if not symbol:
        return ""
    s = symbol.strip().upper()
    if s.endswith((".HK", ".JK", ".SS", ".SZ", ".SI")):
        return s
    if not market:
        return s
    if market == "HK":
        return (s.lstrip("0") or "0").zfill(4) + ".HK"
    if market == "ID":
        return s + ".JK"
    if market == "CN":
        return s + (".SS" if s.startswith("6") else ".SZ")
    if market == "SG":
        return s + ".SI"
    return s


def market_of_symbol(sym: str) -> tuple[str, str]:
    """(market, ccy)"""
    s = sym.upper()
    for suf, mkt in config.SUFFIX_MARKET.items():
        if s.endswith("." + suf):
            return mkt, config.SUFFIX_CCY[suf]
    return "US", "USD"


def _read_cache(sym: str, ttl: int) -> dict | None:
    row = db.one("SELECT * FROM quotes WHERE symbol=?", [sym])
    if not row:
        return None
    if time.time() - (row.get("fetched_at") or 0) > ttl:
        return None
    return row


def quote(symbol: str, market: str | None = None, use_cache: bool = True) -> dict:
    sym = resolve(symbol, market)
    if use_cache:
        cached = _read_cache(sym, config.QUOTE_TTL)
        if cached:
            return cached
    mk, ccy = market_of_symbol(sym)
    out: dict[str, Any] = {"symbol": sym, "price": None, "prev_close": None, "ccy": ccy,
                           "market": mk, "as_of": now_iso(), "source": "yahoo", "ok": False}
    try:
        t = yf.Ticker(sym)
        fi = t.fast_info
        for key, field in (("last_price", "price"), ("previous_close", "prev_close"),
                           ("open", "open"), ("day_high", "day_high"), ("day_low", "day_low"),
                           ("year_high", "year_high"), ("year_low", "year_low"),
                           ("last_volume", "volume")):
            try:
                out[field] = fi[key]
            except Exception:
                pass
        try:
            out["ccy"] = fi["currency"] or ccy
        except Exception:
            pass
        out["ok"] = out["price"] is not None
    except Exception as e:
        out["error"] = repr(e)[:200]
    out["fetched_at"] = time.time()
    if out["price"] is not None:
        db.replace_into("quotes", {k: out.get(k) for k in
                                   ("symbol", "price", "prev_close", "open", "day_high",
                                    "day_low", "year_high", "year_low", "volume",
                                    "ccy", "market", "as_of", "fetched_at", "source")})
    return out


def history(symbol: str, market: str | None = None, period: str = "6mo", interval: str = "1d") -> dict:
    sym = resolve(symbol, market)
    # yfinance 只认 1mo/3mo 这类写法，前端习惯写 1m/3m，这里统一收口
    period = {"1m": "1mo", "3m": "3mo", "1w": "5d", "1d": "1d"}.get(period, period)
    try:
        df = yf.Ticker(sym).history(period=period, interval=interval, auto_adjust=False)
    except Exception as e:
        return {"symbol": sym, "error": repr(e)[:200], "bars": []}
    if df is None or df.empty:
        return {"symbol": sym, "bars": [], "error": "no_data",
                "hint": f"yfinance 对 {sym} / period={period} 未返回数据"}
    bars = []
    for idx, row in df.iterrows():
        # 用交易日（index 的日期）而不是 UTC 时间戳，避免亚洲市场日期前移一天
        bars.append({
            "t": idx.date().isoformat(),
            "o": round(float(row["Open"]), 4), "h": round(float(row["High"]), 4),
            "l": round(float(row["Low"]), 4), "c": round(float(row["Close"]), 4),
            "v": float(row["Volume"]) if "Volume" in df.columns else None,
        })
    return {"symbol": sym, "bars": bars, "interval": interval, "period": period,
            "first": bars[0]["t"], "last": bars[-1]["t"], "as_of": now_iso()}


def search(q: str, limit: int = 10) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    from .aliases import ALIASES
    markets = {m["code"]: m for m in db.query("SELECT * FROM markets")}
    out = []
    alias_syms = ALIASES.get(q)
    if alias_syms:
        if isinstance(alias_syms, str):
            alias_syms = [alias_syms]
        for alias_sym in alias_syms:
            mk, ccy = market_of_symbol(alias_sym)
            out.append({
                "symbol": alias_sym, "shortname": q, "exchange": "", "exchDisp": "",
                "market": mk, "market_name": markets.get(mk, {}).get("name", mk),
                "tradable": bool(markets.get(mk, {}).get("tradable", 0)),
                "ccy": markets.get(mk, {}).get("ccy", ccy), "type": "EQUITY",
            })
    # Yahoo 的 search 接口偶发限流/超时，一次失败就返回空会让用户以为查不到股票，
    # 这里重试一次再放弃。
    quotes = []
    for attempt in range(2):
        try:
            res = yf.Search(q, max_results=max(limit * 3, 15))
            quotes = res.quotes or []
            break
        except Exception:
            if attempt == 0:
                time.sleep(0.8)
    if not quotes:
        return out[:limit]
    target_exch = {k: v for k, v in config.EXCHANGE_TO_MARKET.items() if v != "SG"}
    seen = {r["symbol"].upper() for r in out}
    for item in quotes:
        sym = item.get("symbol")
        if not sym or sym.upper() in seen:
            continue
        exch = (item.get("exchange") or "").upper()
        mk = target_exch.get(exch)
        if not mk:
            # 没登记过的交易所（多伦多/墨西哥/布宜诺斯艾利斯…）直接丢弃，
            # 只有带明确后缀（.HK/.JK/.SS/.SZ）的才按后缀落市场
            suffix = sym.rsplit(".", 1)[-1].upper() if "." in sym else ""
            if suffix in config.SUFFIX_MARKET:
                mk, _ = market_of_symbol(sym)
            else:
                continue
            if mk == "SG":
                continue
        ccy = config.SUFFIX_CCY.get(sym.rsplit(".", 1)[-1].upper(), "USD") if "." in sym else "USD"
        if mk in markets:
            ccy = markets[mk]["ccy"]
        out.append({
            "symbol": sym,
            "shortname": item.get("shortname") or item.get("longname") or sym,
            "exchange": item.get("exchange"),
            "exchDisp": item.get("exchDisp"),
            "market": mk,
            "market_name": markets.get(mk, {}).get("name", mk),
            "tradable": bool(markets.get(mk, {}).get("tradable", 0)),
            "ccy": ccy,
            "type": item.get("quoteType"),
        })
        seen.add(sym.upper())
    # 排序：精确代码 > 可交易市场 > 参考市场
    out.sort(key=lambda r: (
        0 if r.get("shortname") == q else 1,
        0 if r["symbol"].upper() == q.upper() else 1,
        0 if r["market"] in ("US", "HK", "ID") else 1,
        len(r["symbol"]),
    ))
    return out[:limit]


def fx_rates() -> dict:
    global _fx_mem, _fx_at
    with _lock:
        if _fx_mem and time.time() - _fx_at < 3600:
            return _fx_mem
    rates = {"USD": 1.0}
    for ccy, pair in config.FX_PAIRS.items():
        rate = None
        try:
            fi = yf.Ticker(pair).fast_info
            p = fi["last_price"]
            if p and 0 < float(p) < 1:
                rate = float(p)
        except Exception:
            rate = None
        rates[ccy] = round(rate, 10) if rate else config.FX_FALLBACK[ccy]
        if rate:
            db.replace_into("fx_cache", {"pair": pair, "rate": float(rate), "updated_at": time.time()})
    with _lock:
        _fx_mem, _fx_at = rates, time.time()
    return rates


INFO_FIELDS = [
    "longName", "shortName", "sector", "industry", "marketCap", "currency", "exchange",
    "trailingPE", "forwardPE", "priceToBook", "priceToSalesTrailing12Months",
    "dividendYield", "returnOnEquity", "profitMargins", "grossMargins", "debtToEquity",
    "revenueGrowth", "earningsGrowth", "currentPrice", "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow", "averageVolume", "beta", "sharesOutstanding", "trailingEps",
    "bookValue", "totalRevenue", "freeCashflow", "heldPercentInsiders",
]


def fundamentals(symbol: str, market: str | None = None, use_cache: bool = True) -> dict:
    sym = resolve(symbol, market)
    if use_cache:
        row = db.one("SELECT * FROM fundamentals WHERE symbol=?", [sym])
        if row and time.time() - (row.get("updated_at") or 0) < config.INFO_TTL:
            try:
                payload = json.loads(row["payload"])
                payload["_cached"] = True
                return payload
            except Exception:
                pass
    payload: dict[str, Any] = {"symbol": sym, "ok": False, "fetched_at": now_iso()}
    try:
        info = yf.Ticker(sym).get_info() or {}
        for f in INFO_FIELDS:
            if f in info:
                payload[f] = info[f]
        payload["ok"] = bool(payload.get("currentPrice") or payload.get("marketCap"))
    except Exception as e:
        payload["error"] = repr(e)[:200]
    db.replace_into("fundamentals", {
        "symbol": sym, "payload": json.dumps(payload, ensure_ascii=False),
        "updated_at": time.time(), "ok": 1 if payload["ok"] else 0,
    })
    return payload


# ---------------------------------------------------------------- 定价三层

def override(symbol: str, market: str | None = None) -> dict | None:
    """人工定价（最高优先）。没有就返回 None。"""
    sym = resolve(symbol, market)
    if not sym:
        return None
    return db.one("SELECT * FROM price_overrides WHERE symbol=?", [sym]) or None


def set_override(symbol: str, price, note: str = "", asof: str | None = None,
                 market: str | None = None) -> dict:
    sym = resolve(symbol, market)
    if not sym:
        raise ValueError("代码不能为空")
    try:
        p = float(price)
    except (TypeError, ValueError):
        raise ValueError("价格必须是数字")
    if p <= 0:
        raise ValueError("价格必须大于 0")
    ts = now_iso()
    db.replace_into("price_overrides", {
        "symbol": sym, "price": round(p, 6), "note": (note or "").strip(),
        "asof": asof or ts[:16], "updated_at": ts,
    })
    return db.one("SELECT * FROM price_overrides WHERE symbol=?", [sym]) or {}


def clear_override(symbol: str, market: str | None = None) -> dict:
    sym = resolve(symbol, market)
    db.execute("DELETE FROM price_overrides WHERE symbol=?", [sym])
    return {"symbol": sym, "cleared": True}


def list_overrides() -> list[dict]:
    return db.query("SELECT * FROM price_overrides ORDER BY symbol")


def last_close(symbol: str, market: str | None = None) -> dict | None:
    sym = resolve(symbol, market)
    if not sym:
        return None
    return db.one("SELECT * FROM daily_closes WHERE symbol=?", [sym]) or None


def set_close(symbol: str, close, trade_date: str, ccy: str = "",
              source: str = "yahoo") -> dict:
    # 收盘价按 Yahoo 代码存（调用方负责传入带后缀的完整代码）
    sym = (symbol or "").strip().upper()
    try:
        c = float(close)
    except (TypeError, ValueError):
        return {}
    if c <= 0:
        return {}
    db.replace_into("daily_closes", {
        "symbol": sym, "close": round(c, 6), "trade_date": trade_date,
        "ccy": ccy, "source": source, "updated_at": now_iso(),
    })
    return db.one("SELECT * FROM daily_closes WHERE symbol=?", [sym]) or {}


def refresh(symbols: list[tuple[str, str]]) -> list[dict]:
    """强制拉最新报价（跳过 5 分钟缓存），返回每只的结果。"""
    out = []
    for sym, mk in symbols:
        if not sym:
            continue
        y = resolve(sym, mk)
        try:
            db.execute("DELETE FROM quotes WHERE symbol=?", [y])
        except Exception:
            pass
        q = quote(sym, mk, use_cache=False)
        out.append({"symbol": y, "price": q.get("price"), "as_of": q.get("as_of"),
                    "ok": bool(q.get("ok")), "error": q.get("error")})
    return out
