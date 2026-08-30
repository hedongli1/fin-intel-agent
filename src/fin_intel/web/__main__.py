"""Web 管理台独立入口: python -m src.fin_intel.web

默认绑定 127.0.0.1:8620,可用 --host/--port 覆盖。
面板进程自身日志写入 data/web.log(供 /api/logs 尾部浏览)。

示例:
    python -m src.fin_intel.web                    # 默认 127.0.0.1:8620
    python -m src.fin_intel.web --port 9000
    python -m src.fin_intel.web --host 0.0.0.0     # 局域网访问(谨慎,无鉴权)
"""
from __future__ import annotations

import argparse
import sys

import uvicorn
from loguru import logger

from ..report_store import DATA_DIR


def main() -> int:
    parser = argparse.ArgumentParser(prog="fin-intel-web", description="fin-intel-agent Web 管理台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址(默认 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8620, help="监听端口(默认 8620)")
    parser.add_argument("--config", default="config/config.yaml", help="配置文件路径(默认 config/config.yaml)")
    parser.add_argument("--log-file", default=None, help="日志文件路径(默认 <项目>/data/web.log)")
    args = parser.parse_args()

    log_file = args.log_file or str(DATA_DIR / "web.log")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add(log_file, level="INFO", encoding="utf-8", rotation="10 MB", retention=7)

    from .app import create_app

    app = create_app(args.config)
    logger.info("fin-intel Web 管理台启动: http://{}:{}/ (config={})", args.host, args.port, args.config)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
