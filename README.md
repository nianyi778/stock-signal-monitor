# Stock Signal Monitor

> 美股技术信号自动扫描 + Telegram 交互机器人 + AI 分析。每日收盘后自动检测多指标共振，推送 LLM 生成的中文分析摘要。解决"没空盯盘"的问题。

![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-latest-green)
![Tests](https://img.shields.io/badge/Tests-107%20passed-brightgreen)
![Version](https://img.shields.io/badge/version-1.5.5-orange)
![Docker](https://img.shields.io/badge/Docker-ghcr.io-blue)

---

## 为什么做这个

单一技术指标的胜率只有 40–60%，信号太多、噪音太大。核心逻辑是**信号共振降噪**：

- 单指标触发 → 记录数据库，不打扰
- **2+ 指标同向共振 → 才推 Telegram**，附 LLM 中文分析
- 在东京没空盯美股盘，需要及时且准确的信号

---

## 功能概览

### 📡 自动扫描（每日）
- 每日 17:00 ET（美股收盘后）自动触发
- MACD / RSI / 均线交叉（20/50 EMA）/ 布林带 四大指标
- 共振检测：2+ 同向 → STRONG（推送），单指标 → WEAK，布林带 → WATCH
- GPT-4o-mini 生成分析摘要，仅推置信度 ≥ 60 的强信号
- **前置过滤**：SPY > 50日均线 且 VIX < 25（大盘多头）+ 个股价格 > 200日均线 + 成交量 ≥ 1.2× 均量
- **完整进/跑/割价格**：每条 STRONG 信号附带进场区间、目标价、止损价（ATR自适应）、风险回报比（R:R ≥ 1.5 才推送）

### 📱 Telegram Bot 交互

通过菜单按钮操作，无需记命令：

| 按钮 | 功能 |
|------|------|
| 📡 立即扫描 | 手动触发扫描，实时返回结果（含价格/置信度/升级提示） |
| 📋 查看信号 | 最近 10 条强信号历史，点击可查看个股信号详情 |
| 📈 我的自选 | 查看/删除自选股 |
| ➕ 添加股票 | 对话式输入（支持中文名：苹果、英伟达等） |
| 📅 大事日历 | 未来 14 天重大经济事件 |
| 💼 我的持仓 | 录入持仓（代码 均价 股数）、查看实时盈亏、记录卖出 |

### 📊 个股深度分析

点击信号中的「分析」按钮，获取：

```
📊 NVDA — NVIDIA Corporation

💰 价格概况
  🌅 盘前交易
  • 价格: $875.20  (+1.23%)
  • 盘前: $879.50  +0.49%
  • 成交量: 42.1M（均量 38.5M，1.1x）
  • 52周区间: $455.72 ~ $974.00（当前位于 78%）

🎯 操作建议
  🟢 加仓 — 多指标共振看涨
  📥 买入区间: $862.30 ~ $883.95
  🛑 止损参考: $836.43（支撑下方 3%）
  🎯 目标阻力: $920.00（+5.1%）

📐 支撑 / 阻力位
  • 布林上轨: $920.15 🔺阻力
  ▶️ 当前 $875.20
  • 50日均线: $862.30 🔻支撑

🧠 市场情绪
  • RSI(14): 34.2 — 偏空
  • 均线: MA50 > MA200 — 多头排列 🟢
  • 分析师(42人): 35买 / 5持有 / 2卖（看多 83%）
  • Beta: 1.68  ⚡高波动

⚠️ 近期大事预警
  🔴 财报: 2026-05-28（68天后）
      预估 EPS: $5.922（区间 $5.60 ~ $6.30）
  📅 FOMC 利率决议: 2026-05-06（46天后）
```

### 📅 经济日历（动态数据）

```
📅 美股大事日历 (未来14天)

7天后 | 03/27 周五 | 🎯 PCE 物价指数
  预期: 2.50% | 前值: 2.60%

14天后 | 04/03 周五 | 👷 非农就业数据
  预期: 148K | 前值: 151K
```

每天两次从 Finnhub 更新预期值（forecast）和前值（prior）。数据公布后自动补实际值。

### 📲 信号推送格式（v1.5.0）

STRONG 信号推送包含完整进/跑/割信息：

```
🟢 *NVDA* — 做多  `MACD+RSI`
置信度: 85%

📥 *进场区间:*  $875.00 ~ $886.00  _（3日内有效）_
🎯 *目标价:*     $950.00  R:R 2.1
🛑 *止损价:*     $844.00
💰 *分批止盈:*  $902.50  _（卖50%，止损移保本）_

📦 成交量: 1.4× 均量 ✅
🌍 大盘: BULL

_多指标共振看涨，ATR自适应止损，R:R 2.1..._
```

### 🤖 MCP Server（AI Agent 接入）

11 个 MCP tools，可挂载到 Claude Desktop / Claude Code：

| Tool | 功能 |
|------|------|
| `stock_monitor_get_watchlist` | 查看自选股 |
| `stock_monitor_add_stock` | 添加股票 |
| `stock_monitor_remove_stock` | 移除股票 |
| `stock_monitor_get_signals` | 查询信号（ticker/level/limit 过滤）|
| `stock_monitor_scan` | 立即扫描并返回结果 |
| `stock_monitor_analyze` | 完整个股分析 |
| `stock_monitor_get_calendar` | 大事日历（含预期+前值）|
| `stock_monitor_refresh_calendar` | 强制刷新 Finnhub 数据 |
| `stock_monitor_get_active_trades` | 查看监控中的活跃持仓信号（止损/目标/状态）|
| `stock_monitor_add_position` | 录入买入记录（代码 + 均价 + 股数）|
| `stock_monitor_get_positions` | 查看持仓实时盈亏汇总 |

---

## 快速部署

### Docker（推荐）

```bash
cp .env.example .env   # 填入配置
docker compose up -d
```

启动两个服务：
- `backend` — FastAPI + Telegram Bot + Scheduler（port 8000）
- `mcp` — MCP HTTP Server（port 8001）

### 本地开发

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

---

## 环境变量

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `DATABASE_URL` | ✅ | — | TiDB Serverless 连接串（MySQL 兼容）|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | 从 [@BotFather](https://t.me/botfather) 获取 |
| `TELEGRAM_CHAT_ID` | ✅ | — | 你的 Telegram Chat ID（数字）|
| `OPENAI_API_KEY` | ✅ | — | OpenAI / Sub2API Key |
| `OPENAI_BASE_URL` | — | `https://sub2api.nianyi.dpdns.org/v1` | LLM 代理地址 |
| `SCHEDULER_CRON_HOUR` | — | `17` | 每日扫描时间（美东时区）|
| `PUSH_MIN_CONFIDENCE` | — | `60` | 推送置信度阈值（0–100）|
| `FINNHUB_API_KEY` | — | — | Finnhub Key（财报日历 + 宏观预期值）|
| `LLM_MODEL_SIGNAL` | — | `gpt-4o-mini` | 信号摘要模型 |
| `LLM_MODEL_ANALYSIS` | — | `gpt-4.1` | 个股分析模型 |
| `PORTFOLIO_VALUE` | — | `0` | 账户总额（用于仓位占比计算，0=禁用）|

> 东京时区参考：`SCHEDULER_CRON_HOUR=17`（ET）≈ 次日早上 6:00–7:00 JST

---

## MCP 接入

### Claude Desktop（stdio）

`~/.claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "stock_monitor": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/path/to/stock-signal-monitor",
      "env": {
        "DATABASE_URL": "...",
        "TELEGRAM_BOT_TOKEN": "...",
        "TELEGRAM_CHAT_ID": "...",
        "OPENAI_API_KEY": "...",
        "FINNHUB_API_KEY": "..."
      }
    }
  }
}
```

### Claude Code / 远程（HTTP）

```json
{
  "mcpServers": {
    "stock_monitor": {
      "type": "http",
      "url": "http://your-server:8001/mcp"
    }
  }
}
```

---

## 信号分级逻辑

```
MACD 上穿零轴                    →  WEAK BUY
RSI < 30 超卖                    →  WEAK BUY
MACD + RSI 同时触发              →  STRONG BUY  ✈️ 推送 Telegram
  indicator: "MACD+RSI"
  confidence: max(单指标) + 20 加成，上限 95

布林带突破                        →  WATCH（仅记录）
任意信号 confidence < 60         →  不推送
```

---

## REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/stocks/` | 自选股列表 |
| `POST` | `/api/stocks/` | 添加股票 |
| `DELETE` | `/api/stocks/{ticker}` | 移除股票（软删除）|
| `POST` | `/api/stocks/scan` | 手动触发扫描 |
| `GET` | `/api/signals/` | 信号列表（`limit` / `level` 过滤）|
| `GET` | `/api/signals/{ticker}` | 指定股票信号历史 |

---

## 项目结构

```
app/
├── main.py              # FastAPI 入口 + lifespan
├── config.py            # 配置（Pydantic BaseSettings）
├── database.py          # SQLAlchemy + TiDB Serverless
├── models.py            # WatchlistItem / Signal / EconomicEvent
├── schemas.py           # Pydantic 响应模型
├── scheduler.py         # APScheduler：每日扫描 + 日历刷新
├── mcp_server.py        # MCP Server（fastmcp，stdio + HTTP）
├── api/
│   ├── stocks.py        # 自选股 CRUD + 触发扫描
│   └── signals.py       # 信号历史查询
├── data/
│   └── fetcher.py       # yfinance 行情获取
├── signals/
│   ├── indicators.py    # 纯函数：MACD / RSI / EMA / Bollinger
│   └── engine.py        # 信号引擎：检测 + 共振合并
├── llm/
│   └── summarizer.py    # LLM 分析摘要（含 fallback）
├── notifications/
│   └── telegram.py      # Telegram Bot API 推送
└── bot/
    ├── application.py   # Bot 生命周期
    ├── handlers.py      # 命令和按钮处理
    ├── keyboards.py     # Reply Keyboard + Inline 布局
    ├── analysis.py      # 个股深度分析
    └── calendar.py      # 经济日历（DB + Finnhub 爬取）
```

---

## 技术栈

| 层 | 技术 |
|----|------|
| 后端框架 | Python 3.12 · FastAPI · APScheduler |
| 行情数据 | yfinance（价格 / 盘前盘后 / 财报 / 分析师）|
| 技术指标 | pandas-ta（MACD / RSI / EMA / Bollinger Bands）|
| LLM | GPT-4o-mini（信号摘要）· GPT-4.1（个股分析）via Sub2API |
| Bot | python-telegram-bot v20（async polling）|
| 日历数据 | Finnhub API（财报 + 宏观预期）+ Fed/BLS 官方日程 |
| MCP | fastmcp 3.x（stdio + HTTP transport）|
| 数据库 | TiDB Serverless + SQLAlchemy + pymysql |
| CI/CD | GitHub Actions → ghcr.io（amd64 + arm64）|

---

## 测试

```bash
pytest tests/ -v
# 90 个单元测试，全部 mock，不依赖真实网络
```

---

## Roadmap

| 版本 | 状态 | 内容 |
|------|------|------|
| **v1.5.0** | ✅ 已发布 | 进/跑/割完整价格 · ATR自适应止损 · R:R过滤 · 持仓追踪 · 每日持仓监控 |
| **v1.5.1** | ✅ 已发布 | 仓位占比自动回退（未设账户总额时按持仓市值计算）|
| **v1.5.2** | ✅ 已发布 | 录入持仓自动加入自选股，卖出不影响自选 |
| **v1.5.3** | ✅ 已发布 | 算法修正：ATR 吊灯止损 · 52周高点阻力 · 卖空方向大盘过滤 · 进场区收窄 · EMA20/50 正确命名 |
| **v1.5.4** | ✅ 已发布 | 信号智能增强：多空辩论过滤 · Finnhub新闻情绪 · 持仓感知推送 · RSI事件检测 · 个股200d趋势过滤 · 布林带趋势感知 |
| **v1.5.5** | ✅ 已发布 | Telegram Markdown 容错推送：parse 失败自动降级纯文本重试，解决 LLM 生成内容含特殊字符导致推送失败 |
| **v2.0** | 规划中 | 回测引擎 · Walk-Forward权重优化 · 自学习循环（月度自动调参）|
| **v3.0** | 规划中 | 盘中实时监控 · React看板 · Finnhub WebSocket新闻流 |
| **v4.0** | 远期 | 多用户 SaaS · 策略社区 · 付费订阅 |
