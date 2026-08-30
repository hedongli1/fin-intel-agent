---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dc6659ba1c54cf2eb05f67c93dd94cf6_870998cf9f9f11f1a238525400e6dd8f
    ReservedCode1: y8QkWFyCpY5s6P07AzqtwoJ04eIBli5nqKon+PyTsge8ENdKqhWpRZIorv/EKxZ/mRwhpAW976zvOo7TVoSYJlyDRMj9wKdCzHnko2Nv+kdfbXzc4urFI2/f7TnJkj3cqqg95XTmU3dk1gZ8Dd0zR4IDaKdLGkq4J4SDio/spLka0B/faWVoapPpFjk=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dc6659ba1c54cf2eb05f67c93dd94cf6_870998cf9f9f11f1a238525400e6dd8f
    ReservedCode2: y8QkWFyCpY5s6P07AzqtwoJ04eIBli5nqKon+PyTsge8ENdKqhWpRZIorv/EKxZ/mRwhpAW976zvOo7TVoSYJlyDRMj9wKdCzHnko2Nv+kdfbXzc4urFI2/f7TnJkj3cqqg95XTmU3dk1gZ8Dd0zR4IDaKdLGkq4J4SDio/spLka0B/faWVoapPpFjk=
---

# STATUS — 项目状态与交付记录

## 交付状态:动态排行榜监控池 + 五维深采集改造完成(最新)

- 从「全A实时排行前100榜单监控」(仅 rank 采集器)回退并升级为「五维深采集 + 动态排行榜监控池」模式。
- 新增 `src/fin_intel/collectors/rank.py` 的 `RankedPoolCollector`:按 `ranked_pool.rank_by_list`
  各榜(成交额/涨幅/换手率/量比/总市值,默认每榜前 20)降序取前 N 名 -> 五榜并集去重 ->
  动态监控池(默认上限 100,超出按"命中榜数 + 成交额"截断);池内异动股自动附最新新闻。
- `schema.py`:移除 `rank` 维度,`KNOWN_DIMENSIONS` 恢复五维;`CollectorResult` 新增 `boards` 字段。
- `config.py`:新增 `RankedPoolConfig`(rank_by_list / top_n / max_pool_size / alert_pct / news_per_alert),
  删除旧 `RankingConfig`(四选一 rank_by);`Config` 新增 `ranked_pool` 段;`.env` 覆盖支持
  `RANK_BY_LIST` / `TOP_N` / `MAX_POOL_SIZE` / `ALERT_PCT` 等。
- `aggregator.py`:新增 `aggregate_pool`(五榜快照 + 池内异动 + 池内股票五维情报 + 免责声明);
  旧 `aggregate` 保留供 `ranked_pool.enabled=false` 时回退使用。
- `__main__.py`:动态池模式为主流程(池生成 -> 五维深采集 -> 聚合 -> 推送),旧 watchlist 模式为兼容分支。
- `config/config.yaml`:新增 `ranked_pool` 段,`collectors.enabled` 恢复五维全开;保留 schedule/feishu/alerts/app。
- `.env` / `.env.example`:按新配置更新(`FEISHU_WEBHOOK_URL` 留空;`RANK_BY_LIST` 五榜;`TOP_N=20`;
  `MAX_POOL_SIZE=100`;`ALERT_PCT=9.0`;`DRY_RUN`;`PREMARKET_CRON`;`AFTERMARKET_CRON`;`LOG_LEVEL`)。
- README.md / docs/ARCHITECTURE.md / docs/CHANGELOG.md 同步更新,描述与改造后行为一致。

## 自测结论(本次改造,纯逻辑 mock 验证,未联网)

- 命令:`python -c "import src.fin_intel.*"` + `load_config` + mock `RankedPoolCollector` + `aggregate_pool`。
- 环境:Python 3.11.8 / akshare / pandas / pydantic 2.x(依赖已按 requirements.txt 安装)。
- 结果:
  1. **配置加载**:新 `config.yaml` + 新 `.env` 加载成功,`ranked_pool` 五榜/top_n=20/max_pool_size=100
     均正确,`collectors.enabled` 恢复五维全开;
  2. **池生成**:mock 6 只股票 5 榜各取前 3 名,并集去重得到 5 只,命中榜数与命中榜列表正确,
     超上限按"命中榜数+成交额"截断逻辑生效;
  3. **异动筛选**:`|涨跌幅|>=alert_pct` 的股票正确标记进 alerts 并附新闻;
  4. **报告渲染**:`aggregate_pool` 输出含五榜快照表头/池内异动/池内五维情报/生成时间/数据来源/免责声明,
     等宽列对齐正常;
  5. **异常隔离**:监控池失败时报告输出明确中文错误并跳过五维采集,不抛未捕获异常。

## 既往记录(保留)

### v0.1.0 五维情报守望者(固定 watchlist 模式,首版)

- 完整骨架:配置/数据模型/五维采集器/聚合器/通知器/调度器/交易日判断/入口。
- 五维情报采集:行情、基本面、新闻舆情、研报评级、行业板块。
- 飞书 webhook 推送(按行分片、片间 sleep、dry-run 降级)。
- APScheduler 盘前(09:30)/盘后(15:30)cron 调度,非交易日自动跳过。
- 自测:三只标杆股(贵州茅台/宁德时代/中国平安)五维均回流真实数据,EXIT=0。

### v0.1.1 全A TOP100 榜单监控模式(中间态,已被本次改造取代)

- 新增 RankCollector(榜单口径可配:成交额/涨跌幅/换手率/量比,默认成交额,take TOP100)。
- schema 加入 rank 维度;config 新增 RankingConfig;aggregator 新增 aggregate_rank;__main__ 增加榜单分支。
- mock 单测:排序/top_n 截断/成交额转亿/异动筛选/新闻探测降级/aggregator 格式全部通过。

## 已知环境相关问题

1. **pandas 3.x 兼容性**:pandas 3.0 默认启用 pyarrow 字符串后端,会触发 akshare 的
   `ArrowInvalid` 报错。已在 `requirements.txt` 固定 `pandas>=2.1,<3.0`。
2. **requirements.txt 编码**:pip 在 GBK 默认编码环境下解析含中文注释的 requirements 会
   `UnicodeDecodeError`;已将注释改为 ASCII。
3. **东财 push2.\* 接口不可达**:某些沙箱环境对 `push2.eastmoney.com` 连接被远端断开
   (`ConnectionError: RemoteDisconnected`)。监控池采集器不做降级(榜单唯一合理数据源),
   而是返回 `status=error` + 明确中文提示;本地 Windows 下正常。
4. **五维接口可达性**:沙箱断连时,五维中依赖 push2 的接口(如 news 的东财新闻)同样可能失败,
   但因异常隔离会以「⚠ 采集失败」呈现,不影响其它维度与报告生成。

## 仍需用户完成

- 填写 `config/config.yaml` 的 `feishu.webhook_url`(留空则始终 dry-run 打印)。
- 按需调整 `ranked_pool`(rank_by_list / top_n / max_pool_size / alert_pct / news_per_alert)。
- 盘中实时监控可把 `schedule` 的 cron 调密,例如 `*/10 9-15 * * 1-5`(每10分钟)。
- 正式推送前把 `app.dry_run` 改为 `false`。

## 既有保证(未破坏)

- **.env 不存在照常运行**:`load_config` 用 `dotenv_values` 读 .env,文件不存在返回空 dict,
  走 config.yaml 默认;自测确认无 .env 时 exit=0。
- **webhook 空 = 只打印不推送**:`notifier.send_report` 在 webhook 为空或 dry_run 时打印到 stdout。
- **旧 watchlist 模式可回退**:`ranked_pool.enabled=false` 时走旧五维聚合分支,向后兼容。
*（内容由AI生成，仅供参考）*
