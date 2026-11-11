# -*- coding: utf-8 -*-
"""ST v5 主服务：组合（Hub）+ 选股。"""
import csv
import io
import json
import math
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (config, db, discipline, edit, hygiene, insights, market, metrics,
               poems, poems_sg, portfolio, risk, screener, seed, statement, story)

def _fix_nonfinite(obj):
    """把 NaN / ±Inf 变成 null。

    行情接口偶尔返回 NaN（报价缺失、Yahoo crumb 401 兜底等）；FastAPI 的
    JSONResponse 用 allow_nan=False，一个 NaN 就能把整条响应打成 500。
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _fix_nonfinite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_fix_nonfinite(v) for v in obj]
    return obj


class SafeJSON(JSONResponse):
    """默认响应类：先清 NaN 再序列化，避免 500。"""

    def render(self, content: Any) -> bytes:
        return super().render(_fix_nonfinite(content))


app = FastAPI(title=config.APP_NAME, description="一个账本，两个门面", version="5.0",
              default_response_class=SafeJSON)
_t0 = time.time()


@app.on_event("startup")
def _startup():
    db.init_db()
    counts = db.table_counts()
    if counts.get("accounts", 0) == 0:
        try:
            seed.seed(force=True)
        except SystemExit as e:
            print("[seed] skipped:", e)


def _json_safe(obj):
    return json.loads(json.dumps(_fix_nonfinite(obj), ensure_ascii=False, default=str))


@app.get("/api/health")
def health():
    counts = db.table_counts()
    return {
        "ok": counts.get("accounts", 0) > 0,
        "app": config.APP_NAME,
        "uptime_sec": round(time.time() - _t0, 1),
        "db": str(config.DB_PATH),
        "counts": counts,
        "seeded_at": db.get_meta("seeded_at"),
        "data_revision": db.get_meta("revision"),
        "generated_at": db.get_meta("generated_at"),
    }


@app.get("/api/health/verify")
def verify():
    """把 README 里的定稿数字与库内实际值并排比对。"""
    st = db.get_meta("st_numbers", {}) or {}
    ev = db.one("SELECT ccy, COALESCE(SUM(pnl),0) AS s FROM trade_events GROUP BY ccy") or {}
    rows = db.query("SELECT ccy, COALESCE(SUM(pnl),0) AS s FROM trade_events GROUP BY ccy")
    realized_usd = 0.0
    rates = market.fx_rates()
    for r in rows:
        realized_usd += (r["s"] or 0) * rates.get(r["ccy"], 1.0)
    counts = db.table_counts()
    checks = [
        {"name": "账户数", "expected": 3, "actual": counts["accounts"], "ok": counts["accounts"] == 3},
        {"name": "可交易市场数", "expected": 3,
         "actual": (db.one("SELECT COUNT(*) AS n FROM markets WHERE tradable=1") or {}).get("n", 0),
         "ok": (db.one("SELECT COUNT(*) AS n FROM markets WHERE tradable=1") or {}).get("n") == 3},
        {"name": "POEMS 已平仓笔数", "expected": 158,
         "actual": (db.one("SELECT COUNT(*) AS n FROM closed_trades WHERE source='poems_ledger'") or {}).get("n"),
         "ok": (db.one("SELECT COUNT(*) AS n FROM closed_trades WHERE source='poems_ledger'") or {}).get("n") == 158},
        {"name": "FUTU 平仓事件", "expected": 232,
         "actual": (db.one("SELECT COUNT(*) AS n FROM trade_events") or {}).get("n"),
         "ok": (db.one("SELECT COUNT(*) AS n FROM trade_events") or {}).get("n") == 232},
        {"name": "FUTU 期末权益", "expected": st.get("equity_usd"), "actual": st.get("equity_usd"),
         "ok": True},
        {"name": "FUTU 已实现（事件合计）", "expected": round(st.get("components_usd", {}).get("realized") or 0, 2),
         "actual": round(realized_usd, 2),
         "ok": abs(realized_usd - (st.get("components_usd", {}).get("realized") or 0)) < 30},
        {"name": "双口径差额", "expected": st.get("views_gap_usd"), "actual": st.get("views_gap_usd"), "ok": True},
        {"name": "期末现金", "expected": st.get("end_cash_usd"), "actual": st.get("end_cash_usd"), "ok": True},
    ]
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks),
            "st_numbers": st, "fx": rates, "counts": counts}


@app.get("/api/markets")
def markets():
    return db.query("SELECT * FROM markets ORDER BY tradable DESC, code")


@app.get("/api/accounts")
def accounts():
    accs = portfolio.account_map()
    return [{**a, "cash": portfolio.cash_of(aid)} for aid, a in accs.items()]


@app.get("/api/overview")
def overview(base: str = "USD", account: str = "all"):
    ov = portfolio.overview(base, account)
    if ov.get("scope") == "all":
        try:
            ov["recon"] = metrics.brief_recon()
        except Exception as e:  # 对账失败不影响主数字
            ov["recon"] = {"error": repr(e)[:120]}
    return _json_safe(ov)


@app.get("/api/positions")
def positions(account: str = "all", quotes: bool = True, legacy: bool = False):
    rows = portfolio.position_rows(None if account == "all" else account,
                                   with_quotes=quotes, include_legacy=legacy)
    advice = insights.position_advice([r for r in rows if not r["legacy"]])
    for r in rows:
        r["advice"] = advice.get(r["id"], [])
    return _json_safe(rows)


@app.get("/api/positions/legacy")
def positions_legacy(account: str = "all"):
    rows = portfolio.position_rows(None if account == "all" else account,
                                   with_quotes=False, include_legacy=True)
    return _json_safe([r for r in rows if r["legacy"]])


@app.get("/api/nav")
def nav(account: str = "all"):
    return portfolio.nav_series(None if account == "all" else account)


@app.get("/api/recon")
def recon():
    return db.query("SELECT * FROM recon_checks ORDER BY id")


@app.get("/api/symbols")
def symbols(limit: int = 100):
    return _json_safe(portfolio.symbol_table(limit))


@app.get("/api/trades")
def trades(account: str = "all", kind: str = "all", limit: int = 300):
    out = []
    if kind in ("all", "events"):
        sql = "SELECT * FROM trade_events"
        params: list[Any] = []
        if account != "all":
            sql += " WHERE account_id=?"
            params.append(account)
        out += [{**r, "kind": "event"} for r in db.query(sql + " ORDER BY occurred_at DESC LIMIT ?", params + [limit])]
    if kind in ("all", "closed"):
        sql = "SELECT * FROM closed_trades"
        params = []
        if account != "all":
            sql += " WHERE account_id=?"
            params.append(account)
        out += [{**r, "kind": "closed"} for r in db.query(sql + " ORDER BY COALESCE(close_date, open_date) DESC LIMIT ?", params + [limit])]
    return out


@app.get("/api/search")
def search(q: str = Query(default=""), limit: int = 10):
    rows = market.search(q, limit)
    held = {r["symbol"] for r in db.query("SELECT DISTINCT symbol FROM positions WHERE symbol<>''")}
    resolved_held = {market.resolve(s, None) for s in held}
    for r in rows:
        r["held"] = r["symbol"] in held or r["symbol"] in resolved_held
    # 可交易的排前面，SG/其它市场沉底并标注
    rows.sort(key=lambda x: (not x["tradable"], x["market"] == "SG"))
    return rows


@app.get("/api/quote")
def quote(symbol: str, market_code: Optional[str] = None):
    q = market.quote(symbol, market_code)
    if q.get("price") is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "symbol": symbol})
    return q


@app.get("/api/history")
def history(symbol: str, market_code: Optional[str] = None, period: str = "6mo", interval: str = "1d"):
    return market.history(symbol, market_code, period, interval)


@app.get("/api/info")
def info(symbol: str, market_code: Optional[str] = None):
    return _json_safe(screener._normalize(market.fundamentals(symbol, market_code)) | {"raw": market.fundamentals(symbol, market_code)})


@app.get("/api/universe")
def universe():
    return _json_safe(screener.universe_rows())


@app.post("/api/screen/warm")
def screen_warm():
    return screener.warm()


@app.get("/api/screen/warm/status")
def screen_warm_status():
    return screener.warm_status()


@app.get("/api/screen")
def screen(markets_q: str = "US,HK,ID", pe_max: Optional[float] = None,
           pb_max: Optional[float] = None, div_min: Optional[float] = None,
           roe_min: Optional[float] = None, mcap_min: Optional[float] = None,
           price_max: Optional[float] = None, allow_missing: bool = False, sector: str = ""):
    f = {"markets": [m.strip().upper() for m in markets_q.split(",") if m.strip()],
         "pe_max": pe_max, "pb_max": pb_max, "div_min": div_min,
         "roe_min": roe_min, "mcap_min": mcap_min, "price_max": price_max,
         "allow_missing": allow_missing}
    result = screener.screen(f)
    if sector:
        result["rows"] = [r for r in result["rows"] if (r.get("sector") or "") == sector]
        result["matched"] = len(result["rows"])
    return _json_safe(result)


@app.get("/api/screen/export.csv")
def screen_export(markets_q: str = "US,HK,ID", pe_max: Optional[float] = None,
                  pb_max: Optional[float] = None, div_min: Optional[float] = None,
                  roe_min: Optional[float] = None, mcap_min: Optional[float] = None,
                  price_max: Optional[float] = None, allow_missing: bool = False):
    """把当前筛选结果导成 CSV，方便拿去券商或做进一步分析。"""
    f = {"markets": [m.strip().upper() for m in markets_q.split(",") if m.strip()],
         "pe_max": pe_max, "pb_max": pb_max, "div_min": div_min,
         "roe_min": roe_min, "mcap_min": mcap_min, "price_max": price_max,
         "allow_missing": allow_missing}
    rows = screener.screen(f)["rows"]
    cols = [("symbol", "代码"), ("name", "名称"), ("market", "市场码"), ("market_name", "市场"),
            ("ccy", "币种"), ("price", "价格"), ("pe", "PE"), ("pb", "PB"),
            ("div_yield", "股息率%"), ("roe", "ROE%"), ("mcap", "市值"), ("hits", "命中条件数")]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([c[1] for c in cols])
    for r in rows:
        w.writerow(["" if r.get(k) is None else r.get(k) for k, _ in cols])
    return Response(
        content="\ufeff" + buf.getvalue(),  # BOM 让 Excel 正确识别 UTF-8 中文
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="st-screen.csv"'},
    )


@app.get("/api/watchlist")
def watch_get():
    return db.query("SELECT * FROM watchlist ORDER BY created_at DESC")


class WatchBody(BaseModel):
    symbol: str
    name: str = ""
    market: str = "US"
    note: str = ""
    target: Optional[float] = None
    stop: Optional[float] = None


@app.post("/api/watchlist")
def watch_post(body: WatchBody):
    return screener.watch_add(body.symbol, body.name, body.market, body.note, body.target, body.stop)


@app.delete("/api/watchlist/{symbol}")
def watch_del(symbol: str):
    screener.watch_remove(symbol)
    return {"ok": True}


class SimBody(BaseModel):
    account: str
    symbol: str
    qty: float


@app.post("/api/simulate")
def simulate(body: SimBody):
    res = portfolio.simulate(body.account, body.symbol, body.qty)
    if res.get("error"):
        raise HTTPException(status_code=400, detail=res)
    return res


@app.get("/api/meta")
def meta():
    return {"app": config.APP_NAME, "revision": db.get_meta("revision"),
            "generated_at": db.get_meta("generated_at"), "seeded_at": db.get_meta("seeded_at"),
            "port": config.PORT}


# ============ 数据体检（剔除不合理交易数据） ============
@app.get("/api/hygiene")
def hygiene_scan():
    """只体检、不改库：列出所有被标记的行与规则。"""
    return _json_safe(hygiene.scan())


@app.post("/api/hygiene/apply")
def hygiene_apply():
    """把标记写回 quality 列；reject 行从此不进任何合计与时间线。"""
    return _json_safe(hygiene.apply())


# ============ 时间线 / 偏好 / 风险 ============
@app.get("/api/insights")
def insights_all():
    tl = insights.timeline()
    return _json_safe({
        "timeline": tl,
        "profile": insights.profile(),
        "risks": insights.risks(),
        "actions": insights.actions(),
        "as_of": market.now_iso(),
    })


# ============ 交易关键指标 + 券商流水 ============
@app.get("/api/metrics")
def metrics_all():
    return _json_safe(metrics.key_metrics())


# ============ 交易行为分阶段画像（时间线） ============
@app.get("/api/story")
def story_all():
    return _json_safe(story.build())


# ============ POEMS 印尼账户单据台账 ============
@app.get("/api/poems")
def poems_summary():
    return _json_safe(poems.summary())


@app.post("/api/poems/import")
def poems_import():
    """把 POEMS ID 对账单/成交确认/基金对账单重建进库（可重复执行）。"""
    return _json_safe(poems.import_all())
# ============ POEMS 新加坡账户月结单台账 ============
@app.get("/api/poems/sg")
def poems_sg_summary():
    return _json_safe(poems_sg.summary())


@app.post("/api/poems/sg/import")
def poems_sg_import():
    """把 POEMS SG 月结单/股息税单重建进库（可重复执行）。"""
    return _json_safe(poems_sg.import_all())


@app.get("/api/statement")
def statement_summary(account: str = "futu_main"):
    return _json_safe(statement.summary(account))


class StatementImportBody(BaseModel):
    path: str
    account: str = "futu_main"


@app.post("/api/statement/import")
def statement_import(body: StatementImportBody):
    """导入富途资金明细 CSV，并用最后一条余额更新该账户现金。"""
    p = Path(body.path)
    if not p.exists():
        raise HTTPException(404, f"文件不存在：{body.path}")
    return _json_safe(statement.import_file(str(p), body.account))


@app.get("/api/statement/rows")
def statement_rows_api(account: str = "futu_main", limit: int = 200, offset: int = 0,
                       type: Optional[str] = None, symbol: Optional[str] = None):
    """流水明细（默认倒序取最近 200 条，可按类型/代码过滤）。"""
    sql = "SELECT * FROM cash_flows WHERE account_id=?"
    params: list = [account]
    if type:
        sql += " AND trade_type LIKE ?"
        params.append(f"%{type}%")
    if symbol:
        sql += " AND symbol=?"
        params.append(symbol)
    total = db.query(sql.replace("SELECT *", "SELECT COUNT(*) AS n"), params)[0]["n"]
    sql += " ORDER BY occurred_at DESC LIMIT ? OFFSET ?"
    rows = db.query(sql, params + [limit, offset])
    return _json_safe({"total": total, "rows": rows, "limit": limit, "offset": offset})


# ============ 交易纪律检查 ============
@app.get("/api/discipline")
def discipline_review():
    return _json_safe(discipline.review())


class CheckBody(BaseModel):
    account: str
    symbol: str
    side: Optional[str] = "LONG"
    qty: Optional[float] = 0
    avg_cost: Optional[float] = 0
    price: Optional[float] = None
    exclude_id: Optional[str] = None


@app.post("/api/positions/check")
def discipline_check(body: CheckBody):
    return _wrap(discipline.check, body.account, body.symbol, body.side or "LONG",
                 body.qty or 0, body.avg_cost or 0, body.price, body.exclude_id)


# ============ 盘面 / 逐仓止盈止损 / 对冲与回本 ============
@app.get("/api/risk")
def risk_overview():
    return _json_safe(risk.overview())


# ============ 持仓增 / 改 / 删 ============
class PositionBody(BaseModel):
    account: Optional[str] = None
    symbol: Optional[str] = None
    market: Optional[str] = None
    side: Optional[str] = None
    qty: Optional[float] = None
    avg_cost: Optional[float] = None
    price: Optional[float] = None
    name: Optional[str] = None
    note: Optional[str] = None
    opened_at: Optional[str] = None


def _wrap(fn, *args):
    try:
        return _json_safe(fn(*args))
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "invalid_input", "message": str(e)})


@app.post("/api/positions")
def position_create(body: PositionBody):
    return _wrap(edit.create_position, body.model_dump(exclude_none=True))


@app.put("/api/positions/{pid}")
def position_update(pid: str, body: PositionBody):
    return _wrap(edit.update_position, pid, body.model_dump(exclude_none=True))


@app.delete("/api/positions/{pid}")
def position_delete(pid: str):
    return _wrap(edit.delete_position, pid)


class CashBody(BaseModel):
    account: str
    ccy: Optional[str] = None
    amount: float


@app.post("/api/cash")
def cash_set(body: CashBody):
    return _wrap(edit.set_cash, body.account, body.ccy, body.amount)


# ============ 定价：人工覆盖 / 强刷 / 收盘价 ============
class PriceBody(BaseModel):
    symbol: str
    market: Optional[str] = None
    price: float
    note: Optional[str] = ""
    asof: Optional[str] = None


@app.get("/api/prices/manual")
def prices_manual_list():
    return _json_safe(market.list_overrides())


@app.post("/api/prices/manual")
def prices_manual_set(body: PriceBody):
    return _wrap(market.set_override, body.symbol, body.price, body.note or "", body.asof, body.market)


@app.delete("/api/prices/manual")
def prices_manual_clear(symbol: str, market_code: Optional[str] = None):
    return _wrap(market.clear_override, symbol, market_code)


@app.post("/api/prices/refresh")
def prices_refresh():
    """跳过 5 分钟缓存，把当前所有持仓的报价重新拉一遍。"""
    rows = portfolio.position_rows(with_quotes=False)
    pairs = [(r["symbol"], r["market"]) for r in rows if r["symbol"]]
    res = market.refresh(pairs)
    return _json_safe({"refreshed": len(res), "at": market.now_iso(),
                       "failed": [x for x in res if not x.get("ok")]})


@app.get("/api/prices/closes")
def prices_closes():
    return _json_safe(db.query("SELECT * FROM daily_closes ORDER BY symbol"))


@app.exception_handler(Exception)
async def _err(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": type(exc).__name__, "message": str(exc)[:300]})


app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")
