# -*- coding: utf-8 -*-
"""拉取持仓标的的最近收盘价，落库到 daily_closes。

用途：实时报价拿不到时（休市、停牌、数据源抽风），组合自动用最近收盘价兜底，
而不是回退到台账里的历史价。可挂在定时任务里每天收盘后跑一次。

用法：
    uv run --with yfinance python tools/update_close.py
    uv run --with yfinance python tools/update_close.py --symbol 0700.HK
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, market  # noqa: E402


def symbols_to_update(only: list[str] | None = None) -> list[tuple[str, str, str]]:
    """返回 [(原始代码, 市场, Yahoo 代码)]，来自当前在册的持仓。"""
    if only:
        return [(s, "", market.resolve(s)) for s in only]
    rows = db.query("SELECT symbol, market, ccy FROM positions "
                    "WHERE COALESCE(symbol,'')<>'' AND COALESCE(quality,'ok')<>'reject'")
    out, seen = [], set()
    for r in rows:
        sym, mk = r["symbol"], r["market"]
        y = market.resolve(sym, mk)
        if y in seen:
            continue
        seen.add(y)
        out.append((sym, mk, y))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", action="append", help="只更新指定代码（可重复）")
    args = ap.parse_args()

    db.init_db()
    items = symbols_to_update(args.symbol)
    ok, failed = 0, []
    for sym, mk, y in items:
        try:
            h = market.history(sym, mk, period="5d", interval="1d")
            bars = h.get("bars") or []
            if not bars:
                failed.append((y, h.get("error") or "no_data"))
                continue
            last = bars[-1]
            ccy = market.market_of_symbol(y)[1]
            row = market.set_close(y, last["c"], last["t"], ccy, source="yahoo")
            if row:
                ok += 1
                print(f"ok   {y:<12} {last['t']}  close={last['c']}")
            else:
                failed.append((y, "bad_close"))
        except Exception as e:  # 单只失败不影响其它
            failed.append((y, repr(e)[:120]))
    print(f"完成：{ok}/{len(items)} 只已写入收盘价，失败 {len(failed)} 只")
    for y, err in failed:
        print(f"fail {y:<12} {err}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
