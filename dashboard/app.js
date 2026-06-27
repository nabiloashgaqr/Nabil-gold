// ═══════════════════════════════════════════════════════════
// SmartSignal Dashboard — Main Application
// ═══════════════════════════════════════════════════════════

// Supabase Configuration
const SUPABASE_URL = 'https://trsmuzekxmpqtvxdkxwe.supabase.co';
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRyc211emVreG1wcXR2eGRreHdlIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MTc2NDc5NiwiZXhwIjoyMDk3MzQwNzk2fQ.SshxIsbWpD-2sXHOSXbaLRxLGDGNcQC9G3SyDvisvso';

// Chart instances
let dailyPnlChart = null;
let cumulativePnlChart = null;
let sessionChart = null;
let instrumentChart = null;

// ═══════════════════════════════════════════════════════════
// Navigation
// ═══════════════════════════════════════════════════════════

function showSection(sectionId) {
    // Hide all sections
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    
    // Show target section
    const target = document.getElementById(sectionId);
    if (target) target.classList.add('active');
    
    // Update nav links
    document.querySelectorAll('.nav-link').forEach(link => {
        link.classList.remove('active');
        if (link.getAttribute('href') === '#' + sectionId) {
            link.classList.add('active');
        }
    });
}

// ═══════════════════════════════════════════════════════════
// Telegram Contact
// ═══════════════════════════════════════════════════════════

function contactTelegram(plan) {
    const message = `Hi! I'm interested in the ${plan} plan for SmartSignal VIP.`;
    const telegramUrl = `https://t.me/GoldOilSignals?text=${encodeURIComponent(message)}`;
    window.open(telegramUrl, '_blank');
}

// ═══════════════════════════════════════════════════════════
// Data Loading
// ═══════════════════════════════════════════════════════════

async function loadDashboardData() {
    try {
        // Load trades from Supabase
        const trades = await fetchTrades();
        
        // Update stats
        updateStats(trades);
        
        // Update charts
        updateCharts(trades);
        
        // Update trades table
        updateTradesTable(trades);
        
        // Load reports
        await loadReports();
        
        // Update last update time
        document.getElementById('lastUpdate').textContent = new Date().toLocaleString();
        
    } catch (error) {
        console.error('Error loading dashboard:', error);
        // Load demo data if Supabase is not configured
        loadDemoData();
    }
}

async function fetchTrades() {
    // Try to fetch from Supabase
    if (SUPABASE_URL !== 'https://your-project.supabase.co') {
        const response = await fetch(`${SUPABASE_URL}/rest/v1/trades?order=created_at.desc&limit=100`, {
            headers: {
                'apikey': SUPABASE_KEY,
                'Authorization': `Bearer ${SUPABASE_KEY}`
            }
        });
        return await response.json();
    }
    
    // Return demo data
    return getDemoTrades();
}

function getDemoTrades() {
    return [
        { created_at: '2026-06-27T10:00:00Z', symbol: 'XAU/USD', type: 'BUY', entry_price: 3350.20, stop_loss: 3340.20, tp1: 3365.20, tp2: 3380.20, final_pnl: 550.0, status: 'TP2_HIT', session: 'London Session (07:00-12:00 UTC)' },
        { created_at: '2026-06-27T14:00:00Z', symbol: 'WTI/USD', type: 'SELL', entry_price: 75.50, stop_loss: 76.50, tp1: 74.50, tp2: 73.50, final_pnl: -320.0, status: 'SL_HIT', session: 'London-NY Overlap (12:00-16:00 UTC)' },
        { created_at: '2026-06-26T08:00:00Z', symbol: 'XAU/USD', type: 'BUY', entry_price: 3345.00, stop_loss: 3335.00, tp1: 3360.00, tp2: 3375.00, final_pnl: 480.0, status: 'TP2_HIT', session: 'London Session (07:00-12:00 UTC)' },
        { created_at: '2026-06-26T16:00:00Z', symbol: 'XAU/USD', type: 'SELL', entry_price: 3355.50, stop_loss: 3365.50, tp1: 3340.50, tp2: 3325.50, final_pnl: 420.0, status: 'TP1_HIT', session: 'New York Session (16:00-21:00 UTC)' },
        { created_at: '2026-06-25T12:00:00Z', symbol: 'XAU/USD', type: 'BUY', entry_price: 3340.80, stop_loss: 3330.80, tp1: 3355.80, tp2: 3370.80, final_pnl: 659.6, status: 'TP2_HIT', session: 'London-NY Overlap (12:00-16:00 UTC)' },
        { created_at: '2026-06-25T20:00:00Z', symbol: 'WTI/USD', type: 'BUY', entry_price: 74.80, stop_loss: 73.80, tp1: 75.80, tp2: 76.80, final_pnl: 280.0, status: 'TP1_HIT', session: 'New York Session (16:00-21:00 UTC)' },
        { created_at: '2026-06-24T06:00:00Z', symbol: 'XAU/USD', type: 'BUY', entry_price: 3335.20, stop_loss: 3325.20, tp1: 3350.20, tp2: 3365.20, final_pnl: 520.0, status: 'TP2_HIT', session: 'Asian Session (00:00-07:00 UTC)' },
        { created_at: '2026-06-24T18:00:00Z', symbol: 'XAU/USD', type: 'SELL', entry_price: 3360.00, stop_loss: 3370.00, tp1: 3345.00, tp2: 3330.00, final_pnl: -280.0, status: 'SL_HIT', session: 'New York Session (16:00-21:00 UTC)' },
    ];
}

// ═══════════════════════════════════════════════════════════
// Stats Update
// ═══════════════════════════════════════════════════════════

function updateStats(trades) {
    const total = trades.length;
    const wins = trades.filter(t => t.final_pnl > 0).length;
    const losses = trades.filter(t => t.final_pnl < 0).length;
    const netPnl = trades.reduce((sum, t) => sum + (t.final_pnl || 0), 0);
    const winRate = total > 0 ? ((wins / total) * 100).toFixed(1) : 0;
    
    const grossProfit = trades.filter(t => t.final_pnl > 0).reduce((sum, t) => sum + t.final_pnl, 0);
    const grossLoss = Math.abs(trades.filter(t => t.final_pnl < 0).reduce((sum, t) => sum + t.final_pnl, 0));
    const profitFactor = grossLoss > 0 ? (grossProfit / grossLoss).toFixed(2) : '∞';
    
    const bestTrade = Math.max(...trades.map(t => t.final_pnl || 0));
    const worstTrade = Math.min(...trades.map(t => t.final_pnl || 0));
    
    document.getElementById('totalTrades').textContent = total;
    document.getElementById('winRate').textContent = `${winRate}%`;
    document.getElementById('netPoints').textContent = `${netPnl > 0 ? '+' : ''}${netPnl.toFixed(1)}`;
    document.getElementById('profitFactor').textContent = profitFactor;
    document.getElementById('bestTrade').textContent = `+${bestTrade.toFixed(1)}`;
    document.getElementById('worstTrade').textContent = worstTrade.toFixed(1);
    
    // Update colors
    document.getElementById('netPoints').className = `stat-value ${netPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
}

// ═══════════════════════════════════════════════════════════
// Charts Update
// ═══════════════════════════════════════════════════════════

function updateCharts(trades) {
    updateDailyPnlChart(trades);
    updateCumulativePnlChart(trades);
    updateSessionChart(trades);
    updateInstrumentChart(trades);
}

function updateDailyPnlChart(trades) {
    const dailyPnl = {};
    trades.forEach(t => {
        const date = t.created_at.substring(0, 10);
        dailyPnl[date] = (dailyPnl[date] || 0) + (t.final_pnl || 0);
    });
    
    const labels = Object.keys(dailyPnl).sort();
    const data = labels.map(d => dailyPnl[d]);
    const colors = data.map(v => v >= 0 ? '#3fb950' : '#f85149');
    
    if (dailyPnlChart) dailyPnlChart.destroy();
    
    const ctx = document.getElementById('dailyPnlChart').getContext('2d');
    dailyPnlChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels.map(d => d.substring(5)),
            datasets: [{
                label: 'Daily PnL (pts)',
                data: data,
                backgroundColor: colors,
                borderColor: colors,
                borderWidth: 1,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: {
                    grid: { color: '#30363d' },
                    ticks: { color: '#8b949e' }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: '#8b949e' }
                }
            }
        }
    });
}

function updateCumulativePnlChart(trades) {
    const sorted = [...trades].sort((a, b) => a.created_at.localeCompare(b.created_at));
    let cumulative = 0;
    const data = sorted.map(t => {
        cumulative += (t.final_pnl || 0);
        return cumulative;
    });
    const labels = sorted.map(t => t.created_at.substring(5, 10));
    
    if (cumulativePnlChart) cumulativePnlChart.destroy();
    
    const ctx = document.getElementById('cumulativePnlChart').getContext('2d');
    cumulativePnlChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Cumulative PnL (pts)',
                data: data,
                borderColor: '#58a6ff',
                backgroundColor: 'rgba(88, 166, 255, 0.1)',
                fill: true,
                tension: 0.4,
                pointRadius: 4,
                pointBackgroundColor: '#58a6ff'
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: {
                    grid: { color: '#30363d' },
                    ticks: { color: '#8b949e' }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: '#8b949e' }
                }
            }
        }
    });
}

function updateSessionChart(trades) {
    const sessionPnl = {};
    trades.forEach(t => {
        const session = t.session || 'Unknown';
        sessionPnl[session] = (sessionPnl[session] || 0) + (t.final_pnl || 0);
    });
    
    const labels = Object.keys(sessionPnl);
    const data = labels.map(s => sessionPnl[s]);
    const colors = data.map(v => v >= 0 ? '#3fb950' : '#f85149');
    
    if (sessionChart) sessionChart.destroy();
    
    const ctx = document.getElementById('sessionChart').getContext('2d');
    sessionChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels.map(l => l.split('(')[0].trim()),
            datasets: [{
                label: 'PnL by Session',
                data: data,
                backgroundColor: colors,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            indexAxis: 'y',
            plugins: {
                legend: { display: false }
            },
            scales: {
                x: {
                    grid: { color: '#30363d' },
                    ticks: { color: '#8b949e' }
                },
                y: {
                    grid: { display: false },
                    ticks: { color: '#8b949e' }
                }
            }
        }
    });
}

function updateInstrumentChart(trades) {
    const instrumentPnl = {};
    trades.forEach(t => {
        const symbol = t.symbol || 'XAU/USD';
        instrumentPnl[symbol] = (instrumentPnl[symbol] || 0) + (t.final_pnl || 0);
    });
    
    const labels = Object.keys(instrumentPnl);
    const data = labels.map(s => instrumentPnl[s]);
    
    if (instrumentChart) instrumentChart.destroy();
    
    const ctx = document.getElementById('instrumentChart').getContext('2d');
    instrumentChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: data.map(Math.abs),
                backgroundColor: ['#d4a017', '#58a6ff', '#3fb950', '#f85149'],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: '#8b949e' }
                }
            }
        }
    });
}

// ═══════════════════════════════════════════════════════════
// Trades Table
// ═══════════════════════════════════════════════════════════

function updateTradesTable(trades) {
    const tbody = document.getElementById('tradesBody');
    
    if (trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="loading">No trades found</td></tr>';
        return;
    }
    
    tbody.innerHTML = trades.slice(0, 10).map(t => {
        const pnlClass = (t.final_pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';
        const statusClass = (t.final_pnl || 0) >= 0 ? 'status-win' : 'status-loss';
        const statusIcon = (t.final_pnl || 0) >= 0 ? '✅' : '❌';
        
        return `
            <tr>
                <td>${t.created_at.substring(0, 10)}</td>
                <td>${t.symbol || 'XAU/USD'}</td>
                <td>${t.type || '?'}</td>
                <td>${(t.entry_price || 0).toFixed(2)}</td>
                <td>${(t.stop_loss || 0).toFixed(2)}</td>
                <td>${(t.tp1 || 0).toFixed(2)}</td>
                <td>${(t.tp2 || 0).toFixed(2)}</td>
                <td class="${pnlClass}">${(t.final_pnl || 0) > 0 ? '+' : ''}${(t.final_pnl || 0).toFixed(1)}</td>
                <td class="${statusClass}">${statusIcon} ${(t.status || '').replace(/_/g, ' ')}</td>
            </tr>
        `;
    }).join('');
}

// ═══════════════════════════════════════════════════════════
// Reports Loading
// ═══════════════════════════════════════════════════════════

async function loadReports() {
    // Try to load from Supabase or storage
    try {
        const dailyReport = await fetchReport('daily');
        const weeklyReport = await fetchReport('weekly');
        
        document.getElementById('dailyReport').textContent = dailyReport || 'No daily report available yet.';
        document.getElementById('weeklyReport').textContent = weeklyReport || 'No weekly report available yet.';
    } catch (error) {
        console.error('Error loading reports:', error);
        loadDemoReports();
    }
}

async function fetchReport(type) {
    // Try to fetch from Supabase
    if (SUPABASE_URL !== 'https://your-project.supabase.co') {
        const table = type === 'daily' ? 'daily_reports' : 'weekly_reports';
        const response = await fetch(`${SUPABASE_URL}/rest/v1/${table}?order=created_at.desc&limit=1`, {
            headers: {
                'apikey': SUPABASE_KEY,
                'Authorization': `Bearer ${SUPABASE_KEY}`
            }
        });
        const data = await response.json();
        return data[0]?.report_text || null;
    }
    return null;
}

function loadDemoReports() {
    const dailyReport = `📊 SmartSignal — Daily Report
📅 Period: 2026-06-27
────────────────────

📈 SUMMARY
  Total: 3 trades
  ✅ Wins: 2  |  ❌ Losses: 1
  🎯 Win Rate: 66.7%
────────────────────

💰 PERFORMANCE
  💵 Net: +550.0 pts ($+55.0)
  ⚖️ Profit Factor: 2.00
────────────────────

📊 BY INSTRUMENT
  [+] XAU/USD: 2 trades | WR 100% | Net +1100 pts
  [-] WTI/USD: 1 trades | WR 0% | Net -550 pts
────────────────────

🌍 BY SESSION
  [+] 🇬🇧 London (10:00-03:00 PM): 2 trades | Net +1100 pts
  [-] 🇺🇸 New York (07:00 PM-12:00 AM): 1 trades | Net -550 pts
────────────────────

🛡️ RISK GRADE
  Grade: A+
  ✅ System is profitable`;

    const weeklyReport = `📊 SmartSignal — Weekly Report
Week: 2026-06-20 → 2026-06-27
────────────────────

📈 SUMMARY
  Total trades: 8
  ✅ Wins: 7  |  ❌ Losses: 1
  🎯 Win Rate: 87.5%
  🏆 Best Instrument: XAU/USD
────────────────────

💰 PERFORMANCE
  💵 Net: +3250.3 pts ($+325.0)
  ⚖️ Profit Factor: 6.42
  📈 Expectancy: +406.3 pts/trade
────────────────────

📊 BY INSTRUMENT
  [+] XAU/USD: 6 trades | WR 100% | Net +3850 pts
  [-] WTI/USD: 2 trades | WR 50% | Net -600 pts
────────────────────

🛡️ RISK GRADE
  Grade: A+
  ✅ System is profitable`;

    document.getElementById('dailyReport').textContent = dailyReport;
    document.getElementById('weeklyReport').textContent = weeklyReport;
}

// ═══════════════════════════════════════════════════════════
// Demo Data
// ═══════════════════════════════════════════════════════════

function loadDemoData() {
    const trades = getDemoTrades();
    updateStats(trades);
    updateCharts(trades);
    updateTradesTable(trades);
    loadDemoReports();
    document.getElementById('lastUpdate').textContent = new Date().toLocaleString() + ' (Demo)';
}

// ═══════════════════════════════════════════════════════════
// Initialize
// ═══════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    loadDashboardData();
    
    // Handle hash navigation
    const hash = window.location.hash.substring(1);
    if (hash && ['dashboard', 'pricing'].includes(hash)) {
        showSection(hash);
    }
});
