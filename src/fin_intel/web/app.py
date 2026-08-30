"""Web 管理台 FastAPI 应用。

路由:
- 面板: GET /  (index.html)
- 状态: GET  /api/status
- 报告: GET  /api/reports | GET /api/reports/read?path=
- 触发: POST /api/trigger {kind, dry_run?} | GET /api/trigger/status
- 配置: GET  /api/config (webhook 脱敏) | PUT /api/config (写回 YAML + 热重载)
- 调度: POST /api/scheduler/{start|pause|resume|stop} | 状态并入 /api/status
- 日志: GET  /api/logs?lines=
- 推送记录: GET /api/push-records?limit=

线程模型:
- 手动触发在后台 daemon 线程执行 run_report(采集耗时长),不阻塞事件循环。
- 调度启停操作的是本进程内 APScheduler 的 premarket/aftermarket 两个 job。
- 所有共享状态以模块级单例 + threading.RLock 保护。
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from loguru import logger

from .. import __version__ as core_version
from ..__main__ import run_report
from ..config import Config, load_config
from ..report_store import (
    DATA_DIR,
    PUSH_RECORDS_FILE,
    REPORTS_DIR,
    list_push_records,
    list_reports,
    read_report,
)
from ..scheduler import build_scheduler

# ---- 进程内共享状态 ----
_lock = threading.RLock()
_scheduler = None  # BackgroundScheduler | None
_cfg: Config | None = None
_config_path: Path | None = None
_manual_running = False
_last_manual: dict = {}

# webhook 脱敏占位符(前端编辑区显示;保存时识别为"未修改")
WEBHOOK_MASK = "***MASKED***"


def _mask_webhook(url: str) -> str:
    """webhook 脱敏:仅保留协议头 + 主机,凭据段整体替换为星号。"""
    url = (url or "").strip()
    if not url:
        return ""
    head = url.split("//", 1)
    if len(head) == 2:
        return head[0] + "//" + head[1].split("/")[0] + "/******"
    return "******"


def _webhook_set(url: str) -> bool:
    return bool((url or "").strip())


def _current_cfg() -> Config:
    if _cfg is None:
        raise HTTPException(503, "配置未初始化")
    return _cfg


def _effective_dry_run(cfg: Config) -> bool:
    return bool(cfg.app.dry_run)


def _set_webhook_line(text: str, webhook: str) -> tuple[str, bool]:
    """在 YAML 文本中替换 webhook_url 行(保留其余注释/格式)。

    返回 (新文本, 是否替换成功)。未找到该行(用户删除)时返回原文本 + False。
    """
    pattern = re.compile(r"^(\s*webhook_url\s*:\s*)[^\n]*$", re.M)
    new_line = lambda _m: f"{_m.group(1)}{yaml.safe_dump(webhook, allow_unicode=True).strip()}"
    new_text, n = pattern.subn(new_line, text, count=1)
    return new_text, n > 0


def _serialize_yaml(cfg: Config) -> str:
    """当前生效配置 -> YAML 文本(webhook 已脱敏)。"""
    data = cfg.model_dump(mode="json")
    if data.get("feishu") and data["feishu"].get("webhook_url"):
        data["feishu"]["webhook_url"] = WEBHOOK_MASK
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def _env_note(config_path: Path) -> str:
    """提示 .env 优先级关系(在线编辑写回 YAML 时须提示)。"""
    env_path = config_path.resolve().parent.parent / ".env"
    has_env = env_path.exists()
    if not has_env:
        return "未检测到项目根 .env 文件,当前配置完全来自 config/config.yaml。"
    return (
        "注意:项目根 .env 中已存在的环境变量(FEISHU_WEBHOOK_URL / RANK_BY_LIST / TOP_N / "
        "MAX_POOL_SIZE / ALERT_PCT / DRY_RUN / PREMARKET_CRON / AFTERMARKET_CRON / "
        "COLLECTORS_ENABLED / LOG_LEVEL 等)优先级高于本 YAML。写回 config/config.yaml 后,"
        "若 .env 配置了同名项,将以 .env 为准。"
    )


def create_app(config_path: str | Path) -> FastAPI:
    """构建 FastAPI 应用。config_path 为 config/config.yaml 路径。"""
    global _cfg, _config_path
    with _lock:
        _config_path = Path(config_path).resolve()
        _cfg = load_config(_config_path)

    pkg_dir = Path(__file__).resolve().parent
    app = FastAPI(title="fin-intel-agent Web 管理台", version=core_version)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(pkg_dir / "static" / "index.html")

    # ---------------- 状态 ----------------
    @app.get("/api/status")
    def status() -> dict:
        with _lock:
            cfg = _current_cfg()
            sched = _scheduler
            jobs = []
            if sched is not None:
                try:
                    for j in sched.get_jobs():
                        jobs.append(
                            {
                                "id": j.id,
                                "name": j.name,
                                "next_run_time": j.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                                if j.next_run_time
                                else None,
                                "paused": j.next_run_time is None,
                                "trigger": str(j.trigger),
                            }
                        )
                except Exception:  # noqa: BLE001  调度器已关闭等
                    jobs = []
            return {
                "version": core_version,
                "scheduler_running": bool(sched is not None and sched.running),
                "jobs": jobs,
                "effective_dry_run": _effective_dry_run(cfg),
                "webhook_set": _webhook_set(cfg.feishu.webhook_url),
                "ranked_pool_enabled": cfg.ranked_pool.enabled,
                "timezone": cfg.schedule.timezone,
                "premarket_cron": cfg.schedule.premarket_cron,
                "aftermarket_cron": cfg.schedule.aftermarket_cron,
                "config_path": str(_config_path),
                "data_dir": str(DATA_DIR),
                "manual_running": _manual_running,
                "last_manual": _last_manual,
            }

    # ---------------- 报告 ----------------
    @app.get("/api/reports")
    def reports(kind: str | None = None, limit: int = Query(50, ge=1, le=500)) -> dict:
        return {"reports": list_reports(kind=kind, limit=limit)}

    @app.get("/api/reports/read")
    def report_read(path: str = Query(..., description="相对 data 目录的报告路径")) -> PlainTextResponse:
        try:
            text = read_report(path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "报告不存在") from exc
        return PlainTextResponse(text, media_type="text/plain; charset=utf-8")

    # ---------------- 手动触发 ----------------
    @app.post("/api/trigger")
    def trigger(kind: str = Body(..., pattern="^(premarket|aftermarket)$"), dry_run: bool = Body(False)) -> dict:
        global _manual_running
        with _lock:
            if _manual_running:
                raise HTTPException(409, "已有手动触发任务正在执行,请等待完成后再试")
            cfg = _current_cfg()
            _manual_running = True

        def _worker(kind_: str, dry_: bool) -> None:
            global _manual_running, _last_manual
            try:
                with _lock:
                    cfg_ = _current_cfg()
                result = run_report(kind_, cfg_, dry_, source="web")
                with _lock:
                    _last_manual = {"ok": True, "result": result, "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            except Exception as exc:  # noqa: BLE001
                logger.exception("手动触发执行失败")
                with _lock:
                    _last_manual = {
                        "ok": False,
                        "error": str(exc),
                        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
            finally:
                with _lock:
                    _manual_running = False

        t = threading.Thread(target=_worker, args=(kind, dry_run), daemon=True)
        t.start()
        return {"ok": True, "kind": kind, "dry_run": dry_run, "message": "已在后台线程开始执行,可通过 /api/trigger/status 查看结果"}

    @app.get("/api/trigger/status")
    def trigger_status() -> dict:
        with _lock:
            return {"running": _manual_running, "last": _last_manual}

    # ---------------- 配置 ----------------
    @app.get("/api/config")
    def get_config() -> dict:
        with _lock:
            cfg = _current_cfg()
            return {
                "config_path": str(_config_path),
                "yaml_text": _serialize_yaml(cfg),
                "webhook": _mask_webhook(cfg.feishu.webhook_url),
                "webhook_set": _webhook_set(cfg.feishu.webhook_url),
                "effective_dry_run": _effective_dry_run(cfg),
                "env_note": _env_note(_config_path),
            }

    @app.put("/api/config")
    def put_config(payload: dict = Body(...)) -> dict:
        global _cfg, _scheduler
        content: str = (payload.get("content") or "").strip()
        webhook: str = (payload.get("webhook_url") or "").strip()
        if not content:
            raise HTTPException(400, "YAML 内容为空")
        try:
            parsed = yaml.safe_load(content)
            if not isinstance(parsed, dict):
                raise ValueError("YAML 根节点必须是映射")
        except yaml.YAMLError as exc:
            raise HTTPException(400, f"YAML 解析失败: {exc}") from exc

        with _lock:
            cfg_old = _current_cfg()
            # 保留/更新 webhook:显式提供新值时使用新值;否则沿用当前生效值(即使 .env 覆盖也以生效值为准)
            if webhook and webhook != WEBHOOK_MASK:
                final_webhook = webhook
            else:
                final_webhook = cfg_old.feishu.webhook_url
            new_text, replaced = _set_webhook_line(content, final_webhook)
            if not replaced:
                # 用户在编辑区删掉了 webhook_url 行,回退到结构化写回(该场景注释会丢失)
                data = yaml.safe_load(content)
                data.setdefault("feishu", {})["webhook_url"] = final_webhook
                new_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)

            # 先做校验性加载(不落盘):确认写回后配置合法
            import tempfile

            with tempfile.TemporaryDirectory() as td:
                probe = Path(td) / "probe.yaml"
                probe.write_text(new_text, encoding="utf-8")
                try:
                    load_config(probe)
                except Exception as exc:  # noqa: BLE001
                    raise HTTPException(400, f"配置校验失败,已放弃写回: {exc}") from exc

            # 备份后写回
            backup = _config_path.with_suffix(".yaml.bak")
            try:
                backup.write_text(_config_path.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass
            _config_path.write_text(new_text, encoding="utf-8")

            # 热重载:重新加载配置;若调度器在跑,重建调度器应用新 cron
            _cfg = load_config(_config_path)
            if _scheduler is not None and _scheduler.running:
                _scheduler.shutdown(wait=False)
                _scheduler = _rebuild_scheduler()
                _scheduler.start()

            logger.info("配置已写回 {} 并热重载", _config_path)
            return {
                "ok": True,
                "message": "配置已保存并热重载生效" + (f" | {_env_note(_config_path)}"),
                "replaced_webhook_line": replaced,
            }

    # ---------------- 调度 ----------------
    def _rebuild_scheduler():
        global _scheduler
        cfg = _current_cfg()
        _scheduler = build_scheduler(cfg, lambda kind: run_report(kind, cfg, False, source="scheduler"))
        return _scheduler

    @app.post("/api/scheduler/start")
    def scheduler_start() -> dict:
        global _scheduler
        with _lock:
            if _scheduler is not None and _scheduler.running:
                return {"ok": True, "message": "调度器已在运行"}
            _scheduler = _rebuild_scheduler()
            _scheduler.start()
            logger.info("调度器已启动(盘前 {} / 盘后 {})", _cfg.schedule.premarket_cron, _cfg.schedule.aftermarket_cron)
            return {"ok": True, "message": "调度器已启动"}

    @app.post("/api/scheduler/pause")
    def scheduler_pause() -> dict:
        global _scheduler
        with _lock:
            if _scheduler is None or not _scheduler.running:
                raise HTTPException(409, "调度器未运行")
            for jid in ("premarket", "aftermarket"):
                try:
                    _scheduler.pause_job(jid)
                except Exception:  # noqa: BLE001
                    pass
            logger.info("调度器已暂停(盘前/盘后 job)")
            return {"ok": True, "message": "已暂停盘前/盘后两个 job"}

    @app.post("/api/scheduler/resume")
    def scheduler_resume() -> dict:
        global _scheduler
        with _lock:
            if _scheduler is None or not _scheduler.running:
                raise HTTPException(409, "调度器未运行")
            for jid in ("premarket", "aftermarket"):
                try:
                    _scheduler.resume_job(jid)
                except Exception:  # noqa: BLE001
                    pass
            logger.info("调度器已恢复(盘前/盘后 job)")
            return {"ok": True, "message": "已恢复盘前/盘后两个 job"}

    @app.post("/api/scheduler/stop")
    def scheduler_stop() -> dict:
        global _scheduler
        with _lock:
            if _scheduler is not None and _scheduler.running:
                _scheduler.shutdown(wait=False)
                _scheduler = None
                logger.info("调度器已停止")
                return {"ok": True, "message": "调度器已停止"}
            return {"ok": True, "message": "调度器未运行(无需停止)"}

    # ---------------- 日志 ----------------
    @app.get("/api/logs")
    def logs(lines: int = Query(200, ge=1, le=2000)) -> dict:
        log_file = DATA_DIR / "web.log"
        if not log_file.exists():
            return {"path": str(log_file), "lines": []}
        text = log_file.read_text(encoding="utf-8", errors="replace")
        tail = text.splitlines()[-lines:]
        return {"path": str(log_file), "lines": tail}

    # ---------------- 推送记录 ----------------
    @app.get("/api/push-records")
    def push_records(limit: int = Query(100, ge=1, le=1000)) -> dict:
        return {"path": str(PUSH_RECORDS_FILE), "records": list_push_records(limit=limit)}

    return app
