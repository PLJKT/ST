# ST — 投资交易记录与分析仪表盘

个人投资账户（**POEMS** 新加坡保证金 + **FUTU** 港股美股保证金）全量交易记录的私有归档与专业分析仪表盘。

## ⚠️ 隐私与安全声明

本仓库为 **GitHub 私有仓库**，包含真实交易数据。已采取以下保护：

1. **数据加密**：仪表盘的全部分析数据以 **AES-256-GCM** 加密存放于 `site/vault.json`，密钥由登录密码经 **PBKDF2-SHA256（210,000 次迭代）** 派生。未登录状态下，仓库中不存在任何可读的明文交易数据（`data/raw/` 仅对仓库协作者可见——协作者即拥有 GitHub 账号者）。
2. **登录保护**：`site/login.html` 要求用户名 `admin` + 密码；密码校验与解密**完全在本机浏览器内完成**，不发送凭据到任何服务器，无第三方脚本、无遥测（图表库 ECharts 已 vendor 到本地）。
3. **会话隔离**：解密后的明文仅存在于当前标签页 `sessionStorage`，关闭标签页或点击「登出」即销毁。
4. **无后门**：系统不存储任何凭据，密码即密钥——忘记密码无法找回，需重新运行 `scripts/build_vault.py` 重置。

> 登录口令：`admin / e%mX#fMh0CDGzxpKoOlP`（已于 2026-09-23 从默认口令更换。此文件位于私有仓库；公开仓库不含密码。改密命令：`python scripts/build_vault.py --password '<新密码>' --username admin`，并同步推送私有仓与公开仓的 vault。）

## 访问方式

- **在线（GitHub Pages，公开站）**：https://pljkt.github.io/ST-Pages/ —— 页面与数据均为密文，登录时才在本机解密。
- **本地**：双击 `site/login.html`（file:// 即可，vault.js 为离线加载回退）。

## 目录结构

```
ST/
├── data/
│   ├── raw/          # 券商原始导出（POEMS xlsx + FUTU csv ×2）
│   └── processed/    # 标准化分析数据集 analytics.json + tidy CSV（明文，仅本机）
├── site/             # 加密仪表盘（vault.json 为密文，可安全提交）
│   ├── login.html  dashboard.html  index.html
│   ├── app.js  crypto.js  sha256.js  vault.js(=vault.json 的 file:// 回退)
│   └── echarts.min.js  # Apache ECharts 5.5.1 (vendor, Apache-2.0)
└── scripts/
    ├── normalize.py    # 原始数据 → analytics.json（可重跑，增量更新）
    └── build_vault.py  # analytics.json → 加密 vault（--password 重设口令）
```

## 数据更新流程

1. 从券商重新导出 CSV / 更新 Excel，覆盖 `data/raw/`；
2. `python scripts/normalize.py`；
3. `python scripts/build_vault.py --password '<密码>' --username admin`；
4. 提交推送。

## 数据口径（重要）

- **v2（2026-09-23 修订）**：修复 v1 的 FUTU 三处口径错误——①"卖空"方向被误当"买入"（SBUX 实际亏 $9,486 被记 -$705、01519 实际盈 HK$4,500 被记 -$4,540）；②USD/HKD 金额混加虚增约 $2,846；③KPI 误用 2021-22 旧台账（本金 9500/亏 4260）。现全部按定稿的多空双批次模型 + 分币种核算。
- **资金明细并入**：`data/raw/资金明细-*.csv`（1082 条，2024-08→2026-09）提供账户级真相：FUTU 1599 账户由 2024-08-03 资产迁移设立（迁入净 +$22,468 等值），此后银行净入金 $20,485；期末权益（USD 折算）≈$16,769，**账户净亏 ≈ -$26,184**。
- **三重勾稽全部闭合**：成交 CSV ↔ 资金流水（差 ≤$0.01）；逐条余额链（1080 条，多腿记录除外全部闭合）；净值双口径互证（资金流法 vs 交易层法差 $0.21）。
- **隐性成本**：融券费 $1,091、融资利息 $298、卖空股息 $955——此前订单表口径完全漏掉，实际交易成本比"佣金 $1,082"高出一倍。
- FUTU 2022-03→2024-08 成交仍缺失（无导出），POEMS 台账止于 2022-01（矩阵与明细差额已标注于 poems_recon）。
- POEMS 股票名称源文件公式损坏无法恢复；Day Trade 明细区（109 笔）与汇总区（73 笔）不一致，明细为准。
- 详细限制见仪表盘底部「数据口径与隐私说明」。

## License

数据与分析内容：个人私有，禁止再分发。代码：仅供本人使用。
