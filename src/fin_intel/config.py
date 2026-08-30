"""配置加载与校验(pydantic)。

从 config/config.yaml 读入并强校验,常见错误在启动时即报出,避免运行时踩坑。
支持用项目根目录 .env 的环境变量覆盖 YAML 值(缺省时回退 YAML)。
"""
from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import dotenv_values
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from .schema import KNOWN_DIMENSIONS


class ScheduleConfig(BaseModel):
    timezone: str = "Asia/Shanghai"
    premarket_cron: str = "0 30 9 * * 1-5"  # 盘前 09:30,周一至周五
    aftermarket_cron: str = "0 30 15 * * 1-5"  # 盘后 15:30,周一至周五
    skip_non_trading_days: bool = True


class FeishuConfig(BaseModel):
    webhook_url: str = ""  # 允许为空(dry_run 模式)
    chunk_size: int = 18000  # 单条消息字符上限,飞书约 20KB,留余量


class WatchItem(BaseModel):
    code: str
    name: str

    @field_validator("code")
    @classmethod
    def _code_is_6_digits(cls, v: object) -> str:
        s = str(v).strip()
        if len(s) != 6 or not s.isdigit():
            raise ValueError(f"watchlist.code 必须是 6 位数字字符串,当前: {v!r}")
        return s


class CollectorsConfig(BaseModel):
    # 动态排行榜监控池模式下,对池内股票执行的五维深采集维度,全部启用
    enabled: list[str] = Field(default_factory=lambda: ["quote", "fundamental", "news", "research", "sector"])

    @field_validator("enabled")
    @classmethod
    def _enabled_known(cls, v: list[str]) -> list[str]:
        for dim in v:
            if dim not in KNOWN_DIMENSIONS:
                raise ValueError(f"collectors.enabled 含未知维度 {dim!r},可选值: {list(KNOWN_DIMENSIONS)}")
        return v


# 排行榜池允许的榜单口径(榜名 -> 东财全A快照排序列的映射见 collectors/rank.py)
RANK_BY_ALLOWED: tuple[str, ...] = ("成交额", "涨幅", "换手率", "量比", "总市值")


class RankedPoolConfig(BaseModel):
    """动态排行榜监控池配置(核心模式)。

    监控对象不再来自固定 watchlist,而是由多个排行榜前列股票取并集去重后动态生成:
    - rank_by_list:参与取池的榜单口径列表(成交额榜/涨幅榜/换手率榜/量比榜/总市值榜);
    - top_n:每个榜单取前 N 名;
    - max_pool_size:并集去重后的监控池数量上限(超出时按“命中榜数降序 + 成交额降序”截断);
    - alert_pct:|涨跌幅| >= 该值标记异动并附新闻;
    - news_per_alert:每只异动股附最新新闻条数。
    """

    enabled: bool = True
    rank_by_list: list[str] = Field(default_factory=lambda: ["成交额", "涨幅", "换手率", "量比", "总市值"])
    top_n: int = Field(default=20, gt=0)  # 每榜前 N 名
    max_pool_size: int = Field(default=100, gt=0)  # 并集去重后监控池上限
    alert_pct: float = Field(default=9.0, ge=0)  # 异动阈值(%)
    news_per_alert: int = Field(default=1, ge=0)  # 每只异动股附最新新闻条数

    @field_validator("rank_by_list")
    @classmethod
    def _rank_by_list_known(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in v:
            s = str(item).strip()
            if not s:
                continue
            if s not in RANK_BY_ALLOWED:
                raise ValueError(
                    f"ranked_pool.rank_by_list 含未知榜单 {s!r},可选值: {list(RANK_BY_ALLOWED)}"
                )
            if s not in cleaned:
                cleaned.append(s)
        if not cleaned:
            raise ValueError("ranked_pool.rank_by_list 不能为空")
        return cleaned


class AlertsConfig(BaseModel):
    enabled: bool = False
    price_change_pct: float = 5.0  # |当日涨跌幅| 阈值(%)


class AppConfig(BaseModel):
    log_level: str = "INFO"
    dry_run: bool = True


class Config(BaseModel):
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    watchlist: list[WatchItem] = Field(default_factory=list)  # 动态池模式下不再使用,保留兼容旧五维
    collectors: CollectorsConfig = Field(default_factory=CollectorsConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    ranked_pool: RankedPoolConfig = Field(default_factory=RankedPoolConfig)
    app: AppConfig = Field(default_factory=AppConfig)

    @field_validator("watchlist", mode="before")
    @classmethod
    def _watchlist_none_ok(cls, v: object) -> object:
        if v is None:
            return []
        return v


def _parse_watchlist_env(value: str) -> list[dict[str, str]]:
    """解析 WATCHLIST 环境变量。

    格式:`600519:贵州茅台,300750:宁德时代`(英文逗号分隔条目,英文冒号分隔 code:name)
    -> `[{"code": "600519", "name": "贵州茅台"}, ...]`。
    单个条目解析失败时跳过并告警,不中断整体加载。
    """
    items: list[dict[str, str]] = []
    for raw_entry in value.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            logger.warning("WATCHLIST 条目缺少英文冒号分隔,已跳过: {!r}", raw_entry)
            continue
        code, _, name = entry.partition(":")
        code, name = code.strip(), name.strip()
        if len(code) != 6 or not code.isdigit():
            logger.warning("WATCHLIST 条目 code 非 6 位数字,已跳过: {!r}", raw_entry)
            continue
        if not name:
            logger.warning("WATCHLIST 条目 name 为空,已跳过: {!r}", raw_entry)
            continue
        items.append({"code": code, "name": name})
    return items


def _apply_env_overrides(raw: dict, config_path: Path) -> None:
    """用项目根目录 .env 的环境变量覆盖 YAML 原始字典(在实例化 Config 之前)。

    - .env 路径由 config.yaml 所在目录向上定位到项目根(约定 config.yaml 位于 <root>/config/)。
    - 仅当环境变量存在且为非空字符串时才覆盖;缺失/为空则保留 YAML 原值。
    - 覆盖前对 raw 做 setdefault 兜底,避免嵌套键缺失导致 KeyError。
    """
    # 项目根 = config.yaml 的父目录的父目录(即 <root>/config/config.yaml -> <root>)
    env_path = config_path.resolve().parent.parent / ".env"
    env = dotenv_values(str(env_path))  # 文件不存在或为空时返回空 dict,不抛异常
    if not env:
        return

    # 飞书 webhook
    if env.get("FEISHU_WEBHOOK_URL"):
        raw.setdefault("feishu", {})["webhook_url"] = env["FEISHU_WEBHOOK_URL"]

    # 固定企业清单(动态池模式下不再使用;若仍设置 WATCHLIST 则继续覆盖,非核心)
    if env.get("WATCHLIST"):
        parsed = _parse_watchlist_env(env["WATCHLIST"])
        if parsed:
            raw["watchlist"] = parsed

    # 动态排行榜监控池配置(核心)
    rp = raw.setdefault("ranked_pool", {})
    # RANK_BY_LIST(逗号分隔,新格式)优先;RANK_BY(单值,旧格式)兼容为单元素列表
    rank_env = env.get("RANK_BY_LIST") or env.get("RANK_BY")
    if rank_env:
        rp["rank_by_list"] = [s.strip() for s in rank_env.split(",") if s.strip()]
    if env.get("TOP_N"):
        rp["top_n"] = int(env["TOP_N"])
    if env.get("ALERT_PCT"):
        rp["alert_pct"] = float(env["ALERT_PCT"])
    if env.get("MAX_POOL_SIZE"):
        rp["max_pool_size"] = int(env["MAX_POOL_SIZE"])

    # 运行模式(dry_run)
    dry_run = env.get("DRY_RUN", "").strip().lower()
    if dry_run in ("true", "1"):
        raw.setdefault("app", {})["dry_run"] = True
    elif dry_run in ("false", "0"):
        raw.setdefault("app", {})["dry_run"] = False

    # 定时调度 cron
    if env.get("PREMARKET_CRON"):
        raw.setdefault("schedule", {})["premarket_cron"] = env["PREMARKET_CRON"]
    if env.get("AFTERMARKET_CRON"):
        raw.setdefault("schedule", {})["aftermarket_cron"] = env["AFTERMARKET_CRON"]

    # 采集器开关(五维)
    if env.get("COLLECTORS_ENABLED"):
        dims = [d.strip() for d in env["COLLECTORS_ENABLED"].split(",") if d.strip()]
        if dims:
            raw.setdefault("collectors", {})["enabled"] = dims

    # 日志级别(原样字符串,不做大小写改写)
    if env.get("LOG_LEVEL"):
        raw.setdefault("app", {})["log_level"] = env["LOG_LEVEL"]


def load_config(path: str | Path) -> Config:
    """读取 YAML,用 .env 环境变量覆盖后实例化 Config;文件不存在或字段非法时直接抛出。"""
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    _apply_env_overrides(raw, p)
    return Config(**raw)
