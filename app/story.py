"""交易行为分阶段画像：按「交易时间 / 频率 / 交易量 / 结果」把全部历史切成若干阶段。

数据来源全部在库内，换机可复算：
- 2022-01 → 2024-08 富途 6519（美股）/ 9461（港股）
  * 成交：data/processed/futu_hist_trades.csv
  * 费用：data/source/futu/历史订单费用-*.csv（按「下单时间」落月，只算已成交订单的合计费用）
  * 该段没有逐笔平仓台账，已实现只能用整段汇总（见 app/futu_hist.py），不能按月拆
- 2024-08 → 2026-11 富途 1599
  * 成交 / 费用：data/st.db 的 cash_flows（成交行；费用只取「买入费用 / 卖出费用」）
  * 平仓盈亏：data/st.db 的 trade_events（232 条，2024-08-05 → 2026-09-17）
- 2026-02 → 2026-11 POEMS ID：data/processed/poems.json（trades / closed_lots）
- 2020-03 → 2022-01 POEMS SG 旧户：data/st.db 的 closed_trades（只有平仓批次，没有逐笔成交）

口径：
- 金额一律折算 USD，汇率取 app.metrics 同一套（market.fx_rates()，取不到才用兜底值）。
- 「成交笔数」= 券商成交行条数（同一订单可能分多笔成交），不是订单数。
- 「单笔」= 成交额 / 成交笔数；SG 旧户没有逐笔成交，单笔按平仓批次的买入成本估算。
- 费用率 = 交易费用 / 成交额（bp，1bp = 万分之一），不含融资利息、融券费、股息税。
- 已实现只有在有逐笔平仓来源的阶段才给数，其余显式标注「整段口径，不可细分」，不猜。
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "st.db"
PROC = ROOT / "data" / "processed"
SRC_FUTU = ROOT / "data" / "source" / "futu"

FALLBACK_FX = {"USD": 1.0, "HKD": 0.1282, "SGD": 0.74, "IDR": 0.000064}

# 阶段边界按月度活跃度的自然断点（见 /api/story 的 monthly 序列）：
# - 2022-04 起 6519 美股开闸，单月 361/484 笔；
# - 2022-08 单月掉到 14 笔、只剩 2 个标的，是第一次「收手」；
# - 2023-06/07 连续两月只 3/8 笔，第二次长歇手；
# - 2024-07 单月 327 笔，是迁移（2024-08-03 子账户升级）前的清仓冲量；
# - 2024-09 起单月 0–12 笔，进入休眠；
# - 2025-04 单月 182 笔，是唯一一次回场；
# - 2026-02 起 POEMS ID 上线，进入小额分散。
PHASES = [
    ("p1", "起步试水", "新加坡小仓、按月稳定出手", "2020-03-01", "2022-01-31", ["poems_sg"]),
    ("p2", "开局爆发", "一个月换手一轮，大进大出", "2022-01-01", "2022-07-31", ["futu_main"]),
    ("p3", "节奏化高频", "笔数稳住、标的收窄到 10–25 个", "2022-08-01", "2023-05-31", ["futu_main"]),
    ("p4", "间歇性高频", "两次连续两月歇手，回来就放大", "2023-06-01", "2024-06-30", ["futu_main"]),
    ("p5", "迁移前冲量", "清仓换手，两月 539 笔", "2024-07-01", "2024-08-31", ["futu_main"]),
    ("p6", "升级后休眠", "只留底仓，月均 5 笔", "2024-09-01", "2025-03-31", ["futu_main"]),
    ("p7", "一次回场", "四月集中回场，随后再次离场", "2025-04-01", "2025-11-30", ["futu_main"]),
    ("p8", "转战印尼", "双账户小额并行，单笔明显变小", "2026-02-01", "2026-11-30", ["futu_main", "poems_id"]),
]

ACCOUNT_LABEL = {
    "futu_main": "富途",
    "poems_sg": "POEMS SG",
    "poems_id": "POEMS ID",
}
# 富途的子账户名（6519/9461、1599）只作括号里的子标识，不能被读成另一个账户
SUB_LABEL = {"富途 6519/9461": "6519/9461", "富途 1599": "1599"}


def _acct_name(label: str) -> str:
    """把「富途 6519/9461」这类子账户标签收敛回账户名。"""
    if label.startswith("富途"):
        return "富途"
    return label


def _acct_sub(label: str) -> str | None:
    if label in SUB_LABEL:
        return SUB_LABEL[label]
    return None


def _rates() -> dict:
    try:
        from . import metrics  # 与全站保持同一套汇率
        return metrics._rates()
    except Exception:
        return dict(FALLBACK_FX)


def _month(ts: str) -> str:
    return (ts or "")[:7]


def _sg_ledger_realized() -> float | None:
    """SG 旧户已实现取“分段对账”口径，与 /api/metrics 的 +6,662.84 同一个来源（meta.st_numbers）。"""
    try:
        from . import db
        life = (db.get_meta("st_numbers") or {}).get("life_recon") or {}
        start, end = life.get("sg_legacy_start"), life.get("sg_legacy_end")
        if start is not None and end is not None:
            return round(float(end) - float(start), 2)
    except Exception:
        return None


def _fee_num(s: str) -> float:
    s = (s or "").replace(",", "").replace('"', "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _read_futu_trades(fx: dict) -> list[dict]:
    """富途 6519/9461 逐笔成交（2022-01 → 2024-08）。"""
    path = PROC / "futu_hist_trades.csv"
    out: list[dict] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                amt = abs(float(r.get("金额") or 0))
            except ValueError:
                continue
            ccy = (r.get("币种") or "USD").strip()
            at = (r.get("成交时间") or "").strip()
            out.append({
                "at": at,
                "m": _month(at),
                "symbol": (r.get("代码") or "").strip(),
                "amt_usd": amt * fx.get(ccy, 1.0),
                "side": (r.get("方向") or "").strip(),
                "account": "futu_main",
                "label": "富途 6519/9461",
                "market": (r.get("市场") or "").strip(),
            })
    return out


def _read_futu_fees(fx: dict) -> list[dict]:
    """富途 6519/9461 订单费用（按订单时间落月）。"""
    out: list[dict] = []
    if not SRC_FUTU.exists():
        return out
    for path in sorted(SRC_FUTU.glob("历史订单费用-*.csv")):
        ccy = "HKD" if "港股" in path.name else "USD"
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                fee = _fee_num(r.get("合计费用") or "")
                if fee <= 0:
                    continue
                ts = (r.get("下单时间") or "").strip().replace("/", "-")
                if len(ts) < 7:
                    continue
                out.append({"m": ts[:7], "fee_usd": fee * fx.get(ccy, 1.0)})
    return out


def _read_1599(fx: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """富途 1599：成交行 / 交易费用行 / 平仓事件。"""
    trades: list[dict] = []
    fees: list[dict] = []
    closes: list[dict] = []
    if not DB.exists():
        return trades, fees, closes
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    for r in con.execute("select * from cash_flows where account_id='futu_main'"):
        t = r["trade_type"] or ""
        at = (r["occurred_at"] or "").strip()
        ccy = (r["ccy"] or "USD").strip()
        amt = abs(r["amount"] or 0) * fx.get(ccy, 1.0)
        if "成交" in t:
            trades.append({
                "at": at, "m": _month(at),
                "symbol": (r["symbol"] or "").strip(),
                "amt_usd": amt,
                "side": "买入" if "买入" in t else ("卖出" if "卖出" in t else ""),
                "account": "futu_main",
                "label": "富途 1599",
            })
        elif "买入费用" in t or "卖出费用" in t:
            fees.append({"m": _month(at), "fee_usd": amt})
    for r in con.execute("select * from trade_events where account_id='futu_main'"):
        at = (r["occurred_at"] or "").strip()
        if not at:
            continue
        closes.append({
            "m": _month(at), "pnl_usd": float(r["pnl"] or 0),
            "symbol": r["symbol"] or "", "at": at,
        })
    con.close()
    return trades, fees, closes


def _read_id(fx: dict) -> tuple[list[dict], list[dict]]:
    """POEMS ID：成交（trades）与平仓（closed_lots）。"""
    path = PROC / "poems.json"
    trades: list[dict] = []
    closes: list[dict] = []
    if not path.exists():
        return trades, closes
    j = json.loads(path.read_text(encoding="utf-8"))
    idr = fx.get("IDR", FALLBACK_FX["IDR"])
    for t in j.get("trades") or []:
        d = (t.get("date") or "").strip()
        trades.append({
            "at": d, "m": _month(d), "symbol": (t.get("code") or "").strip(),
            "amt_usd": abs(float(t.get("gross") or 0)) * idr,
            "side": "买入" if (t.get("side") or "").upper() == "B" else "卖出",
            "account": "poems_id",
            "label": "POEMS ID",
            "fee_usd": abs(float(t.get("fee") or 0)) * idr,
        })
    for c in j.get("closed_lots") or []:
        d = (c.get("close_date") or "").strip()
        closes.append({
            "m": _month(d), "pnl_usd": float(c.get("pnl") or 0) * idr,
            "symbol": c.get("symbol") or "", "at": d,
            "hold": c.get("holding_days"),
        })
    return trades, closes


def _read_sg_old(fx: dict) -> dict:
    """POEMS SG 旧户：只有平仓批次（无逐笔成交）。"""
    sgd = fx.get("SGD", FALLBACK_FX["SGD"])
    out = {"dated": [], "all": [], "pnl_usd": 0.0, "fee_usd": 0.0,
           "turnover_usd": 0.0, "symbols": set(), "wins": 0}
    if not DB.exists():
        return out
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    for r in con.execute("select * from closed_trades where account_id='poems_sg'"):
        pnl = float(r["pnl"] or 0) * sgd
        fee = abs(float(r["fee"] or 0)) * sgd
        qty = abs(float(r["qty"] or 0))
        cost = abs(float(r["avg_price"] or r["buy_price"] or 0)) * qty * sgd
        hold = r["holding_days"]
        try:
            hold = int(hold) if hold is not None else None
        except (TypeError, ValueError):
            hold = None
        sym = r["symbol"] or ""
        lot = {"m": _month(r["close_date"] or ""), "at": (r["close_date"] or ""),
               "pnl_usd": pnl, "hold": hold, "symbol": sym, "win": pnl > 0,
               "quality": (r["quality"] or ""), "turn_usd": cost, "fee_usd": fee}
        out["all"].append(lot)
        if lot["m"]:
            out["dated"].append(lot)
        out["pnl_usd"] += pnl
        out["fee_usd"] += fee
        out["turnover_usd"] += cost
        if sym:
            out["symbols"].add(sym)
        if pnl > 0:
            out["wins"] += 1
    con.close()
    return out


def _agg(trades: list[dict], fees: list[dict], closes: list[dict], *,
         turnover: float | None = None, fee_total: float | None = None,
         count: int | None = None) -> dict:
    n = len(trades)
    turn = turnover if turnover is not None else sum(t["amt_usd"] for t in trades)
    fee = fee_total if fee_total is not None else sum(f["fee_usd"] for f in fees)
    syms = Counter(t["symbol"] for t in trades if t.get("symbol"))
    top3 = (sum(v for _, v in syms.most_common(3)) / n) if n else None
    days = {t["at"][:10] for t in trades if t.get("at")}
    months = {t["m"] for t in trades if t.get("m")}
    hold_vals = [c["hold"] for c in closes if isinstance(c.get("hold"), int) and 0 <= c["hold"] <= 3000]
    pnl = sum(c["pnl_usd"] for c in closes) if closes else None
    win = (sum(1 for c in closes if c.get("pnl_usd", 0) > 0) / len(closes)) if closes else None
    eff_n = n if n else (count or 0)
    return {
        "trades": eff_n,
        "trades_kind": "成交笔数" if n else "平仓批次",
        "turnover_usd": turn,
        "avg_ticket_usd": (turn / eff_n) if eff_n else None,
        "fees_usd": fee,
        "fee_bp": (fee / turn * 10000) if turn else None,
        "symbols": len(syms) if n else None,
        "top3_share": top3,
        "active_days": len(days),
        "trades_per_day": (n / len(days)) if days else None,
        "months_active": len(months),
        "realized_usd": pnl,
        "win_rate": win,
        "hold_median": (sorted(hold_vals)[len(hold_vals) // 2] if hold_vals else None),
        "hold_avg": (sum(hold_vals) / len(hold_vals)) if hold_vals else None,
    }


def _month_span(start: str, end: str) -> list[str]:
    y0, m0 = int(start[:4]), int(start[5:7])
    y1, m1 = int(end[:4]), int(end[5:7])
    out, y, m = [], y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _phase_of(m: str) -> str | None:
    for key, _, _, start, end, _ in PHASES:
        if start[:7] <= m <= end[:7]:
            return key
    return None


def build() -> dict:
    fx = _rates()
    futu = _read_futu_trades(fx)
    futu_fees = _read_futu_fees(fx)
    t1599, f1599, c1599 = _read_1599(fx)
    idt, idc = _read_id(fx)
    sg = _read_sg_old(fx)
    sg_real = _sg_ledger_realized()

    trades_by_phase: dict[str, list[dict]] = defaultdict(list)
    fees_by_phase: dict[str, list[dict]] = defaultdict(list)
    closes_by_phase: dict[str, list[dict]] = defaultdict(list)

    for t in futu + t1599 + idt:
        key = _phase_of(t["m"])
        if key:
            trades_by_phase[key].append(t)
    for f in futu_fees + f1599:
        key = _phase_of(f["m"])
        if key:
            fees_by_phase[key].append(f)
    for c in c1599 + idc:
        key = _phase_of(c["m"])
        if key:
            closes_by_phase[key].append(c)
    for l in sg["dated"]:
        key = _phase_of(l["m"])
        if key:
            closes_by_phase[key].append(l)
    for t in idt:
        fees_by_phase["p8"].append({"m": t["m"], "fee_usd": t.get("fee_usd", 0.0)})

    phases = []
    for key, title, behavior, start, end, accounts in PHASES:
        span = _month_span(start, end)
        tr, fe, cl = trades_by_phase[key], fees_by_phase[key], closes_by_phase[key]
        if key == "p1":
            # SG 旧户批次表 267 条里有 24 条 quality=reject（缺平仓日/缺卖出价，持有天数为负）。
            # 行为指标只用可算的 243 条；盈亏取分段对账口径（与 /api/metrics 同一来源）。
            valid = [l for l in sg["all"] if (l.get("quality") or "") != "reject"]
            agg = _agg([], [], valid,
                       turnover=sum(l["turn_usd"] for l in valid),
                       fee_total=sum(l["fee_usd"] for l in valid),
                       count=len(sg["all"]))
            agg["realized_usd"] = sg_real if sg_real is not None else sg["pnl_usd"]
            agg["symbols"] = None
            agg["top3_share"] = None
            agg["win_rate"] = (sum(1 for l in valid if l["win"]) / len(valid)) if valid else None
            hv = [l["hold"] for l in valid if isinstance(l["hold"], int) and 0 <= l["hold"] <= 3000]
            agg["hold_median"] = sorted(hv)[len(hv) // 2] if hv else None
            agg["active_days"] = len({l["at"][:10] for l in valid if l.get("at")})
            agg["quality_note"] = f"{len(sg['all'])} 条批次中 {len(valid)} 条可算（{len(sg['all']) - len(valid)} 条解析异常）"
            realized_kind = "分段对账（旧户期初→期末）"
        elif key in ("p6", "p7", "p8"):
            agg = _agg(tr, fe, cl)
            agg["symbols"] = len({t["symbol"] for t in tr if t.get("symbol")})
            tm = {t["symbol"] for t in tr if t.get("symbol")}
            if tm and tr:
                cnt = Counter(t["symbol"] for t in tr if t.get("symbol"))
                agg["top3_share"] = sum(v for _, v in cnt.most_common(3)) / len(tr)
            realized_kind = ("ID 平仓批次 + 1599 平仓事件" if key == "p8"
                             else "平仓事件（trade_events）")
        else:
            agg = _agg(tr, fe, [])
            agg["symbols"] = len({t["symbol"] for t in tr if t.get("symbol")})
            cnt = Counter(t["symbol"] for t in tr if t.get("symbol"))
            agg["top3_share"] = (sum(v for _, v in cnt.most_common(3)) / len(tr)) if tr else None
            agg["win_rate"] = None
            realized_kind = "整段口径，无法逐月细分"

        labels: dict[str, list[str]] = {}
        for t in tr:
            lab = t.get("label") or ACCOUNT_LABEL.get(t["account"], t["account"])
            name, sub = _acct_name(lab), _acct_sub(lab)
            key2 = f"{name}·{sub}" if sub else name
            labels.setdefault(key2, []).append(t)
        acc_counts = [(k, len(v)) for k, v in labels.items()]
        acc_counts.sort(key=lambda kv: -kv[1])
        if key == "p1":
            acc_counts = [("POEMS SG·旧户", len(sg["all"]))]
        months = len(span)
        agg.update({
            "key": key, "title": title, "behavior": behavior,
            "start": start, "end": end, "months": months,
            "accounts": [k for k, _ in acc_counts],
            "account_counts": [{"name": k.split("·")[0],
                                "sub": k.split("·")[1] if "·" in k else None,
                                "n": v} for k, v in acc_counts],
            "trades_per_month": (agg["trades"] / months) if months else None,
            "realized_kind": realized_kind,
            "span_label": f"{start[:7]} → {end[:7]}",
        })
        if key in ("p2", "p3", "p4", "p5"):
            agg["realized_hint"] = "富途 6519/9461 整段合计 −$3,268（成交+费用口径，见账户对账），无法拆到单月"
        phases.append(agg)

    all_months = _month_span(PHASES[0][3], PHASES[-1][4])
    monthly = []
    for m in all_months:
        key = _phase_of(m)
        tr = [t for t in trades_by_phase.get(key, []) if t["m"] == m] if key else []
        lots = [c for c in closes_by_phase.get(key, []) if c["m"] == m] if key else []
        monthly.append({
            "m": m, "phase": key,
            "trades": len(tr), "lots": len(lots),
            "units": len(tr) if tr else len(lots),
            "turnover_usd": sum(t["amt_usd"] for t in tr),
            "symbols": len({t["symbol"] for t in tr if t.get("symbol")}),
        })

    all_trades = futu + t1599 + idt
    valid_sg = [l for l in sg["all"] if (l.get("quality") or "") != "reject"]
    trade_fee = (sum(f["fee_usd"] for f in futu_fees + f1599)
                 + sum(l["fee_usd"] for l in valid_sg)
                 + sum(t.get("fee_usd", 0) for t in idt))
    holds = [l["hold"] for l in valid_sg if isinstance(l["hold"], int) and 0 <= l["hold"] <= 3000]
    holds += [c["hold"] for c in idc if isinstance(c.get("hold"), int) and 0 <= c.get("hold") <= 3000]
    syms_all = {t["symbol"] for t in all_trades if t.get("symbol")}
    active_months = len({t["m"] for t in all_trades} | {l["m"] for l in sg["dated"]})
    total_turn = sum(t["amt_usd"] for t in all_trades) + sum(l["turn_usd"] for l in valid_sg)

    summary = {
        "start": PHASES[0][3][:7], "end": PHASES[-1][4][:7],
        "months": len(all_months), "active_months": active_months,
        "trades": len(all_trades), "sg_lots": len(sg["all"]),
        "turnover_usd": total_turn,
        "avg_ticket_usd": (sum(t["amt_usd"] for t in all_trades) / len(all_trades)) if all_trades else None,
        "fees_usd": trade_fee,
        "fee_bp": (trade_fee / total_turn * 10000) if total_turn else None,
        "symbols": len(syms_all),
        "hold_median": (sorted(holds)[len(holds) // 2] if holds else None),
        "peak_month": max(monthly, key=lambda x: x["units"])["m"] if monthly else None,
        "peak_units": max((x["units"] for x in monthly), default=0),
    }

    notes = [
        "成交笔数来自券商成交流水（同一订单可能分多笔成交），不是下单次数；POEMS SG 旧户只有平仓批次，单独标注。",
        "「开局爆发 → 间歇性高频」四段（2022-01→2024-08 富途 6519/9461）没有逐笔平仓台账，已实现只能整段汇总，故不给阶段盈亏。",
        "POEMS SG 旧户 267 条平仓批次中有 24 条解析异常（缺平仓日/缺卖出价/持有天数为负）：盈亏取分段对账口径，行为指标只用其余 243 条。",
        "POEMS SG 旧户批次表没有标的代码，故该阶段不给标的数与集中度；已实现取分段对账口径（旧户期初→期末），与批次合计的差来自 SGD 汇率与期末估值。",
        "交易费用只计佣金类（订单费用表「合计费用」与现金流「买入/卖出费用」），不含融资利息、融券费、股息税。",
        "金额统一折算 USD，汇率取 market.fx_rates()（与全站指标同一套），取不到时用 HKD 0.1282 / SGD 0.74 / IDR 0.000064。",
        "阶段盈亏只统计能逐笔归因的部分（SG 旧户、1599 平仓事件、ID 平仓批次），不等于「全历史」三账户合计；后者还含 6519/9461 整段、SG 新户与在手持仓浮亏，见「账户盈亏 · 三账户对账」。",
    ]

    return {
        "as_of": date.today().isoformat(),
        "basis": "USD",
        "fx": fx,
        "summary": summary,
        "phases": phases,
        "monthly": monthly,
        "notes": notes,
    }


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2)[:4000])
