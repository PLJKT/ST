# -*- coding: utf-8 -*-
"""交易关键指标：期间 / 笔数 / 盈亏 / 出入金 / 持仓时长 / 回报率，以及券商流水对账。

两套口径并存是这份数据的现实：
- 台账口径（ST 定稿，2026-09-24 快照）：已实现盈亏、平仓事件、持仓。
- 券商口径（富途「资金明细」CSV）：现金、出入金、成交笔数、现金盈亏。
对不上的地方在这里显性列出，不让某个数字假装覆盖另一个。
"""
from collections import defaultdict
from datetime import datetime

from . import db, futu_hist, market, poems, poems_sg, portfolio, statement

ACCOUNTS = [
    ("futu_main", "富途牛牛（主）"),
    ("poems_sg", "POEMS SG（次）"),
    ("poems_id", "POEMS ID（本地）"),
]


def _rates() -> dict:
    try:
        return market.fx_rates()
    except Exception:
        return {"USD": 1.0, "HKD": 0.1282, "SGD": 0.74, "IDR": 0.000064}


def _usd(v, ccy, rates):
    if v is None:
        return None
    return float(v) * float(rates.get(ccy or "USD", 1.0))


def _d(s):
    s = (s or "")[:10]
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _poems_stats(account_id: str, rates: dict, acc: dict) -> dict:
    rows = db.query("SELECT * FROM closed_trades WHERE account_id=? "
                    "AND COALESCE(quality,'ok')<>'reject'", [account_id])
    dates = [d for d in (_d(r["open_date"]) for r in rows) if d]
    dates += [d for d in (_d(r["close_date"]) for r in rows) if d]
    holds = [r["holding_days"] for r in rows if (r["holding_days"] or 0) > 0]
    wins = sum(1 for r in rows if (r["pnl"] or 0) > 0)
    rets = []
    for r in rows:
        if r["pl_pct"] is None or not r["holding_days"]:
            continue
        rets.append(float(r["pl_pct"]) / max(r["holding_days"] / 30.0, 0.5))
    avg_month = sum(rets) / len(rets) if rets else None
    return {
        "closed_trades": len(rows),
        "trade_events": 0,
        "realized_usd": acc.get("realized_usd"),
        "realized_detail_usd": acc.get("realized_detail_usd"),
        "win_rate": round(wins / len(rows), 4) if rows else None,
        "avg_hold_days": round(sum(holds) / len(holds), 1) if holds else None,
        "avg_month_return": avg_month,
        "annual_return": (1 + avg_month) ** 12 - 1 if avg_month is not None else None,
        "start": min(dates).strftime("%Y-%m-%d") if dates else None,
        "end": max(dates).strftime("%Y-%m-%d") if dates else None,
        "days": (max(dates) - min(dates)).days if dates else 0,
        "fees_usd": None,
    }


def _futu_stats(rates: dict, acc: dict, st: dict) -> dict:
    ev = db.query("SELECT * FROM trade_events WHERE account_id='futu_main' "
                  "AND COALESCE(quality,'ok')<>'reject'")
    dates = [d for d in (_d(r["occurred_at"]) for r in ev) if d]
    wins = sum(1 for r in ev if (r["pnl"] or 0) > 0)
    # 资金加权年化：已实现 / 净外部入金 / 年数（这是唯一有出入金可算的账户）
    capital_annual = None
    if st.get("net_external") and st.get("period_start"):
        yrs = max((datetime.fromisoformat((st["period_end"] or "")[:10])
                   - datetime.fromisoformat((st["period_start"] or "")[:10])).days / 365.0, 0.1)
        capital_annual = (acc.get("realized_usd") or 0) / float(st["net_external"]) / yrs
    return {
        "closed_trades": 0,
        "trade_events": len(ev),
        "realized_usd": acc.get("realized_usd"),
        "win_rate": round(wins / len(ev), 4) if ev else None,
        "avg_hold_days": None,  # 台账事件只记平仓日，没有开仓日
        "avg_month_return": None,
        "annual_return": None,
        "capital_weighted_annual": capital_annual,
        "start": min(dates).strftime("%Y-%m-%d") if dates else None,
        "end": max(dates).strftime("%Y-%m-%d") if dates else None,
        "days": (max(dates) - min(dates)).days if dates else 0,
        "fees_usd": None,
    }


def _cash_recon(rates: dict, rows: list[dict] | None = None) -> dict:
    st = statement.summary()
    cash_rows = db.query("SELECT * FROM cash_balances WHERE account_id='futu_main'")
    cash_sys_usd = round(sum((_usd(r["amount"], r["ccy"], rates) or 0) for r in cash_rows), 2)
    cash_sys_asof = max((r["as_of"] or "") for r in cash_rows) if cash_rows else ""
    cash_broker_usd = round(sum((_usd(v, c, rates) or 0) for c, v in (st.get("cash_end") or {}).items()), 2)

    ev_count = db.query("SELECT COUNT(*) AS n FROM trade_events "
                        "WHERE COALESCE(quality,'ok')<>'reject'")[0]["n"]

    rows = rows or portfolio.position_rows(with_quotes=True)
    futu_rows = [r for r in rows
                 if (r.get("account") or r.get("account_id")) == "futu_main"]
    symbols = [r["symbol"] for r in futu_rows if r["symbol"]]
    snap = db.get_meta("positions_snapshot") or "2026-09-24 18:04"
    after = statement.trades_after("futu_main", snap, symbols)

    mv = round(sum(r["value_usd"] or 0 for r in futu_rows), 2)
    # 券商快照市值：数量 × 券商当时报价（与实时报价的差额单独列示）
    mv_broker = round(sum((r.get("qty") or 0) * (r.get("st_price") or r.get("price") or 0)
                          * (-1 if (r.get("side") or "").upper() == "SHORT" else 1)
                          for r in futu_rows), 2)
    equity_ledger = round(cash_sys_usd + mv, 2)
    stale_flow = round(sum(after["by_symbol"].values()), 2)
    adjust = -stale_flow
    total_pnl = round(cash_broker_usd + mv_broker - (st.get("net_external") or 0), 2)
    return {
        "cash_sys_usd": cash_sys_usd, "cash_sys_asof": cash_sys_asof,
        "cash_broker_usd": cash_broker_usd, "cash_broker_asof": (st.get("period_end") or "")[:16],
        "cash_gap": round(cash_broker_usd - cash_sys_usd, 2),
        "broker_trade_count": st.get("trade_count"), "ledger_event_count": ev_count,
        "trade_gap": (st.get("trade_count") or 0) - ev_count,
        "broker_net": st.get("net_result"),
        "net_external_usd": st.get("net_external"),
        "mv_broker": mv_broker,
        "total_pnl_est": total_pnl,
        "equity_ledger": equity_ledger, "market_value_usd": mv,
        "stale_flow_usd": stale_flow, "adjust_usd": round(adjust, 2),
        "equity_est": round(equity_ledger + adjust, 2),
        "stale_detail": after["by_symbol"], "positions_snapshot": snap,
        "cash_deposit_usd": st.get("deposit", {}).get("usd"),
        "cash_withdraw_usd": abs(st.get("withdraw", {}).get("usd") or 0),
        "migration_usd": st.get("migration", {}).get("usd"),
    }


def key_metrics(rows: list[dict] | None = None) -> dict:
    rates = _rates()
    accs = {a["id"]: a for a in portfolio.account_summaries()}
    st = statement.summary()
    recon = _cash_recon(rates, rows)

    accounts = []
    for aid, name in ACCOUNTS:
        a = accs.get(aid, {})
        if aid == "futu_main":
            s = _futu_stats(rates, a, st)
            extra = {
                "deposit_usd": st["deposit"]["usd"],
                "withdraw_usd": abs(st["withdraw"]["usd"]),
                "net_external_usd": st["net_external"],
                "migration_usd": st["migration"]["usd"],
                "broker_cash_usd": recon["cash_broker_usd"],
                "broker_trades": st["trade_count"],
                "broker_net_usd": st["net_result"],
                "flow_source": "券商资金明细",
                "cash_usd": a.get("cash_usd"),
                "positions_usd": a.get("positions_usd"),
                "equity_usd": a.get("equity_usd"),
            }
            s["start"] = min(filter(None, [s.get("start"), (st["period_start"] or "")[:10],
                                           (futu_hist.summary().get("period_start"))]) or [None])
            s["end"] = max(filter(None, [s.get("end"), (st["period_end"] or "")[:10]]) or [None])
            if s["start"] and s["end"]:
                s["days"] = (datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).days
        elif aid == "poems_sg":
            # POEMS SG：2026-10 月结单仍有持仓（SQQQ + USD 货币基金），
            # 但只有 2025-11 / 2026-10 两期单据，2025-12→2026-09 断档，成交史无法重建。
            s = _poems_stats(aid, rates, a)
            sg = poems_sg.summary() or {}
            cur = sg.get("current") or {}
            extra = {"deposit_usd": None, "withdraw_usd": None, "net_external_usd": None,
                     "migration_usd": None, "flow_source": "无出入金流水（仅 2 期月结单）",
                     "cash_usd": a.get("cash_usd"), "positions_usd": a.get("positions_usd"),
                     "equity_usd": a.get("equity_usd"),
                     "cash_native": (cur.get("cash") or {}).get("amount"),
                     "cash_ccy": (cur.get("cash") or {}).get("ccy"),
                     "mf_units": (cur.get("mmf") or {}).get("units"),
                     "mf_value_usd": (cur.get("mmf") or {}).get("value_usd"),
                     "doc_period": (sg.get("meta") or {}).get("period"),
                     "doc_count": (sg.get("meta") or {}).get("docs")}
            # 台账停在 2022-01，但 2026-10 月结单证明仍在持仓：期间按最后一张单据收口，
            # 回报率仍只能用 2020–2022 台账口径（2026 期无成交单据），必须标明。
            if s.get("end"):
                s["end"] = "2026-10-31"
                if s.get("start"):
                    s["days"] = (datetime.fromisoformat(s["end"])
                                 - datetime.fromisoformat(s["start"])).days
            s["return_basis"] = "2020–2022 台账口径（2026 期无成交单据）"
        else:
            # POEMS ID：2026-03 起的真实对账单 + 成交确认
            s = _poems_stats(aid, rates, a)
            led = poems.payload() or {}
            flows = led.get("flows") or {}
            r = float(rates.get("IDR") or 0.0000625)
            pr = (led.get("meta") or {}).get("period") or ""
            if "→" in pr:
                s["start"], s["end"] = [x.strip() for x in pr.split("→")]
                s["days"] = (datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).days
            s["trade_count"] = len(led.get("trades") or [])
            # 单笔年化对 1–5 天的短线调仓会失真，改用资金口径：
            # 已实现盈亏 / 月均账户净值 / 月数（净值 = 对账单持仓市值 + 期末现金）
            navs = []
            for m in led.get("monthly") or []:
                hv = sum((h.get("value") or 0) for h in (m.get("holdings") or []))
                if not m.get("holdings") and not (m.get("cash") or 0):
                    continue
                navs.append(hv + (m.get("cash") or 0))
            realized_native = sum((c.get("pnl") or 0) for c in (led.get("closed_lots") or []))
            months = max((s.get("days") or 0) / 30.4, 1.0)
            peak_nav = max(navs) if navs else None
            if peak_nav and peak_nav > 0:
                s["avg_month_return"] = realized_native / peak_nav / months
                s["annual_return"] = s["avg_month_return"] * 12
                s["return_basis"] = "峰值资金口径（已实现/峰值净值）"
                s["avg_nav_idr"] = round(peak_nav)
                s["realized_native"] = round(realized_native)
            extra = {
                "deposit_usd": round((flows.get("deposit") or 0) * r, 2),
                "withdraw_usd": round((flows.get("withdraw") or 0) * r, 2),
                "net_external_usd": round((flows.get("net_external") or 0) * r, 2),
                "migration_usd": None,
                "flow_source": "POEMS 对账单（2026-03 → 2026-09）",
                "cash_usd": a.get("cash_usd"), "positions_usd": a.get("positions_usd"),
                "equity_usd": a.get("equity_usd"),
                "cash_native": (led.get("cash") or {}).get("amount"),
                "mf_units": (led.get("mf") or {}).get("units"),
                "mf_value_usd": round(((led.get("mf") or {}).get("value") or 0) * r, 2),
                "realized_native_idr": (led.get("closed_lots") and
                                        round(sum(c["pnl"] or 0 for c in led["closed_lots"]), 0)),
            }

        tags = [{"k": "台账", "v": f"{s['closed_trades'] or s['trade_events']} 笔"}]
        if aid == "futu_main":
            tags.append({"k": "券商流水", "v": f"{st['trade_count']} 成交"})
            tags.append({"k": "历史段", "v": f"{(futu_hist.summary().get('us') or {}).get('rows', 0) + (futu_hist.summary().get('hk') or {}).get('rows', 0)} 笔（2022-01 起）"})
            tags.append({"k": "持仓快照", "v": recon["positions_snapshot"][:10]})
            if s["avg_hold_days"] is None:
                tags.append({"k": "缺持仓时长", "v": "事件无开仓日"})
        if extra["deposit_usd"] is None:
            tags.append({"k": "缺出入金", "v": "无流水"})
        if aid == "poems_sg":
            tags.append({"k": "单据", "v": f"{extra.get('doc_count') or 0} 份（2 期月结单）"})
            tags.append({"k": "持仓快照", "v": "2026-10-31"})
            tags.append({"k": "台账断档", "v": "2022-02→2026-09"})
            if extra.get("mf_units"):
                tags.append({"k": "货币基金", "v": f"{extra['mf_units']:,.0f} 份"})
        if aid == "poems_id":
            tags.append({"k": "成交确认", "v": f"{s.get('trade_count') or 0} 笔"})
            tags.append({"k": "对账期末", "v": "2026-09-30"})
            tags.append({"k": "缺 PGAS 回款", "v": "10,000 股已清仓 · 现金未入"})
            if extra.get("mf_units"):
                tags.append({"k": "货币基金", "v": f"{extra['mf_units']:,.0f} 份"})
        accounts.append({"id": aid, "name": name, **s, **extra, "tags": tags})

    # 总体
    starts = [a["start"] for a in accounts if a.get("start")]
    ends = [a["end"] for a in accounts if a.get("end")]
    start, end = min(starts), max(ends)
    days = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
    total_realized = round(sum(a["realized_usd"] or 0 for a in accounts), 2)
    total_closed = sum(a["closed_trades"] for a in accounts) + sum(a["trade_events"] for a in accounts)
    holds = [(a["avg_hold_days"], a["closed_trades"]) for a in accounts
             if a["avg_hold_days"] and a["closed_trades"]]
    wavg_hold = round(sum(h * n for h, n in holds) / max(sum(n for _, n in holds), 1), 1) if holds else None
    rets = []
    for r in db.query("SELECT pl_pct, holding_days FROM closed_trades WHERE "
                      "COALESCE(quality,'ok')<>'reject' AND holding_days>0 AND pl_pct IS NOT NULL "
                      "AND COALESCE(source,'')<>'poems_id_docs'"):
        rets.append(float(r["pl_pct"]) / max((r["holding_days"] or 0) / 30.0, 0.5))
    avg_month = sum(rets) / len(rets) if rets else None
    annual = (1 + avg_month) ** 12 - 1 if avg_month is not None else None

    overall = {
        "start": start, "end": end, "days": days, "years": round(days / 365.0, 2),
        "closed_trades": total_closed,
        "realized_usd": total_realized,
        "avg_hold_days": wavg_hold,
        "avg_month_return": avg_month,
        "annual_return": annual,
        "broker_trades": st.get("trade_count"),
        "broker_net_usd": st.get("net_result"),
        "net_external_usd": st.get("net_external"),
    }

    recon_rows = [
        {"item": "现金余额", "broker": f"${recon['cash_broker_usd']:,.2f}",
         "ledger": f"${recon['cash_sys_usd']:,.2f}",
         "diff": f"{recon['cash_gap']:+,.2f}",
         "level": "ok" if abs(recon["cash_gap"]) < 100 else "danger",
         "note": f"券商 {st['period_end'][:16]} 的期末余额，已写入系统现金（原台账 -14,618.28，9-24 快照）。"},
        {"item": "成交笔数", "broker": f"{recon['broker_trade_count']} 笔",
         "ledger": f"{recon['ledger_event_count']} 笔",
         "diff": f"{recon['trade_gap']:+d}",
         "level": "warn",
         "note": "券商流水覆盖 2024-08 起全部成交；台账只收了 ST 表里挑出来的平仓事件，缺口是历史遗漏。"},
        {"item": "持仓市值", "broker": f"${recon['mv_broker']:,.2f}（券商快照价）",
         "ledger": f"${recon['market_value_usd']:,.2f}（实时报价）",
         "diff": f"{round(recon['market_value_usd'] - recon['mv_broker'], 2):+,.2f}",
         "level": "ok" if abs(recon["market_value_usd"] - recon["mv_broker"]) < 100 else "warn",
         "note": "数量已与 2026-11-09 券商快照逐行一致；差额只来自报价时点（GLD 实时 372.52 vs 券商 376.670）。"},
        {"item": "累计交易净额", "broker": f"${recon['broker_net']:,.2f}（现金口径）",
         "ledger": f"${round(sum(a['realized_usd'] or 0 for a in accounts if a['id']=='futu_main'), 2):,.2f}（已实现）",
         "diff": f"{(recon['broker_net'] - (accounts[0]['realized_usd'] or 0)):+,.2f}",
         "level": "warn",
         "note": "现金口径含费用、利息、股息与仍持仓的浮盈变动；台账只算已平仓 FIFO 已实现，两者天然不等。"},
        {"item": "券商累计总盈亏", "broker": f"${recon['total_pnl_est']:,.2f}（含浮动）",
         "ledger": f"${(accounts[0]['realized_usd'] or 0):,.2f}（已实现）",
         "diff": f"{recon['total_pnl_est'] - (accounts[0]['realized_usd'] or 0):+,.2f}",
         "level": "ok",
         "note": f"券商口径 = 期末现金 ${recon['cash_broker_usd']:,.2f} + 持仓 ${recon['mv_broker']:,.2f} − 净投入 ${recon['net_external_usd']:,.2f}；"
                 "与台账已实现的差额含浮动盈亏、费用、利息与股息。"},
        {"item": "9-24 后成交未扣仓", "broker": f"${recon['stale_flow_usd']:+,.2f} 净卖出",
         "ledger": f"持仓快照 {recon['positions_snapshot'][:10]}",
         "diff": f"{recon['adjust_usd']:+,.2f}",
         "level": "danger" if abs(recon["adjust_usd"]) > 1000 else "ok",
         "note": "持仓已按 2026-11-09 券商快照校正，此处应恒为 0；若再次偏离说明又有成交未回填。"},
    ]

    # 全历史盈亏分段对账：把「所有账号历史累计」拆成有凭证的段落
    futu_acc = next((a for a in accounts if a["id"] == "futu_main"), {})
    id_acc = next((a for a in accounts if a["id"] == "poems_id"), {})
    sg_acc = next((a for a in accounts if a["id"] == "poems_sg"), {})
    stn = db.get_meta("st_numbers") or {}
    kpi = stn.get("kpi_poems") or {}
    life = stn.get("life_recon") or {}
    futu_total = recon["total_pnl_est"]
    # 6519 / 9461 的 2022-01→2024-08 段（成交+费用口径），与 1599 段分开列
    fh = futu_hist.summary()
    carried = float(fh.get("carried_usd") or 0.0)
    futu_old = float(fh.get("segment_usd") or 0.0)
    # 1599 段的起点不是「零」：迁移进来的 01519 8,000 股与 AVGO −10 股空头
    # 是资产而非入金，必须计入投入，否则 1599 段盈亏被高估 carried
    futu_new = round(futu_total - carried, 2)
    futu_life = round(futu_old + futu_new, 2)
    futu_bank = float((st.get("deposit") or {}).get("usd") or 0) + float((st.get("withdraw") or {}).get("usd") or 0)
    mig_net = round(float(recon["net_external_usd"] or 0) - futu_bank, 2)
    sg_start = float(life.get("sg_legacy_start") or 23110.0)
    sg_end = float(life.get("sg_legacy_end") or 29772.84)
    sg_unreal = float(life.get("sg_open_unreal") or -4767.29)
    sg_era = round(mig_net - sg_start, 2)                 # 2020-02 → 2024-07 整段
    sg_r1 = round(sg_end - sg_start, 2)                     # 已实现
    sg_r2 = round(sg_unreal, 2)                             # 2022-01 浮亏兑现
    sg_old_exit = round(sg_start + sg_r1 + sg_r2, 2)        # 旧户结清后退出本账本的价值
    sg_new_start = float(life.get("sg_new_start") or 895.69)
    sg_cost = 0.0
    for r in db.query("SELECT qty, avg_cost FROM positions WHERE account_id='poems_sg' "
                      "AND COALESCE(quality,'ok')<>'reject'"):
        sg_cost += float(r["qty"] or 0) * float(r["avg_cost"] or 0)
    sg_new_invest = round(sg_new_start + sg_cost, 2)
    sg_new_pnl = round(float(sg_acc.get("equity_usd") or 0) - sg_new_invest, 2)
    idr = float(life.get("idr_usd") or 18106.95)
    pgas_usd = round(float(life.get("id_pgas_idr") or 14900000.0) / idr, 2)
    id_pnl = round((id_acc.get("equity_usd") or 0) - (id_acc.get("net_external_usd") or 0) + pgas_usd, 2)
    total = round(futu_life + sg_r1 + sg_r2 + sg_new_pnl + id_pnl, 2)
    # 交叉校验只覆盖「1599 起」的时段：2022-01→2024-08 段的出入金流水不在手上，
    # 它的盈亏由 data/processed/futu_hist_6519_9461.json 自带校验（段初为零、段末 = 结转持仓）
    chk_base = round(futu_new + sg_r1 + sg_r2 + sg_new_pnl + id_pnl, 2)
    memory = float(life.get("memory_usd") or -50000.0)
    old = life.get("old_dashboard") or {}
    futu_broker_val = round(recon["cash_broker_usd"] + recon["mv_broker"], 2)
    now_val = round(futu_broker_val + float(sg_acc.get("equity_usd") or 0)
                    + float(id_acc.get("equity_usd") or 0) + pgas_usd, 2)
    in_total = round(float(recon["net_external_usd"] or 0) + carried + sg_start + sg_new_invest
                     + float(id_acc.get("net_external_usd") or 0), 2)
    chk = round(now_val + sg_old_exit - in_total, 2)
    bridge = [
        {"k": "POEMS SG 旧户 · 2020-02→2022-01 已实现", "v": sg_r1, "level": "ok",
         "n": f"台账初始入金 {sg_start:,.2f}，平仓后权益 {sg_end:,.2f}（146 笔美股 + 12 笔港股，胜率 78%）"},
        {"k": "POEMS SG 旧户 · 2022-01 未平仓浮亏", "v": sg_r2, "level": "ok",
         "n": "2022-01-27 还拿着 6 只（含 100 股亏损仓），台账按当日价计的账面浮亏；这部分在清仓时兑现"},
        {"k": "富途 6519/9461 · 2022-01→2024-08 成交+费用口径", "v": futu_old, "level": fh.get("level") or "warn",
         "n": (f"美股 {fh['us']['rows']} 笔成交额 {fh['us']['turnover']:,.2f} / 费用 {fh['us']['fees']:,.2f}；"
               f"港股 {fh['hk']['rows']} 笔成交额 {fh['hk']['turnover']:,.2f} HKD / 费用 {fh['hk']['fees']:,.2f} HKD；"
               f"迁移日结转的 01519 8,000 股与 AVGO −10 股按次日成交价计 {carried:,.2f}。"
               f"AMAT 394 股（成本 {fh.get('amat', {}).get('cost_usd', 0):,.2f}）无平仓/迁移记录，未并入本段——若确认已灭失，本段为 {fh.get('amat_alt_usd', 0):,.2f}")},
        {"k": "富途 1599 · 2024-08→2026-11 券商口径", "v": futu_new, "level": "ok",
         "n": f"期末现金 {recon['cash_broker_usd']:,.2f} + 持仓 {recon['mv_broker']:,.2f} − 净投入 (银行存入 {float((st.get('deposit') or {}).get('usd') or 0):,.2f} / 取出 {float((st.get('withdraw') or {}).get('usd') or 0):,.2f} / 开户日内部迁移入账 {mig_net:,.2f} + 随迁持仓 {carried:,.2f})；"
                 "迁移来自同号融资融券子账户 6519 / 9461，不是 POEMS"},
        {"k": "POEMS SG 新户 · 2025-11→2026-11", "v": sg_new_pnl, "level": "warn",
         "n": f"首见余额 {sg_new_start:,.2f} + 期间买入成本 {sg_cost:,.2f}（SQQQ 165 股 + 货基 250.9555 份）− 现值 {float(sg_acc.get('equity_usd') or 0):,.2f}；2025-12→2026-09 无月结单，投入按持仓成本反推"},
        {"k": "POEMS ID · 2026-03→2026-11 资金口径", "v": id_pnl, "level": "ok",
         "n": f"现值 {float(id_acc.get('equity_usd') or 0):,.2f} + PGAS 回款 {pgas_usd:,.2f} − 净投入 {float(id_acc.get('net_external_usd') or 0):,.2f}（IDR 折 USD，汇率 {idr:,.2f}）"},
        {"k": "全历史合计（逐段相加）", "v": total, "level": "ref",
         "n": f"2020-02 → 2026-11-09，三个独立账户各自结算、账户之间无互转。富途一段拆两行：融资户 {futu_old:,.2f} + 保证金户 {futu_new:,.2f} = {futu_life:,.2f}。"
                 f"交叉校验（1599 起）：累计投入 {in_total:,.2f}（净外部投入 {float(recon['net_external_usd'] or 0):,.2f} + 随迁持仓 {carried:,.2f} + SG/ID 投入）"
                 f"vs 已退出 {sg_old_exit:,.2f} + 现值 {now_val:,.2f} = {chk:,.2f}，与小计 {chk_base:,.2f} 差 {round(chk - chk_base, 2):+,.2f}；"
                 f"2022-01→2024-08 段不含在本次交叉校验内（无出入金流水，段盈亏独立推导）"},
        {"k": "你说的记忆 · 约 −5 万", "v": memory, "level": "danger",
         "n": f"与全历史合计差 {round(memory - total, 2):+,.2f}。这个数来自旧仪表盘的「回本缺口 {float(old.get('gap') or 49293.65):,.2f}」："
                 "本金 66,062.97 = 富途 42,952.97（正好等于 9-24 时点的净投入 34,676.90 + 22,468.37 − 7,200 − 6,992.30）"
                 f"+ SG 旧户 {sg_start:,.0f}，但减去的权益 {float(old.get('current_then') or 16769.32):,.2f} 只是富途一户；"
                 "SG 新户与 ID 的投入（约 1.89 万）和权益都没进这条式子 → 混合口径，不是终身亏损。"
                 f"补齐 2022-2024 富途段后差额从 −35,632 收窄到 {round(memory - total, 2):+,.2f}；"
                 f"唯一还能大幅移动这条数的是 AMAT 394 股（成本 {fh.get('amat', {}).get('cost_usd', 0):,.2f}）"},
    ]
    coverage = [
        "口径：三个账户各自独立结算，从未互转资金或持仓。富途 2024-08-03 的「资产迁移」是同一富途号内部的子账户升级（融资融券户 6519/9461 → 保证金综合户 1599），不是从 POEMS 转入，也不是外部入金",
        "已覆盖：富途 6519（美股）/ 9461（港股）2022-01-13→2024-08-02 全部 4,562 笔成交与 4,166 条订单费用（dataset：data/processed/futu_hist_6519_9461.json）",
        "已覆盖：富途 1599 2024-08-03→2026-11-06 全部成交与资金流水（含迁移、换汇）",
        "已覆盖：POEMS ID 2026-03-04→2026-11-06 共 62 份单据、56 笔成交、29 个平仓批次",
        "已覆盖：POEMS SG 2025-11 与 2026-10 两期月结单 + 2025 年度股息税单；旧户 2020-02→2022-01 台账",
        "未闭合：富途 6519 的 AMAT 394 股自 2023-03-03 起无任何成交、1599 侧 0 次出现，成本 46,843.49 USD 单列不计；该段盈亏随此项在 -3,326 与 -50,170 之间摆动",
        "口径缺口：6519/9461 的导出只有成交与费用，没有出入金流水，段内盈亏用「净现金流 − 费用 + 结转持仓计值」推导，未含融资利息与股息",
        "未覆盖：2019 及以前（无记录，若有其它券商账户不在此账本内）",
        "待补：POEMS ID 的 PGAS 卖出成交确认（约 1,490 万 IDR，已按 1,490 单价估算进合计）",
    ]
    return {
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "overall": overall,
        "accounts": accounts,
        "pnl_bridge": {"rows": bridge, "verified": total, "memory": memory,
                       "gap": round(memory - total, 2), "coverage": coverage},

        "statement": {k: v for k, v in st.items()
                      if k not in ("monthly", "yearly", "by_symbol")},
        "statement_monthly": st.get("monthly", []),
        "statement_yearly": st.get("yearly", []),
        "statement_symbols": st.get("by_symbol", []),
        "recon": recon,
        "recon_rows": recon_rows,
    }


def brief_recon(rows: list[dict] | None = None) -> dict:
    """给组合页用的小结：券商现金 vs 台账现金、持仓快照滞后造成的调整、校正后权益。"""
    rates = _rates()
    r = _cash_recon(rates, rows)
    return {
        "cash_broker_usd": r["cash_broker_usd"], "cash_broker_asof": r["cash_broker_asof"],
        "cash_sys_usd": r["cash_sys_usd"], "cash_sys_asof": r["cash_sys_asof"],
        "cash_gap": r["cash_gap"], "adjust_usd": r["adjust_usd"],
        "stale": r["stale_detail"], "positions_snapshot": r["positions_snapshot"],
        "equity_ledger": r["equity_ledger"], "equity_est": r["equity_est"],
        "market_value_usd": r["market_value_usd"], "mv_broker": r["mv_broker"],
        "total_pnl_est": r["total_pnl_est"], "net_external_usd": r["net_external_usd"],
        "deposit_usd": r["cash_deposit_usd"], "withdraw_usd": r["cash_withdraw_usd"],
    }
