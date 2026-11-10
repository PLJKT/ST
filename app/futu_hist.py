# -*- coding: utf-8 -*-
"""富途融资融券账户 6519 / 9461（2022-01 → 2024-08）历史成交与费用口径。

数据集由 tools/import_futu_hist.py 解析四份券商导出后落到
data/processed/futu_hist_6519_9461.json，这里只做「折算 + 段盈亏」这一层。

为什么单列一段：
  1599（保证金综合账户）在 2024-08-03 收到的「资产迁移」并不是外部入金，
  而是同一富途号下的融资融券子账户 6519（美股）/ 9461（港股）在 08-02、08-01
  停止交易后的内部升级。证据：6519 末笔 2024-08-02 12:38、9461 末笔 08-01，
  1599 于 08-03 收到 USD +27,902.71 / HKD −42,208.44，且 01519 的 8,000 股
  在 08-05 由 1599 卖出 59,100 HKD、AVGO 的 −10 股空头在 08-05 被 10 股买入平掉。

段盈亏怎么得到（不含 AMAT）：
  净现金流（买入为负、卖出/卖空为正）
  − 交易费用（完整费用表）
  + 迁移日结转出去的持仓按次日成交价计值（01519 +59,120 HKD / AVGO −1,300.30 USD）
  AMAT 394 股 2023-03-03 之后再无成交、1599 侧 0 次出现，成本 46,843.49
  单列为「未闭合项」，不并入本段——所以本段是 warn 级，不是 ok。
"""
import json

from . import config

FX_FALLBACK = {"USD": 1.0, "HKD": 0.1281}


def _rates() -> dict:
    try:
        from . import market
        r = market.fx_rates()
        return r or FX_FALLBACK
    except Exception:
        return FX_FALLBACK


def payload() -> dict:
    p = config.PROCESSED_DIR / "futu_hist_6519_9461.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def summary() -> dict:
    d = payload()
    if not d:
        return {"has_data": False}
    rates = _rates()
    hkd = float(rates.get("HKD") or FX_FALLBACK["HKD"])
    us, hk, carried = d["us"], d["hk"], d["carried"]

    amat = next((x for x in us["residuals"] if x["symbol"] == "AMAT"), None)
    amat_cost = round(-(amat["net"]), 2) if amat else 0.0

    us_net = us["net_cash"] + amat_cost          # 剔出 AMAT 的建仓成本
    us_pnl = round(us_net - us["fees"]["total"], 2)
    hk_pnl = round(hk["net_cash"] - hk["fees"]["total"], 2)

    carried_usd = round(carried["hk_qty"] * carried["hk_price"] * hkd
                        + carried["us_qty"] * carried["us_price"], 2)
    seg_usd = round(us_pnl + hk_pnl * hkd + carried_usd, 2)

    return {
        "has_data": True,
        "period_start": min(us["first"], hk["first"])[:10],
        "period_end": max(us["last"], hk["last"])[:10],
        "rows": us["rows"] + hk["rows"],
        "fee_rows": us["fees"]["rows"] + hk["fees"]["rows"],
        "labels": "富途 6519 / 9461 · 融资融券子账户",
        "us": {"rows": us["rows"], "turnover": us["turnover"], "net_cash": us["net_cash"],
               "fees": us["fees"]["total"], "pnl": us_pnl,
               "first": us["first"][:10], "last": us["last"][:10]},
        "hk": {"rows": hk["rows"], "turnover": hk["turnover"], "net_cash": hk["net_cash"],
               "fees": hk["fees"]["total"], "pnl_native": hk_pnl,
               "pnl": round(hk_pnl * hkd, 2),
               "first": hk["first"][:10], "last": hk["last"][:10]},
        "carried_usd": carried_usd,
        "carried_note": d["meta"]["evidence"]["position_carry"],
        "segment_usd": seg_usd,
        "level": "warn",
        "amat": {"symbol": "AMAT", "qty": round(amat["pos"], 2) if amat else 0,
                 "cost_usd": amat_cost,
                 "last_trade": amat["last"][:10] if amat else None},
        "amat_alt_usd": round(seg_usd - amat_cost, 2),
        "evidence": d["meta"]["evidence"],
        "checks": d["meta"]["checks"],
        "fees_breakdown": {"us": us["fees"]["breakdown"], "hk": hk["fees"]["breakdown"]},
        "years": {"us": us["years"], "hk": hk["years"]},
        "symbols_us": us["symbols"][:12],
        "symbols_hk": hk["symbols"],
    }
