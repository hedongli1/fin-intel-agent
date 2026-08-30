"""行情/股价采集:ak.stock_zh_a_daily(symbol=sh/sz+code) 取新浪日线。

用最近两根日线计算涨跌幅,输出收盘价/涨跌幅/成交量/成交额/换手率。
注:东财全 A 快照 stock_zh_a_spot_em 在本环境不可达(push2 被远端断开),改走新浪渠道。
"""
from __future__ import annotations

import akshare as ak

from ..schema import CollectorResult
from .base import BaseCollector, call_with_timeout, symbol_with_market


def _num(v: object) -> float:
    return float(str(v).replace("%", "").replace(",", ""))


class QuoteCollector(BaseCollector):
    dimension = "quote"

    def _collect(self, code: str, name: str) -> CollectorResult:
        fn = getattr(ak, "stock_zh_a_daily", None)
        df = call_with_timeout(fn, symbol=symbol_with_market(code), adjust="", timeout=60)
        if df is None or df.empty:
            return CollectorResult(dimension=self.dimension, status="error", error="行情接口返回为空")

        df = df.sort_values("date")
        last = df.iloc[-1]
        if len(df) < 2:
            return CollectorResult(dimension=self.dimension, status="error", error="日线数据不足两根,无法计算涨跌幅")

        prev_close = _num(df.iloc[-2]["close"])
        close = _num(last["close"])
        pct = (close - prev_close) / prev_close * 100 if prev_close else 0.0

        # 新浪日线的 turnover 为 0~1 小数(如 0.0048 = 0.48%),需乘 100 展示
        turnover = _num(last.get("turnover", 0)) * 100
        amount = _num(last.get("amount", 0)) / 1e8
        volume = _num(last.get("volume", 0))

        items = [
            {
                "日期": str(last.get("date", "")),
                "收盘价": f"{close:.2f}",
                "涨跌幅": f"{pct:.2f}%",
                "成交量": f"{volume:,.0f} 股",
                "成交额": f"{amount:.2f} 亿元",
                "换手率": f"{turnover:.2f}%",
            }
        ]
        return CollectorResult(dimension=self.dimension, status="ok", items=items)