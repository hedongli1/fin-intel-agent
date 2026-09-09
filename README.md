# fin-intel-agent 🐂📈

金融/股票领域的定时监控 Agent —— 一个纯后端 Python 项目，自动盯盘并推送到飞书群。

**它干什么**：每天盘前/盘后自动抓取 A 股最活跃的一批股票，做五维深度分析（行情、财务、新闻舆情、机构研报、行业板块），生成一份通俗易懂的 Markdown 报告，推到你的飞书群里。不用手动盯盘，打开飞书就能看到今天的市场动向。

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![akshare](https://img.shields.io/badge/数据源-akshare-orange)](https://github.com/akfamily/akshare)

## 一句话说清它是怎么工作的

```
排行榜前列股票（成交额/涨幅/换手率/量比/总市值 各取前20名）
        ↓ 五榜并集去重，形成动态监控池（上限100只）
        ↓ 对每只股票做五维深采集
        ↓ 汇总成 Markdown 报告
        ↓ 推送到飞书群机器人
```

**核心思路**：监控对象不是手动维护的"自选股"，而是**市场当天最活跃的资金所在**——哪些股票成交额大、涨得猛、换手高、量比异动、市值居前，它就盯哪些。这样永远不会错过热点。

## 特性

| 能力 | 说明 |
|------|------|
| 🎯 动态监控池 | 排行榜前列股票并集去重，紧跟市场最活跃资金，无需维护 watchlist |
| 🔍 五维深采集 | 行情 / 基本面财务 / 新闻舆情 / 研报评级 / 行业板块，一次看全 |
| 🛡️ 异常隔离 | 每个接口都 try/except 包住，单只股票失败不影响整轮报告 |
| 🔀 多源降级 | 东财不可达时自动切换腾讯/新浪/同花顺/巨潮等源，行情数据不中断 |
| 📦 单一数据源 | 全部走 akshare 公开接口，无需申请任何第三方 API key |
| ⏰ 定时调度 | APScheduler 盘前/盘后 cron，非交易日自动跳过 |
| 📨 飞书分片推送 | 超长报告按行分片发送防限流，无 webhook 时自动降级打印 |

## 快速开始

**环境要求**：Python 3.11+（推荐 3.12）

```bash
# 1. 装依赖
python3 -m venv .venv && source .venv/bin/activate   # Windows 用 .venv\Scripts\activate
pip install -r requirements.txt

# 2. 跑一次试试（先不推送，打印到控制台）
python3 -m src.fin_intel --once --dry-run

# 3. 正式推送：先配好飞书 webhook，再跑
python3 -m src.fin_intel --once

# 4. 常驻定时（盘前 09:30 / 盘后 15:30 自动跑）
python3 -m src.fin_intel
```

### 怎么配飞书 webhook

1. 飞书群 → 设置 → 群机器人 → 添加「自定义机器人」→ 复制 Webhook 地址（形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx`）
2. 填进 `config/config.yaml` 的 `feishu.webhook_url`，或复制 `.env.example` 为 `.env` 填 `FEISHU_WEBHOOK_URL=`
3. 先用 `--once` 验证能收到消息，再上定时

## 配置（`config/config.yaml`）

```yaml
ranked_pool:
  enabled: true                           # false 则回退固定 watchlist
  rank_by_list: [成交额, 涨幅, 换手率, 量比, 总市值]   # 可增删/调整顺序
  top_n: 20                               # 每榜前 N 名
  max_pool_size: 100                      # 五榜并集去重后的监控池上限
  alert_pct: 9.0                          # |涨跌幅| >= 9% 标记异动并附新闻
  news_per_alert: 1                       # 每只异动股附最新新闻条数
```

## 目录结构

```
fin-intel-agent/
├── config/config.yaml          # 配置（含详细中文注释）
├── .env.example                # 环境变量示例
├── src/fin_intel/
│   ├── __main__.py             # 入口：--once / --kind / --dry-run / 常驻
│   ├── config.py               # pydantic 加载/校验配置
│   ├── collectors/             # 采集器（rank 监控池 + 五维）
│   ├── aggregator.py           # 汇总为 Markdown 报告
│   ├── notifier.py             # 飞书 webhook 推送（含分片）
│   ├── scheduler.py            # APScheduler 定时
│   └── trade_calendar.py       # 交易日判断
├── requirements.txt
├── Dockerfile
└── deploy/fin-intel-agent.service   # systemd 示例
```

## Docker 部署

```bash
docker build -t fin-intel-agent .
docker run -d --name fin-intel-agent \
  -v "$(pwd)/config/config.yaml:/app/config/config.yaml" \
  fin-intel-agent
```

## 数据来源与免责声明

- 数据全部来自 [akshare](https://github.com/akfamily/akshare) 公开接口，无需申请密钥。
- **行情**：腾讯全 A 实时快照（最新价/涨跌幅/振幅/换手率/量比/成交额/PE/PB/总市值/多周期涨幅/主力资金流），新浪日线兜底。
- **基本面**：巨潮公司概况 + 财务摘要（归母净利润/营收/扣非/股东权益/现金流/每股收益/ROE/ROA/毛利率/销售净利率/资产负债率，含上期对比）。
- **新闻**：东方财富个股新闻（含内容全文摘要）+ 财联社电报快讯。
- **研报**：东方财富研报（标题/机构/评级/盈利预测 2026-2028/行业/PDF 链接）。
- **板块**：同花顺行业（净流入/涨跌家数/领涨股）+ 新浪行业补充。
- 行情/财务数据可能存在**延迟或误差**，请以交易所及上市公司官方披露为准。
- **本报告仅供研究参考，不构成任何投资建议。** 据此操作产生的盈亏与项目无关。
