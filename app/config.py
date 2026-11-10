# -*- coding: utf-8 -*-
"""全局配置：路径、市场、账户、汇率兜底值。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
WEB_DIR = ROOT / "web"
DB_PATH = DATA_DIR / "st.db"
ANALYTICS_PATH = PROCESSED_DIR / "analytics.json"
UNIVERSE_PATH = DATA_DIR / "universe.csv"

APP_NAME = "ST v5"
APP_SUBTITLE = "一个账本，两个门面"

HOST = "127.0.0.1"
PORT = 8300

# 市场：tradable=0 只能看，不能下单/模拟
MARKETS = [
    {"code": "US", "name": "美股", "ccy": "USD", "tz": "America/New_York",
     "tradable": 1, "data_quality": "high", "fx": "USD"},
    {"code": "HK", "name": "港股", "ccy": "HKD", "tz": "Asia/Hong_Kong",
     "tradable": 1, "data_quality": "high", "fx": "HKD"},
    {"code": "ID", "name": "印尼股", "ccy": "IDR", "tz": "Asia/Jakarta",
     "tradable": 1, "data_quality": "medium", "fx": "IDR"},
    {"code": "CN", "name": "A股（参考）", "ccy": "CNY", "tz": "Asia/Shanghai",
     "tradable": 0, "data_quality": "low", "fx": "CNY"},
]

# 三个账户 + 一个参考市场。entity 是法人实体，broker 只是品牌。
ACCOUNTS = [
    {
        "id": "futu_main", "broker": "FUTU", "entity": "Futu Securities",
        "name": "富途牛牛（主）", "base_ccy": "USD", "settle_ccy": "USD",
        "multi_ccy": 1, "markets": ["US", "HK"], "status": "active",
        "opened_at": "2022-01-13", "closed_at": None,
        "opening_cash": 0.0, "opening_ccy": "USD",
        "margin_enabled": 1, "margin_multiplier": None, "mmf_sweep": 1,
        "feed": "成交/资金/订单 CSV：2022-01→2024-08 融资融券户 6519/9461，2024-08 起保证金综合户 1599",
        "feed_status": "live",
        "note": ("同一富途号：2022-01-13 起在融资融券子账户 6519（美股）/ 9461（港股）交易，"
                 "2024-08-03 内部迁移升级为保证金综合账户 1599（USD +27,902.71 / HKD −42,208.44，"
                 "同日结转 01519 8,000 股与 AVGO −10 股）。不是从 POEMS 迁入，也不是外部入金。"),
    },
    {
        "id": "poems_sg", "broker": "POEMS", "entity": "Phillip Securities Pte Ltd (SG)",
        "name": "POEMS SG（次）", "base_ccy": "SGD", "settle_ccy": "USD",
        "multi_ccy": 1, "markets": ["US"], "status": "active",
        "opened_at": "2020-03-01", "closed_at": None,
        "opening_cash": 23110.0, "opening_ccy": "USD",
        "margin_enabled": 1, "margin_multiplier": 3.5, "mmf_sweep": 1,
        "feed": "辉立月结单，2025-11 / 2026-10（2025-12–2026-09 断档）",
        "feed_status": "archive",
        "note": "只做美股（SQQQ + USD 货币基金）。2026-10 月结单仍有持仓；仅 2 期单据，中间成交缺失，未重建成交与已实现。",
    },
    {
        "id": "poems_id", "broker": "POEMS", "entity": "Phillip Indonesia",
        "name": "POEMS ID（本地）", "base_ccy": "IDR", "settle_ccy": "IDR",
        "multi_ccy": 0, "markets": ["ID"], "status": "active",
        "opened_at": None, "closed_at": None,
        "opening_cash": None, "opening_ccy": "IDR",
        "margin_enabled": 0, "margin_multiplier": None, "mmf_sweep": 0,
        "feed": "IndoStock 台账，仅 2020 年（无证券代码列）",
        "feed_status": "archive",
        "note": "只做印尼股；现有数据来自 Share Investment.xlsx 的 IndoStock sheet（2020 年持仓，无代码列）。",
    },
]

# 展示层折算兜底（取不到实时汇率时用）。1 单位外币 = 多少 USD
FX_FALLBACK = {"USD": 1.0, "HKD": 0.12875, "SGD": 0.75, "IDR": 0.0000625, "CNY": 0.14}

FX_PAIRS = {
    "HKD": "HKDUSD=X",
    "SGD": "SGDUSD=X",
    "IDR": "IDRUSD=X",
    "CNY": "CNYUSD=X",
}

# 交易所代码 → 本系统市场码
EXCHANGE_TO_MARKET = {
    "NMS": "US", "NYQ": "US", "NGM": "US", "NCM": "US", "ASE": "US", "PCX": "US",
    "PNK": "US", "OPQ": "US", "BATS": "US", "NASDAQ": "US", "NYSE": "US", "AMEX": "US",
    "HKG": "HK", "HKEX": "HK",
    "JKT": "ID", "JK": "ID", "IDX": "ID", "JKTX": "ID",
    "SHH": "CN", "SHZ": "CN", "SSE": "CN", "SZE": "CN", "SHG": "CN",
    "SES": "SG",
}

SUFFIX_MARKET = {"HK": "HK", "JK": "ID", "SS": "CN", "SZ": "CN", "SI": "SG", "TW": "TW"}
SUFFIX_CCY = {"HK": "HKD", "JK": "IDR", "SS": "CNY", "SZ": "CNY", "SI": "SGD", "TW": "TWD"}

# 行情缓存有效期（秒）
QUOTE_TTL = 300
INFO_TTL = 7 * 24 * 3600
