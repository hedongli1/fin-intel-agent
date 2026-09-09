"""动态排行榜监控池采集器(五榜并集去重)。

数据源(按优先级):
1. ak.stock_zh_a_spot_em()   东财全市场实时快照(含成交额/涨跌幅/换手率/量比/总市值);
2. ak.stock_zh_a_spot_tx()   腾讯全市场实时快照(fallback,push2.eastmoney.com 不可达时自动降级,
                             英文列名 code/name/zxj/zdf/turnover/hsl/lb/zsz/pe_ttm 等)。

流程:
1. 拉一次全A实时快照;
2. 按 ranked_pool.rank_by_list 中每个榜单口径分别降序取前 top_n 名,得到各榜快照(boards);
3. 各榜前列股票取并集去重,得到动态监控池(items);超过 max_pool_size 时按
   “命中榜数降序 + 成交额降序”截断;
4. 对池内 |涨跌幅| >= alert_pct 的异动股逐只调 ak.stock_news_em 取最新新闻(alerts);
   新闻接口缺失/失败时仅保留异动标记、跳过该股新闻,保证整体不崩溃。

注:本采集器对快照做双源降级,任一源可用即继续;两源都不可达才返回 status=error。
"""
from __future__ import annotations

from typing import Any

import akshare as ak
import pandas as pd

from ..schema import CollectorResult
from .base import BaseCollector, call_with_timeout, extract_record

# 榜单口径 -> 东财全A快照中文列名(涨幅/涨跌幅 为同义别名,统一映射到“涨跌幅”列)
RANK_COLUMN: dict[str, str] = {
    "成交额": "成交额",
    "涨幅": "涨跌幅",
    "涨跌幅": "涨跌幅",
    "换手率": "换手率",
    "量比": "量比",
    "总市值": "总市值",
}

# 榜单口径 -> 报告中展示的榜名
BOARD_LABEL: dict[str, str] = {
    "成交额": "成交额榜",
    "涨幅": "涨幅榜",
    "涨跌幅": "涨幅榜",
    "换手率": "换手率榜",
    "量比": "量比榜",
    "总市值": "总市值榜",
}

_NEWS_MAPPING: dict[str, list[str]] = {
    "标题": ["新闻标题", "标题"],
    "来源": ["文章来源", "来源"],
    "时间": ["发布时间", "时间"],
}

# 腾讯快照英文列名 -> 统一中文列名(与东财快照对齐,便于后续榜单处理)
_TX_COLUMNS: dict[str, str] = {
    "code": "代码",
    "name": "名称",
    "zxj": "最新价",
    "zdf": "涨跌幅",
    "turnover": "成交额",  # 万元
    "hsl": "换手率",
    "lb": "量比",
    "zsz": "总市值",  # 亿元
    "pe_ttm": "市盈率TTM",
    "pn": "市净率",
    "zf": "振幅",
    "zd": "涨跌额",
    "ltsz": "流通市值",
}


def _num(v: Any) -> float:
    """把 akshare 返回的数值(可能带 % / 逗号 / NaN)安全转 float,失败或 NaN 返回 0.0。"""
    if v is None:
        return 0.0
    s = str(v).replace("%", "").replace(",", "").strip()
    try:
        f = float(s)
    except (TypeError, ValueError):
        return 0.0
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
        return 0.0
    return f


def _snapshot_em() -> pd.DataFrame:
    """东财全A实时快照(中文列名,成交额/总市值为元)。"""
    fn = getattr(ak, "stock_zh_a_spot_em", None)
    if fn is None:
        raise RuntimeError("akshare 缺少 stock_zh_a_spot_em 接口")
    df = call_with_timeout(fn, timeout=90)
    if df is None or df.empty:
        raise RuntimeError("东财全A实时快照返回为空")
    return df


def _snapshot_tx() -> pd.DataFrame:
    """腾讯全A实时快照(英文列名) -> 统一中文列名。

    单位换算:成交额 万元->元,总市值 亿元->元,以便与东财快照同一套榜单/展示逻辑。
    """
    fn = getattr(ak, "stock_zh_a_spot_tx", None)
    if fn is None:
        raise RuntimeError("akshare 缺少 stock_zh_a_spot_tx 接口")
    df = call_with_timeout(fn, timeout=90)
    if df is None or df.empty:
        raise RuntimeError("腾讯全A实时快照返回为空")

    df = df.rename(columns=_TX_COLUMNS)
    keep = [c for c in ("代码", "名称", "最新价", "涨跌幅", "成交额", "换手率", "量比", "总市值") if c in df.columns]
    df = df[keep].copy()
    # 代码去市场前缀(东财格式为纯 6 位)
    df["代码"] = df["代码"].astype(str).str.replace(r"^(sh|sz|bj)", "", regex=True)
    # 单位换算:成交额 万元 -> 元;总市值 亿元 -> 元
    if "成交额" in df.columns:
        df["成交额"] = pd.to_numeric(df["成交额"], errors="coerce") * 1e4
    if "总市值" in df.columns:
        df["总市值"] = pd.to_numeric(df["总市值"], errors="coerce") * 1e8
    return df


def _load_snapshot() -> tuple[pd.DataFrame, str]:
    """按优先级加载全A快照,返回 (df, 来源名);两源都失败时抛异常。"""
    errors: list[str] = []
    for name, loader in (("东财", _snapshot_em), ("腾讯", _snapshot_tx)):
        try:
            df = loader()
            required = {"代码", "名称", "最新价", "涨跌幅", "成交额", "换手率", "量比", "总市值"}
            missing = required - set(df.columns)
            if missing:
                errors.append(f"{name}快照缺少列 {sorted(missing)}")
                continue
            return df, name
        except Exception as exc:  # noqa: BLE001 —— 逐个源尝试
            errors.append(f"{name}快照({type(exc).__name__}:{exc})")
    raise RuntimeError("; ".join(errors) or "全A实时快照不可用")


class RankedPoolCollector(BaseCollector):
    """五榜前列股票并集去重 -> 动态监控池 + 各榜快照 + 异动新闻。"""

    dimension = "ranked_pool"

    def __init__(
        self,
        rank_by_list: list[str] | None = None,
        top_n: int = 20,
        max_pool_size: int = 100,
        alert_pct: float = 9.0,
        news_per_alert: int = 1,
    ) -> None:
        super().__init__(top_n=max_pool_size)
        self.rank_by_list = list(rank_by_list or ["成交额", "涨幅", "换手率", "量比", "总市值"])
        self.top_n = top_n
        self.max_pool_size = max_pool_size
        self.alert_pct = alert_pct
        self.news_per_alert = news_per_alert

    def collect(self, code: str = "", name: str = "") -> CollectorResult:
        """池采集面向全市场,不针对单只股票;复用基类的计时 + 异常隔离。"""
        return super().collect(code, name)

    def _stock_news(self, code: str) -> list[dict]:
        """取某只异动股的最新若干条新闻;接口缺失/失败时返回空,不上抛。"""
        fn = getattr(ak, "stock_news_em", None)
        if fn is None:
            return []
        try:
            df = call_with_timeout(fn, symbol=code, timeout=30)
        except Exception:  # noqa: BLE001 —— 单只新闻失败仅跳过,不影响其余
            return []
        if df is None or df.empty:
            return []
        out: list[dict] = []
        for rec in df.head(self.news_per_alert).to_dict("records"):
            out.append(extract_record(rec, _NEWS_MAPPING))
        return out

    def _collect(self, code: str = "", name: str = "") -> CollectorResult:
        try:
            work, source = _load_snapshot()
        except Exception as exc:  # noqa: BLE001
            return CollectorResult(
                dimension=self.dimension,
                status="error",
                error=(
                    f"全A实时快照不可用({type(exc).__name__}: {exc})。"
                    "已尝试东财(push2.eastmoney.com)与腾讯(proxy.finance.qq.com)两个渠道。"
                ),
            )

        for col in ("成交额", "涨跌幅", "换手率", "量比", "总市值"):
            work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0.0)

        # 1) 各榜快照(boards):每榜按口径降序取前 top_n 名
        boards: dict[str, list[dict]] = {}
        for rk in self.rank_by_list:
            col = RANK_COLUMN.get(rk)
            if col is None:
                continue
            sub = work.sort_values(col, ascending=False).head(self.top_n)
            boards[rk] = [
                self._to_board_row(rk, i + 1, row)
                for i, (_, row) in enumerate(sub.iterrows())
            ]

        # 2) 并集去重 -> 动态监控池
        code_order: list[str] = []
        hit: dict[str, int] = {}
        board_of: dict[str, list[str]] = {}
        for rk, rows in boards.items():
            for row in rows:
                code_ = str(row.get("代码", "")).strip()
                if not code_:
                    continue
                if code_ not in hit:
                    hit[code_] = 0
                    board_of[code_] = []
                    code_order.append(code_)
                hit[code_] += 1
                board_of[code_].append(rk)

        pool_records: list[dict] = []
        for code_ in code_order:
            mrow = work[work["代码"].astype(str).str.strip() == code_].iloc[0]
            pool_records.append(
                self._to_pool_row(mrow, hit=hit[code_], boards=board_of[code_])
            )

        # 截断:先按命中榜数降序、再按成交额(元)降序,保留市场最活跃的股票
        pool_records.sort(key=lambda r: (-int(r.get("命中榜数", 0)), -float(r.get("成交额", 0.0))))
        pool = pool_records[: self.max_pool_size]

        # 3) 池内异动股票(|涨跌幅| >= alert_pct)附最新新闻
        alerts: list[dict] = []
        for p in pool:
            pct = float(p.get("涨跌幅", 0.0) or 0.0)
            if abs(pct) >= self.alert_pct:
                news: list[dict] = []
                code_ = str(p.get("代码", ""))
                if code_ and self.news_per_alert > 0:
                    news = self._stock_news(code_)
                alerts.append({"代码": code_, "名称": p.get("名称", ""), "涨跌幅": pct, "新闻": news})

        summary = (
            f"[数据源:{source}] {len(self.rank_by_list)} 榜各取前 {self.top_n} 名,并集去重后 "
            f"{len(pool)} 只(上限 {self.max_pool_size});异动(|涨跌幅|≥{self.alert_pct:g}%) {len(alerts)} 只"
        )
        return CollectorResult(
            dimension=self.dimension,
            status="ok",
            items=pool,
            boards=boards,
            alerts=alerts,
            summary=summary,
        )

    @staticmethod
    def _to_board_row(rank_key: str, rank_no: int, row: Any) -> dict:
        """榜单快照行:含该榜排名。"""
        d = row.to_dict()
        return {
            "榜": rank_key,
            "排名": rank_no,
            "代码": str(d.get("代码", "")).strip(),
            "名称": str(d.get("名称", "")).strip(),
            "现价": _num(d.get("最新价")),
            "涨跌幅": _num(d.get("涨跌幅")),
            "成交额": _num(d.get("成交额")) / 1e8,  # 元 -> 亿
            "换手率": _num(d.get("换手率")),
            "量比": _num(d.get("量比")),
            "总市值": _num(d.get("总市值")) / 1e8,  # 元 -> 亿
        }

    @staticmethod
    def _to_pool_row(row: Any, hit: int, boards: list[str]) -> dict:
        """监控池行:含命中榜数/命中榜列表,用于五维深采集与报告展示。"""
        d = row.to_dict()
        return {
            "代码": str(d.get("代码", "")).strip(),
            "名称": str(d.get("名称", "")).strip(),
            "现价": _num(d.get("最新价")),
            "涨跌幅": _num(d.get("涨跌幅")),
            "成交额": _num(d.get("成交额")) / 1e8,  # 元 -> 亿
            "换手率": _num(d.get("换手率")),
            "量比": _num(d.get("量比")),
            "总市值": _num(d.get("总市值")) / 1e8,  # 元 -> 亿
            "命中榜数": hit,
            "命中榜": boards,
        }
