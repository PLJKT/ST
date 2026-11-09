# -*- coding: utf-8 -*-
r"""静态导出脱敏层。

原则：公开站点只发布"看得懂的分析"，不发布"能定位到你"的信息。
本地服务（127.0.0.1:8300）保持全量；只有写进 docs/ 的东西过这一层。

脱敏范围：
  1. 身份：持有人姓名（中英文各种写法）
  2. 账号：券商账号 / 子账户 / SID / IFUA / 银行账号（BCA）
  3. 路径：本机绝对路径与 Windows 用户名
  4. 凭证文件名：去掉账号与目录，只留类型与日期
金额、持股数量、收益率**不脱敏**（用户明确选择公开真实仓位），需要时可在
`AMOUNT_INDEX_BASE` 打开指数化（见 README「隐私与脱敏」）。

用法（被 tools/export_static.py 调用）：
    from redact import redact_payload, scrub_text, assert_clean
"""
from __future__ import annotations

import json
import re
from typing import Any

# ---- 1. 身份与账号字面量（真实值在 redact_secrets.py，仅私有库持有） ----
try:  # 私有库（ST-archive）里存在；公开镜像里不存在，此时列表为空
    from redact_secrets import (
        ACCT_LITERALS as _ACCT,
        BANK_LITERALS as _BANK,
        NAME_LITERALS as _NAME,
        USER_LITERALS as _USER,
    )
except ImportError:  # pragma: no cover
    _NAME, _BANK, _ACCT, _USER = [], [], [], []

NAME_LITERALS = list(_NAME)
BANK_LITERALS = list(_BANK)
ACCT_LITERALS = list(_ACCT)
USER_LITERALS = list(_USER)

ALL_LITERALS = sorted(
    NAME_LITERALS + BANK_LITERALS + ACCT_LITERALS + USER_LITERALS,
    key=len,
    reverse=True,
)

# 文件名里才替换的短账号（"1599" 也可能是金额，不能全局替换）
FILENAME_ACCT = [re.compile(r"(?<![\d.])1599(?![\d.])")]

# ---- 2. 键级规则：这些键的值一律不发布 ----
KEY_BLOCK = {
    "holder", "held_by", "acct_no", "account_no", "poems_id", "sid", "rdn",
    "ifua", "tax_id", "npwp", "email", "phone", "address", "account_name_full",
}
KEY_PATH = {"db", "path", "file", "filename", "files", "source_file", "source_files", "dir", "statement_file"}

# ---- 3. 文本级正则 ----
RE_WIN_PATH = re.compile(r"[A-Za-z]:\\(?:[^\\\s\"']+\\)*[^\\\s\"']*")
RE_LONG_PATH = re.compile(r"<local>\\?(?:[^\\\s\"']+\\)*[^\\\s\"']*")  # 清理替换后残留的尾巴
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RE_MOBILE = re.compile(r"(?<![\d])(?:\+?62|0)8\d{7,11}(?![\d])")
RE_BCA = re.compile(r"(?i)\bBCA\b[\s:：-]*\d{6,}")

PLACEHOLDER = "***"


def _basename(path: str) -> str:
    clean = path.replace("/", "\\")
    base = clean.rsplit("\\", 1)[-1]
    for pat in FILENAME_ACCT:
        base = pat.sub(PLACEHOLDER, base)
    return scrub_text(base)


def scrub_text(value: str) -> str:
    if not isinstance(value, str):
        return value
    s = value
    for lit in ALL_LITERALS:
        s = s.replace(lit, PLACEHOLDER)
    s = RE_BCA.sub("BCA " + PLACEHOLDER, s)
    s = RE_WIN_PATH.sub("<local>", s)
    s = RE_EMAIL.sub(PLACEHOLDER + "@" + PLACEHOLDER, s)
    s = RE_MOBILE.sub(PLACEHOLDER, s)
    return s


def redact_payload(obj: Any) -> Any:
    """递归脱敏：返回新对象，不修改入参。"""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in KEY_BLOCK and isinstance(v, (str, int, float)):
                out[k] = PLACEHOLDER
            elif k == "db":
                out[k] = "data/st.db"
            elif k in KEY_PATH and isinstance(v, str):
                out[k] = _basename(v)
            elif k in KEY_PATH and isinstance(v, list):
                out[k] = [_basename(x) if isinstance(x, str) else x for x in v]
            else:
                out[k] = redact_payload(v)
        return out
    if isinstance(obj, list):
        return [redact_payload(x) for x in obj]
    if isinstance(obj, str):
        return scrub_text(obj)
    return obj


# ---- 4. 上线前硬校验：命中任何一条就不许发布 ----
BANNED = [
    ("姓名", NAME_LITERALS),
    ("银行账号", BANK_LITERALS),
    ("券商账号", ACCT_LITERALS),
    ("用户名", USER_LITERALS),
]
BANNED_PATTERNS = [
    ("Windows 路径", re.compile(r"[A-Za-z]:\\Users")),
    ("Windows 路径(转义)", re.compile(r"[A-Za-z]:\\\\[Uu]sers")),
    ("邮箱", RE_EMAIL),
]

SAFE_EMAIL_DOMAINS = ("users.noreply.github.com",)


def find_leaks(text: str) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for label, lits in BANNED:
        for lit in lits:
            if lit in text:
                hits.append((label, lit))
    for label, pat in BANNED_PATTERNS:
        for m in pat.finditer(text):
            frag = m.group(0)[:40]
            if label == "邮箱" and frag.endswith(SAFE_EMAIL_DOMAINS):
                continue  # GitHub 自己的 noreply 地址，不含个人信息
            hits.append((label, frag))
    return hits


def assert_clean(path_text_pairs: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """返回 [(文件, 类别, 片段)]；空列表即干净。"""
    problems: list[tuple[str, str, str]] = []
    for name, text in path_text_pairs:
        for label, frag in find_leaks(text):
            problems.append((name, label, frag))
    return problems


def dump(payload: Any) -> str:
    """统一出口：先脱敏再序列化，保证任何写盘路径都过一遍。"""
    return json.dumps(redact_payload(payload), ensure_ascii=False, separators=(",", ":"), default=str)
