---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dc6659ba1c54cf2eb05f67c93dd94cf6_87f040339f9f11f1a238525400e6dd8f
    ReservedCode1: mwq3P9GsyWevQKx4DvdU391Pq0HeRHOP1qsZ8PemZSGmP7nHmUBvX6U7HtV9NdBKGi5nStkm9h9Z9BSzs27tpOPcnXbWozysuOivFf4BittpLck4dsoR2u8iWmbdNgYBI+iPd15qOELWXxjxJs83mYkliZqPTe3sKsSDc3KHn5k7I5qz8ByPM/+Bi1g=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dc6659ba1c54cf2eb05f67c93dd94cf6_87f040339f9f11f1a238525400e6dd8f
    ReservedCode2: mwq3P9GsyWevQKx4DvdU391Pq0HeRHOP1qsZ8PemZSGmP7nHmUBvX6U7HtV9NdBKGi5nStkm9h9Z9BSzs27tpOPcnXbWozysuOivFf4BittpLck4dsoR2u8iWmbdNgYBI+iPd15qOELWXxjxJs83mYkliZqPTe3sKsSDc3KHn5k7I5qz8ByPM/+Bi1g=
---

# CHANGELOG — 变更记录

## v0.1.0 — 2026-08-23

### 新增

- 完整项目骨架:配置/数据模型/五维采集器/聚合器/通知器/调度器/交易日判断/入口。
- 五维情报采集:行情、基本面、新闻舆情、研报评级、行业板块。
- 飞书 webhook 推送(按行分片、片间 sleep、dry-run 降级)。
- APScheduler 盘前(09:30)/盘后(15:30)cron 调度,非交易日自动跳过。
- Dockerfile 与 systemd unit 示例。
- README / STATUS / ARCHITECTURE 文档。

### 实际使用的 akshare 接口(冒烟测试校准后)

| 维度 | 规格建议接口 | 实际采用接口 | 变更原因 |
|------|-------------|-------------|---------|
| quote | `stock_zh_a_spot_em` | `stock_zh_a_daily`(新浪) | 测试环境 `push2.eastmoney.com` 被远端断开 |
| fundamental | `stock_individual_info_em` | `stock_profile_cninfo`(巨潮) | 同上(push2 不可达) |
| news | `stock_news_em` + `stock_notice_report` + `stock_info_global_cls` | `stock_news_em` + `stock_info_global_cls` | `stock_notice_report` 在 akshare 1.18.92 抛 `KeyError`,移除 |
| research | `stock_research_report_em` | 同左(列名校准为 报告名称/机构/东财评级/日期) | 仅列名映射调整 |
| sector | `stock_board_industry_name_em` | `stock_board_industry_summary_ths`(同花顺) + `stock_individual_basic_info_xq`(雪球,取板块) | push2 不可达,改同花顺 |

### 依赖调整

- `requirements.txt` 增加 `pandas>=2.1,<3.0`:pandas 3.x 默认 pyarrow 字符串后端会触发 akshare 的
  `ArrowInvalid: invalid escape sequence` 报错。

## v0.2.0 — 2026-08-24

### 变更:全A TOP100 榜单监控 -> 动态排行榜监控池 + 五维深采集

- 监控对象改为动态生成:成交额榜/涨幅榜/换手率榜/量比榜/总市值榜各取前 N 名(默认 20)
  -> 五榜并集去重 -> 动态监控池(默认上限 100,超出按"命中榜数 + 成交额"截断),
  不再使用固定 watchlist(旧 watchlist 模式保留为 `ranked_pool.enabled=false` 兼容分支)。
- `collectors/rank.py`:RankCollector 重写为 RankedPoolCollector,输出各榜快照(boards)+
  动态监控池(items)+ 池内异动股新闻(alerts);榜单口径支持 成交额/涨幅/换手率/量比/总市值 任意组合。
- `schema.py`:移除 rank 维度,`KNOWN_DIMENSIONS` 恢复五维;`CollectorResult` 新增 `boards` 字段。
- `config.py`:新增 `RankedPoolConfig`(enabled/rank_by_list/top_n/max_pool_size/alert_pct/news_per_alert),
  删除旧 `RankingConfig`;`_apply_env_overrides` 支持 `RANK_BY_LIST`(逗号分隔,优先)、
  `RANK_BY`(单值,兼容)、`TOP_N`、`MAX_POOL_SIZE`、`ALERT_PCT` 等环境变量覆盖。
- `aggregator.py`:新增 `aggregate_pool`(五榜快照等宽表 + 池内异动 + 池内股票五维情报 + 免责声明);
  旧 `aggregate` 保留兼容。
- `__main__.py`:主流程改为 池生成 -> 池内五维深采集 -> 聚合 -> 推送;`--once/--kind/--dry-run` 不变。
- `config/config.yaml`:新增 `ranked_pool` 段;`collectors.enabled` 恢复五维全开
  `[quote, fundamental, news, research, sector]`。
- `.env` / `.env.example`:新增 `RANK_BY_LIST` / `MAX_POOL_SIZE`,`TOP_N` 默认 20,
  `FEISHU_WEBHOOK_URL` 留空(dry-run);`PREMARKET_CRON` / `AFTERMARKET_CRON` / `DRY_RUN` / `LOG_LEVEL` 按默认填写。
- README.md / docs/ARCHITECTURE.md / docs/STATUS.md 同步更新。

### 依赖调整

- `requirements.txt` 注释改为 ASCII(GBK 编码环境下 pip 解析中文注释会 UnicodeDecodeError),
  依赖版本约束不变。
*（内容由AI生成，仅供参考）*
