# -*- coding: utf-8 -*-
"""数据体检：把台账里不合理/不可用的行标出来，并给出可追溯的清洗结果。

原则：不删原始行（原始台账是唯一可信源），只打质量标记：
  ok      —— 参与统计
  flag    —— 可疑（保留，但统计时单列说明）
  reject  —— 剔除（不进入任何合计、筛选、时间线）
改完可随时重跑，结果写回 positions / closed_trades / trade_events 的 quality 列。
"""
from collections import defaultdict

from . import db, market

QUALITY_RULES = [
    ("qty<=0", "数量 ≤ 0"),
    ("price<=0", "价格 ≤ 0"),
    ("no_close", "无平仓日/无卖出价"),
    ("neg_hold", "持有天数为负"),
    ("pnl_mismatch", "盈亏与价差不一致"),
    ("dup", "疑似重复"),
    ("no_cost", "无成本（台账残值）"),
    ("odd_lot", "碎股残值 < 1 股且无成本"),
]


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _scan_closed() -> list[dict]:
    rows = db.query("SELECT * FROM closed_trades")
    out = []
    sig = defaultdict(list)
    for r in rows:
        qty, buy, sell, fee = _num(r["qty"]), _num(r["buy_price"]), _num(r["sell_price"]), _num(r["fee"]) or 0
        pnl = _num(r["pnl"])
        quality, note = "ok", ""
        if qty is None or qty <= 0:
            quality, note = "reject", "数量 ≤ 0"
        elif buy is None or buy <= 0:
            quality, note = "reject", "买入价 ≤ 0"
        elif (sell is None or sell <= 0) and not r.get("close_date"):
            quality, note = "reject", "无卖出价且无平仓日（疑似未平仓/台账错行）"
        elif not r.get("close_date"):
            quality, note = "flag", "无日期，无法进入时间线"
        elif (r.get("holding_days") or 0) < 0:
            quality, note = "flag", "持有天数为负"
        if quality == "ok" and pnl is not None and sell and buy:
            notional = abs(qty * buy) or 1
            calc = qty * (sell - buy) - fee
            if abs(pnl - calc) > max(5.0, 0.01 * notional):
                quality, note = "flag", f"盈亏与价差不一致（按价差应为 {calc:,.2f}）"
        key = (r["account_id"], qty, buy, sell, round(pnl or 0, 2))
        if quality == "ok":
            sig[key].append(r["id"])
            if len(sig[key]) > 1:
                quality, note = "flag", "与另一笔完全同参数，疑似重复导入"
        out.append({"table": "closed_trades", "id": r["id"], "quality": quality,
                    "note": note, "symbol": r.get("symbol"), "account": r.get("account_id")})
    return out


def _scan_events() -> list[dict]:
    rows = db.query("SELECT * FROM trade_events")
    out = []
    for r in rows:
        qty, price, pnl = _num(r["qty"]), _num(r["price"]), _num(r["pnl"])
        quality, note = "ok", ""
        if qty is None or qty <= 0:
            quality, note = "reject", "数量 ≤ 0"
        elif price is None or price <= 0:
            quality, note = "reject", "成交价 ≤ 0"
        elif r.get("event_type") not in ("close_long", "close_short"):
            quality, note = "flag", f"未知事件类型 {r.get('event_type')}"
        elif pnl is None:
            quality, note = "reject", "缺盈亏"
        out.append({"table": "trade_events", "id": r["id"], "quality": quality,
                    "note": note, "symbol": r.get("symbol"), "account": r.get("account_id")})
    return out


def _scan_positions() -> list[dict]:
    rows = db.query("SELECT * FROM positions")
    out = []
    for r in rows:
        qty, cost = _num(r["qty"]), _num(r["avg_cost"])
        quality, note = "ok", ""
        if qty is None or qty <= 0:
            quality, note = "reject", "数量 ≤ 0（负数量/零数量）"
        elif cost is None or cost <= 0:
            if abs(qty) < 1:
                quality, note = "reject", "碎股残值（<1 股且无成本）"
            else:
                quality, note = "flag", "缺成本价"
        elif r.get("side") not in ("LONG", "SHORT"):
            quality, note = "flag", f"未知方向 {r.get('side')}"
        elif not r.get("symbol"):
            quality, note = "flag", "无证券代码（台账留档，不计入权益）"
        out.append({"table": "positions", "id": r["id"], "quality": quality,
                    "note": note, "symbol": r.get("symbol"), "account": r.get("account_id")})
    return out


def scan() -> dict:
    """只体检不改库，返回明细与汇总。"""
    detail = _scan_closed() + _scan_events() + _scan_positions()
    summary = defaultdict(lambda: {"ok": 0, "flag": 0, "reject": 0})
    for d in detail:
        summary[d["table"]][d["quality"]] += 1
    flagged = [d for d in detail if d["quality"] != "ok"]
    return {"summary": {k: dict(v) for k, v in summary.items()},
            "flagged": flagged, "flagged_count": len(flagged),
            "total": len(detail)}


def apply() -> dict:
    """把体检结果写回 quality / quality_note 两列。"""
    detail = _scan_closed() + _scan_events() + _scan_positions()
    for d in detail:
        db.execute(f"UPDATE {d['table']} SET quality=?, quality_note=? WHERE id=?",
                   [d["quality"], d["note"], d["id"]])
    summary = defaultdict(lambda: {"ok": 0, "flag": 0, "reject": 0})
    for d in detail:
        summary[d["table"]][d["quality"]] += 1
    db.set_meta("hygiene", {"applied_at": market.now_iso(),
                            "summary": {k: dict(v) for k, v in summary.items()},
                            "flagged_count": sum(v["flag"] + v["reject"] for v in summary.values())})
    return scan() | {"applied_at": market.now_iso()}


def rejected_ids(table: str) -> set[str]:
    return {r["id"] for r in db.query(f"SELECT id FROM {table} WHERE quality='reject'")}


def notes_by(table: str) -> dict:
    return {r["id"]: {"quality": r["quality"], "note": r["quality_note"]}
            for r in db.query(f"SELECT id, quality, quality_note FROM {table} WHERE quality<>'ok'")}
