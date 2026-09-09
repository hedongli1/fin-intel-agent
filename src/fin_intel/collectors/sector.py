"""行业/板块采集,两级数据源:
1. ak.stock_board_industry_summary_ths  同花顺行业板块涨跌幅榜 topN(板块/涨跌幅/总成交额/净流入/上涨家数/下跌家数/领涨股);
2. ak.stock_sector_spot(indicator="新浪行业") 新浪行业板块(公司家数/平均价格/总成交量/总成交额/领涨股),补充展示;
3. 板块归属:用同花顺行业榜中与目标股名称相近的领涨股回查板块,写入 summary(原雪球接口需 token 已废弃)。

注:东财 stock_board_industry_name_em 在本环境不可达(push2 被远端断开),改走同花顺+新浪渠道。
"""
from __future__ import annotations

from typing import Any

import akshare as ak

from ..schema import CollectorResult
from .base import BaseCollector, call_with_timeout, extract_record


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


class SectorCollector(BaseCollector):
    dimension = "sector"

    def _collect(self, code: str, name: str) -> CollectorResult:
        items: list[dict] = []
        failures: list[str] = []

        # 1) 同花顺行业板块涨幅榜
        try:
            fn_board = getattr(ak, "stock_board_industry_summary_ths", None)
            df = call_with_timeout(fn_board, timeout=60)
            if df is None or df.empty:
                failures.append("同花顺行业榜返回为空")
            else:
                # 涨跌幅可能是带 % 的字符串,先数值化再排序
                df["_pct"] = df["涨跌幅"].map(_num)
                df = df.dropna(subset=["_pct"]).sort_values("_pct", ascending=False)
                for rec in df.head(self.top_n).to_dict("records"):
                    items.append(
                        extract_record(
                            rec,
                            {
                                "板块": ["板块"],
                                "涨跌幅": ["涨跌幅"],
                                "总成交额": ["总成交额"],
                                "净流入": ["净流入"],
                                "上涨家数": ["上涨家数"],
                                "下跌家数": ["下跌家数"],
                                "领涨股": ["领涨股"],
                                "领涨股涨跌幅": ["领涨股-涨跌幅"],
                            },
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"同花顺行业榜({type(exc).__name__}:{exc})")

        # 2) 新浪行业板块(补充:公司家数/平均价格/总成交量)
        try:
            fn_sina = getattr(ak, "stock_sector_spot", None)
            sdf = call_with_timeout(fn_sina, indicator="新浪行业", timeout=60)
            if sdf is not None and not sdf.empty:
                sdf["_pct"] = sdf["涨跌幅"].map(_num)
                sdf = sdf.dropna(subset=["_pct"]).sort_values("_pct", ascending=False)
                for rec in sdf.head(5).to_dict("records"):
                    item = extract_record(
                        rec,
                        {
                            "板块(新浪)": ["板块"],
                            "公司家数": ["公司家数"],
                            "平均价格": ["平均价格"],
                            "涨跌幅": ["涨跌幅"],
                            "总成交额": ["总成交额"],
                            "领涨股": ["股票名称"],
                        },
                    )
                    # 新浪总成交额单位为元,转亿元展示
                    try:
                        item["总成交额"] = f"{float(item['总成交额']) / 1e8:.2f} 亿元"
                    except (TypeError, ValueError):
                        pass
                    items.append(item)
            else:
                failures.append("新浪行业榜返回为空")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"新浪行业榜({type(exc).__name__}:{exc})")

        if not items:
            return CollectorResult(
                dimension=self.dimension,
                status="error",
                error="; ".join(failures) or "行业板块数据源均无数据",
            )

        # 目标股所属板块表现:在同花顺行业榜中按领涨股名匹配目标股
        summary = ""
        try:
            matched = df[df["领涨股"].astype(str) == name]
            if not matched.empty:
                pct_v = _num(matched.iloc[0]["涨跌幅"])
                summary = (
                    f"目标股 {name} 是 {matched.iloc[0]['板块']} 板块领涨股,板块涨跌幅 "
                    f"{pct_v}%"
                    if pct_v is not None
                    else f"目标股 {name} 是 {matched.iloc[0]['板块']} 板块领涨股"
                )
            else:
                summary = f"{name} 未出现在行业涨幅榜领涨股中"
        except Exception as exc:  # noqa: BLE001 —— 板块归属仅锦上添花
            summary = f"板块归属检索失败: {type(exc).__name__}: {exc}"

        if failures:
            summary = (summary + " | " if summary else "") + f"部分来源失败: {'; '.join(failures)}"
        return CollectorResult(dimension=self.dimension, status="ok", items=items, summary=summary)