"""富途 OpenAPI 只读采集器（trade:read）。

凭据：<仓库的上一级目录>/secrets/futu_token.json（在 Git 仓库之外，不随仓库分发）
输出：<repo>/data/futu_api/*.json（只进私有库，不进 docs，不会公开）

用法：
    python tools/futu_collect.py            # 采一次（默认近 90 天资金流水）
    python tools/futu_collect.py --days 800 # 采更早的流水（2024-08 起约 800 天）
    python tools/futu_collect.py --check    # 只验证 token 与账户，不落盘
"""
import argparse
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://webapi.futunn.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 凭据放在仓库的同级目录，换机器时只需把 secrets 目录搬到同一位置（或设 FUTU_SECRETS 指向）
SECRETS = os.environ.get("FUTU_SECRETS") or os.path.join(os.path.dirname(ROOT), "secrets")
TOKEN_FILE = os.path.join(SECRETS, "futu_token.json")
OUT_DIR = os.path.join(ROOT, "data", "futu_api")


# ---------------------------------------------------------------- 凭据
def load_token():
    return json.load(open(TOKEN_FILE, encoding="utf-8"))


def save_token(tok):
    json.dump(tok, open(TOKEN_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def refresh(tok):
    form = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
        "client_id": tok["client_id"],
    }).encode()
    req = urllib.request.Request(BASE + "/oauth2/token", data=form, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        new = json.loads(r.read().decode())
    tok["access_token"] = new["access_token"]
    tok["expires_in"] = new.get("expires_in", 7200)
    tok["obtained_at"] = int(time.time())
    if new.get("refresh_token"):
        tok["refresh_token"] = new["refresh_token"]
    save_token(tok)
    return tok


def api(path, params=None, tok=None, retry=2):
    if params:
        path += "?" + urllib.parse.urlencode(params)
    for attempt in range(retry + 1):
        req = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + tok["access_token"]})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            # -12102 / 401 token 过期 → 刷新后重试
            if e.code == 401 and attempt == 0:
                tok = refresh(tok)
                continue
            raise RuntimeError(f"{path} HTTP {e.code} {body}")
    raise RuntimeError("unreachable")


def us(d):
    fmt = "%Y-%m-%d %H:%M:%S" if len(d) > 10 else "%Y-%m-%d"
    return int(dt.datetime.strptime(d, fmt).timestamp() * 1_000_000)


# ---------------------------------------------------------------- 采集
def collect(tok, days, since=None):
    accounts = api("/api/v1.0/accounts/authorized_trd_accs", tok=tok)["d"]["accounts"]
    out = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"), "accounts": [], "cash_logs": []}

    for a in accounts:
        acc_id = str(a["account_id"])
        rec = {
            "account_id": acc_id,
            "security_firm": a.get("security_firm"),
            "enable_market": a.get("enable_market"),
            "acc_type": a.get("acc_type"),
            "card_tail": str(a.get("account_card_number", ""))[-4:],
            "univs_card_tail": str(a.get("univs_account_card_number", ""))[-4:],
        }
        try:
            rec["funds"] = api(f"/api/v1.0/accounts/{acc_id}/funds", {"currency": "USD"}, tok=tok).get("d")
        except RuntimeError as e:
            rec["funds_error"] = str(e)[:160]
        try:
            pos = api(f"/api/v1.0/accounts/{acc_id}/positions", tok=tok).get("d") or []
            rec["positions"] = [{
                "code": p.get("code"), "name": p.get("stock_name"), "qty": p.get("qty"),
                "cost": p.get("cost_price"), "price": p.get("nominal_price"),
                "market_val": p.get("market_val"), "pl_val": p.get("pl_val"), "pl_ratio": p.get("pl_ratio"),
                "currency": p.get("currency"),
            } for p in pos]
            rec["position_mv_usd"] = round(sum(float(p.get("market_val") or 0) for p in pos
                                               if (p.get("currency") or "USD") == "USD"), 2)
        except RuntimeError as e:
            rec["positions_error"] = str(e)[:160]
        out["accounts"].append(rec)

        # 资金流水（分页到底）
        begin = since or (dt.date.today() - dt.timedelta(days=days)).isoformat()
        end = (dt.date.today() + dt.timedelta(days=1)).isoformat() + " 00:00:00"
        flag, pages, rows = None, 0, []
        while True:
            p = {"begin_time": us(begin), "end_time": us(end), "page_size": 20}
            if flag:
                p["page_flag"] = flag
            try:
                d = api(f"/api/v1.0/accounts/{acc_id}/cash_logs", p, tok=tok).get("d") or {}
            except RuntimeError as e:
                out.setdefault("errors", []).append(f"{acc_id} cash_logs {str(e)[:120]}")
                break
            rows += d.get("cash_logs") or []
            flag, pages = d.get("page_flag"), pages + 1
            if d.get("completed") or not flag or pages > 400:
                break
            time.sleep(0.4)
        for r in rows:
            r["_acc"] = acc_id
        out["cash_logs"] += rows
        rec["cash_log_rows"] = len(rows)
        rec["cash_log_pages"] = pages
        rec["cash_log_range"] = [begin, end]

    return out


def summarize(out):
    print(f"生成时间 {out['generated_at']}    账户 {len(out['accounts'])} 个    流水 {len(out['cash_logs'])} 行")
    for a in out["accounts"]:
        f = a.get("funds") or {}
        print(f"\n账户 {a['account_id']}  尾号 {a['card_tail']}  综合账户 …{a['univs_card_tail']}  "
              f"市场 {a['enable_market']}")
        if f:
            print(f"  总资产 {f.get('total_assets')}  现金 {f.get('cash')}  证券市值 {f.get('market_val')}  "
                  f"未实现 {f.get('unrealized_pl')}  已实现 {f.get('realized_pl')}")
        if a.get("positions"):
            for p in a["positions"]:
                print(f"    {p['code']:12s} {p['qty']:>8s} @ {p['price']}  市值 {p['market_val']}  浮盈 {p['pl_val']}")
        print(f"  资金流水 {a.get('cash_log_rows', 0)} 行（{a.get('cash_log_range', ['', ''])[0]} 起）")

    if out["cash_logs"]:
        kinds, ext = {}, {}
        for r in out["cash_logs"]:
            n = r.get("transaction_name") or "?"
            v = float((r.get("cash_change") or "0").replace(",", "").replace("+", ""))
            kinds[n] = kinds.get(n, 0) + v
            if any(w in n.lower() for w in ["deposit", "withdraw", "transfer", "入金", "出金"]):
                ext[n] = round(ext.get(n, 0.0) + v, 2)
        cost = sum(v for k, v in kinds.items() if any(w in k for w in ["Fee", "Interest", "Tax", "Borrowing"]))
        print(f"\n  净外部出入金 {ext}")
        print(f"  费用/利息/税合计 {cost:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--since", type=str, default=None, help="起始日 YYYY-MM-DD，优先于 --days")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    tok = load_token()
    if time.time() - tok.get("obtained_at", 0) > tok.get("expires_in", 7200) - 300:
        tok = refresh(tok)
        print("token 已刷新")

    out = collect(tok, args.days, args.since)
    if args.check:
        summarize(out)
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(OUT_DIR, f"futu_api_{stamp}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    latest = os.path.join(OUT_DIR, "latest.json")
    json.dump(out, open(latest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    summarize(out)
    print("\n已写入", path)


if __name__ == "__main__":
    main()
