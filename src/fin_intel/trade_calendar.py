"""交易日判断(可选):用 ak.tool_trade_date_hist_sina 缓存交易日集合。
判断失败时容错返回 True(宁可多跑不可漏)。
"""
from __future__ import annotations

from datetime import date

import akshare as ak
from loguru import logger

_calendar: set[str] | None = None


def _load() -> set[str]:
    global _calendar
    if _calendar is None:
        fn = getattr(ak, "tool_trade_date_hist_sina", None)
        if fn is None:
            raise RuntimeError("akshare 无 tool_trade_date_hist_sina 接口")
        df = fn()
        col = df.columns[0]
        _calendar = {str(d).replace("-", "") for d in df[col]}
    return _calendar


def is_trading_day(d: date) -> bool:
    try:
        return d.strftime("%Y%m%d") in _load()
    except Exception as exc:  # noqa: BLE001
        logger.warning("交易日判断失败,容错返回 True(宁可多跑不可漏): {}", exc)
        return True


def clear_cache() -> None:
    """清空交易日缓存(测试用)。"""
    global _calendar
    _calendar = None