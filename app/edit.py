# -*- coding: utf-8 -*-
"""持仓的增 / 改 / 删：手工录入的持仓进入同一张 positions 表，和台账行一起被统计。"""
import uuid

from . import config, db, market

MARKET_CCY = {m["code"]: m["ccy"] for m in config.MARKETS}
MARKET_TRADABLE = {m["code"]: m["tradable"] for m in config.MARKETS}


def _account(account_id: str) -> dict:
    acc = db.one("SELECT * FROM accounts WHERE id=?", [account_id])
    if not acc:
        raise ValueError(f"账户不存在：{account_id}")
    if acc.get("status") != "active":
        raise ValueError(f"{acc.get('name')} 已关闭，不能再新增/修改持仓")
    import json
    acc["markets"] = json.loads(acc.get("markets") or "[]")
    return acc


def _resolve_market(symbol: str, market_code: str | None, acc: dict) -> tuple[str, str, str]:
    sym = (symbol or "").strip().upper()
    if not sym:
        raise ValueError("代码不能为空")
    mk = (market_code or "").upper()
    if not mk:
        mk, _ = market.market_of_symbol(sym)
    mk, ccy = market.market_of_symbol(market.resolve(sym, mk))
    if mk == "SG":
        raise ValueError("不支持新加坡市场（POEMS SG 只交易美股）")
    if mk not in acc["markets"]:
        raise ValueError(f"{acc['name']} 只能持有 {'/'.join(acc['markets'])}，不能加 {mk}")
    if not MARKET_TRADABLE.get(mk, 0):
        raise ValueError(f"{mk} 仅为参考市场，不能建仓")
    return market.resolve(sym, mk), mk, ccy


def create_position(payload: dict) -> dict:
    acc = _account(payload.get("account") or "")
    sym, mk, ccy = _resolve_market(payload.get("symbol"), payload.get("market"), acc)
    side = (payload.get("side") or "LONG").upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError("方向只能是 LONG 或 SHORT")
    try:
        qty = float(payload.get("qty") or 0)
        cost = float(payload.get("avg_cost") or 0)
    except (TypeError, ValueError):
        raise ValueError("数量与成本必须是数字")
    if qty <= 0:
        raise ValueError("数量必须大于 0（空头也用正数量，方向选 SHORT）")
    if cost <= 0:
        raise ValueError("成本价必须大于 0")
    pid = "manual:" + uuid.uuid4().hex[:10]
    db.replace_into("positions", {
        "id": pid, "account_id": acc["id"], "symbol": sym,
        "name": payload.get("name") or "", "market": mk, "ccy": ccy,
        "side": side, "qty": qty, "avg_cost": cost,
        "st_price": payload.get("price"),
        "opened_at": payload.get("opened_at") or market.now_iso()[:10],
        "status": "open", "source": "manual", "note": payload.get("note") or "",
        "quality": "ok", "quality_note": "",
    })
    return db.one("SELECT * FROM positions WHERE id=?", [pid]) or {}


def update_position(pid: str, payload: dict) -> dict:
    row = db.one("SELECT * FROM positions WHERE id=?", [pid])
    if not row:
        raise ValueError(f"持仓不存在：{pid}")
    acc = _account(row["account_id"])
    updates: dict = {}
    if payload.get("symbol") or payload.get("market"):
        sym, mk, ccy = _resolve_market(payload.get("symbol") or row["symbol"],
                                       payload.get("market") or row["market"], acc)
        updates.update({"symbol": sym, "market": mk, "ccy": ccy})
    if payload.get("side"):
        side = str(payload["side"]).upper()
        if side not in ("LONG", "SHORT"):
            raise ValueError("方向只能是 LONG 或 SHORT")
        updates["side"] = side
    for key in ("qty", "avg_cost", "st_price", "price"):
        if payload.get(key) not in (None, ""):
            try:
                v = float(payload[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} 必须是数字")
            if key in ("qty", "avg_cost") and v <= 0:
                raise ValueError(f"{key} 必须大于 0")
            updates["st_price" if key == "price" else key] = v
    for key in ("name", "note", "opened_at"):
        if payload.get(key) is not None:
            updates[key] = payload[key]
    if not updates:
        raise ValueError("没有要更新的字段")
    updates["quality"] = "ok"
    updates["quality_note"] = "手工修改"
    cols = ",".join(f"{k}=?" for k in updates)
    db.execute(f"UPDATE positions SET {cols} WHERE id=?", list(updates.values()) + [pid])
    return db.one("SELECT * FROM positions WHERE id=?", [pid]) or {}


def delete_position(pid: str) -> dict:
    row = db.one("SELECT * FROM positions WHERE id=?", [pid])
    if not row:
        raise ValueError(f"持仓不存在：{pid}")
    db.execute("DELETE FROM positions WHERE id=?", [pid])
    return {"deleted": pid, "symbol": row.get("symbol"), "account": row.get("account_id")}


def set_cash(account_id: str, ccy: str, amount: float) -> dict:
    acc = db.one("SELECT * FROM accounts WHERE id=?", [account_id])
    if not acc:
        raise ValueError(f"账户不存在：{account_id}")
    ccy = (ccy or acc["base_ccy"]).upper()
    if ccy not in MARKET_CCY.values():
        raise ValueError(f"不支持的币种：{ccy}")
    db.replace_into("cash_balances", {"account_id": account_id, "ccy": ccy,
                                      "amount": float(amount), "as_of": market.now_iso()[:16],
                                      "source": "manual"})
    return db.one("SELECT * FROM cash_balances WHERE account_id=? AND ccy=?", [account_id, ccy]) or {}
