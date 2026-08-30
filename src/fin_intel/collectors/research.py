"""研报/机构评级采集:ak.stock_research_report_em(symbol=code) 取最近券商研报。
输出最近 N 篇的标题/机构/评级/日期。
"""
from __future__ import annotations

import akshare as ak

from ..schema import CollectorResult
from .base import BaseCollector, call_with_timeout, extract_record


class ResearchCollector(BaseCollector):
    dimension = "research"

    def _collect(self, code: str, name: str) -> CollectorResult:
        fn = getattr(ak, "stock_research_report_em", None)
        df = call_with_timeout(fn, symbol=code, timeout=60)
        if df is None or df.empty:
            return CollectorResult(dimension=self.dimension, status="error", error="研报接口返回为空")

        if "日期" in df.columns:
            try:
                df = df.sort_values("日期", ascending=False)
            except Exception:  # noqa: BLE001 —— 日期列格式不统一时保持原序
                pass

        items: list[dict] = []
        for rec in df.head(self.top_n).to_dict("records"):
            items.append(
                extract_record(
                    rec,
                    {
                        "标题": ["报告名称", "报告标题", "标题"],
                        "机构": ["机构", "评级机构", "研究机构"],
                        "评级": ["东财评级", "最近评级", "评级"],
                        "日期": ["日期", "报告日期"],
                    },
                )
            )
        return CollectorResult(dimension=self.dimension, status="ok", items=items)