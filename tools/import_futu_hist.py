# -*- coding: utf-8 -*-
"""把富途「融资融券账户 6519 / 9461」的历史成交与订单费用解析成结构化数据集。

输入（原样归档在 data/source/futu/）：
  历史成交-美股融资融券账户(6519)-20200101-20241231.csv
  历史订单费用-美股融资融券账户(6519)-20200101-20241231.csv
  历史成交-港股融资融券账户(9461)-20200101-20241231.csv
  历史订单费用-港股融资融券账户(9461)-20200101-20241231.csv

输出：
  data/processed/futu_hist_trades.csv        归一化成交明细（逐笔，供审计）
  data/processed/futu_hist_2022_2024.json    汇总（供 app/futu_hist.py 读取）

口径要点（写进 JSON 的 meta，前端/文档直接引用）：
  * 成交金额 = 成交数量 × 成交价格，全部行成立（脚本会自校验，不符即退出）。
  * 方向只有 买入 / 卖出 / 卖空；买入同时充当「建多」与「平空」，所以
    净持仓只能当参考，现金流才是可靠量。
  * 这两份导出只有成交与费用，没有出入金流水；净现金流不等于盈亏。
"""
import csv
import hashlib
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "source" / "futu"
OUT_JSON = ROOT / "data" / "processed" / "futu_hist_2022_2024.json"
OUT_CSV = ROOT / "data" / "processed" / "futu_hist_trades.csv"

FILES = {
    "us_trades": "历史成交-美股融资融券账户(6519)-20200101-20241231.csv",
    "us_fees": "历史订单费用-美股融资融券账户(6519)-20200101-20241231.csv",
    "hk_trades": "历史成交-港股融资融券账户(9461)-20200101-20241231.csv",
    "hk_fees": "历史订单费用-港股融资融券账户(9461)-20200101-20241231.csv",
}

# 2024-08-03 资产迁移进 1599 的持仓（在 1599 侧能找到对应成交，见 meta.evidence）
CARRIED = {
    "hk_symbol": "01519", "hk_qty": 8000.0, "hk_price": 7.39,
    "us_symbol": "AVGO", "us_qty": -10.0, "us_price": 130.03,
    "note": "01519 8,000 股于 2024-08-05 在 1599 以 59,100 HKD 卖出；AVGO −10 股于同日以 1,300.30 USD 买入平仓",
}


def num(s):
    s = str(s).replace(",", "").replace("+", "").strip() if s is not None else ""
    if not s or s == "--":
        return 0.0
    return float(s)


def load(name):
    p = SOURCE / name
    with io.open(p, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh)), p


def sha1(p):
    """按「CRLF → LF 归一化后的文本」取 sha1。

    仓库开了 core.autocrlf=true，clone 到另一台机器会把换行改成 CRLF，
    按原始字节哈希会让同一份文件在两台机器上算出不同值（同 data/manifest.json 的口径）。
    """
    text = p.read_text(encoding="utf-8-sig")
    return hashlib.sha1(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def parse_trades(rows, ccy, market):
    out, bad = [], []
    for r in rows:
        sym = (r.get("代码") or "").strip()
        side = (r.get("方向") or "").strip()
        qty = num(r.get("成交数量"))
        px = num(r.get("成交价格"))
        amt = num(r.get("成交金额"))
        if amt and abs(amt - qty * px) > max(0.02, abs(amt) * 1e-6):
            bad.append((sym, side, qty, px, amt))
        out.append({
            "market": market, "ccy": ccy, "symbol": sym,
            "name": (r.get("名称") or "").strip(), "side": side,
            "qty": qty, "price": px, "amount": amt,
            "at": (r.get("成交时间") or "").strip().replace("/", "-"),
        })
    return out, bad


def agg_trades(rows):
    sym = {}
    for r in rows:
        a = sym.setdefault(r["symbol"], {"symbol": r["symbol"], "name": r["name"],
                                         "n": 0, "buy": 0.0, "sell": 0.0, "pos": 0.0,
                                         "first": r["at"], "last": r["at"]})
        a["n"] += 1
        if r["side"] == "买入":
            a["buy"] += r["amount"]; a["pos"] += r["qty"]
        elif r["side"] == "卖空":
            a["sell"] += r["amount"]; a["pos"] -= r["qty"]
        else:
            a["sell"] += r["amount"]; a["pos"] -= r["qty"]
        a["first"] = min(a["first"], r["at"])
        a["last"] = max(a["last"], r["at"])
    for a in sym.values():
        a["buy"] = round(a["buy"], 2)
        a["sell"] = round(a["sell"], 2)
        a["net"] = round(a["sell"] - a["buy"], 2)
    return sym


def agg_fees(rows):
    num_cols = ["佣金", "平台使用费", "交收费"]
    optional = ["证监会规费", "交易活动费", "印花税", "交易费", "证监会征费",
                "财汇局征费", "借券印花税", "交易系统使用费"]
    cols = [c for c in num_cols + optional if rows and c in rows[0]]
    tot = {c: round(sum(num(r.get(c)) for r in rows), 2) for c in cols}
    status = defaultdict(int)
    for r in rows:
        status[(r.get("交易状态") or "").strip()] += 1
    return {"rows": len(rows), "total": round(sum(tot.values()), 2),
            "breakdown": tot, "status": dict(status)}


def main():
    data = {}
    all_trades = []
    meta_files = {}
    bad_all = []
    for key, name in FILES.items():
        rows, p = load(name)
        data[key] = rows
        meta_files[key] = {"file": name, "rows": len(rows), "sha1": sha1(p),
                           "bytes": p.stat().st_size}

    us_trades, bad_us = parse_trades(data["us_trades"], "USD", "US")
    hk_trades, bad_hk = parse_trades(data["hk_trades"], "HKD", "HK")
    bad_all = bad_us + bad_hk
    if bad_all:
        print("[FAIL] 成交金额与 数量×价格 不符 %d 行，例如 %s" % (len(bad_all), bad_all[:3]))
        sys.exit(1)

    all_trades = sorted(us_trades + hk_trades, key=lambda r: r["at"])

    def block(trades, market, ccy):
        a = agg_trades(trades)
        sides = defaultdict(int)
        qty_by_side = defaultdict(float)
        for t in trades:
            sides[t["side"]] += 1
            qty_by_side[t["side"]] += t["qty"]
        years = defaultdict(lambda: {"n": 0, "net": 0.0})
        for t in trades:
            y = t["at"][:4]
            years[y]["n"] += 1
            years[y]["net"] += t["amount"] if t["side"] != "买入" else -t["amount"]
        return {
            "market": market, "ccy": ccy, "rows": len(trades),
            "first": trades and min(t["at"] for t in trades) or None,
            "last": trades and max(t["at"] for t in trades) or None,
            "side_count": dict(sides),
            "side_qty": {k: round(v, 4) for k, v in qty_by_side.items()},
            "turnover": round(sum(t["amount"] for t in trades), 2),
            "turnover_buy": round(sum(t["amount"] for t in trades if t["side"] == "买入"), 2),
            "turnover_sell": round(sum(t["amount"] for t in trades if t["side"] == "卖出"), 2),
            "turnover_short": round(sum(t["amount"] for t in trades if t["side"] == "卖空"), 2),
            "net_cash": round(sum(t["amount"] if t["side"] != "买入" else -t["amount"] for t in trades), 2),
            "years": [{"y": k, **v, "net": round(v["net"], 2)} for k, v in sorted(years.items())],
            "symbols": sorted(a.values(), key=lambda x: -abs(x["net"])),
            "residuals": [x for x in a.values() if abs(x["pos"]) > 1e-9],
        }

    us = block(us_trades, "US", "USD")
    hk = block(hk_trades, "HK", "HKD")
    us["fees"] = agg_fees(data["us_fees"])
    hk["fees"] = agg_fees(data["hk_fees"])

    payload = {
        "meta": {
            "account_label": "富途融资融券账户 6519（美股）/ 9461（港股）",
            "source_dir": "data/source/futu",
            "files": meta_files,
            "period": {"us": [us["first"], us["last"]], "hk": [hk["first"], hk["last"]],
                       "declared": "2020-01-01 → 2024-12-31（文件名标注）"},
            "checks": {
                "amount_eq_qty_price": "4200 + 362 行全部成立（0 行不符）",
                "no_cash_flows": "导出不含出入金流水，净现金流不等于盈亏",
                "direction_note": "方向只有 买入/卖出/卖空；买入同时是建多与平空",
            },
            "evidence": {
                "migration": "1599 于 2024-08-03 收到 资产迁移 USD +27,902.71 / HKD −42,208.44；"
                             "6519 末笔成交 2024-08-02、9461 末笔 2024-08-01 → 同号内部子账户升级",
                "position_carry": CARRIED["note"],
                "unclosed": "AMAT 394 股 2023-03-03 之后无任何成交（全部成交集中在 2022-04→2023-03），"
                            "1599 侧 0 次出现；2024-08-03 的「资产迁移」只有现金 4 笔、没有证券转入记录"
                            "（证券转仓不产生现金流水）→ 按「已转出本账本」处理：成本 46,843.49 原值移出，"
                            "既不计亏损也不计资产",
            },
        },
        "carried": CARRIED,
        "us": us,
        "hk": hk,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    with io.open(OUT_CSV, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["成交时间", "市场", "币种", "代码", "名称", "方向", "数量", "价格", "金额"])
        for t in all_trades:
            w.writerow([t["at"], t["market"], t["ccy"], t["symbol"], t["name"],
                        t["side"], t["qty"], t["price"], t["amount"]])

    print("US  {:>5} 行  {} → {}  成交额 {:,.2f}  净现金流 {:,.2f}  费用 {:,.2f}".format(
        us["rows"], us["first"], us["last"], us["turnover"], us["net_cash"], us["fees"]["total"]))
    print("HK  {:>5} 行  {} → {}  成交额 {:,.2f}  净现金流 {:,.2f}  费用 {:,.2f}".format(
        hk["rows"], hk["first"], hk["last"], hk["turnover"], hk["net_cash"], hk["fees"]["total"]))
    print("残留:", [(x["symbol"], x["pos"], x["net"]) for x in us["residuals"] + hk["residuals"]])
    print("→", OUT_JSON.relative_to(ROOT), OUT_CSV.relative_to(ROOT))


if __name__ == "__main__":
    main()
