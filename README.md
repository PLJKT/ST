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

## 分析口径（重要）

- **两套数据源并存**：Excel 台账（POEMS 全历史 + FUTU 2021-01→2022-01）与 FUTU CSV 成交明细（2024-08→2026-09，541 笔）。FUTU 2022-03→2024-08 的导出缺失，两账户合并分析时已分别标注。
- FUTU 逐笔盈亏采用 **FIFO 批次法（毛利）**；Excel 台账为含费用的自记口径。
- POEMS 源文件中股票名称公式损坏（`#VALUE!`），无法恢复，历史成交以编号+市场+币种标识。
- 详细限制见仪表盘底部「数据口径与隐私说明」。

## License

数据与分析内容：个人私有，禁止再分发。代码：仅供本人使用。
