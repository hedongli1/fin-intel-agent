"""行情/股价采集,两级数据源:
1. ak.stock_zh_a_spot_tx()  腾讯全市场实时快照(优先,28 列英文列名,含估值/市值/资金流/多周期涨幅);
2. ak.stock_zh_a_daily()    新浪日线(fallback,用最近两根日线算涨跌幅)。

注:东财全 A 快照 stock_zh_a_spot_em 在本环境不可达(push2 被远端断开),腾讯快照为实时行情首选。
"""
from __future__ import annotations

import akshare as ak

from ..schema import CollectorResult
from .base import BaseCollector, call_with_timeout, symbol_with_market

# 腾讯全A快照:英文列名 -> 展示标签(单位:金额为亿元,涨跌幅/换手率/振幅为 %)
_TX_FIELDS: list[tuple[str, str]] = [
    ("zxj", "最新价"),
    ("zdf", "涨跌幅"),
    ("zd", "涨跌额"),
    ("zf", "振幅"),
    ("hsl", "换手率"),
    ("lb", "量比"),
    ("turnover", "成交额"),  # 万元
    ("volume", "成交量"),  # 手
    ("pe_ttm", "市盈率TTM"),
    ("pn", "市净率"),
    ("zsz", "总市值"),  # 亿元
    ("ltsz", "流通市值"),  # 亿元
    ("zdf_d5", "5日涨幅"),
    ("zdf_d10", "10日涨幅"),
    ("zdf_d20", "20日涨幅"),
    ("zdf_d60", "60日涨幅"),
    ("zdf_w52", "年涨幅"),
    ("zljlr", "主力净流入"),  # 万元
    ("zllr", "主力流入"),  # 万元
    ("zllc", "主力流出"),  # 万元
]


def _num(v: object) -> float:
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _fmt(v: float, suffix: str = "", nd: int = 2) -> str:
    return f"{v:,.{nd}f}{suffix}"


class QuoteCollector(BaseCollector):
    dimension = "quote"

    def _collect(self, code: str, name: str) -> CollectorResult:
        result = self._collect_tx(code, name)
        if result.status == "ok":
            return result
        # 腾讯快照失败时降级新浪日线
        fallback = self._collect_sina_daily(code, name)
        if fallback.status == "ok":
            return fallback
        return result  # 返回主源错误信息(更明确)

    def _collect_tx(self, code: str, name: str) -> CollectorResult:
        fn = getattr(ak, "stock_zh_a_spot_tx", None)
        df = call_with_timeout(fn, timeout=90)
        if df is None or df.empty:
            return CollectorResult(dimension=self.dimension, status="error", error="腾讯全A快照返回为空")

        row = df[df["code"].astype(str).str.contains(code, na=False)]
        if row.empty:
            return CollectorResult(dimension=self.dimension, status="error", error=f"腾讯快照未找到 {code}")
        rec = row.iloc[0]

        def g(k: str) -> float:
            try:
                return float(str(rec.get(k, 0)).replace(",", "").strip())
            except (TypeError, ValueError):
                return 0.0

        item: dict[str, str] = {"代码": code, "名称": name}
        for col, label in _TX_FIELDS:
            v = g(col)
            if label in ("成交额", "主力净流入", "主力流入", "主力流出"):
                item[label] = _fmt(v / 1e4, " 亿元")  # 万元 -> 亿元
            elif label == "涨跌额":
                item[label] = _fmt(v, " 元")  # 价格差(元),非百分比
            elif label in ("涨跌幅", "振幅", "换手率", "5日涨幅", "10日涨幅", "20日涨幅", "60日涨幅", "年涨幅"):
                item[label] = _fmt(v, "%")
            elif label in ("市盈率TTM", "市净率", "量比"):
                item[label] = _fmt(v)
            elif label in ("总市值", "流通市值"):
                item[label] = _fmt(v, " 亿元")
            elif label == "成交量":
                item[label] = _fmt(v * 100, " 股")  # 手 -> 股
            else:
                item[label] = _fmt(v, " 元")
        return CollectorResult(dimension=self.dimension, status="ok", items=[item])

    def _collect_sina_daily(self, code: str, name: str) -> CollectorResult:
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
                "代码": code,
                "名称": name,
                "日期": str(last.get("date", "")),
                "收盘价": f"{close:.2f} 元",
                "涨跌幅": f"{pct:.2f}%",
                "成交量": f"{volume:,.0f} 股",
                "成交额": f"{amount:.2f} 亿元",
                "换手率": f"{turnover:.2f}%",
            }
        ]
        return CollectorResult(dimension=self.dimension, status="ok", items=items)