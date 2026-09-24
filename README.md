# ST — Investment Trading Dashboard

Personal trading analytics system covering two brokerage accounts (POEMS Singapore + FUTU Hong Kong/US), with an encrypted online dashboard hosted on GitHub Pages.

**Live site**: <https://pljkt.github.io/ST/>

---

## 1. Project Overview

| | |
|---|---|
| **POEMS** | Singapore margin account. Operated 2020-03 to 2022-01. Closed and migrated to FUTU on 2024-08-03. Current value: **$0** (account no longer exists). Historical realized P/L retained for review. |
| **FUTU** | Hong Kong/US margin account (#1599). Active since 2024-08-03 (asset migration from POEMS). All numbers derived strictly from broker-exported CSVs. |
| **Total loss** | ~$50,000 across both accounts. |

---

## 2. Repository Structure

```
PLJKT/ST (public repo)
│
├── main branch              # Source code & scripts
│   ├── README.md            # This file
│   ├── .gitignore           # Excludes data/, .github/, __pycache__/
│   ├── scripts/
│   │   ├── normalize.py     # Data pipeline: raw CSV/Excel -> analytics.json
│   │   └── build_vault.py   # Encryption: analytics.json -> vault.json
│   └── site/                # Frontend source (mirrored to gh-pages)
│       ├── index.html       # Redirect -> login.html
│       ├── login.html       # Username + password -> decrypt vault
│       ├── dashboard.html   # Main dashboard shell + CSS
│       ├── app.js           # Dashboard rendering logic (ECharts)
│       ├── crypto.js        # Browser-side AES-256-GCM decryption
│       ├── sha256.js        # SHA-256 fallback for non-secure contexts
│       ├── echarts.min.js   # Apache ECharts 5.5.1 (vendored, Apache-2.0)
│       ├── vault.js         # Encrypted data (JS wrapper for file:// access)
│       └── vault.json       # Encrypted data (AES-256-GCM)
│
├── gh-pages branch          # Deployed site (root-level files only)
│   └── [same 9 files from site/ — no scripts, no data]
│
└── Local only (gitignored)
    └── data/
        ├── raw/             # Broker exports (CSV x3 + Excel x1)
        └── processed/      # analytics.json + intermediate CSVs
```

**Key principle**: The `gh-pages` branch contains only the 9 frontend files needed to serve the site. No scripts, no raw data, no Python — anything else is stripped.

---

## 3. Data Sources

### FUTU (3 CSVs — the authoritative source)

| File | Records | Period | Role |
|------|---------|--------|------|
| 资金明细-*.csv | 1,082 | 2024-08 to 2026-09 | **Authority**: account-level truth (cash, fees, interest, dividends, deposits) |
| 历史成交-*.csv | 541 | 2024-08 to 2026-09 | Trade fills for FIFO matching |
| 历史订单-*.csv | 824 | 2024-08 to 2026-09 | Validation (order structure, cancel rate, fees) |

### POEMS (1 Excel)

| File | Sheets used | Role |
|------|-------------|------|
| Share Investment.xlsx | Overall Summary, SG Portfolio, Day Trade, Fee and Margin, IndoStock | Historical snapshot (closed 2022-01). Names, trades, margin — read-only for review. |

### Multi-currency handling

- FUTU trades span USD (519) and HKD (22, for SMIC/00981).
- All statistics are kept **per-currency**. HKD to USD conversion rate (0.12875, 2026-09 reference) is used **only** for display-level totals, never in core calculations.
- Exchange rate extracted from currency conversion records in 资金明细 CSV.

---

## 4. Data Pipeline (`scripts/normalize.py`)

### Input to Output

```
data/raw/ (CSV x3 + Excel x1)
        |
        v
  normalize.py
        |
        v
data/processed/analytics.json (144KB, plaintext)
```

### Pipeline stages

1. **FUTU trade fills** — FIFO matching for three directions (buy/sell/short):
   - Buy: opens long, or covers existing short
   - Sell: closes long, remainder opens short
   - Short sell: opens new short
   - Produces per-symbol realized P/L, open positions, floating P/L

2. **FUTU cash flow** (资金明细) — account-level truth:
   - Aggregates by type (trades, fees, interest, dividends, asset migration, bank deposits/withdrawals, currency conversion)
   - Balance chain verification: initial + sum(all amounts) = final
   - Extracts deposits, withdrawals, transfers, fees, interest, dividends

3. **FUTU orders** — validation:
   - Status breakdown (filled/cancelled/partially cancelled), direction counts, fee aggregation
   - Cancel rate calculation

4. **Three-way reconciliation** (资金明细 as authority):
   - Fills vs cash: multiset matching by (currency, direction, amount), +/- $0.02 tolerance
   - Order fees vs cash fees: difference = margin lending fee (USD $1,091 / HKD $22.86)
   - Duplicate fill detection: 7 groups confirmed as real split-fills via cash-side dual entries

5. **FUTU NAV** (dual-view cross-validation):
   - View 1 (MOT): equity - migration - bank deposits = P/L
   - View 2 (Trade-layer): realized + floating + costs + dividends + MMF = P/L
   - Gap: $0.21 (rounding level) — both views confirmed

6. **POEMS** (Excel, read-only snapshot):
   - KPI from Overall Summary row 4 (initial deposit, realized, unrealized, equity, net gain)
   - Closed trades from SG Portfolio (158 trades with full detail)
   - Day trade from Day Trade sheet (109 trades by detail, 73 by summary — detail is authority)
   - Margin from Fee and Margin sheet
   - **All current values overridden to $0** (account closed 2022-01, migrated to FUTU 2024-08)
   - Historical realized/fees/net-gain retained as review records

7. **Analytics modules**:
   - Yearly P/L (by account, USD-converted)
   - Payback scenarios (5 rates x pure-compound + monthly-topup)
   - Ranking (top 10 winners/losers, cross-account, USD-converted)
   - Equity curve, monthly P/L, benchmark comparison

### Output structure (`analytics.json`)

| Key | Contents |
|-----|----------|
| `generated_at` | Pipeline run timestamp |
| `revision` | Schema version (currently 4) |
| `recon` | Three-way reconciliation results |
| `kpi` | POEMS summary card data |
| `futu_nav` | FUTU account-level NAV (dual-view) |
| `equity` | Monthly equity curve + POEMS recon |
| `monthly` | POEMS monthly series + benchmarks |
| `poems` | Closed trades, open positions, margin, market split |
| `futu` | Per-symbol stats, long/short books, events, orders, cash |
| `yearly` | Annual P/L breakdown |
| `ranking` | Top 10 winners/losers |
| `payback` | Break-even scenarios |
| `day_trade` | Day trade stats by grade |
| `changelog` | Revision history |
| `caveats` | Known data limitations |

### Running the pipeline

```bash
# Prerequisites: Python 3.13+, openpyxl, cryptography
# 1. Place raw files in data/raw/
# 2. Run pipeline
python scripts/normalize.py
# Output: data/processed/analytics.json
```

---

## 5. Encryption System

### Architecture

```
analytics.json (plaintext, local only)
        |
        v
  build_vault.py (--password)
        |
        +-- PBKDF2-SHA256 (210,000 iterations) -> derive AES-256 key
        +-- AES-256-GCM encrypt (with associated data "ST-vault-v1")
        +-- Output: vault.json + vault.js
        |
        v
  vault.json / vault.js (ciphertext, safe to commit)
```

### Security properties

- **Zero-knowledge**: Password never leaves the browser. Key derivation + decryption happen entirely client-side via WebCrypto API.
- **No backend**: Static files only (HTML/JS/JSON). No server, no API, no database.
- **No third-party**: ECharts vendored locally. No CDN, no analytics, no telemetry.
- **Session isolation**: Decrypted data exists only in `sessionStorage` for the current tab. Logout or tab close = data destroyed.
- **No backdoor**: Password = key. If forgotten, must re-run `build_vault.py` to reset.
- **Username verification**: SHA-256 hash of username stored in vault; checked before decryption attempt.

### Building the vault

```bash
python scripts/build_vault.py --password '<password>' --username admin
# Output: site/vault.json + site/vault.js
```

### Browser-side decryption flow

1. User enters username + password on `login.html`
2. SHA-256(username) compared against vault.username_sha256
3. PBKDF2-SHA256(password, salt, 210K iterations) -> AES-256 key
4. SHA-256(key) compared against vault.verifier (password check)
5. AES-256-GCM decrypt(vault.data) -> analytics.json
6. Parsed JSON stored in sessionStorage
7. Redirect to dashboard.html

---

## 6. Deployment

### Branch strategy

| Branch | Purpose | Contents |
|--------|---------|----------|
| `main` | Source code | Scripts + site/ source + README + .gitignore |
| `gh-pages` | Live site | 9 frontend files at root (no scripts, no data) |

### Deploy workflow

```bash
# 1. Run data pipeline
python scripts/normalize.py

# 2. Rebuild encrypted vault
python scripts/build_vault.py --password '<password>' --username admin

# 3. Commit to main
git add scripts/ site/ .gitignore README.md
git commit -m "Update: <description>"
git push origin main

# 4. Sync site files to gh-pages
git checkout gh-pages
git show main:site/app.js > app.js
git show main:site/dashboard.html > dashboard.html
git show main:site/vault.js > vault.js
git show main:site/vault.json > vault.json
# (index.html, login.html, crypto.js, sha256.js, echarts.min.js rarely change)
git add -A
git commit -m "Sync site files from main"
git push origin gh-pages

# 5. Return to main
git checkout main
```

### GitHub Pages configuration

- **Source**: Deploy from `gh-pages` branch, root directory
- **Site URL**: <https://pljkt.github.io/ST/>
- **Repo visibility**: Public (required for free GitHub Pages on this plan)
- **Data protection**: Raw CSVs and Excel are gitignored; only encrypted vault.json is public

---

## 7. Frontend (`site/app.js`)

### Smart number formatting

```javascript
fmt(v)   // Integers -> "1,234" (no decimals); non-integers -> "1,234.5" (1 decimal)
fmt0(v)  // Alias of fmt(v)
pct(v)   // Integer % -> "50%"; non-integer -> "57.8%"
```

### Dashboard sections

| Section | ID | Description |
|---------|-----|-------------|
| Overview | `#overview` | KPI cards (FUTU NAV, P/L, POEMS $0 closed) |
| Diagnosis | `#diagnosis` | Auto-generated insights |
| Reconciliation | `#recon` | Three-way data validation table |
| Equity | `#equity` | Net value curve, benchmark, monthly P/L |
| Yearly | `#yearly` | Annual capital vs P/L |
| Payback | `#payback` | Break-even scenarios |
| Ranking | `#ranking` | Top 10 winners/losers |
| POEMS | `#poems` | Scatter plot, holding days, market split |
| FUTU | `#futu` | Per-symbol P/L, order structure |
| Positions | `#positions` | Current holdings (FIFO remaining) |
| Trades | `#trades` | Searchable trade detail table |
| Notes | `#notes` | Data caveats and security notes |

### Error handling

Each rendering module is wrapped in `safe(name, fn)`. If a module throws, it is caught and displayed as an error banner at the top of the page, without breaking other modules.

---

## 8. Data Accuracy

### FUTU — verified against raw CSVs

| Metric | Value | Source |
|--------|-------|--------|
| End cash (USD) | -$14,618.28 | 资金明细 last balance |
| Equity (USD equiv) | $16,769.32 | Cash + positions MV (HKD->USD at 0.12875) |
| Net P/L | -$26,183.65 | Equity - migration - bank deposits |
| Realized | -$15,910.30 | FIFO matching (trade-layer) |
| Floating | -$7,269.82 | Last fill price x open qty |
| Hidden costs | -$2,670.54 + (-$353.39) + $20.61 | Fees + dividends + MMF (order table does not include) |
| NAV gap | $0.21 | MOT vs trade-layer (rounding) |

### POEMS — closed account

- Last transaction: 2022-01-27
- Migrated to FUTU: 2024-08-03
- Current treatment: equity = $0, cash = $0, unrealized = $0, all margin = $0
- Retained for review: realized $6,663, net gain $1,896
- Excel ledger maintenance stopped at 2022-01

### Known limitations

1. POEMS stock names in detail rows are formula-damaged (#VALUE!). Names recovered from ledger's Transaction History summary.
2. Day Trade detail (109 trades) vs summary (73 trades) mismatch — detail is authority.
3. FUTU 2022-03 to 2024-08 trades missing (no export available).
4. Floating P/L uses last fill price, not real-time quotes.
5. FUTU unrealized loss attributed to last event year (no daily valuation available).

---

## 9. Tech Stack

| Layer | Technology |
|-------|-----------|
| Data pipeline | Python 3.13, openpyxl (Excel), csv (CSV), cryptography (AES) |
| Frontend | Vanilla JS, HTML5, CSS Grid/Flexbox |
| Charts | Apache ECharts 5.5.1 (vendored, Apache-2.0) |
| Crypto | WebCrypto API (browser) + `cryptography` library (Python) |
| Hosting | GitHub Pages (static, gh-pages branch) |
| Encryption | AES-256-GCM + PBKDF2-SHA256 (210K iterations) |

---

## 10. Maintenance Guide

### Updating data (new broker export)

1. Download new CSVs from Futu (历史成交, 历史订单, 资金明细)
2. Replace files in `data/raw/`
3. If POEMS Excel updated, replace `data/raw/Share Investment.xlsx`
4. Run `python scripts/normalize.py` — verify output prints `v2 OK` and validations pass
5. Run `python scripts/build_vault.py --password '<password>' --username admin`
6. Follow deployment steps in section 6

### Changing the password

```bash
python scripts/build_vault.py --password '<new_password>' --username admin
# Then deploy vault.json + vault.js to both branches
```

### Adding a new chart/section

1. Add rendering function in `app.js` (follow `safe()` pattern)
2. Call it in `DOMContentLoaded` handler
3. Add section HTML in `dashboard.html`
4. Add nav link in the `<nav>` element
5. If new data needed, add to `analytics.json` in `normalize.py`

### Production release checklist

- [ ] `normalize.py` runs clean (all validations pass)
- [ ] `build_vault.py` produces vault.json + vault.js
- [ ] Test login locally (file://login.html with vault.js fallback)
- [ ] Verify dashboard renders all sections
- [ ] Check POEMS cards show $0 (closed account)
- [ ] Check FUTU numbers match raw CSVs
- [ ] Commit to main branch
- [ ] Sync 4 files (app.js, dashboard.html, vault.js, vault.json) to gh-pages
- [ ] Push both branches
- [ ] Verify live site at https://pljkt.github.io/ST/

---

## License

Data and analysis content: personal private, not for redistribution. Code: personal use only.
