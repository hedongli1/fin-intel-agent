"""程序入口:`python -m src.fin_intel`。

模式:
- `--once`:立即执行一轮报告后退出(默认 aftermarket,可用 --kind premarket)。
- `--dry-run`:强制打印到 stdout,不推送 webhook(即使配置了 webhook)。
- 无参数:启动常驻 scheduler(按 schedule 中 cron 定时执行)。

运行逻辑(动态排行榜监控池模式):
  1. RankedPoolCollector 拉东财全A实时快照,按五榜(成交额/涨幅/换手率/量比/总市值)
     各取前 top_n 名 -> 并集去重 -> 动态监控池;
  2. 对池内每只股票执行已启用的五维深采集(行情/基本面/新闻舆情/研报评级/行业板块);
     每个维度异常独立隔离(CollectorResult.status=error),互不影响;
  3. aggregator.aggregate_pool 聚合为纯文本报告 -> notifier 推送飞书/webhook 为空或
     dry_run 时打印 stdout。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

from . import __version__
from .aggregator import aggregate, aggregate_pool
from .collectors import get_collector
from .collectors.rank import RankedPoolCollector
from .config import Config, load_config
from .notifier import send_report
from .report_store import record_push, save_report
from .scheduler import build_scheduler
from .schema import CollectorResult
from .trade_calendar import is_trading_day


def init_logging(level: str) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level.upper(),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )


def _collect_stock_dimensions(
    cfg: Config, pool_items: list[dict]
) -> dict[str, dict[str, CollectorResult]]:
    """对池内每只股票执行已启用维度的五维深采集。

    采集层已做异常隔离(BaseCollector.collect 统一 try/except),任何单维度失败都转为
    status=error 的 CollectorResult,不影响其它维度与其它股票。
    """
    collectors = {dim: get_collector(dim) for dim in cfg.collectors.enabled}
    results: dict[str, dict[str, CollectorResult]] = {}
    for p in pool_items:
        code_ = str(p.get("代码", "")).strip()
        name_ = str(p.get("名称", "")).strip()
        if not code_:
            continue
        results[code_] = {}
        for dim, collector in collectors.items():
            if collector is None:
                results[code_][dim] = CollectorResult(
                    dimension=dim, status="error", error=f"未注册的维度 {dim}"
                )
                continue
            results[code_][dim] = collector.collect(code_, name_)
    return results


def _finalize_report(
    kind: str,
    date_str: str,
    generated_at: str,
    text: str,
    cfg: Config,
    dry_run: bool,
    skipped: bool = False,
    reason: str = "",
    source: str = "cli",
) -> dict:
    """统一收尾:落盘报告 + 推送 + 记录,返回本轮摘要 dict。

    - text 为空(如非交易日跳过)仍会写一条 skipped 推送记录,不落盘文件。
    - 返回值供调用方(CLI / Web 管理台)展示,不影响原有推送行为。
    """
    saved = save_report(kind, text, source=source) if text else None
    push = (
        send_report(text, cfg.feishu, dry_run)
        if text
        else {"dry_run": dry_run, "pushed": False, "chunks": 0, "failed": 0, "message": reason or "无报告文本"}
    )
    record_push(
        {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
            "date": date_str,
            "generated_at": generated_at,
            "skipped": skipped,
            "reason": reason,
            "source": source,
            "dry_run": dry_run,
            "pushed": push["pushed"],
            "chunks": push["chunks"],
            "failed": push["failed"],
            "message": push["message"],
            "report_rel": (saved or {}).get("rel_path"),
            "report_abs": (saved or {}).get("abs_path"),
            "length": (saved or {}).get("length", 0),
        }
    )
    return {
        "kind": kind,
        "date": date_str,
        "generated_at": generated_at,
        "skipped": skipped,
        "reason": reason,
        "dry_run": dry_run,
        "pushed": push["pushed"],
        "chunks": push["chunks"],
        "failed": push["failed"],
        "message": push["message"],
        "report_rel": (saved or {}).get("rel_path"),
        "report_abs": (saved or {}).get("abs_path"),
        "length": (saved or {}).get("length", 0),
    }


def _run_pool_report(
    kind: str, date_str: str, generated_at: str, cfg: Config, dry_run: bool
) -> str:
    """动态排行榜监控池模式:池生成 -> 五维深采集 -> 聚合。返回报告纯文本。"""
    rp = cfg.ranked_pool
    logger.info(
        "监控池模式: rank_by_list={}, 每榜 top_n={}, 池上限={}, alert_pct={}%, collectors={}",
        rp.rank_by_list, rp.top_n, rp.max_pool_size, rp.alert_pct, cfg.collectors.enabled,
    )

    pool_collector = RankedPoolCollector(
        rank_by_list=rp.rank_by_list,
        top_n=rp.top_n,
        max_pool_size=rp.max_pool_size,
        alert_pct=rp.alert_pct,
        news_per_alert=rp.news_per_alert,
    )
    pool_result = pool_collector.collect()

    stock_results: dict[str, dict[str, CollectorResult]] = {}
    if pool_result.status == "ok":
        stock_results = _collect_stock_dimensions(cfg, pool_result.items)
    else:
        logger.warning("监控池生成失败,跳过五维深采集: {}", pool_result.error)

    return aggregate_pool(kind, date_str, generated_at, pool_result, stock_results, cfg)


def run_report(kind: str, cfg: Config, dry_run: bool, source: str = "cli") -> dict:
    """执行一轮报告:交易日判断 -> 池生成(或旧 watchlist) -> 五维采集 -> 聚合 -> 推送。

    dry_run 生效口径:CLI --dry-run 与 config app.dry_run 任一为真即不推送,
    与 README 说明一致。
    返回本轮摘要 dict(含 report_rel/report_abs 存档路径、推送结果等)。
    """
    tz = ZoneInfo(cfg.schedule.timezone)
    now = datetime.now(tz)
    today = now.date()

    if cfg.schedule.skip_non_trading_days and not is_trading_day(today):
        logger.info("今日 {} 非交易日,跳过 {} 报告", today, kind)
        return _finalize_report(
            kind,
            today.strftime("%Y-%m-%d"),
            now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "",
            cfg,
            dry_run or cfg.app.dry_run,
            skipped=True,
            reason="非交易日",
            source=source,
        )

    date_str = today.strftime("%Y-%m-%d")
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    effective_dry_run = dry_run or cfg.app.dry_run

    if cfg.ranked_pool.enabled:
        text = _run_pool_report(kind, date_str, generated_at, cfg, effective_dry_run)
        return _finalize_report(kind, date_str, generated_at, text, cfg, effective_dry_run, source=source)

    # 旧固定 watchlist 五维模式(向后兼容;ranked_pool.enabled=false 时使用)
    logger.warning("ranked_pool.enabled=false,回退到旧固定 watchlist 五维模式")
    collectors = {dim: get_collector(dim) for dim in cfg.collectors.enabled}
    results: dict[str, dict[str, CollectorResult]] = {}
    for w in cfg.watchlist:
        results[w.code] = {}
        for dim, collector in collectors.items():
            if collector is None:
                results[w.code][dim] = CollectorResult(
                    dimension=dim, status="error", error=f"未注册的维度 {dim}"
                )
                continue
            results[w.code][dim] = collector.collect(w.code, w.name)

    text = aggregate(kind, date_str, generated_at, cfg.watchlist, results, cfg)
    return _finalize_report(kind, date_str, generated_at, text, cfg, effective_dry_run, source=source)


def main() -> int:
    parser = argparse.ArgumentParser(prog="fin-intel-agent", description="金融/股票领域定时监控 Agent")
    parser.add_argument("--once", action="store_true", help="立即执行一轮报告后退出")
    parser.add_argument(
        "--kind", choices=("premarket", "aftermarket"), default="aftermarket",
        help="报告类型(默认 aftermarket 盘后)",
    )
    parser.add_argument("--dry-run", action="store_true", help="强制只打印到 stdout,不推送 webhook")
    parser.add_argument("--config", default="config/config.yaml", help="配置文件路径")
    args = parser.parse_args()

    cfg = load_config(args.config)
    init_logging(cfg.app.log_level)

    logger.info("fin-intel-agent v{}", __version__)
    if cfg.ranked_pool.enabled:
        logger.info(
            "已加载配置: 动态排行榜监控池模式(rank_by_list={}, 每榜 top_n={}, 池上限={}, alert_pct={}%, collectors={}, timezone={})",
            cfg.ranked_pool.rank_by_list, cfg.ranked_pool.top_n, cfg.ranked_pool.max_pool_size,
            cfg.ranked_pool.alert_pct, cfg.collectors.enabled, cfg.schedule.timezone,
        )
    else:
        logger.info(
            "已加载配置: 固定 watchlist 模式(watchlist={} 只, collectors={}, timezone={})",
            len(cfg.watchlist), cfg.collectors.enabled, cfg.schedule.timezone,
        )
    logger.info(
        "飞书 webhook={} / dry_run={} / skip_non_trading_days={}",
        "已配置" if cfg.feishu.webhook_url else "未配置(空)",
        cfg.app.dry_run or args.dry_run,
        cfg.schedule.skip_non_trading_days,
    )

    if args.once:
        run_report(args.kind, cfg, args.dry_run)
        return 0

    # 常驻调度:闭包绑定 cfg/dry_run,调度器侧仅传 kind,规避签名不匹配
    scheduler = build_scheduler(
        cfg, lambda kind: run_report(kind, cfg, args.dry_run)
    )
    scheduler.start()
    logger.info("常驻调度已启动(盘前 {} / 盘后 {}),Ctrl+C 退出", cfg.schedule.premarket_cron, cfg.schedule.aftermarket_cron)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C,停止调度")
        scheduler.shutdown(wait=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
