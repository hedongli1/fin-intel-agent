"""动态排行榜监控池采集器(五榜并集去重)。

数据源:ak.stock_zh_a_spot_em() 取东财全市场实时快照(含成交额/涨跌幅/换手率/量比/总市值)。

流程:
1. 拉一次全A实时快照;
2. 按 ranked_pool.rank_by_list 中每个榜单口径分别降序取前 top_n 名,得到各榜快照(boards);
3. 各榜前列股票取并集去重,得到动态监控池(items);超过 max_pool_size 时按
   “命中榜数降序 + 成交额降序”截断;
4. 对池内 |涨跌幅| >= alert_pct 的异动股逐只调 ak.stock_news_em 取最新新闻(alerts);
   新闻接口缺失/失败时仅保留异动标记、跳过该股新闻,保证整体不崩溃。

注:该接口是「实时榜单」唯一合理数据源(新浪日线无实时成交额/换手率/量比,做不了榜单),
故本采集器不做降级;接口不可达/返回为空时返回 status=error 并给出明确中文提示
(本地 Windows 下 push2.eastmoney.com 通常可达)。
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
        fn = getattr(ak, "stock_zh_a_spot_em", None)
        if fn is None:
            return CollectorResult(
                dimension=self.dimension,
                status="error",
                error="akshare 缺少 stock_zh_a_spot_em 接口(可能版本过旧或接口改名),请升级 akshare。",
            )

        try:
            df = call_with_timeout(fn, timeout=90)
        except Exception as exc:  # noqa: BLE001
            return CollectorResult(
                dimension=self.dimension,
                status="error",
                error=(
                    f"东财全A实时快照接口调用失败({type(exc).__name__}): {exc}。"
                    "请检查网络连通性 / 升级 akshare 版本;本地 Windows 下 push2.eastmoney.com 通常可达。"
                ),
            )

        if df is None or df.empty:
            return CollectorResult(
                dimension=self.dimension,
                status="error",
                error="东财全A实时快照返回为空(push2.eastmoney.com 可能被远端断开,本地 Windows 通常正常)。",
            )

        required = {"代码", "名称", "最新价", "涨跌幅", "成交额", "换手率", "量比", "总市值"}
        missing = required - set(df.columns)
        if missing:
            return CollectorResult(
                dimension=self.dimension,
                status="error",
                error=f"快照缺少必要列: {sorted(missing)},实际列: {list(df.columns)}",
            )

        work = df.copy()
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
            f"{len(self.rank_by_list)} 榜各取前 {self.top_n} 名,并集去重后 {len(pool)} 只"
            f"(上限 {self.max_pool_size});异动(|涨跌幅|≥{self.alert_pct:g}%) {len(alerts)} 只"
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
