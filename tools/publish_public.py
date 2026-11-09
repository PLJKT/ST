# -*- coding: utf-8 -*-
r"""把私有开发库发布成公开镜像：只有代码 + 脱敏站点。

私有库（ST-archive）负责：原始单据 PDF、st.db、processed/raw/snapshot、真实字面量。
公开库（ST）负责：app/ web/ tools/ server.py run.bat requirements.txt、
            清洗后的 README / DEVELOPMENT、脱敏后的 docs/（GitHub Pages 源）、
            data/universe.csv（股票池，非敏感）。

用法（仓库根目录）：
    python tools/publish_public.py            # 只生成 ../ST-public
    python tools/publish_public.py --push     # 生成 + 提交 + 推送
    python tools/publish_public.py --dest D:\tmp\st-pub

安全约束：
  - tools/redact_secrets.py **永不进入**公开镜像；
  - 生成后对全部文本做一次 assert_clean，命中即中止，不写盘、不推送。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from redact import assert_clean, scrub_text  # noqa: E402

try:  # 私有库里存在；公开镜像里没有，此时不做文档替换
    from redact_secrets import DOC_TOKENS as _DOC_TOKENS
    from redact_secrets import DOC_WORD_TOKENS as _DOC_WORD_TOKENS
except ImportError:  # pragma: no cover
    _DOC_TOKENS, _DOC_WORD_TOKENS = [], []

DEFAULT_DEST = ROOT.parent / "ST-public"
GIT = r"C:\Program Files\Git\cmd\git.exe"
REMOTE = "https://github.com/PLJKT/ST.git"

COPY_DIRS = ["app", "web", "tools", "docs"]
COPY_FILES = ["server.py", "run.bat", "requirements.txt", "README.md", "DEVELOPMENT.md", ".gitattributes"]
NEVER_COPY = {"tools/redact_secrets.py"}

PUBLIC_HEADER = """<!-- 公开镜像说明（由 tools/publish_public.py 生成，勿手改本文件） -->
> **本仓库只发布「代码 + 脱敏站点」。**
> 原始单据（65 份 PDF）、`st.db`、CSV 快照与完整性清单在**私有仓库 `ST-archive`**，需授权访问。
> 站点数字是真实仓位与损益，但已隐去姓名、银行账号、券商账号、IFUA/SID 与本机路径；
> 导出前由 `tools/redact_audit.py` 硬校验，命中任何一条即禁止发布。

"""

PUBLIC_GITIGNORE = """# 公开镜像：只跟踪代码与脱敏站点
server.out.log
server.err.log
*.pyc
__pycache__/
.env
data/st.db
data/st.db-wal
data/st.db-shm
data/source/
data/raw/
data/processed/
data/snapshot/
"""

PUBLIC_DATA_README = """# data/（公开镜像）

本目录在公开仓库里只有 `universe.csv`（股票池，非敏感）。

原始数据全部在私有仓库 `ST-archive/data/`：单据 PDF、转文本、归一化 JSON、
`st.db`、表快照 `snapshot/*.csv`、`manifest.json`（每个文件的 sha256）。

为什么拆开：PDF 里带姓名、地址、银行/券商账号，无法可靠打码，只能整包私有。
公开站点的数字由 `tools/export_static.py` 经 `tools/redact.py` 脱敏后生成，
每次发布前跑 `python tools/redact_audit.py`，必须 PASS。
"""


def sanitize(text: str) -> str:
    for token, repl in _DOC_TOKENS:
        text = text.replace(token, repl)
    for word in _DOC_WORD_TOKENS:
        text = re.sub(rf"(?<![\d.]){re.escape(word)}(?![\d.])", "账户", text)
    return scrub_text(text)


def copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--push", action="store_true", help="生成后 git add/commit/push")
    args = ap.parse_args()

    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    # 保留 .git（公开仓库是要持续推送的），只清掉上一次生成的内容
    managed = ["app", "web", "tools", "docs", "data"]
    managed_files = COPY_FILES + [".gitignore"]
    for name in managed:
        p = dest / name
        if p.is_dir():
            shutil.rmtree(p)
    for name in managed_files:
        p = dest / name
        if p.is_file():
            p.unlink()

    for d in COPY_DIRS:
        src = ROOT / d
        if not src.exists():
            print(f"  跳过（不存在）：{d}")
            continue
        for item in src.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(ROOT).as_posix()
            if rel in NEVER_COPY or "__pycache__" in rel or item.suffix == ".pyc":
                continue
            copy_file(item, dest / rel)

    for f in COPY_FILES:
        src = ROOT / f
        if src.exists():
            copy_file(src, dest / f)

    for doc in ["README.md", "DEVELOPMENT.md"]:
        p = dest / doc
        if p.exists():
            p.write_text(PUBLIC_HEADER + sanitize(p.read_text(encoding="utf-8")), encoding="utf-8")
            print(f"  已清洗并加公开说明：{doc}")

    (dest / "data").mkdir(exist_ok=True)
    if (ROOT / "data" / "universe.csv").exists():
        copy_file(ROOT / "data" / "universe.csv", dest / "data" / "universe.csv")
    (dest / "data" / "README.md").write_text(PUBLIC_DATA_README, encoding="utf-8")

    (dest / ".gitignore").write_text(PUBLIC_GITIGNORE, encoding="utf-8")

    pairs: list[tuple[str, str]] = []
    for p in dest.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".py", ".js", ".html", ".css", ".json", ".md", ".txt", ".bat", ".csv"}:
            pairs.append((str(p.relative_to(dest)), p.read_text(encoding="utf-8", errors="ignore")))
    problems = assert_clean(pairs)
    if problems:
        for name, label, frag in problems[:20]:
            print(f"  [BLOCK] {label} {name} -> {frag}")
        print(f"中止：镜像里有 {len(problems)} 处身份信息，未推送。")
        return 1

    counted = [p for p in dest.rglob("*") if p.is_file() and ".git" not in p.parts]
    files = len(counted)
    size = sum(p.stat().st_size for p in counted)
    print(f"镜像已生成：{dest}  （{files} files / {size/1024:.0f} KB）")

    if not args.push:
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not (dest / ".git").exists():
        subprocess.run([GIT, "init", "-b", "main"], cwd=dest, check=True)
        subprocess.run([GIT, "remote", "add", "origin", REMOTE], cwd=dest, check=True)
    cmds = [
        [GIT, "add", "-A"],
        [GIT, "-c", "user.name=PLJKT", "-c", "user.email=pljkt@users.noreply.github.com",
         "commit", "-m", f"publish: 公开镜像（代码 + 脱敏站点）{stamp}"],
        [GIT, "-c", "credential.helper=", "-c", "credential.username=x-access-token", "push", "origin", "main"],
    ]
    for cmd in cmds:
        res = subprocess.run(cmd, cwd=dest, capture_output=True, text=True)
        print(f"  $ {' '.join(cmd[1:3])} -> rc={res.returncode}")
        if res.returncode != 0:
            print((res.stdout + res.stderr).strip()[-800:])
            return 1
    print("已推送公开镜像。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
