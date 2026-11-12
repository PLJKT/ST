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

    # 只统计富途本账户事件：此前未加账户过滤，把 SG/ID 的平仓批次也算进了「券商 1599 成交」分母
    ev_count = db.query("SELECT COUNT(*) AS n FROM trade_events "
                        "WHERE account_id=? AND COALESCE(quality,'ok')<>'reject'",
                        ("futu_main",))[0]["n"]

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
         "level": "ok",
         "note": "两种口径不可相减：现金口径含费用、利息、股息与在手持仓浮盈变动，台账只算已平仓 FIFO，差额属口径差、不是错账。"},
        {"item": "券商累计总盈亏", "broker": f"${recon['total_pnl_est']:,.2f}（含浮动）",
         "ledger": f"${(accounts[0]['realized_usd'] or 0):,.2f}（已实现）",
         "diff": f"{recon['total_pnl_est'] - (accounts[0]['realized_usd'] or 0):+,.2f}",
         "level": "ok",
         "note": f"券商口径 = 期末现金 ${recon['cash_broker_usd']:,.2f} + 持仓 ${recon['mv_broker']:,.2f} − 净投入 ${recon['net_external_usd']:,.2f}；"
                 "与台账已实现的差额含浮动盈亏、费用、利息与股息，同属口径差、不是错账。"},
        {"item": "9-24 后成交未扣仓", "broker": f"${recon['stale_flow_usd']:+,.2f} 净卖出",
         "ledger": f"持仓快照 {recon['positions_snapshot'][:10]}",
         "diff": f"{recon['adjust_usd']:+,.2f}",
         "level": "danger" if abs(recon["adjust_usd"]) > 1000 else "ok",
         "note": "持仓已按 2026-11-09 券商快照校正，此处应恒为 0；若再次偏离说明又有成交未回填。"},
    ]
    for r in recon_rows:                     # 只属于富途 1599：标注账户，避免被当成全站口径
        r["acct"] = "富途"

    # 全历史盈亏：**以账户为单位**结算（一个账户一行），账户内的时期只作子行。
    # 旧版按「段」平铺，富途被拆成 6519/9461 与 1599 两条并列，SG 旧户/新户又被 ID 隔开，
    # 读起来像四个账户混在一起 —— 这正是用户反复指出的「各个账号数据混在一起」。
    futu_acc = next((a for a in accounts if a["id"] == "futu_main"), {})
    id_acc = next((a for a in accounts if a["id"] == "poems_id"), {})
    sg_acc = next((a for a in accounts if a["id"] == "poems_sg"), {})
    stn = db.get_meta("st_numbers") or {}
    life = stn.get("life_recon") or {}
    futu_total = recon["total_pnl_est"]
    # 6519 / 9461 的 2022-01→2024-08 段（成交+费用口径），与 1599 段分开列
    fh = futu_hist.summary()
    carried = float(fh.get("carried_usd") or 0.0)
    futu_old = float(fh.get("segment_usd") or 0.0)
    # 1599 段的起点不是「零」：迁移进来的 01519 8,000 股与 AVGO −10 股空头
    # 是资产而非入金，必须计入投入，否则 1599 段盈亏被高估 carried
    # 口径选择（2026-11-11 用户定）：全历史按「亏损更大」的券商资金口径统计。
    #   券商资金口径 = 期末现金 − 净外部投入（不含在手持仓）
    #   现值口径     = 期末现金 + 持仓 − 净外部投入 − 随迁证券
    # 两者差 = 在手持仓 − 随迁证券；取更负的那个，现值口径只作脚注与交叉校验。
    futu_new_eq = round(futu_total - carried, 2)
    futu_new = round(futu_total - float(recon["mv_broker"] or 0), 2)
    futu_new_gap = round(futu_new - futu_new_eq, 2)
    futu_life = round(futu_old + futu_new, 2)
    futu_life_eq = round(futu_old + futu_new_eq, 2)
    futu_bank = float((st.get("deposit") or {}).get("usd") or 0) + float((st.get("withdraw") or {}).get("usd") or 0)
    mig_net = round(float(recon["net_external_usd"] or 0) - futu_bank, 2)
    sg_start = float(life.get("sg_legacy_start") or 23110.0)
    sg_end = float(life.get("sg_legacy_end") or 29772.84)
    sg_unreal = float(life.get("sg_open_unreal") or -4767.29)
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
    sg_life = round(sg_r1 + sg_r2 + sg_new_pnl, 2)          # SG 本账户合计
    idr = float(life.get("idr_usd") or 18106.95)
    pgas_usd = round(float(life.get("id_pgas_idr") or 14900000.0) / idr, 2)
    id_pnl = round((id_acc.get("equity_usd") or 0) - (id_acc.get("net_external_usd") or 0) + pgas_usd, 2)
    total = round(futu_life + sg_life + id_pnl, 2)          # 三账户合计（亏损更大口径）
    total_eq = round(futu_life_eq + sg_life + id_pnl, 2)    # 三账户合计（现值口径）
    memory = float(life.get("memory_usd") or -50000.0)
    old = life.get("old_dashboard") or {}

    # 账户分组：账户合计行 = 账户内各段之和（段只用于说明「钱是怎么亏的」）
    futu_segs = [
        {"k": "6519 / 9461 · 2022-01→2024-08（成交 + 费用口径）", "v": futu_old, "level": fh.get("level") or "warn",
         "n": (f"美股 {fh['us']['rows']} 笔成交额 {fh['us']['turnover']:,.2f} / 费用 {fh['us']['fees']:,.2f}；"
               f"港股 {fh['hk']['rows']} 笔成交额 {fh['hk']['turnover']:,.2f} HKD / 费用 {fh['hk']['fees']:,.2f} HKD；"
               f"迁移日结转的 01519 8,000 股与 AVGO −10 股按次日成交价计 {carried:,.2f}。"
               f"AMAT 394 股（成本 {fh.get('amat', {}).get('cost_usd', 0):,.2f}）按「已转出本账本」处理，原值移出，不计亏损也不计资产；"
               f"6519 出入金 {((fh.get('cashflow_us') or {}).get('rows') or 0)} 笔（净入金 "
               f"{(fh.get('cashflow_us') or {}).get('net_in') or 0:,.2f}，与成交表推定期末现金 "
               f"{(fh.get('cashflow_us') or {}).get('implied_cash_end') or 0:,.2f} 自洽）")},
        {"k": "1599 · 2024-08→2026-11（券商资金口径，不含在手持仓）", "v": futu_new, "level": "ok",
         "n": (f"期末现金 {recon['cash_broker_usd']:,.2f} − 净投入 {float(recon['net_external_usd'] or 0):,.2f}"
               f"（银行存入 {float((st.get('deposit') or {}).get('usd') or 0):,.2f} / 取出 "
               f"{float((st.get('withdraw') or {}).get('usd') or 0):,.2f} / 同日内部迁移 {mig_net:,.2f}）；"
               f"在手持仓 {float(recon['mv_broker'] or 0):,.2f} 未计入，现值口径 {futu_new_eq:,.2f}（差 {futu_new_gap:+,.2f}）。"
               "迁移是同一富途号内部子账户升级，不是外部入金、也不是从 POEMS 转入")},
    ]
    sg_segs = [
        {"k": "旧户 · 2020-02→2022-01（已实现）", "v": sg_r1, "level": "ok",
         "n": f"台账初始入金 {sg_start:,.2f}，平仓后权益 {sg_end:,.2f}（146 笔美股 + 12 笔港股，胜率 78%）"},
        {"k": "旧户 · 2022-01 未平仓浮亏", "v": sg_r2, "level": "ok",
         "n": "2022-01-27 还持有 6 只（含 100 股亏损仓），按当日价计的账面浮亏，清仓时兑现；旧户结清后退出本账本"},
        {"k": "新户 · 2025-11→2026-11（资金口径）", "v": sg_new_pnl, "level": "warn",
         "n": (f"首见余额 {sg_new_start:,.2f} + 买入成本 {sg_cost:,.2f}（SQQQ 165 股 + 货基 250.9555 份）"
               f"− 现值 {float(sg_acc.get('equity_usd') or 0):,.2f}；2025-12→2026-09 无月结单，投入按持仓成本反推")},
    ]
    id_segs = [
        {"k": "2026-03→2026-11（资金口径）", "v": id_pnl, "level": "ok",
         "n": (f"现值 {float(id_acc.get('equity_usd') or 0):,.2f} + PGAS 回款 {pgas_usd:,.2f} − 净投入 "
               f"{float(id_acc.get('net_external_usd') or 0):,.2f}（IDR 折 USD，汇率 {idr:,.2f}）")},
    ]
    groups = [
        {"id": "futu_main", "name": "富途（主账号）", "v": futu_life, "v_eq": futu_life_eq,
         "level": fh.get("level") or "warn",
         "basis": "6519/9461 成交+费用 + 1599 券商资金口径（不含在手持仓）",
         "headline_hint": "全部时期、全部币种（美股 / 港股），一个富途号",
         "segments": futu_segs,
         "note": (f"6519（美股融资户）+ 9461（港股融资户）+ 1599（保证金综合户）同属一个富途号，"
                  f"2024-08-03 只是内部升级。本账户合计 {futu_life:,.2f} = 段一 {futu_old:,.2f} + 段二 {futu_new:,.2f}。"
                  f"1599 段自校验：现金 {recon['cash_broker_usd']:,.2f} + 持仓 {float(recon['mv_broker'] or 0):,.2f} − 净投入 "
                  f"{float(recon['net_external_usd'] or 0):,.2f} − 随迁证券 {carried:,.2f} = {futu_new_eq:,.2f}（现值口径）。")},
        {"id": "poems_sg", "name": "POEMS SG（次账号）", "v": sg_life, "v_eq": sg_life,
         "level": "warn",
         "basis": "旧户台账（2020-02→2022-01） + 新户资金口径（2025-11→2026-11）",
         "headline_hint": "只含 POEMS Singapore，不含任何其他市场",
         "segments": sg_segs,
         "note": (f"旧户 {sg_r1:+,.2f} + 浮亏 {sg_r2:+,.2f} + 新户 {sg_new_pnl:+,.2f} = {sg_life:+,.2f}。"
                  f"两段同属 POEMS Singapore（旧户结清后退出本账本，新户首见余额 {sg_new_start:,.2f}），故合并为一个账户。")},
        {"id": "poems_id", "name": "POEMS ID（本地账号）", "v": id_pnl, "v_eq": id_pnl,
         "level": "ok",
         "basis": "对账单净投入 vs 现值（IDR 折 USD）",
         "headline_hint": "只含 POEMS Indonesia",
         "segments": id_segs,
         "note": "PGAS 卖出成交确认仍待补（约 1,490 万 IDR 已按 1,490 单价估算，计入本值）；补单后若单价不同，只影响本账户个位美元。"},
    ]
    bridge_rows = []
    for g in groups:
        bridge_rows.append({"k": g["name"], "v": g["v"], "level": g["level"], "n": g["basis"], "grp": True})
        for s in g["segments"]:
            bridge_rows.append({"k": "· " + s["k"], "v": s["v"], "level": s["level"], "n": s["n"], "grp": False})
    bridge_rows.append({
        "k": "三账户合计", "v": total, "level": "ref", "grp": True,
        "n": (f"富途 {futu_life:,.2f} + SG {sg_life:,.2f} + ID {id_pnl:,.2f} = {total:,.2f}；三个账户各自独立结算、"
              f"账户之间从未互转资金或持仓，入金全部来自个人储蓄。富途取券商资金口径（不含在手持仓）；"
              f"若三账户统一按现值口径，合计 {total_eq:,.2f}，差 {round(total - total_eq, 2):+,.2f}"
              f"（= 1599 在手持仓 {float(recon['mv_broker'] or 0):,.2f} − 随迁证券 {carried:,.2f}）。")},
    )
    bridge_rows.append({
        "k": "你的记忆 · 约 −5 万（全部账户已实现 + 浮亏的总体感觉）", "v": memory, "level": "danger", "grp": True,
        "n": (f"与三账户合计差 {round(memory - total, 2):+,.2f}，与现值口径合计差 {round(memory - total_eq, 2):+,.2f}。"
              "只作统计方向、不作凭证：账面能逐笔对上的是上表各账户，差额来自记忆本身的粗估，不是漏账。"
              f"早期那条「回本缺口 {float(old.get('gap') or 49293.65):,.2f}」把多账户本金减单账户权益，口径作废。")},
    )
    bridge = {
        "groups": groups,
        "rows": bridge_rows,
        "verified": total,
        "verified_eq": total_eq,
        "memory": memory,
        "gap": round(memory - total, 2),
        "gap_eq": round(memory - total_eq, 2),
    }
    coverage = [
        "口径：三个账户各自独立结算，从未互转资金或持仓，入金全部来自个人储蓄。富途 2024-08-03 的「资产迁移」是同一富途号内部的子账户升级（6519/9461 → 1599），不是从 POEMS 转入，也不是外部入金",
        "富途 · 6519（美股）：2022-01-13→2024-08-02 全部 4,200 笔成交、3,803 条订单费用（dataset：data/processed/futu_hist_2022_2024.json）",
        "富途 · 9461（港股）：2022-01-13→2024-08-01 全部 362 笔成交、363 条订单费用；仍无出入金导出，合并段盈亏为推导口径",
        "富途 · 6519 出入金：2021-04-19→2024-06-04 共 24 笔（存入 +93,300.00 / 取出 −51,800.02 / 净入金 +41,499.98），与成交表推定期末现金 4,794.85 自洽",
        "富途 · 1599（保证金综合户）：2024-08-03→2026-11-06 全部 555 笔成交与 1,115 行资金流水（含迁移、换汇）",
        "富途 · AMAT 394 股（成本 46,843.49）：自 2023-03-03 起无成交、1599 侧 0 次出现，迁移只有现金没有证券 → 按「已转出本账本」处理，原值移出",
        "POEMS SG · 旧户：2020-02-24→2022-01-20 共 158 笔台账成交 + 109 条日内批次；期初 23,110、期末权益 29,772.84",
        "POEMS SG · 旧户的 109 条日内批次来自 Excel 日内块：无日期、无代码，计入笔数与胜率，但不参与持有天数与月度分布（台账自述 581.78 与明细 1,125.46 不一致，以明细为准）",
        "POEMS SG · 新户：2025-11 与 2026-10 两期月结单 + 2025 年度股息税单；2025-12→2026-09 无月结单，新户投入按持仓成本反推",
        "POEMS ID · 2026-03-04→2026-11-06：62 份单据、56 笔成交、29 个平仓批次，净投入 6,511.19",
        "口径：累计盈亏取「亏损更大」的券商资金口径 —— 富途 1599 段按 期末现金 − 净外部投入 计，不含在手持仓 8,994.85；比现值口径多计 2,758.05 亏损",
        "未覆盖：2019 及以前（无记录）；待补：POEMS ID 的 PGAS 卖出成交确认（约 1,490 万 IDR，已按 1,490 单价估算）",
    ]
    # 账户卡：卡面主数 = 该账户全历史合计（不再用「某一段的已实现」当账户数）
    acc_by_id = {g["id"]: g for g in groups}
    for a in accounts:
        g = acc_by_id.get(a["id"])
        if g:
            a["lifetime_usd"] = g["v"]
            a["lifetime_eq_usd"] = g["v_eq"]
            a["lifetime_basis"] = g["basis"]
            a["segments"] = g["segments"]
            a["account_note"] = g["note"]
            a["level"] = g["level"]
    overall.update({"lifetime_usd": total, "lifetime_eq_usd": total_eq,
                    "memory_usd": memory, "memory_gap": round(memory - total, 2)})
    return {
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "overall": overall,
        "accounts": accounts,
        "pnl_bridge": bridge,

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
