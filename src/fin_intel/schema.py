"""数据模型:CollectorResult(采集结果)、StockReport / Report(报告结构)。

维度名在此统一声明,config.py 与 collectors 注册表均引用它,避免重复定义。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 情报维度名(顺序即五维报告展示顺序;ranked_pool 为动态监控池采集器,不在此维度列表中)
KNOWN_DIMENSIONS: tuple[str, ...] = ("quote", "fundamental", "news", "research", "sector")


class CollectorResult(BaseModel):
    """单个采集器(维度)对单只股票的一次采集结果。"""

    dimension: str
    status: Literal["ok", "error"] = "ok"
    items: list[dict] = Field(default_factory=list)  # 结构化要点(逐条成 bullet)
    summary: str = ""  # 自由文本概述(可选,渲染在 items 之前)
    message: str = ""  # 附加说明(预留)
    error: str = ""  # status=error 时的失败原因
    alerts: list[dict] = Field(default_factory=list)  # 异动股及其新闻(ranked_pool 池采集器用)
    boards: dict[str, list[dict]] = Field(default_factory=dict)  # 各排行榜快照(ranked_pool 池采集器用)


class StockReport(BaseModel):
    """单只股票的五维聚合结果。"""

    code: str
    name: str
    dimensions: dict[str, CollectorResult] = Field(default_factory=dict)


class Report(BaseModel):
    """一次完整报告(结构化中间产物,最终渲染为纯文本 Markdown 风格文本)。"""

    kind: Literal["premarket", "aftermarket"]
    date: str
    generated_at: str
    stocks: list[StockReport] = Field(default_factory=list)