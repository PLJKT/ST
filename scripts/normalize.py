# -*- coding: utf-8 -*-
"""ST 数据管线：从原始交易文件提取并标准化为 dashboard 分析数据集。
输入: ST/data/raw/  输出: ST/data/processed/
"""
import csv, json, re
from collections import deque
from datetime import datetime, date, timedelta
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)

ERR_TOKENS = {"", "#VALUE!", "#DIV/0!", "#REF!", "#N/A", "n/a", "None"}

def num(v):
    if v is None or v == "": return None
    if isinstance(v, (int, float)): return float(v)
    try: return float(str(v).replace(",", ""))
    except ValueError: return None

def clean(v):
    if v is None: return None
    if isinstance(v, str):
        v = v.strip()
        return None if v in ERR_TOKENS else v
    if isinstance(v, float): return round(v, 6)
    return v

def excel_date(v):
    if isinstance(v, datetime): return v.strftime("%Y-%m-%d")
    if isinstance(v, date): return v.strftime("%Y-%m-%d")
    if isinstance(v, str):
        s = v.strip()
        m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
        if m: return "%04d-%02d-%02d" % (int(m[1]), int(m[2]), int(m[3]))
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)  # MM/DD/YYYY (Futu导出为美式)
        if m: return "%04d-%02d-%02d" % (int(m[3]), int(m[1]), int(m[2]))
    if isinstance(v, (int, float)) and 20000 < v < 60000:
        return (date(1899, 12, 30) + timedelta(days=float(v))).strftime("%Y-%m-%d")
    return None

def as_int(v):
    try: return int(str(v).strip())
    except Exception: return None

# ---------------- POEMS (Excel) ----------------
wb = openpyxl.load_workbook(RAW / "Share Investment.xlsx", data_only=True, read_only=True)
rows_of = {name: list(wb[name].iter_rows(values_only=True))
           for name in ["Overall Summary", "SG Portfolio", "Day Trade", "Fee and Margin", "IndoStock"]}
wb.close()

# -- KPI from Overall Summary rows 4-6 --
def kpi_row(i):
    v = rows_of["Overall Summary"][i - 1]
    return {"initial_deposit": clean(v[4]), "realized": clean(v[5]), "unrealized": clean(v[6]),
            "fees_paid": clean(v[7]), "equity": clean(v[8]), "net_gain": clean(v[9]),
            "return": clean(v[10]), "cash_balance": clean(v[2])}
kpi = {"POEMS": dict(kpi_row(4), label="POEMS (SG Portfolio)"),
       "FUTU": dict(kpi_row(5), label="FUTU (Excel 台账, 止于 2022-01)"),
       "TOTAL": dict(kpi_row(6), label="Entire Portfolio")}

# -- 月度矩阵 (Overall Summary rows 11-20, 22-23) --
osr = rows_of["Overall Summary"]
months = []
for v in osr[10]:
    d = excel_date(v)
    if d and re.match(r"20\d\d-\d\d", d): months.append(d[:7])

def series_row(i):
    """返回该行按 months 对齐的值；起点=该行第2个文本标签之后的第一个数值。"""
    v = osr[i - 1]
    text_seen, start = 0, None
    for j, c in enumerate(v):
        if isinstance(c, str) and c.strip() and c.strip() not in ERR_TOKENS:
            text_seen += 1
        elif isinstance(c, (int, float)) and text_seen >= 2:
            start = j; break
    start = start if start is not None else 3
    outv = []
    for k in range(len(months)):
        x = v[start + k] if start + k < len(v) else None
        outv.append(clean(x) if isinstance(x, (int, float)) else (0 if x is None else None))
    return outv

series = {"POEMS_regular": series_row(12), "POEMS_day": series_row(13), "POEMS_sub": series_row(14),
          "FUTU_regular": series_row(15), "FUTU_day": series_row(16), "FUTU_sub": series_row(17),
          "TOTAL_sub": series_row(20)}
bench = {}
for r, nm in [(42, "HSI"), (43, "DJI"), (44, "NASDAQ"), (45, "SPX")]:
    v = osr[r - 1]
    text_seen, start = 0, None
    for j, c in enumerate(v):
        if isinstance(c, str) and c.strip(): text_seen += 1
        elif isinstance(c, (int, float)) and text_seen >= 1:
            start = j; break
    start = start if start is not None else 3
    bench[nm] = [clean(v[start + k]) if isinstance(v[start + k], (int, float)) else None
                 for k in range(len(months)) if start + k < len(v)]

# -- SG Portfolio 历史平仓交易 --
sg = rows_of["SG Portfolio"]
closed = []
for r in sg:
    if len(r) > 1 and clean(r[1]) == "Current Transactions": break
    no = as_int(clean(r[1])) if len(r) > 1 else None
    if no is None: continue
    g = lambda i: clean(r[i]) if len(r) > i else None
    closed.append({"no": no, "name": g(2), "market": g(3), "ccy": g(4), "buy_price": g(5),
                   "qty": g(6), "fee": g(7), "avg_price": g(8), "investment": g(9),
                   "current_price": g(10), "gain_loss": g(11), "pl_pct": g(12), "exit_price": g(13),
                   "open_date": excel_date(r[14]) if len(r) > 14 else None,
                   "close_date": excel_date(r[15]) if len(r) > 15 else None, "holding_days": g(16)})

# -- SG Portfolio 当前持仓 (Current Transactions 区块, 止于 Worst Scenario) --
open_pos, started = [], False
for r in sg:
    row_txt = " ".join(str(x) for x in r if x) if r else ""
    if not started:
        if len(r) > 1 and clean(r[1]) == "Current Transactions": started = True
        continue
    if "Worst Scenario" in row_txt or (r and any(isinstance(x, str) and "Worst Scenario" in x for x in r)): break
    no = as_int(clean(r[1])) if len(r) > 1 else None
    if no is None: continue
    g = lambda i: clean(r[i]) if len(r) > i else None
    open_pos.append({"no": no, "name": g(2), "grade": g(3), "market": g(4), "ccy": g(5),
                     "buy_price": g(6), "qty": g(7), "fee": g(8), "avg_price": g(9),
                     "investment_usd": g(10), "current_price": g(11), "book_gain_loss": g(12),
                     "pl_pct": g(13), "open_date": excel_date(r[18]) if len(r) > 18 else None,
                     "holding_days": g(21)})

# -- IndoStock --
ind = []
for r in rows_of["IndoStock"][7:]:
    g = lambda i: clean(r[i]) if len(r) > i else None
    if g(4) is None and g(1) is None: continue
    ind.append({"name": g(1), "market": g(2), "ccy": g(3), "buy_price": g(4), "qty": g(5),
                "fee": g(6), "avg_price": g(7), "total": g(8), "current_price": g(9),
                "gain_loss": g(10), "pl_pct": g(11), "date": excel_date(r[12]) if len(r) > 12 else None,
                "status": g(13)})

# -- Fee and Margin --
margin = {}
for r in rows_of["Fee and Margin"]:
    if len(r) < 4: continue
    k, val = clean(r[2]), clean(r[3])
    if k in ("Forex SGD to USD", "Cash Balance", "Equity Balance", "Margin Call", "Force Selling"):
        margin[k] = val

# -- Day Trade --
dt_rows = []
for r in rows_of["Day Trade"][3:]:
    g = lambda i: clean(r[i]) if len(r) > i else None
    if any(isinstance(x, str) and x.strip() in ("(USD)", "Amount") for x in r if x): break
    no = as_int(g(2))
    if no is None: continue
    dt_rows.append({"no": no, "date": excel_date(r[3]) if len(r) > 3 else None, "name": g(4),
                    "ccy": g(5), "grade": g(6), "buy": g(7), "qty": g(8), "fee": g(9),
                    "avg": g(10), "invest": g(11), "sell": g(12), "gain": g(13), "p_pct": g(14),
                    "stop": g(15), "loss": g(16), "l_pct": g(17),
                    "pnl": (g(13) or 0) + (g(16) or 0) if (g(13) is not None or g(16) is not None) else None})

# ---------------- FUTU CSV ----------------
def read_csv(p):
    return list(csv.reader(p.read_text(encoding="utf-8-sig").splitlines()))

fills = read_csv(RAW / "历史成交-保证金综合账户(1599)-20200101-20260922.csv")
fh = {h: i for i, h in enumerate(fills[0])}
fut_trades = []
for r in fills[1:]:
    if not r or len(r) < 8: continue
    fut_trades.append({"code": r[fh["代码"]].strip(), "name": r[fh["名称"]].strip(),
                       "side": r[fh["方向"]].strip(), "qty": num(r[fh["成交数量"]]),
                       "price": num(r[fh["成交价格"]]), "amount": num(r[fh["成交金额"]]),
                       "time": r[fh["成交时间"]].strip(), "market": r[fh["市场"]].strip(),
                       "ccy": r[fh["币种"]].strip()})
fut_trades.sort(key=lambda t: t["time"])

lots = {}; realized_total = 0.0; per_code = {}; sell_events = []
for t in fut_trades:
    c = t["code"]
    d = per_code.setdefault(c, {"name": t["name"], "market": t["market"], "ccy": t["ccy"],
                                "buy_amt": 0.0, "sell_amt": 0.0, "buy_qty": 0.0, "sell_qty": 0.0,
                                "n_fills": 0, "realized": 0.0, "closed_events": 0, "wins": 0})
    d["n_fills"] += 1
    if t["side"] == "买入":
        d["buy_amt"] += t["amount"]; d["buy_qty"] += t["qty"]
        lots.setdefault(c, deque()).append([t["qty"], t["price"]])
    else:
        d["sell_amt"] += t["amount"]; d["sell_qty"] += t["qty"]
        q, px, pnl = t["qty"], t["price"], 0.0
        dq = lots.get(c, deque())
        while q > 1e-9 and dq:
            lot = dq[0]; take = min(q, lot[0]); pnl += take * (px - lot[1]); lot[0] -= take; q -= take
            if lot[0] <= 1e-9: dq.popleft()
        d["realized"] += pnl; d["closed_events"] += 1
        if pnl > 0: d["wins"] += 1
        realized_total += pnl
        sell_events.append({"date": t["time"][:10].replace("/", "-"), "code": c, "name": t["name"],
                            "qty": t["qty"], "price": t["price"], "pnl": round(pnl, 2), "ccy": t["ccy"]})

last_px = {}
for t in fut_trades:
    last_px[t["code"]] = t["price"]
futu_open = {c: {"qty": round(sum(l[0] for l in dq), 2),
                 "last_price": last_px.get(c),
                 "avg_cost": round(sum(l[0] * l[1] for l in dq) / max(sum(l[0] for l in dq), 1e-9), 4)}
             for c, dq in lots.items() if dq}

orders = read_csv(RAW / "历史订单-保证金综合账户(1599)-20200101-20260922.csv")
oh = {h: i for i, h in enumerate(orders[0])}
o_stat = {"n": 0, "status": {}, "fees_by_ccy": {}, "by_market": {}}
for r in orders[1:]:
    if not r or len(r) <= oh["合计费用"]: continue
    o_stat["n"] += 1
    st = r[oh["交易状态"]] or "?"
    o_stat["status"][st] = o_stat["status"].get(st, 0) + 1
    ccy = (r[oh["币种"]] or "USD").strip()
    fee = num(r[oh["合计费用"]])
    if fee: o_stat["fees_by_ccy"][ccy] = round(o_stat["fees_by_ccy"].get(ccy, 0) + fee, 2)
    mk = (r[oh["市场"]] or "?").strip()
    o_stat["by_market"][mk] = o_stat["by_market"].get(mk, 0) + 1
o_stat["cancel_rate"] = round((o_stat["status"].get("已撤单", 0) + o_stat["status"].get("部成已撤", 0)) / max(o_stat["n"], 1), 4)

fut_monthly = {}
for ev in sell_events:
    m = fut_monthly.setdefault(ev["ccy"], {}).setdefault(ev["date"][:7], 0.0)
    fut_monthly[ev["ccy"]][ev["date"][:7]] = round(m + ev["pnl"], 2)
fut_years = {}
for t in fut_trades:
    y = t["time"][:4]
    fut_years[y] = fut_years.get(y, 0) + 1

# ---------------- stats & assemble ----------------
def dist_stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals: return None
    win = [v for v in vals if v > 0]; loss = [v for v in vals if v < 0]
    return {"n": len(vals), "win_rate": round(len(win) / len(vals), 4), "sum": round(sum(vals), 2),
            "avg_win": round(sum(win) / len(win), 2) if win else None,
            "avg_loss": round(sum(loss) / len(loss), 2) if loss else None,
            "payoff": round((sum(win) / len(win)) / abs(sum(loss) / len(loss)), 2) if win and loss else None,
            "max_win": round(max(vals), 2), "max_loss": round(min(vals), 2)}

poems_ccy = {}
for t in closed:
    poems_ccy.setdefault(t["ccy"] or "?", []).append(t["gain_loss"])
grade_stats = {}
for t in dt_rows:
    if t["pnl"] is not None:
        grade_stats.setdefault(t["grade"] or "?", []).append(t["pnl"])
market_split = {}
for t in closed:
    a = market_split.setdefault(t["market"] or "?", {"n": 0, "pl": 0.0})
    a["n"] += 1
    if t["gain_loss"] is not None: a["pl"] = round(a["pl"] + t["gain_loss"], 2)
hd = [t["holding_days"] for t in closed if t["holding_days"] is not None]

def equity_curve(dep, subs):
    cash, peak, mdd, curve = dep, dep, 0.0, []
    for x in subs:
        cash += x or 0.0
        peak = max(peak, cash); mdd = max(mdd, (peak - cash) / peak if peak else 0.0)
        curve.append(round(cash, 2))
    return curve, round(mdd, 4)

poems_curve, poems_mdd = equity_curve(kpi["POEMS"]["initial_deposit"], series["POEMS_sub"])
futu_curve, futu_mdd = equity_curve(kpi["FUTU"]["initial_deposit"], series["FUTU_sub"])
total_curve, total_mdd = equity_curve(kpi["TOTAL"]["initial_deposit"], series["TOTAL_sub"])

def monthly_pnl_map(dates, amounts):
    m = {}
    for dt_, am in zip(dates, amounts):
        if dt_ and am is not None:
            k = dt_[:7]; m[k] = round(m.get(k, 0.0) + am, 2)
    return m

poems_monthly_ccy = {c: monthly_pnl_map([t["close_date"] for t in closed if t["ccy"] == c],
                                        [t["gain_loss"] for t in closed if t["ccy"] == c])
                     for c in {t["ccy"] for t in closed}}

analytics = {
    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    "caveats": [
        "POEMS 历史成交的股票名称在源文件中为损坏公式(#VALUE!)，已无法恢复，仅保留编号/市场/币种。",
        "POEMS 成交金额混合 USD/HKD 等币种，跨币种合计仅为名义近似值，统计已按币种拆分。",
        "Day Trade 表大部分行缺失日期，时间序列统计仅覆盖有日期的行。",
        "Excel『Overall Summary』台账中 FUTU 部分更新至 2022-01；2024-08 之后的 FUTU 交易仅存在于 CSV 导出中，两套口径并存。",
        "FUTU CSV 逐笔 FIFO 毛利与 Excel 自记已实现净利(含费用)存在口径差异。",
        "数据截至 2026-09-22（导出时点），当前价格为最后记录值，非实时行情。",
    ],
    "kpi": kpi,
    "equity": {"months": months, "POEMS": poems_curve, "FUTU": futu_curve, "TOTAL": total_curve,
               "mdd": {"POEMS": poems_mdd, "FUTU": futu_mdd, "TOTAL": total_mdd}},
    "poems_monthly_from_trades": poems_monthly_ccy,
    "monthly": {"months": months, **series, "benchmark": bench},
    "poems": {"closed_trades": closed,
              "closed_stats_by_ccy": {c: dist_stats(v) for c, v in poems_ccy.items()},
              "open_positions": open_pos,
              "holding_days_buckets": {"<=5": sum(1 for x in hd if x <= 5), "6-20": sum(1 for x in hd if 5 < x <= 20),
                                       "21-60": sum(1 for x in hd if 20 < x <= 60), "61-180": sum(1 for x in hd if 60 < x <= 180),
                                       ">180": sum(1 for x in hd if x > 180)},
              "market_split": market_split, "margin": margin, "indostock": ind},
    "day_trade": {"trades": dt_rows, "stats_by_grade": {g: dist_stats(v) for g, v in grade_stats.items()},
                  "overall": dist_stats([t["pnl"] for t in dt_rows])},
    "futu": {"n_fills": len(fut_trades),
             "first_fill": fut_trades[0]["time"] if fut_trades else None,
             "last_fill": fut_trades[-1]["time"] if fut_trades else None,
             "realized_fifo_gross": round(realized_total, 2),
             "per_symbol": {c: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()} for c, d in per_code.items()},
             "sell_event_stats": dist_stats([e["pnl"] for e in sell_events]),
             "sell_events": sell_events,
             "open_positions": futu_open, "orders": o_stat,
             "monthly_realized": fut_monthly, "fills_by_year": fut_years},
}

(OUT / "analytics.json").write_text(json.dumps(analytics, ensure_ascii=False), encoding="utf-8")
with open(OUT / "poems_closed_trades.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(closed[0].keys())); w.writeheader(); w.writerows(closed)
with open(OUT / "futu_sell_realized.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(sell_events[0].keys())); w.writeheader(); w.writerows(sell_events)

print("OK", (OUT / "analytics.json").stat().st_size, "bytes |",
      "closed:", len(closed), "open:", len(open_pos), "daytrade:", len(dt_rows),
      "futu_fills:", len(fut_trades), "fifo:", round(realized_total, 2),
      "| months:", (months[0], months[-1]) if months else None)
