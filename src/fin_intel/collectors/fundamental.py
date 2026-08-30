"""基本面/财务采集:ak.stock_profile_cninfo(symbol=code) 取巨潮资讯公司概况。
输出所属行业/主营业务/上市日期/注册资金/所属市场等。
注:东财 stock_individual_info_em 在本环境不可达(push2 被远端断开),改走巨潮渠道。
"""
from __future__ import annotations

import akshare as ak

from ..schema import CollectorResult
from .base import BaseCollector, call_with_timeout, extract_record

_INFO_MAPPING: dict[str, list[str]] = {
    "公司名称": ["公司名称"],
    "所属行业": ["所属行业"],
    "主营业务": ["主营业务"],
    "上市日期": ["上市日期"],
    "注册资金": ["注册资金"],
    "所属市场": ["所属市场"],
}


class FundamentalCollector(BaseCollector):
    dimension = "fundamental"

    def _collect(self, code: str, name: str) -> CollectorResult:
        fn = getattr(ak, "stock_profile_cninfo", None)
        df = call_with_timeout(fn, symbol=code, timeout=60)
        if df is None or df.empty:
            return CollectorResult(dimension=self.dimension, status="error", error="基本面接口返回为空")

        rec = df.iloc[0].to_dict()
        items = [extract_record(rec, _INFO_MAPPING)]
        return CollectorResult(dimension=self.dimension, status="ok", items=items)