# Signal Intelligence V2 — Design Spec

**Date:** 2026-03-21
**Revision:** 2 (post spec-review)
**Status:** Approved
**Goal:** 系统给出的建议操盘人直接信任就能拿到跑赢大盘的胜率

---

## 背景与目标

当前系统（v1.4.0）已能检测技术信号并推送 Telegram，但存在以下问题：
- 打分权重未经回测验证
- 只给入场建议，缺少目标价、止损价、持仓期间监控
- 无法量化验证准确率，也无法自我优化

**目标用户：** 波段操作为主（1~4周），接受中长线，目标跑赢 S&P500（以 SPY 为基准）。
**成功标准：** 系统信号期望值 > SPY 同期表现，风险回报比 ≥ 1.5。

---

## 子系统一：完整信号输出（进 / 跑 / 割）

### 信号过滤前置条件（满足所有条件才推送）

| 条件 | 规则 |
|------|------|
| 市场环境 | SPY > SPY 50日均线 **且** VIX < 25，否则不开新多头 |
| 成交量确认 | 信号当日成交量 ≥ 20日均量的 1.2 倍 |
| 风险回报比 | R:R ≥ 1.5，否则不推送 |
| 财报风险 | 距下次财报 ≤ 5 天 → 推送但标注 ⚠️ 高危，建议减半仓 |

### 价格计算规则

| 字段 | 计算方式 |
|------|---------|
| 进场区间下限 | 最近支撑位上方 0.2% |
| 进场区间上限 | **信号日**收盘价上方 0.5%（固定值，3 个交易日内均以此为基准）。任意一天开盘价 > 此值，立即标记 `status=EXPIRED`，不随每日价格重置 |
| 目标价 | 当前价上方最近阻力位，优先级：布林上轨 → 20日高点 → 52周高；要求满足 R:R ≥ 1.5 |
| 止损价 | 最近支撑位 - 1.5 × ATR(14)（自适应波动率，替代固定 3%） |
| 预警线 | 止损价上方 1.5×ATR × 0.5（给操作缓冲） |
| 分批止盈 | 目标价的 95%，触发时建议卖出 50% 仓位，剩余仓位止损上移至进场中间价（保本线）|
| 风险回报比 | (目标价 - 进场中间价) / (进场中间价 - 止损价) |
| 信号有效期 | 3 个交易日，过期自动关闭 `status=EXPIRED` |

### 仓位建议 + 持仓追踪

用户通过 Telegram 输入实际持仓：`NVDA 882.5 10`（代码 + 买入均价 + 股数）。

系统记录并实时计算：

```
NVDA  10股 @ $882.50 买入
  当前价:  $910.00
  持仓盈亏: +$275.00  (+3.1%)
  仓位占比: 9.1%（总资产 $100,000）

  🎯 目标价 $950 → 预期盈利 +$675 (+7.7%)
  🛑 止损价 $844 → 最大亏损 -$385 (-4.3%)
  📊 风险回报: 1 : 1.8
```

建议仓位公式（新开仓时参考）：
```
建议股数 = (账户 × 1%) / (进场中间价 - 止损价)
```

配置：`.env` 中 `PORTFOLIO_VALUE=100000`（账户总额，用于计算仓位占比和风险金额）。

`active_trades` 额外记录：
- `actual_entry_price` — 用户实际买入价
- `shares` — 持有股数
- 每日更新 `current_pnl_pct`、`position_pct`

### 推送格式

```
🟢 NVDA — 多指标共振，建议入场

📥 进场区间:  $875 ~ $882  （3日内有效）
🎯 目标价:   $950  (+7.7%，R:R 2.1)
🛑 止损价:   $844  (-4.3%，ATR 自适应)
📊 风险回报:  1 : 2.1
⏱ 预计持仓:  2~4 周（波段）
📦 成交量:   1.4× 均量 ✅
🌍 市场环境: SPY 多头 ✅

━━ 持仓期间监控 ━━
  ⚠️  触及 $849 → 预警，关注
  🔴  触及 $844 → 止损，割
  ✅  触及 $903 → 分批止盈区间
  ✅  触及 $950 → 目标达成
```

### 持仓监控

每日收盘后调度器检查所有 `status=ACTIVE` 的 ActiveTrade：
1. 收盘价 ≤ 止损价 → 推"🔴 止损提醒" + `status=STOPPED`
2. 收盘价 ≥ 目标价 → 推"✅ 目标达成" + `status=TARGET_HIT`
3. 收盘价 ≤ 预警线 → 推"⚠️ 预警，建议关注"（不关闭）
4. 收盘价 ≥ 分批止盈（目标价×95%）→ 推"💰 建议卖出 50%，剩余止损上移至保本线"（不关闭，将 stop_price 更新为进场中间价）
5. **财报预警（active trade 专用）：**
   - 距财报 7 天 → 推"📅 财报预警（7天），建议缩减至 50% 仓位"
   - 距财报 2 天 → 推"🔴 财报临近（2天），建议清仓规避缺口风险"
   - 财报日过后 → 清除 `earnings_date` 标记，恢复正常监控
   - 若 yfinance 更新了财报日期，每日同步 `earnings_date` 字段
6. 开盘价 > `entry_high` 且 `status=ACTIVE` 且尚未入场 → `status=EXPIRED`
7. 当前日期 > `valid_until` → `status=EXPIRED`

---

## 子系统二：回测引擎

### 关键设计原则

**防止前视偏差（Lookahead Bias）：**
在历史时间点 T 计算指标时，**只能使用 T 时刻及之前的数据**。所有 rolling window 计算必须通过 `.shift(1)` 或滚动切片，确保 T 时刻"看不到未来"。

**悲观成交假设：**
- 入场填价：信号日次日的 `open`（不用收盘价，贴近真实）
- 止损填价：止损价 - 0.1%（模拟滑点）
- 目标填价：目标价（限价单）

**防幸存者偏差：**
文档标注：yfinance 只含现存股票，回测结果会有轻微乐观偏差。建议在实际胜率上折扣 3~5%。

### 评估结果分类

| 结果 | 定义 | 处理方式 |
|------|------|---------|
| WIN | 20日内先触及目标价 | 记录实际持仓天数 + 实际盈利 |
| LOSS | 20日内先触及止损价 | 记录亏损 |
| NEUTRAL | 20日均未触及 | 按第20日收盘价计算实际收益，不算胜负但计入期望值 |

超额收益 = 信号收益 - 同期 SPY 收益（逐笔对比）

### 权重优化：Walk-Forward 验证

**不使用简单网格搜索**（样本少时严重过拟合）。改用滚动窗口验证：

```
训练窗口: 前 18 个月信号
验证窗口: 后 6 个月（不参与训练，作为 out-of-sample 测试）
每次滚动 3 个月前进
```

更新权重的门槛：
- 有效信号数 ≥ 100（防止小样本失真）
- 验证集期望值 > 训练集期望值的 80%（防止过拟合）
- 新权重与旧权重差异 ≤ 30%（防止剧烈跳变）
- **冷启动（无历史权重时）：** 跳过 delta 检查，直接使用回测最优权重写入 v1；初始默认权重为 `{strong: 3.0, weak: 1.0, rsi: 1.5, ma: 0.5, analyst: 1.0, volume: 1.2}`

### 回测报告

```
回测结果 (2024-01 ~ 2025-12)，共 312 条信号
  胜: 189  败: 87  持平: 36
  胜率: 60.6%  |  平均盈利: +7.8%  |  平均亏损: -4.1%
  期望值: +3.1%/笔
  vs SPY 同期超额收益: +1.6%

  验证集 (out-of-sample): 胜率 58.2%，期望值 +2.7%
  注: yfinance 无幸存者偏差保护，实际可能低 3~5%

  最优权重:
    强信号: 3.5  弱信号: 1.0
    RSI: 1.8     MA位置: 0.5
    分析师: 0.7  成交量: 1.2
```

---

## 子系统三：自学习循环

### 架构

```
每日调度器
  ├─ 持仓监控
  └─ 每月 1 日：
       ├─ 运行 Walk-Forward 回测
       ├─ 检查是否满足更新门槛（≥100 信号 + OOS 验证通过）
       ├─ 插入新权重行（append-only，保留历史）
       └─ 权重变化 > 20% → 推 Telegram:
            "⚙️ 权重已更新 v3
             期望值: +2.8% → +3.4%（OOS 验证: +3.1%）
             基于 127 条信号"

run_signals() 执行时
  └─ 读取 signal_weights 表最新 valid_from 行（热加载）
```

---

## 数据库 Schema

### `active_trades`

```sql
id                  INT PK
signal_id           INT FK signals
ticker              VARCHAR(10)
entry_low           DECIMAL
entry_high          DECIMAL          -- 次日开盘 > 此值则作废
target_price        DECIMAL
stop_price          DECIMAL
warn_price          DECIMAL
partial_tp_price    DECIMAL          -- 分批止盈触发价
rr_ratio            DECIMAL
atr_at_signal       DECIMAL          -- 用于记录当时波动率
earnings_date       DATE             -- 下次财报日
regime_state        VARCHAR(10)      -- BULL/BEAR/NEUTRAL
volume_ratio        DECIMAL          -- 信号日成交量倍数
status              ENUM(ACTIVE, STOPPED, TARGET_HIT, EXPIRED, CANCELLED)
valid_until         DATE             -- 3 trading days
opened_at           DATETIME
closed_at           DATETIME
```

### `signal_outcomes`

```sql
id                  INT PK
signal_id           INT FK
ticker              VARCHAR(10)
direction           ENUM(BUY, SELL)
actual_entry_price  DECIMAL          -- 实际成交价（次日开盘）
exit_price          DECIMAL
outcome             ENUM(WIN, LOSS, NEUTRAL)
days_held           INT
pnl_pct             DECIMAL          -- 实际盈亏 %
spy_pnl_pct         DECIMAL          -- 同期 SPY %
excess_return       DECIMAL          -- pnl - spy
outcome_date        DATE
```

### `signal_weights`

```sql
id                  INT PK
version             INT              -- 递增，append-only
weights_json        JSON             -- {strong: 3.5, rsi: 1.8, ...}
backtest_winrate    DECIMAL
backtest_expectancy DECIMAL
oos_winrate         DECIMAL          -- out-of-sample 验证胜率
oos_expectancy      DECIMAL
sample_size         INT
valid_from          DATE
created_at          DATETIME
```

---

## 实现顺序

1. **回测引擎基础** — `app/backtest/engine.py`，先在已知策略上验证无前视偏差
2. **信号计算移植** — 把 entry/target/stop 逻辑在回测框架内实现并验证
3. **live 信号输出** — 将验证过的逻辑移植到 `engine.py` + `telegram.py`
4. **ActiveTrade 持仓监控** — 新增 DB 表 + scheduler 监控逻辑
5. **Walk-Forward 优化器** — `app/backtest/optimizer.py`
6. **自学习循环** — 权重热加载 + 月度自动更新
7. **Telegram 入口** — "运行回测"按钮 + 权重更新推送

---

## 不在本次范围

- 实时盘中监控
- 多用户 SaaS
- React 看板
- 期权策略
- 做空信号（只做多头）
