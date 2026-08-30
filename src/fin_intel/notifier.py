"""飞书 webhook 推送(含分片)。

- webhook_url 为空或 dry_run 时:打印报告全文到 stdout/log,不联网。
- 非空时:POST {"msg_type":"text","content":{"text": 分片}},按行断片,片间 sleep 防限流。
"""
from __future__ import annotations

import time

import requests
from loguru import logger

from .config import FeishuConfig


def _split_lines(text: str, size: int) -> list[str]:
    """按行累积分片,尽量在换行处切,单片不超过 size 字符。"""
    chunks: list[str] = []
    cur = ""
    for line in text.splitlines(keepends=True):
        if cur and len(cur) + len(line) > size:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    return chunks


def send_report(text: str, cfg: FeishuConfig, dry_run: bool) -> dict:
    """推送报告,返回结果摘要 {dry_run, pushed, chunks, failed, message}。

    - dry_run 或 webhook 为空:打印全文到 stdout/log,不联网;
    - 否则按行分片逐片 POST,返回分片数/失败数。
    """
    url = (cfg.webhook_url or "").strip()
    if dry_run or not url:
        logger.info("dry-run 模式(未配置 webhook 或 --dry-run),报告全文如下:")
        print(text)
        return {
            "dry_run": True,
            "pushed": False,
            "chunks": 0,
            "failed": 0,
            "message": "dry-run(未推送)",
        }

    chunks = _split_lines(text, cfg.chunk_size)
    logger.info("飞书推送开始,共 {} 片", len(chunks))
    failed = 0
    for i, chunk in enumerate(chunks, 1):
        payload = {"msg_type": "text", "content": {"text": chunk}}
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            body = resp.json()
            code = body.get("StatusCode", body.get("code", 0))
            if code != 0:
                failed += 1
                logger.error("第 {}/{} 片飞书返回非 0 状态码: {}", i, len(chunks), body)
            else:
                logger.info("第 {}/{} 片发送成功", i, len(chunks))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.error("第 {}/{} 片发送失败: {}", i, len(chunks), exc)
        if i < len(chunks):
            time.sleep(0.5)  # 片间稍作停顿,避免触发限流

    ok = len(chunks) - failed
    return {
        "dry_run": False,
        "pushed": failed == 0 and len(chunks) > 0,
        "chunks": len(chunks),
        "failed": failed,
        "message": f"共 {len(chunks)} 片,成功 {ok},失败 {failed}",
    }