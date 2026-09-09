"""基本面/财务采集,两级数据源:
1. ak.stock_profile_cninfo(symbol=code)            巨潮资讯公司概况(行业/主营/上市日期等);
2. ak.stock_financial_abstract(symbol=code)        巨潮财务摘要:80 行指标 x 105 期(含 ROE/毛利率/净利率/资产负债率/每股收益/归母净利/营收)。

注:东财 stock_individual_info_em 在本环境不可达(push2 被远端断开),改走巨潮渠道。
财务摘要接口返回的列名/指标名为 GBK 字节被误读为 latin-1,需 encode('latin-1').decode('gbk') 修正;
指标按固定行号定位(与接口返回顺序一致,已在多只股票上验证稳定)。
"""
from __future__ import annotations

from typing import Any

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

# 财务摘要指标行号(0-based,按接口返回顺序): (行号, 标签)
_FIN_ROWS: list[tuple[int, str]] = [
    (0, "归母净利润"),
    (1, "营业总收入"),
    (4, "扣非净利润"),
    (5, "股东权益"),
    (7, "经营现金流净额"),
    (8, "基本每股收益"),
    (9, "每股净资产"),
    (11, "净资产收益率ROE"),
    (12, "总资产报酬率ROA"),
    (13, "毛利率"),
    (14, "销售净利率"),
    (16, "资产负债率"),
]


def _fix_gbk(v: Any) -> Any:
    """修正 akshare 财务摘要接口的 GBK->latin-1 乱码(列名/指标名)。"""
    if not isinstance(v, str):
        return v
    try:
        return v.encode("latin-1").decode("gbk")
    except Exception:  # noqa: BLE001 —— 非乱码字符串原样返回
        return v


def _fmt_num(v: Any, nd: int = 2) -> str:
    try:
        return f"{float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


class FundamentalCollector(BaseCollector):
    dimension = "fundamental"

    def _collect(self, code: str, name: str) -> CollectorResult:
        items: list[dict] = []
        failures: list[str] = []

        # 1) 公司概况
        try:
            fn = getattr(ak, "stock_profile_cninfo", None)
            df = call_with_timeout(fn, symbol=code, timeout=60)
            if df is not None and not df.empty:
                rec = df.iloc[0].to_dict()
                items.append(extract_record(rec, _INFO_MAPPING))
            else:
                failures.append("公司概况返回为空")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"公司概况({type(exc).__name__}:{exc})")

        # 2) 财务指标摘要
        try:
            fn = getattr(ak, "stock_financial_abstract", None)
            df = call_with_timeout(fn, symbol=code, timeout=60)
            if df is not None and not df.empty:
                ind_row = df.iloc[:, 1].astype(str).tolist()
                period_cols = [str(c) for c in df.columns[2:]]
                # 修正列名(期数),并保证从左到右为最新->最旧
                periods = []
                for c in period_cols:
                    fixed = _fix_gbk(c)
                    periods.append(fixed)
                rec: dict[str, str] = {"财务摘要期间": periods[0] if periods else ""}
                for row_no, label in _FIN_ROWS:
                    if row_no >= len(df):
                        continue
                    vals = df.iloc[row_no].tolist()[2:]
                    cur = vals[0] if vals else None  # 最新一期
                    prev = vals[1] if len(vals) > 1 else None  # 上一期
                    # 金额类指标(行 0/1/4/5/7)单位为元,转亿元展示;比率类(11-16)直接展示 %
                    try:
                        cur_f = float(cur) if cur is not None else None
                        prev_f = float(prev) if prev is not None else None
                    except (TypeError, ValueError):
                        cur_f, prev_f = None, None

                    def _money(v: float) -> str:
                        return f"{v / 1e8:,.2f} 亿元"

                    def _pct(v: float) -> str:
                        return f"{v:.2f}%"

                    def _raw(v: float) -> str:
                        return f"{v:.4f}"

                    if row_no in (0, 1, 4, 5, 7):
                        rec[label] = _money(cur_f) if cur_f is not None and cur_f == cur_f else "-"
                    elif row_no in (11, 12, 13, 14, 16):
                        rec[label] = _pct(cur_f) if cur_f is not None and cur_f == cur_f else "-"
                    else:
                        rec[label] = _raw(cur_f) if cur_f is not None and cur_f == cur_f else "-"
                    if prev_f is not None and prev_f == prev_f:  # 非nan
                        if row_no in (0, 1, 4, 5, 7):
                            rec[f"{label}上期"] = _money(prev_f)
                        elif row_no in (11, 12, 13, 14, 16):
                            rec[f"{label}上期"] = _pct(prev_f)
                        else:
                            rec[f"{label}上期"] = _raw(prev_f)
                items.append(rec)
            else:
                failures.append("财务摘要返回为空")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"财务摘要({type(exc).__name__}:{exc})")

        if not items:
            return CollectorResult(
                dimension=self.dimension,
                status="error",
                error="; ".join(failures) or "基本面数据源均无数据",
            )
        summary = f"部分来源失败: {'; '.join(failures)}" if failures else ""
        return CollectorResult(dimension=self.dimension, status="ok", items=items, summary=summary)