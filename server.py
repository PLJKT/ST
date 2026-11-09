# -*- coding: utf-8 -*-
"""启动入口：python server.py"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from app import config  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    print(f"ST v5 -> http://{args.host}:{args.port}")
    # 用 import string 启动，避免 reload=True 时抛 SystemExit(3)
    uvicorn.run("app.main:app", host=args.host, port=args.port,
                reload=args.reload, reload_dirs=[str(config.ROOT / "app")] if args.reload else None)


if __name__ == "__main__":
    main()
