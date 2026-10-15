# 事件驱动交易台 · 第一阶段(只出信号,不下单)

代码:`src/stateverge/trading/eventdesk/`。本阶段**没有任何下单代码**。

## 流程
1. **calendar** — FRED 查当天有哪些目录内数据(CPI/核心CPI/非农=S,失业率/PPI/核心PCE=A,零售=B)。
2. **report** — 数据公布后:实际值(FRED 或手填)对比预期值(手填)→ 方向(risk_off / risk_on / none)+ 标的池价外一档合约建议 + 铁律。
3. **confirm** — 开盘前:纳指期货、VIX、标的盘前 ≥2% 同向,三者一致才写"可入场"。
4. **journal** — `data/trading/journal.json` 记录每笔(期权价与正股价分开),`stats.json` 自动算胜率、盈亏比、最大回撤;≥30 笔且胜率 >60%、盈亏比 >1.5 才讨论第二阶段。

## 用法
在 `data/trading/requests.json` 追加:
```json
{"status": "pending", "action": "calendar", "date": "2026-10-16"}
{"status": "pending", "action": "report", "event": "ppi"}                     ← 预期值用 ForexFactory
{"status": "pending", "action": "report", "event": "ppi", "consensus": 0.3}   ← 手填覆盖
{"status": "pending", "action": "confirm", "report": "reports/2026-10-16-ppi.json"}
```
推送后 `trading-desk.yml` 运行,结果写回 `data/trading/reports/`。

## 需要
- GitHub Secret `FRED_API_KEY`(免费:fred.stlouisfed.org → My Account → API Keys)。
- 预期值:默认读 ForexFactory 公开周历(Forecast 栏,免费);请求里写 `consensus` 则以手填为准(Moomoo 核对后填)。自动取的预期值战报会标注来源并提示核对。
- 标的池:`data/trading/config.json`。

## 已知限制
- FRED 数据通常比官方发布晚几分钟到几小时;要即时值请在 report 里手填 `actual`。
- 个股只有周期权,0DTE 只有 SPY/QQQ 等指数 ETF。
- 合约建议只按价外一档行权价,未检查买卖价差、IV;下单前自己核对(价差 ≤10%,只用限价单)。
