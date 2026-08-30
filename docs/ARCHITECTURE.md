---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: dc6659ba1c54cf2eb05f67c93dd94cf6_861d88bf9f9f11f1a238525400e6dd8f
    ReservedCode1: K6dm2c4+YrhP3eOAVzJLIGedsRAEe0nVdvLKVt+iJPmD/HVMy2I6lQJsMK2qKy7lxAm4KqYOkNtDJxKoj9Z+4W/leKXHTqeSQJw2q1l8td8j2UDHg+zFkHcijUwJpDan/1ZunNo5s3VprhjUdOFok0rJaHQDcezz+TMG8UBD26bvwPg0piVy2piAoTY=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: dc6659ba1c54cf2eb05f67c93dd94cf6_861d88bf9f9f11f1a238525400e6dd8f
    ReservedCode2: K6dm2c4+YrhP3eOAVzJLIGedsRAEe0nVdvLKVt+iJPmD/HVMy2I6lQJsMK2qKy7lxAm4KqYOkNtDJxKoj9Z+4W/leKXHTqeSQJw2q1l8td8j2UDHg+zFkHcijUwJpDan/1ZunNo5s3VprhjUdOFok0rJaHQDcezz+TMG8UBD26bvwPg0piVy2piAoTY=
---

# ARCHITECTURE — 架构说明

## 分层

```
__main__.py       入口/编排:加载配置 -> 初始化日志 -> --once 或常驻调度
   │
   ├─ config.py    pydantic 加载校验 config/config.yaml(.env 环境变量可覆盖)
   ├─ scheduler.py APScheduler BackgroundScheduler(盘前/盘后 cron)
   ├─ run_report   一轮任务的编排函数(定义在 __main__.py)
   │    ├─ collectors/rank.py       动态排行榜监控池采集器(五榜并集去重)
   │    ├─ collectors/quote.py      五维①行情/股价
   │    ├─ collectors/fundamental.py 五维②基本面/财务
   │    ├─ collectors/news.py       五维③新闻/公告/舆情
   │    ├─ collectors/research.py   五维④研报/机构评级
   │    ├─ collectors/sector.py     五维⑤行业/板块
   │    ├─ aggregator.py            聚合 -> 纯文本 Markdown 报告
   │    ├─ notifier.py              飞书 webhook 推送(分片)
   │    └─ trade_calendar.py 交易日判断(可选,跳过)
   └─ schema.py    共享数据模型(CollectorResult / Report / KNOWN_DIMENSIONS)
```

## 数据流(一轮报告,动态排行榜监控池模式)

```
run_report(kind)
  1. 交易日判断(skip_non_trading_days=true 且非交易日 -> 直接 return)
  2. RankedPoolCollector.collect()(异常已在 base.collect 内捕获)
        a. 拉东财全A实时快照 stock_zh_a_spot_em
        b. 按 ranked_pool.rank_by_list 各榜口径降序取前 top_n -> 各榜快照(boards)
        c. 五榜并集去重 -> 动态监控池(items);超过 max_pool_size 按"命中榜数+成交额"截断
        d. 池内 |涨跌幅|>=alert_pct 的异动股逐只取 stock_news_em 最新新闻(alerts)
  3. 对池内每只股票执行 collectors.enabled 中的五维深采集
        dict[code][dimension] = CollectorResult(异常已在 base.collect 内捕获)
  4. aggregator.aggregate_pool(...) -> 纯文本报告字符串
        (五榜快照 + 池内异动 + 池内股票五维情报 + 生成时间/数据来源/免责声明)
  5. notifier.send_report(..., dry_run)
        webhook 为空或 dry_run -> print 全文
        否则按 chunk_size 分片 POST 到飞书
```

旧固定 watchlist 五维模式(ranked_pool.enabled=false)保留为向后兼容分支,数据流与上类似但第 2 步改为直接遍历 watchlist。

## 异常隔离(硬性要求)

- 采集层:`BaseCollector.collect` 统一 try/except,任何子类异常都被转为 `status=error` 的
  `CollectorResult`,并通过 `loguru` 记录耗时与状态。
- 接口层:`call_with_timeout` 在**守护线程**中调用 akshare 接口,`join(timeout)` 超时抛
  `TimeoutError`;守护线程不阻塞进程退出,防止慢接口(如公告)卡死任务。
- 聚合层:单维度/单只股票 `status=error` 不影响其它维度与其它股票,报告中以「⚠ 采集失败:原因」呈现。
- 池生成层:五榜并集与异动筛选相互独立;单只异动股新闻失败仅跳过该股新闻,保留异动标记。
- 兼容性:每个接口较于 `getattr(ak, "func", None)` 探测,缺失即返回明确 error;DataFrame 一律
  `.to_dict("records")` 且只取前 top_n 条,不塞大块数据。

## 扩展点

- 新增榜单口径:在 `config.py` 的 `RANK_BY_ALLOWED` 与 `collectors/rank.py` 的
  `RANK_COLUMN`/`BOARD_LABEL` 补映射即可(需东财快照存在对应列)。
- 新增维度:在 `src/fin_intel/collectors/` 下加一个继承 `BaseCollector` 的类,实现 `_collect`,
  再到 `collectors/__init__.py` 的 `_COLLECTOR_CLASSES` 注册、`schema.KNOWN_DIMENSIONS` 与
  `aggregator.DIMENSION_TITLES` 补一条标题即可。
- 换消息通道:新增类似 `notifier.py` 的模块(如钉钉/企业微信),替换 `run_report` 中的推送调用。
- 换数据源:某个 akshare 接口失效时,只需改对应 collector 内部实现,外层契约不变。

## 关键决策

- **动态监控池**:监控对象由五榜(成交额/涨幅/换手率/量比/总市值)前列股票并集去重动态生成,
  紧跟市场最活跃资金,无需手动维护 watchlist。
- **单一 akshare**:榜单与五维数据都走 akshare 公开接口,不接搜索 API、不申请密钥。
- **纯文本报告**:飞书 text 消息不支持真正的 Markdown 渲染,报告用 `\n` + `---` 分隔线 + bullet 的
  纯文本排版(榜单快照用等宽列对齐),保证多端可读。
- **配置外置 + pydantic**:webhook 可空(dry_run)、code 6 位数字、enabled 维度名与榜单口径
  合法性均在启动即校验。
*（内容由AI生成，仅供参考）*
