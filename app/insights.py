# -*- coding: utf-8 -*-
"""交易分析：时间线阶段、交易偏好画像、风险清单、持仓标注。"""
from collections import defaultdict
from datetime import date

from . import db, market

LEVERED = {"SQQQ", "SOXL", "SOXS", "TQQQ", "SQQQ", "TSLL", "LABU", "TNA", "FAS", "TZA",
           "YINN", "YANG", "SPXL", "SPXU", "UVXY", "SVXY", "NUGT", "DUST"}
DATED_SOURCES = ("poems_ledger",)


def _fx():
    return market.fx_rates()


def _usd(v, ccy, rates):
    if v is None:
        return 0.0
    return float(v) * float(rates.get(ccy or "USD", 1.0))


def _month(s: str) -> str:
    return (s or "")[:7]


def _symbol_key(sym: str) -> str:
    """01519 / 01519.HK 都归到 01519;  0700.HK → 0700"""
    s = (sym or "").upper()
    for suf in (".HK", ".JK", ".SS", ".SZ"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s


def _market_of(sym: str) -> str:
    s = (sym or "").upper()
    if s.endswith(".HK"):
        return "HK"
    if s.endswith(".JK"):
        return "ID"
    if s.endswith((".SS", ".SZ")):
        return "CN"
    return "US"


def _dated_items() -> list[dict]:
    """把「已平仓台账」与「FUTU 平仓事件」统一成一条时间线：date / symbol / pnl / hold。"""
    rates = _fx()
    items = []
    rejected_rows = db.query("SELECT id FROM closed_trades WHERE quality='reject'")
    rejected = {r["id"] for r in rejected_rows}
    for r in db.query("SELECT * FROM closed_trades WHERE close_date IS NOT NULL AND close_date<>''"):
        if r["id"] in rejected:
            continue
        items.append({
            "kind": "closed", "date": r["close_date"], "account": r["account_id"],
            "symbol": _symbol_key(r["symbol"]), "market": r.get("market") or "US",
            "ccy": r["ccy"], "pnl_usd": _usd(r["pnl"], r["ccy"], rates),
            "hold": r.get("holding_days"), "qty": r.get("qty"),
            "notional_usd": abs(_usd((r["qty"] or 0) * (r["buy_price"] or 0), r["ccy"], rates)),
        })
    for r in db.query("SELECT * FROM trade_events WHERE quality<>'reject'"):
        items.append({
            "kind": "event", "date": (r["occurred_at"] or "")[:10], "account": r["account_id"],
            "symbol": _symbol_key(r["symbol"]), "market": _market_of(r["symbol"]),
            "ccy": r["ccy"], "pnl_usd": _usd(r["pnl"], r["ccy"], rates),
            "hold": None, "qty": r.get("qty"),
            "notional_usd": abs(_usd((r["qty"] or 0) * (r["price"] or 0), r["ccy"], rates)),
            "side": r.get("side"), "event_type": r.get("event_type"),
        })
    items = [i for i in items if i["date"]]
    items.sort(key=lambda i: i["date"])
    return items


def _phase_stats(items: list[dict], rates) -> dict:
    pnl = [i["pnl_usd"] for i in items]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    holds = [i["hold"] for i in items if i["kind"] == "closed" and i["hold"] is not None]
    by_sym = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for i in items:
        key = i["symbol"] or "POEMS·无代码"
        by_sym[key]["n"] += 1
        by_sym[key]["pnl"] += i["pnl_usd"]
    top = sorted(by_sym.items(), key=lambda kv: -abs(kv[1]["pnl"]))[:3]
    return {
        "n": len(items),
        "pnl_usd": round(sum(pnl), 2),
        "win_rate": round(len(wins) / len(pnl), 4) if pnl else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "payoff": round((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 2) if wins and losses else None,
        "avg_hold": round(sum(holds) / len(holds), 1) if holds else None,
        "long_share": round(sum(1 for i in items if i.get("event_type") == "close_long") /
                            max(1, sum(1 for i in items if i.get("event_type") in ("close_long", "close_short"))), 4)
        if any(i.get("event_type") for i in items) else None,
        "top_symbols": [{"symbol": k, "n": v["n"], "pnl_usd": round(v["pnl"], 2)} for k, v in top],
        "levered_share": round(sum(1 for i in items if i["symbol"] in LEVERED) / len(items), 4) if items else 0,
    }


def _style(stats: dict, months: int) -> str:
    n, hold = stats["n"], stats["avg_hold"]
    per_month = n / max(1, months)
    if hold is not None and hold <= 3:
        return "超短线（几乎日内）"
    if hold is not None and hold <= 14:
        return "短线波段"
    if hold is not None and hold <= 60:
        return "中线波段"
    if hold is not None:
        return "中长线持有"
    if per_month >= 8:
        return "高频短线（无持仓期记录）"
    return "波段（无持仓期记录）"


def timeline() -> dict:
    items = _dated_items()
    if not items:
        return {"points": [], "phases": []}
    months = sorted({_month(i["date"]) for i in items})
    rates = _fx()
    # 按月聚合（跨账户合计，USD）
    monthly = defaultdict(lambda: {"pnl_usd": 0.0, "n": 0, "win": 0, "loss": 0})
    for i in items:
        m = monthly[_month(i["date"])]
        m["pnl_usd"] += i["pnl_usd"]
        m["n"] += 1
        if i["pnl_usd"] > 0:
            m["win"] += 1
        elif i["pnl_usd"] < 0:
            m["loss"] += 1
    points = [{"month": k, "pnl_usd": round(v["pnl_usd"], 2), "n": v["n"],
               "win": v["win"], "loss": v["loss"]} for k, v in sorted(monthly.items())]

    # 阶段切分：连续月份间隔 > 6 个月就视为新阶段（POEMS 2022-01 → FUTU 2024-08 就是两个阶段）
    def mi(m):
        y, mo = m.split("-")
        return int(y) * 12 + int(mo) + 1

    phases, cur = [], [months[0]]
    for prev, nxt in zip(months, months[1:]):
        if mi(nxt) - mi(prev) > 6:
            phases.append((cur[0], cur[-1]))
            cur = [nxt]
        else:
            cur.append(nxt)
    phases.append((cur[0], cur[-1]))

    out = []
    for a, b in phases:
        chunk = [i for i in items if a <= _month(i["date"]) <= b]
        stats = _phase_stats(chunk, rates)
        months_n = mi(b) - mi(a) + 1
        accounts = sorted({i["account"] for i in chunk})
        out.append({
            "start": a, "end": b, "months": months_n,
            "accounts": accounts, "style": _style(stats, months_n),
            **stats,
        })
    return {"points": points, "phases": out, "items": len(items)}


def profile() -> dict:
    items = _dated_items()
    rates = _fx()
    stats = _phase_stats(items, rates) if items else {}
    by_sym = defaultdict(lambda: {"n": 0, "pnl": 0.0, "win": 0})
    by_mkt = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for i in items:
        by_sym[i["symbol"] or "POEMS·无代码"]["n"] += 1
        by_sym[i["symbol"] or "POEMS·无代码"]["pnl"] += i["pnl_usd"]
        by_sym[i["symbol"] or "POEMS·无代码"]["win"] += 1 if i["pnl_usd"] > 0 else 0
        by_mkt[i["market"]]["n"] += 1
        by_mkt[i["market"]]["pnl"] += i["pnl_usd"]
    sym_rows = [{"symbol": k, "n": v["n"], "pnl_usd": round(v["pnl"], 2),
                 "win_rate": round(v["win"] / v["n"], 4), "levered": k in LEVERED}
                for k, v in sorted(by_sym.items(), key=lambda kv: -kv[1]["n"])]
    top_loss = sorted([s for s in sym_rows if s["pnl_usd"] < 0], key=lambda s: s["pnl_usd"])[:3]
    total_loss = sum(-s["pnl_usd"] for s in sym_rows if s["pnl_usd"] < 0) or 1
    lev = [s for s in sym_rows if s["levered"]]
    months = 12 * 3  # 2020-03 → 2026-09 里实际有交易的月份由 timeline 提供
    tl = timeline()
    active_months = len(tl["points"]) or 1
    return {
        "n_trades": len(items),
        "per_month": round(len(items) / active_months, 2),
        "active_months": active_months,
        "win_rate": stats.get("win_rate"),
        "payoff": stats.get("payoff"),
        "avg_hold_days": stats.get("avg_hold"),
        "long_share": stats.get("long_share"),
        "levered_share": round(sum(s["n"] for s in lev) / max(1, len(items)), 4),
        "levered_pnl_usd": round(sum(s["pnl_usd"] for s in lev), 2),
        "markets": [{"code": k, "n": v["n"], "pnl_usd": round(v["pnl"], 2)}
                    for k, v in sorted(by_mkt.items(), key=lambda kv: -kv[1]["n"])],
        "top_symbols": sym_rows[:5],
        "concentration": round(next((s["n"] for s in sym_rows if s["symbol"] != "POEMS·无代码"), 0)
                               / max(1, len(items)), 4),
        "top_loss_symbols": top_loss,
        "top_loss_share": round(sum(-s["pnl_usd"] for s in top_loss) / total_loss, 4) if top_loss else None,
        "conclusion": _conclude(items, stats, by_sym, by_mkt, lev),
    }


def _conclude(items, stats, by_sym, by_mkt, lev) -> list[str]:
    out = []
    if not items:
        return out
    hold = stats.get("avg_hold")
    if hold is not None:
        out.append(f"已平仓笔平均持有 {hold:.0f} 天，" +
                   ("偏中线波段，靠时间换空间。" if hold > 30 else "偏短线，靠节奏换收益。"))
    if stats.get("win_rate") is not None:
        wr, pay = stats["win_rate"], stats.get("payoff") or 0
        out.append(f"整体胜率 {wr * 100:.0f}%、赔率 {pay:.2f}（平均盈利/平均亏损）："
                   + ("赢的次数多，但每次赢得少、亏得多，胜率抵不掉赔率。" if pay < 1 and wr > 0.5
                      else "胜率低但赔率高，靠少数大赢覆盖多数小亏。" if pay >= 1.5 and wr < 0.5
                      else "胜率与赔率大致平衡。"))
    if lev:
        lev_n = sum(s["n"] for s in lev)
        out.append(f"杠杆 ETF 占交易笔数 {lev_n / len(items) * 100:.0f}%（{', '.join(s['symbol'] for s in lev[:3])}），"
                   f"这部分合计 {sum(s['pnl_usd'] for s in lev):,.2f} USD。")
    named = [(k, v) for k, v in by_sym.items() if k != "POEMS·无代码"]
    top = max(named or list(by_sym.items()), key=lambda kv: kv[1]["n"])
    out.append(f"交易最集中在 {top[0]}（{top[1]['n']} 笔，{top[1]['pnl']:,.2f} USD）。")
    mkt = max(by_mkt.items(), key=lambda kv: kv[1]["n"])
    out.append(f"市场以 {mkt[0]} 为主（{mkt[1]['n']} 笔，{mkt[1]['pnl']:,.2f} USD）。")
    return out


def risks() -> list[dict]:
    items = _dated_items()
    rates = _fx()
    out = []
    by_sym = defaultdict(lambda: {"n": 0, "pnl": 0.0, "win": 0})
    for i in items:
        k = i["symbol"] or "POEMS·无代码"
        by_sym[k]["n"] += 1
        by_sym[k]["pnl"] += i["pnl_usd"]
        by_sym[k]["win"] += 1 if i["pnl_usd"] > 0 else 0
    total_pnl = sum(v["pnl"] for v in by_sym.values())
    lev = [(k, v) for k, v in by_sym.items() if k in LEVERED]
    if lev:
        lev_pnl = sum(v["pnl"] for _, v in lev)
        lev_n = sum(v["n"] for _, v in lev)
        ratio = lev_pnl / total_pnl if total_pnl else 0
        share_txt = (f"占全部已实现盈亏的 {ratio * 100:.0f}%" if ratio < 1
                     else f"是全部已实现盈亏的 {ratio:.1f} 倍（绝对值）")
        out.append({"level": "danger", "title": "杠杆 ETF 是主要亏损来源",
                    "detail": f"{lev_n} 笔（{', '.join(k for k, _ in lev[:3])}）合计 {lev_pnl:,.2f} USD，"
                              f"{share_txt}。3x 产品有波动衰减，"
                              "不适合按「越跌越买、等反弹」的方式持有。",
                    "metric": round(lev_pnl, 2)})
    for k, v in sorted(by_sym.items(), key=lambda kv: kv[1]["pnl"])[:2]:
        if v["pnl"] < 0 and v["n"] >= 5:
            out.append({"level": "danger", "title": f"{k} 单票失血",
                        "detail": f"{v['n']} 笔、胜率 {v['win'] / v['n'] * 100:.0f}%、合计 {v['pnl']:,.2f} USD；"
                                  "笔数越多亏越多，说明这个标的不适合当前手法。",
                        "metric": round(v["pnl"], 2)})
    if items:
        win = sum(1 for i in items if i["pnl_usd"] > 0)
        wr = win / len(items)
        wins = [i["pnl_usd"] for i in items if i["pnl_usd"] > 0]
        losses = [i["pnl_usd"] for i in items if i["pnl_usd"] < 0]
        pay = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses else None
        if pay is not None and pay < 1:
            out.append({"level": "danger", "title": "盈亏比小于 1",
                        "detail": f"平均盈利 {sum(wins) / len(wins):,.0f} USD、平均亏损 "
                                  f"{abs(sum(losses) / len(losses)):,.0f} USD，赔率 {pay:.2f}；"
                                  f"即使胜率 {wr * 100:.0f}%，长期期望仍为负。要么把亏损单砍小，要么让盈利单跑得更远。",
                        "metric": round(pay, 2)})
        elif wr < 0.5:
            out.append({"level": "warn", "title": "胜率偏低",
                        "detail": f"总胜率 {wr * 100:.0f}%（{win}/{len(items)}），靠赔率覆盖。",
                        "metric": round(wr, 4)})

    # 当前持仓风险
    from . import portfolio
    ov = portfolio.overview("USD", "all")
    rows = portfolio.position_rows(with_quotes=True)
    exp = sum(abs(r["value_usd"] or 0) for r in rows) or 1
    for r in rows:
        w = (abs(r["value_usd"] or 0) / exp) if r["value_usd"] is not None else 0
        if w >= 0.5:
            out.append({"level": "danger", "title": f"{r['symbol']} 单票集中度 {w * 100:.0f}%",
                        "detail": f"单一标的占组合敞口 {w * 100:.0f}%，超过 50% 的风控上限；"
                                  f"当前浮动 {r['pnl_usd']:,.2f} USD。",
                        "metric": round(w, 4)})
        elif w >= 0.3:
            out.append({"level": "warn", "title": f"{r['symbol']} 集中度 {w * 100:.0f}%",
                        "detail": "单一标的占敞口三成以上，单票波动会主导组合。", "metric": round(w, 4)})
        if r["side"] == "SHORT" and w >= 0.1:
            out.append({"level": "warn", "title": f"{r['symbol']} 空头敞口 {w * 100:.0f}%",
                        "detail": "空头占用保证金、无上限亏损风险，需设定回补条件。", "metric": round(w, 4)})
        if r["symbol"] in LEVERED and w >= 0.1:
            out.append({"level": "warn", "title": f"{r['symbol']} 杠杆 ETF 持仓",
                        "detail": "杠杆 ETF 每日重置，长期持有时损耗与标的走势不成比例。",
                        "metric": round(w, 4)})
    if ov["total_cash_usd"] < 0:
        out.append({"level": "danger", "title": "现金为负，已在用保证金",
                    "detail": f"合并现金 {ov['total_cash_usd']:,.2f} USD，杠杆 {ov['leverage']}x；"
                              "下跌时会被追缴保证金，先补现金或减仓。",
                    "metric": round(ov["total_cash_usd"], 2)})
    st = (db.get_meta("st_numbers", {}) or {}).get("components_usd", {})
    fees = st.get("fees_interest")
    if fees:
        base = abs(sum(i["pnl_usd"] for i in items)) or 1
        out.append({"level": "info", "title": "费用侵蚀",
                    "detail": f"台账里费用+利息合计 {fees:,.2f} USD，约为全部已实现盈亏绝对值的 "
                              f"{abs(fees) / base * 100:.0f}%。高频+杠杆的摩擦成本要单独记账看。",
                    "metric": fees})
    return out


def actions() -> list[dict]:
    """把诊断翻成可执行的下一步，每条都带具体数字。"""
    from . import portfolio
    ov = portfolio.overview("USD", "all")
    rows = portfolio.position_rows(with_quotes=True)
    exp = sum(abs(r["value_usd"] or 0) for r in rows) or 1
    out = []
    top = max(rows, key=lambda r: abs(r["value_usd"] or 0), default=None)
    if top and abs(top["value_usd"] or 0) / exp >= 0.5:
        keep = 0.3 * exp
        cut = abs(top["value_usd"]) - keep
        px = top.get("price") or 0
        shares = int(cut / px) if px else 0
        out.append({"step": "减集中度",
                    "text": f"{top['symbol']} 占敞口 {abs(top['value_usd']) / exp * 100:.0f}%，"
                            f"先减 {cut:,.0f} USD（约 {shares} 股）压到 30% 以内"})
    if ov["total_cash_usd"] < 0:
        out.append({"step": "补现金",
                    "text": f"现金 {ov['total_cash_usd']:,.0f} USD 为负，需补足或等额减仓，"
                            f"把杠杆从 {ov['leverage']}x 降到 1.0x 以下"})
    for r in rows:
        if r["side"] == "SHORT":
            out.append({"step": "管空头",
                        "text": f"{r['symbol']} 空头 {abs(r['value_usd'] or 0):,.0f} USD，"
                                "开仓前写下回补价与止损价，空头没有上限亏损"})
    items = _dated_items()
    wins = [i["pnl_usd"] for i in items if i["pnl_usd"] > 0]
    losses = [i["pnl_usd"] for i in items if i["pnl_usd"] < 0]
    if wins and losses:
        avg_w = sum(wins) / len(wins)
        avg_l = abs(sum(losses) / len(losses))
        if avg_w < avg_l:
            out.append({"step": "改赔率",
                        "text": f"平均盈利 {avg_w:,.0f} < 平均亏损 {avg_l:,.0f}；"
                                f"把单笔止损压到 {avg_l / 2:,.0f} USD 以内，"
                                "或让盈利单拿到目标价再走"})
    lev = [i for i in items if i["symbol"] in LEVERED]
    if lev:
        out.append({"step": "限杠杆 ETF",
                    "text": f"历史 {len(lev)} 笔杠杆 ETF 合计 {sum(i['pnl_usd'] for i in lev):,.0f} USD；"
                            "新开仓只做日内，不越跌越买"})
    return out


def position_advice(rows: list[dict]) -> dict:
    """给当前持仓逐行打标签：集中度、杠杆 ETF、空头、浮亏比例、仓位过小。"""
    exp = sum(abs(r["value_usd"] or 0) for r in rows) or 1
    out = {}
    for r in rows:
        tags = []
        w = abs(r["value_usd"] or 0) / exp
        if r["side"] == "SHORT":
            tags.append({"level": "warn", "text": "空头 · 占保证金"})
        if r["symbol"] in LEVERED:
            tags.append({"level": "warn", "text": "3x 杠杆 ETF · 有衰减"})
        if w >= 0.5:
            tags.append({"level": "danger", "text": f"集中度 {w * 100:.0f}% ≥50%"})
        elif w >= 0.3:
            tags.append({"level": "warn", "text": f"集中度 {w * 100:.0f}% ≥30%"})
        if abs(r["value_usd"] or 0) < 100:
            tags.append({"level": "info", "text": "仓位过小"})
        pnl, val = r["pnl_usd"], r["value_usd"]
        if pnl is not None and val not in (None, 0) and abs(pnl / val) >= 0.2 and pnl < 0:
            tags.append({"level": "danger", "text": f"浮亏 {abs(pnl / val) * 100:.0f}%"})
        out[r["id"]] = tags
    return out
