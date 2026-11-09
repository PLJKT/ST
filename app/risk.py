# -*- coding: utf-8 -*-
"""盘面 / 逐仓盈亏比与止盈止损 / 对冲与回本。

三块都只用真实数据：
- 逐仓：日线 ATR(14) + 20 日结构位 → 止损/止盈，盈亏比 = 有利距离/不利距离，
  概率 = 无漂移随机游走首达概率（先碰哪一边），另附该标的历史胜率。
- 盘面：各市场指数（美股 / 港股 / 印尼股 / A 股参考）现价、涨跌、20/50/200 日均线位置、
  距 52 周高回撤，加上组合在该市场的敞口与浮盈。
- 对冲：逐仓对本地基准回归 beta，折算净 beta 敞口，给出 SPY / 2800.HK 的对冲名义与股数，
  并按 -10% / -20% / +10% 三个情景算未对冲 vs 对冲后的权益变化；回本给出每仓所需价位。
"""
import math
import time
from concurrent.futures import ThreadPoolExecutor

from . import market, portfolio

BENCH = {"US": ("^GSPC", "标普 500"), "HK": ("^HSI", "恒生指数"),
         "ID": ("^JKSE", "雅加达综合"), "CN": ("000001.SS", "上证指数")}
HEDGE = {"US": ("SPY", "SPY（标普 500 ETF）"), "HK": ("2800.HK", "2800.HK 盈富基金")}

# 杠杆 ETF 用产品本身的倍数，不用回归 beta：每日重置产品的路径依赖会让回归值失真
LEVER = {"SQQQ": -3, "SOXS": -3, "SPXU": -3, "TZA": -3, "YANG": -3,
         "SOXL": 3, "TQQQ": 3, "SPXL": 3, "TNA": 3, "LABU": 3, "FAS": 3, "YINN": 3,
         "TSLL": 2, "NUGT": 2, "DUST": -2, "UVXY": 1.5, "SVXY": -0.5}

BOARD = [
    ("US", "美股", [("^GSPC", "标普 500"), ("^IXIC", "纳斯达克"), ("^DJI", "道琼斯"),
                    ("^VIX", "VIX 波动率")]),
    ("HK", "港股", [("^HSI", "恒生指数"), ("^HSCE", "恒生国企")]),
    ("ID", "印尼股", [("^JKSE", "雅加达综合")]),
    ("CN", "A 股（参考，不可交易）", [("000001.SS", "上证指数"), ("399001.SZ", "深证成指")]),
]

_TTL = 1800
_cache: dict[str, tuple[float, list]] = {}


def _bars(sym: str, mk: str | None = None, period: str = "1y") -> list:
    key = f"{sym}|{period}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    # 指数（^GSPC / ^HSI / ^JKSE）和已带后缀的代码不能被 resolve 再拼一次市场后缀
    mk_arg = None if (sym.startswith("^") or "." in sym) else mk
    try:
        data = market.history(sym, mk_arg, period=period)
        bars = data.get("bars") or []
    except Exception:
        bars = []
    _cache[key] = (time.time(), bars)
    return bars


def _warm(specs: list[tuple[str, str | None]], period: str = "1y"):
    """并行拉日线，避免 16 个标的串行把页面拖到十几秒。"""
    todo = [s for s in specs if _cache.get(f"{s[0]}|{period}") is None
            or time.time() - _cache[f"{s[0]}|{period}"][0] >= _TTL]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda s: _bars(s[0], s[1], period), todo))


def _atr(bars: list, n: int = 14) -> float:
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    return sum(trs[-n:]) / len(trs[-n:])


def _ma(bars: list, n: int) -> float | None:
    closes = [b["c"] for b in bars]
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _ret_map(bars: list) -> dict[str, float]:
    out = {}
    for i in range(1, len(bars)):
        prev = bars[i - 1]["c"]
        if prev:
            out[bars[i]["t"]] = bars[i]["c"] / prev - 1
    return out


def _beta(sym_bars: list, bench_bars: list) -> float | None:
    a, b = _ret_map(sym_bars), _ret_map(bench_bars)
    common = sorted(set(a) & set(b))[-120:]
    if len(common) < 30:
        return None
    xs = [b[d] for d in common]
    ys = [a[d] for d in common]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    var = sum((x - mx) ** 2 for x in xs)
    return (cov / var) if var else None


_hist_cache: dict[str, dict] = {}
_hist_at = 0.0


def _hist_win(sym: str) -> dict:
    global _hist_at
    from .insights import _dated_items, _symbol_key
    if time.time() - _hist_at > 600:
        _hist_cache.clear()
        for i in _dated_items():
            k = _symbol_key(i["symbol"])
            d = _hist_cache.setdefault(k, {"n": 0, "w": 0})
            d["n"] += 1
            if i["pnl_usd"] > 0:
                d["w"] += 1
        _hist_at = time.time()
    d = _hist_cache.get(_symbol_key(sym)) or {"n": 0, "w": 0}
    return {"n": d["n"], "win_rate": round(d["w"] / d["n"], 4) if d["n"] else None}


def _overall_win() -> tuple[float, int]:
    if time.time() - _hist_at > 600:
        _hist_win("")
    n = sum(d["n"] for d in _hist_cache.values())
    w = sum(d["w"] for d in _hist_cache.values())
    return ((w / n) if n else 0.5, n)


def _levels(row: dict, fx: dict) -> dict:
    sym = row["symbol"]
    mk = row["market"]
    price = row["price"]
    qty = float(row["qty"] or 0)
    side = row["side"]
    bars = _bars(sym, mk, "1y")
    base = {"id": row["id"], "symbol": sym, "name": row.get("name") or "",
            "account": row.get("account"), "side": side, "qty": qty,
            "cost": row.get("avg_cost"), "price": price,
            "pnl_usd": row.get("pnl_usd"), "weight": row.get("weight"),
            "ccy": row.get("ccy"), "bars": len(bars), "exp_usd": None}
    if not bars or price in (None, 0) or len(bars) < 25:
        base.update({"note": "日线不足或没有现价，无法计算结构位"})
        return base
    atr = _atr(bars)
    lows = [b["l"] for b in bars[-20:]]
    highs = [b["h"] for b in bars[-20:]]
    low20, high20 = min(lows), max(highs)
    p = float(price)
    if side == "SHORT":
        stop = max(p + 2 * atr, high20 + 0.3 * atr)
        stop = min(stop, p + 3 * atr)          # 结构位再远也不超过 3×ATR
        target = min(p - 3 * atr, low20 - 0.1 * atr)
        stop = max(stop, p * 1.001)
        target = max(min(target, p * 0.999), p - 5 * atr)
        adverse, favour = stop - p, p - target
    else:
        stop = min(p - 2 * atr, low20 - 0.3 * atr)
        stop = max(stop, p - 3 * atr)          # 20 日低点太远时收回到 3×ATR
        target = max(p + 3 * atr, high20 + 0.1 * atr)
        stop = min(stop, p * 0.999)
        target = min(target, p + 5 * atr)
        target = max(target, p * 1.001)
        adverse, favour = p - stop, target - p
    rr = (favour / adverse) if adverse > 0 else None
    prob = (adverse / (adverse + favour)) if (adverse + favour) > 0 else None
    hist = _hist_win(sym)
    overall, overall_n = _overall_win()
    if hist["n"] >= 5:
        win, win_src = hist["win_rate"], f"该标的 {hist['n']} 笔"
    else:
        win, win_src = overall, f"整体 {overall_n} 笔（该标的仅 {hist['n']} 笔）"
    exp_ps = (win * favour - (1 - win) * adverse) if prob is not None else None
    exp_usd = exp_ps * qty * float(fx.get(row["ccy"], 1.0)) if exp_ps is not None else None
    note = ""
    if exp_usd is not None and exp_usd < 0:
        note = f"按历史胜率 {win:.0%} 算期望为负"
    elif rr is not None and rr < 1.0:
        note = "盈亏比 <1，风险大于回报，不值得做"
    elif prob is not None and prob < 0.35:
        note = "首达概率偏低，止盈位偏远"
    elif rr is not None and rr <= 1.05:
        note = "盈亏比 ≈1，回报不足以覆盖风险"
    base.update({
        "atr": round(atr, 4), "atr_pct": round(atr / p, 4) if p else None,
        "stop": round(stop, 4), "target": round(target, 4),
        "stop_dist": round(abs(p - stop) / p, 4), "target_dist": round(favour / p, 4),
        "rr": round(rr, 2) if rr is not None else None,
        "prob": round(prob, 4) if prob is not None else None,
        "win_used": round(win, 4), "win_src": win_src,
        "exp_ps": round(exp_ps, 4) if exp_ps is not None else None,
        "exp_usd": round(exp_usd, 2) if exp_usd is not None else None,
        "hist_win": hist["win_rate"] if hist["n"] >= 5 else None, "hist_n": hist["n"],
        "low20": round(low20, 4), "high20": round(high20, 4),
        "note": note, "basis": "止损取 2×ATR 与 20 日低点更外侧、上限 3×ATR；止盈 3×ATR 与 20 日高，上限 5×ATR",
    })
    return base


def _board(fx: dict) -> dict:
    rows_all = portfolio.position_rows(with_quotes=True)
    markets = []
    notes = []
    for code, label, items in BOARD:
        rows = []
        for sym, name in items:
            bars = _bars(sym, code, "1y")
            if len(bars) < 30:
                rows.append({"symbol": sym, "name": name, "error": "无日线"})
                continue
            p = bars[-1]["c"]
            prev = bars[-2]["c"]
            ma20, ma50, ma200 = _ma(bars, 20), _ma(bars, 50), _ma(bars, 200)
            hi = max(b["h"] for b in bars)
            atr = _atr(bars)
            row = {
                "symbol": sym, "name": name, "price": round(p, 2),
                "chg": round(p - prev, 2), "chg_pct": round((p / prev - 1) * 100, 2) if prev else None,
                "ma20": round(ma20, 2) if ma20 else None,
                "ma50": round(ma50, 2) if ma50 else None,
                "ma200": round(ma200, 2) if ma200 else None,
                "vs200": round((p / ma200 - 1) * 100, 2) if ma200 else None,
                "off_high": round((p / hi - 1) * 100, 2) if hi else None,
                "atr_pct": round(atr / p * 100, 2) if p else None,
                "as_of": bars[-1]["t"],
            }
            rows.append(row)
            if code in ("US", "HK", "ID") and sym != "^VIX" and ma200:
                if p < ma200:
                    notes.append(f"{name} 在 200 日均线下方 {abs(p / ma200 - 1) * 100:.1f}%，中期趋势偏弱")
                if hi and p / hi - 1 <= -0.15:
                    notes.append(f"{name} 距 52 周高 {abs((p / hi - 1) * 100):.0f}%，处于深度回撤")
        exp_rows = [r for r in rows_all if r["market"] == code]
        exposure = sum(abs(r["value_usd"] or 0) for r in exp_rows)
        pnl = sum(r["pnl_usd"] or 0 for r in exp_rows)
        eq = float(portfolio.overview("USD", "all").get("total_equity_usd") or 0)
        if code != "CN" and eq and exposure > eq:
            notes.append(f"{label}敞口 {exposure:,.0f} USD ≈ 权益 {exposure / eq:.1f} 倍，单一市场集中度偏高")
        markets.append({"code": code, "label": label, "indices": rows,
                        "exposure_usd": round(exposure, 2), "pnl_usd": round(pnl, 2),
                        "positions": len(exp_rows)})
    vix = next((r for m in markets if m["code"] == "US"
                for r in m["indices"] if r["symbol"] == "^VIX"), None)
    if vix and vix.get("price") is not None:
        v = vix["price"]
        state = "低波动" if v < 16 else ("中性" if v < 22 else ("偏高" if v < 30 else "恐慌"))
        notes.insert(0, f"VIX {v:.2f}（{state}，日变动 {vix.get('chg_pct')}%）")
    return {"markets": markets, "notes": notes[:8], "fx": fx,
            "long_short": {"long": round(sum(r["value_usd"] or 0 for r in rows_all if r["side"] == "LONG"), 2),
                           "short": round(sum(abs(r["value_usd"] or 0) for r in rows_all if r["side"] == "SHORT"), 2)}}


def _hedge(rows: list, fx: dict) -> dict:
    specs = [(b[0], m) for m, b in BENCH.items()] + [(h[0], m) for m, h in HEDGE.items()] \
            + [(r["symbol"], r["market"]) for r in rows]
    _warm(specs, "1y")
    equity0 = float(portfolio.overview("USD", "all").get("total_equity_usd") or 0)
    by_mkt: dict[str, dict] = {}
    detail = []
    for r in rows:
        mk = r["market"]
        bench_sym = BENCH.get(mk)
        if not bench_sym:
            continue
        sign = 1 if r["side"] == "LONG" else -1
        if r["symbol"] in LEVER:
            beta, beta_src = LEVER[r["symbol"]], "产品倍数"
        else:
            beta = _beta(_bars(r["symbol"], mk, "1y"), _bars(bench_sym[0], mk, "1y"))
            beta_src = "回归"
        if beta is None:
            continue
        expo = abs(r["value_usd"] or 0) * beta * sign
        d = by_mkt.setdefault(mk, {"bench": bench_sym[1], "beta_exposure": 0.0, "parts": []})
        d["beta_exposure"] += expo
        detail.append({"symbol": r["symbol"], "side": r["side"], "beta": round(beta, 2),
                       "beta_src": beta_src,
                       "value_usd": r["value_usd"], "beta_exposure": round(expo, 2)})

    plans = []
    for mk, d in by_mkt.items():
        d["beta_exposure"] = round(d["beta_exposure"], 2)
        if mk not in HEDGE:
            plans.append({"market": mk, "bench": d["bench"], "net": d["beta_exposure"],
                          "note": "该市场没有合适的 ETF 对冲标的，只能减仓"})
            continue
        hsym, hname = HEDGE[mk]
        bars = _bars(hsym, mk, "1y")
        if not bars:
            plans.append({"market": mk, "bench": d["bench"], "net": d["beta_exposure"],
                          "note": "对冲标的暂无行情"})
            continue
        hp = bars[-1]["c"]
        need = -d["beta_exposure"]
        shares = need / hp if hp else 0
        plan = {"market": mk, "bench": d["bench"], "net": d["beta_exposure"],
                "hedge": hname, "hedge_price": round(hp, 2),
                "hedge_notional": round(need, 2),
                "hedge_shares": int(round(shares)),
                "side": "卖空" if shares < 0 else "买入"}
        if equity0 and abs(need) > equity0:
            plan["feasible"] = False
            plan["note"] = (f"需对冲名义 {abs(need):,.0f} USD ≈ 权益 {abs(need) / equity0:.1f} 倍，"
                            "实际无法用 ETF 对冲；唯一可行是减仓")
        else:
            plan["feasible"] = True
        plans.append(plan)
        d["hedge_notional"] = need
        d["hedge_price"] = hp

    equity = equity0
    net = sum(d["beta_exposure"] for d in by_mkt.values())
    scen = []
    for s in (-0.10, -0.20, 0.10):
        un = sum((d["beta_exposure"] * s) for d in by_mkt.values())
        hedged = un + sum((d.get("hedge_notional", 0) * s) for d in by_mkt.values())
        scen.append({"shock": s, "unhedged": round(un, 2), "hedged": round(hedged, 2),
                     "diff": round(hedged - un, 2),
                     "unhedged_pct": round(un / equity, 4) if equity else None,
                     "hedged_pct": round(hedged / equity, 4) if equity else None})
    warn = ""
    if equity and abs(net) > 0.5 * equity:
        direction = "做空" if net < 0 else "做多"
        warn = (f"净 beta 敞口 {net:,.0f} USD ≈ 权益 {net / equity:.1f} 倍（{direction}方向），"
                f"市场反向 10% 就是 {abs(net) * 0.1:,.0f} USD 盈亏 ≈ 权益 {abs(net) * 0.1 / equity * 100:.0f}%。")
    return {"by_market": plans, "detail": detail, "scenarios": scen,
            "equity": equity,
            "net_beta_exposure": round(net, 2),
            "net_vs_equity": round(net / equity, 2) if equity else None,
            "warning": warn,
            "note": "beta：杠杆 ETF 用产品倍数，其余为近 120 个交易日对本地基准的回归值；"
                    "情景按基准指数同幅波动、个股随之线性变动估算，不含汇率与个股特有事件。"}


def _breakeven(rows: list, fx: dict, equity: float) -> dict:
    items = []
    for r in rows:
        p, c = r.get("price"), r.get("avg_cost")
        if p in (None, 0) or c in (None, 0):
            continue
        if r["side"] == "SHORT":
            need = (p - c) / p          # 空头：现价到成本价还要跌的幅度
            need_dir = "跌"
        else:
            need = (c - p) / p          # 多头：现价到成本价还要涨的幅度
            need_dir = "涨"
        items.append({"symbol": r["symbol"], "name": r.get("name") or "", "side": r["side"],
                      "pnl_usd": r.get("pnl_usd"), "price": round(p, 4),
                      "breakeven": round(c, 4), "need_pct": round(need, 4),
                      "need_dir": need_dir, "ccy": r.get("ccy")})
    losers = [i for i in items if (i["pnl_usd"] or 0) < 0]
    losers.sort(key=lambda i: i["pnl_usd"])
    gross = sum(abs(r["value_usd"] or 0) for r in rows)
    unreal = sum(r["pnl_usd"] or 0 for r in rows)
    return {"positions": items, "losers": losers,
            "portfolio_unrealized": round(unreal, 2),
            "portfolio_need_pct": round(-unreal / gross, 4) if gross and unreal < 0 else 0,
            "equity": equity,
            "note": "回本价按成本价（不含费用）算：多头要涨到成本价，空头要跌到成本价。"}


def overview() -> dict:
    rows = portfolio.position_rows(with_quotes=True)
    fx = market.fx_rates()
    specs = [(sym, code) for code, _, items in BOARD for sym, _ in items] \
            + [(r["symbol"], r["market"]) for r in rows] \
            + [(b[0], m) for m, b in BENCH.items()] + [(h[0], m) for m, h in HEDGE.items()]
    _warm(specs, "1y")
    board = _board(fx)
    levels = [_levels(r, fx) for r in rows]
    hedge = _hedge(rows, fx)
    equity = float(portfolio.overview("USD", "all").get("total_equity_usd") or 0)
    be = _breakeven(rows, fx, equity)
    avg_rr = [x["rr"] for x in levels if x.get("rr")]
    avg_p = [x["prob"] for x in levels if x.get("prob")]
    return {
        "levels": levels,
        "board": board,
        "hedge": hedge,
        "breakeven": be,
        "summary": {
            "positions": len(levels),
            "avg_rr": round(sum(avg_rr) / len(avg_rr), 2) if avg_rr else None,
            "avg_prob": round(sum(avg_p) / len(avg_p), 4) if avg_p else None,
            "expectancy_usd": round(sum(x.get("exp_usd") or 0 for x in levels), 2),
            "equity": equity,
        },
        "as_of": market.now_iso(),
    }
