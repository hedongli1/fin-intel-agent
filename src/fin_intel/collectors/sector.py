"""行业/板块采集:
1. ak.stock_board_industry_summary_ths 取同花顺行业板块涨跌幅榜 topN;
2. 用 ak.stock_individual_basic_info_xq 的 affiliate_industry.ind_name(同花顺 BK 板块名)
   回查目标股所属板块表现写入 summary。
注:东财 stock_board_industry_name_em 在本环境不可达(push2 被远端断开),改走同花顺渠道。
"""
from __future__ import annotations

from typing import Any

import akshare as ak

from ..schema import CollectorResult
from .base import BaseCollector, call_with_timeout, extract_record, symbol_with_market


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


class SectorCollector(BaseCollector):
    dimension = "sector"

    def _collect(self, code: str, name: str) -> CollectorResult:
        fn_board = getattr(ak, "stock_board_industry_summary_ths", None)
        df = call_with_timeout(fn_board, timeout=60)
        if df is None or df.empty:
            return CollectorResult(dimension=self.dimension, status="error", error="行业板块接口返回为空")

        # 涨跌幅可能是带 % 的字符串,先数值化再排序
        df["_pct"] = df["涨跌幅"].map(_num)
        df = df.dropna(subset=["_pct"]).sort_values("_pct", ascending=False)

        items: list[dict] = []
        for rec in df.head(self.top_n).to_dict("records"):
            items.append(
                extract_record(
                    rec,
                    {
                        "板块": ["板块"],
                        "涨跌幅": ["涨跌幅"],
                        "领涨股": ["领涨股"],
                    },
                )
            )

        # 目标股所属板块表现(雪球 affiliate_industry 与同花顺板块命名同源)
        summary = ""
        try:
            xq = getattr(ak, "stock_individual_basic_info_xq", None)
            if xq is not None and "板块" in df.columns:
                info_df = call_with_timeout(xq, symbol=symbol_with_market(code), timeout=60)
                info = dict(zip(info_df["item"], info_df["value"]))
                aff = info.get("affiliate_industry")
                ind: str | None = None
                if isinstance(aff, dict):
                    ind = aff.get("ind_name")
                elif isinstance(aff, str):
                    ind = aff
                if ind:
                    matched = df[df["板块"] == ind]
                    if not matched.empty:
                        pct_v = _num(matched.iloc[0]["涨跌幅"])
                        summary = f"所属板块 {ind} 涨跌幅 {pct_v}%(见涨幅榜)" if pct_v is not None else f"所属板块: {ind}"
                    else:
                        summary = f"所属板块: {ind}(未出现在上涨前列)"
        except Exception as exc:  # noqa: BLE001 —— 板块归属仅锦上添花
            summary = f"板块归属检索失败: {type(exc).__name__}: {exc}"

        return CollectorResult(dimension=self.dimension, status="ok", items=items, summary=summary)