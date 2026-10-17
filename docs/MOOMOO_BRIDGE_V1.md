# Moomoo 桥 · 第一阶段(只用模拟盘)

代码:`src/stateverge/trading/moomoo_bridge/`。在**你自己的电脑**上运行(Moomoo OpenD 只能本机连)。

**没有真钱路径**:只有 `dry`(拉真实报价,不发单)和 `paper`(Moomoo 模拟账户 `TrdEnv.SIMULATE`)。
真仓要等 `stats.json` 达标(≥30 笔、胜率 >60%、盈亏比 >1.5)并由你明确授权后另行开发。

## 每日流程
1. 你给出当天策略(在聊天里讲,或自己写 JSON)→ `data/trading/plans/<日期>.json`(格式见 `EXAMPLE.json`)。
2. 电脑上 `git pull`,先检查:`python -m stateverge.trading.moomoo_bridge --plan data/trading/plans/2026-10-19.json --check`
3. 开盘前启动:`... --mode paper`(或 `--mode dry` 只看不下单)。程序会:
   - 再查铁律:≤2 笔、单张 ≤$400、总 ≤$1,000、不同时买 put+call、今日亏损未达 $1,000、计划日期 = 今天
   - 等到入场窗口(默认 09:30–10:00 ET),过了窗口就不入场
   - `require_confirm` 时做开盘确认(纳指期货、VIX、标的盘前 ≥2% 同向)
   - 找合约 → 买卖价差 >10% 跳过 → **限价单**,价格 = min(卖一价, 你的 max_price),90 秒不成交撤单
   - 每 15 秒检查:+100% 平一半;余下由高位回吐 40% 全走;−50% 全斩;`flat_by_et` 清仓
   - 每笔卖出写入 `data/trading/journal.json`(`paper: true`)
4. 收市后 `git add data/trading/journal.json && git commit && git push` → GitHub 自动更新 `stats.json`。

## 快进快出(默认)
- 只买期权;`underlying_min/max` 只是用正股价做入场触发,不买正股。
- `strike`: 数字,或 `atm` / `otm1` / `otm2`(入场时按实时价从期权链挑)。
- `expiry`: 日期,或 `0dte`(当天到期,只有 SPY/QQQ 等有)/ `weekly`(最近周五)。
- `exit.max_hold_min`: 入场后满 N 分钟无论盈亏都走。默认当日 `flat_by_et` 前全部清仓,不过夜。
- 出场每 5 秒检查一次。

## 分批 / 持仓过夜(可选)
- 腿上加 `underlying_min` / `underlying_max`:正股不在区间就不入场(例:RKLB ≥54 才入,跌穿 53 不追 → `"underlying_min": 54`)。
- `exit.hold_until`:最迟哪天清仓(当天 `flat_by_et`)。非最后一天 16:00 ET 会把仓位存进 `data/trading/open_positions.json` 并退出。
- 第二天:`--plan <原计划> --mode paper --resume` 继续管理出场(09:30 前会等开盘)。
- 第二批是一个新计划文件;程序会把隔夜仓位计入 $1,000 总预算,同一标的同方向再买会被拒绝(不加仓摊平)。

## 电脑设置(一次)
1. 下载安装 Moomoo OpenD(moomoo 官网 → OpenAPI),用你的 moomoo 账号登录,端口默认 11111。
2. 在 moomoo App 开通模拟交易(美股期权)。
3. `git clone` 本仓库,`pip install moomoo-api`。
4. 在仓库根目录运行,并设 `PYTHONPATH=src`。

## 注意
- 交易密码不需要(模拟盘不用解锁);不要把任何账号信息放进仓库。
- 如果 OpenAPI 模拟盘不支持某些期权合约,程序会报错停下;先用 `--mode dry` 跑通。
- 电脑休眠/断网会中断出场管理 —— 跑的时候保持电脑开着。
