# -*- coding: utf-8 -*-
"""SQLite 存储层：建表、连接、通用查询。"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
  id TEXT PRIMARY KEY,
  broker TEXT, entity TEXT, name TEXT,
  base_ccy TEXT, settle_ccy TEXT, multi_ccy INTEGER,
  markets TEXT, status TEXT,
  opened_at TEXT, closed_at TEXT,
  opening_cash REAL, opening_ccy TEXT,
  margin_enabled INTEGER, margin_multiplier REAL,
  mmf_sweep INTEGER, note TEXT
);

CREATE TABLE IF NOT EXISTS markets(
  code TEXT PRIMARY KEY, name TEXT, ccy TEXT, tz TEXT,
  tradable INTEGER, data_quality TEXT
);

CREATE TABLE IF NOT EXISTS instruments(
  symbol TEXT PRIMARY KEY,
  name TEXT, long_name TEXT,
  market TEXT, exchange TEXT, ccy TEXT,
  sector TEXT, industry TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS symbol_stats(
  symbol TEXT, account_id TEXT,
  name TEXT, ccy TEXT, market TEXT,
  n INTEGER, buy_q REAL, sell_q REAL, short_q REAL,
  buy_amt REAL, sell_amt REAL, short_amt REAL,
  realized REAL, events INTEGER, wins INTEGER,
  max_win REAL, max_loss REAL,
  open_long REAL, long_avg REAL, open_short REAL, short_avg REAL,
  last_price REAL,
  PRIMARY KEY(symbol, account_id)
);

-- 已导入的开仓批次（来自 ST 的 long_book / short_book / POEMS 台账）
CREATE TABLE IF NOT EXISTS positions(
  id TEXT PRIMARY KEY,
  account_id TEXT, symbol TEXT, name TEXT,
  market TEXT, ccy TEXT, side TEXT,
  qty REAL, avg_cost REAL, st_price REAL,
  opened_at TEXT, status TEXT, source TEXT, note TEXT,
  quality TEXT, quality_note TEXT
);

-- 已平仓交易（POEMS 台账 158 笔 + 日内 109 笔）
CREATE TABLE IF NOT EXISTS closed_trades(
  id TEXT PRIMARY KEY,
  account_id TEXT, symbol TEXT, name TEXT,
  market TEXT, ccy TEXT,
  qty REAL, buy_price REAL, sell_price REAL, avg_price REAL,
  fee REAL, pnl REAL, pl_pct REAL,
  open_date TEXT, close_date TEXT, holding_days INTEGER,
  grade TEXT, source TEXT,
  quality TEXT, quality_note TEXT
);

-- FUTU 平仓事件（232 条，带币种与盈亏）
CREATE TABLE IF NOT EXISTS trade_events(
  id TEXT PRIMARY KEY,
  account_id TEXT, symbol TEXT, name TEXT,
  market TEXT, ccy TEXT,
  event_type TEXT, side TEXT,
  qty REAL, price REAL, pnl REAL,
  occurred_at TEXT, source TEXT,
  quality TEXT, quality_note TEXT
);

CREATE TABLE IF NOT EXISTS nav_points(
  account_id TEXT, as_of TEXT, equity REAL, ccy TEXT, source TEXT,
  PRIMARY KEY(account_id, as_of)
);

CREATE TABLE IF NOT EXISTS recon_checks(
  id TEXT PRIMARY KEY,
  name TEXT, scope TEXT, status TEXT, detail TEXT, value TEXT
);

CREATE TABLE IF NOT EXISTS cash_balances(
  account_id TEXT, ccy TEXT, amount REAL, as_of TEXT, source TEXT,
  PRIMARY KEY(account_id, ccy)
);

-- 券商资金流水（富途「资金明细」CSV 逐行入库，出入金/成交/费用/利息的唯一来源）
CREATE TABLE IF NOT EXISTS cash_flows(
  id TEXT PRIMARY KEY,
  account_id TEXT,
  trade_type TEXT,
  symbol TEXT,
  name TEXT,
  amount REAL,
  balance REAL,
  ccy TEXT,
  occurred_at TEXT,
  source_file TEXT
);
CREATE INDEX IF NOT EXISTS idx_cash_flows_time ON cash_flows(account_id, occurred_at);

-- 每份券商流水文件的导入记录
CREATE TABLE IF NOT EXISTS statement_files(
  account_id TEXT, file TEXT, sha1 TEXT, rows INTEGER,
  period_start TEXT, period_end TEXT, cash_end TEXT, imported_at TEXT,
  PRIMARY KEY(account_id, sha1)
);

CREATE TABLE IF NOT EXISTS quotes(
  symbol TEXT PRIMARY KEY,
  price REAL, prev_close REAL, open REAL, day_high REAL, day_low REAL,
  year_high REAL, year_low REAL, volume REAL,
  ccy TEXT, market TEXT, as_of TEXT, fetched_at REAL, source TEXT
);

CREATE TABLE IF NOT EXISTS fundamentals(
  symbol TEXT PRIMARY KEY,
  payload TEXT, updated_at REAL, ok INTEGER
);

CREATE TABLE IF NOT EXISTS watchlist(
  symbol TEXT PRIMARY KEY,
  name TEXT, market TEXT, note TEXT,
  target REAL, stop REAL, created_at TEXT
);

CREATE TABLE IF NOT EXISTS fx_cache(
  pair TEXT PRIMARY KEY,
  rate REAL, updated_at REAL
);

CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY, value TEXT
);

-- 人工定价：优先级最高，覆盖实时价与收盘价（用于券商无报价、停牌、错价修正）
CREATE TABLE IF NOT EXISTS price_overrides(
  symbol TEXT PRIMARY KEY,
  price REAL,
  note TEXT,
  asof TEXT,
  updated_at TEXT
);

-- 每日收盘价（由 tools/update_close.py 拉取落库，实时价缺失时兜底）
CREATE TABLE IF NOT EXISTS daily_closes(
  symbol TEXT PRIMARY KEY,
  close REAL,
  trade_date TEXT,
  ccy TEXT,
  source TEXT,
  updated_at TEXT
);

-- POEMS 印尼成交确认逐笔：56 个单侧成交，来自 38 份 TC
CREATE TABLE IF NOT EXISTS poems_trades(
  id TEXT PRIMARY KEY,
  account_id TEXT, trade_date TEXT, settle_date TEXT,
  ref TEXT, side TEXT, symbol TEXT, name TEXT,
  qty REAL, price REAL, gross REAL, fee REAL, net REAL,
  ccy TEXT, source_file TEXT
);
CREATE INDEX IF NOT EXISTS idx_poems_trades_time ON poems_trades(account_id, trade_date);

-- POEMS 对账单的现金流（期末现金 / 存入 / 取出 / 分红 / 利息，按月一行kind）
CREATE TABLE IF NOT EXISTS poems_cashflows(
  id TEXT PRIMARY KEY,
  account_id TEXT, period_end TEXT, kind TEXT,
  amount REAL, ccy TEXT
);

-- POEMS 基金（Phillip Money Market Fund）月度余额
CREATE TABLE IF NOT EXISTS poems_mf(
  id TEXT PRIMARY KEY,
  account_id TEXT, period_end TEXT,
  units REAL, nav REAL, value REAL, ccy TEXT
);

CREATE INDEX IF NOT EXISTS idx_trade_events_account ON trade_events(account_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_positions_account ON positions(account_id);
CREATE INDEX IF NOT EXISTS idx_closed_account ON closed_trades(account_id, close_date);
"""


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        # 老库补列：quality/quality_note 由 hygiene 写入
        for table in ("positions", "closed_trades", "trade_events"):
            for col in ("quality", "quality_note"):
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass  # 列已存在


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with db() as conn:
        conn.execute(sql, tuple(params))


def replace_into(table: str, values: dict) -> None:
    cols = list(values.keys())
    sql = f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    execute(sql, [values[c] for c in cols])


def set_meta(key: str, value: Any) -> None:
    replace_into("meta", {"key": key, "value": json.dumps(value, ensure_ascii=False)})


def get_meta(key: str, default=None):
    row = one("SELECT value FROM meta WHERE key=?", [key])
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def table_counts() -> dict:
    tables = ["accounts", "markets", "positions", "symbol_stats", "closed_trades",
              "trade_events", "nav_points", "recon_checks", "instruments"]
    return {t: (one(f"SELECT COUNT(*) AS n FROM {t}") or {}).get("n", 0) for t in tables}


def db_exists() -> bool:
    return Path(config.DB_PATH).exists()
