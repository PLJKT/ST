# -*- coding: utf-8 -*-
"""每日自动同步：富途 OpenAPI → 台账 → 静态站 → 双仓库推送。

一条命令跑完整条链，任何一步失败即停，不会把半成品发布出去：
  1. tools/futu_collect.py   拉最新流水/持仓快照，写 data/futu_api/latest.json
  2. tools/update_from_api.py 把新流水与持仓并入 data/st.db（幂等）
  3. 确保本机 8300 服务在跑（当前源码起的进程）
  4. tools/export_static.py   重算指标、导出 docs/
  5. tools/redact_audit.py    脱敏扫描，必须 PASS
  6. tools/publish_public.py  生成公开镜像
  7. git commit + push（私有 ST-archive、公开 ST 各一次）

用法：
    python -X utf8 tools/daily_sync.py                # 正式跑
    python -X utf8 tools/daily_sync.py --dry-run      # 只拉取与核对，不写不发布
    python -X utf8 tools/daily_sync.py --no-push      # 本地更新，不推远端

日志：data/logs/daily_sync_YYYYMMDD_HHMMSS.log，状态：data/logs/last_status.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = Path(os.environ.get("ST_PUBLIC", str(ROOT.parent / "ST-public")))
PY = Path(sys.executable)
GIT = os.environ.get("ST_GIT", r"C:\Program Files\Git\cmd\git.exe")
BASE = os.environ.get("ST_BASE", "http://127.0.0.1:8300")

STATE = ROOT / "data" / "logs"
STATE.mkdir(parents=True, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
LOGFILE = STATE / f"daily_sync_{stamp}.log"


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(args: list[str], timeout: int = 600) -> tuple[int, str]:
    log("执行：" + " ".join(str(a) for a in args))
    p = subprocess.run([str(a) for a in args], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.splitlines():
        if any(k in line for k in ("完成：", "已写入", "待补", "PASS", "跳过", "镜像已生成", "失败")):
            log("  " + line.strip())
    return p.returncode, out


def service_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/api/metrics", timeout=90) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_service() -> bool:
    if service_up():
        log("8300 服务已在跑")
        return True
    log("8300 未监听，用当前源码启动")
    subprocess.Popen([str(PY), "-X", "utf8", "server.py"], cwd=str(ROOT),
                     stdout=open(STATE / "server.out.log", "a", encoding="utf-8"),
                     stderr=open(STATE / "server.err.log", "a", encoding="utf-8"),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(30):
        time.sleep(3)
        if service_up():
            log("服务已就绪")
            return True
    log("服务 90 秒内没有起来")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--since", default=os.environ.get("ST_SYNC_SINCE", "2024-08-01"))
    args = ap.parse_args()

    log(f"开始每日同步{'（dry-run）' if args.dry_run else ''}")
    status = {"started": datetime.now().isoformat(timespec="seconds"), "ok": False}

    # 1. 拉取
    rc, out = run([PY, "-X", "utf8", "tools/futu_collect.py", "--since", args.since], 420)
    if rc != 0:
        log("采集失败（通常是 OAuth token 过期），本次不动数据、不发布")
        status["error"] = "collect_failed"
        (STATE / "last_status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        return 1

    # 2. 入账
    if args.dry_run:
        rc, _ = run([PY, "-X", "utf8", "tools/update_from_api.py", "--dry-run"], 180)
        if rc != 0:
            status["error"] = "merge_dry_failed"
            return 1
    else:
        rc, _ = run([PY, "-X", "utf8", "tools/update_from_api.py"], 180)
        if rc != 0:
            log("入账失败，停止")
            status["error"] = "merge_failed"
            (STATE / "last_status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
            return 1
    if args.dry_run:
        log("dry-run 结束，未写库、未发布")
        return 0

    # 3-4. 服务 + 导出
    if not ensure_service():
        status["error"] = "server_down"
        return 1
    rc, out = run([PY, "-X", "utf8", "tools/export_static.py", "--universe-limit", "65"], 900)
    if rc != 0 or "失败 0 个" not in out:
        log("导出不完整，停止（不发布）")
        status["error"] = "export_failed"
        (STATE / "last_status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        return 1

    # 5. 脱敏
    rc, out = run([PY, "-X", "utf8", "tools/redact_audit.py"], 300)
    if rc != 0 or "PASS" not in out:
        log("脱敏扫描未通过，停止（不发布）")
        status["error"] = "redact_failed"
        (STATE / "last_status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        return 1

    # 6. 公开镜像
    rc, _ = run([PY, "-X", "utf8", "tools/publish_public.py"], 300)
    if rc != 0:
        log("公开镜像生成失败，停止")
        status["error"] = "publish_failed"
        return 1

    # 7. 双仓库提交推送
    if not args.no_push:
        d = datetime.now().strftime("%Y-%m-%d %H:%M")
        for repo, msg in ((ROOT, f"sync: futu daily update {d}"),
                          (PUBLIC, f"publish: futu daily update {d}")):
            if not repo.exists():
                log(f"跳过 {repo}（不存在）")
                continue
            run([GIT, "-C", repo, "add", "-A"], 300)
            rc, out = run([GIT, "-C", repo, "commit", "-m", msg], 300)
            if "nothing to commit" in out or "无需提交" in out:
                log(f"{repo.name}：没有变化，不提交")
            run([GIT, "-C", repo, "push"], 300)
        log("已推送到 GitHub，Pages 约 60–90 秒后生效")

    # 收尾状态
    try:
        sys.path.insert(0, str(ROOT))
        from app import db, metrics  # noqa: E402
        r = metrics.key_metrics().get("recon") or {}
        snap = r.get("positions_snapshot")
        status.update({
            "ok": True,
            "positions_snapshot": snap,
            "cash_broker_usd": r.get("cash_broker_usd"),
            "mv_broker": r.get("mv_broker"),
            "cash_gap": r.get("cash_gap"),
            "total_pnl_est": r.get("total_pnl_est"),
            "rows": db.one("SELECT COUNT(*) AS n FROM cash_flows WHERE account_id='futu_main'")["n"],
        })
    except Exception as e:  # 状态写失败不影响同步本身
        log(f"状态汇总失败：{e}")
    status["finished"] = datetime.now().isoformat(timespec="seconds")
    (STATE / "last_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"完成：快照 {status.get('positions_snapshot')}，现金 {status.get('cash_broker_usd')}，"
        f"市值 {status.get('mv_broker')}，现金差 {status.get('cash_gap')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
