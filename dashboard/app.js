// SmartSignal Live Dashboard
// Secure version: no Supabase keys in frontend. Data comes from /api/dashboard.

const API_URL = (window.SMARTSIGNAL_API_URL || '/api/dashboard');
const OUTCOME_STATUSES = new Set(['TP2_HIT', 'SL_HIT', 'BE_HIT', 'EXPIRED', 'MANUAL_CLOSE', 'CLOSED']);
const LIVE_STATUSES = new Set(['OPEN', 'TP1_HIT', 'PARTIAL', 'PENDING']);
const CLOSED_TRADES_TABLE_LIMIT = 50;

let currentLang = 'ar';
let closedTrades = [];
let liveTrades = [];
let filteredTrades = [];
let dashboardPayload = null;
let charts = { daily: null, cumulative: null, session: null, instrument: null, regime: null, news: null };
let autoRefreshInterval = null;

const I18N = {
    ar: {
        api404: 'ملف API غير منشور على Vercel: /api/dashboard يرجع 404. إذا كان Root Directory في Vercel هو dashboard، يجب رفع الملف داخل dashboard/api/dashboard.js ثم عمل Redeploy.',
        loadError: 'تعذر تحميل البيانات',
        noClosed: 'لا توجد صفقات مغلقة حسب الفلتر الحالي',
        noLive: 'لا توجد صفقات حية أو TP1 حالياً',
        noDaily: 'لا يوجد تقرير يومي بعد.',
        noWeekly: 'لا يوجد تقرير أسبوعي بعد.',
        noReports: 'لا توجد تقارير محفوظة',
        loading: 'جاري التحميل...',
        details: 'تفاصيل',
        status: 'الحالة',
        entryDate: 'تاريخ الدخول',
        closeDate: 'تاريخ الإغلاق',
        symbol: 'الرمز',
        type: 'النوع',
        entryPrice: 'سعر الدخول',
        currentClose: 'السعر الحالي/الإغلاق',
        sl: 'وقف الخسارة',
        tp1: 'الهدف 1',
        tp2: 'الهدف 2',
        pnl: 'الربح/الخسارة',
        confidence: 'الثقة',
        mode: 'الوضع',
        id: 'المعرف',
        noReportText: 'لا يوجد نص للتقرير',
    },
    en: {
        api404: 'Dashboard API is not deployed: /api/dashboard returns 404. If Vercel Root Directory is dashboard, upload dashboard/api/dashboard.js and redeploy.',
        loadError: 'Failed to load data',
        noClosed: 'No closed trades match the current filter',
        noLive: 'No live or TP1 trades right now',
        noDaily: 'No daily report yet.',
        noWeekly: 'No weekly report yet.',
        noReports: 'No saved reports',
        loading: 'Loading...',
        details: 'Details',
        status: 'Status',
        entryDate: 'Entry Date',
        closeDate: 'Close Date',
        symbol: 'Symbol',
        type: 'Type',
        entryPrice: 'Entry Price',
        currentClose: 'Current/Close',
        sl: 'SL',
        tp1: 'TP1',
        tp2: 'TP2',
        pnl: 'PnL',
        confidence: 'Confidence',
        mode: 'Mode',
        id: 'ID',
        noReportText: 'No report text',
    }
};
function tr(key) { return (I18N[currentLang] && I18N[currentLang][key]) || I18N.ar[key] || key; }
function collectingText() { return currentLang === 'ar' ? 'قيد تجميع البيانات' : 'Collecting data'; }
function localeCode() { return currentLang === 'ar' ? 'ar-SA' : 'en-US'; }
function formatDateTime(value) {
    const d = value ? new Date(value) : new Date();
    return d.toLocaleString(localeCode(), { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
}
function reportText(r) { return currentLang === 'ar' ? (r.report_text_ar || r.report_text || '') : (r.report_text_en || r.report_text || ''); }
function wordTrades(n) { return currentLang === 'ar' ? `${n} صفقات` : `${n} trades`; }
function wordReports(n) { return currentLang === 'ar' ? `${n} تقارير` : `${n} reports`; }
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
    if (dashboardPayload) {
        setText('lastUpdate', formatDateTime(dashboardPayload.generatedAt || Date.now()));
        renderReports(dashboardPayload);
        }
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
function pnlOf(t) {
    // SL_HIT can be a real loss, breakeven, or positive trailing stop.
    // Never force SL_HIT negative; use the realized PnL sign stored/calculated by trade management.
    return num(t.pnl ?? t.final_pnl_points ?? t.final_pnl ?? t.current_pnl_points ?? t.current_pnl ?? 0);
}
function snapshotOf(t) {
    const snap = t.signal_snapshot || {};
    if (typeof snap === 'string') { try { return JSON.parse(snap); } catch { return {}; } }
    return snap && typeof snap === 'object' ? snap : {};
}
function plannedRiskOf(t) {
    const direct = num(t.planned_risk_points, NaN);
    if (Number.isFinite(direct) && direct > 0) return Math.abs(direct);
    const entry = num(t.entry_price, 0), sl = num(t.initial_stop_loss ?? t.stop_loss, 0);
    return entry && sl ? Math.abs(entry - sl) * 10 : 0;
}
function plannedRrOf(t) {
    const direct = num(t.planned_rr, NaN);
    if (Number.isFinite(direct) && direct > 0) return direct;
    const sig = snapshotOf(t).signal || {};
    return num(sig.rr_ratio ?? sig.tp2_rr, 0);
}
function bucketMetric(trades, keyFunc) {
    const buckets = {};
    trades.forEach(t => {
        const key = String(keyFunc(t) || 'UNKNOWN');
        const b = buckets[key] || { count: 0, pnl: 0, wins: 0, losses: 0 };
        const pnl = pnlOf(t);
        b.count += 1; b.pnl += pnl;
        if (pnl > 0) b.wins += 1; else if (pnl < 0) b.losses += 1;
        buckets[key] = b;
    });
    Object.values(buckets).forEach(b => { b.winRate = b.count ? (b.wins / b.count) * 100 : 0; });
    return buckets;
}
function bestBucket(buckets) {
    const items = Object.entries(buckets || {});
    return items.length ? items.sort((a,b) => num(b[1].pnl) - num(a[1].pnl))[0] : null;
}
function worstBucket(buckets, skipUnknown = false) {
    const items = Object.entries(buckets || {}).filter(([k]) => !(skipUnknown && k.toUpperCase() === 'UNKNOWN'));
    return items.length ? items.sort((a,b) => num(a[1].pnl) - num(b[1].pnl))[0] : null;
}
function sessionLabelOf(t) {
    // Prefer hour-based bucket which always returns a standardised
    // session name (e.g. "Asia Morning") over the raw stored label
    // which may be a config name like "Main Trading Session".
    const bucket = sessionBucket(t);
    if (bucket) return bucket;
    // Fallback to stored label if bucket somehow fails
    const snap = snapshotOf(t);
    const si = snap.session_info || {};
    return t.session_label || si.current_session || 'Unknown';
}

// ─── Report Search & Day Trades ───────────────────────────────────────────

function searchReportsByDate() {
    const dateVal = $('reportDateSearch')?.value || '';
    if (!dateVal) { clearReportSearch(); return; }
    // Filter both archive sections to show only matching date
    _filterReportArchive('dailyReportsArchive', item => {
        const period = item.querySelector('.file-title')?.textContent || '';
        return period.includes(dateVal);
    });
    _filterReportArchive('weeklyReportsArchive', item => {
        const period = item.querySelector('.file-title')?.textContent || '';
        return period.includes(dateVal);
    });
    // Also try to show day trades for the exact date
    showDayTrades(dateVal);
}

function searchReportsByKeyword() {
    const keyword = ($('reportKeywordSearch')?.value || '').toLowerCase().trim();
    if (!keyword) { clearReportSearch(); return; }
    _filterReportArchive('dailyReportsArchive', item => {
        const text = item.textContent.toLowerCase();
        return text.includes(keyword);
    });
    _filterReportArchive('weeklyReportsArchive', item => {
        const text = item.textContent.toLowerCase();
        return text.includes(keyword);
    });
}

function clearReportSearch() {
    if ($('reportDateSearch')) $('reportDateSearch').value = '';
    if ($('reportKeywordSearch')) $('reportKeywordSearch').value = '';
    _filterReportArchive('dailyReportsArchive', () => true);
    _filterReportArchive('weeklyReportsArchive', () => true);
    closeDayTrades();
}

function _filterReportArchive(containerId, predicate) {
    const container = $(containerId);
    if (!container) return;
    // Show/hide report-file items
    container.querySelectorAll('.report-file').forEach(item => {
        item.style.display = predicate(item) ? '' : 'none';
    });
    // Show/hide month folders based on whether they have visible children
    container.querySelectorAll('.report-month-folder').forEach(folder => {
        const visibleFiles = folder.querySelectorAll('.report-file:not([style*="display: none"])');
        folder.style.display = visibleFiles.length > 0 ? '' : 'none';
    });
}

function showDayTrades(dateStr) {
    if (!dateStr || !closedTrades.length) return;
    const panel = $('dayTradesPanel');
    if (!panel) return;

    // Find trades for this date (by close date or open date)
    const dayTrades = closedTrades.filter(t => {
        const tDate = reportDate(t);
        const oDate = dateText(tradeTime(t));
        return tDate === dateStr || oDate === dateStr;
    });

    const ar = currentLang === 'ar';
    const title = ar ? `صفقات يوم ${dateStr}` : `Trades for ${dateStr}`;
    $('dayTradesTitle').textContent = title;

    // Summary
    const wins = dayTrades.filter(t => pnlOf(t) > 0).length;
    const losses = dayTrades.filter(t => pnlOf(t) < 0).length;
    const net = dayTrades.reduce((s, t) => s + pnlOf(t), 0);
    const wr = dayTrades.length ? ((wins / dayTrades.length) * 100).toFixed(1) : 0;
    $('dayTradesSubtitle').textContent = ar
        ? `${dayTrades.length} صفقة · ✅ ${wins} ❌ ${losses} · صافي ${signed(net)} نقطة · ربح ${wr}%`
        : `${dayTrades.length} trades · ✅ ${wins} ❌ ${losses} · Net ${signed(net)} pts · WR ${wr}%`;

    // Build trades table
    const body = $('dayTradesBody');
    if (!dayTrades.length) {
        body.innerHTML = `<div class="empty">${ar ? 'لا توجد صفقات لهذا اليوم' : 'No trades found for this day'}</div>`;
    } else {
        body.innerHTML = `<div class="table-container"><table>
            <thead><tr>
                <th>${ar ? 'النوع' : 'Type'}</th>
                <th>${ar ? 'الرمز' : 'Symbol'}</th>
                <th>${ar ? 'دخول' : 'Entry'}</th>
                <th>${ar ? 'إغلاق' : 'Close'}</th>
                <th>${ar ? 'SL' : 'SL'}</th>
                <th>${ar ? 'TP1' : 'TP1'}</th>
                <th>${ar ? 'النقاط' : 'PnL'}</th>
                <th>${ar ? 'الحالة' : 'Status'}</th>
                <th>${ar ? 'الثقة' : 'Conf'}</th>
            </tr></thead>
            <tbody>${dayTrades.map(t => {
                const pnl = pnlOf(t);
                const cls = pnl > 0 ? 'pnl-positive' : pnl < 0 ? 'pnl-negative' : '';
                return `<tr>
                    <td><span class="badge ${t.type === 'BUY' ? 'buy' : 'sell'}">${esc(t.type)}</span></td>
                    <td>${esc(t.symbol)}</td>
                    <td>${num(t.entry_price).toFixed(2)}</td>
                    <td>${t.close_price != null ? num(t.close_price).toFixed(2) : '-'}</td>
                    <td>${t.stop_loss ?? '-'}</td>
                    <td>${t.tp1 ?? '-'}</td>
                    <td class="${cls}"><strong>${signed(pnl)}</strong></td>
                    <td><span class="badge ${statusClassOf(t)}">${esc(displayStatus(t))}</span></td>
                    <td>${esc(t.confidence ?? '--')}%</td>
                </tr>`;
            }).join('')}</tbody>
        </table></div>`;
    }
    panel.style.display = 'block';
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function closeDayTrades() {
    const panel = $('dayTradesPanel');
    if (panel) panel.style.display = 'none';
}

// Extend renderReportArchive to add "Show Trades" button for daily reports
const _origRenderReportArchive = typeof renderReportArchive === 'function' ? renderReportArchive : null;
function newsLabelOf(t) {
    const snap = snapshotOf(t);
    const rule = (snap.news_context || {}).rule_based || {};
    return String(t.news_status_at_entry || rule.market_status || rule.status || 'UNKNOWN').toUpperCase();
}
function regimeLabelOf(t) {
    const snap = snapshotOf(t);
    const tech = (snap.market_context || {}).technical_regime || {};
    return String(t.volatility_regime || tech.volatility_regime || 'UNKNOWN').toUpperCase();
}
function stopOutcomeOf(t) {
    if (String(t.status || '').toUpperCase() !== 'SL_HIT') return null;
    const pnl = pnlOf(t);
    if (t.stop_outcome) return t.stop_outcome;
    if (pnl > 0) return 'SL_PLUS';
    if (pnl < 0) return 'SL_LOSS';
    return 'SL_BE';
}
function displayStatus(t) {
    const outcome = stopOutcomeOf(t);
    if (outcome === 'SL_PLUS') return 'SL+';
    if (outcome === 'SL_BE') return 'SL BE';
    if (outcome === 'SL_LOSS') return 'SL LOSS';
    return String(t.status || 'UNKNOWN').toUpperCase();
}
function statusClassOf(t) {
    const pnl = pnlOf(t);
    const outcome = stopOutcomeOf(t);
    if (outcome === 'SL_PLUS') return 'win';
    if (outcome === 'SL_BE') return 'neutral';
    if (outcome === 'SL_LOSS') return 'loss';
    return pnl > 0 || t.status === 'TP2_HIT' || t.status === 'TP1_HIT' ? 'win' : pnl < 0 ? 'loss' : 'neutral';
}
function tradeTime(t) { return t.created_at || t.entry_time || t.opened_at || t.updated_at || ''; }
function closeTime(t) { return t.closed_at || t.close_time || ''; }
function openTime(t) { return t.entry_time || t.created_at || t.opened_at || t.updated_at || ''; }
function tradeReportTime(t) {
    // Realized performance belongs to the day the trade CLOSED, not the day it
    // was opened.  A trade opened yesterday and closed today must therefore
    // appear in today's table filters and daily PnL chart.
    return closeTime(t) || openTime(t);
}
function reportDate(t) { return dateText(tradeReportTime(t)); }
function localHourJerusalem(value) {
    const d = value ? new Date(value) : new Date();
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Jerusalem', hour: '2-digit', hour12: false }).formatToParts(d);
    return Number(parts.find(p => p.type === 'hour')?.value || 0);
}
function sessionBucket(t) {
    const h = localHourJerusalem(openTime(t));
    if (h >= 3 && h < 10) return currentLang === 'ar' ? 'آسيا صباحاً' : 'Asia Morning';
    if (h >= 10 && h < 15) return currentLang === 'ar' ? 'لندن / أوروبا ظهراً' : 'London / Europe Midday';
    if (h >= 15 && h < 19) return currentLang === 'ar' ? 'لندن + أمريكا عصراً' : 'London + New York Afternoon';
    if (h >= 19 && h < 24) return currentLang === 'ar' ? 'أمريكا مساءً' : 'New York Evening';
    return currentLang === 'ar' ? 'أمريكا متأخرة ليلاً' : 'Late New York Night';
}
function isLiveStatus(status) { return LIVE_STATUSES.has(String(status || '').toUpperCase()); }
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
        liveTrades = (payload.liveTrades || []).map(normalizeTrade).filter(t => isLiveStatus(t.status));
        filteredTrades = [...closedTrades];

        updateStats(filteredTrades, liveTrades);
        updateCharts(filteredTrades);
        renderTradesTable(filteredTrades);
        updateOpenTrades(liveTrades);
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
        updateOpenTrades([]);
        renderReports({ dailyReports: [], weeklyReports: [] });
        setText('dataSource', 'خطأ');
        setText('lastUpdate', formatDateTime(Date.now()));
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
    const losses = trades.filter(t => pnlOf(t) < 0);
    const netPnl = trades.reduce((sum, t) => sum + pnlOf(t), 0);
    const winRate = total ? (wins.length / total) * 100 : 0;
    const grossProfit = trades.filter(t => pnlOf(t) > 0).reduce((sum, t) => sum + pnlOf(t), 0);
    const grossLoss = Math.abs(trades.filter(t => pnlOf(t) < 0).reduce((sum, t) => sum + pnlOf(t), 0));
    const profitFactor = grossLoss > 0 ? (grossProfit / grossLoss).toFixed(2) : grossProfit > 0 ? '∞' : '--';
    const pnls = trades.map(pnlOf);
    const best = pnls.length ? Math.max(...pnls) : 0;
    const worst = pnls.length ? Math.min(...pnls) : 0;
    const avg = total ? netPnl / total : 0;

    setText('totalTrades', total);
    setText('winRate', `${winRate.toFixed(1)}%`);
    setText('netPoints', signed(netPnl));
    setText('profitFactor', profitFactor);
    setText('liveCount', live.length);
    setText('tp1Count', live.filter(t => t.status === 'TP1_HIT').length);
    setText('bestTrade', pnls.length ? signed(best) : '--');
    setText('worstTrade', pnls.length ? signed(worst) : '--');
    setText('avgTrade', total ? signed(avg) : '--');
    // Streak: longest consecutive win streak across closed trades
    let maxStreak = 0, curStreak = 0;
    trades.forEach(t => { if (pnlOf(t) > 0) { curStreak++; maxStreak = Math.max(maxStreak, curStreak); } else { curStreak = 0; } });
    setText('streak', maxStreak > 0 ? `${maxStreak}W` : '--');
    setText('tradesCount', total > CLOSED_TRADES_TABLE_LIMIT ? `(${CLOSED_TRADES_TABLE_LIMIT}/${total})` : `(${total})`);
    updateEdgeSnapshot(trades);

    const netEl = $('netPoints');
    if (netEl) netEl.style.color = netPnl >= 0 ? '#2b8a3e' : '#c92a2a';
    const bar = $('winRateBar');
    if (bar) bar.style.width = `${Math.min(winRate, 100)}%`;
}

function updateEdgeSnapshot(trades) {
    const actualR = [], planned = [];
    trades.forEach(t => {
        const risk = plannedRiskOf(t);
        if (risk > 0) {
            actualR.push(pnlOf(t) / risk);
            const rr = plannedRrOf(t);
            if (rr > 0) planned.push(rr);
        }
    });
    if (actualR.length) {
        const avgActual = actualR.reduce((a,b)=>a+b,0) / actualR.length;
        const avgPlanned = planned.length ? planned.reduce((a,b)=>a+b,0) / planned.length : 0;
        const capture = avgPlanned ? (avgActual / avgPlanned) * 100 : 0;
        setText('rrCapture', `${capture.toFixed(1)}%`);
        setText('rrCaptureSub', `${avgActual >= 0 ? '+' : ''}${avgActual.toFixed(2)}R / ${avgPlanned.toFixed(2)}R`);
    } else {
        setText('rrCapture', '--'); setText('rrCaptureSub', currentLang === 'ar' ? 'لا توجد بيانات' : 'No enriched data');
    }
    const sessions = bucketMetric(trades, sessionLabelOf);
    const news = bucketMetric(trades, newsLabelOf);
    const regimes = bucketMetric(trades, regimeLabelOf);
    const bestSession = bestBucket(sessions);
    const weakNews = worstBucket(news, true);
    const knownRegimes = Object.fromEntries(Object.entries(regimes).filter(([k]) => k.toUpperCase() !== 'UNKNOWN'));
    const bestRegime = bestBucket(knownRegimes);
    setText('bestSession', bestSession ? bestSession[0] : '--');
    setText('bestSessionSub', bestSession ? `${signed(bestSession[1].pnl)} pts · WR ${bestSession[1].winRate.toFixed(0)}%` : '--');
    setText('newsImpact', weakNews ? weakNews[0] : collectingText());
    setText('newsImpactSub', weakNews ? `${signed(weakNews[1].pnl)} pts · ${weakNews[1].count} ${currentLang === 'ar' ? 'صفقات' : 'trades'}` : (currentLang === 'ar' ? 'تظهر بعد توفر سياق الأخبار' : 'Shown when news context is available'));
    setText('bestRegime', bestRegime ? bestRegime[0] : collectingText());
    setText('bestRegimeSub', bestRegime ? `${signed(bestRegime[1].pnl)} pts · WR ${bestRegime[1].winRate.toFixed(0)}%` : (currentLang === 'ar' ? 'تظهر بعد توفر بيانات السوق' : 'Shown when regime data is available'));
}

function updateCharts(trades) {
    updateDailyPnlChart(trades);
    updateCumulativePnlChart(trades);
    updateSessionChart(trades);
    updateInstrumentChart(trades);
    updateRegimeChart(trades);
    updateNewsChart(trades);
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
    const sorted = [...trades].sort((a, b) => String(tradeReportTime(a)).localeCompare(String(tradeReportTime(b))));
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
    const order = currentLang === 'ar'
        ? ['آسيا صباحاً', 'لندن / أوروبا ظهراً', 'لندن + أمريكا عصراً', 'أمريكا مساءً', 'أمريكا متأخرة ليلاً']
        : ['Asia Morning', 'London / Europe Midday', 'London + New York Afternoon', 'New York Evening', 'Late New York Night'];
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
        data: {
            labels: displayLabels,
            datasets: [{
                data,
                backgroundColor: data.map(v => v >= 0 ? 'rgba(22,163,74,.72)' : 'rgba(220,38,38,.72)'),
                borderColor: data.map(v => v >= 0 ? '#16a34a' : '#dc2626'),
                borderWidth: 1,
                borderRadius: 8,
                maxBarThickness: 34,
            }]
        },
        options: chartOptions({
            indexAxis: 'y',
            plugins: {
                ...chartOptions().plugins,
                tooltip: {
                    ...chartOptions().plugins.tooltip,
                    callbacks: { label: ctx => ` ${signed(ctx.parsed.x, 1)} pts · ${counts[labels[ctx.dataIndex]]} ${currentLang === 'ar' ? 'صفقات' : 'trades'}` }
                }
            },
            scales: {
                x: chartOptions().scales.y,
                y: { grid: { display: false }, ticks: { color: document.body.classList.contains('dark') ? '#94a3b8' : '#64748b' } }
            }
        }),
    });
}


function updateRegimeChart(trades) {
    const grouped = bucketMetric(trades, regimeLabelOf);
    const labels = Object.keys(grouped).filter(k => k.toUpperCase() !== 'UNKNOWN');
    const data = labels.map(k => grouped[k].pnl);
    const ctx = $('regimeChart');
    setChartEmpty('regimeEmpty', !data.length);
    if (!ctx || typeof Chart === 'undefined') return;
    safeDestroyChart('regime');
    if (!data.length) return;
    charts.regime = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: { labels: labels.map(k => `${k} (${grouped[k].count})`), datasets: [{ data, backgroundColor: data.map(v => v >= 0 ? 'rgba(22,163,74,.72)' : 'rgba(220,38,38,.72)'), borderRadius: 8, maxBarThickness: 38 }] },
        options: chartOptions(),
    });
}

function updateNewsChart(trades) {
    const grouped = bucketMetric(trades, newsLabelOf);
    const labels = Object.keys(grouped).filter(k => k.toUpperCase() !== 'UNKNOWN');
    const data = labels.map(k => grouped[k].pnl);
    const ctx = $('newsChart');
    setChartEmpty('newsEmpty', !data.length);
    if (!ctx || typeof Chart === 'undefined') return;
    safeDestroyChart('news');
    if (!data.length) return;
    charts.news = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: { labels: labels.map(k => `${k} (${grouped[k].count})`), datasets: [{ data, backgroundColor: data.map(v => v >= 0 ? 'rgba(22,163,74,.72)' : 'rgba(220,38,38,.72)'), borderRadius: 8, maxBarThickness: 38 }] },
        options: chartOptions(),
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
    const visibleTrades = trades.slice(0, CLOSED_TRADES_TABLE_LIMIT);
    tbody.innerHTML = visibleTrades.map(trade => {
        const pnl = pnlOf(trade);
        const statusClass = statusClassOf(trade);
        return `<tr onclick='showTradeModalById(${JSON.stringify(trade.id)})'>
            <td>${esc(dateText(tradeTime(trade)))}</td>
            <td>${esc(dateText(closeTime(trade)))}</td>
            <td><strong>${esc(trade.symbol)}</strong></td>
            <td><span class="badge ${trade.type === 'BUY' ? 'buy' : trade.type === 'SELL' ? 'sell' : 'neutral'}">${esc(trade.type || 'N/A')}</span></td>
            <td>${num(trade.entry_price).toFixed(2)}</td>
            <td>${trade.close_price != null ? num(trade.close_price).toFixed(2) : '-'}</td>
            <td class="${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}"><strong>${signed(pnl)}</strong></td>
            <td><span class="badge ${statusClass}">${esc(displayStatus(trade))}</span></td>
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


function monthLabel(month) {
    if (!month || month.length < 7) return currentLang === 'ar' ? 'بدون شهر' : 'No Month';
    const [y, m] = month.split('-');
    const namesAr = ['يناير','فبراير','مارس','أبريل','مايو','يونيو','يوليو','أغسطس','سبتمبر','أكتوبر','نوفمبر','ديسمبر'];
    const namesEn = ['January','February','March','April','May','June','July','August','September','October','November','December'];
    const idx = Math.max(0, Math.min(11, Number(m) - 1));
    return currentLang === 'ar' ? `${namesAr[idx]} ${y}` : `${namesEn[idx]} ${y}`;
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
        el.innerHTML = `<div class="empty">${currentLang === 'ar' ? 'لا توجد تقارير' : 'No reports'}</div>`;
        return;
    }
    const groups = groupReportsByMonth(reports, type);
    const months = Object.keys(groups).sort().reverse();
    el.innerHTML = months.map(month => {
        const list = groups[month];
        const totalNet = list.reduce((sum, r) => sum + num(r.daily_pnl ?? r.net_pnl_points ?? r.stats_json?.net_pnl_points ?? r.stats_json?.net_points ?? 0), 0);
        const items = list.map((r, idx) => {
            const period = reportPeriod(r, type);
            const net = num(r.daily_pnl ?? r.net_pnl_points ?? r.stats_json?.net_pnl_points ?? r.stats_json?.net_points ?? 0);
            const trades = num(r.closed_trades ?? r.stats_json?.closed_trades ?? r.stats_json?.total_trades ?? r.stats_json?.total ?? 0);
            const wr = num(r.win_rate ?? r.stats_json?.win_rate_pct ?? r.stats_json?.win_rate ?? 0);
            const rid = `${containerId}-${month}-${idx}`.replace(/[^a-zA-Z0-9_-]/g, '_');
            const isDaily = type === 'daily';
            const showTradesBtn = isDaily
                ? `<button class="btn btn-sm btn-show-trades" onclick="event.stopPropagation(); showDayTrades('${esc(period.substring(0, 10))}')" data-en="Show Trades" data-ar="عرض الصفقات">${currentLang === 'ar' ? '📋 عرض الصفقات' : '📋 Show Trades'}</button>`
                : '';
            return `<div class="report-file">
                <button class="report-file-head" onclick="toggleReportFile('${rid}')">
                    <span class="file-icon">${type === 'weekly' ? '🗓️' : '📄'}</span>
                    <span class="file-title">${esc(period)}</span>
                    <span class="file-meta">${wordTrades(trades)} · ${currentLang === 'ar' ? 'نسبة الربح' : 'WR'} ${wr.toFixed(1)}% · <b class="${net >= 0 ? 'pnl-positive' : 'pnl-negative'}">${signed(net, 1)}</b></span>
                </button>
                <div class="report-file-actions">${showTradesBtn}</div>
                <pre id="${rid}" class="report-file-body">${reportText(r) || tr('noReportText')}</pre>
            </div>`;
        }).join('');
        return `<div class="report-month-folder">
            <div class="folder-head"><div><span class="folder-icon">📁</span><strong>${esc(monthLabel(month))}</strong></div><span>${wordReports(list.length)} · <b class="${totalNet >= 0 ? 'pnl-positive' : 'pnl-negative'}">${signed(totalNet, 1)}</b></span></div>
            <div class="folder-body">${items}</div>
        </div>`;
    }).join('');
}
function toggleReportFile(id) {
    const el = $(id);
    if (!el) return;
    el.classList.toggle('open');
}
function renderReports(payload) {
    const daily = payload.dailyReports || [];
    const weekly = payload.weeklyReports || [];
    const latestDaily = daily[0];
    const latestWeekly = weekly[0];
    setHTML('dailyReport', latestDaily ? (reportText(latestDaily) || tr('noReportText')) : tr('noDaily'));
    setHTML('weeklyReport', latestWeekly ? (reportText(latestWeekly) || tr('noWeekly')) : tr('noWeekly'));
    renderReportArchive('dailyReportsArchive', daily, 'daily');
    renderReportArchive('weeklyReportsArchive', weekly, 'weekly');
    setText('dailyReportsCount', daily.length ? wordReports(daily.length) : '');
    setText('weeklyReportsCount', weekly.length ? wordReports(weekly.length) : '');
    setHTML('reportsBody', '');
}

function updateAgentPerformance() {
    const agents = (dashboardPayload?.agentPerformance || []);
    const agentWeightsFromApi = (dashboardPayload?.agentWeights || []);
    const grid = $('agentsGrid');
    if (!grid) return;
    if (!agents.length) {
        // Fallback weights — must match config.json::agent_weights and utils/helpers.py::get_agent_weights
        const fallbackWeights = {multitimeframe: 0.15, classical: 0.25, smc: 0.20, price_action: 0.20, technical: 0.20};
        grid.innerHTML = Object.keys(fallbackWeights).map(name => `<div class="agent-card"><div class="agent-header"><span class="agent-icon">🤖</span><span class="agent-name">${name}</span></div><div class="agent-stats"><div class="agent-metric"><span>${currentLang === 'ar' ? 'الوزن' : 'Weight'}</span><strong>${(fallbackWeights[name]*100).toFixed(1)}%</strong></div></div><div class="muted">No performance data yet</div></div>`).join('');
        setText('consensusStrength', '--');
        return;
    }
    // Build a lookup from API agentWeights so DB values override code fallbacks
    const weightMap = {};
    (agentWeightsFromApi || []).forEach(a => {
        const name = String(a.agent_name || '').toLowerCase();
        if (name) weightMap[name] = Number(a.weight ?? 0);
    });
    grid.innerHTML = agents.map(a => {
        const hasComputed = a.win_rate !== null && a.win_rate !== undefined && Number.isFinite(Number(a.win_rate));
        const wr = hasComputed ? num(a.win_rate) : 0;
        const apiWeight = weightMap[String(a.agent_name || '').toLowerCase()];
        const weight = num(apiWeight !== undefined ? apiWeight : a.weight) * 100;
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
                <div class="agent-metric"><span>${currentLang === 'ar' ? 'الوزن' : 'Weight'}</span><strong>${weight.toFixed(1)}%</strong></div>
                <div class="agent-metric"><span>${currentLang === 'ar' ? 'نسبة الربح' : 'Win Rate'}</span><strong>${hasComputed ? `${wr.toFixed(1)}%` : 'N/A'}</strong></div>
                <div class="agent-metric"><span>Predictions</span><strong>${predictions}</strong></div>
                <div class="agent-metric"><span>W / L</span><strong>${wins} / ${losses}</strong></div>
                <div class="agent-metric"><span>Net PnL</span><strong class="${net >= 0 ? 'pnl-positive' : 'pnl-negative'}">${signed(net, 1)}</strong></div>
                <div class="agent-metric"><span>${currentLang === 'ar' ? 'انتقائية' : 'Selectivity'}</span><strong>${predictions > 0 ? (predictions < 20 ? '⭐' + (currentLang === 'ar' ? ' انتقائي' : ' Selective') : predictions < 35 ? '✅' + (currentLang === 'ar' ? ' متوازن' : ' Balanced') : '⚠️' + (currentLang === 'ar' ? ' نشط' : ' Active')) : 'N/A'}</strong></div>
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
    title.textContent = `${trade.type} ${trade.symbol} — ${displayStatus(trade)}`;
    body.innerHTML = `<div class="trade-detail-grid">
        <div><strong>${tr('id')}:</strong> <code>${esc(trade.id)}</code></div>
        <div><strong>${tr('status')}:</strong> <span class="badge ${statusClassOf(trade)}">${esc(displayStatus(trade))}</span></div>
        <div><strong>${tr('entryDate')}:</strong> ${esc(timeText(tradeTime(trade)))}</div>
        <div><strong>${tr('closeDate')}:</strong> ${esc(timeText(closeTime(trade)))}</div>
        <div><strong>${tr('symbol')}:</strong> ${esc(trade.symbol)}</div>
        <div><strong>${tr('type')}:</strong> <span class="badge ${trade.type === 'BUY' ? 'buy' : 'sell'}">${esc(trade.type)}</span></div>
        <div><strong>${tr('entryPrice')}:</strong> ${num(trade.entry_price).toFixed(2)}</div>
        <div><strong>${tr('currentClose')}:</strong> ${trade.close_price ?? trade.current_price ?? '-'}</div>
        <div><strong>${tr('sl')}:</strong> ${trade.stop_loss ?? '-'}</div>
        <div><strong>${tr('tp1')}:</strong> ${trade.tp1 ?? '-'}</div>
        <div><strong>${tr('tp2')}:</strong> ${trade.tp2 ?? '-'}</div>
        <div><strong>${tr('pnl')}:</strong> <span class="${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${signed(pnl)}</span></div>
        <div><strong>${tr('confidence')}:</strong> ${esc(trade.confidence ?? '--')}%</div>
        <div><strong>${tr('mode')}:</strong> ${esc(trade.trading_mode || 'paper')}</div>
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
    if (hash && ['dashboard', 'reports', 'agents', 'pricing', 'subscribe'].includes(hash)) showSection(hash);
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') closeModal();
        if (e.key === '/' && document.activeElement?.tagName === 'BODY') { e.preventDefault(); $('searchInput')?.focus(); }
    });
});
