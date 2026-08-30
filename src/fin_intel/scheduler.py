"""APScheduler 定时调度:盘前/盘后两个 cron job,均调用 run_report(kind)。"""
from __future__ import annotations

from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Config


def build_scheduler(cfg: Config, job: Callable[[str], None]) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=cfg.schedule.timezone)
    sched.add_job(
        job,
        trigger=CronTrigger.from_crontab(cfg.schedule.premarket_cron),
        args=["premarket"],
        id="premarket",
        name="盘前监控报告",
        misfire_grace_time=300,
        coalesce=True,
    )
    sched.add_job(
        job,
        trigger=CronTrigger.from_crontab(cfg.schedule.aftermarket_cron),
        args=["aftermarket"],
        id="aftermarket",
        name="盘后监控报告",
        misfire_grace_time=300,
        coalesce=True,
    )
    return sched