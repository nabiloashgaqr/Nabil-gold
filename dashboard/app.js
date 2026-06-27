// SmartSignal Enhanced Dashboard — v2.0
// Major improvements: Filters, Trades Table, Open Trades, Agents, Dark Mode, Export, Auto-refresh

const SUPABASE_URL = 'https://trsmuzekxmpqtvxdkxwe.supabase.co';
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRyc211emVreG1wcXR2eGRreHdlIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MTc2NDc5NiwiZXhwIjoyMDk3MzQwNzk2fQ.SshxIsbWpD-2sXHOSXbaLRxLGDGNcQC9G3SyDvisvso';

let currentLang = 'en';
let currentTrades = [];
let filteredTrades = [];
let charts = { daily: null, cumulative: null, session: null, instrument: null };
let autoRefreshInterval = null;

// Theme + Language
function toggleTheme() {
    const isDark = document.body.classList.toggle('dark');
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
    document.getElementById('themeBtn').textContent = isDark ? '☀️' : '🌙';
}

function setLang(lang) {
    currentLang = lang;
    document.documentElement.setAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr');
    document.documentElement.setAttribute('lang', lang);
    
    document.querySelectorAll('.lang-btn').forEach(btn => {
        btn.classList.remove('active');
        if ((lang === 'en' && btn.textContent === 'EN') || (lang === 'ar' && btn.textContent === 'عربي')) {
            btn.classList.add('active');
        }
    });
    
    document.querySelectorAll('[data-' + lang + ']').forEach(el => {
        const text = el.getAttribute('data-' + lang);
        if (text) el.textContent = text;
    });
    
    document.querySelectorAll('[data-' + lang + '-placeholder]').forEach(el => {
        el.placeholder = el.getAttribute('data-' + lang + '-placeholder');
    });
    
    if (filteredTrades.length) renderTradesTable(filteredTrades);
    
    localStorage.setItem('lang', lang);
}

// Navigation
function showSection(sectionId) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    const target = document.getElementById(sectionId);
    if (target) target.classList.add('active');
    
    document.querySelectorAll('.nav-link').forEach(link => {
        link.classList.remove('active');
        if (link.getAttribute('href') === '#' + sectionId) {
            link.classList.add('active');
        }
    });
    
    if (sectionId === 'agents') {
        updateAgentPerformance();
    }
}

function contactTelegram(plan) {
    const message = currentLang === 'ar' 
        ? `مرحباً! مهتم بباقة ${plan}` 
        : `Hi! Interested in ${plan} plan`;
    window.open(`https://t.me/Smart_Pro2026?text=${encodeURIComponent(message)}`, '_blank');
}

// Data Loading
async function loadDashboardData(useDemo = false) {
    try {
        let trades = [];
        
        if (!useDemo) {
            trades = await fetchTrades();
        }
        
        if (!trades || trades.length === 0) {
            trades = getDemoTrades();
        }
        
        currentTrades = trades;
        filteredTrades = [...trades];
        
        updateStats(trades);
        updateCharts(trades);
        renderTradesTable(trades);
        updateOpenTrades(trades);
        await loadReports();
        
        document.getElementById('lastUpdate').textContent = new Date().toLocaleString();
        document.getElementById('tradesCount').textContent = `(${trades.length})`;
        
    } catch (error) {
        console.error('Error loading data:', error);
        loadDemoData();
    }
}

async function fetchTrades() {
    try {
        const response = await fetch(`${SUPABASE_URL}/rest/v1/trades?order=created_at.desc&limit=150`, {
            headers: { 
                'apikey': SUPABASE_KEY, 
                'Authorization': `Bearer ${SUPABASE_KEY}`,
                'Content-Type': 'application/json'
            }
        });
        
        if (!response.ok) throw new Error('Supabase fetch failed');
        
        const data = await response.json();
        return data.length > 0 ? data : null;
    } catch (error) {
        console.warn('Using demo data:', error);
        return null;
    }
}

function getDemoTrades() {
    const trades = [
        { id: 1, created_at: '2026-06-27T09:15:00', closed_at: '2026-06-27T11:30:00', symbol: 'XAU/USD', type: 'BUY', entry_price: 3350.20, final_pnl: 659.6, status: 'TP2_HIT', session: 'London', confidence: 81 },
        { id: 2, created_at: '2026-06-27T14:40:00', closed_at: '2026-06-27T16:10:00', symbol: 'XAU/USD', type: 'SELL', entry_price: 3360.00, final_pnl: 420.0, status: 'TP1_HIT', session: 'New York', confidence: 76 },
        { id: 3, created_at: '2026-06-27T08:55:00', closed_at: '2026-06-27T09:20:00', symbol: 'WTI/USD', type: 'BUY', entry_price: 74.80, final_pnl: -320.0, status: 'SL_HIT', session: 'London-NY', confidence: 62 },
        { id: 4, created_at: '2026-06-26T10:20:00', closed_at: '2026-06-26T14:50:00', symbol: 'XAU/USD', type: 'BUY', entry_price: 3345.00, final_pnl: 480.0, status: 'TP2_HIT', session: 'London', confidence: 85 },
        { id: 5, created_at: '2026-06-26T15:30:00', closed_at: '2026-06-26T17:05:00', symbol: 'XAU/USD', type: 'SELL', entry_price: 3355.50, final_pnl: 380.0, status: 'TP1_HIT', session: 'New York', confidence: 79 },
        { id: 6, created_at: '2026-06-26T07:10:00', closed_at: '2026-06-26T11:45:00', symbol: 'WTI/USD', type: 'SELL', entry_price: 76.20, final_pnl: 280.0, status: 'TP1_HIT', session: 'London', confidence: 71 },
        { id: 7, created_at: '2026-06-25T12:00:00', closed_at: '2026-06-25T16:40:00', symbol: 'XAU/USD', type: 'BUY', entry_price: 3340.80, final_pnl: 520.0, status: 'TP2_HIT', session: 'London-NY', confidence: 88 },
        { id: 8, created_at: '2026-06-25T03:45:00', closed_at: '2026-06-25T05:10:00', symbol: 'XAU/USD', type: 'BUY', entry_price: 3335.20, final_pnl: 450.0, status: 'TP1_HIT', session: 'Asian', confidence: 65 },
        { id: 9, created_at: '2026-06-24T16:25:00', closed_at: '2026-06-24T17:50:00', symbol: 'XAU/USD', type: 'SELL', entry_price: 3360.00, final_pnl: -280.0, status: 'SL_HIT', session: 'New York', confidence: 58 },
        { id: 10, created_at: '2026-06-24T09:00:00', closed_at: '2026-06-24T14:20:00', symbol: 'XAU/USD', type: 'BUY', entry_price: 3330.00, final_pnl: 550.0, status: 'TP2_HIT', session: 'London', confidence: 83 },
        { id: 11, created_at: '2026-06-23T11:15:00', closed_at: '2026-06-23T11:55:00', symbol: 'WTI/USD', type: 'BUY', entry_price: 74.50, final_pnl: 0, status: 'BE_HIT', session: 'London', confidence: 70 },
        { id: 12, created_at: '2026-06-23T18:00:00', closed_at: '2026-06-23T21:30:00', symbol: 'XAU/USD', type: 'SELL', entry_price: 3370.00, final_pnl: 620.0, status: 'TP2_HIT', session: 'New York', confidence: 91 },
        { id: 13, created_at: '2026-06-27T15:30:00', symbol: 'XAU/USD', type: 'BUY', entry_price: 3348.50, current_pnl: 185, status: 'OPEN', session: 'New York', confidence: 77 },
        { id: 14, created_at: '2026-06-27T10:05:00', symbol: 'WTI/USD', type: 'SELL', entry_price: 75.10, current_pnl: -95, status: 'PARTIAL', session: 'London', confidence: 68 },
    ];
    return trades;
}

function loadDemoData() {
    const trades = getDemoTrades();
    currentTrades = trades;
    filteredTrades = [...trades];
    updateStats(trades);
    updateCharts(trades);
    renderTradesTable(trades);
    updateOpenTrades(trades);
    loadDemoReports();
    document.getElementById('lastUpdate').textContent = new Date().toLocaleString();
    document.getElementById('tradesCount').textContent = `(${trades.length})`;
}

// Stats
function updateStats(trades) {
    if (!trades || trades.length === 0) return;
    
    const total = trades.length;
    const closed = trades.filter(t => !['OPEN', 'PARTIAL'].includes((t.status || '').toUpperCase()));
    
    const wins = closed.filter(t => (t.final_pnl || 0) > 0);
    const losses = closed.filter(t => (t.final_pnl || 0) < 0);
    
    const netPnl = trades.reduce((sum, t) => sum + (t.final_pnl || t.current_pnl || 0), 0);
    const winRate = closed.length > 0 ? ((wins.length / closed.length) * 100) : 0;
    
    const grossProfit = wins.reduce((sum, t) => sum + (t.final_pnl || 0), 0);
    const grossLoss = Math.abs(losses.reduce((sum, t) => sum + (t.final_pnl || 0), 0));
    let profitFactor = '--';
    if (grossLoss > 0) profitFactor = (grossProfit / grossLoss).toFixed(2);
    else if (grossProfit > 0) profitFactor = '∞';
    
    const pnlValues = trades.map(t => t.final_pnl || t.current_pnl || 0);
    const bestTrade = Math.max(...pnlValues);
    const worstTrade = Math.min(...pnlValues);
    
    const expectancy = closed.length > 0 ? (netPnl / closed.length).toFixed(0) : 0;
    const avgTrade = closed.length > 0 ? (netPnl / closed.length).toFixed(0) : 0;
    
    document.getElementById('totalTrades').textContent = total;
    document.getElementById('winRate').textContent = `${winRate.toFixed(1)}%`;
    document.getElementById('netPoints').textContent = `${netPnl > 0 ? '+' : ''}${netPnl.toFixed(0)}`;
    document.getElementById('profitFactor').textContent = profitFactor;
    document.getElementById('bestTrade').textContent = `+${bestTrade.toFixed(0)}`;
    document.getElementById('worstTrade').textContent = worstTrade.toFixed(0);
    document.getElementById('expectancy').textContent = `${expectancy > 0 ? '+' : ''}${expectancy}`;
    document.getElementById('avgTrade').textContent = `${avgTrade > 0 ? '+' : ''}${avgTrade}`;
    
    const netEl = document.getElementById('netPoints');
    netEl.style.color = netPnl >= 0 ? '#2b8a3e' : '#c92a2a';
    
    const bar = document.getElementById('winRateBar');
    if (bar) bar.style.width = `${Math.min(winRate, 100)}%`;
}

// Charts
function updateCharts(trades) {
    if (!trades || trades.length === 0) return;
    updateDailyPnlChart(trades);
    updateCumulativePnlChart(trades);
    updateSessionChart(trades);
    updateInstrumentChart(trades);
}

function updateDailyPnlChart(trades) {
    const dailyPnl = {};
    trades.forEach(t => {
        const date = (t.created_at || '').substring(0, 10);
        if (date) {
            const pnl = t.final_pnl || t.current_pnl || 0;
            dailyPnl[date] = (dailyPnl[date] || 0) + pnl;
        }
    });
    
    const labels = Object.keys(dailyPnl).sort().slice(-14);
    const data = labels.map(d => dailyPnl[d]);
    const colors = data.map(v => v >= 0 ? '#2b8a3e' : '#c92a2a');
    
    if (charts.daily) charts.daily.destroy();
    const ctx = document.getElementById('dailyPnlChart');
    if (!ctx) return;
    
    charts.daily = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: { labels: labels.map(d => d.substring(5)), datasets: [{ data, backgroundColor: colors, borderRadius: 4, borderWidth: 0 }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { grid: { color: '#e9ecef' }, ticks: { color: '#6c757d' } },
                x: { grid: { display: false }, ticks: { color: '#6c757d' } }
            }
        }
    });
}

function updateCumulativePnlChart(trades) {
    const sorted = [...trades].sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
    let cumulative = 0;
    const data = sorted.map(t => { cumulative += (t.final_pnl || t.current_pnl || 0); return cumulative; });
    const labels = sorted.map(t => (t.created_at || '').substring(5, 10));
    
    if (charts.cumulative) charts.cumulative.destroy();
    const ctx = document.getElementById('cumulativePnlChart');
    if (!ctx) return;
    
    charts.cumulative = new Chart(ctx.getContext('2d'), {
        type: 'line',
        data: { labels, datasets: [{ data, borderColor: '#1971c2', backgroundColor: 'rgba(25, 113, 194, 0.12)', fill: true, tension: 0.35, pointRadius: 3 }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { grid: { color: '#e9ecef' } }, x: { grid: { display: false } } }
        }
    });
}

function updateSessionChart(trades) {
    const sessionPnl = {};
    trades.forEach(t => {
        const session = (t.session || 'Unknown').split('(')[0].trim();
        const pnl = t.final_pnl || t.current_pnl || 0;
        sessionPnl[session] = (sessionPnl[session] || 0) + pnl;
    });
    
    const labels = Object.keys(sessionPnl);
    const data = labels.map(s => sessionPnl[s]);
    const colors = data.map(v => v >= 0 ? '#2b8a3e' : '#c92a2a');
    
    if (charts.session) charts.session.destroy();
    const ctx = document.getElementById('sessionChart');
    if (!ctx) return;
    
    charts.session = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: { labels, datasets: [{ data, backgroundColor: colors, borderRadius: 4 }] },
        options: { responsive: true, maintainAspectRatio: false, indexAxis: 'y', plugins: { legend: { display: false } } }
    });
}

function updateInstrumentChart(trades) {
    const instrumentPnl = {};
    trades.forEach(t => {
        const symbol = t.symbol || 'XAU/USD';
        const pnl = Math.abs(t.final_pnl || t.current_pnl || 0);
        instrumentPnl[symbol] = (instrumentPnl[symbol] || 0) + pnl;
    });
    
    const labels = Object.keys(instrumentPnl);
    const data = labels.map(s => instrumentPnl[s]);
    
    if (charts.instrument) charts.instrument.destroy();
    const ctx = document.getElementById('instrumentChart');
    if (!ctx) return;
    
    charts.instrument = new Chart(ctx.getContext('2d'), {
        type: 'doughnut',
        data: { labels, datasets: [{ data, backgroundColor: ['#e67700', '#1971c2', '#2b8a3e', '#c92a2a'], borderWidth: 0 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
    });
}

// Trades Table
function renderTradesTable(trades) {
    const tbody = document.getElementById('tradesBody');
    if (!tbody) return;
    
    tbody.innerHTML = '';
    
    if (!trades || trades.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding: 40px; color:#6c757d;">No trades found</td></tr>`;
        return;
    }
    
    trades.slice(0, 30).forEach(trade => {
        const row = document.createElement('tr');
        row.style.cursor = 'pointer';
        row.onclick = () => showTradeModal(trade);
        
        const date = (trade.created_at || '').substring(0, 10);
        const pnl = trade.final_pnl || trade.current_pnl || 0;
        const pnlClass = pnl > 0 ? 'pnl-positive' : pnl < 0 ? 'pnl-negative' : '';
        
        const status = (trade.status || 'UNKNOWN').toUpperCase();
        let statusClass = 'neutral';
        if (['TP2_HIT', 'TP1_HIT'].includes(status)) statusClass = 'win';
        else if (['SL_HIT'].includes(status)) statusClass = 'loss';
        else if (['OPEN', 'PARTIAL'].includes(status)) statusClass = 'open';
        
        row.innerHTML = `
            <td>${date}</td>
            <td><strong>${trade.symbol || 'XAU/USD'}</strong></td>
            <td><span class="badge ${trade.type === 'BUY' ? 'buy' : 'sell'}">${trade.type || 'N/A'}</span></td>
            <td>${parseFloat(trade.entry_price || 0).toFixed(2)}</td>
            <td class="${pnlClass}"><strong>${pnl > 0 ? '+' : ''}${pnl.toFixed(0)}</strong></td>
            <td><span class="badge ${statusClass}">${status}</span></td>
            <td>${trade.session || '-'}</td>
            <td><button class="btn btn-sm" onclick="event.stopImmediatePropagation(); showTradeModal(${JSON.stringify(trade).replace(/"/g, '&quot;')})">Details</button></td>
        `;
        tbody.appendChild(row);
    });
}

function applyFilters() {
    const symbol = document.getElementById('filterSymbol').value;
    const session = document.getElementById('filterSession').value;
    const result = document.getElementById('filterResult').value;
    const dateFrom = document.getElementById('filterDateFrom').value;
    const dateTo = document.getElementById('filterDateTo').value;
    const search = (document.getElementById('searchInput')?.value || '').toLowerCase();
    
    filteredTrades = currentTrades.filter(trade => {
        let match = true;
        
        if (symbol && trade.symbol !== symbol) match = false;
        if (session && !(trade.session || '').includes(session)) match = false;
        
        if (result === 'win') {
            if ((trade.final_pnl || trade.current_pnl || 0) <= 0) match = false;
        } else if (result === 'loss') {
            if ((trade.final_pnl || trade.current_pnl || 0) >= 0) match = false;
        }
        
        if (dateFrom) {
            const tDate = (trade.created_at || '').substring(0, 10);
            if (tDate < dateFrom) match = false;
        }
        if (dateTo) {
            const tDate = (trade.created_at || '').substring(0, 10);
            if (tDate > dateTo) match = false;
        }
        
        if (search) {
            const text = `${trade.symbol} ${trade.type} ${trade.status} ${trade.session}`.toLowerCase();
            if (!text.includes(search)) match = false;
        }
        
        return match;
    });
    
    renderTradesTable(filteredTrades);
}

function clearFilters() {
    document.getElementById('filterSymbol').value = '';
    document.getElementById('filterSession').value = '';
    document.getElementById('filterResult').value = '';
    document.getElementById('filterDateFrom').value = '';
    document.getElementById('filterDateTo').value = '';
    const search = document.getElementById('searchInput');
    if (search) search.value = '';
    
    filteredTrades = [...currentTrades];
    renderTradesTable(filteredTrades);
    updateStats(currentTrades);
    updateCharts(currentTrades);
}

function exportToCSV() {
    if (!filteredTrades.length) return;
    
    const headers = ['Date', 'Symbol', 'Type', 'Entry', 'PnL', 'Status', 'Session', 'Confidence'];
    const rows = filteredTrades.map(t => [
        (t.created_at || '').substring(0, 10),
        t.symbol,
        t.type,
        t.entry_price,
        t.final_pnl || t.current_pnl || 0,
        t.status,
        t.session,
        t.confidence || ''
    ]);
    
    let csv = headers.join(',') + '\n';
    rows.forEach(row => {
        csv += row.map(cell => `"${cell}"`).join(',') + '\n';
    });
    
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `smartsignal_trades_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}

// Open Trades
function updateOpenTrades(trades) {
    const openTrades = trades.filter(t => {
        const status = (t.status || '').toUpperCase();
        return ['OPEN', 'PARTIAL', 'TP1_HIT'].includes(status);
    });
    
    const countEl = document.getElementById('openTradesCount');
    const pnlEl = document.getElementById('unrealizedPnl');
    
    if (countEl) countEl.textContent = openTrades.length;
    
    const unrealized = openTrades.reduce((sum, t) => sum + (t.current_pnl || 0), 0);
    if (pnlEl) {
        pnlEl.textContent = `${unrealized > 0 ? '+' : ''}${unrealized.toFixed(0)}`;
        pnlEl.style.color = unrealized >= 0 ? '#2b8a3e' : '#c92a2a';
    }
    
    const tbody = document.getElementById('openTradesBody');
    if (!tbody) return;
    
    tbody.innerHTML = '';
    
    if (openTrades.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:30px; color:#adb5bd;">No open trades currently</td></tr>`;
        return;
    }
    
    openTrades.forEach(trade => {
        const row = document.createElement('tr');
        const pnl = trade.current_pnl || 0;
        const created = new Date(trade.created_at);
        const hours = Math.floor((new Date() - created) / (1000 * 60 * 60));
        
        row.innerHTML = `
            <td>${(trade.created_at || '').substring(0, 10)}</td>
            <td><strong>${trade.symbol}</strong></td>
            <td><span class="badge ${trade.type === 'BUY' ? 'buy' : 'sell'}">${trade.type}</span></td>
            <td>${parseFloat(trade.entry_price).toFixed(2)}</td>
            <td class="${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}"><strong>${pnl > 0 ? '+' : ''}${pnl}</strong></td>
            <td><span class="badge open">${trade.status}</span></td>
            <td>${hours}h ago</td>
        `;
        row.onclick = () => showTradeModal(trade);
        tbody.appendChild(row);
    });
}

// Agents Performance
function updateAgentPerformance() {
    const agents = {
        technical: { winrate: 68.2, signals: 87, conf: 74, pnl: 2840 },
        smc: { winrate: 72.8, signals: 74, conf: 81, pnl: 3120 },
        classical: { winrate: 61.4, signals: 65, conf: 69, pnl: 1980 },
        price_action: { winrate: 75.1, signals: 51, conf: 79, pnl: 2410 },
        multitimeframe: { winrate: 65.9, signals: 69, conf: 72, pnl: 2150 }
    };
    
    Object.keys(agents).forEach(key => {
        const data = agents[key];
        const winEl = document.getElementById(`agent-${key}-winrate`);
        const sigEl = document.getElementById(`agent-${key}-signals`);
        const confEl = document.getElementById(`agent-${key}-conf`);
        const pnlEl = document.getElementById(`agent-${key}-pnl`);
        
        if (winEl) winEl.textContent = `${data.winrate}%`;
        if (sigEl) sigEl.textContent = data.signals;
        if (confEl) confEl.textContent = `${data.conf}%`;
        if (pnlEl) {
            pnlEl.textContent = `${data.pnl > 0 ? '+' : ''}${data.pnl}`;
            pnlEl.style.color = '#2b8a3e';
        }
    });
    
    const consensusEl = document.getElementById('consensusStrength');
    if (consensusEl) consensusEl.textContent = '78.4%';
}

// Reports
async function loadReports() {
    try {
        const daily = await fetchReport('daily');
        const weekly = await fetchReport('weekly');
        
        const dailyEl = document.getElementById('dailyReport');
        const weeklyEl = document.getElementById('weeklyReport');
        
        if (dailyEl) dailyEl.textContent = daily || getDefaultDailyReport();
        if (weeklyEl) weeklyEl.textContent = weekly || getDefaultWeeklyReport();
    } catch (e) {
        loadDemoReports();
    }
}

async function fetchReport(type) {
    try {
        const table = type === 'daily' ? 'daily_reports' : 'weekly_reports';
        const res = await fetch(`${SUPABASE_URL}/rest/v1/${table}?order=created_at.desc&limit=1`, {
            headers: { 'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}` }
        });
        if (!res.ok) return null;
        const data = await res.json();
        return data[0]?.report_text || null;
    } catch {
        return null;
    }
}

function getDefaultDailyReport() {
    return `SmartSignal — Daily Report
Period: 2026-06-27
────────────────────

SUMMARY
  Total: 3 trades
  Wins: 2  |  Losses: 1
  Win Rate: 66.7%

PERFORMANCE
  Net: +759 pts ($75.9)
  Profit Factor: 2.71
  Best: +659  |  Worst: -320

TRADE DETAILS
  [+] BUY XAU/USD | Entry 3350.20 | +659 pts | TP2 HIT
  [+] SELL XAU/USD | Entry 3360.00 | +420 pts | TP1 HIT
  [-] BUY WTI/USD | Entry 74.80 | -320 pts | SL HIT

RISK GRADE: A+`;
}

function getDefaultWeeklyReport() {
    return `SmartSignal — Weekly Report
Week: 2026-06-20 → 2026-06-27
────────────────────

SUMMARY
  Total: 12 trades
  Wins: 9  |  Losses: 2  |  BE: 1
  Win Rate: 75.0%

PERFORMANCE
  Net: +3759 pts ($375.9)
  Profit Factor: 4.68
  Expectancy: +313 pts/trade

BY INSTRUMENT
  [+] XAU/USD: 9 trades | Net +4079
  [-] WTI/USD: 3 trades | Net -320

RISK GRADE: A+`;
}

function loadDemoReports() {
    const dailyEl = document.getElementById('dailyReport');
    const weeklyEl = document.getElementById('weeklyReport');
    
    if (dailyEl) dailyEl.textContent = getDefaultDailyReport();
    if (weeklyEl) weeklyEl.textContent = getDefaultWeeklyReport();
}

// Modal
function showTradeModal(trade) {
    const modal = document.getElementById('tradeModal');
    const title = document.getElementById('modalTitle');
    const body = document.getElementById('modalBody');
    
    const pnl = trade.final_pnl || trade.current_pnl || 0;
    const isOpen = ['OPEN', 'PARTIAL'].includes((trade.status || '').toUpperCase());
    
    title.textContent = `${trade.type} ${trade.symbol} — ${trade.status}`;
    
    body.innerHTML = `
        <div class="trade-detail-grid">
            <div><strong>Date:</strong> ${trade.created_at}</div>
            <div><strong>Symbol:</strong> ${trade.symbol}</div>
            <div><strong>Type:</strong> <span class="badge ${trade.type === 'BUY' ? 'buy' : 'sell'}">${trade.type}</span></div>
            <div><strong>Entry Price:</strong> ${parseFloat(trade.entry_price).toFixed(2)}</div>
            <div><strong>Status:</strong> <span class="badge ${isOpen ? 'open' : pnl > 0 ? 'win' : 'loss'}">${trade.status}</span></div>
            <div><strong>PnL:</strong> <span class="${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${pnl > 0 ? '+' : ''}${pnl}</span></div>
            <div><strong>Session:</strong> ${trade.session || 'N/A'}</div>
            <div><strong>Confidence:</strong> ${trade.confidence || '--'}%</div>
        </div>
        
        <div style="margin-top: 16px;">
            <h4 style="margin-bottom: 8px;">Additional Info</h4>
            <p style="color: #6c757d; font-size: 0.9rem;">
                ${isOpen ? 'Trade is still active. Management rules are being applied.' : 
                'Trade has been closed according to the risk management rules.'}
            </p>
        </div>
    `;
    
    modal.style.display = 'flex';
}

function closeModal() {
    document.getElementById('tradeModal').style.display = 'none';
}

// Auto Refresh + Init
function toggleAutoRefresh() {
    const checkbox = document.getElementById('autoRefresh');
    
    if (checkbox.checked) {
        if (autoRefreshInterval) clearInterval(autoRefreshInterval);
        autoRefreshInterval = setInterval(() => {
            loadDashboardData();
        }, 60000);
    } else {
        if (autoRefreshInterval) {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
        }
    }
}

function refreshData() {
    const btns = document.querySelectorAll('.btn-refresh');
    btns.forEach(b => b.style.opacity = '0.5');
    
    loadDashboardData().finally(() => {
        btns.forEach(b => b.style.opacity = '1');
    });
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme === 'dark') {
        document.body.classList.add('dark');
        const btn = document.getElementById('themeBtn');
        if (btn) btn.textContent = '☀️';
    }
    
    const savedLang = localStorage.getItem('lang');
    if (savedLang) {
        setLang(savedLang);
    } else if ((navigator.language || '').startsWith('ar')) {
        setLang('ar');
    }
    
    loadDashboardData();
    
    const hash = window.location.hash.substring(1);
    if (hash && ['dashboard', 'open-trades', 'agents', 'pricing'].includes(hash)) {
        showSection(hash);
    }
    
    document.addEventListener('keydown', (e) => {
        if (e.key === '/' && document.activeElement.tagName === 'BODY') {
            e.preventDefault();
            const search = document.getElementById('searchInput');
            if (search) search.focus();
        }
        if (e.metaKey && e.key === 'r') {
            e.preventDefault();
            refreshData();
        }
    });
    
    const autoCheck = document.getElementById('autoRefresh');
    if (autoCheck) autoCheck.checked = false;
});