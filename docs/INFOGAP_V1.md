# Stateverge 信息差发现器 v1(「Stateverge · 24H 信息差」)

> 把英文互联网里真正有价值的信息,提前翻译成中文世界能理解的机会、风险和趋势。

代码:`src/stateverge/cn_news/infogap/`(Python 标准库,无额外依赖)

## 流程

```
英文互联网过去 24 小时
  Hacker News(热门)· GitHub(近 7 天新项目按星数)· Reddit(按主线选的版块,当天热门)
→ 按链接去重
→ 按海外热度取前 20 条(只对这 20 条做付费检测,控制成本)
→ 中文覆盖度检测:Tavily 只搜 36 个中文主流科技/商业/社区网站(36氪、虎嗅、IT之家、知乎、掘金、微博、B站等)近一周,数相关结果条数;或用 Brave 搜中文网页
→ AI 评估 + 中文草稿(OpenAI,只能用原始材料,不许编造)
→ Information Gap Score ≥ 70 分进入候选(5–10 条)
→ 最多选 3 条,尽量覆盖不同主线
→ 生成审核稿 + X 预览 → 人工审核 → X 首发
```

## Information Gap Score

| 项目 | 权重 | 怎么算 |
|---|---|---|
| 海外热度 | 30% | HN 分数 / GitHub 星数 / Reddit 赞数,取对数归一到 0–100 |
| 中文稀缺度 | 30% | 中文近一周结果 0 条=100,1–2=80,3–5=60,6–10=35,>10=10;未检测=50 并在审核稿标注 |
| 实用价值 | 20% | AI 评分(无 AI 时为关键词估算并标注) |
| 讨论潜力 | 10% | 同上 |
| 可视化素材 | 10% | 同上 |

## 四条主线

`AI_TECH` AI/科技 · `MONEY` 美国赚钱/小生意 · `US_LIFE` 美国普通人生活 · `INDUSTRY` 产业/商业趋势

Reddit 默认版块:artificial, LocalLLaMA, SaaS, SideProject, Entrepreneur, smallbusiness, personalfinance, Frugal, povertyfinance, doordash_drivers, Costco, technology, Futurology(`collectors.DEFAULT_SUBREDDITS`)。

## 草稿结构

发生了什么 → 为什么现在 → 美国人在怎么用 → 中国用户为什么应该注意 → 有没有机会 → 限制/风险

## 发布前检查(任一失败即拦截)

- 审核人署名;已核对原文事实;已写清中国用户可用性/地区限制
- 每条都有原文链接、都有「限制/风险」、都 ≥ 70 分
- 不能有【待编辑】占位(没有 AI 时草稿必须人工写)
- 不能出现「稳赚、躺赚、暴富、保证收益、无风险、必赚、零风险、月入百万」等夸大承诺
- 同一天的日报只发一次(`x_ledger.json`)

## 手机上使用

1. 编辑 `data/infogap/digest_requests.json`,写入 `[{"status": "pending"}]` 并提交 → 云端扫描。
2. 运行后条目变成 `drafted`,打开 `data/infogap/digests/日期.md` 看审核稿和 X 预览。
3. 确认后在同一条目里加:
   ```json
   "post": true, "reviewer": "你的名字", "facts_checked": true, "access_checked": true, "status": "pending"
   ```
   可选 `"picks": [1, 3, 4]` 按审核稿编号换选题;可选 `"text": "..."` 用你改过的文案(仍会检查)。
4. 提交后云端发到 X,条目变成 `posted` 并附链接;被拦截时为 `blocked` 并写明原因。

中文覆盖度检测目前用 **OpenAI 联网搜索**(工作流里 `INFOGAP_COVERAGE: openai`):只需要 `OPENAI_API_KEY`,每次搜索由 OpenAI 另外计费;程序只数 AI 实际引用的中文来源链接,不采信 AI 的主观判断。也可改成 `tavily`(免费,需 `TAVILY_API_KEY`)或 `brave`。

需要的 GitHub Secrets:`OPENAI_API_KEY`(写草稿)、`TAVILY_API_KEY`(中文覆盖度,免费 1,000 次/月,每天扫描约用 20 次)、四个 `X_*`(发帖)。缺 OpenAI 时只出评分和空白草稿;缺 Brave 时稀缺度按 50 计。

## 电脑上

```bash
cp .env.example .env.local   # 填入密钥
PYTHONPATH=src python3 -m stateverge.cn_news.infogap scan
```

## 已知限制

- 三个来源和 Brave/OpenAI 接口在开发环境无法联网,只用模拟数据测试过;第一次云端运行时请看审核稿是否正常。
- Reddit 经常拒绝云服务器的匿名访问,失败会自动跳过。
- 中文覆盖度只代表 Brave 能搜到的中文网页,不包括微信公众号、小红书、抖音等站内内容,稀缺度可能偏高。
- Product Hunt、公司博客、SEC、行业媒体等来源还没接入。
