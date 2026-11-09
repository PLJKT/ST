# -*- coding: utf-8 -*-
"""组合聚合：账户权益、持仓行（含双账户合并）、净值、模拟。"""
import json
from collections import defaultdict

from . import config, db, market


def _fx() -> dict:
    rates = market.fx_rates()
    return rates


def to_usd(amount, ccy, rates=None):
    if amount is None:
        return None
    rates = rates or _fx()
    return round(float(amount) * float(rates.get(ccy, 1.0)), 2)


def account_map() -> dict:
    rows = db.query("SELECT * FROM accounts ORDER BY CASE id WHEN 'futu_main' THEN 0 WHEN 'poems_sg' THEN 1 ELSE 2 END")
    cfg = {c["id"]: c for c in config.ACCOUNTS}
    out = {}
    for r in rows:
        r["markets"] = json.loads(r["markets"] or "[]")
        extra = cfg.get(r["id"], {})
        r["feed"] = extra.get("feed", "")
        r["feed_status"] = extra.get("feed_status", "")
        out[r["id"]] = r
    return out


def cash_of(account_id: str) -> list[dict]:
    return db.query("SELECT * FROM cash_balances WHERE account_id=?", [account_id])


def position_rows(account_id: str | None = None, with_quotes: bool = True,
                  include_legacy: bool = False) -> list[dict]:
    """返回持仓行。symbol 为空的台账行默认不进入实时持仓（不计权益），需要时显式打开。
    空头的市值与盈亏按负号计入：市值是负债，不是资产。"""
    accounts = account_map()
    # quality='reject' 的行由 hygiene 判定为不合理（负数量/碎股残值/缺成本…），不进任何合计
    sql = "SELECT * FROM positions WHERE COALESCE(quality,'ok')<>'reject'"
    params = []
    if account_id and account_id != "all":
        sql += " AND account_id=?"
        params.append(account_id)
    pos = db.query(sql, params)
    holdings = db.query("SELECT symbol, name, market, ccy FROM symbol_stats")
    name_by_symbol = {h["symbol"]: h for h in holdings}
    rates = _fx()
    rows = []
    for p in pos:
        acc = accounts.get(p["account_id"], {})
        sym = p["symbol"] or ""
        legacy = not sym
        if legacy and not include_legacy:
            continue
        ysym = market.resolve(sym, p["market"]) if sym else ""
        q = market.quote(sym, p["market"]) if (sym and with_quotes) else None
        live = bool(q and q.get("price") is not None)
        # 取价优先级：人工定价 > 实时报价 > 最近收盘价 > 台账价
        ov = market.override(ysym) if (sym and with_quotes) else None
        cl = market.last_close(ysym) if (sym and with_quotes) else None
        if ov and ov.get("price"):
            price = float(ov["price"])
            price_asof = ov.get("asof") or "人工定价"
            price_src = "manual"
        elif live:
            price = q.get("price")
            price_asof = q.get("as_of")
            price_src = "live"
        elif cl and cl.get("close"):
            price = float(cl["close"])
            price_asof = cl.get("trade_date") or "收盘"
            price_src = "close"
        else:
            price = p.get("st_price")
            price_asof = p.get("opened_at") or "台账期末"
            price_src = "st" if p.get("st_price") else "none"
        qty = p.get("qty") or 0
        sign = -1 if p["side"] == "SHORT" else 1
        value = round(qty * price * sign, 2) if price is not None else None
        cost = p.get("avg_cost")
        pnl = round((price - cost) * qty * sign, 2) if (price is not None and cost is not None) else None
        rows.append({
            "id": p["id"], "account": p["account_id"], "account_name": acc.get("name"),
            "symbol": sym, "yahoo": ysym,
            "name": p.get("name") or name_by_symbol.get(sym, {}).get("name"),
            "market": p["market"], "ccy": p["ccy"], "side": p["side"],
            "qty": qty, "avg_cost": cost, "price": price,
            "st_price": p.get("st_price"),
            "price_asof": price_asof, "price_source": price_src,
            "value_native": value,
            "value_usd": to_usd(value, p["ccy"], rates) if value is not None else None,
            "pnl_native": pnl,
            "pnl_usd": to_usd(pnl, p["ccy"], rates) if pnl is not None else None,
            "status": p.get("status"), "legacy": legacy, "source": p.get("source"),
            "note": p.get("note"),
        })
    # 双账户同票标记
    by_key = defaultdict(list)
    for r in rows:
        if r["symbol"]:
            by_key[(r["market"], r["symbol"])].append(r)
    for r in rows:
        key = (r["market"], r["symbol"])
        peers = by_key.get(key, []) if r["symbol"] else []
        r["accounts_holding"] = sorted({x["account"] for x in peers})
        r["merged"] = len(r["accounts_holding"]) > 1
    # 权重：分母用总敞口（绝对值），空头才不会把权重顶到 100% 以上
    total = sum(abs(r["value_usd"] or 0) for r in rows) or 0
    for r in rows:
        r["weight"] = round((r["value_usd"] or 0) / total, 4) if total else 0
    return rows


def account_summaries() -> list[dict]:
    accounts = account_map()
    rows = position_rows(with_quotes=True)
    legacy_rows = [r for r in position_rows(with_quotes=True, include_legacy=True) if r["legacy"]]
    rates = _fx()
    st = db.get_meta("st_numbers", {}) or {}
    kpi = st.get("kpi_poems") or {}
    by_acc = defaultdict(list)
    for r in rows:
        by_acc[r["account"]].append(r)
    legacy_by_acc = defaultdict(list)
    for r in legacy_rows:
        legacy_by_acc[r["account"]].append(r)

    out = []
    for aid, a in accounts.items():
        pos = by_acc.get(aid, [])
        pos_val = round(sum(r["value_usd"] or 0 for r in pos), 2)
        cash_rows = cash_of(aid)
        cash_usd = round(sum(to_usd(c["amount"], c["ccy"], rates) or 0 for c in cash_rows), 2)
        legacy_val = round(sum(r["value_usd"] or 0 for r in legacy_by_acc.get(aid, [])), 2)
        closed = a["status"] == "closed"
        # 已关闭账户：权益强制为 0，历史台账只留档、不计入任何合计（ST 的口径）
        equity = 0.0 if closed else round(pos_val + cash_usd, 2)
        floating = 0.0 if closed else round(sum(r["pnl_usd"] or 0 for r in pos), 2)

        realized_usd, realized_detail = None, None
        if aid == "futu_main":
            ev = db.query("SELECT ccy, COALESCE(SUM(pnl),0) AS s FROM trade_events "
                          "WHERE account_id=? AND COALESCE(quality,'ok')<>'reject' GROUP BY ccy", [aid])
            realized_usd = round(sum(to_usd(e["s"], e["ccy"], rates) or 0 for e in ev), 2)
            realized_by_ccy = [{"ccy": e["ccy"], "value": round(e["s"], 2)} for e in ev]
        else:
            src = " AND source='poems_ledger'" if aid == "poems_sg" else ""
            cl = db.query("SELECT ccy, COALESCE(SUM(pnl),0) AS s FROM closed_trades "
                          "WHERE account_id=?" + src +
                          " AND COALESCE(quality,'ok')<>'reject' GROUP BY ccy", [aid])
            realized_by_ccy = [{"ccy": c["ccy"], "value": round(c["s"], 2)} for c in cl]
            realized_detail = round(sum(to_usd(c["s"], c["ccy"], rates) or 0 for c in cl), 2)
            if aid == "poems_sg":
                realized_usd = round(kpi.get("realized") or 0, 2)
            else:
                # POEMS ID：只有 2026-03 起的真实单据，已实现直接取 FIFO 明细合计
                realized_usd = realized_detail

        out.append({
            "id": aid, "name": a["name"], "broker": a["broker"], "entity": a["entity"],
            "base_ccy": a["base_ccy"], "status": a["status"],
            "feed": a.get("feed") or "", "feed_status": a.get("feed_status") or "",
            "markets": a["markets"], "opened_at": a["opened_at"], "closed_at": a["closed_at"],
            "note": a["note"],
            "positions_usd": 0.0 if closed else pos_val,
            "cash_usd": 0.0 if closed else cash_usd,
            "equity_usd": equity,
            "legacy_positions_usd": legacy_val,
            "legacy_positions_count": len(legacy_by_acc.get(aid, [])),
            "realized_usd": realized_usd, "realized_detail_usd": realized_detail,
            "realized_by_ccy": realized_by_ccy,
            "floating_usd": floating,
            "positions_count": len(pos),
            "data_gap": (not closed) and not pos,
        })
    return out


def overview(base: str = "USD", account: str | None = None) -> dict:
    """account=None/'all' 为合并口径；指定账户时权益、市场分布、台账都只算这个账户。
    base 只影响展示层换算（返回 base_rate，由前端乘到数字上），USD 仍是记账本位。"""
    rates = _fx()
    scope = account if account and account != "all" else "all"
    accs_all = account_summaries()
    accs = accs_all if scope == "all" else [a for a in accs_all if a["id"] == scope]
    rows = position_rows(account_id=None if scope == "all" else scope, with_quotes=True)
    legacy_all = [r for r in position_rows(with_quotes=True, include_legacy=True) if r["legacy"]]
    legacy = legacy_all if scope == "all" else [r for r in legacy_all if r["account"] == scope]
    total_equity = round(sum(a["equity_usd"] for a in accs), 2)
    total_pos = round(sum(a["positions_usd"] for a in accs), 2)
    total_cash = round(sum(a["cash_usd"] for a in accs), 2)
    total_float = round(sum(a["floating_usd"] for a in accs), 2)
    # 净盈亏 = 已实现 + 浮动（两个口径都在台账里，这里只做加法，不改口径）
    realized = round(sum((a.get("realized_usd") or 0) for a in accs), 2)
    st = db.get_meta("st_numbers", {}) or {}
    # 按市场聚合（只含可计入权益的当前持仓）
    by_market = defaultdict(lambda: {"value_usd": 0.0, "pnl_usd": 0.0, "n": 0})
    for r in rows:
        by_market[r["market"]]["value_usd"] += r["value_usd"] or 0
        by_market[r["market"]]["pnl_usd"] += r["pnl_usd"] or 0
        by_market[r["market"]]["n"] += 1
    markets = [{"code": m["code"], "name": m["name"], "tradable": m["tradable"],
                "value_usd": round(by_market[m["code"]]["value_usd"], 2),
                "pnl_usd": round(by_market[m["code"]]["pnl_usd"], 2),
                "n": by_market[m["code"]]["n"]} for m in config.MARKETS]
    st_equity = st.get("equity_usd")
    # 只在合并口径下比较台账定稿（台账本身是三账户合起来的）
    delta = round(total_equity - st_equity, 2) if (st_equity and scope == "all") else None
    return {
        "scope": scope, "base": base,
        "base_rate": round(1.0 / float(rates.get(base) or 1.0), 8),
        "total_equity_usd": total_equity,
        "total_positions_usd": total_pos, "total_cash_usd": total_cash,
        "total_floating_usd": total_float,
        "total_realized_usd": realized,
        "total_net_usd": round(realized + total_float, 2),
        "leverage": round(abs(total_pos) / total_equity, 2) if total_equity else None,
        "legacy": {
            "count": len(legacy),
            "value_usd": round(sum(r["value_usd"] or 0 for r in legacy), 2),
            "note": "历史台账行（无代码）只留档，不计入任何权益合计",
        },
        "accounts": accs, "markets": markets,
        "fx": {k: v for k, v in rates.items()}, "fx_asof": market.now_iso(),
        "fx_note": "合并总资产按实时汇率折算，仅用于展示层；分币种数值不受影响。",
        "st_reference": {
            "equity_usd": st_equity,
            "pnl_tradeview_usd": st.get("pnl_tradeview_usd"),
            "end_cash_usd": st.get("end_cash_usd"),
            "float_usd": (st.get("components_usd") or {}).get("unrealized"),
            "as_of": db.get_meta("generated_at"),
            "delta_usd": delta,
            "note": "台账为 2026-09-24 定稿；差额来自此后行情变动，不是数据错误",
        },
        "as_of": market.now_iso(),
    }


def nav_series(account_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM nav_points"
    params = []
    if account_id and account_id != "all":
        sql += " WHERE account_id=?"
        params.append(account_id)
    return db.query(sql + " ORDER BY as_of", params)


def symbol_table(limit: int = 100) -> list[dict]:
    rows = db.query("SELECT * FROM symbol_stats ORDER BY realized", [])
    accounts = account_map()
    rates = _fx()
    out = []
    for r in rows:
        open_qty = (r["open_long"] or 0) - (r["open_short"] or 0)
        out.append({
            **r,
            "open_qty": open_qty,
            "realized_usd": to_usd(r["realized"], r["ccy"], rates),
            "account_name": accounts.get(r["account_id"], {}).get("name"),
        })
    out.sort(key=lambda x: (x["realized_usd"] or 0))
    return out[:limit]


def simulate(account_id: str, symbol: str, qty: float) -> dict:
    acc = account_map().get(account_id)
    if not acc:
        return {"error": "account_not_found"}
    q = market.quote(symbol)
    mk, ccy = market.market_of_symbol(q["symbol"])
    markets_cfg = {m["code"]: m for m in config.MARKETS}
    if mk not in acc["markets"]:
        return {"error": "account_cannot_trade_this_market",
                "message": f"{acc['name']} 不能交易 {markets_cfg.get(mk, {}).get('name', mk)}"}
    if not markets_cfg.get(mk, {}).get("tradable", 1):
        return {"error": "market_is_reference_only",
                "message": f"{markets_cfg.get(mk, {}).get('name', mk)} 仅为参考市场，不允许模拟买入"}
    price = q.get("price")
    if price is None:
        return {"error": "no_price", "message": "取不到现价，暂不能模拟"}
    rates = _fx()
    accs = account_summaries()
    total = sum(a["equity_usd"] for a in accs) or 0
    add_usd = to_usd(price * qty, ccy, rates)
    rows = position_rows(with_quotes=False)
    # 用总敞口（绝对值）计算"该市场持仓将变为"，否则空头会抵掉新增
    market_after = sum(abs(r["value_usd"] or 0) for r in rows if r["market"] == mk) + add_usd
    return {
        "account": account_id, "symbol": q["symbol"], "qty": qty, "price": price, "ccy": ccy,
        "market": mk, "add_usd": add_usd,
        "weight_before": round(add_usd / total, 4) if total else None,
        "weight_after_hint": f"新增市值 ${add_usd:,.2f}，占合并组合 {add_usd / total:.2%}" if total else "",
        "market_value_after_usd": round(market_after, 2),
        "cash_note": "现金余额需按结算日与结算币重算（POEMS SG 可选 USD/SGD）。",
        "fee_note": "佣金/换汇点差按设置里的费率表计算，本模拟未计费用。",
    }
