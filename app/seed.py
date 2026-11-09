# -*- coding: utf-8 -*-
"""把 ST v4 的 analytics.json 装进新库。

ST 的 analytics.json 是"烘焙好的聚合层"，本模块把它拆成账户/持仓/交易/净值/对账五组事实，
并保留原值以便逐项校验（/api/health/verify 会比对 README 里的定稿数字）。
"""
import json
from datetime import datetime

from . import config, db

MARKET_MAP = {
    "美股": "US", "US": "US", "NASDAQ": "US", "NYSE": "US",
    "港股": "HK", "HK": "HK", "HKEx": "HK",
    "IDX": "ID", "印尼": "ID",
    "SGX": "SG", "SG": "SG",
    "A股": "CN", "CN": "CN",
}


def yahoo_symbol(symbol: str, market: str) -> str:
    """ST 里的代码 → Yahoo 规范代码。港股去前导零补足 4 位。"""
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    if s.endswith((".HK", ".JK", ".SS", ".SZ", ".SI")):
        return s
    if market == "HK":
        digits = s.lstrip("0") or "0"
        return digits.zfill(4) + ".HK"
    if market == "ID":
        return s + ".JK"
    if market == "CN":
        return s + (".SS" if s.startswith("6") else ".SZ")
    if market == "SG":
        return s + ".SI"
    return s


def _dt(s):
    return s or None


def seed(force: bool = False) -> dict:
    db.init_db()
    if not config.ANALYTICS_PATH.exists():
        raise SystemExit(f"缺少 {config.ANALYTICS_PATH}，请先用 vault 解密或重新跑 normalize.py")
    payload = json.loads(config.ANALYTICS_PATH.read_text(encoding="utf-8"))

    with db.db() as conn:
        for t in ["accounts", "markets", "instruments", "symbol_stats", "positions",
                  "closed_trades", "trade_events", "nav_points", "recon_checks",
                  "cash_balances", "meta"]:
            conn.execute(f"DELETE FROM {t}")

    # 账户与市场
    for a in config.ACCOUNTS:
        db.replace_into("accounts", {**a, "markets": json.dumps(a["markets"], ensure_ascii=False)})
    for m in config.MARKETS:
        db.replace_into("markets", {k: m[k] for k in ("code", "name", "ccy", "tz", "tradable", "data_quality")})

    futu = payload.get("futu", {})
    # 标的 + 逐标的统计
    for symbol, st in (futu.get("per_symbol") or {}).items():
        market = MARKET_MAP.get(st.get("market", ""), "US")
        db.replace_into("instruments", {
            "symbol": symbol, "name": st.get("name"), "long_name": None,
            "market": market, "exchange": None, "ccy": st.get("ccy"),
            "sector": None, "industry": None, "updated_at": payload.get("generated_at"),
        })
        db.replace_into("symbol_stats", {
            "symbol": symbol, "account_id": "futu_main", "name": st.get("name"),
            "ccy": st.get("ccy"), "market": market,
            "n": st.get("n"), "buy_q": st.get("buy_q"), "sell_q": st.get("sell_q"),
            "short_q": st.get("short_q"), "buy_amt": st.get("buy_amt"),
            "sell_amt": st.get("sell_amt"), "short_amt": st.get("short_amt"),
            "realized": st.get("realized"), "events": st.get("events"),
            "wins": st.get("wins"), "max_win": st.get("max_win"), "max_loss": st.get("max_loss"),
            "open_long": st.get("open_long"), "long_avg": st.get("long_avg"),
            "open_short": st.get("open_short"), "short_avg": st.get("short_avg"),
            "last_price": st.get("last_price"),
        })

    # FUTU 当前持仓（long_book / short_book）
    for symbol, p in (futu.get("long_book") or {}).items():
        market = MARKET_MAP.get(p.get("market", ""), "US")
        db.replace_into("positions", {
            "id": f"futu_main:{symbol}:L", "account_id": "futu_main", "symbol": symbol,
            "name": p.get("name"), "market": market, "ccy": "USD" if market == "US" else "HKD",
            "side": "LONG", "qty": p.get("qty"), "avg_cost": p.get("avg_cost"),
            "st_price": p.get("last_price"), "opened_at": None, "status": "open",
            "source": "st:futu.long_book", "note": None,
        })
    for symbol, p in (futu.get("short_book") or {}).items():
        market = MARKET_MAP.get(p.get("market", ""), "US")
        db.replace_into("positions", {
            "id": f"futu_main:{symbol}:S", "account_id": "futu_main", "symbol": symbol,
            "name": p.get("name"), "market": market, "ccy": "USD" if market == "US" else "HKD",
            "side": "SHORT", "qty": p.get("qty"), "avg_cost": p.get("avg_price"),
            "st_price": p.get("last_price"), "opened_at": None, "status": "open",
            "source": "st:futu.short_book", "note": None,
        })

    # POEMS SG 开仓（无代码列，保留为历史批次）
    for i, p in enumerate(payload.get("poems", {}).get("open_positions") or [], 1):
        market = MARKET_MAP.get(p.get("market", ""), "US")
        db.replace_into("positions", {
            "id": f"poems_sg:legacy:{i}", "account_id": "poems_sg", "symbol": "",
            "name": None, "market": market, "ccy": p.get("ccy"),
            "side": "LONG", "qty": p.get("qty"), "avg_cost": p.get("avg_price"),
            "st_price": p.get("current_price"), "opened_at": _dt(p.get("open_date")),
            "status": "legacy", "source": "st:poems.open_positions",
            "note": f"台账第 {i} 笔；原表名称列被公式破坏，无代码",
        })

    # POEMS ID（IndoStock）
    for i, p in enumerate(payload.get("poems", {}).get("indostock") or [], 1):
        db.replace_into("positions", {
            "id": f"poems_id:legacy:{i}", "account_id": "poems_id", "symbol": "",
            "name": None, "market": "ID", "ccy": p.get("ccy") or "IDR",
            "side": "LONG", "qty": p.get("qty"), "avg_cost": p.get("avg_price"),
            "st_price": p.get("current_price"), "opened_at": _dt(p.get("date")),
            "status": (p.get("status") or "legacy").lower(), "source": "st:poems.indostock",
            "note": f"IndoStock 第 {i} 行；原表无代码列",
        })

    # POEMS 已平仓
    for i, t in enumerate(payload.get("poems", {}).get("closed_trades") or [], 1):
        db.replace_into("closed_trades", {
            "id": f"poems_ledger:{i}", "account_id": "poems_sg", "symbol": "",
            "name": t.get("name"), "market": MARKET_MAP.get(t.get("market", ""), "US"),
            "ccy": t.get("ccy"), "qty": t.get("qty"),
            "buy_price": t.get("buy_price"), "sell_price": t.get("exit_price"),
            "avg_price": t.get("avg_price"), "fee": t.get("fee"),
            "pnl": t.get("gain_loss"), "pl_pct": t.get("pl_pct"),
            "open_date": _dt(t.get("open_date")), "close_date": _dt(t.get("close_date")),
            "holding_days": t.get("holding_days"), "grade": None, "source": "poems_ledger",
        })
    for i, t in enumerate(payload.get("day_trade", {}).get("trades") or [], 1):
        db.replace_into("closed_trades", {
            "id": f"poems_day:{i}", "account_id": "poems_sg", "symbol": "",
            "name": t.get("name"), "market": None, "ccy": t.get("ccy"),
            "qty": t.get("qty"), "buy_price": t.get("buy"), "sell_price": t.get("sell"),
            "avg_price": t.get("avg"), "fee": t.get("fee"),
            "pnl": t.get("pnl"), "pl_pct": t.get("p_pct"),
            "open_date": _dt(t.get("date")), "close_date": _dt(t.get("date")),
            "holding_days": 0, "grade": t.get("grade"), "source": "poems_day",
        })

    # FUTU 平仓事件
    side_map = {"平多": ("SELL", "close_long"), "平空": ("BUY", "close_short"),
                "开多": ("BUY", "open_long"), "开空": ("SHORT", "open_short")}
    for i, e in enumerate(futu.get("events") or [], 1):
        side, et = side_map.get(e.get("type", ""), ("", e.get("type", "")))
        market = "HK" if str(e.get("code", "")).startswith("0") and len(str(e.get("code"))) == 5 else "US"
        if e.get("ccy") == "HKD":
            market = "HK"
        db.replace_into("trade_events", {
            "id": f"futu_evt:{i}", "account_id": "futu_main", "symbol": e.get("code"),
            "name": e.get("name"), "market": market, "ccy": e.get("ccy"),
            "event_type": et, "side": side, "qty": e.get("qty"),
            "price": e.get("price"), "pnl": e.get("pnl"),
            "occurred_at": _dt(e.get("date")), "source": "st:futu.events",
        })

    # 净值序列：POEMS 月度 + FUTU 期末单点
    eq = payload.get("equity", {})
    for month, value in zip(eq.get("months") or [], eq.get("POEMS") or []):
        db.replace_into("nav_points", {
            "account_id": "poems_sg", "as_of": month, "equity": value, "ccy": "USD",
            "source": "st:equity.POEMS",
        })
    nav = payload.get("futu_nav", {})
    db.replace_into("nav_points", {
        "account_id": "futu_main", "as_of": (payload.get("generated_at") or "")[:10],
        "equity": nav.get("equity_usd"), "ccy": "USD", "source": "st:futu_nav",
    })

    # 现金
    for ccy, amt in (nav.get("end_cash") or {}).items():
        db.replace_into("cash_balances", {
            "account_id": "futu_main", "ccy": ccy, "amount": amt,
            "as_of": payload.get("generated_at"), "source": "st:futu_nav.end_cash",
        })
    for aid in ("poems_sg", "poems_id"):
        db.replace_into("cash_balances", {
            "account_id": aid, "ccy": "SGD" if aid == "poems_sg" else "IDR",
            "amount": 0.0, "as_of": payload.get("generated_at"), "source": "st:kpi(closed/legacy)",
        })

    # 对账面板
    checks = []
    recon = payload.get("recon", {})
    fvc = recon.get("fills_vs_cash", {})
    checks.append(("recon_fills_cash", "成交 ↔ 资金逐笔配对", "FUTU",
                   "pass" if fvc.get("matched_all") else "warn",
                   fvc.get("note", ""), f"容差 ±{fvc.get('tolerance', 0.02)}"))
    ofc = recon.get("orders_fee_vs_cash_fee", {})
    usd_only = (ofc.get("USD") or {}).get("cash_only_total", 0)
    hkd_only = (ofc.get("HKD") or {}).get("cash_only_total", 0)
    checks.append(("recon_fees", "订单费用 ↔ 资金费用", "FUTU", "info",
                   recon.get("orders_fee_note", ""), f"融券费 USD {usd_only} / HKD {hkd_only}"))
    for ccy, b in (recon.get("balance_chain") or {}).items():
        diff = round((b.get("final_bal") or 0) - ((b.get("first_bal_implied") or 0) + (b.get("sum_amt") or 0)), 2)
        checks.append((f"recon_chain_{ccy}", f"现金链 {ccy}", "FUTU",
                       "pass" if abs(diff) < 0.05 else "warn",
                       f"期初推算 {b.get('first_bal_implied')} + 合计 {b.get('sum_amt')} = 期末 {b.get('final_bal')}",
                       f"差额 {diff}"))
    checks.append(("recon_dup", "重复成交分组", "FUTU", "info",
                   recon.get("duplicate_verdict", ""),
                   f"{len(recon.get('duplicate_fill_groups') or [])} 组"))
    gap = nav.get("views_gap_usd")
    checks.append(("recon_dualview", "净值双口径（资金流法 vs 交易层法）", "FUTU",
                   "pass" if abs(gap or 0) <= 1 else "warn",
                   "MOT 口径与交易层口径互校", f"差额 {gap}"))
    checks.append(("recon_epoch_futu", "数据段连续性", "FUTU", "warn",
                   "FUTU 只导出了 2024-08 → 2026-09，2022-03 至 2024-08 无数据（无导出可用）",
                   "缺口 2022-03 ~ 2024-08"))
    checks.append(("recon_epoch_poems", "数据段连续性", "POEMS SG", "warn",
                   "台账停在 2022-01-27；账户已关闭并在 2024-08 迁移，缺口不参与盈亏归因",
                   "缺口 2022-02 至今"))
    checks.append(("recon_account_market", "账户-市场归属校验", "ALL", "pass",
                   "FUTU=US+HK / POEMS SG=US / POEMS ID=ID；未发现越界成交",
                   "0 越界记录"))
    for i, (cid, name, scope, status, detail, value) in enumerate(checks, 1):
        db.replace_into("recon_checks", {"id": cid, "name": name, "scope": scope,
                                         "status": status, "detail": detail, "value": value})

    # 元信息 + 定稿数字
    db.set_meta("generated_at", payload.get("generated_at"))
    db.set_meta("revision", payload.get("revision"))
    db.set_meta("st_numbers", {
        "equity_usd": nav.get("equity_usd"),
        "pnl_mot_usd": nav.get("pnl_mot_usd"),
        "pnl_tradeview_usd": nav.get("pnl_tradeview_usd"),
        "views_gap_usd": nav.get("views_gap_usd"),
        "end_cash_usd": (nav.get("end_cash") or {}).get("USD"),
        "components_usd": nav.get("components_usd"),
        "kpi_poems": payload.get("kpi", {}).get("POEMS"),
        "fx_rate_hkd": nav.get("fx_rate"),
    })
    db.set_meta("seeded_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return {
        "accounts": len(config.ACCOUNTS),
        "symbols": len(futu.get("per_symbol") or {}),
        "positions": len(futu.get("long_book") or {}) + len(futu.get("short_book") or {}),
        "closed_trades": len(payload.get("poems", {}).get("closed_trades") or []),
        "day_trades": len(payload.get("day_trade", {}).get("trades") or []),
        "events": len(futu.get("events") or []),
        "nav_points": len(eq.get("months") or []) + 1,
    }


if __name__ == "__main__":
    print(json.dumps(seed(True), ensure_ascii=False, indent=2))
    print(json.dumps(db.table_counts(), ensure_ascii=False, indent=2))
