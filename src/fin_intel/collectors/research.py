"""研报/机构评级采集:ak.stock_research_report_em(symbol=code) 取最近券商研报。
输出最近 N 篇的标题/机构/评级/日期/行业/盈利预测(2026-2028 收益+PE)/PDF链接。
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
            item = extract_record(
                rec,
                {
                    "标题": ["报告名称", "报告标题", "标题"],
                    "机构": ["机构", "评级机构", "研究机构"],
                    "评级": ["东财评级", "最近评级", "评级"],
                    "日期": ["日期", "报告日期"],
                    "行业": ["行业", "所属行业"],
                    "PDF链接": ["报告PDF链接", "PDF链接", "报告链接"],
                },
            )
            # 盈利预测:3 年年份 -> 收益/市盈率
            for year in (2026, 2027, 2028):
                eps = extract_record(rec, {f"eps_{year}": [f"{year}-盈利预测-收益"]}).get(f"eps_{year}", "")
                pe = extract_record(rec, {f"pe_{year}": [f"{year}-盈利预测-市盈率"]}).get(f"pe_{year}", "")
                eps_s = str(eps).strip()
                pe_s = str(pe).strip()
                if eps_s and eps_s.lower() != "nan":
                    item[f"{year}预测收益"] = eps_s
                if pe_s and pe_s.lower() != "nan":
                    item[f"{year}预测PE"] = pe_s
            items.append(item)
        return CollectorResult(dimension=self.dimension, status="ok", items=items)