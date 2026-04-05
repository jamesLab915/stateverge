# Signal Quality Tests & Regression Matrix

StateVerge Evolution Engine — repeatable verification without new product features.  
Scripts live under `scripts/`; helpers under `scripts/_regressionEnv.js`.

---

## 1. Syntax check

| | |
|---|---|
| **目的** | 保证核心 JS 可被 Node 解析，无语法错误。 |
| **前提** | 仓库完整。 |
| **执行** | `npm run test:signal-quality`（内含 `node --check`）或手动： `node --check scripts/updateScores.js` 等。 |
| **预期** | 无输出错误，退出码 0。 |
| **失败含义** | 语法损坏或 Node 版本不兼容；需先修复再跑其它测试。 |

覆盖文件：`updateScores.js`, `eventSignalQuality.js`, `consequenceTemplates.js`, `trendEngine.js`, `runDailyEvolution.js`。

---

## 2. 无待处理事件（No pending events）

| | |
|---|---|
| **目的** | `updateScores` 在无未应用事件时早退且不重复写分。 |
| **前提** | DB 中无 `applied_to_scores = false` 的事件（或已跑完一轮）。 |
| **执行** | `node scripts/updateScores.js` |
| **预期** | 标准输出含 `No pending events.` 与 `Done.`。 |
| **失败含义** | 仍有未应用事件或脚本异常；**已修复**：早退路径不得重复 `pool.end()`（见 `updateScores.js`）。 |

---

## 3. 合并路径（Merge path）

| | |
|---|---|
| **目的** | 同国、同 `event_type`、相邻间隔 ≤3 天的事件聚为一簇，合并阻尼生效。 |
| **前提** | 存在满足条件的未应用事件；或依赖纯函数测试（`testSignalQuality.js` 内 cluster 用例）。 |
| **执行** | 有数据时：`node scripts/updateScores.js`，日志出现 `merged N cluster(s)`。 |
| **预期** | 合并日志出现；聚合后影响非线性叠加（`clusterMergeFactor`）。 |
| **失败含义** | 聚类或阻尼逻辑被破坏。 |

---

## 4. 幂等（Idempotency）

| | |
|---|---|
| **目的** | 连续执行不产生重复应用；`runDailyEvolution --dry-run` 可重复。 |
| **前提** | DB 可连。 |
| **执行** | `npm run test:idempotency` |
| **预期** | 第二次 `updateScores` 仍报 `No pending events`；`dry-run` 两次无异常。 |
| **失败含义** | 事件被重复标记或子进程崩溃。 |

---

## 5. Snapshot 唯一性

| | |
|---|---|
| **目的** | 同一 `(country_code, 日历日)` 不应有多条 snapshot（规则层目标）。 |
| **前提** | 表 `country_score_snapshots` 存在。 |
| **执行** | `npm run test:snapshot-uniqueness` |
| **预期** | 无重复组时打印通过；若有历史重复则 **WARN 软通过**；`STRICT_SNAPSHOT_UNIQUE=1` 时失败。 |
| **失败含义** | 历史多次 `updateScores`/snapshot 写入造成重复；新逻辑以 `runDailyEvolution` 当日幂等为准。 |

---

## 6. 趋势稳定性（Trend stability）

| | |
|---|---|
| **目的** | `classifySeries` 对小噪声不过度标 `volatile`；单调序列稳定。 |
| **前提** | 无。 |
| **执行** | `npm run test:trend-stability` |
| **预期** | 平坦微波动非 volatile；平滑升降序列非 volatile。 |
| **失败含义** | `trendEngine` 阈值或实现回退。 |

---

## 7. 极值 / Noise control

| | |
|---|---|
| **目的** | 维度软顶、L1 压缩、`power_score` 通道封顶、情境缩放、模板中性阻尼。 |
| **前提** | 无。 |
| **执行** | `npm run test:signal-quality`（内含 `applyNoiseCaps*`, `applyContextScaleToDelta`, 模板对比）。 |
| **预期** | 断言通过。 |
| **失败含义** | `eventSignalQuality` 或 `consequenceTemplates` 行为改变。 |

---

## 8. Narrative / Cache 回归

| | |
|---|---|
| **目的** | 缓存键规范；表存在时可抽样检查。 |
| **前提** | 迁移后存在 `ai_insights_cache(cache_key, …)`。 |
| **执行** | `node scripts/testCacheSample.js`（含在 `test:regression`）。 |
| **预期** | 表缺失时软跳过；有表时抽样键无空格。 |
| **失败含义** | 表结构不符或键含非法字符。 |

页面行为（规则层覆盖 AI）以人工 / E2E 说明：删除缓存后由服务端规则重算；此处仅键名与表存在性。

---

## 9. Build 回归

| | |
|---|---|
| **目的** | Next 构建不被测试脚本破坏。 |
| **前提** | `npm install` 完成。 |
| **执行** | `npm run build` 或 `npm run test:regression`（末尾含 build）。 |
| **预期** | 构建成功。 |
| **失败含义** | 类型/导入错误。 |

---

## 10. 总入口

| 命令 | 说明 |
|------|------|
| `npm run test:signal-quality` | 语法 + 纯函数 + 无待处理事件 + 噪声/模板/趋势快检 |
| `npm run test:idempotency` | 双次 updateScores + dry-run daily |
| `npm run test:snapshot-uniqueness` | DB 重复组检测（软/严） |
| `npm run test:trend-stability` | trendEngine |
| `npm run test:regression` | 上述 + cache 抽样 + `npm run build` |

---

## 失败排查顺序

1. `node --check` / `test:signal-quality`  
2. `updateScores` 单跑与 DB 连接  
3. `STRICT_SNAPSHOT_UNIQUE` 与历史数据  
4. `npm run build`
