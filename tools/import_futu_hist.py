# -*- coding: utf-8 -*-
"""把富途「融资融券账户 6519 / 9461」的历史成交与订单费用解析成结构化数据集。

输入（原样归档在 data/source/futu/）：
  历史成交-美股融资融券账户(6519)-20200101-20241231.csv
  历史订单费用-美股融资融券账户(6519)-20200101-20241231.csv
  历史成交-港股融资融券账户(9461)-20200101-20241231.csv
  历史订单费用-港股融资融券账户(9461)-20200101-20241231.csv
  资金明细-美股融资融券账户(6519)-20261111-075440.csv   （2026-11-11 补；9461 侧仍无同类导出）

输出：
  data/processed/futu_hist_trades.csv        归一化成交明细（逐笔，供审计）
  data/processed/futu_hist_2022_2024.json    汇总（供 app/futu_hist.py 读取）

口径要点（写进 JSON 的 meta，前端/文档直接引用）：
  * 成交金额 = 成交数量 × 成交价格，全部行成立（脚本会自校验，不符即退出）。
  * 方向只有 买入 / 卖出 / 卖空；买入同时充当「建多」与「平空」，所以
    净持仓只能当参考，现金流才是可靠量。
  * 9461（港股）只有成交与费用，没有出入金流水；6519（美股）补了资金明细后
    才有出入金（24 笔 / 净入金 +41,499.98），但该文件只覆盖美股子账户。
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
    "cash_us": "资金明细-美股融资融券账户(6519)-20261111-075440.csv",
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


def text_bytes(p):
    """字节数：原始字节数减去 CRLF 增量（= LF 归一化后的长度），含 BOM 的 3 字节。

    不能用 read_text 的结果计算：它会做通用换行转换并去掉 BOM，算出来会偏小。
    """
    raw = p.read_bytes()
    return len(raw) - raw.count(b"\r\n")


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


def agg_cashflows(rows):
    """6519 出入金：银行转存 / 出金退款 记入，银行转取 记出。

    只有 交易类型 / 代码 / 名称 / 金额 / 余额 / 币种 / 创建时间 七列，代码与名称全为 --。
    """
    dep, wit, series = [], [], []
    for r in rows:
        item = {"at": (r.get("创建时间") or "").strip().replace("/", "-"),
                "type": (r.get("交易类型") or "").strip(),
                "amount": round(num(r.get("金额")), 2),
                "balance": round(num(r.get("余额")), 2)}
        series.append(item)
        (wit if item["type"] == "银行转取" else dep).append(item)
    series.sort(key=lambda x: x["at"])
    types = defaultdict(int)
    for s in series:
        types[s["type"]] += 1
    years = defaultdict(lambda: {"n": 0, "in": 0.0, "out": 0.0})
    for s in series:
        y = years[s["at"][:4]]
        y["n"] += 1
        if s["amount"] > 0:
            y["in"] += s["amount"]
        else:
            y["out"] += s["amount"]
    dep_total = round(sum(s["amount"] for s in dep), 2)
    wit_total = round(sum(s["amount"] for s in wit), 2)
    return {
        "rows": len(series), "ccy": "USD",
        "first": series[0]["at"] if series else None,
        "last": series[-1]["at"] if series else None,
        "types": dict(types),
        "deposit_rows": len(dep), "deposit_total": dep_total,
        "withdraw_rows": len(wit), "withdraw_total": wit_total,
        "net_in": round(dep_total + wit_total, 2),
        "by_year": [{"y": k, **v, "net": round(v["in"] + v["out"], 2),
                     "in": round(v["in"], 2), "out": round(v["out"], 2)}
                    for k, v in sorted(years.items())],
        "balance_last": series[-1]["balance"] if series else None,
        "balance_last_at": series[-1]["at"] if series else None,
        "series": series,
    }


def main():
    data = {}
    all_trades = []
    meta_files = {}
    bad_all = []
    for key, name in FILES.items():
        rows, p = load(name)
        data[key] = rows
        meta_files[key] = {"file": name, "rows": len(rows), "sha1": sha1(p),
                           "bytes": text_bytes(p)}

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
    cash_us = agg_cashflows(data["cash_us"])
    # 与成交表交叉验证：净入金 + 净现金流 − 费用 = 推定期末现金（不含结转持仓与空头）
    cash_us["implied_cash_end"] = round(cash_us["net_in"] + us["net_cash"] - us["fees"]["total"], 2)

    payload = {
        "meta": {
            "account_label": "富途融资融券账户 6519（美股）/ 9461（港股）",
            "source_dir": "data/source/futu",
            "files": meta_files,
            "period": {"us": [us["first"], us["last"]], "hk": [hk["first"], hk["last"]],
                       "declared": "2020-01-01 → 2024-12-31（文件名标注）"},
            "checks": {
                "amount_eq_qty_price": "4200 + 362 行全部成立（0 行不符）",
                "cash_flows": "6519（美股）有完整出入金：24 笔，存入 +93,300.00 / 取出 −51,800.02"
                              " → 净入金 +41,499.98；9461（港股）仍无出入金导出",
                "balance_unclear": "6519 资金明细的『余额』与『累计入金 + 已记录成交』差额剧烈跳动"
                                   "（2022-03-28 +38,860 / 2022-05-09 +31,169 / 2024-06-04 +16,766）；"
                                   "而导出首笔美股成交是 2022-04-12，2021-04→2022-04 余额已从 3,000 涨到 68,860"
                                   "却无一条买单 → 要么该期间成交不在导出内（4,200 行像上限截断），"
                                   "要么『余额』不是纯现金。未定案，不据此改任何数字",
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
        "cash_us": cash_us,
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
    print("6519 出入金 {:>3} 行  {} → {}  存入 {:,.2f} / 取出 {:,.2f} / 净入金 {:,.2f}  推定期末现金 {:,.2f}".format(
        cash_us["rows"], cash_us["first"], cash_us["last"], cash_us["deposit_total"],
        cash_us["withdraw_total"], cash_us["net_in"], cash_us["implied_cash_end"]))
    print("→", OUT_JSON.relative_to(ROOT), OUT_CSV.relative_to(ROOT))


if __name__ == "__main__":
    main()
