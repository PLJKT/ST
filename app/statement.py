# -*- coding: utf-8 -*-
"""券商资金流水：解析富途「资金明细」CSV、入库、汇总。

这份流水是出入金与现金口径的唯一权威来源（台账里的现金是手抄的期末数）。
所有金额保留原币种，需要比较时用 market.fx_rates() 折算 USD。
"""
import csv
import hashlib
import io
from collections import defaultdict
from datetime import datetime

from . import db, market

ACCOUNT = "futu_main"

# 成交（含买卖费用）
TRADE_TYPES = {
    "股票买入成交", "股票卖出成交", "ETFs买入成交", "ETFs卖出成交",
    "股票买入费用", "股票卖出费用", "ETFs买入费用", "ETFs卖出费用",
}
BUY_TYPES = {"股票买入成交", "ETFs买入成交", "股票买入费用", "ETFs买入费用"}
SELL_TYPES = {"股票卖出成交", "ETFs卖出成交", "股票卖出费用", "ETFs卖出费用"}
TRADE_ONLY = {"股票买入成交", "股票卖出成交", "ETFs买入成交", "ETFs卖出成交"}
INCOME_TYPES = {"现金股息", "现金分红", "卖空股息", "股息税", "非美国居民预扣税"}
COST_TYPES = {"融资/欠款利息", "融券费用"}
FUND_TYPES = {"基金申购", "基金赎回"}
DEPOSIT_TYPES = {"银行转存"}
WITHDRAW_TYPES = {"银行转取"}
MIGRATION_TYPES = {"资产迁移", "货币兑换"}


def _num(s: str) -> float:
    s = (s or "").replace(",", "").replace("+", "").strip()
    if not s or s == "--":
        return 0.0
    return float(s)


def _iso(ts: str) -> str:
    ts = (ts or "").strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ts


def parse_csv(path: str) -> list[dict]:
    """读富途资金明细。文件是 UTF-8 BOM + 全字段带引号。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            if not r.get("创建时间"):
                continue
            rows.append({
                "trade_type": (r.get("交易类型") or "").strip(),
                "symbol": (r.get("代码") or "").strip(),
                "name": (r.get("名称") or "").strip(),
                "amount": _num(r.get("金额")),
                "balance": _num(r.get("余额")),
                "ccy": (r.get("币种") or "").strip(),
                "occurred_at": _iso(r.get("创建时间")),
            })
    return rows


def import_file(path: str, account_id: str = ACCOUNT) -> dict:
    """导入一份流水（幂等：同文件重复导入只会覆盖同 id 行）。"""
    rows = parse_csv(path)
    raw = open(path, "rb").read()
    sha1 = hashlib.sha1(raw).hexdigest()
    file_name = path.split("\\")[-1].split("/")[-1]

    with db.db() as conn:
        for r in rows:
            rid = hashlib.sha1(
                f"{account_id}|{r['occurred_at']}|{r['trade_type']}|{r['symbol']}|"
                f"{r['amount']}|{r['balance']}|{r['ccy']}".encode()).hexdigest()[:16]
            conn.execute(
                "INSERT OR REPLACE INTO cash_flows"
                "(id,account_id,trade_type,symbol,name,amount,balance,ccy,occurred_at,source_file)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, account_id, r["trade_type"], r["symbol"], r["name"],
                 r["amount"], r["balance"], r["ccy"], r["occurred_at"], file_name))
        conn.execute(
            "INSERT OR REPLACE INTO statement_files"
            "(account_id,file,sha1,rows,period_start,period_end,cash_end,imported_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (account_id, file_name, sha1, len(rows),
             rows[0]["occurred_at"] if rows else "", rows[-1]["occurred_at"] if rows else "",
             "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    # 用流水最后一条余额更新系统现金（这是券商给的数，比手抄的期末数可靠）
    cash = cash_end(account_id)
    for ccy, amount in cash.items():
        db.replace_into("cash_balances", {
            "account_id": account_id, "ccy": ccy, "amount": amount,
            "as_of": summary(account_id)["period_end"][:16],
            "source": f"futu:cash_flow:{file_name}",
        })
    return {"file": file_name, "sha1": sha1, "rows": len(rows),
            "period": [summary(account_id)["period_start"], summary(account_id)["period_end"]],
            "cash_end": cash, "imported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def rows_of(account_id: str = ACCOUNT) -> list[dict]:
    return db.query("SELECT * FROM cash_flows WHERE account_id=? ORDER BY occurred_at",
                    [account_id])


def _rates() -> dict:
    try:
        return market.fx_rates()
    except Exception:
        return {"USD": 1.0, "HKD": 0.1282, "SGD": 0.74, "IDR": 0.000064}


def cash_end(account_id: str = ACCOUNT) -> dict:
    """各币种最后一条流水的余额。"""
    rows = rows_of(account_id)
    out = {}
    for r in rows:
        out[r["ccy"]] = float(r["balance"])
    return out


def _bucket_total(rows: list[dict], types: set, rates: dict) -> dict:
    """按币种汇总一组类型的金额，并给 USD 折算。"""
    by = defaultdict(float)
    for r in rows:
        if r["trade_type"] in types:
            by[r["ccy"]] += r["amount"]
    usd = round(sum(v * rates.get(c, 1.0) for c, v in by.items()), 2)
    return {"by_ccy": {c: round(v, 2) for c, v in by.items()}, "usd": usd}


def summary(account_id: str = ACCOUNT) -> dict:
    rows = rows_of(account_id)
    rates = _rates()
    if not rows:
        return {"account": account_id, "rows": 0, "has_data": False}

    trade = [r for r in rows if r["trade_type"] in TRADE_TYPES]
    trade_only = [r for r in rows if r["trade_type"] in TRADE_ONLY]
    buys = [r for r in trade_only if r["trade_type"] in BUY_TYPES or r["amount"] < 0]
    sells = [r for r in trade_only if r["amount"] > 0]

    # 分月/分年的交易净额（成交 + 费用，不含出入金与迁移）
    monthly = defaultdict(float)
    yearly = defaultdict(float)
    for r in trade:
        usd = r["amount"] * rates.get(r["ccy"], 1.0)
        monthly[r["occurred_at"][:7]] += usd
        yearly[r["occurred_at"][:4]] += usd

    # 分标的的交易净额（卖出收入 - 买入成本，含费用）
    by_sym = defaultdict(float)
    for r in trade:
        if r["symbol"] and r["symbol"] != "--":
            by_sym[r["symbol"]] += r["amount"] * rates.get(r["ccy"], 1.0)

    out = {
        "account": account_id,
        "has_data": True,
        "rows": len(rows),
        "period_start": rows[0]["occurred_at"],
        "period_end": rows[-1]["occurred_at"],
        "days": (datetime.fromisoformat(rows[-1]["occurred_at"][:10])
                 - datetime.fromisoformat(rows[0]["occurred_at"][:10])).days,
        "trade_count": len(trade_only),
        "buy_count": len([r for r in trade_only if r["amount"] < 0]),
        "sell_count": len(sells),
        "buy_notional": round(sum(-r["amount"] * rates.get(r["ccy"], 1.0) for r in trade_only if r["amount"] < 0), 2),
        "sell_notional": round(sum(r["amount"] * rates.get(r["ccy"], 1.0) for r in trade_only if r["amount"] > 0), 2),
        "trading_net": _bucket_total(rows, TRADE_TYPES, rates),
        "income": _bucket_total(rows, INCOME_TYPES, rates),
        "cost": _bucket_total(rows, COST_TYPES, rates),
        "fund_flow": _bucket_total(rows, FUND_TYPES, rates),
        "deposit": _bucket_total(rows, DEPOSIT_TYPES, rates),
        "withdraw": _bucket_total(rows, WITHDRAW_TYPES, rates),
        "migration": _bucket_total(rows, MIGRATION_TYPES, rates),
        "cash_end": cash_end(account_id),
        "monthly": [{"month": k, "pnl_usd": round(v, 2)} for k, v in sorted(monthly.items())],
        "yearly": [{"year": k, "pnl_usd": round(v, 2)} for k, v in sorted(yearly.items())],
        "by_symbol": [{"symbol": k, "net_usd": round(v, 2)}
                      for k, v in sorted(by_sym.items(), key=lambda x: x[1])],
    }
    # 净外部投入 = 银行存入 + 银行取出（负数）+ 资产迁移/换汇净额
    # 校验恒等式：net_external + net_result 应等于末笔余额
    out["net_external"] = round(out["deposit"]["usd"] + out["withdraw"]["usd"]
                                 + out["migration"]["usd"], 2)
    out["net_result"] = round(out["trading_net"]["usd"] + out["income"]["usd"]
                              + out["cost"]["usd"] + out["fund_flow"]["usd"], 2)
    return out


def trades_after(account_id: str, since: str, symbols: list[str]) -> dict:
    """抓某日之后、当前持仓标的的成交净额（用于判断台账持仓是否落后于券商）。"""
    rows = db.query(
        "SELECT symbol, trade_type, amount, ccy, occurred_at FROM cash_flows "
        "WHERE account_id=? AND occurred_at>? AND trade_type LIKE '%成交'", [account_id, since])
    rates = _rates()
    want = {s.upper() for s in symbols if s}
    out = defaultdict(float)
    detail = []
    for r in rows:
        sym = (r["symbol"] or "").upper()
        if sym and sym in want:
            usd = float(r["amount"]) * rates.get(r["ccy"], 1.0)
            out[sym] += usd
            detail.append({"symbol": sym, "at": r["occurred_at"], "usd": round(usd, 2)})
    return {"by_symbol": {k: round(v, 2) for k, v in out.items()}, "detail": detail}
