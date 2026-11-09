# -*- coding: utf-8 -*-
"""POEMS 印尼账户（PT Phillip Sekuritas Indonesia）单据台账。

数据来源：2025-11 → 2026-11 的券商对账单（SOA）、成交确认（TC）、基金对账单（MF）。
口径约定：
- 成交以「成交确认」为准（56 个单侧成交，全部与对账单逐月持仓链对平）；
- 现金以「对账单期末现金」为准；10–11 月没有对账单，用 9-30 现金叠加成交净额；
- 基金只持 Phillip Money Market Fund，单位/净值取最近一期基金对账单；
- POEMS SG 在本档案夹里没有任何单据，SG 侧仍是 2022-01 的台账，两者不合并。
"""
import json
from pathlib import Path

from . import config, db

LEDGER = config.PROCESSED_DIR / "poems.json"
ACCOUNT = "poems_id"
MMF_SYMBOL = "PHILLIPMMF.JK"


def payload() -> dict:
    if not Path(LEDGER).exists():
        return {}
    return json.loads(Path(LEDGER).read_text(encoding="utf-8"))


def _last_close(code: str) -> float | None:
    """优先用 9-30 对账单收盘价，没有则用最后一笔成交价。"""
    led = payload()
    if not led:
        return None
    for m in reversed(led.get("monthly") or []):
        for h in m.get("holdings") or []:
            if h["code"] == code:
                return h.get("close")
    for t in reversed(led.get("trades") or []):
        if t["code"] == code:
            return t.get("price")
    return None


def import_all() -> dict:
    """把 POEMS ID 单据写入 st.db（可重复执行）。"""
    led = payload()
    if not led:
        return {"error": "no_ledger", "message": "缺 data/processed/poems.json"}

    stats = {"trades": 0, "closed": 0, "positions": 0}
    with db.db() as conn:
        conn.execute("DELETE FROM poems_trades WHERE account_id=?", [ACCOUNT])
        conn.execute("DELETE FROM poems_cashflows WHERE account_id=?", [ACCOUNT])
        conn.execute("DELETE FROM poems_mf WHERE account_id=?", [ACCOUNT])
        conn.execute("DELETE FROM closed_trades WHERE account_id=? AND source='poems_id_docs'", [ACCOUNT])
        conn.execute("DELETE FROM positions WHERE account_id=? AND source='poems_id_docs'", [ACCOUNT])

        for i, t in enumerate(led["trades"]):
            conn.execute(
                "INSERT OR REPLACE INTO poems_trades(id, account_id, trade_date, settle_date, ref, side, "
                "symbol, name, qty, price, gross, fee, net, ccy, source_file) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [f"pid_{t['date']}_{t['ref']}_{t['side']}", ACCOUNT, t["date"], t.get("settle"), t["ref"],
                 t["side"], t["code"], t["name"], t["qty"], t["price"], t["gross"], t["fee"], t["net"],
                 "IDR", t.get("file")])
            stats["trades"] += 1

        for m in led.get("monthly") or []:
            cid = f"pid_soa_{m['period_end']}"
            for kind, val in (("cash_end", m.get("cash")), ("deposit", m.get("deposit")),
                              ("withdraw", -(m.get("withdraw") or 0)), ("dividend", m.get("dividend")),
                              ("interest", -(m.get("interest") or 0))):
                if not val:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO poems_cashflows(id, account_id, period_end, kind, amount, ccy) "
                    "VALUES(?,?,?,?,?,?)", [f"{cid}_{kind}", ACCOUNT, m["period_end"], kind, val, "IDR"])

        for c in led.get("closed_lots") or []:
            if c.get("pnl") is None:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO closed_trades(id, account_id, symbol, name, market, ccy, qty, "
                "buy_price, sell_price, avg_price, fee, pnl, pl_pct, open_date, close_date, holding_days, "
                "grade, source, quality, quality_note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [f"pid_{c['symbol']}_{c['close_date']}_{c['open_date']}_{round(c['qty'],2)}", ACCOUNT,
                 c["symbol"], c["name"], "ID", "IDR", c["qty"], c["buy_price"], c["sell_price"],
                 c["buy_price"], c.get("fee") or 0, c["pnl"], c.get("pl_pct"), c["open_date"],
                 c["close_date"], c.get("holding_days"), None, "poems_id_docs", "ok",
                 "POEMS ID 成交确认 FIFO"])
            stats["closed"] += 1

        for p in led.get("positions") or []:
            code = p["symbol"]
            price = _last_close(code)
            conn.execute(
                "INSERT OR REPLACE INTO positions(id, account_id, symbol, name, market, ccy, side, qty, "
                "avg_cost, st_price, opened_at, status, source, note, quality, quality_note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [f"pid_{code}", ACCOUNT, code, p["name"], "ID", "IDR", "LONG", p["qty"], p["avg_cost"],
                 price, p.get("first_open"), "open", "poems_id_docs",
                 "POEMS ID 成交确认 FIFO 重建", "ok", "逐月持仓链对平"])
            stats["positions"] += 1

        mf = led.get("mf") or {}
        if mf.get("units"):
            conn.execute(
                "INSERT OR REPLACE INTO positions(id, account_id, symbol, name, market, ccy, side, qty, "
                "avg_cost, st_price, opened_at, status, source, note, quality, quality_note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [f"pid_MMF", ACCOUNT, MMF_SYMBOL, "Phillip Money Market Fund", "ID", "IDR", "LONG",
                 mf["units"], mf.get("nav") or 0, mf.get("nav") or 0, f"{mf.get('period_end')}-01",
                 "open", "poems_id_docs",
                 f"货币基金 {mf['units']:,.2f} 份 · NAV {mf.get('nav'):,.2f} · {mf.get('period_end')}",
                 "ok", "基金对账单"])
            stats["positions"] += 1

        # 旧的「无代码」台账行：2026 单据完全没有复现，标为 reject 留档
        conn.execute(
            "UPDATE positions SET quality='reject', quality_note=? WHERE account_id=? AND "
            "COALESCE(source,'')<>'poems_id_docs'", ["旧台账无证券代码，2026 年单据未复现，已由单据重建替代", ACCOUNT])

        # 账户备注：别再沿用「仅 2020 年台账」的旧描述
        conn.execute(
            "UPDATE accounts SET note=? WHERE id=?",
            ["只做印尼股；2026-03 → 2026-11 由 62 份单据重建（对账单 / 成交确认 / 基金对账单），"
             "2020 年旧台账无证券代码、未复现，已标 reject 留档。", ACCOUNT])

        cash = led.get("cash") or {}
        if cash.get("amount") is not None:
            conn.execute(
                "INSERT OR REPLACE INTO cash_balances(account_id, ccy, amount, as_of, source) VALUES(?,?,?,?,?)",
                [ACCOUNT, "IDR", cash["amount"], "2026-11-06", "poems:id 对账单 9-30 期末 + 10–11 月成交净额"])

        if mf.get("nav"):
            conn.execute(
                "INSERT OR REPLACE INTO price_overrides(symbol, price, note, asof, updated_at) VALUES(?,?,?,?,?)",
                [MMF_SYMBOL, mf["nav"], "POEMS 货币基金 NAV（基金对账单）", mf.get("period_end"), "2026-11-09"])

        cash_meta = cash
        mf_meta = mf

    db.set_meta("poems_summary", {
        "account": ACCOUNT, "poems_id": led["meta"]["poems_id"], "trades": stats["trades"],
        "closed_lots": stats["closed"], "positions": stats["positions"],
        "cash": cash_meta.get("amount"), "cash_base": cash_meta.get("base"),
        "mf_units": mf_meta.get("units"), "mf_nav": mf_meta.get("nav"),
        "mf_value": mf_meta.get("value"),
        "period": led["meta"]["period"], "flows": led.get("flows"), "checks": led.get("checks"),
        "imported_at": "2026-11-09",
    })
    return {"ok": True, **stats}


def summary() -> dict:
    """给前端用：台账原始 + 数据库里的持仓（带实时价）。"""
    led = payload()
    if not led:
        return {"error": "no_ledger"}
    from . import portfolio
    rows = [r for r in portfolio.position_rows(account_id=ACCOUNT, with_quotes=True)]
    mf_value = None
    for r in rows:
        if r["symbol"] == MMF_SYMBOL:
            mf_value = r["value_native"]
    return {
        "meta": led["meta"],
        "period": led["meta"]["period"],
        "trades": led["trades"],
        "positions": rows,
        "cash": led["cash"],
        "mf": led["mf"],
        "mf_history": led["mf_history"],
        "flows": led["flows"],
        "monthly": led["monthly"],
        "closed_lots": led["closed_lots"],
        "checks": led["checks"],
        "valuation": {
            "stocks_idr": round(sum((r["value_native"] or 0) for r in rows
                                    if r["symbol"] != MMF_SYMBOL), 2),
            "mf_idr": mf_value,
            "cash_idr": led["cash"].get("amount"),
            "total_idr": round(sum((r["value_native"] or 0) for r in rows), 2)
                         + (led["cash"].get("amount") or 0),
        },
        "as_of": led["meta"]["generated"],
    }
