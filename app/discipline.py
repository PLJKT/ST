# -*- coding: utf-8 -*-
"""交易纪律检查：2% 单笔风险、单票集中度、总敞口、现金缓冲、空头、杠杆 ETF、期望值与赔率。

所有阈值都是公开交易纪律里的常用口径，检查对象是"当前库里的真实数字"，不是建议值：
- 2% 法则：单笔最大亏损 ≤ 权益 2%（按 10% 止损反推名义仓位上限）
- 单票集中度：≤30%，≥50% 视为硬失效
- 总敞口（绝对值合计）/ 权益：≤2.0x
- 现金不得为负（负现金＝在用保证金）
- 空头敞口 ≤10%
- 杠杆 ETF 隔夜占比 ≤10%（3x 产品只适合日内）
- 历史赔率 ≥1.0、期望值 >0
"""
from . import market, portfolio
from .edit import _account, _resolve_market

RULE = {
    "risk_pct": 0.02,      # 2% 法则
    "stop_pct": 0.10,      # 反推用的假设止损幅度
    "max_weight": 0.30,    # 单票集中度
    "hard_weight": 0.50,
    "max_gross": 2.0,      # 总敞口倍数
    "max_short": 0.10,     # 空头占比
    "max_levered": 0.10,   # 杠杆 ETF 占比
    "min_payoff": 1.0,
}

LEVERED = {
    "SQQQ", "SOXL", "SOXS", "TQQQ", "TSLL", "LABU", "TNA", "FAS", "TZA",
    "YINN", "YANG", "SPXL", "SPXU", "UVXY", "SVXY", "NUGT", "DUST",
}


def _key(sym: str) -> str:
    """01519 / 01519.HK 视为同一只票。"""
    s = (sym or "").upper()
    for suf in (".HK", ".JK", ".SS", ".SZ"):
        if s.endswith(suf):
            return s[: -len(suf)]
    return s


def _history() -> dict:
    from . import insights
    items = insights._dated_items()
    wins = [i["pnl_usd"] for i in items if i["pnl_usd"] > 0]
    losses = [i["pnl_usd"] for i in items if i["pnl_usd"] < 0]
    n = len(items)
    wr = len(wins) / n if n else 0
    aw = sum(wins) / len(wins) if wins else 0
    al = abs(sum(losses) / len(losses)) if losses else 0
    payoff = (aw / al) if al else None
    exp = (wr * aw - (1 - wr) * al) if n else 0
    return {"n": n, "win_rate": wr, "avg_win": round(aw, 2), "avg_loss": round(al, 2),
            "payoff": round(payoff, 3) if payoff is not None else None,
            "expectancy": round(exp, 2)}


def _level(value, limit, hard=None) -> str:
    if value is None:
        return "info"
    if hard is not None and value >= hard:
        return "danger"
    if value >= limit:
        return "warn"
    return "pass"


def review() -> dict:
    """组合层面的纪律体检。"""
    ov = portfolio.overview("USD", "all")
    rows = portfolio.position_rows(with_quotes=True)
    equity = float(ov.get("total_equity_usd") or 0)
    gross = sum(abs(r["value_usd"] or 0) for r in rows)
    top = max(rows, key=lambda r: abs(r["value_usd"] or 0), default=None)
    top_w = (abs(top["value_usd"] or 0) / gross) if top and gross else 0
    short_abs = sum(abs(r["value_usd"] or 0) for r in rows if r["side"] == "SHORT")
    lev_abs = sum(abs(r["value_usd"] or 0) for r in rows if r["symbol"] in LEVERED)
    worst_risk = max((abs(r["value_usd"] or 0) for r in rows), default=0) * RULE["stop_pct"]
    hist = _history()

    rules = []
    rules.append({
        "key": "risk_2pct",
        "label": "2% 法则（单笔最大亏损）",
        "limit": "≤2% 权益",
        "actual": f"{(worst_risk / equity * 100) if equity else 0:.1f}%"
                  f"（{worst_risk:,.0f} / 权益 {equity:,.0f}，按 10% 止损反推）",
        "status": _level((worst_risk / equity) if equity else 0, RULE["risk_pct"], RULE["risk_pct"] * 3),
        "detail": "单笔亏损上限 2%，超过就是把几个仓位捆成一个赌注。",
    })
    rules.append({
        "key": "concentration",
        "label": "单票集中度",
        "limit": "≤30%（50% 硬失效）",
        "actual": f"{top['symbol'] if top else '—'} {top_w * 100:.1f}%",
        "status": _level(top_w, RULE["max_weight"], RULE["hard_weight"]),
        "detail": "单一标的超过 30% 后，组合波动由这一只票决定。",
    })
    ratio = (gross / equity) if equity else 0
    rules.append({
        "key": "gross_leverage",
        "label": "总敞口倍数",
        "limit": "≤2.0x 权益",
        "actual": f"{ratio:.2f}x（敞口 {gross:,.0f} / 权益 {equity:,.0f}）",
        "status": _level(ratio, RULE["max_gross"], RULE["max_gross"] * 1.25),
        "detail": "全力加杠杆时，一次 15% 的回撤就会触发追缴。",
    })
    cash = float(ov.get("total_cash_usd") or 0)
    rules.append({
        "key": "cash",
        "label": "现金缓冲",
        "limit": "≥0（不为负）",
        "actual": f"{cash:,.2f}",
        "status": "danger" if cash < -0.25 * equity else ("warn" if cash < 0 else "pass"),
        "detail": "现金为负说明已在用保证金，下跌时没有缓冲。",
    })
    short_share = (short_abs / gross) if gross else 0
    rules.append({
        "key": "short",
        "label": "空头敞口",
        "limit": "≤10% 敞口",
        "actual": f"{short_share * 100:.1f}%（{short_abs:,.0f}）",
        "status": _level(short_share, RULE["max_short"], RULE["max_short"] * 2),
        "detail": "空头理论亏损无上限，且保证金要求随股价上涨同步提高。",
    })
    lev_share = (lev_abs / gross) if gross else 0
    rules.append({
        "key": "levered",
        "label": "杠杆 ETF 隔夜占比",
        "limit": "≤10% 敞口（建议日内）",
        "actual": f"{lev_share * 100:.1f}%（{lev_abs:,.0f}）",
        "status": _level(lev_share, RULE["max_levered"], RULE["max_levered"] * 3),
        "detail": "3x 产品每日重置，隔夜与波段持有会被波动衰减吃掉。",
    })
    payoff = hist.get("payoff")
    rules.append({
        "key": "payoff",
        "label": "历史赔率（均盈/均亏）",
        "limit": "≥1.0（2:1 更稳）",
        "actual": f"{payoff if payoff is not None else '—'}（均盈 {hist['avg_win']:,.0f} / 均亏 {hist['avg_loss']:,.0f}）",
        "status": "pass" if (payoff or 0) >= 1 else ("warn" if (payoff or 0) >= 0.5 else "danger"),
        "detail": f"{hist['n']} 笔有日期交易：胜率 {hist['win_rate'] * 100:.0f}%。"
                  "赔率低于 1 时，胜率再高也会被亏损单吃掉。",
    })
    exp = hist.get("expectancy") or 0
    rules.append({
        "key": "expectancy",
        "label": "历史期望值（每笔）",
        "limit": ">0",
        "actual": f"{exp:,.2f} USD/笔",
        "status": "pass" if exp > 0 else "danger",
        "detail": "E = 胜率×均盈 − 败率×均亏；为负时做得越多亏得越多。",
    })
    summary = {"pass": sum(1 for r in rules if r["status"] == "pass"),
               "warn": sum(1 for r in rules if r["status"] == "warn"),
               "danger": sum(1 for r in rules if r["status"] == "danger")}
    return {"rules": rules, "summary": summary, "history": hist,
            "equity": equity, "gross": round(gross, 2), "as_of": market.now_iso()}


def check(account: str, symbol: str, side: str = "LONG", qty: float = 0,
          avg_cost: float = 0, price: float | None = None,
          exclude_id: str | None = None) -> dict:
    """新增/修改前的预检：把这一笔放进组合后，纪律还守不守得住。"""
    acc = _account(account or "")
    sym, mk, ccy = _resolve_market(symbol, None, acc)
    side = (side or "LONG").upper()
    qty = float(qty or 0)
    if qty <= 0:
        raise ValueError("数量必须大于 0")
    if price in (None, ""):
        try:
            price = market.quote(sym, mk).get("price")
        except Exception:
            price = None
    if price in (None, 0):
        price = float(avg_cost or 0)
    price = float(price)
    rates = portfolio._fx()
    notional = abs(portfolio.to_usd(qty * price, ccy, rates) or 0)

    ov = portfolio.overview("USD", "all")
    rows = portfolio.position_rows(with_quotes=True)
    equity0 = float(ov.get("total_equity_usd") or 0)
    gross0 = sum(abs(r["value_usd"] or 0) for r in rows)
    old = 0.0
    if exclude_id:
        old = sum(abs(r["value_usd"] or 0) for r in rows if r["id"] == exclude_id)
    gross1 = gross0 - old + notional
    equity1 = equity0 - old + notional

    checks = []
    # 风险与杠杆都用「当前资本」做分母：新增持仓不扣现金，权益不该被这笔名义额稀释。
    risk = notional * RULE["stop_pct"]
    pct = (risk / equity0) if equity0 else 0
    checks.append({
        "level": "danger" if pct > RULE["risk_pct"] * 3 else ("warn" if pct > RULE["risk_pct"] else "pass"),
        "text": f"单笔风险（10% 止损）{risk:,.0f} USD ≈ 权益 {pct * 100:.1f}%，上限 2%（{equity0 * RULE['risk_pct']:,.0f} USD）",
        "key": "risk_2pct"})

    same = sum(abs(r["value_usd"] or 0) for r in rows
               if _key(r["symbol"]) == _key(sym) and r["id"] != exclude_id)
    w = ((same + notional) / gross1) if gross1 else 0
    if exclude_id:
        ctext = f"{sym} 改后占敞口 {w * 100:.1f}%（上限 30%，50% 硬失效）"
    else:
        ctext = (f"{sym} 合计占敞口 {w * 100:.1f}%"
                 f"（含已有 {same:,.0f} + 这笔 {notional:,.0f}；上限 30%，50% 硬失效）")
    checks.append({
        "level": "danger" if w >= RULE["hard_weight"] else ("warn" if w >= RULE["max_weight"] else "pass"),
        "text": ctext,
        "key": "concentration"})

    gr = (gross1 / equity0) if equity0 else 0
    checks.append({
        "level": "danger" if gr > RULE["max_gross"] * 1.25 else ("warn" if gr > RULE["max_gross"] else "pass"),
        "text": f"加完后总敞口 {gr:.2f}x 权益（上限 2.0x）",
        "key": "gross"})

    cash = float(ov.get("total_cash_usd") or 0)
    if cash < 0:
        checks.append({
            "level": "warn",
            "text": f"现金仍为 {cash:,.0f} USD（负现金＝占用保证金，建仓不扣现金）",
            "key": "cash"})

    if side == "SHORT":
        short_abs = sum(abs(r["value_usd"] or 0) for r in rows
                        if r["side"] == "SHORT" and r["id"] != exclude_id)
        share = ((short_abs + notional) / gross1) if gross1 else 0
        checks.append({
            "level": "danger" if share >= RULE["max_short"] * 2 else ("warn" if share >= RULE["max_short"] else "pass"),
            "text": f"空头敞口将达 {share * 100:.1f}%（上限 10%）；空头无上限亏损，必须写回补价",
            "key": "short"})

    if sym in LEVERED:
        lev_abs = sum(abs(r["value_usd"] or 0) for r in rows
                      if r["symbol"] in LEVERED and r["id"] != exclude_id)
        share = ((lev_abs + notional) / gross1) if gross1 else 0
        checks.append({
            "level": "warn" if share < RULE["max_levered"] * 3 else "danger",
            "text": f"杠杆 ETF 敞口将达 {share * 100:.1f}%（3x 产品建议只做日内）",
            "key": "levered"})

    hist = _history()
    if hist["payoff"] is not None:
        ok = hist["payoff"] >= 1
        checks.append({
            "level": "pass" if ok else "warn",
            "text": f"历史赔率 {hist['payoff']}（均盈 {hist['avg_win']:,.0f} / 均亏 {hist['avg_loss']:,.0f}）；"
                    f"期望值 {hist['expectancy']:,.2f} USD/笔",
            "key": "history"})

    return {"symbol": sym, "market": mk, "ccy": ccy, "price": round(price, 4),
            "notional_usd": round(notional, 2), "equity_after": round(equity1, 2),
            "gross_after": round(gross1, 2),
            "leverage_after": round((gross1 / equity0) if equity0 else 0, 2),
            "checks": checks,
            "requires_confirm": any(c["level"] == "danger" for c in checks),
            "as_of": market.now_iso()}
