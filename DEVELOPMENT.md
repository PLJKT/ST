<!-- 公开镜像说明（由 tools/publish_public.py 生成，勿手改本文件） -->
> **本仓库只发布「代码 + 脱敏站点」。**
> 原始单据（65 份 PDF）、`st.db`、CSV 快照与完整性清单在**私有仓库 `ST-archive`**，需授权访问。
> 站点数字是真实仓位与损益，但已隐去姓名、银行账号、券商账号、IFUA/SID 与本机路径；
> 导出前由 `tools/redact_audit.py` 硬校验，命中任何一条即禁止发布。

# DEVELOPMENT.md — ST v5 开发说明

面向"下一次要改这个系统"的人（包括未来的我）。业务口径与页面说明看 [`README.md`](README.md)，数据文件清单看 [`data/README.md`](data/README.md)。

---

## 1. 系统是什么

一套**单一用户、本地优先**的个人投资台账 + 行情分析站，两个门面：

| 门面 | 内容 |
|---|---|
| 组合 | 三账户（富途 `futu_main` / 辉立新加坡 `poems_sg` / 辉立印尼 `poems_id`）、四市场（US / HK / ID / CN 参考）、权益与持仓、净值、对账、标的盈亏、历史台账 |
| 选股 | 代码/中文名搜索、单票基本面与 K 线、条件筛选、观察池、交易前模拟 |

两种运行形态：

1. **本地动态服务**（`http://127.0.0.1:8300`，FastAPI + SQLite，可写）——唯一真相源。
2. **静态只读快照**（`https://pljkt.github.io/ST/`）——`tools/export_static.py` 把 `/api/*` 的 GET 全量导出成 JSON，前端用 `static-shim.js` 拦截 `fetch` 改读快照；写操作在快照里被禁用。

技术栈刻意保持最小：Python 3 + FastAPI + SQLite（WAL）+ 原生 JS（无构建步骤）+ ECharts。不引入 ORM、不引入前端框架，改一个数字就能直接看到结果。

---

## 2. 目录结构

```
ST/
├─ server.py            uvicorn 启动入口（字符串 import，避免 reload 死循环）
├─ run.bat              Windows 双击启动（优先 uv，退化到 python）
├─ requirements.txt     fastapi / uvicorn / yfinance
├─ app/
│  ├─ config.py         市场表、账户定义、汇率兜底、交易所→市场映射、缓存 TTL
│  ├─ db.py             建表 + 连接 + 迁移（新增表/字段都加在这里，幂等）
│  ├─ seed.py           空库时把 analytics.json 拆成事实写入（账户/市场/骨架）
│  ├─ market.py         resolve / quote / history / search / fx_rates / fundamentals
│  ├─ portfolio.py      权益、持仓行、敞口权重、双口径合并
│  ├─ hygiene.py        数据体检三态（ok/flag/reject）
│  ├─ insights.py       时间线 / 偏好画像 / 风险清单 / 下一步动作
│  ├─ metrics.py        交易关键指标（总期 + 分账户 + 全历史分段对账）
│  ├─ futu_hist.py      富途 账户/账户（2022-01→2024-08）段盈亏：折算 + 口径说明
│  ├─ statement.py      富途资金明细 CSV 导入与现金对账（账户）
│  ├─ poems.py          POEMS 印尼单据台账
│  ├─ poems_sg.py        POEMS 新加坡月结单台账
│  ├─ edit.py           持仓增改删 + 现金写入
│  ├─ discipline.py     交易纪律规则（2% 法则 / 集中度 / 空头 / 杠杆 ETF…）
│  ├─ risk.py           分市场盘面 / 逐仓止盈止损与概率 / 对冲 / 回本
│  ├─ screener.py       股票池、基本面预热、筛选、观察池
│  ├─ aliases.py        中文名 → 代码
│  └─ main.py           全部 FastAPI 路由 + 静态站点挂载
├─ web/                 index.html / style.css / app.js / echarts.min.js
├─ tools/
│  ├─ export_static.py  生成 docs/ 静态快照（199 个接口，写盘统一走脱敏出口）
│  ├─ static_shim.js    静态站的 fetch 适配层（会被复制进 docs/）
│  ├─ import_futu_hist.py 富途 账户/账户 四份 CSV → data/processed 两份数据集（带自校验）
│  ├─ update_close.py   拉持仓标的日线收盘写 daily_closes（可挂计划任务）
│  ├─ export_data_pack.py  st.db 每张表 → data/snapshot/*.csv + MANIFEST.json
│  ├─ manifest_data.py  data/ 全量 sha256 清单 → data/manifest.json
│  ├─ verify_data.py    一条命令复核 data/ 是否与清单一致（PASS/FAIL）
│  ├─ redact.py         脱敏核心：键级遮蔽 + 文本/文件名正则 + 发布前硬校验
│  ├─ redact_secrets.py **真实姓名/银行/券商账号字面量，只在私有库，永不发布**
│  ├─ redact_audit.py   发布门禁：扫 docs/，命中身份信息即退出码 1
│  └─ publish_public.py 生成公开镜像（代码 + 脱敏站点）并推送
├─ data/                原始单据 + 台账 + st.db + 快照（**只在私有库 ST-archive**）
└─ docs/                脱敏后的静态快照（随公开库 ST 发布，GitHub Pages 源 = main:/docs）
```

**两个仓库的分工（2026-11 起）**

| 仓库 | 可见性 | 放什么 | 用途 |
|---|---|---|---|
| `PLJKT/ST-archive` | 私有 | 全部原始数据：65 份单据 PDF、转文本、`st.db`、`snapshot/*.csv`、`manifest.json`、`tools/redact_secrets.py` | 事实来源。改系统只依赖它，不必回券商门户/邮箱取证 |
| `PLJKT/ST` | 公开 | `app/` `web/` `tools/` `server.py` `docs/`（脱敏站点）、`data/universe.csv` | 代码展示 + 站点 `https://pljkt.github.io/ST/` |

公开库由 `tools/publish_public.py` 生成，**不是**手抄：它复制代码、清洗文档、剔除 `redact_secrets.py`、带上已脱敏的 `docs/`，最后对整份镜像再跑一次 `assert_clean`，命中即中止推送。

---

## 3. 数据模型（`app/db.py`）

SQLite，`data/st.db`，WAL 模式。**改表结构只改 `db.py` 里的 `SCHEMA`，全部 `CREATE ... IF NOT EXISTS`，重启即生效**。

| 表 | 作用 | 关键字段 / 口径 |
|---|---|---|
| `accounts` | 账户定义与状态 | `status`（active/closed）、`base_ccy`（记账币）、`settle_ccy`（结算币）、`margin_multiplier` |
| `markets` | 市场 | `code`(US/HK/ID/CN)、`tradable`（CN=0 仅参考） |
| `instruments` | 标的元数据 | `symbol`、`market_code`、`name`、`ccy` |
| `positions` | 当前持仓 | `qty`（空头为负）、`avg_cost`、`last_price`、`account_id`、`source`、`quality` |
| `closed_trades` | 平仓批次 | `qty`、`buy_price`、`sell_price`、`open_date`、`close_date`、`pnl_usd`、`realized_usd` |
| `trade_events` | 成交事件（无开平配对） | `side`、`price`、`qty`、`fee_usd`、`occurred_at` |
| `cash_balances` | 各账户现金 | `account_id`、`ccy`、`amount`、`as_of`、`source` |
| `cash_flows` | 券商逐笔资金流水 | `kind`（成交/股息/利息/出入金/迁移）、`amount`、`balance`、`occurred_at`、`ext_id` |
| `nav_points` | 净值曲线 | `as_of`、`nav_usd` |
| `recon_checks` | 对账条目 | `item`、`broker`、`ledger`、`diff`、`level` |
| `statement_files` | 已导入的单据文件 | `path`、`sha1`、`rows`、`imported_at` |
| `poems_trades` | POEMS 单据成交（归一化） | `account_id`、`trade_date`、`side`、`code`、`qty`、`price` |
| `poems_cashflows` | POEMS 现金流（分红/利息/税） | `kind`、`amount`、`ccy`、`occurred_at` |
| `poems_mf` | POEMS 货币基金期次 | `as_of`、`units`、`nav`、`value_idr` |
| `price_overrides` | 人工定价 | `symbol`、`market_code`、`price`、`reason`、`set_at` |
| `daily_closes` | 日收盘价 | `symbol`、`trade_date`、`close`、`source` |
| `quotes` / `fundamentals` / `fx_cache` | 行情缓存（可再生，不进 CSV 快照） | `fetched_at` 控制 TTL |
| `meta` | 派生摘要与迁移标记 | `poems_summary`、`poems_sg_summary`、`seeded_at`、数据版本 |

口径红线（改代码时不许破坏）：

1. `status='closed'` 的账户权益归零；`active` 一律"持仓市值 + 现金"。**不要把某个账户硬编码为 0**（曾经 POEMS SG 被这样写死过）。
2. 空头市值记负；权重分母是 `Σ|市值|`。
3. `quality='reject'` 的行不进任何合计；`flag` 保留并在界面标注。
4. 现金以券商流水/对账单期末为准，不用推算值覆盖。
5. 单据只有快照、没有流水时，**只记快照，不重建买入价与已实现盈亏**，并在界面上用 warn chip 说明。

---

## 4. 数据流水线

三条来源，三种处理方式，互不合并账户。

### 4.1 辉立印尼（62 份 PDF）

```
data/source/poems_id/*.pdf
  └ pypdf → data/raw/poems_txt/*.txt
      └ 解析（印尼 1.570.092,94 与英文 12,345.67 双数制；成交确认的价格粘在公司名末尾，从尾部正则抓；
         同一 REF# 多行按 (ref, side, code) 聚合，手续费按成交额分摊）
          └ FIFO 平仓 + 现金 + 基金期次 → data/processed/poems.json
              └ POST /api/poems/import → poems_trades / poems_cashflows / poems_mf / positions / closed_trades
```

### 4.2 辉立新加坡（2 期月结单 + 1 份股息税单）

```
data/source/poems_sg/*.pdf → data/raw/poems_sg_txt/*.txt
  └ 人工核对六列数字 → data/processed/poems_sg.json（快照 / 股息 / 退税 / 6 条 checks）
      └ POST /api/poems/sg/import → positions(psg_SQQQ, psg_MMF) + cash_balances(USD) +
        price_overrides(PHILLIPMMF.SG) + accounts.status='active'
```

SG 只有 2 期快照、中间成交流水缺失，所以**不生成 `closed_trades`**，`realized` 继续用 2020–2022 台账的 6,662.84，并在 `metrics.py` 里带 `return_basis` 标签说明口径。

### 4.3 富途（美股+港股）：一个号，两段历史

```
data/source/futu/
  ├─ 历史成交-美股融资融券账户(账户)-20200101-20241231.csv   318,239 B / 4,200 行 / sha1 e8885ca549f12f21dc5c3b11175a64ed1f46911e
  ├─ 历史订单费用-美股融资融券账户(账户)-20200101-20241231.csv 665,989 B / 3,550 行 / sha1 725410b1984fe54c340d1fd849cf7cb347aaade7
  ├─ 历史成交-港股融资融券账户(账户)-20200101-20241231.csv    32,471 B /   362 行 / sha1 9d45df08561dbe560bfa9beeb08ce0fcc72cc54f
  ├─ 历史订单费用-港股融资融券账户(账户)-20200101-20241231.csv 124,040 B /   616 行 / sha1 d7d1154e2a6ca93442b9db54c8d2405b02c64d7b
  └─ 资金明细-保证金综合账户(账户)-20261108-073230.csv        108,375 B / 1,115 行
       │
       ├ tools/import_futu_hist.py → data/processed/futu_hist_trades.csv（4,562 笔逐笔成交，审计用）
       │                          → data/processed/futu_hist_2022_2024.json（段汇总 + 校验 + 证据）
       ├ statement.import_file()  → cash_flows（账户，幂等 id = sha1 前 16 位）+ 用末笔余额改写现金
       └ metrics.key_metrics()    → 分段对账拆成「账户/账户 段」+「账户 段」两行
```

文件名写 `20200101-20241231`，**实际数据从 2022 年开始**：美股首笔 2022-04-12 13:02:41，港股首笔 2022-01-13 11:04:52，末端分别到 2024-08-02 12:38:33 与 2024-08-01 11:08:28。2020-01 → 2022-01 仍只属于 POEMS SG 旧户。

（上面 4 个 sha1 按 CRLF→LF 归一化后取，与 `data/manifest.json` 同口径——`core.autocrlf=true` 会在另一台机器 checkout 时改写换行，按原始字节哈希会对不上。）

成交量：美股 4,200 行（2022:1,654 / 2023:1,847 / 2024:699），港股 362 行（2022:56 / 2023:214 / 2024:92）。

| 段 | 成交笔数 | 成交额 | 净现金流 | 交易费用 |
|---|---|---|---|---|
| 美股 账户 | 4,200 | USD 35,695,338.86 | USD −29,501.88 | USD 7,203.25（佣金 2,932.22 / 平台 2,975.33 / 交收 1,120.55 / 证监会规费 145.07 / 交易活动费 30.08） |
| 港股 账户 | 362 | HKD 19,572,765.00 | HKD −113,731.00 | HKD 40,347.15（佣金 5,899.51 / 平台 4,230.00 / 印花税 23,768.00 / 借券印花税 3,966.00 / 交易费 1,065.43 / 交收 832.48 / 证监会征费 528.50 / 财汇局 29.23 / 系统费 28.00） |

订单费用表：美股 3,550 条（全部成交 1,861 / 已撤单 1,646 / 部成已撤 28 / 下单失败 15），港股 616 条（全部成交 279 / 已撤单 331 / 部成已撤 3 / 下单失败 3）。撤单与失败不收费用，所以费用合计只来自已成交订单。

**自校验**：`成交金额 = 成交数量 × 成交价格`，4,562 行全部成立（0 行不符），`import_futu_hist.py` 发现不符会 `exit 1`。方向只有 `买入 / 卖出 / 卖空`（美股）与 `买入 / 卖出`（港股）——**美股的「买入」同时充当建多与平空**，所以净持仓不可信，只有现金流向可信。

**归因更正（旧版写错，本轮已改）**：账户 在 2024-08-03 收到的「资产迁移」不是从 POEMS 转入，而是同一富途号内部的子账户升级。

- 账户 末笔成交 2024-08-02 12:38、账户 末笔 2024-08-01 11:08，账户 恰好接在 08-03 收到 USD +27,902.71 / HKD −42,208.44；
- 迁移过来的持仓在 账户 侧对得上：01519 的 8,000 股于 08-05 13:20 买入 −11,960 HKD、13:40 卖出 +59,100 HKD（当日清仓）；AVGO 的 −10 股空头于 08-05 12:55 被 10 股买入 −1,300.30 平掉。

`app/config.py::ACCOUNTS["futu_main"]` 与 `accounts` 表的 note 已同步改为「2022-01-13 起融资融券子账户 账户/账户，2024-08-03 内部升级为保证金综合账户 账户」，`opened_at` 由 `2024-08-03` 改为 `2022-01-13`。

**2022-01→2024-08 段盈亏怎么算（口径 A，warn）**：

```
净现金流（买入为负 / 卖出·卖空为正）
  − 交易费用（完整费用表，含已撤单订单的 0 费用行）
  + 迁移日结转出去的持仓按次日成交价计值（01519 +59,120 HKD、AVGO −1,300.30 USD，折 USD 6,235.9）
= 美股 −29,501.88 − 7,203.25 = −36,705.13
  港股 (−113,731.00 − 40,347.15) × 0.12751 = −19,651.4
  结转持仓 +6,235.9
  → 段合计 −3,266.5 USD
```

**AMAT 394 股：已转出本账本（不计损益）**

- 92 条成交全部集中在 2022-04-12 → 2023-03-03，之后一笔都没有；费用表里 2023-09-14 还有一张未成交订单，再往后彻底安静；
- 2024-08-03 的「资产迁移」在 账户 只有 **4 笔现金**（USD +18,865.01 / +25,696.42、HKD −17,141.59 / −25,080.84），**没有任何证券转入行**；账户 全库 26 个代码里没有 AMAT；
- 证券转仓不产生现金流水，所以两份导出和 账户 流水都看不到它——与「转出、不可能遗失」一致。

因此按 **原值移出** 处理：成本 46,843.49 既不算亏损、也不算资产，本段 −3,266.5 即最终数（不再保留「若灭失则 −50,110」的备选口径）。若日后在别的账户找到这 394 股，应作为独立持仓补进那个账户，不回填这里。

因为该导出**没有出入金流水**（只有成交 + 费用），净现金流不等于盈亏，所以这一段长期是 `warn` 级，不是 `ok`。要升到 `ok`，需要补 账户/账户 的「资金明细 / 出入金」导出。

账户 这一段的口径与修正：

| 口径 | 含义 | 富途 账户 段 |
|---|---|---|
| 券商总盈亏（修正后，界面采用） | 期末现金 28,808.21 + 持仓 8,994.85 −（净外部投入 49,997.12 + 随迁持仓 6,235.9） | **−18,432.7** |
| 券商总盈亏（旧算法，未计随迁持仓） | 同上但不加 6,235.9 | −12,194.2 |
| 券商现金口径 | 期末现金 − 净投入 | −21,189.2 |
| 台账 FIFO 已实现 | 232 条平仓事件 | −15,917.8 |

**为什么要加那 6,235.9**：迁移只把现金记成入账（USD +27,902.71、HKD −42,208.44 折 −5,382，净 +22,521），但 01519 8,000 股与 AVGO −10 股是资产、不是入金。不把它们的价值计入投入，账户 段盈亏就会高估同样一笔钱。修正后 账户 段 −18,432.7，加上 2022-2024 段 −3,266.5，富途全周期 −21,699.2，与「原始现金流 + 期末权益」的合并算法（−21,7xx，差 < 30 美元汇率尾差）自洽。

四个数不可互换；界面逐条标"凭证完整 / 口径注意 / 对不上"。

### 4.4 全历史分段对账（2020-02 → 2026-11）

前置口径：**三个账户各自独立结算，账户之间从未互转资金或持仓**，所有入金都来自个人储蓄。富途 2024-08-03 的「资产迁移」是同一富途号内部的子账户升级（见 4.3），既不是 POEMS 转入，也不是外部入金。

| 期间 | 账户 | USD | 级别 | 锚点 |
|---|---|---|---|---|
| 2020-02 → 2022-01 | POEMS SG 旧户 已实现 | +6,662.84 | ok | 初始入金 23,110 → 平仓后权益 29,772.84（158 笔平仓） |
| 2022-01 | POEMS SG 旧户 浮亏 | −4,767.29 | ok | 期末 6 只遗留仓按 2022-01-27 价计的账面浮亏 |
| 2022-01 → 2024-08 | **富途 账户/账户** | **−3,266.5** | warn | 4,562 笔成交净现金流 − 费用 + 结转持仓计值；AMAT 394 股按「已转出」原值移出，不计损益 |
| 2024-08 → 2026-11 | 富途 账户 | −18,432.7 | ok | 期末现金 28,808.21 + 持仓 8,994.85 −（净投入 49,997.12 + 随迁持仓 6,235.9） |
| 2025-11 → 2026-11 | POEMS SG 新户 | −4,839.3 | warn | 首见余额 895.69 + 买入成本 − 现值（无月结单，重估随行情浮动） |
| 2026-03 → 2026-11 | POEMS ID | +177.5 | ok | 现值 + PGAS 回款 − 净投入 |
| **合计** | | **−24,465.9** | | |

（表中数字取 2026-11-10 快照；现值部分随行情与汇率浮动，每次重算会小幅变化。）

交叉校验：累计投入 98,311.9（净外部投入 49,999.7 + 随迁持仓 6,235.9 + SG 旧户 23,110 + SG 新户 12,408.7 + ID 6,523.1）vs 已退出 25,005.55 + 现值 52,107.4 = −21,199.0，与「账户 起」小计 −21,199.0 **差 0.00**。2022-01→2024-08 段不在这个校验里——它没有出入金流水，只能由数据集自身推导（段初为零、段末 = 结转持仓）。

**"亏了 5 万"的来源已定位**：旧仪表盘 2026-09-24 的「回本缺口 49,293.65」= 本金 66,062.97 − 权益 16,769.32。拆开看：

- 富途那 42,952.97 **正好等于 9-24 时点的净投入**（存入 34,676.90 + 开户入账 22,468.37 − 取出 7,200 − 10-06 才到的 6,992.30），不是重复计价；
- 本金里还加了 SG 旧户 23,110，但减去的 16,769.32 只是富途一户的权益；
- SG 新户（投入 1.24 万）与 ID（0.65 万）的投入和权益都没进这条式子。

所以那 49,293.65 是「多账户本金对单账户权益」的混合口径。补齐 2022-2024 富途段之后，全历史凭证口径是 **−24,465.9**，与「约 −5 万」的差额从 −35,632 收窄到 **−25,534**。AMAT 394 股已按「转出」原值移出，不再进入任何备选口径。**结论：方向一致（确实在亏），量级对不上——记忆是方向，不是账。**

覆盖边界（界面 `coverage` 同步展示）：已覆盖富途 账户/账户 的 4,562 笔成交 + 4,166 条订单费用、账户 的 555 笔成交与全部流水、POEMS ID 62 份单据、SG 两期月结单 + 旧户台账；未覆盖 2019 及以前；账户/账户 缺出入金流水（段盈亏为推导值，warn）；AMAT 394 股已按「转出」处理；待补 PGAS 卖出确认。




---

## 5. 模块职责速查

| 模块 | 入口函数 | 输出到界面哪里 |
|---|---|---|
| `portfolio.py` | `overview()` / `positions()` | 组合页 KPI、持仓表、市场分布 |
| `metrics.py` | `metrics()` | 分析页「交易关键指标」+ 三张账户卡 |
| `insights.py` | `timeline()` / `profile()` / `risks()` / `position_advice()` | 分析页时间线、画像、风险、下一步动作 |
| `hygiene.py` | `summary()` / `apply()` | 分析页「数据体检」 |
| `statement.py` | `summary()` / `rows()` / `import_file()` | 富途资金去向、流水明细 |
| `poems.py` | `payload()` / `import_all()` | 分析页「POEMS 印尼账户台账」 |
| `poems_sg.py` | `payload()` / `import_all()` | 分析页「POEMS 新加坡账户台账」 |
| `discipline.py` | `evaluate()` | 风控页纪律条 |
| `risk.py` | `payload()` | 风控页盘面 / 逐仓止盈止损 / 对冲 / 回本 |
| `screener.py` | `screen()` / `warm()` | 选股页 |

---

## 6. API 契约

全部 GET 都是**幂等只读**，可以任意被静态导出；写操作只有 POST/PUT/DELETE，见 `app/main.py`。

| 分组 | 端点 |
|---|---|
| 基础 | `/api/health`、`/api/health/verify`、`/api/markets`、`/api/accounts`、`/api/meta` |
| 组合 | `/api/overview?account=&base=`、`/api/positions`、`/api/positions/legacy`、`/api/nav`、`/api/recon`、`/api/symbols`、`/api/trades` |
| 行情 | `/api/quote`、`/api/history`、`/api/info`、`/api/search`、`/api/universe` |
| 选股 | `/api/screen`、`/api/screen/warm`（POST）、`/api/screen/warm/status`、`/api/screen/export.csv`、`/api/watchlist*`、`/api/simulate` |
| 治理 | `/api/hygiene`、`/api/hygiene/apply`（POST）、`/api/insights`、`/api/discipline`、`/api/risk` |
| 单据 | `/api/statement`、`/api/statement/rows`、`/api/statement/import`（POST）、`/api/poems`、`/api/poems/import`（POST）、`/api/poems/sg`、`/api/poems/sg/import`（POST） |
| 指标 | `/api/metrics` |
| 定价 | `/api/prices/manual`（GET/POST/DELETE）、`/api/prices/refresh`（POST）、`/api/prices/closes` |
| 编辑 | `/api/positions`（POST/PUT/DELETE）、`/api/cash`（POST）、`/api/positions/check`（POST） |

契约约定：

- 所有金额字段带 `_usd` 后缀表示已折算到 USD；原始币种金额保留原字段。
- 返回里的 `tags` 是界面 chip 的直接来源：`{k, v}`，`k` 含「缺/断档/差异」时前端自动渲染成 warn 色。
- `return_basis` 必须写明回报率的资金口径（如 `2020–2022 台账口径（2026 期无成交单据）`），前端只做展示，不猜口径。
- `checks` 是对账结论：`{ok: bool, text}`，warn 项也要输出，不允许静默吞掉差异。

---

## 7. 行情与定价（四层）

优先级：**人工定价 `price_overrides` > 实时报价（5 分钟缓存）> 最近收盘 `daily_closes` > 台账价**。每行都带来源标签（`手动价` / `◐ 5m` / `收盘价` / `台账价`）。

- Yahoo 没有的代码：`PHILLIPMMF.SG`、`PHILLIPMMF.JK`（404 Quote not found），必须走人工价，别指望实时价。
- 收盘价落库：`tools/update_close.py`，挂计划任务即可（`schtasks /create ... /sc daily /st 06:30`）。
- 汇率来自 Yahoo（`fx_cache` 兜底），账户 `base_ccy` 与 `settle_ccy` 不同时先按结算币记账再折算。

---

## 8. 静态快照与发布

```powershell
# 1) 服务必须在听（8300）
uv run --with fastapi --with uvicorn --with yfinance python tools\export_static.py --universe-limit 65
#    → docs/（199 个接口 / 约 3–4 分钟）；失败清单会在末尾打印，正常应为「失败 0 个」
```

快照机制：`docs/data/index.json` 把「规范化后的接口 key」映射到内容哈希文件 `data/<sha1>.json`；`static-shim.js` 拦截 `fetch('/api/...')` 查表并带上 `?v=<时间戳>` 取回，从而绕过 CDN 缓存。`export_static.py` 会同步清掉不再被映射的旧哈希文件（`docs/data` 文件数应等于接口数 + 1）。

发布到 GitHub Pages（公开库 `PLJKT/ST`，Pages 源 = `main` `/docs`）：

```powershell
# 0) 服务在听（8300），先把 docs/ 重导一次
uv run --with fastapi --with uvicorn --with yfinance python tools\export_static.py --universe-limit 65

# 1) 门禁：必须 PASS 才能往下走（命中姓名/银行/券商账号/本地路径即退出码 1）
python tools\redact_audit.py

# 2) 生成公开镜像 + 推送（内部已再做一次整镜像校验）
python tools\publish_public.py --push
```

`publish_public.py` 会：复制 `app/ web/ tools/ docs/` 与根文件 → 清洗 README/DEVELOPMENT（账号写法转「SG 账户 / ID 账户 / 富途账户」、Windows 路径转 `<local>`）→ 剔除 `tools/redact_secrets.py` → `data/` 只留 `universe.csv` + 说明 → 写公开版 `.gitignore` → 整份镜像 `assert_clean` → `git init/add/commit/push` 到 `PLJKT/ST`。

凭据走 `GIT_ASKPASS`（`@echo off` + `gh auth token`），并加 `-c credential.helper= -c credential.username=x-access-token`，否则 HTTPS 推送会静默挂起。

线上验收套路：打开 `https://pljkt.github.io/ST/?v=<新值>#analysis`（hash 只切 tab，不刷新数据；用 `?v=` 强制换 URL 才能拿到新快照），点 tab 后等 3–9 秒再读 DOM。

### 8.1 脱敏规则与发布门禁

| 层 | 处理 |
|---|---|
| 键级（`KEY_BLOCK`） | `holder` `held_by` `acct_no` `account_no` `poems_id` `sid` `rdn` `ifua` `tax_id` `npwp` `email` `phone` `address`… → 一律 `***` |
| 键级（`KEY_PATH`） | `db` 固定写 `data/st.db`；`path/file/filename/files/source_file/source_files/dir` → 只留 basename 再 scrub |
| 文本级 | 姓名/银行/券商账号/IFUA/SID/用户名 → 按字面量替换；`<local> → `<local>`；`BCA 数字` → `BCA ***`；邮箱 → `***@***`；印尼手机号 → `***` |
| 文件名级 | 成交确认文件名里的账号段 → `***`（账户尾号只按独立数字替换，避免误伤金额） |
| 不脱敏 | **金额、股数、收益率、持仓权重**（用户明确选择公开真实仓位） |

- 真实字面量只写 `tools/redact_secrets.py`（私有库），`redact.py` 用 try-import：公开镜像里没有该文件，脱敏器仍可运行但列表为空 —— 这是刻意的，脱敏只在私有库执行。
- `redact_audit.py` 是硬门禁：`docs/` 里出现姓名/银行账号/券商账号/用户名/`[A-Za-z]:\Users`/邮箱即 FAIL；GitHub 自己的 `@users.noreply.github.com` 白名单放行。
- 改完脱敏规则要**重导 + 重审**，不要只改代码不重导：`export_static.py` 是所有 JSON 的唯一出口。
- 别在 `web/index.html`、`app/*.py` 注释里写真实账号（曾经写过，被门禁抓了两次）；界面标签用「POEMS SG · USD 结算」这类描述。

---

## 9. 本地开发流程

```powershell
# 起服务（工作目录必须是仓库根）
uv run --with fastapi --with uvicorn --with yfinance python server.py
# 端口默认 8300；日志重定向到 server.out.log / server.err.log
```

- **改代码必须先杀掉 8300 的监听进程再启动**，否则跑的还是旧代码：
  `Get-NetTCPConnection -LocalPort 8300 -State Listen | Select -Expand OwningProcess -Unique | Stop-Process -Force`
- 前端改完要 bump 版本号（`web/index.html` 里 `style.css?v=` / `app.js?v=`），否则浏览器用旧缓存。
- 12–16 秒后接口才可用（首次要拉汇率/行情）。

**跑任何导入 `app.*` 的 Python 脚本，必须带 `--with yfinance --with fastapi`**，否则 `app.market` 导入时 `ModuleNotFoundError: No module named 'yfinance'`。

---

## 10. 改完必须走的验收清单

1. `GET /api/health` 200；`GET /api/health/verify` 差异项=0 或已解释。
2. 改动涉及的接口用 `Invoke-RestMethod` 直连 8300 看真实 JSON（`fastapi.testclient` 因缺 httpx 不能用）。
3. 界面（本地 `?v=<新值>`）逐块对照数字；有 warn 的地方要能说清是什么差异。
4. 重启 → `uv run ... tools\export_static.py` → **失败数必须为 0**。
5. `git push main` → gh-pages 推送 → 线上 `?v=` 打开验收。
6. 数据有变化时收尾四件套：

```powershell
python -c "import sqlite3;c=sqlite3.connect(r'data\st.db');print(c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone());c.close()"
python tools\export_data_pack.py   # 表快照 CSV
python tools\manifest_data.py      # data/ 全量 sha256 清单
python tools\verify_data.py        # 必须 PASS，否则仓库里的数据与清单不符
```

`st.db` 是 WAL 模式：**不 checkpoint 就提交，最新写入还在 `-wal` 文件里，仓库里的库是旧的**。

---

## 11. 已知坑（踩过，别再踩）

| 现象 | 原因 / 处理 |
|---|---|
| 静态站数据看起来是旧的 | 浏览器缓存了上一次部署的 `data/index.json`（shim 版本号没变时）；用 `?v=` 换 URL，或确认 `static-shim.js?v=` 已随新部署变化 |
| 线上页面切 tab 数据不刷新 | hash 导航不触发重新加载；用 `bu.js` 点击 `.tabs button` 或加 `?v=` |
| `usd(...)` 在 `web/app.js` 里报未定义 | 没有 `usd()` 这个函数，用 `money()` / `money2()` / `signed()` / `pct()` |
| `PHILLIPMMF.SG` 报价 404 | Yahoo 无此代码，走 `price_overrides` 人工价 |
| `/api/hygiene` 没有 `rows` | 它返回 `summary` + `flagged`，明细在 `flagged` 里 |
| `fastapi.testclient` 报 `ModuleNotFoundError: httpx` | 本机没装 httpx2；用 `Invoke-RestMethod` 直连服务 |
| 前台 PowerShell 15 秒被转后台 | 长命令（导出、浏览器验收）用 `run_in_background` 或 `TaskOutput(block=true)` |
| `docs/data` 里出现两份同名接口快照 | 旧哈希没被清理；`export_static.py` 会清，若仍出现说明导出中断，重跑一次 |
| POEMS SG 的 realized 被当成 2026 年业绩 | 那是 2020–2022 台账口径；指标卡 `return_basis` 已写明，别再合并两段 |

---

## 12. 从零 clone 起跑

```powershell
# 要跑系统（含真实数据）：clone 私有库
git clone https://github.com/PLJKT/ST-archive.git ST
cd ST
uv run --with fastapi --with uvicorn --with yfinance python server.py

# 只读代码 / 看站点：clone 公开库（无 data/，起服务会空库）
git clone https://github.com/PLJKT/ST.git
```

私有库里自带 `data/st.db` 与全部原始单据，**clone 完就能直接跑**，不需要回券商门户或邮箱取证；跑前用 `python tools\verify_data.py` 确认 `data/` 与 `manifest.json` 一致（应为 `PASS`）。要重建库见 `data/README.md`「重建」一节。

公开库只有代码和 `docs/`，clone 后 `data/` 里只有 `universe.csv`，直接起服务会得到空库 —— 这是设计，不是 bug。

---

## 13. 待办（诚实清单）

- **POEMS SG 中间流水**：只覆盖 2025-11 与 2026-10，2025-12 → 2026-09 缺 10 个月；补齐后才能重建 SG 2026 年成交与替代 realized 口径。
- **富途持仓已按 11-09 快照校正，但缺中间流水**：只有券商最终数量与成本，没有 2026-09-24 → 11-09 的逐笔成交明细，重建分账户 FIFO 仍需成交/订单 CSV 的新导出。FIFO −15,917.8 与券商现金口径 −21,188.6 的 5,270.8 差额仍未拍板。
- **POEMS ID 现金缺 PGAS 回款**：10,000 股 PGAS 已清仓（11-09 快照为 0），但成交发生在 11-06 之后、62 份单据里没有这笔，现金 35,342,054.57 IDR 未含卖出回款（约 14.9M IDR），ID 权益暂被低估约 820 美元。
- **"累计亏 5 万"已口径澄清**：分账户合计 −24,465.9（2020-02→2026-11-10），与"净投入 vs 已退出+现值（账户 起）"完全闭合（差 0.00）。旧仪表的 49,293.65 是「富途 9-24 净投入 42,952.97 + SG 旧户本金 23,110」对「富途单户权益 16,769.32」的混合口径，既漏了 SG 新户与 ID 的本金和权益，也不是终身数字。旧结论"迁移被重复计成本金"已撤销——账户之间没有互转；富途 08-03 那次迁移是同号内部子账户升级。这段差额若要再缩，只剩 2019 及以前是否还有别的券商账户。
- **富途 账户/账户 缺出入金流水**：现有导出只有成交 + 费用，段盈亏是「净现金流 − 费用 + 结转持仓计值」推导值（warn）。要升到 ok，需补这两个子账户的「资金明细 / 出入金」导出。
- **AMAT 394 股已定案为「转出」**：2023-03-03 之后无成交、迁移只有现金没有证券转入、账户 侧 0 次出现 → 证券转仓本来就不写现金流水。按原值 46,843.49 移出本账本，不计亏损也不计资产；2022-2024 段维持 −3,266.5，全历史维持 −24,465.9。若日后在别处找到，另立持仓，不回填。
- **收盘价定时任务未创建**：`tools/update_close.py` 就绪，`schtasks` 还没注册；人工价只落本地库，不进静态快照。
- **静态快照不含 `/api/prices/*`**：线上看到的价源标签是最新一次导出时的状态。
- **隐私已分层，但历史不可回收**：旧公开周期（2026-11-09 之前）的仓库已被 fork/缓存可能留存，git 历史无法改写；现在公开面（`PLJKT/ST` 代码 + 站点）已 0 命中身份信息，见 §8.1。
