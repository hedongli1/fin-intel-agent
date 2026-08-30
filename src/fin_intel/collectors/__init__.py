"""collector 注册表:按维度名取 collector 类并实例化。"""
from __future__ import annotations

from ..schema import KNOWN_DIMENSIONS
from .base import BaseCollector
from .fundamental import FundamentalCollector
from .news import NewsCollector
from .quote import QuoteCollector
from .rank import RankedPoolCollector
from .research import ResearchCollector
from .sector import SectorCollector

_COLLECTOR_CLASSES: dict[str, type[BaseCollector]] = {
    "quote": QuoteCollector,
    "fundamental": FundamentalCollector,
    "news": NewsCollector,
    "research": ResearchCollector,
    "sector": SectorCollector,
    "ranked_pool": RankedPoolCollector,
}


def get_collector(name: str) -> BaseCollector | None:
    """每次调用返回新实例,保证无跨轮次状态残留(行情快照/快讯缓存在单轮内复用)。"""
    cls = _COLLECTOR_CLASSES.get(name)
    return cls() if cls else None


def available_dimensions() -> tuple[str, ...]:
    return KNOWN_DIMENSIONS