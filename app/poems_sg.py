# -*- coding: utf-8 -*-
"""POEMS 新加坡账户（Phillip Securities Pte Ltd，Margin Account）月结单台账。

数据来源（3 份 PDF）：
- Monthly statement 2025-11（结算币 USD，账户为 Margin Account）
- Monthly statement 2026-10
- Annual Dividend Statement 2025（含 30% 美国预扣税明细）

口径约定：
- 只做美股（SQQQ）＋ Phillip USD 货币基金，账户以 SGD 折算列示、结算币 USD；
- 现金/持股以「月结单期末」为准，不推算中间月份；
- **单据断档**：2025-12 → 2026-09 无月结单，持仓从 12 股跳到 165 股、基金从 51.23 份
  跳到 250.94 份，中间成交流水缺失，无法重建买入价与已实现盈亏，故不生成 closed_trades；
- 2020-02 → 2022-01 的旧台账（267 行）保留留档，与本段单据不合并。
"""
import json
from pathlib import Path

from . import config, db

LEDGER = config.PROCESSED_DIR / "poems_sg.json"
ACCOUNT = "poems_sg"
MMF_SYMBOL = "PHILLIPMMF.SG"
SOURCE = "poems_sg_docs"


def payload() -> dict:
    if not Path(LEDGER).exists():
        return {}
    return json.loads(Path(LEDGER).read_text(encoding="utf-8"))


def import_all() -> dict:
    """把 SG 月结单写入 st.db（可重复执行）。"""
    led = payload()
    if not led:
        return {"error": "no_ledger", "message": "缺 data/processed/poems_sg.json"}

    cur = led["current"]
    stats = {"positions": 0, "cash": None, "snapshots": len(led.get("snapshots") or [])}
    with db.db() as conn:
        conn.execute("DELETE FROM positions WHERE account_id=? AND source=?", [ACCOUNT, SOURCE])
        conn.execute("DELETE FROM cash_balances WHERE account_id=?", [ACCOUNT])

        for e in cur.get("equities") or []:
            opened = e.get("opened_at") or None
            conn.execute(
                "INSERT OR REPLACE INTO positions(id, account_id, symbol, name, market, ccy, side, qty, "
                "avg_cost, st_price, opened_at, status, source, note, quality, quality_note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [f"psg_{e['symbol']}", ACCOUNT, e["symbol"], e.get("name"), "US", e["ccy"], "LONG",
                 e["qty"], e["avg_cost"], e["close"], opened, "open", SOURCE,
                 f"POEMS SG 月结单 {cur['as_of'][:7]} · 成本 {e['avg_cost']:,.5f} · 收盘 {e['close']:,.2f}",
                 "ok", f"{cur['as_of']} 月结单持股"])
            stats["positions"] += 1

        mf = cur.get("mmf") or {}
        if mf.get("units"):
            conn.execute(
                "INSERT OR REPLACE INTO positions(id, account_id, symbol, name, market, ccy, side, qty, "
                "avg_cost, st_price, opened_at, status, source, note, quality, quality_note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [f"psg_MMF", ACCOUNT, MMF_SYMBOL, "Phillip USD Money Market Fund", "US", "USD", "LONG",
                 mf["units"], mf.get("cost_nav") or mf.get("nav") or 0, mf.get("nav") or 0, None,
                 "open", SOURCE,
                 f"货币基金 {mf['units']:,.2f} 份 · NAV {mf['nav']:,.5f} · {cur['as_of'][:7]}",
                 "ok", "月结单 MMF 行"])
            stats["positions"] += 1

            conn.execute(
                "INSERT OR REPLACE INTO price_overrides(symbol, price, note, asof, updated_at) VALUES(?,?,?,?,?)",
                [MMF_SYMBOL, mf["nav"], "POEMS SG 货币基金 NAV（月结单）", cur["as_of"], "2026-11-09"])

        cash = cur.get("cash") or {}
        conn.execute(
            "INSERT OR REPLACE INTO cash_balances(account_id, ccy, amount, as_of, source) VALUES(?,?,?,?,?)",
            [ACCOUNT, cash.get("ccy") or "USD", cash.get("amount"), cur["as_of"],
             "poems:sg 月结单期末现金"])
        stats["cash"] = cash.get("amount")

        # 账户状态：旧的「已关闭」判定作废，SG 仍在持仓
        conn.execute(
            "UPDATE accounts SET status='active', closed_at=NULL, note=? WHERE id=?",
            ["只做美股（SQQQ）＋USD 货币基金；2026-10 月结单仍有持仓。"
             "单据仅 2 期（2025-11 / 2026-10），2025-12→2026-09 断档，未重建成交。", ACCOUNT])

        # 旧的「无代码」2022 台账行：2026 月结单未复现，保留但不计权益
        conn.execute(
            "UPDATE positions SET quality_note=? WHERE account_id=? AND COALESCE(source,'')<>?",
            ["2022-01 旧台账行，2026 月结单未复现，不计权益", ACCOUNT, SOURCE])

    db.set_meta("poems_sg_summary", {
        "account": ACCOUNT, "acct_no": led["meta"]["acct_no"], "ac_type": led["meta"]["ac_type"],
        "period": led["meta"]["period"], "documents": led["meta"]["docs"],
        "coverage": led["meta"]["coverage"], "gap": led["meta"]["gap"],
        "cash": cash.get("amount"), "cash_ccy": cash.get("ccy"),
        "equity_usd": round((cash.get("amount") or 0) + (mf.get("value_usd") or 0), 2),
        "mmf_units": mf.get("units"), "mmf_nav": mf.get("nav"), "mmf_value_usd": mf.get("value_usd"),
        "positions": stats["positions"], "checks": led.get("checks"),
        "imported_at": "2026-11-09",
    })
    return {"ok": True, **stats}


def summary() -> dict:
    """给前端用：结单原始 + 数据库里的持仓（带实时价）。"""
    led = payload()
    if not led:
        return {"error": "no_ledger"}
    from . import portfolio
    rows = portfolio.position_rows(account_id=ACCOUNT, with_quotes=True)
    rates = portfolio._fx()
    cash = led["current"]["cash"]
    cash_usd = portfolio.to_usd(cash.get("amount"), cash.get("ccy"), rates)
    pos_usd = round(sum((r["value_usd"] or 0) for r in rows), 2)
    return {
        "meta": led["meta"],
        "snapshots": led["snapshots"],
        "current": led["current"],
        "transactions": led["transactions"],
        "dividends": led["dividends"],
        "checks": led["checks"],
        "positions": rows,
        "valuation": {
            "cash_usd": cash_usd,
            "stocks_usd": round(sum((r["value_usd"] or 0) for r in rows
                                    if r["symbol"] != MMF_SYMBOL), 2),
            "mmf_usd": round(sum((r["value_usd"] or 0) for r in rows
                                 if r["symbol"] == MMF_SYMBOL), 2),
            "total_usd": round(cash_usd + pos_usd, 2),
            "total_sgd": led["current"]["total"]["mv_sgd"],
        },
        "as_of": led["meta"]["generated"],
    }
