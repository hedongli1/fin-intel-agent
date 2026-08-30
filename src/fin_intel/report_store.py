"""报告落盘存档 + 推送/触发记录(JSONL)。

- 报告存档: data/reports/<kind>/<date>/<ts>_<kind>.txt
- 推送/触发记录: data/push_records.jsonl(run_report 每次执行追加一行)
项目根由本模块所在位置向上推导:<root>/src/fin_intel/report_store.py -> <root>。
Web 管理台与 CLI/常驻调度共用本模块,保证数据同一份。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from loguru import logger

# 项目根 = 本文件向上 2 级( report_store.py -> fin_intel -> src -> 项目根 )
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DATA_DIR: Path = PROJECT_ROOT / "data"
REPORTS_DIR: Path = DATA_DIR / "reports"
PUSH_RECORDS_FILE: Path = DATA_DIR / "push_records.jsonl"

_lock = threading.Lock()


def ensure_dirs() -> None:
    """确保 data 相关目录存在。"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    PUSH_RECORDS_FILE.parent.mkdir(parents=True, exist_ok=True)


def save_report(kind: str, text: str, source: str = "cli") -> dict:
    """把报告文本落盘存档,返回摘要(含相对 data 目录的 rel_path)。"""
    ensure_dirs()
    now = datetime.now()
    date_dir = REPORTS_DIR / kind / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{now.strftime('%Y%m%d_%H%M%S')}_{kind}.txt"
    fpath = date_dir / fname
    with _lock:
        fpath.write_text(text, encoding="utf-8")
    rel = fpath.relative_to(DATA_DIR).as_posix()
    logger.info("报告已落盘: {} ({} 字符)", rel, len(text))
    return {"kind": kind, "rel_path": rel, "abs_path": str(fpath), "length": len(text)}


def record_push(record: dict) -> None:
    """追加一条推送/触发记录到 JSONL。"""
    ensure_dirs()
    with _lock:
        with PUSH_RECORDS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def list_reports(kind: str | None = None, limit: int = 50) -> list[dict]:
    """按修改时间倒序列出存档报告。kind 为空则列出全部。"""
    ensure_dirs()
    base = REPORTS_DIR / kind if kind else REPORTS_DIR
    if not base.exists():
        return []
    reports: list[dict] = []
    for p in sorted(base.rglob("*.txt"), key=lambda x: x.stat().st_mtime, reverse=True):
        rel = p.relative_to(DATA_DIR).as_posix()
        st = p.stat()
        reports.append(
            {
                "kind": p.parent.parent.name,
                "date": p.parent.name,
                "name": p.name,
                "rel_path": rel,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        if len(reports) >= limit:
            break
    return reports


def read_report(rel_path: str) -> str:
    """读取报告全文。仅允许 data 目录内的相对路径,防目录穿越。"""
    ensure_dirs()
    target = (DATA_DIR / rel_path).resolve()
    if not target.is_relative_to(DATA_DIR.resolve()):
        raise ValueError(f"非法报告路径: {rel_path}")
    return target.read_text(encoding="utf-8")


def list_push_records(limit: int = 100) -> list[dict]:
    """倒序列出最近推送/触发记录。"""
    ensure_dirs()
    if not PUSH_RECORDS_FILE.exists():
        return []
    records: list[dict] = []
    with _lock:
        lines = PUSH_RECORDS_FILE.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(records) >= limit:
            break
    return records
