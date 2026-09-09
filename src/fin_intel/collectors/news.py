"""新闻/公告/舆情采集,聚合公开源(任一失败不影响其余):
1. ak.stock_news_em(symbol=code)        个股新闻(东方财富,可达,含新闻内容全文);
2. ak.stock_info_global_cls(symbol="全部") 财联社电报快讯(市场级,整轮任务缓存一次)。
注:ak.stock_notice_report 于 akshare 1.18.92 抛 KeyError(接口失效),已移除,个股公告由新闻源替代兜底。
"""
from __future__ import annotations

import akshare as ak

from ..schema import CollectorResult
from .base import BaseCollector, call_with_timeout, extract_record


class NewsCollector(BaseCollector):
    dimension = "news"

    def __init__(self, top_n: int = 6) -> None:
        super().__init__(top_n)
        self._cls_cache = None

    def _global_flash(self):
        if self._cls_cache is None:
            fn = getattr(ak, "stock_info_global_cls", None)
            self._cls_cache = call_with_timeout(fn, symbol="全部", timeout=60)
        return self._cls_cache

    def _collect(self, code: str, name: str) -> CollectorResult:
        items: list[dict] = []
        failures: list[str] = []

        # 1) 个股新闻(含内容全文,截断至 120 字)
        try:
            fn = getattr(ak, "stock_news_em", None)
            df = call_with_timeout(fn, symbol=code, timeout=60)
            if df is not None and not df.empty:
                for rec in df.head(self.top_n).to_dict("records"):
                    item = extract_record(
                        rec,
                        {
                            "标题": ["新闻标题", "标题"],
                            "时间": ["发布时间"],
                            "来源": ["文章来源", "来源"],
                            "链接": ["新闻链接", "链接", "网址"],
                        },
                    )
                    content = extract_record(rec, {"内容": ["新闻内容", "内容"]}).get("内容", "")
                    item["摘要"] = str(content).strip()[:120]
                    items.append(item)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"个股新闻({type(exc).__name__}:{exc})")

        # 2) 财联社电报快讯(市场级)
        try:
            cdf = self._global_flash()
            if cdf is not None and not cdf.empty:
                for rec in cdf.head(3).to_dict("records"):
                    # 注意:财联社列名可能带前导空格(如" 标题"),这里按内容提取
                    title = ""
                    for k in rec:
                        if k.strip() == "标题":
                            title = str(rec[k])
                            break
                    if not title:
                        for k in rec:
                            if k.strip() == "内容":
                                title = str(rec[k])
                                break
                    t = ""
                    for k in rec:
                        if k.strip() == "发布时间" or k.strip() == "发布日期":
                            t = str(rec[k])
                            break
                    items.append({"财联社快讯": title.strip()[:80], "时间": t})
        except Exception as exc:  # noqa: BLE001
            failures.append(f"财联社快讯({type(exc).__name__}:{exc})")

        if not items:
            return CollectorResult(
                dimension=self.dimension,
                status="error",
                error="; ".join(failures) or "新闻源均无数据",
            )
        summary = f"部分来源失败: {'; '.join(failures)}" if failures else ""
        return CollectorResult(dimension=self.dimension, status="ok", items=items, summary=summary)