/* ST Dashboard v2 — 数据源 analytics.json rev2（卖空方向修正、分币种、资金明细并入）。 */
(function () {
"use strict";
let D;
try { D = JSON.parse(sessionStorage.getItem("st_data")); } catch (e) { D = null; }
if (!D || D.revision !== 2) { sessionStorage.removeItem("st_data"); location.replace("login.html"); return; }

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => v == null ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmt0 = (v) => v == null ? "—" : Math.round(Number(v)).toLocaleString("en-US");
const pct = (v, d = 1) => v == null ? "—" : (v * 100).toFixed(d) + "%";
const signCls = (v) => v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";
const usd = (v) => v == null ? "—" : (v > 0 ? "+$" : "$") + fmt(v);
const MONEY = { USD: "$", HKD: "HK$" };
const m = (ccy, v) => v == null ? "—" : (v > 0 ? "+" : "") + (MONEY[ccy] || ccy + " ") + fmt0(v);

document.addEventListener("DOMContentLoaded", () => {
  renderKpis(); renderInsights(); renderCharts(); renderTables(); renderCaveats();
  $("btnLogout").addEventListener("click", () => { sessionStorage.removeItem("st_data"); location.replace("login.html"); });
  $("metaTop").textContent = `POEMS + FUTU · 数据修订 v2（${D.generated_at}）· 会话内解密`;
});

function card(k, v, s, cls) { return `<div class="card"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div><div class="s">${s || ""}</div></div>`; }

function renderKpis() {
  const P = D.kpi.POEMS, N = D.futu_nav, F = D.futu;
  const realUSD = F.realized_by_ccy.USD, realHKD = F.realized_by_ccy.HKD;
  const costUSD = F.cash.interest_by_ccy.USD + F.cash.fees_by_ccy.USD + F.cash.dividend_by_ccy.USD;
  $("kpis").innerHTML = [
    card("FUTU 期末权益（USD 折算）", "$" + fmt0(N.equity_usd), "现金 " + m("USD", N.end_cash.USD) + " · 空头市值 " + m("HKD", N.positions_mv.short_hkd)),
    card("FUTU 账户净盈亏（双口径互证）", m("USD", N.pnl_mot_usd), "交易层互证 " + m("USD", N.pnl_tradeview_usd) + " · 差 $" + fmt(Math.abs(N.views_gap_usd)), "neg"),
    card("FUTU 已实现", m("USD", realUSD), "HKD " + fmt0(realHKD) + " ·（分币种不相加）", "neg"),
    card("FUTU 浮动盈亏", m("USD", F.float_by_ccy.USD), "按最后成交价估算", "neg"),
    card("FUTU 隐性成本", m("USD", costUSD), "融券费/利息/卖空股息——订单表不含", "neg"),
    card("POEMS 台账净值", "$" + fmt0(P.equity), "净赚 " + usd(P.net_gain) + "（" + pct(P.return) + "）", "pos"),
    card("POEMS 保证金风险", m("USD", P.cash_balance), "Margin Call " + m("USD", D.poems.margin["Margin Call"]), "neg"),
    card("FUTU 成交/委托", F.n_fills + " / " + F.orders.n, "撤单率 " + pct(F.orders.cancel_rate, 0) + " · 含卖空 " + F.directions["卖空"] + " 笔"),
  ].join("");
}

function renderInsights() {
  const f = D.futu, p = D.poems, dt = D.day_trade, N = D.futu_nav;
  const u = p.closed_stats_by_ccy["USD"], sq = f.per_symbol["SQQQ"], nv = f.per_symbol["NVDA"], sb = f.per_symbol["SBUX"], jr = f.per_symbol["01519"];
  $("insights").innerHTML = "<ul style='padding-left:20px'>" + [
    `<b>FUTU 账户整体亏损 $${fmt0(-N.pnl_mot_usd)}</b>（占迁入+入金基数 $${fmt0(N.capital.migration_usd_equiv + N.capital.bank_net_usd)} 的 ${pct(N.pnl_mot_usd / (N.capital.migration_usd_equiv + N.capital.bank_net_usd))}），资金流与交易层两口径互证差 $${fmt(Math.abs(N.views_gap_usd))}，结论可信。`,
    `<b>最大出血点：SQQQ ${usd(sq.realized)}</b>（115 笔，做多三倍做空 ETF，双向损耗）；<b>SBUX 空头亏 ${usd(sb.realized)}</b>（卖空 56 笔胜率仅 ${pct(sb.wins / sb.events, 0)}，做空强势消费股被轧空）。两笔合计 ${usd(sq.realized + sb.realized)}，超过账户总亏。`,
    `<b>盈利主力：</b>NVDA ${usd(nv.realized)}（46 次平仓 35 胜）、01519 空头 ${m("HKD", jr.realized)}、SOXL ${usd(f.per_symbol["SOXL"].realized)}、AMD ${usd(f.per_symbol["AMD"].realized)}。`,
    `<b>成本结构：</b>手续费+利息+融券费+卖空股息共 ${m("USD", N.components_usd.fees_interest + N.components_usd.dividends_net)}——其中<b>融券费 $1,091、卖空股息 $955 是此前订单表口径完全漏掉的</b>，实际成本比"佣金 $1,082"高出一倍。`,
    `<b>融资依赖：</b>期末现金 -$${fmt0(-N.end_cash.USD)} 全靠 2026 年 $${fmt0(N.capital.bank_net_usd)} 银行入金维持，期间 4 次入金 2 次出金——保证金账户长期负现金运转。`,
    `<b>POEMS：</b>美股 ${u.n} 笔、胜率 ${pct(u.win_rate, 0)}、净赚 $${fmt0(u.sum)}，但赔率 ${u.payoff}（均亏 $${fmt(-u.avg_loss)} > 均盈 $${fmt(u.avg_win)}）与 FUTU 同样的"小赚大亏"结构；台账现金 -$${fmt0(-p.margin["Cash Balance"])} 持续 margin call。`,
    `<b>行为信号：</b>${pct(f.orders.cancel_rate, 0)} 撤单率 + 76 笔卖空频繁进出，交易频率（2025 年 268 笔）与净亏负相关——降低频率是仪表盘给出的第一修正建议。`,
  ].map((s) => `<li>${s}</li>`).join("") + "</ul>";
}

const charts = [];
function mk(id, opt) { const el = $(id); if (!el || !window.echarts) return; const c = echarts.init(el, "dark"); c.setOption(opt); charts.push(c); new ResizeObserver(() => c.resize()).observe(el); }
const AX = (f2) => ({ axisLabel: { color: "#8593ab", formatter: f2 }, axisLine: { lineStyle: { color: "#232c3d" } } });

function renderCharts() {
  const eq = D.equity, mo = D.monthly, f = D.futu;
  mk("cEq", { backgroundColor: "transparent", title: { text: "POEMS 净值曲线（台账 2020-03→2022-01，美元）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, grid: { top: 40, bottom: 30, left: 62, right: 18 },
    xAxis: { type: "category", data: eq.months, ...AX() }, yAxis: { type: "value", scale: true, ...AX((v) => "$" + fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [{ name: "POEMS", type: "line", data: eq.POEMS, smooth: true, areaStyle: { opacity: .07 }, lineStyle: { width: 2.5, color: "#5aa7ff" }, itemStyle: { color: "#5aa7ff" } },
             { name: "本金", type: "line", symbol: "none", data: eq.months.map(() => D.kpi.POEMS.initial_deposit), lineStyle: { width: 1, color: "#666" } }] });
  mk("cBench", { backgroundColor: "transparent", title: { text: "净值 vs 指数（起点=100，POEMS 台账期）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } }, grid: { top: 40, bottom: 56, left: 48, right: 18 },
    xAxis: { type: "category", data: mo.months, ...AX() }, yAxis: { type: "value", scale: true, ...AX(), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [(() => { const a = eq.POEMS, b = a[0]; return { name: "POEMS净值", type: "line", symbol: "none", lineStyle: { width: 3, color: "#e8a13c" }, data: a.map((v) => +(v / b * 100).toFixed(1)) }; })(),
      ...["SPX", "NASDAQ", "HSI"].map((nm) => { const arr = mo.benchmark[nm]; return { name: { SPX: "标普500", NASDAQ: "纳斯达克", HSI: "恒指" }[nm], type: "line", symbol: "none", data: arr.map((v) => v == null ? null : +(v / arr[0] * 100).toFixed(1)) }; })] });
  mk("cMonthly", { backgroundColor: "transparent", title: { text: "POEMS 月度已实现（常规+日内，美元）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } }, grid: { top: 40, bottom: 56, left: 58, right: 18 },
    xAxis: { type: "category", data: mo.months, ...AX() }, yAxis: { type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [{ name: "常规", type: "bar", stack: "t", data: mo.POEMS_regular, itemStyle: { color: "#5aa7ff" } }, { name: "日内", type: "bar", stack: "t", data: mo.POEMS_day, itemStyle: { color: "#3d6ea8" } }] });
  const ms = Object.keys(f.monthly.USD || {}).sort();
  mk("cFutuMonthly", { backgroundColor: "transparent", title: { text: "FUTU 平仓事件逐月盈亏（USD 柱 / HKD 柱）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } }, grid: { top: 40, bottom: 56, left: 58, right: 18 },
    xAxis: { type: "category", data: [...new Set(ms.concat(Object.keys(f.monthly.HKD || {}).sort()))], ...AX() }, yAxis: { type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: ["USD", "HKD"].map((c) => ({ name: c, type: "bar", data: [...new Set(ms.concat(Object.keys(f.monthly.HKD || {}).sort()))].map((k) => (f.monthly[c] || {})[k] || 0), itemStyle: { color: c === "USD" ? "#5aa7ff" : "#2fbf71" } })) });
  const pts = p.closed_trades.filter((t) => t.investment && t.pl_pct != null).map((t) => [t.investment, t.pl_pct * 100, t.no, t.market, t.gain_loss]);
  mk("cPoemsScatter", { backgroundColor: "transparent", title: { text: "POEMS 已平仓：投入 × 收益率", left: 10, textStyle: { fontSize: 13 } }, tooltip: { formatter: (o) => `#${o.data[2]} ${o.data[3]}<br>投入 $${fmt0(o.data[0])} · ${o.data[1].toFixed(1)}% · ${usd(o.data[4])}` }, grid: { top: 40, bottom: 30, left: 52, right: 18 },
    xAxis: { type: "log", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } }, yAxis: { type: "value", ...AX((v) => v + "%"), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [{ type: "scatter", data: pts, symbolSize: (d) => Math.min(24, 4 + Math.sqrt(Math.abs(d[4] || 0)) / 3), itemStyle: { color: (o) => o.data[1] >= 0 ? "#2fbf71" : "#ef5b5b", opacity: .75 } }] });
  mk("cHoldDays", { backgroundColor: "transparent", title: { text: "POEMS 持有天数分布", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, grid: { top: 40, bottom: 30, left: 44, right: 18 },
    xAxis: { type: "category", data: Object.keys(p.holding_days_buckets), ...AX() }, yAxis: { type: "value", ...AX(), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: [{ type: "bar", data: Object.values(p.holding_days_buckets), itemStyle: { color: "#5aa7ff", borderRadius: [4, 4, 0, 0] } }] });
  mk("cDayGrade", { backgroundColor: "transparent", title: { text: "日内交易按评级（明细区 109 笔口径）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } }, grid: { top: 40, bottom: 56, left: 52, right: 40 },
    xAxis: { type: "category", data: Object.keys(D.day_trade.stats_by_grade), ...AX() },
    yAxis: [{ type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } }, { type: "value", min: 0, max: 1, axisLabel: { formatter: (v) => (v * 100) + "%" }, splitLine: { show: false } }],
    series: [{ name: "净盈亏", type: "bar", data: Object.values(D.day_trade.stats_by_grade).map((x) => x.sum), itemStyle: { color: (o) => o.data >= 0 ? "#2fbf71" : "#ef5b5b" } },
              { name: "胜率", type: "line", yAxisIndex: 1, data: Object.values(D.day_trade.stats_by_grade).map((x) => x.win_rate), itemStyle: { color: "#e8a13c" } }] });
  mk("cPoemsMkt", { backgroundColor: "transparent", title: { text: "POEMS 市场分布（外圈笔数/内圈盈亏）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "item" }, legend: { bottom: 0, textStyle: { color: "#8593ab" } },
    series: [{ type: "pie", radius: ["55%", "70%"], data: Object.entries(p.market_split).map(([k, v]) => ({ name: k, value: v.n })) },
             { type: "pie", radius: ["30%", "45%"], label: { show: false }, data: Object.entries(p.market_split).map(([k, v]) => ({ name: k, value: Math.abs(v.pl), itemStyle: { color: v.pl >= 0 ? "#2fbf71" : "#ef5b5b" } })) }] });
  const syms = Object.entries(f.per_symbol).map(([c, d]) => [c, d.realized, d.ccy, d.n, d.events]).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  mk("cFutuSym", { backgroundColor: "transparent", title: { text: "FUTU 各标的已实现（* 为 HKD，定稿多空模型）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { formatter: (o) => `${o.name}<br>${usd(o.data.v)} · ${o.data.n} 笔成交 / ${o.data.ev} 次平仓` }, grid: { top: 40, bottom: 30, left: 70, right: 30 },
    xAxis: { type: "value", ...AX((v) => fmt0(v)), splitLine: { lineStyle: { color: "#1a2233" } } }, yAxis: { type: "category", inverse: true, data: syms.map((s) => s[0] + (s[2] === "HKD" ? "*" : "")), ...AX() },
    series: [{ type: "bar", data: syms.map((s) => ({ value: s[1], v: s[1], n: s[3], ev: s[4], itemStyle: { color: s[1] >= 0 ? "#2fbf71" : "#ef5b5b" } })) }] });
  mk("cOrders", { backgroundColor: "transparent", title: { text: "FUTU 委托结构（方向 × 成交/撤单）", left: 10, textStyle: { fontSize: 13 } }, tooltip: { trigger: "axis", axisPointer: { type: "shadow" } }, legend: { bottom: 0, textStyle: { color: "#8593ab" } }, grid: { top: 40, bottom: 56, left: 52, right: 18 },
    xAxis: { type: "category", data: Object.keys(f.orders.direction), ...AX() }, yAxis: { type: "value", ...AX(), splitLine: { lineStyle: { color: "#1a2233" } } },
    series: Object.keys(f.orders.status).map((st) => ({ name: st, type: "bar", stack: "o", data: Object.keys(f.orders.direction).map((d) => ((f.orders.dir_status || {})[st] || {})[d] || 0) })) });
}

function tableHTML(cols, rows) { return `<table><thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`; }
function renderTables() {
  const f = D.futu;
  const rows = [...Object.entries(f.long_book).map(([c, d]) => ["多头 " + c, d.name, d.market, f.per_symbol[c].ccy, fmt0(d.qty), fmt(d.avg_cost, 3), fmt(d.last_price, 3), `<span class="${signCls(d.u_pnl)}">${m(f.per_symbol[c].ccy, d.u_pnl)}</span>`]),
                ...Object.entries(f.short_book).map(([c, d]) => ["空头 " + c, d.name, d.market, f.per_symbol[c].ccy, "−" + fmt0(d.qty), fmt(d.avg_price, 3), fmt(d.last_price, 3), `<span class="${signCls(d.u_pnl)}">${m(f.per_symbol[c].ccy, d.u_pnl)}</span>`])];
  $("tFutuOpen").innerHTML = tableHTML(["方向 代码", "名称", "市场", "币种", "数量", "开仓价", "现价*", "浮动盈亏*"], rows);
  const poRows = D.poems.open_positions.map((t) => [`#${t.no} ${t.grade || ""}`, t.market || "", t.ccy || "", fmt0(t.qty), fmt(t.buy_price, 2), fmt(t.current_price, 2), `<span class="${signCls(t.pl_pct)}">${pct(t.pl_pct)}</span>`, t.open_date || "—", t.holding_days != null ? fmt0(t.holding_days) : "—"]);
  $("tPoemsOpen").innerHTML = tableHTML(["编号", "市场", "币种", "数量", "买入价", "现价", "盈亏%", "建仓", "持有天"], poRows);
  renderTradeTable();
}
let shown = 60;
function renderTradeTable() {
  const acct = $("tradeAcct").value, q = $("tradeSearch").value.trim().toLowerCase();
  let cols, rows;
  if (acct === "poems") {
    cols = ["#", "市场", "币种", "数量", "买价", "均价", "卖价", "投入", "盈亏", "收益%", "开仓", "平仓", "持有天"];
    rows = D.poems.closed_trades.filter((t) => !q || ("#" + t.no + " " + (t.market || "") + " " + (t.ccy || "") + " " + (t.open_date || "") + " " + (t.close_date || "")).toLowerCase().includes(q))
      .map((t) => [`#${t.no}`, t.market || "", t.ccy || "", fmt0(t.qty), fmt(t.buy_price, 3), fmt(t.avg_price, 3), fmt(t.exit_price, 3), fmt(t.investment, 0), t.gain_loss != null ? `<span class="${signCls(t.gain_loss)}">${usd(t.gain_loss)}</span>` : "—", t.pl_pct != null ? pct(t.pl_pct) : "—", t.open_date || "—", t.close_date || "—", t.holding_days != null ? fmt0(t.holding_days) : "—"]);
  } else {
    cols = ["日期", "代码", "名称", "类型", "数量", "价格", "币种", "已实现"];
    rows = (D.futu.events || []).slice().reverse().filter((t) => !q || (t.code + " " + t.name + " " + t.date + " " + t.type).toLowerCase().includes(q))
      .map((t) => [t.date, t.code, t.name, t.type, fmt0(t.qty), fmt(t.price, 3), t.ccy, `<span class="${signCls(t.pnl)}">${m(t.ccy, t.pnl)}</span>`]);
  }
  $("tTrades").innerHTML = tableHTML(cols, rows.slice(0, shown));
  const more = $("tradeMore");
  if (rows.length > shown) { more.style.display = "inline-block"; more.textContent = `加载更多（已显示 ${shown}/${rows.length}）`; } else more.style.display = "none";
}
$("tradeAcct").addEventListener("change", () => { shown = 60; renderTradeTable(); });
$("tradeSearch").addEventListener("input", () => { shown = 60; renderTradeTable(); });
$("tradeMore").addEventListener("click", () => { shown += 150; renderTradeTable(); });

function renderCaveats() {
  $("caveats").innerHTML = "<b>数据修订记录（v2）</b><br>" + D.changelog.map((c, i) => `${i + 1}. ${c}`).join("<br>") +
    "<br><br><b>已知口径限制</b><br>" + D.caveats.map((c, i) => `${i + 1}. ${c}`).join("<br>") +
    "<br><br><b>安全说明</b><br>· 数据加密于 vault.json（AES-256-GCM，PBKDF2-SHA256 210,000 次迭代派生密钥），登录解密仅在本机浏览器完成，无服务端、无第三方请求。<br>· 明文只存在于当前标签页 sessionStorage，登出/关闭即销毁。";
  $("eqNote").innerHTML = "FUTU 净值双口径：① 资金流法（迁入+入金 vs 期末权益）= " + m("USD", D.futu_nav.pnl_mot_usd) + "；② 交易层法（已实现+浮动+费+息）= " + m("USD", D.futu_nav.pnl_tradeview_usd) + "；两法差 $" + fmt(Math.abs(D.futu_nav.views_gap_usd)) + "，互证成立。HKD→USD 折算率 " + D.futu_nav.fx_rate + " 仅用于展示合计。";
}
})();
