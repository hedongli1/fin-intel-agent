"""collector 抽象基类与公共工具。

约定:
- 每个具体 collector 实现 ``_collect(code, name) -> CollectorResult``,只返回成功结果。
- 基类 ``collect()`` 统一做:耗时统计 + 全量异常捕获(异常隔离为硬性要求)。
- ``call_with_timeout`` 在守护线程里跑 akshare 接口,超时即抛 TimeoutError,防止个别慢接口卡死任务。
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

from loguru import logger

from ..schema import CollectorResult


def call_with_timeout(fn: Callable[..., Any] | None, *args: Any, timeout: float = 60.0, **kwargs: Any) -> Any:
    """在守护线程中调用 akshare 接口,超时抛 TimeoutError。

    akshare 部分接口(如公告)无内置超时,可能长时间阻塞;用守护线程 + join(timeout)
    兜底,既避阻塞主流程,又不因非守护线程残留而影响进程退出。
    """
    if fn is None:
        raise RuntimeError("akshare 接口不存在,可能因版本升级而改名或下线(已用 getattr 探测)")

    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 —— 转交给调用方统一处理
            box["error"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"接口调用超时(>{timeout}s): {getattr(fn, '__name__', fn)}")
    if "error" in box:
        raise box["error"]
    return box.get("result")


def symbol_with_market(code: str) -> str:
    """6 位代码 -> 带市场前缀的完整代码(新浪/雪球等接口需要 sh/sz 前缀)。

    规则:6/9 开头判为沪市(sh),其余判为深市(sz);覆盖 A 股主板/创业板/科创板常见情形。
    """
    return ("sh" if code.startswith(("6", "9", "5")) else "sz") + code


def extract_record(record: dict, mapping: dict[str, list[str]]) -> dict:
    """按 label -> 候选列名 从一条记录里抽取字段,首个命中的非空值优先。

    用于把 akshare 的中文列名规整为稳定的展示标签,兼容不同版本列名微调。
    """
    out: dict[str, Any] = {}
    for label, candidates in mapping.items():
        val: Any = None
        for c in candidates:
            if c in record and record[c] is not None:
                val = record[c]
                break
        out[label] = "" if val is None else val
    return out


class BaseCollector(ABC):
    dimension: str = "base"

    def __init__(self, top_n: int = 6) -> None:
        self.top_n = top_n  # 单维度最多产出条数

    def collect(self, code: str, name: str) -> CollectorResult:
        start = time.perf_counter()
        try:
            result = self._collect(code, name)
        except Exception as exc:  # noqa: BLE001 —— 任何异常不得上抛拖垮整轮任务
            result = CollectorResult(
                dimension=self.dimension,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
            )
        result.dimension = self.dimension
        elapsed = time.perf_counter() - start
        logger.info(
            "collector={} code={} status={} elapsed={:.2f}s",
            self.dimension,
            code,
            result.status,
            elapsed,
        )
        return result

    @abstractmethod
    def _collect(self, code: str, name: str) -> CollectorResult:
        """子类实现具体采集;只返回成功结果,异常由基类收集。"""
        raise NotImplementedError