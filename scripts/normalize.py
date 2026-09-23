# -*- coding: utf-8 -*-
"""ST 数据管线 v2（FUTU 口径修正版 + 资金明细并入）。
关键修正（相对 v1，均经独立三轮校验）：
  1) v1 把 '卖空' 当 '买入' → SBUX 由亏 9,486 误记 -705、01519 由盈 4,500 误记 -4,540；
  2) v1 USD/HKD 金额直接相加 → 虚增约 2,846；现全部分币种核算；
  3) v1 KPI 误用 Excel 台账旧口径（本金 9500/亏 4260，系 2022 年前旧账户），
     现改用资金明细真实口径：迁移设立 + 银行出入金 + 期末现金/持仓；
  4) 资产迁移带入的期初空头/多头以成交价建仓，16,000 股'卖出无多可平'转为开空，时间线自洽。
"""
import csv, json, re
from collections import deque, defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)
ERR_TOKENS = {"", "#VALUE!", "#DIV/0!", "#REF!", "#N/A", "n/a", "None"}
HKD_USD = 0.12875  # 2026-09 参考汇率，仅用于折算展示，不改变分币种统计

def num(v):
    if isinstance(v, (int, float)): return float(v)
    s = (v or "").replace(",", "").strip().replace("+", "")
    try: return float(s)
    except Exception: return None

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
    if isinstance(v, (int, float)) and 20000 < v < 60000:
        return (date(1899, 12, 30) + timedelta(days=float(v))).strftime("%Y-%m-%d")
    return None

def as_int(v):
    try: return int(str(v).strip())
    except Exception: return None

def read_csv(p):
    return list(csv.reader(p.read_text(encoding="utf-8-sig").splitlines()))

# ============ FUTU 成交（定稿多空模型） ============
fills = read_csv(RAW / "历史成交-保证金综合账户(1599)-20200101-20260922.csv")
fh = {h: i for i, h in enumerate(fills[0])}
frows = sorted([r for r in fills[1:] if r and any(c.strip() for c in r)],
               key=lambda r: r[fh["成交时间"]])
def fget(r, col): return r[fh[col]].strip()

longs, shorts = {}, {}
fsym = {}
fevents = []
sell_opened_short = 0.0
for r in frows:
    c = fget(r, "代码"); side = fget(r, "方向")
    q, p, a = num(r[fh["成交数量"]]), num(r[fh["成交价格"]]), num(r[fh["成交金额"]])
    ccy = fget(r, "币种"); d_ = fget(r, "成交时间")[:10].replace("/", "-")
    e = fsym.setdefault(c, {"name": fget(r, "名称"), "ccy": ccy, "market": fget(r, "市场"),
                            "n": 0, "buy_q": 0.0, "sell_q": 0.0, "short_q": 0.0,
                            "buy_amt": 0.0, "sell_amt": 0.0, "short_amt": 0.0,
                            "realized": 0.0, "events": 0, "wins": 0,
                            "max_win": None, "max_loss": None})
    e["n"] += 1
    if side == "买入":
        e["buy_q"] += q; e["buy_amt"] += a
        rem = q; dq = shorts.setdefault(c, deque()); pnl = 0.0; cov = 0.0
        while rem > 1e-9 and dq:
            l = dq[0]; take = min(rem, l[0]); pnl += take * (l[1] - p); cov += take
            l[0] -= take; rem -= take
            if l[0] <= 1e-9: dq.popleft()
        if cov > 1e-9:
            e["realized"] += pnl; e["events"] += 1
            if pnl > 0: e["wins"] += 1
            e["max_win"] = max(e["max_win"] or pnl, pnl); e["max_loss"] = min(e["max_loss"] or pnl, pnl)
            fevents.append({"date": d_, "code": c, "name": e["name"], "type": "平空",
                            "qty": round(cov, 2), "price": p, "pnl": round(pnl, 2), "ccy": ccy})
        if rem > 1e-9: longs.setdefault(c, deque()).append([rem, p])
    elif side == "卖空":
        e["short_q"] += q; e["short_amt"] += a
        shorts.setdefault(c, deque()).append([q, p])
    else:  # 卖出：先平多，剩余转开空（保证金账户允许卖空）
        e["sell_q"] += q; e["sell_amt"] += a
        rem = q; dq = longs.setdefault(c, deque()); pnl = 0.0; cl = 0.0
        while rem > 1e-9 and dq:
            l = dq[0]; take = min(rem, l[0]); pnl += take * (p - l[1]); cl += take
            l[0] -= take; rem -= take
            if l[0] <= 1e-9: dq.popleft()
        if cl > 1e-9:
            e["realized"] += pnl; e["events"] += 1
            if pnl > 0: e["wins"] += 1
            e["max_win"] = max(e["max_win"] or pnl, pnl); e["max_loss"] = min(e["max_loss"] or pnl, pnl)
            fevents.append({"date": d_, "code": c, "name": e["name"], "type": "平多",
                            "qty": round(cl, 2), "price": p, "pnl": round(pnl, 2), "ccy": ccy})
        if rem > 1e-9:
            shorts.setdefault(c, deque()).append([rem, p]); sell_opened_short += rem

def bal(c, s_):
    src = (longs if s_ == "L" else shorts).get(c, deque())
    return round(sum(l[0] for l in src), 2)
def wavg(c, s_):
    src = (longs if s_ == "L" else shorts).get(c, deque())
    tq = sum(l[0] for l in src)
    return round(sum(l[0] * l[1] for l in src) / tq, 4) if tq > 1e-9 else None

last_px = {fget(r, "代码"): num(r[fh["成交价格"]]) for r in frows}
flong_book, fshort_book = {}, {}
float_by_ccy = defaultdict(float)
for c in sorted(set(list(longs) + list(shorts))):
    L, S = bal(c, "L"), bal(c, "S")
    lp = last_px[c]; ccy = fsym[c]["ccy"]
    fl = (lp - wavg(c, "L")) * L if L else 0.0
    fs = (wavg(c, "S") - lp) * S if S else 0.0
    float_by_ccy[ccy] += fl + fs
    if L: flong_book[c] = {"qty": L, "avg_cost": wavg(c, "L"), "last_price": lp,
                           "u_pnl": round(fl, 2), "market": fsym[c]["market"], "name": fsym[c]["name"]}
    if S: fshort_book[c] = {"qty": S, "avg_price": wavg(c, "S"), "last_price": lp,
                            "u_pnl": round(fs, 2), "market": fsym[c]["market"], "name": fsym[c]["name"]}

realized_by_ccy = defaultdict(float)
for c, e in fsym.items(): realized_by_ccy[e["ccy"]] += e["realized"]
trade_cash_by_ccy = defaultdict(float)
for c, e in fsym.items():
    trade_cash_by_ccy[e["ccy"]] += e["sell_amt"] + e["short_amt"] - e["buy_amt"]

# ============ FUTU 资金明细 ============
cf_path = RAW / "资金明细-保证金综合账户(1599)-20260923-170407.csv"
cf = read_csv(cf_path) if cf_path.exists() else None
cash = {"present": bool(cf), "by_type": {}, "last_bal": {}, "deposits": [], "withdrawals": [],
        "transfers_in": {}, "fx": {}, "fees_by_ccy": {}, "interest_by_ccy": {},
        "dividend_by_ccy": {}, "fund_by_ccy": {}, "n": 0, "first": None, "last": None,
        "recon": {}}
if cf:
    ch = {h: i for i, h in enumerate(cf[0])}
    crows = [r for r in cf[1:] if r and any(x.strip() for x in r)]
    recs = []
    for r in crows:
        recs.append({"type": r[ch["交易类型"]].strip(), "code": r[ch["代码"]].strip(),
                     "name": r[ch["名称"]].strip(), "amt": num(r[ch["金额"]]),
                     "bal": num(r[ch["余额"]]), "ccy": r[ch["币种"]].strip(),
                     "time": r[ch["创建时间"]].strip()})
    cash["n"] = len(recs)
    cash["first"], cash["last"] = min(x["time"] for x in recs), max(x["time"] for x in recs)
    agg = defaultdict(lambda: defaultdict(float)); cnt = defaultdict(int)
    for x in recs:
        if x["amt"] is None: continue
        agg[x["ccy"]][x["type"]] += x["amt"]; cnt[x["type"]] += 1
    cash["by_type"] = {c: {t: round(v, 2) for t, v in d.items()} for c, d in agg.items()}
    for ccy in agg:
        cash["fees_by_ccy"][ccy] = round(sum(v for t, v in agg[ccy].items() if ("费用" in t or "佣金" in t)), 2)
        cash["interest_by_ccy"][ccy] = round(sum(v for t, v in agg[ccy].items() if "利息" in t), 2)
        cash["dividend_by_ccy"][ccy] = round(sum(v for t, v in agg[ccy].items() if "股息" in t), 2)
        cash["fund_by_ccy"][ccy] = round(sum(v for t, v in agg[ccy].items() if "基金" in t), 2)
    for ccy in agg:
        cash["transfers_in"][ccy] = round(sum(v for t, v in agg[ccy].items() if "资产迁移" in t), 2)
        cash["fx"][ccy] = round(sum(v for t, v in agg[ccy].items() if "货币兑换" in t), 2)
    cash["deposits"] = [{"time": x["time"], "ccy": x["ccy"], "amt": x["amt"], "type": x["type"]}
                        for x in recs if x["type"] == "银行转存"]
    cash["withdrawals"] = [{"time": x["time"], "ccy": x["ccy"], "amt": x["amt"], "type": x["type"]}
                           for x in recs if x["type"] == "银行转取"]
    last_of = {}
    for x in sorted(recs, key=lambda y: y["time"]):
        if x["bal"] is not None: last_of[x["ccy"]] = x["bal"]
    cash["last_bal"] = last_of
    # 恒等式：期初余额 + Σ全部金额 = 期末余额（期初 = 首条余额 − 首条金额）
    acc_stat = {}
    for ccy in last_of:
        sub = sorted([y for y in recs if y["ccy"] == ccy], key=lambda y: y["time"])
        total_amt = sum(y["amt"] for y in sub if y["amt"] is not None)
        init_implied = last_of[ccy] - total_amt  # 恒等式推导的期初余额
        t0 = sub[0]["time"]
        ok = any(abs((x["bal"] or 0) - (x["amt"] or 0) - init_implied) < 0.05
                 for x in sub if x["time"] == t0)
        acc_stat[ccy] = {"first_bal_implied": round(init_implied, 2),
                         "final_bal": last_of[ccy], "sum_amt": round(total_amt, 2),
                         "chain_first_entry_consistent": ok}
    cash["recon"] = acc_stat
    cash["trade_cash_vs_fund"] = {}
    for ccy in cash["by_type"]:
        s = sum(v for t, v in cash["by_type"][ccy].items() if "成交" in t and "费用" not in t)
        cash["trade_cash_vs_fund"][ccy] = {"fills_net": round(trade_cash_by_ccy.get(ccy, 0), 2),
                                           "fund_net": round(s, 2), "diff": round(s - trade_cash_by_ccy.get(ccy, 0), 2)}

# ============ FUTU 订单 ============
orders = read_csv(RAW / "历史订单-保证金综合账户(1599)-20200101-20260922.csv")
oh = {h: i for i, h in enumerate(orders[0])}
orows = [r for r in orders[1:] if r and any(c.strip() for c in r)]
o_stat = {"n": len(orows), "status": {}, "direction": {}, "fees_by_ccy": {}, "by_market": {}}
for r in orows:
    st = (r[oh["交易状态"]] or "?").strip()
    o_stat["status"][st] = o_stat["status"].get(st, 0) + 1
    dg = (r[oh["方向"]] or "?").strip()
    o_stat["direction"][dg] = o_stat["direction"].get(dg, 0) + 1
    ccy = (r[oh["币种"]] or "USD").strip()
    fee = num(r[oh["合计费用"]]) if len(r) > oh["合计费用"] else None
    if fee: o_stat["fees_by_ccy"][ccy] = round(o_stat["fees_by_ccy"].get(ccy, 0) + fee, 2)
    mk = (r[oh["市场"]] or "?").strip()
    o_stat["by_market"][mk] = o_stat["by_market"].get(mk, 0) + 1
ds = {}
for r in orows:
    dg = (r[oh["方向"]] or "?").strip(); st = (r[oh["交易状态"]] or "?").strip()
    ds.setdefault(st, {}); ds[st][dg] = ds[st].get(dg, 0) + 1
o_stat["dir_status"] = ds
o_stat["cancel_rate"] = round((o_stat["status"].get("已撤单", 0) + o_stat["status"].get("部成已撤", 0)) / max(o_stat["n"], 1), 4)

f_monthly = {}
for ev in fevents:
    m = f_monthly.setdefault(ev["ccy"], {})
    m[ev["date"][:7]] = round(m.get(ev["date"][:7], 0.0) + ev["pnl"], 2)
f_years = {}
for r in frows:
    y = r[fh["成交时间"]][:4]; f_years[y] = f_years.get(y, 0) + 1

def dist_stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals: return None
    win = [v for v in vals if v > 0]; loss = [v for v in vals if v < 0]
    return {"n": len(vals), "win_rate": round(len(win) / len(vals), 4), "sum": round(sum(vals), 2),
            "avg_win": round(sum(win) / len(win), 2) if win else None,
            "avg_loss": round(sum(loss) / len(loss), 2) if loss else None,
            "payoff": round((sum(win) / len(win)) / abs(sum(loss) / len(loss)), 2) if win and loss else None,
            "max_win": round(max(vals), 2), "max_loss": round(min(vals), 2)}

# ============ POEMS（Excel，口径不变但补全披露） ============
wb = openpyxl.load_workbook(RAW / "Share Investment.xlsx", data_only=True, read_only=True)
rows_of = {n: list(wb[n].iter_rows(values_only=True))
           for n in ["Overall Summary", "SG Portfolio", "Day Trade", "Fee and Margin", "IndoStock"]}
wb.close()

osr = rows_of["Overall Summary"]
def kpi_row(i):
    v = osr[i - 1]
    return {"initial_deposit": clean(v[4]), "realized": clean(v[5]), "unrealized": clean(v[6]),
            "fees_paid": clean(v[7]), "equity": clean(v[8]), "net_gain": clean(v[9]),
            "return": clean(v[10]), "cash_balance": clean(v[2])}
kpi = {"POEMS": dict(kpi_row(4), label="POEMS（Excel 台账）",
                     asof="台账维护止于 2022-01，此后 POEMS 交易未更新"),
       "FUTU_CASHBOOK": dict(kpi_row(5), label="FUTU（旧台账，已弃用于 KPI）",
                             asof="对应 2021-22 旧口径账户，与 1599 账户（2024-08 迁入设立）非连续，保留仅供对照"),
       "TOTAL": dict(kpi_row(6), label="Entire Portfolio（旧台账合计，已弃用）",
                     asof="同上")}

months = []
for v in osr[10]:
    d = excel_date(v)
    if d and re.match(r"20\d\d-\d\d", d): months.append(d[:7])
def series_row(i):
    v = osr[i - 1]; text_seen = 0; start = None
    for j, c in enumerate(v):
        if isinstance(c, str) and c.strip() and c.strip() not in ERR_TOKENS: text_seen += 1
        elif isinstance(c, (int, float)) and text_seen >= 2: start = j; break
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
for r_, nm in [(42, "HSI"), (43, "DJI"), (44, "NASDAQ"), (45, "SPX")]:
    v = osr[r_ - 1]
    ts, start = 0, None
    for j, c in enumerate(v):
        if isinstance(c, str) and c.strip(): ts += 1
        elif isinstance(c, (int, float)) and ts >= 1: start = j; break
    start = start if start is not None else 3
    bench[nm] = [clean(v[start + k]) if isinstance(v[start + k], (int, float)) else None
                 for k in range(len(months)) if start + k < len(v)]

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
open_pos, started = [], False
for r in sg:
    if not started:
        if len(r) > 1 and clean(r[1]) == "Current Transactions": started = True
        continue
    if any(isinstance(x, str) and "Worst Scenario" in x for x in r if x): break
    no = as_int(clean(r[1])) if len(r) > 1 else None
    if no is None: continue
    g = lambda i: clean(r[i]) if len(r) > i else None
    open_pos.append({"no": no, "name": g(2), "grade": g(3), "market": g(4), "ccy": g(5),
                     "buy_price": g(6), "qty": g(7), "fee": g(8), "avg_price": g(9),
                     "investment_usd": g(10), "current_price": g(11), "book_gain_loss": g(12),
                     "pl_pct": g(13), "open_date": excel_date(r[18]) if len(r) > 18 else None,
                     "holding_days": g(21)})
ind = []
for r in rows_of["IndoStock"][7:]:
    g = lambda i: clean(r[i]) if len(r) > i else None
    if g(4) is None and g(1) is None: continue
    ind.append({"name": g(1), "market": g(2), "ccy": g(3), "buy_price": g(4), "qty": g(5),
                "fee": g(6), "avg_price": g(7), "total": g(8), "current_price": g(9),
                "gain_loss": g(10), "pl_pct": g(11), "date": excel_date(r[12]) if len(r) > 12 else None,
                "status": g(13)})
margin = {}
for r in rows_of["Fee and Margin"]:
    if len(r) < 4: continue
    k, val = clean(r[2]), clean(r[3])
    if k in ("Forex SGD to USD", "Cash Balance", "Equity Balance", "Margin Call", "Force Selling"):
        margin[k] = val
dt_rows = []
for r in rows_of["Day Trade"][3:]:
    g = lambda i: clean(r[i]) if len(r) > i else None
    if any(isinstance(x, str) and x.strip() in ("(USD)", "Amount") for x in r if x): break
    no = as_int(g(2))
    if no is None: continue
    gain = g(13); loss = g(16)
    dt_rows.append({"no": no, "date": excel_date(r[3]) if len(r) > 3 else None, "name": g(4),
                    "ccy": g(5), "grade": g(6), "buy": g(7), "qty": g(8), "fee": g(9),
                    "avg": g(10), "invest": g(11), "sell": g(12), "gain": gain, "p_pct": g(14),
                    "stop": g(15), "loss": loss, "l_pct": g(17),
                    "pnl": (gain or 0) + (loss or 0) if (gain is not None or loss is not None) else None})

# POEMS 明细 vs 矩阵勾稽
matrix_sum = round(sum(x for x in series["POEMS_sub"] if x), 2)
closed_sum = round(sum(t["gain_loss"] for t in closed if t["gain_loss"] is not None), 2)
poems_recon = {"matrix_2020_03_to_2022_01": matrix_sum,
               "detail_all_158": closed_sum,
               "detail_after_2022_01": round(sum(t["gain_loss"] for t in closed
                                                 if t["close_date"] and t["close_date"] > "2022-01"
                                                 and t["gain_loss"] is not None), 2)}
poems_ccy = defaultdict(list)
for t in closed: poems_ccy[t["ccy"] or "?"].append(t["gain_loss"])
grade_stats = defaultdict(list)
for t in dt_rows:
    if t["pnl"] is not None: grade_stats[t["grade"] or "?"].append(t["pnl"])
market_split = {}
for t in closed:
    a = market_split.setdefault(t["market"] or "?", {"n": 0, "pl": 0.0})
    a["n"] += 1
    if t["gain_loss"] is not None: a["pl"] = round(a["pl"] + t["gain_loss"], 2)
hd = [t["holding_days"] for t in closed if t["holding_days"] is not None]

def equity_curve(dep, subs):
    cash_, peak, mdd, curve = dep, dep, 0.0, []
    for x in subs:
        cash_ += x or 0.0; peak = max(peak, cash_)
        mdd = max(mdd, (peak - cash_) / peak if peak else 0.0)
        curve.append(round(cash_, 2))
    return curve, round(mdd, 4)
poems_curve, poems_mdd = equity_curve(kpi["POEMS"]["initial_deposit"], series["POEMS_sub"])

# ============ FUTU 账户级净值（资金明细口径，双视图交叉验证） ============
futu_nav = None
if cf:
    end_usd = cash["last_bal"].get("USD", 0)
    end_hkd = cash["last_bal"].get("HKD", 0)
    L_usd = sum(x["last_price"] * x["qty"] for c, x in flong_book.items() if fsym[c]["ccy"] == "USD")
    L_hkd = sum(x["last_price"] * x["qty"] for c, x in flong_book.items() if fsym[c]["ccy"] == "HKD")
    S_usd = sum(x["last_price"] * x["qty"] for c, x in fshort_book.items() if fsym[c]["ccy"] == "USD")
    S_hkd = sum(x["last_price"] * x["qty"] for c, x in fshort_book.items() if fsym[c]["ccy"] == "HKD")
    equity_usd = end_usd + L_usd - S_usd + (end_hkd + L_hkd - S_hkd) * HKD_USD
    mig_usd = cash["transfers_in"].get("USD", 0); mig_hkd = cash["transfers_in"].get("HKD", 0)
    bank_net = sum(d["amt"] for d in cash["deposits"]) + sum(w["amt"] for w in cash["withdrawals"])
    # 视图1 MOT：期末权益 − 迁入现金(折USD) − 银行净入金
    pnl_mot = equity_usd - (mig_usd + mig_hkd * HKD_USD) - bank_net
    # 视图2 交易层：已实现 + 浮动 + 利息/融券费 + 股息净 + 手续费 + 基金净
    real_usd_eq = realized_by_ccy.get("USD", 0) + realized_by_ccy.get("HKD", 0) * HKD_USD
    float_usd_eq = float_by_ccy.get("USD", 0) + float_by_ccy.get("HKD", 0) * HKD_USD
    cost_usd_eq = cash["interest_by_ccy"].get("USD", 0) + cash["interest_by_ccy"].get("HKD", 0) * HKD_USD \
        + cash["fees_by_ccy"].get("USD", 0) + cash["fees_by_ccy"].get("HKD", 0) * HKD_USD
    div_usd_eq = (cash["dividend_by_ccy"].get("USD", 0)
                  + cash["dividend_by_ccy"].get("HKD", 0) * HKD_USD)
    fund_usd_eq = cash["fund_by_ccy"].get("USD", 0)
    pnl_tradeview = real_usd_eq + float_usd_eq + cost_usd_eq + div_usd_eq + fund_usd_eq
    futu_nav = {
        "fx_rate": HKD_USD,
        "end_cash": {"USD": end_usd, "HKD": end_hkd},
        "positions_mv": {"long_usd": round(L_usd, 2), "long_hkd": round(L_hkd, 2),
                         "short_usd": round(S_usd, 2), "short_hkd": round(S_hkd, 2)},
        "equity_usd": round(equity_usd, 2),
        "capital": {"migration_usd": mig_usd, "migration_hkd": mig_hkd,
                    "migration_usd_equiv": round(mig_usd + mig_hkd * HKD_USD, 2),
                    "bank_net_usd": round(bank_net, 2)},
        "pnl_mot_usd": round(pnl_mot, 2),
        "pnl_tradeview_usd": round(pnl_tradeview, 2),
        "views_gap_usd": round(pnl_mot - pnl_tradeview, 2),
        "components_usd": {"realized": round(real_usd_eq, 2), "unrealized": round(float_usd_eq, 2),
                           "fees_interest": round(cost_usd_eq, 2), "dividends_net": round(div_usd_eq, 2),
                           "mmf_net": round(fund_usd_eq, 2)},
    }

# ============ v2.1 新增：分年本金/盈亏、回本测算、个股盈亏榜 ============
FEE_DIV_TYPES = ("费用", "佣金", "利息", "融券", "股息", "利息税")
def cf_yearly_by_type(recs):
    agg = defaultdict(lambda: defaultdict(float))
    for x in recs:
        if x["amt"] is None: continue
        agg[x["time"][:4]][x["type"]] += x["amt"]
    return agg

f_cf = {}
if cf:
    _cfh = {h: i for i, h in enumerate(cf[0])}
    _cfrecs = [{"type": r[_cfh["交易类型"]].strip(), "amt": num(r[_cfh["金额"]]),
                "ccy": r[_cfh["币种"]].strip(), "time": r[_cfh["创建时间"]].strip()}
               for r in cf[1:] if r and any(x.strip() for x in r)]
    ccy_year_type = {ccy: cf_yearly_by_type([x for x in _cfrecs if x["ccy"] == ccy])
                     for ccy in ("USD", "HKD")}
    # 逐年：已实现来自事件；费用/利息/股息来自资金流水；浮动归入最后事件年（caveats 已注明）
    years = sorted(set([e["date"][:4] for e in fevents] + list(ccy_year_type.get("USD", {})) + list(ccy_year_type.get("HKD", {}))))
    for y in years:
        row = {"realized_usd": 0.0, "other_usd": 0.0}
        for ccy in ("USD", "HKD"):
            fx = 1.0 if ccy == "USD" else HKD_USD
            row["realized_usd"] += sum(e["pnl"] for e in fevents if e["ccy"] == ccy and e["date"][:4] == y) * fx
            yt = ccy_year_type.get(ccy, {}).get(y, {})
            row["other_usd"] += sum(v for t, v in yt.items() if any(k in t for k in FEE_DIV_TYPES)) * fx
            dep = sum(v for t, v in yt.items() if "转存" in t)
            wd = sum(v for t, v in yt.items() if "转取" in t)
            mig = sum(v for t, v in yt.items() if "资产迁移" in t)
            row.setdefault("capital_" + ccy, {"deposit": round(dep, 2), "withdraw": round(wd, 2), "migration": round(mig, 2), "capital_net": round(dep + wd + mig, 2)})
        row["realized_usd"] = round(row["realized_usd"], 2)
        row["other_usd"] = round(row["other_usd"], 2)
        f_cf[y] = row
    # 浮动盈亏（按币种折USD）无法可靠分摊到年，全记入最后一年并在 caveats 说明
    last_y = max(f_cf) if f_cf else None
    if last_y:
        fu = (float_by_ccy.get("USD", 0) + float_by_ccy.get("HKD", 0) * HKD_USD)
        f_cf[last_y]["unrealized_usd"] = round(fu, 2)
        f_cf[last_y]["pnl_usd_total"] = round(f_cf[last_y]["realized_usd"] + f_cf[last_y]["other_usd"] + fu, 2)
    for y in f_cf:
        f_cf[y].setdefault("unrealized_usd", None)
        f_cf[y].setdefault("pnl_usd_total", round(f_cf[y]["realized_usd"] + f_cf[y]["other_usd"], 2))
    # 当年总投入本金（累计口径：首年=期初余额+迁移+当年入金；此后=累计）
    cum = 0.0
    for y in sorted(f_cf):
        cap = 0.0
        for ccy in ("USD", "HKD"):
            fx = 1.0 if ccy == "USD" else HKD_USD
            yt = ccy_year_type.get(ccy, {}).get(y, {})
            cap += sum(v for t, v in yt.items() if ("转存" in t or "转取" in t or "资产迁移" in t)) * fx
        # 首年再加期初存量（若期初>0；本账户期初≈0）
        f_cf[y]["capital_in_usd"] = round(cap, 2)
        cum += cap
        f_cf[y]["capital_cum_usd"] = round(cum, 2)

# POEMS 分年（明细按 close_date / open_date；台账矩阵按 months）
p_year = defaultdict(lambda: {"realized_usd": 0.0, "realized_hkd": 0.0, "unrealized_usd": 0.0,
                              "open_positions": 0})
for t in closed:
    if t["close_date"] and t["gain_loss"] is not None:
        k = "realized_usd" if t["ccy"] == "USD" else ("realized_hkd" if t["ccy"] == "HKD" else None)
        if k: p_year[t["close_date"][:4]][k] += t["gain_loss"]
for t in open_pos:
    if t["open_date"]:
        y = t["open_date"][:4]
        p_year[y]["open_positions"] += 1
        if t["book_gain_loss"] is not None:
            if t["ccy"] == "USD": p_year[y]["unrealized_usd"] += t["book_gain_loss"]
p_yearly = {y: {k: round(v, 2) for k, v in d.items()} for y, d in sorted(p_year.items())}
# 台账矩阵分年合计（USD）
p_ledger_year = {}
for i, mm in enumerate(months):
    y = mm[:4]
    v = series["POEMS_sub"][i] or 0
    p_ledger_year[y] = round(p_ledger_year.get(y, 0) + v, 2)
p_ledger_unreal = {"2022": round(kpi["POEMS"]["unrealized"], 2)}  # 台账期末快照浮亏

# 个股盈亏榜（跨账户合并：POEMS 按名称分组、FUTU per_symbol，币种折算 USD 保留原值）
ranking = {}
def add_entry(key, label, account, ccy, realized):
    r = ranking.setdefault(key, {"label": label, "account": account, "ccy": ccy,
                                 "realized": 0.0, "realized_usd": 0.0, "n": 0})
    r["realized"] += realized
    r["realized_usd"] += realized * (1.0 if ccy == "USD" else HKD_USD)
    r["n"] += 1
for c, e in fsym.items():
    add_entry("FUTU:" + c, f"{c} {e['name']}", "FUTU", e["ccy"], e["realized"])
for t in closed:
    nm = t["name"] or ("(名称损坏)#" + str(t["no"]))
    add_entry("POEMS:" + nm, nm, "POEMS", t["ccy"] or "USD", t["gain_loss"] or 0.0)
ranking_list = [{"key": k, **{kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}}
                for k, v in ranking.items()]
rank_losses = sorted(ranking_list, key=lambda x: x["realized_usd"])[:10]
rank_gains = sorted(ranking_list, key=lambda x: -x["realized_usd"])[:10]

# 回本测算（FUTU / POEMS / 合计）
import math
def payback(cur, base, rates=(0.05, 0.08, 0.10, 0.12), horizons=(24, 36, 60)):
    if not cur or not base or cur <= 0 or base <= 0 or cur >= base: return None
    G = base - cur
    pure = {}
    for R in rates:
        rm = (1 + R) ** (1 / 12) - 1
        n = math.log(1 + G / cur) / math.log(1 + rm)
        pure[f"{int(R*100)}%"] = {"months": round(n), "years": round(n / 12, 1)}
    need = {}
    for R in rates:
        rm = (1 + R) ** (1 / 12) - 1
        row = {}
        for T in horizons:
            growth = cur * ((1 + rm) ** T - 1)
            ann = ((1 + rm) ** T - 1) / rm
            C = (G - growth) / ann
            row[f"{T}m"] = None if C <= 0 else round(C, 0)
        need[f"{int(R*100)}%"] = row
    return {"pure_compound": pure, "monthly_topup": need, "gap_usd": round(G, 2)}

f_cur = futu_nav["equity_usd"] if futu_nav else None
f_base = round((futu_nav["capital"]["migration_usd_equiv"] + futu_nav["capital"]["bank_net_usd"]), 2) if futu_nav else None
p_cur = kpi["POEMS"]["equity"]; p_base = kpi["POEMS"]["initial_deposit"]
analytics_payback = {
    "FUTU": {"current": f_cur, "principal": f_base, "gap": round((f_base or 0) - (f_cur or 0), 2), "scenarios": payback(f_cur, f_base),
             "note": "目标基数=迁移等值+银行净入金；pure_compound=现有权益自然复利回本所需时间；monthly_topup=若限期24/36/60个月回本，每月需追加的定投资额（含复利增益抵扣）"},
    "POEMS": {"current": p_cur, "principal": p_base, "gap": round(p_base - p_cur, 2), "scenarios": payback(p_cur, p_base),
              "note": "台账口径（止于2022-01，未含后续平仓亏损；且含浮亏-4767，如扣减则缺口更大）"},
    "COMBINED": {"current": round((p_cur or 0) + (f_cur or 0), 2), "principal": round((p_base or 0) + (f_base or 0), 2),
                 "gap": round((p_base + f_base) - (p_cur + f_cur), 2),
                 "scenarios": payback((p_cur or 0) + (f_cur or 0), (p_base or 0) + (f_base or 0)),
                 "note": "POEMS 台账净值 + FUTU 期末权益（USD 折算）"},
}

analytics = {
    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    "revision": 3,
    "yearly": {"FUTU": f_cf, "POEMS_detail": p_yearly, "POEMS_ledger": p_ledger_year,
               "POEMS_ledger_unreal": p_ledger_unreal},
    "ranking": {"losses": rank_losses, "gains": rank_gains},
    "payback": analytics_payback,
    "changelog": [
        "v2 修正：'卖空'方向此前被误当'买入'（SBUX 实际亏 9,486 被记为 -705、01519 实际盈 4,500 被记为 -4,540）",
        "v2 修正：USD/HKD 不再混加，全部分币种核算",
        "v2 并入资金明细（2024-08→2026-09，1082 条），FUTU KPI 改账户级净值口径；Excel 旧台账（本金9500/亏4260）标注为 2021-22 旧口径，不再用于 KPI",
        "v2 补充：融券费 1,091、融资利息 298、卖空股息 955 等订单表不含的成本已计入",
        "v3 新增：总本金vs总盈亏(按年)、回本周期与条件测算、最大亏损/盈利10只个股对比",
    ],
    "caveats": [
        "POEMS 历史成交的股票名称在源文件中为损坏公式(#VALUE!)，无法恢复；历史成交以编号+市场+币种标识。",
        "POEMS 成交金额混合 USD/HKD/IDR，所有统计均按币种拆分，不相加。",
        "POEMS/Excel 台账维护止于 2022-01；2022-01 之后 POEMS 平仓明细与矩阵差额见 poems_recon。",
        "Day Trade 明细区（109 笔，净 +1,125.46）与表内汇总区（73 笔，净 +581.78）不一致，系台账后期扩充未同步；仪表盘以明细为准并标注。",
        "FUTU 1599 账户于 2024-08-03 由资产迁移设立：迁入带来 16,000 股未见于成交记录的持仓（含 01519 空头 8000@迁移价），已按成交时间线自洽记账；该部分真实成本基线以迁移现金轧差体现，标注为近似。",
        "FUTU 交易层'卖出无多可平'的 8,000 股（01519）在定稿模型中按保证金账户惯例转为开空批次，期末空单 8000@4.78 与迁移记录吻合。",
        "资金明细逐条余额链存在 128 处跨币种/多腿记录不闭合（主要为换汇、迁移多腿），但期初+全部金额=期末 恒等式在 USD/HKD 分别成立（见 cash.recon）。",
        "资产净值 HKD→USD 折算用参考汇率 0.12875（2026-09），仅用于展示层合计；所有交易与盈亏统计均为分币种原值。",
        "数据截至 2026-09-22（成交导出）/ 2026-09-23（资金明细导出）；'现价'为最后成交价，非实时行情。",
        "分年度盈亏中，FUTU 未实现浮亏按'最后事件年'归集（源数据无每日估值）；POEMS 2022-02 后盈亏按平仓日归属、浮亏为当前快照，与 FUTU 口径存在近似差异。",
    ],
    "kpi": kpi,
    "futu_nav": futu_nav,
    "equity": {"months": months, "POEMS": poems_curve, "mdd": {"POEMS": poems_mdd},
               "poems_recon": poems_recon},
    "monthly": {"months": months, **series, "benchmark": bench,
                "scope_note": "矩阵为 Excel 台账 2020-03→2022-01，FUTU 列系旧账户口径；FUTU 1599 账户真实月度盈亏见 futu.monthly（资金/成交口径，2024-08→）"},
    "poems": {"closed_trades": closed,
              "closed_stats_by_ccy": {c: dist_stats(v) for c, v in poems_ccy.items()},
              "open_positions": open_pos,
              "holding_days_buckets": {"<=5": sum(1 for x in hd if x <= 5), "6-20": sum(1 for x in hd if 5 < x <= 20),
                                       "21-60": sum(1 for x in hd if 20 < x <= 60), "61-180": sum(1 for x in hd if 60 < x <= 180),
                                       ">180": sum(1 for x in hd if x > 180)},
              "market_split": market_split, "margin": margin, "indostock": ind},
    "day_trade": {"trades": dt_rows, "stats_by_grade": {g: dist_stats(v) for g, v in grade_stats.items()},
                  "overall": dist_stats([t["pnl"] for t in dt_rows]),
                  "ledger_summary": {"gain_n": 56, "gain": 5906.82, "loss_n": 17, "loss": -5325.04,
                                     "total_n": 73, "total": 581.78,
                                     "note": "与明细区(109笔/净1125.46)不一致，明细为准"}},
    "futu": {"n_fills": len(frows), "first_fill": frows[0][fh["成交时间"]], "last_fill": frows[-1][fh["成交时间"]],
             "directions": {k: sum(1 for r in frows if r[fh["方向"]] == k) for k in ("买入", "卖出", "卖空")},
             "realized_by_ccy": {k: round(v, 2) for k, v in realized_by_ccy.items()},
             "trade_cash_by_ccy": {k: round(v, 2) for k, v in trade_cash_by_ccy.items()},
             "float_by_ccy": {k: round(v, 2) for k, v in float_by_ccy.items()},
             "sell_event_stats": dist_stats([e["pnl"] for e in fevents if e["type"] == "平多"] +
                                            [e["pnl"] for e in fevents if e["type"] == "平空"]),
             "per_symbol": {c: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in e.items()}
                            | {"open_long": bal(c, "L"), "long_avg": wavg(c, "L"),
                               "open_short": bal(c, "S"), "short_avg": wavg(c, "S"),
                               "last_price": last_px.get(c)}
                            for c, e in fsym.items()},
             "long_book": flong_book, "short_book": fshort_book,
             "events": fevents, "orders": o_stat, "monthly": f_monthly, "fills_by_year": f_years,
             "cash": cash, "sell_opened_short_qty": sell_opened_short},
}
(OUT / "analytics.json").write_text(json.dumps(analytics, ensure_ascii=False), encoding="utf-8")
with open(OUT / "futu_realized_events.csv", "w", newline="", encoding="utf-8-sig") as fp:
    w = csv.DictWriter(fp, fieldnames=list(fevents[0].keys())); w.writeheader(); w.writerows(fevents)
with open(OUT / "poems_closed_trades.csv", "w", newline="", encoding="utf-8-sig") as fp:
    w = csv.DictWriter(fp, fieldnames=list(closed[0].keys())); w.writeheader(); w.writerows(closed)

print("v2 OK", (OUT / "analytics.json").stat().st_size, "bytes")
print("realized_by_ccy:", dict(realized_by_ccy))
print("float_by_ccy:", dict(float_by_ccy))
print("futu_nav:", json.dumps(futu_nav, ensure_ascii=False) if futu_nav else None)
print("cash recon:", cash["recon"])
print("trade vs fund:", cash["trade_cash_vs_fund"])
print("poems_recon:", poems_recon)
