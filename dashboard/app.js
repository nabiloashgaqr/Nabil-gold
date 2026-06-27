// SmartSignal Live Dashboard
// Secure version: no Supabase keys in frontend. Data comes from /api/dashboard.

const API_URL = (window.SMARTSIGNAL_API_URL || '/api/dashboard');
const OUTCOME_STATUSES = new Set(['TP1_HIT', 'TP2_HIT', 'SL_HIT']);
const LIVE_STATUSES = new Set([]);

let currentLang = 'ar';
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
        noClosed: 'لا توجد صفقات TP1 / TP2 / SL حسب الفلتر الحالي',
        noLive: 'لا توجد صفقات حية أو TP1 حالياً',
        noDaily: 'لا يوجد تقرير يومي بعد.',
        noWeekly: 'لا يوجد تقرير أسبوعي بعد.',
        noReports: 'لا توجد تقارير محفوظة',
        loading: 'جاري التحميل...',
        details: 'تفاصيل',
    },
    en: {
        api404: 'Dashboard API is not deployed: /api/dashboard returns 404. If Vercel Root Directory is dashboard, upload dashboard/api/dashboard.js and redeploy.',
        loadError: 'Failed to load data',
        noClosed: 'No TP1 / TP2 / SL trades match the current filter',
        noLive: 'No live or TP1 trades right now',
        noDaily: 'No daily report yet.',
        noWeekly: 'No weekly report yet.',
        noReports: 'No saved reports',
        loading: 'Loading...',
        details: 'Details',
    }
};
function tr(key) { return (I18N[currentLang] && I18N[currentLang][key]) || I18N.ar[key] || key; }
function setLang(lang) {
    currentLang = lang === 'en' ? 'en' : 'ar';
    document.documentElement.lang = currentLang;
    document.documentElement.dir = currentLang === 'ar' ? 'rtl' : 'ltr';
    localStorage.setItem('lang', currentLang);
    document.querySelectorAll('[data-ar][data-en]').forEach(el => { el.textContent = el.getAttribute(`data-${currentLang}`); });
    document.querySelectorAll('.lang-btn').forEach(btn => btn.classList.remove('active'));
    const active = currentLang === 'ar' ? $('langAr') : $('langEn');
    if (active) active.classList.add('active');
    renderTradesTable(filteredTrades);
    updateOpenTrades(liveTrades);
    if (dashboardPayload) renderReports(dashboardPayload);
}


function $(id) { return document.getElementById(id); }
function setText(id, value) { const el = $(id); if (el) el.textContent = value; }
function setHTML(id, value) { const el = $(id); if (el) el.innerHTML = value; }
function num(value, fallback = 0) { const n = Number(value); return Number.isFinite(n) ? n : fallback; }
function signed(value, decimals = 0) { const n = num(value); return `${n > 0 ? '+' : ''}${n.toFixed(decimals)}`; }
function esc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch]));
}
function dateText(value) { return value ? String(value).substring(0, 10) : '-'; }
function timeText(value) { return value ? String(value).replace('T', ' ').substring(0, 19) : '-'; }
function pnlOf(t) { return num(t.pnl ?? t.final_pnl ?? t.current_pnl_points ?? t.current_pnl ?? 0); }
function tradeTime(t) { return t.created_at || t.entry_time || t.opened_at || t.updated_at || ''; }
function closeTime(t) { return t.closed_at || t.close_time || ''; }
function isLiveStatus(status) { return false; }
function isClosedStatus(status) { return OUTCOME_STATUSES.has(String(status || '').toUpperCase()); }

function normalizeTrade(t) {
    const status = String(t.status || 'UNKNOWN').toUpperCase();
    return {
        ...t,
        id: t.id || '',
        symbol: t.symbol || 'XAU/USD',
        type: String(t.type || t.side || t.trade_type || '').toUpperCase(),
        status,
        pnl: pnlOf(t),
        created_at: tradeTime(t),
        closed_at: closeTime(t),
    };
}

function toggleTheme() {
    const isDark = document.body.classList.toggle('dark');
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
    setText('themeBtn', isDark ? '☀️' : '🌙');
    updateCharts(filteredTrades);
}


function showSection(sectionId) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    const target = $(sectionId);
    if (target) target.classList.add('active');
    document.querySelectorAll('.nav-link').forEach(link => {
        link.classList.toggle('active', link.getAttribute('href') === `#${sectionId}`);
    });
    if (sectionId === 'live-trades') sectionId = 'dashboard';
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
        updateOpenTrades(liveTrades);
        renderReports(payload);
        updateAgentPerformance();

        setText('lastUpdate', new Date(payload.generatedAt || Date.now()).toLocaleString('ar'));
        setText('dataSource', payload.source || 'api');
    } catch (error) {
        console.error('Dashboard load failed:', error);
        closedTrades = [];
        liveTrades = [];
        filteredTrades = [];
        updateStats([], []);
        updateCharts([]);
        renderTradesTable([]);
        updateOpenTrades([]);
        renderReports({ dailyReports: [], weeklyReports: [] });
        setText('dataSource', 'خطأ');
        setText('lastUpdate', new Date().toLocaleString('ar'));
        setError(`${tr('loadError')}: ${error.message.includes('404') ? tr('api404') : error.message}`);
    }
}

function setError(message) {
    const el = $('errorBox');
    if (!el) return;
    if (!message) { el.style.display = 'none'; el.textContent = ''; return; }
    el.style.display = 'block';
    el.textContent = message;
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
    const losses = trades.filter(t => pnlOf(t) < 0 || t.status === 'SL_HIT');
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
    setText('liveCount', trades.length);
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

function updateCharts(trades) {
    updateDailyPnlChart(trades);
    updateCumulativePnlChart(trades);
    updateSessionChart(trades);
    updateInstrumentChart(trades);
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
        const d = dateText(closeTime(t) || tradeTime(t));
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
        data: {
            labels: labels.map(d => d.substring(5)),
            datasets: [{
                label: 'Daily PnL',
                data,
                backgroundColor: data.map(v => v >= 0 ? gradient : 'rgba(220,38,38,.72)'),
                hoverBackgroundColor: data.map(v => v >= 0 ? 'rgba(22,163,74,.82)' : 'rgba(220,38,38,.88)'),
                borderRadius: 10,
                borderSkipped: false,
                maxBarThickness: 46,
            }]
        },
        options: chartOptions({
            plugins: { ...chartOptions().plugins, tooltip: { ...chartOptions().plugins.tooltip } },
            scales: {
                y: { ...chartOptions().scales.y, grace: '15%' },
                x: chartOptions().scales.x
            }
        }),
    });
}

function updateCumulativePnlChart(trades) {
    const sorted = [...trades].sort((a, b) => String(closeTime(a) || tradeTime(a)).localeCompare(String(closeTime(b) || tradeTime(b))));
    let cumulative = 0;
    const data = sorted.map(t => { cumulative += pnlOf(t); return cumulative; });
    const labels = sorted.map(t => dateText(closeTime(t) || tradeTime(t)).substring(5));
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
    const grouped = {};
    trades.forEach(t => {
        const session = String(t.session || t.session_name || 'Unknown').split('(')[0].trim();
        grouped[session] = (grouped[session] || 0) + pnlOf(t);
    });
    const labels = Object.keys(grouped);
    const data = labels.map(k => grouped[k]);
    const ctx = $('sessionChart');
    setChartEmpty('sessionEmpty', !data.length);
    if (!ctx || typeof Chart === 'undefined') return;
    safeDestroyChart('session');
    if (!data.length) return;
    charts.session = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: { labels, datasets: [{ data, backgroundColor: data.map(v => v >= 0 ? '#2b8a3e' : '#c92a2a'), borderRadius: 5 }] },
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

function renderTradesTable(trades) {
    const tbody = $('tradesBody');
    if (!tbody) return;
    if (!trades.length) {
        tbody.innerHTML = `<tr><td colspan="10" class="empty">${tr('noClosed')}</td></tr>`;
        return;
    }
    tbody.innerHTML = trades.slice(0, 120).map(trade => {
        const pnl = pnlOf(trade);
        const statusClass = pnl > 0 || trade.status === 'TP2_HIT' ? 'win' : pnl < 0 || trade.status === 'SL_HIT' ? 'loss' : 'neutral';
        return `<tr onclick='showTradeModalById(${JSON.stringify(trade.id)})'>
            <td>${esc(dateText(tradeTime(trade)))}</td>
            <td>${esc(dateText(closeTime(trade)))}</td>
            <td><strong>${esc(trade.symbol)}</strong></td>
            <td><span class="badge ${trade.type === 'BUY' ? 'buy' : trade.type === 'SELL' ? 'sell' : 'neutral'}">${esc(trade.type || 'N/A')}</span></td>
            <td>${num(trade.entry_price).toFixed(2)}</td>
            <td>${trade.close_price != null ? num(trade.close_price).toFixed(2) : '-'}</td>
            <td class="${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}"><strong>${signed(pnl)}</strong></td>
            <td><span class="badge ${statusClass}">${esc(trade.status)}</span></td>
            <td>${esc(trade.confidence ?? '--')}%</td>
            <td><button class="btn btn-sm" onclick="event.stopPropagation(); showTradeModalById(${JSON.stringify(trade.id)})">${tr('details')}</button></td>
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
        const tDate = dateText(closeTime(trade) || tradeTime(trade));
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
    const csv = [headers, ...rows].map(row => row.map(cell => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `smartsignal_closed_trades_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}

function updateOpenTrades(_trades) {
    // Live trades section removed by request. Dashboard shows TP1 / TP2 / SL outcomes only.
}


function renderReports(payload) {
    const daily = payload.dailyReports || [];
    const weekly = payload.weeklyReports || [];
    const latestDaily = daily[0];
    const latestWeekly = weekly[0];
    setText('dailyReport', latestDaily ? (latestDaily.report_text || 'Daily report has no text') : tr('noDaily'));
    setText('weeklyReport', latestWeekly ? (latestWeekly.report_text || JSON.stringify(latestWeekly.stats_json || {}, null, 2)) : tr('noWeekly'));
    const rows = [];
    daily.forEach(r => rows.push(`<tr><td>Daily</td><td>${esc(r.report_date || '-')}</td><td>${esc(timeText(r.created_at))}</td><td>-</td></tr>`));
    weekly.forEach(r => rows.push(`<tr><td>Weekly</td><td>${esc(r.week_start || '-') } → ${esc(r.week_end || '-')}</td><td>${esc(timeText(r.created_at))}</td><td>${esc(r.status || '-')}</td></tr>`));
    setHTML('reportsBody', rows.length ? rows.join('') : `<tr><td colspan="4" class="empty">${tr('noReports')}</td></tr>`);
}

function updateAgentPerformance() {
    const agents = (dashboardPayload?.agentPerformance || dashboardPayload?.agentWeights || []);
    const grid = $('agentsGrid');
    if (!grid) return;
    if (!agents.length) {
        grid.innerHTML = ['technical', 'smc', 'classical', 'price_action', 'multitimeframe'].map(name => `
            <div class="agent-card"><div class="agent-header"><span class="agent-icon">🤖</span><span class="agent-name">${name}</span></div><div class="muted">No performance data yet</div></div>
        `).join('');
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
        const sourceLabel = a.source === 'computed_from_closed_trades'
            ? (currentLang === 'ar' ? 'محسوب من الصفقات المغلقة' : 'Computed from closed trades')
            : (currentLang === 'ar' ? 'من جدول agent_weights' : 'From agent_weights');
        return `<div class="agent-card">
            <div class="agent-header"><span class="agent-icon">🤖</span><span class="agent-name">${esc(a.agent_name)}</span></div>
            <div class="agent-stats">
                <div class="agent-metric"><span>Weight</span><strong>${weight.toFixed(1)}%</strong></div>
                <div class="agent-metric"><span>Win Rate</span><strong>${hasComputed ? `${wr.toFixed(1)}%` : 'N/A'}</strong></div>
                <div class="agent-metric"><span>Predictions</span><strong>${predictions}</strong></div>
                <div class="agent-metric"><span>W / L</span><strong>${wins} / ${losses}</strong></div>
                <div class="agent-metric"><span>Net PnL</span><strong class="${net >= 0 ? 'pnl-positive' : 'pnl-negative'}">${signed(net, 1)}</strong></div>
                <div class="agent-metric"><span>Trend</span><strong>${esc(a.trend || 'N/A')}</strong></div>
            </div>
            <div class="agent-bar"><div class="agent-bar-fill" style="width:${hasComputed ? Math.min(wr,100) : 0}%"></div></div>
            <div class="agent-source">${sourceLabel}</div>
        </div>`;
    }).join('');
    const computable = agents.filter(a => a.win_rate !== null && a.win_rate !== undefined && Number.isFinite(Number(a.win_rate)) && num(a.total_predictions ?? a.predictions ?? 0) > 0);
    const avg = computable.length ? computable.reduce((s, a) => s + num(a.win_rate), 0) / computable.length : 0;
    setText('consensusStrength', computable.length ? `${avg.toFixed(1)}%` : '--');
}

function showTradeModalById(id, live = false) {
    const trade = (live ? liveTrades : [...closedTrades, ...liveTrades]).find(t => t.id === id);
    if (trade) showTradeModal(trade);
}

function showTradeModal(trade) {
    const modal = $('tradeModal');
    const title = $('modalTitle');
    const body = $('modalBody');
    if (!modal || !title || !body) return;
    const pnl = pnlOf(trade);
    const live = isLiveStatus(trade.status);
    title.textContent = `${trade.type} ${trade.symbol} — ${trade.status}`;
    body.innerHTML = `<div class="trade-detail-grid">
        <div><strong>ID:</strong> <code>${esc(trade.id)}</code></div>
        <div><strong>الحالة:</strong> <span class="badge ${live ? 'open' : pnl >= 0 ? 'win' : 'loss'}">${esc(trade.status)}</span></div>
        <div><strong>تاريخ الدخول:</strong> ${esc(timeText(tradeTime(trade)))}</div>
        <div><strong>تاريخ الإغلاق:</strong> ${esc(timeText(closeTime(trade)))}</div>
        <div><strong>الرمز:</strong> ${esc(trade.symbol)}</div>
        <div><strong>النوع:</strong> <span class="badge ${trade.type === 'BUY' ? 'buy' : 'sell'}">${esc(trade.type)}</span></div>
        <div><strong>Entry:</strong> ${num(trade.entry_price).toFixed(2)}</div>
        <div><strong>Current/Close:</strong> ${trade.close_price ?? trade.current_price ?? '-'}</div>
        <div><strong>SL:</strong> ${trade.stop_loss ?? '-'}</div>
        <div><strong>TP1:</strong> ${trade.tp1 ?? '-'}</div>
        <div><strong>TP2:</strong> ${trade.tp2 ?? '-'}</div>
        <div><strong>PnL:</strong> <span class="${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${signed(pnl)}</span></div>
        <div><strong>Confidence:</strong> ${esc(trade.confidence ?? '--')}%</div>
        <div><strong>Mode:</strong> ${esc(trade.trading_mode || 'paper')}</div>
    </div>`;
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

window.addEventListener('click', (event) => { if (event.target === $('tradeModal')) closeModal(); });

document.addEventListener('DOMContentLoaded', () => {
    const savedLang = localStorage.getItem('lang') || 'ar';
    setLang(savedLang);
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
