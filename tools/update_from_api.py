# -*- coding: utf-8 -*-
"""把富途 OpenAPI 快照并入台账（可重复执行）。

做三件事，全部幂等：
  1. 把 `data/futu_api/latest.json` 里「晚于台账最后一条流水」的记录补进 cash_flows；
     英文交易类型按白名单映射成台账的中文类型，**映射不到的整行跳过并报出来**，
     不做猜测式归类。
  2. 用券商侧 positions 覆盖 futu_main 的在场持仓，并把 positions_snapshot 更新到快照时间。
  3. 由 cash_flows 末尾余额重算 cash_balances（与 statement.import_file 同一逻辑）。

用法：
    python -X utf8 tools/update_from_api.py --dry-run
    python -X utf8 tools/update_from_api.py
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import db, statement  # noqa: E402

SNAPSHOT = os.path.join(ROOT, "data", "futu_api", "latest.json")
ACCOUNT = "futu_main"

# 英文交易类型 → 台账中文类型（只列确认过的，缺了就跳过）
TYPE_MAP = {
    "Bank Transfer Deposit": "银行转存",
    "Bank Transfer Withdrawal": "银行转取",
    "Asset Transfer": "资产迁移",
    "Currency Exchange": "货币兑换",
    "Stock Buy Execution": "股票买入成交",
    "Stock Sell Execution": "股票卖出成交",
    "Stock Buy Fee": "股票买入费用",
    "Stock Sell Fee": "股票卖出费用",
    "ETFs Buy Execution": "ETFs买入成交",
    "ETFs Sell Execution": "ETFs卖出成交",
    "ETFs Buy Fee": "ETFs买入费用",
    "ETFs Sell Fee": "ETFs卖出费用",
    "Cash Dividend": "现金股息",
    "Stock Dividend": "现金分红",
    "Dividend Tax": "股息税",
    "Withholding Tax": "非美国居民预扣税",
    "Financing Interest": "融资/欠款利息",
    "Interest": "融资/欠款利息",
    "Borrowing Fee": "融券费用",
    "Short Sale Fee": "融券费用",
    "Fund Subscription": "基金申购",
    "Fund Redemption": "基金赎回",
    # 同一类型的复数写法（API 里两种拼法都存在）
    "Bank Transfer Deposits": "银行转存",
    "Bank Transfer Withdrawals": "银行转取",
    "Margin/Debit Interest": "融资/欠款利息",
    "NRA Withholding Tax": "非美国居民预扣税",
    "Securities Borrowing Fee": "融券费用",
    "Short Sale Dividend": "卖空股息",
}


def _parse_balance(s: str):
    """"Balance: 31,415.22 USD" → (31415.22, "USD")"""
    txt = (s or "").replace("Balance:", "").strip()
    parts = txt.split()
    if len(parts) < 2:
        return None, ""
    try:
        return float(parts[0].replace(",", "")), parts[1].upper()
    except ValueError:
        return None, ""


def _num(s: str) -> float:
    return float((s or "0").replace(",", "").replace("+", "").strip() or 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw = json.load(open(SNAPSHOT, encoding="utf-8"))
    generated = raw.get("generated_at") or ""
    logs = raw.get("cash_logs") or []

    rows = []
    unknown = []
    for r in logs:
        name = (r.get("transaction_name") or "").strip()
        t = TYPE_MAP.get(name)
        if not t:
            unknown.append(name)
            continue
        bal, ccy = _parse_balance(r.get("balance"))
        if bal is None or not ccy:
            unknown.append(f"{name} (余额无法解析)")
            continue
        try:
            ts = datetime.strptime(r["created_time"], "%m/%d/%Y %H:%M:%S")
        except (KeyError, ValueError):
            unknown.append(f"{name} (时间无法解析)")
            continue
        rows.append({
            "trade_type": t, "symbol": (r.get("symbol") or "").strip(),
            "name": (r.get("stock_name") or "").strip(),
            "amount": _num(r.get("cash_change")),
            "balance": bal, "ccy": ccy,
            "occurred_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
        })

    last = db.query("SELECT MAX(occurred_at) AS m FROM cash_flows WHERE account_id=?", (ACCOUNT,))[0]["m"] or ""
    new_rows = sorted([r for r in rows if r["occurred_at"] > last], key=lambda x: x["occurred_at"])
    print(f"快照 {generated}：可映射 {len(rows)} 行，台账尾 {last}，待补 {len(new_rows)} 行")
    if unknown:
        print(f"跳过 {len(unknown)} 行（类型或余额无法确定，不猜）：")
        for u in sorted(set(unknown)):
            print("   -", u)
    from collections import Counter
    print("待补行类型：", dict(Counter(f"{r['trade_type']}/{r['ccy']}" for r in new_rows)))

    src = os.path.basename(SNAPSHOT)
    if not args.dry_run and new_rows:
        with db.db() as conn:
            for r in new_rows:
                rid = hashlib.sha1(
                    f"{ACCOUNT}|{r['occurred_at']}|{r['trade_type']}|{r['symbol']}|"
                    f"{r['amount']}|{r['balance']}|{r['ccy']}".encode()).hexdigest()[:16]
                conn.execute(
                    "INSERT OR REPLACE INTO cash_flows"
                    "(id,account_id,trade_type,symbol,name,amount,balance,ccy,occurred_at,source_file)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rid, ACCOUNT, r["trade_type"], r["symbol"], r["name"],
                     r["amount"], r["balance"], r["ccy"], r["occurred_at"], src))
            conn.execute(
                "INSERT OR REPLACE INTO statement_files"
                "(account_id,file,sha1,rows,period_start,period_end,cash_end,imported_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (ACCOUNT, src, hashlib.sha1(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
                 len(new_rows), new_rows[0]["occurred_at"], new_rows[-1]["occurred_at"],
                 "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    # 现金余额：与 import_file 同源，取流水末尾余额
    cash = {}
    for r in db.query("SELECT ccy, balance FROM cash_flows WHERE account_id=? ORDER BY occurred_at", (ACCOUNT,)):
        cash[r["ccy"]] = float(r["balance"])
    print("流水末尾余额：", {k: round(v, 2) for k, v in cash.items()})

    # ── 持仓：按券商最新持仓覆盖 futu_main ──
    acc = next((a for a in raw.get("accounts") or [] if (a.get("positions") or [])), None) or {}
    live = []
    for p in acc.get("positions") or []:
        qty = float(p.get("qty") or 0)
        if not qty:
            continue
        code = (p.get("code") or "")
        market = "HK" if code.startswith("HK.") else "US"
        sym = code.split(".", 1)[-1]
        live.append({
            "id": f"{ACCOUNT}:{sym}:L", "symbol": sym, "market": market,
            "ccy": (p.get("currency") or "USD").upper(),
            "qty": qty, "avg_cost": float(p.get("cost") or 0),
            "st_price": float(p.get("price") or 0),
            "name": p.get("name") or "",
        })
    print("券商在场持仓：", [(x["symbol"], x["qty"], x["ccy"]) for x in live])

    existing = db.query("SELECT id, symbol, qty FROM positions WHERE account_id=? AND status='open'", (ACCOUNT,))
    keep = {x["id"] for x in live}
    gone = [e for e in existing if e["id"] not in keep]
    if gone:
        print("券商已无、台账仍 open：", [(g["symbol"], g["qty"]) for g in gone])

    if not args.dry_run:
        for x in live:
            db.replace_into("positions", {
                "id": x["id"], "account_id": ACCOUNT, "symbol": x["symbol"],
                "name": x["name"], "market": x["market"], "ccy": x["ccy"],
                "side": "LONG", "qty": x["qty"], "avg_cost": x["avg_cost"],
                "st_price": x["st_price"], "status": "open",
                "source": f"futu:openapi:{generated[:10]}",
                "note": f"券商快照 {generated[:16]}",
            })
        for g in gone:
            full = db.one("SELECT * FROM positions WHERE id=?", (g["id"]) ) or {}
            full = {k: v for k, v in full.items() if k != "quality_note"}
            full.update({"qty": 0.0, "status": "closed", "quality": "reject",
                         "note": f"券商快照 {generated[:16]} 已无此持仓，置为平仓"})
            db.replace_into("positions", full)
        db.set_meta("positions_snapshot", generated[:16])
        for ccy, amount in cash.items():
            db.replace_into("cash_balances", {
                "account_id": ACCOUNT, "ccy": ccy, "amount": amount,
                "as_of": (new_rows[-1]["occurred_at"] if new_rows else last)[:16],
                "source": f"futu:api:{src}",
            })
        print("已写入：流水 %d 行 / 持仓 %d 只 / 快照 %s" % (len(new_rows), len(live), generated[:16]))
    else:
        print("（dry-run，未写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
