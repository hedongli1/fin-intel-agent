"""汇总器:把监控池结果 + dict[code -> dict[dimension -> CollectorResult]] 合成纯文本 Markdown 风格报告。

飞书 text 消息不支持真正的 Markdown 渲染,故用清晰的纯文本排版(标题/分隔线/bullet)。
"""
from __future__ import annotations

from .config import Config
from .schema import KNOWN_DIMENSIONS, CollectorResult
from .collectors.rank import BOARD_LABEL

DIMENSION_TITLES: dict[str, str] = {
    "quote": "① 行情 / 股价",
    "fundamental": "② 基本面 / 财务",
    "news": "③ 新闻 / 公告 / 舆情",
    "research": "④ 研报 / 机构评级",
    "sector": "⑤ 行业 / 板块",
}
DIMENSION_ORDER: list[str] = list(KNOWN_DIMENSIONS)


def _fmt(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _render_dimension(res: CollectorResult) -> list[str]:
    if res.status == "error":
        return [f"  ⚠ 采集失败: {res.error}"]

    lines: list[str] = []
    if res.summary:
        lines.append(f"  {res.summary}")
    for item in res.items:
        parts = [f"{k}: {_fmt(v)}" for k, v in item.items() if v not in (None, "")]
        if parts:
            lines.append("  - " + "  |  ".join(parts))
    if not res.summary and not res.items:
        lines.append("  - (无数据)")
    return lines


def _quote_alert(res: CollectorResult, threshold: float) -> str:
    for item in res.items:
        raw = item.get("涨跌幅")
        try:
            pct = float(str(raw).replace("%", ""))
        except (TypeError, ValueError):
            continue
        if abs(pct) >= threshold:
            return f"  ⚠ 当日涨跌幅 {pct:g}% 超过阈值 {threshold:g}%,注意异动"
    return ""


def aggregate(
    kind: str,
    date_str: str,
    generated_at: str,
    watchlist: list,
    results: dict[str, dict[str, CollectorResult]],
    cfg: Config,
) -> str:
    """旧固定 watchlist 五维模式(向后兼容;ranked_pool.enabled=false 时使用)。"""
    kind_label = "盘前" if kind == "premarket" else "盘后"
    lines: list[str] = [f"【A股五维情报 · {kind_label}监控】{date_str}", ""]

    for w in watchlist:
        lines.append(f"## {w.name} ({w.code})")
        for dim in DIMENSION_ORDER:
            res = results.get(w.code, {}).get(dim)
            if res is None:
                continue
            lines.append(f"### {DIMENSION_TITLES[dim]}")
            lines.extend(_render_dimension(res))
            if cfg.alerts.enabled and dim == "quote" and res.status == "ok":
                alert = _quote_alert(res, cfg.alerts.price_change_pct)
                if alert:
                    lines.append(alert)
        lines.append("---")

    lines.append(f"生成时间: {generated_at}")
    lines.append("数据来源: akshare(聚合自东方财富/财联社/新浪等公开接口)")
    lines.append("免责声明: 本报告仅供研究参考,不构成任何投资建议。数据可能存在延迟或误差,请以官方披露为准。")
    return "\n".join(lines)


# ---- 动态排行榜监控池报告渲染 ----

def _disp_width(s: str) -> int:
    """字符显示宽度:CJK 等全角字符按 2 计,保证等宽字体下对齐。"""
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x2E7F else 1
    return w


def _col(s: object, width: int, align: str = "l") -> str:
    """按显示宽度补齐到 width;align: l=左 / r=右 / c=中。"""
    text = str(s)
    pad = width - _disp_width(text)
    if pad <= 0:
        return text + "  "
    if align == "r":
        return " " * pad + text
    if align == "c":
        left = pad // 2
        return " " * left + text + " " * (pad - left)
    return text + " " * pad


# 五榜快照统一表头:(标签, 宽度, 数值对齐方式)
_POOL_COLS: list[tuple[str, int, str]] = [
    ("排名", 4, "l"),
    ("代码", 8, "l"),
    ("名称", 10, "l"),
    ("现价", 9, "r"),
    ("涨跌幅", 9, "r"),
    ("成交额(亿)", 11, "r"),
    ("换手率", 9, "r"),
    ("量比", 8, "r"),
    ("总市值(亿)", 11, "r"),
]


def _render_board_row(row: dict, rank_no: int, alert_pct: float) -> str:
    """渲染榜单快照中的一行。"""
    pct = float(row.get("涨跌幅", 0.0) or 0.0)
    mark = " ⚠" if abs(pct) >= alert_pct else ""
    cells = [
        _col(str(rank_no), 4, "l"),
        _col(str(row.get("代码", "")), 8, "l"),
        _col(str(row.get("名称", "")), 10, "l"),
        _col(f"{float(row.get('现价', 0.0) or 0.0):.2f}", 9, "r"),
        _col(f"{pct:+.2f}%", 9, "r"),
        _col(f"{float(row.get('成交额', 0.0) or 0.0):.2f}", 11, "r"),
        _col(f"{float(row.get('换手率', 0.0) or 0.0):.2f}%", 9, "r"),
        _col(f"{float(row.get('量比', 0.0) or 0.0):.2f}", 8, "r"),
        _col(f"{float(row.get('总市值', 0.0) or 0.0):.2f}", 11, "r"),
    ]
    return "".join(cells) + mark


def aggregate_pool(
    kind: str,
    date_str: str,
    generated_at: str,
    pool_result: CollectorResult,
    stock_results: dict[str, dict[str, CollectorResult]],
    cfg: Config,
) -> str:
    """把监控池生成结果 + 池内股票五维采集结果合成纯文本报告。"""
    rp = cfg.ranked_pool
    kind_label = "盘前" if kind == "premarket" else "盘后"
    lines: list[str] = [f"【A股五维情报 · {kind_label}监控】{date_str} — 动态排行榜监控池", ""]

    if pool_result.status == "error":
        lines.append(f"⚠️ 监控池生成失败:{pool_result.error}")
        lines.append("(后续五维深采集已跳过,请检查网络/数据源后重试)")
        lines.append("")
    else:
        # 一、五榜快照
        lines.append("— 五榜快照(动态监控池来源)—")
        for rk, rows in pool_result.boards.items():
            label = BOARD_LABEL.get(rk, f"{rk}榜")
            lines.append(f"■ {label} TOP{len(rows)}")
            lines.append("".join(_col(lbl, w, a) for lbl, w, a in _POOL_COLS))
            lines.append("-" * 82)
            for row in rows:
                lines.append(_render_board_row(row, int(row.get("排名", 0) or 0), rp.alert_pct))
            lines.append("")

        # 二、池内异动股票(|涨跌幅| >= alert_pct)
        lines.append(f"— 池内异动股票(|涨跌幅|≥{rp.alert_pct:g}%)—")
        if not pool_result.alerts:
            lines.append("  (无)")
        for a in pool_result.alerts:
            pct = float(a.get("涨跌幅", 0.0) or 0.0)
            lines.append(f"█ {a.get('代码', '')} {a.get('名称', '')}  {pct:+.2f}%")
            news = a.get("新闻") or []
            if not news:
                lines.append("  · (新闻接口不可用或暂无新闻)")
            for n in news:
                title = n.get("标题") or ""
                meta = " | ".join(x for x in (n.get("来源"), n.get("时间")) if x)
                suffix = f"  [{meta}]" if meta else ""
                lines.append(f"  · {title}{suffix}")
        lines.append("")

        # 三、监控池统计
        pool = pool_result.items
        lines.append(
            f"— 监控池(五榜并集去重 {len(pool)} 只,上限 {rp.max_pool_size},"
            f"每榜 top_n={rp.top_n})—"
        )
        lines.append(f"  {pool_result.summary}")
        lines.append("")

        # 四、池内股票五维深采集情报
        lines.append("— 池内股票五维情报 —")
        for p in pool:
            code_ = str(p.get("代码", ""))
            name_ = str(p.get("名称", ""))
            boards = p.get("命中榜") or []
            boards_txt = "/".join(BOARD_LABEL.get(b, b) for b in boards) if boards else "-"
            lines.append(
                f"## {name_} ({code_})  命中 {p.get('命中榜数', len(boards))} 榜: {boards_txt}"
            )
            dims = stock_results.get(code_, {})
            for dim in DIMENSION_ORDER:
                res = dims.get(dim)
                if res is None:
                    continue
                lines.append(f"### {DIMENSION_TITLES[dim]}")
                lines.extend(_render_dimension(res))
                if cfg.alerts.enabled and dim == "quote" and res.status == "ok":
                    alert = _quote_alert(res, cfg.alerts.price_change_pct)
                    if alert:
                        lines.append(alert)
            lines.append("---")

    lines.append(f"生成时间: {generated_at}")
    lines.append("数据来源: akshare(榜单为东财/腾讯全A实时快照;五维数据聚合自东方财富/巨潮/同花顺/新浪/财联社等公开接口)")
    lines.append("免责声明: 本报告仅供研究参考,不构成任何投资建议。数据可能存在延迟或误差,请以官方披露为准。")
    return "\n".join(lines)
