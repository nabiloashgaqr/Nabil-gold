// SmartSignal Live Dashboard
// Hotfix build: robust helpers + safe rendering against malformed records.

const API_URL = (window.SMARTSIGNAL_API_URL || '/api/dashboard');
const OUTCOME_STATUSES = new Set(['TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BE_HIT', 'EXPIRED', 'MANUAL_CLOSE', 'CLOSED']);
const LIVE_STATUSES = new Set([]);

let currentLang = 'en';
let closedTrades = [];
let liveTrades = [];
let filteredTrades = [];
let dashboardPayload = null;
let charts = { daily: null, cumulative: null, session: null, instrument: null };
let autoRefreshInterval = null;

const I18N = {
  ar: {
    api404: 'ملف API غير منشور على Vercel: /api/dashboard يرجع 404. إذا كان Root Directory في Vercel هو dashboard، يجب رفع الملف داخل dashboard/api/dashboard.js ثم عمل Redeploy.',
    loadError: 'تعذر تحميل البيانات',
    noClosed: 'لا توجد صفقات مغلقة حسب الفلتر الحالي',
    noDaily: 'لا يوجد تقرير يومي بعد.',
    noWeekly: 'لا يوجد تقرير أسبوعي بعد.',
    noReports: 'لا توجد تقارير محفوظة',
    details: 'تفاصيل',
    noReportText: 'لا يوجد نص تقرير',
  },
  en: {
    api404: 'Dashboard API is not deployed: /api/dashboard returns 404. If Vercel Root Directory is dashboard, upload dashboard/api/dashboard.js and redeploy.',
    loadError: 'Failed to load data',
    noClosed: 'No closed trades match the current filter',
    noDaily: 'No daily report yet.',
    noWeekly: 'No weekly report yet.',
    noReports: 'No saved reports',
    details: 'Details',
    noReportText: 'No report text',
  }
};

function $(id) { return document.getElementById(id); }
function tr(key) { return (I18N[currentLang] && I18N[currentLang][key]) || I18N.en[key] || key; }
function setText(id, value) { const el = $(id); if (el) el.textContent = value == null ? '' : String(value); }
function setHTML(id, value) { const el = $(id); if (el) el.innerHTML = value == null ? '' : String(value); }
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, m => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[m]));
}
function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}
function signed(value, digits = 1) {
  const n = num(value);
  if (!Number.isFinite(n)) return '--';
  const fixed = n.toFixed(digits);
  return n > 0 ? `+${fixed}` : fixed;
}
function localeCode() { return 'en-US'; }
function formatDateTime(value) {
  const d = value ? new Date(value) : new Date();
  return d.toLocaleString(localeCode(), { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
}
function reportText(r) { return currentLang === 'ar' ? (r.report_text_ar || r.report_text || '') : (r.report_text_en || r.report_text || ''); }
function wordTrades(n) { return currentLang === 'ar' ? `${n} صفقات` : `${n} trades`; }
function wordReports(n) { return currentLang === 'ar' ? `${n} تقارير` : `${n} reports`; }
function openTime(t) { return t.entry_time || t.created_at || t.opened_at || ''; }
function closeTime(t) { return t.closed_at || t.close_time || ''; }
function tradeTime(t) { return openTime(t); }
function reportDate(t) { return String(closeTime(t) || openTime(t) || '').slice(0, 10) || '-'; }
function dateText(v) { return v ? formatDateTime(v) : '-'; }
function timeText(v) { return dateText(v); }
function pnlOf(t) { return num(t.pnl ?? t.final_pnl ?? t.current_pnl_points ?? t.current_pnl ?? 0); }
function isClosedStatus(status) { return OUTCOME_STATUSES.has(String(status || '').toUpperCase()); }
function isLiveStatus(status) { return LIVE_STATUSES.has(String(status || '').toUpperCase()); }
function displayStatus(trade) { return String(trade.status || 'UNKNOWN').replaceAll('_', ' '); }
function statusClassOf(trade) {
  const pnl = pnlOf(trade);
  if (pnl > 0) return 'win';
  if (pnl < 0) return 'loss';
  return 'be';
}
function normalizeTrade(t) {
  return {
    ...t,
    id: t.id || '',
    symbol: t.symbol || 'XAU/USD',
    type: String(t.type || t.side || t.trade_type || '').toUpperCase(),
    status: String(t.status || 'UNKNOWN').toUpperCase(),
    pnl: pnlOf(t),
  };
}
function sessionBucket(t) {
  const source = openTime(t) || closeTime(t);
  const d = source ? new Date(source) : null;
  if (!d || Number.isNaN(d.getTime())) return 'Unknown';
  const h = d.getUTCHours();
  if (h < 7) return 'Asia Morning';
  if (h < 12) return 'London / Europe Midday';
  if (h < 17) return 'London + New York Afternoon';
  if (h < 21) return 'New York Evening';
  return 'Late New York Night';
}

function setLang(_lang) {
  currentLang = 'en';
  document.documentElement.lang = 'en';
  document.documentElement.dir = 'ltr';
  if (dashboardPayload) {
    setText('lastUpdate', formatDateTime(dashboardPayload.generatedAt || Date.now()));
    renderReports(dashboardPayload);
    updateCharts(filteredTrades);
  }
}

function setError(message) {
  const el = $('errorBox');
  if (!el) return;
  if (!message) { el.style.display = 'none'; el.textContent = ''; return; }
  el.style.display = 'block';
  el.textContent = message;
}

function showSection(sectionId) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  const target = $(sectionId);
  if (target) target.classList.add('active');
  document.querySelectorAll('.nav-link').forEach(link => {
    link.classList.toggle('active', link.getAttribute('href') === `#${sectionId}`);
  });
  if (sectionId === 'agents') updateAgentPerformance();
}

async function loadDashboardData() {
  setError('');
  try {
    const res = await fetch(`${API_URL}?limit=200&t=${Date.now()}`, { cache: 'no-store' });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok || !payload.ok) throw new Error(payload.error || `API error ${res.status}`);

    dashboardPayload = payload;
    closedTrades = (payload.closedTrades || []).map(normalizeTrade).filter(t => isClosedStatus(t.status));
    liveTrades = [];
    filteredTrades = [...closedTrades];

    updateStats(filteredTrades, liveTrades);
    updateCharts(filteredTrades);
    renderTradesTable(filteredTrades);
    renderReports(payload);
    updateAgentPerformance();

    setText('lastUpdate', formatDateTime(payload.generatedAt || Date.now()));
    setText('dataSource', payload.source || 'api');
  } catch (error) {
    console.error('Dashboard load failed:', error);
    closedTrades = [];
    liveTrades = [];
    filteredTrades = [];
    updateStats([], []);
    updateCharts([]);
    renderTradesTable([]);
    renderReports({ dailyReports: [], weeklyReports: [] });
    setText('dataSource', 'error');
    setText('lastUpdate', formatDateTime(Date.now()));
    setError(`${tr('loadError')}: ${String(error.message || '').includes('404') ? tr('api404') : error.message}`);
  }
}

function setChartEmpty(id, isEmpty) {
  const el = $(id);
  if (el) el.style.display = isEmpty ? 'flex' : 'none';
}
function safeDestroyChart(name) {
  if (charts[name]) { charts[name].destroy(); charts[name] = null; }
}

function updateStats(trades, live) {
  const total = trades.length;
  const wins = trades.filter(t => pnlOf(t) > 0 || ['TP2_HIT'].includes(t.status));
  const netPnl = trades.reduce((sum, t) => sum + pnlOf(t), 0);
  const winRate = total ? (wins.length / total) * 100 : 0;
  const grossProfit = trades.filter(t => pnlOf(t) > 0).reduce((sum, t) => sum + pnlOf(t), 0);
  const grossLoss = Math.abs(trades.filter(t => pnlOf(t) < 0).reduce((sum, t) => sum + pnlOf(t), 0));
  const profitFactor = grossLoss > 0 ? (grossProfit / grossLoss).toFixed(2) : grossProfit > 0 ? '∞' : '--';
  const pnls = trades.map(pnlOf);
  const best = pnls.length ? Math.max(...pnls) : 0;
  const worst = pnls.length ? Math.min(...pnls) : 0;
  const avg = total ? netPnl / total : 0;
  const tp1Live = trades.filter(t => t.status === 'TP1_HIT').length;

  setText('totalTrades', total);
  setText('winRate', `${winRate.toFixed(1)}%`);
  setText('netPoints', signed(netPnl));
  setText('profitFactor', profitFactor);
  setText('liveCount', total);
  setText('tp1Count', tp1Live);
  setText('bestTrade', pnls.length ? signed(best) : '--');
  setText('worstTrade', pnls.length ? signed(worst) : '--');
  setText('avgTrade', total ? signed(avg) : '--');
  setText('expectancy', total ? signed(avg) : '--');
  setText('tradesCount', `(${total})`);

  const netEl = $('netPoints');
  if (netEl) netEl.style.color = netPnl >= 0 ? '#2b8a3e' : '#c92a2a';
  const bar = $('winRateBar');
  if (bar) bar.style.width = `${Math.min(winRate, 100)}%`;
}

function chartOptions(extra = {}) {
  const isDark = document.body.classList.contains('dark');
  const grid = isDark ? 'rgba(148,163,184,.14)' : 'rgba(148,163,184,.22)';
  const tick = isDark ? '#94a3b8' : '#64748b';
  return {
    responsive: true,
    maintainAspectRatio: false,
    resizeDelay: 120,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: isDark ? '#111827' : '#ffffff',
        titleColor: isDark ? '#f8fafc' : '#111827',
        bodyColor: isDark ? '#f8fafc' : '#111827',
        borderColor: grid,
        borderWidth: 1,
        padding: 12,
        displayColors: false,
        callbacks: { label: ctx => `PnL: ${signed(ctx.parsed.y ?? ctx.parsed, 0)} pts` }
      }
    },
    scales: {
      y: { beginAtZero: true, grid: { color: grid, drawBorder: false }, ticks: { color: tick, callback: v => signed(v, 0) } },
      x: { grid: { display: false }, ticks: { color: tick, maxRotation: 0, autoSkip: true } }
    },
    ...extra,
  };
}

function updateDailyPnlChart(trades) {
  const daily = {};
  trades.forEach(t => {
    const d = reportDate(t);
    if (d !== '-') daily[d] = (daily[d] || 0) + pnlOf(t);
  });
  const labels = Object.keys(daily).sort().slice(-14);
  const data = labels.map(d => daily[d]);
  const ctx = $('dailyPnlChart');
  setChartEmpty('dailyPnlEmpty', !data.length);
  setText('dailyChartTotal', data.length ? signed(data.reduce((a,b)=>a+b,0)) : '--');
  if (!ctx || typeof Chart === 'undefined') return;
  safeDestroyChart('daily');
  if (!data.length) return;
  const gradient = ctx.getContext('2d').createLinearGradient(0, 0, 0, 320);
  gradient.addColorStop(0, 'rgba(37,99,235,.9)');
  gradient.addColorStop(1, 'rgba(6,182,212,.45)');
  charts.daily = new Chart(ctx.getContext('2d'), {
    type: 'bar',
    data: { labels: labels.map(d => d.substring(5)), datasets: [{ data, backgroundColor: data.map(v => v >= 0 ? gradient : 'rgba(220,38,38,.72)'), borderRadius: 10, borderSkipped: false, maxBarThickness: 46 }] },
    options: chartOptions({ scales: { y: { ...chartOptions().scales.y, grace: '15%' }, x: chartOptions().scales.x } }),
  });
}

function updateCumulativePnlChart(trades) {
  const sorted = [...trades].sort((a, b) => String(openTime(a)).localeCompare(String(openTime(b))));
  let cumulative = 0;
  const data = sorted.map(t => { cumulative += pnlOf(t); return cumulative; });
  const labels = sorted.map(t => reportDate(t).substring(5));
  const ctx = $('cumulativePnlChart');
  setChartEmpty('cumulativePnlEmpty', !data.length);
  if (!ctx || typeof Chart === 'undefined') return;
  safeDestroyChart('cumulative');
  if (!data.length) return;
  charts.cumulative = new Chart(ctx.getContext('2d'), {
    type: 'line',
    data: { labels, datasets: [{ data, borderColor: '#1971c2', backgroundColor: 'rgba(25,113,194,.14)', fill: true, tension: .35, pointRadius: 2 }] },
    options: chartOptions(),
  });
}

function updateSessionChart(trades) {
  const order = ['Asia Morning', 'London / Europe Midday', 'London + New York Afternoon', 'New York Evening', 'Late New York Night'];
  const grouped = {};
  const counts = {};
  order.forEach(k => { grouped[k] = 0; counts[k] = 0; });
  trades.forEach(t => {
    const session = sessionBucket(t);
    grouped[session] = (grouped[session] || 0) + pnlOf(t);
    counts[session] = (counts[session] || 0) + 1;
  });
  const labels = order.filter(k => counts[k] > 0 || grouped[k] !== 0);
  const data = labels.map(k => grouped[k]);
  const displayLabels = labels.map(k => `${k} (${counts[k]})`);
  const ctx = $('sessionChart');
  setChartEmpty('sessionEmpty', !data.length);
  if (!ctx || typeof Chart === 'undefined') return;
  safeDestroyChart('session');
  if (!data.length) return;
  charts.session = new Chart(ctx.getContext('2d'), {
    type: 'bar',
    data: { labels: displayLabels, datasets: [{ data, backgroundColor: data.map(v => v >= 0 ? 'rgba(22,163,74,.72)' : 'rgba(220,38,38,.72)'), borderWidth: 1, borderRadius: 8, maxBarThickness: 34 }] },
    options: chartOptions({ indexAxis: 'y' }),
  });
}

function updateInstrumentChart(trades) {
  const grouped = {};
  trades.forEach(t => { grouped[t.symbol || 'XAU/USD'] = (grouped[t.symbol || 'XAU/USD'] || 0) + Math.abs(pnlOf(t)); });
  const labels = Object.keys(grouped);
  const data = labels.map(k => grouped[k]);
  const ctx = $('instrumentChart');
  setChartEmpty('instrumentEmpty', !data.length);
  if (!ctx || typeof Chart === 'undefined') return;
  safeDestroyChart('instrument');
  if (!data.length) return;
  charts.instrument = new Chart(ctx.getContext('2d'), {
    type: 'doughnut',
    data: { labels, datasets: [{ data, backgroundColor: ['#e67700', '#1971c2', '#2b8a3e', '#c92a2a', '#7048e8'], borderWidth: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } },
  });
}

function updateCharts(trades) {
  updateDailyPnlChart(trades);
  updateCumulativePnlChart(trades);
  updateSessionChart(trades);
  updateInstrumentChart(trades);
}

function renderTradesTable(trades) {
  const tbody = $('tradesBody');
  if (!tbody) return;
  if (!trades.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="loading">${esc(tr('noClosed'))}</td></tr>`;
    return;
  }
  tbody.innerHTML = trades.slice(0, 120).map(trade => {
    const pnl = pnlOf(trade);
    const statusClass = statusClassOf(trade);
    return `
      <tr>
        <td>${esc(dateText(tradeTime(trade)))}</td>
        <td>${esc(dateText(closeTime(trade)))}</td>
        <td><strong>${esc(trade.symbol)}</strong></td>
        <td>${esc(trade.type || 'N/A')}</td>
        <td>${num(trade.entry_price).toFixed(2)}</td>
        <td>${trade.close_price != null ? num(trade.close_price).toFixed(2) : '-'}</td>
        <td class="${statusClass}"><strong>${signed(pnl)}</strong></td>
        <td>${esc(displayStatus(trade))}</td>
        <td>${esc(trade.confidence ?? '--')}%</td>
        <td><button class="btn btn-ghost" onclick="showTradeModalById('${esc(trade.id)}')">${tr('details')}</button></td>
      </tr>`;
  }).join('');
}

function applyFilters() {
  const symbol = $('filterSymbol')?.value || '';
  const result = $('filterResult')?.value || '';
  const dateFrom = $('filterDateFrom')?.value || '';
  const dateTo = $('filterDateTo')?.value || '';
  const search = ($('searchInput')?.value || '').toLowerCase().trim();

  filteredTrades = closedTrades.filter(trade => {
    const pnl = pnlOf(trade);
    const tDate = reportDate(trade);
    if (symbol && trade.symbol !== symbol) return false;
    if (result === 'win' && pnl <= 0) return false;
    if (result === 'loss' && pnl >= 0) return false;
    if (result === 'be' && pnl !== 0) return false;
    if (dateFrom && tDate < dateFrom) return false;
    if (dateTo && tDate > dateTo) return false;
    if (search) {
      const text = `${trade.symbol} ${trade.type} ${trade.status} ${trade.session || ''} ${trade.id}`.toLowerCase();
      if (!text.includes(search)) return false;
    }
    return true;
  });
  updateStats(filteredTrades, liveTrades);
  updateCharts(filteredTrades);
  renderTradesTable(filteredTrades);
}

function clearFilters() {
  ['filterSymbol', 'filterResult', 'filterDateFrom', 'filterDateTo', 'searchInput'].forEach(id => { const el = $(id); if (el) el.value = ''; });
  filteredTrades = [...closedTrades];
  updateStats(filteredTrades, liveTrades);
  updateCharts(filteredTrades);
  renderTradesTable(filteredTrades);
}

function exportToCSV() {
  if (!filteredTrades.length) return;
  const headers = ['ID', 'OpenDate', 'CloseDate', 'Symbol', 'Type', 'Entry', 'Close', 'PnL', 'Status', 'Confidence'];
  const rows = filteredTrades.map(t => [t.id, tradeTime(t), closeTime(t), t.symbol, t.type, t.entry_price, t.close_price ?? '', pnlOf(t), t.status, t.confidence ?? '']);
  const csv = [headers, ...rows].map(row => row.map(cell => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(',')).join('
');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `smartsignal_closed_trades_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function monthLabel(month) {
  if (!month || month.length < 7) return 'No Month';
  const [y, m] = month.split('-');
  const namesEn = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  const idx = Math.max(0, Math.min(11, Number(m) - 1));
  return `${namesEn[idx]} ${y}`;
}
function reportPeriod(report, type) {
  if (type === 'weekly') return `${report.week_start || '-'} → ${report.week_end || '-'}`;
  return report.report_date || (report.created_at || '').slice(0, 10) || '-';
}
function groupReportsByMonth(reports, type) {
  const groups = {};
  reports.forEach(r => {
    const period = reportPeriod(r, type);
    const month = r.month || String(period).slice(0, 7) || 'unknown';
    if (!groups[month]) groups[month] = [];
    groups[month].push(r);
  });
  Object.values(groups).forEach(list => list.sort((a, b) => reportPeriod(b, type).localeCompare(reportPeriod(a, type))));
  return groups;
}
function renderReportArchive(containerId, reports, type) {
  const el = $(containerId);
  if (!el) return;
  if (!reports.length) {
    el.innerHTML = `<div class="loading">${esc(tr('noReports'))}</div>`;
    return;
  }
  const groups = groupReportsByMonth(reports, type);
  const months = Object.keys(groups).sort().reverse();
  el.innerHTML = months.map(month => {
    const list = groups[month];
    const totalNet = list.reduce((sum, r) => sum + num(r.daily_pnl ?? r.net_pnl_points ?? r.stats_json?.net_pnl_points ?? r.stats_json?.net_points ?? 0), 0);
    const items = list.map((r) => {
      const period = reportPeriod(r, type);
      const net = num(r.daily_pnl ?? r.net_pnl_points ?? r.stats_json?.net_pnl_points ?? r.stats_json?.net_points ?? 0);
      const trades = num(r.closed_trades ?? r.stats_json?.closed_trades ?? r.stats_json?.total_trades ?? r.stats_json?.total ?? 0);
      const wr = num(r.win_rate ?? r.stats_json?.win_rate_pct ?? r.stats_json?.win_rate ?? 0);
      return `
        <div class="report-file open">
          <div class="report-file-header">${type === 'weekly' ? '🗓️' : '📄'} ${esc(period)} — ${esc(wordTrades(trades))} · WR ${wr.toFixed(1)}% · <strong>${signed(net, 1)}</strong></div>
          <pre class="report-content">${esc(reportText(r) || tr('noReportText'))}</pre>
        </div>`;
    }).join('');
    return `
      <div class="report-month-group">
        <div class="report-month-header">📁 <strong>${esc(monthLabel(month))}</strong> — ${esc(wordReports(list.length))} · <strong>${signed(totalNet, 1)}</strong></div>
        ${items}
      </div>`;
  }).join('');
}
function renderReports(payload) {
  const daily = payload.dailyReports || [];
  const weekly = payload.weeklyReports || [];
  const latestDaily = daily[0];
  const latestWeekly = weekly[0];
  setText('dailyReport', latestDaily ? (reportText(latestDaily) || tr('noReportText')) : tr('noDaily'));
  setText('weeklyReport', latestWeekly ? (reportText(latestWeekly) || JSON.stringify(latestWeekly.stats_json || {}, null, 2)) : tr('noWeekly'));
  renderReportArchive('dailyReportsArchive', daily, 'daily');
  renderReportArchive('weeklyReportsArchive', weekly, 'weekly');
}

function updateAgentPerformance() {
  const agents = (dashboardPayload?.agentPerformance || dashboardPayload?.agentWeights || []);
  const grid = $('agentsGrid');
  if (!grid) return;
  if (!agents.length) {
    grid.innerHTML = ['technical', 'smc', 'classical', 'price_action', 'multitimeframe'].map(name => `
      <div class="agent-card"><h3>🤖 ${esc(name)}</h3><p>No performance data yet</p></div>`).join('');
    setText('consensusStrength', '--');
    return;
  }
  grid.innerHTML = agents.map(a => {
    const hasComputed = a.win_rate !== null && a.win_rate !== undefined && Number.isFinite(Number(a.win_rate));
    const wr = hasComputed ? num(a.win_rate) : 0;
    const weight = num(a.weight) * 100;
    const predictions = num(a.total_predictions ?? a.predictions ?? 0);
    const wins = num(a.wins ?? 0);
    const losses = num(a.losses ?? 0);
    const net = num(a.net_pnl ?? 0);
    const sourceLabel = a.source === 'computed_from_closed_trades' ? 'Computed from closed trades' : 'From agent_weights';
    return `
      <div class="agent-card">
        <h3>🤖 ${esc(a.agent_name)}</h3>
        <p>Weight <strong>${weight.toFixed(1)}%</strong></p>
        <p>Win Rate <strong>${hasComputed ? `${wr.toFixed(1)}%` : 'N/A'}</strong></p>
        <p>Predictions <strong>${predictions}</strong></p>
        <p>W / L <strong>${wins} / ${losses}</strong></p>
        <p>Net PnL <strong>${signed(net, 1)}</strong></p>
        <p>Trend <strong>${esc(a.trend || 'N/A')}</strong></p>
        <small>${esc(sourceLabel)}</small>
      </div>`;
  }).join('');
  const computable = agents.filter(a => a.win_rate !== null && a.win_rate !== undefined && Number.isFinite(Number(a.win_rate)) && num(a.total_predictions ?? a.predictions ?? 0) > 0);
  const avg = computable.length ? computable.reduce((s, a) => s + num(a.win_rate), 0) / computable.length : 0;
  setText('consensusStrength', computable.length ? `${avg.toFixed(1)}%` : '--');
}

function showTradeModalById(id) {
  const trade = [...closedTrades, ...liveTrades].find(t => t.id === id);
  if (trade) showTradeModal(trade);
}
function showTradeModal(trade) {
  const modal = $('tradeModal');
  const title = $('modalTitle');
  const body = $('modalBody');
  if (!modal || !title || !body) return;
  const pnl = pnlOf(trade);
  title.textContent = `${trade.type} ${trade.symbol} — ${displayStatus(trade)}`;
  body.innerHTML = `
    <p><strong>ID:</strong> <code>${esc(trade.id)}</code></p>
    <p><strong>Status:</strong> ${esc(displayStatus(trade))}</p>
    <p><strong>Opened:</strong> ${esc(timeText(tradeTime(trade)))}</p>
    <p><strong>Closed:</strong> ${esc(timeText(closeTime(trade)))}</p>
    <p><strong>Symbol:</strong> ${esc(trade.symbol)}</p>
    <p><strong>Type:</strong> ${esc(trade.type)}</p>
    <p><strong>Entry:</strong> ${num(trade.entry_price).toFixed(2)}</p>
    <p><strong>Current/Close:</strong> ${esc(trade.close_price ?? trade.current_price ?? '-')}</p>
    <p><strong>SL:</strong> ${esc(trade.stop_loss ?? '-')}</p>
    <p><strong>TP1:</strong> ${esc(trade.tp1 ?? '-')}</p>
    <p><strong>TP2:</strong> ${esc(trade.tp2 ?? '-')}</p>
    <p><strong>PnL:</strong> ${signed(pnl)}</p>
    <p><strong>Confidence:</strong> ${esc(trade.confidence ?? '--')}%</p>
    <p><strong>Mode:</strong> ${esc(trade.trading_mode || 'paper')}</p>`;
  modal.style.display = 'flex';
}
function closeModal() { const m = $('tradeModal'); if (m) m.style.display = 'none'; }

function toggleAutoRefresh() {
  const checkbox = $('autoRefresh');
  if (checkbox?.checked) {
    if (autoRefreshInterval) clearInterval(autoRefreshInterval);
    autoRefreshInterval = setInterval(loadDashboardData, 60000);
  } else if (autoRefreshInterval) {
    clearInterval(autoRefreshInterval);
    autoRefreshInterval = null;
  }
}

function refreshData() {
  const btn = $('refreshBtn');
  if (btn) btn.style.opacity = '.55';
  loadDashboardData().finally(() => { if (btn) btn.style.opacity = '1'; });
}

function toggleTheme() {
  document.body.classList.toggle('dark');
  const dark = document.body.classList.contains('dark');
  localStorage.setItem('theme', dark ? 'dark' : 'light');
  setText('themeBtn', dark ? '☀️' : '🌙');
  updateCharts(filteredTrades);
}

window.addEventListener('click', (event) => { if (event.target === $('tradeModal')) closeModal(); });

document.addEventListener('DOMContentLoaded', () => {
  setLang('en');
  const savedTheme = localStorage.getItem('theme');
  if (savedTheme === 'dark') { document.body.classList.add('dark'); setText('themeBtn', '☀️'); }
  loadDashboardData();
  const hash = window.location.hash.substring(1);
  if (hash && ['dashboard', 'reports', 'agents'].includes(hash)) showSection(hash);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
    if (e.key === '/' && document.activeElement?.tagName === 'BODY') { e.preventDefault(); $('searchInput')?.focus(); }
  });
});
