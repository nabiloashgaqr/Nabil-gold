// Secure Vercel API for SmartSignal dashboard.
// Reads Supabase using server-side environment variables only.
// Required env in Vercel: SUPABASE_URL and SUPABASE_SERVICE_KEY (or SUPABASE_KEY).

const OUTCOME_STATUSES = ['TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BE_HIT', 'EXPIRED', 'MANUAL_CLOSE', 'CLOSED'];
const LIVE_STATUSES = [];

function json(res, status, body) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store, max-age=0');
  res.end(JSON.stringify(body));
}

function getEnv() {
  const url = (process.env.SUPABASE_URL || '').replace(/\/$/, '');
  const key = process.env.SUPABASE_SERVICE_KEY || process.env.SUPABASE_KEY || '';
  return { url, key };
}

async function supabaseGet(path, params = {}) {
  const { url, key } = getEnv();
  if (!url || !key) {
    const missing = [];
    if (!url) missing.push('SUPABASE_URL');
    if (!key) missing.push('SUPABASE_SERVICE_KEY or SUPABASE_KEY');
    const err = new Error(`Missing Vercel env: ${missing.join(', ')}`);
    err.code = 'MISSING_ENV';
    throw err;
  }

  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && String(v) !== '') qs.set(k, String(v));
  });

  const endpoint = `${url}/rest/v1/${path}${qs.toString() ? `?${qs}` : ''}`;
  const response = await fetch(endpoint, {
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    const err = new Error(`Supabase ${path} failed: ${response.status} ${text}`);
    err.status = response.status;
    throw err;
  }
  return response.json();
}

function normalizePnlByStatus(_status, rawPnl) {
  // Important: SL_HIT is not always a loss.
  // If SL was moved to breakeven/trailing profit, SL_HIT can be BE or SL+.
  // Therefore we trust stored realized PnL sign and classify the stop separately.
  return Number(rawPnl) || 0;
}

function stopOutcome(t, pnl) {
  const status = String(t.status || '').toUpperCase();
  if (status !== 'SL_HIT') return null;
  if (pnl > 0) return 'SL_PLUS';
  if (pnl < 0) return 'SL_LOSS';
  return 'SL_BE';
}

function normalizeTrade(t) {
  const status = String(t.status || 'UNKNOWN').toUpperCase();
  const rawPnl = t.final_pnl ?? t.current_pnl_points ?? t.current_pnl ?? t.pnl ?? 0;
  const pnl = normalizePnlByStatus(status, rawPnl);
  return {
    ...t,
    id: t.id || '',
    symbol: t.symbol || 'XAU/USD',
    type: String(t.type || t.side || t.trade_type || '').toUpperCase(),
    status,
    stop_outcome: stopOutcome(t, pnl),
    raw_pnl: Number(rawPnl) || 0,
    pnl,
    created_at: t.created_at || t.entry_time || t.opened_at || t.updated_at || '',
    closed_at: t.closed_at || t.close_time || '',
  };
}


function dateOnly(value) {
  return value ? String(value).slice(0, 10) : '';
}
function reportTradeDate(t) {
  return dateOnly(t.closed_at || t.close_time || t.created_at || t.entry_time || t.updated_at);
}
function buildGeneratedDailyReports(closedTrades) {
  const groups = {};
  closedTrades.forEach(t => {
    const d = reportTradeDate(t);
    if (!d) return;
    if (!groups[d]) groups[d] = [];
    groups[d].push(t);
  });
  return Object.keys(groups).sort().reverse().map(period => {
    const trades = groups[period];
    const wins = trades.filter(t => Number(t.pnl) > 0).length;
    const losses = trades.filter(t => Number(t.pnl) < 0).length;
    const be = trades.filter(t => Number(t.pnl) === 0).length;
    const net = trades.reduce((s, t) => s + (Number(t.pnl) || 0), 0);
    const winRate = trades.length ? (wins / trades.length) * 100 : 0;
    const lines = [
      'SmartSignal — Daily Report',
      `Date: ${period}`,
      '────────────────────',
      'SUMMARY',
      `  Closed Trades: ${trades.length}`,
      `  Wins: ${wins} | Losses: ${losses} | BE: ${be}`,
      `  Win Rate: ${winRate.toFixed(1)}%`,
      '',
      'PERFORMANCE',
      `  Net: ${net >= 0 ? '+' : ''}${net.toFixed(1)} pts`,
      '',
      'TRADE DETAILS',
      ...trades.slice(0, 20).map(t => `  [${Number(t.pnl) >= 0 ? '+' : '-'}] ${t.type || ''} ${t.symbol || ''} | Entry ${t.entry_price ?? '-'} | ${Number(t.pnl) >= 0 ? '+' : ''}${Number(t.pnl || 0).toFixed(1)} pts | ${t.status}`),
      '',
      'Source: Generated live from closed trades grouped by open date.',
    ];
    return {
      id: `generated-daily-${period}`,
      generated: true,
      report_type: 'daily',
      report_date: period,
      month: period.slice(0, 7),
      closed_trades: trades.length,
      winning_trades: wins,
      losing_trades: losses,
      breakeven_trades: be,
      daily_pnl: Number(net.toFixed(1)),
      win_rate: Number(winRate.toFixed(1)),
      created_at: new Date().toISOString(),
      report_text: lines.join('\n'),
    };
  });
}
function buildGeneratedDailyReport(closedTrades) {
  return buildGeneratedDailyReports(closedTrades)[0] || {
    id: 'generated-daily-empty', generated: true, report_type: 'daily', report_date: new Date().toISOString().slice(0,10),
    month: new Date().toISOString().slice(0,7), closed_trades: 0, daily_pnl: 0, win_rate: 0,
    created_at: new Date().toISOString(), report_text: 'SmartSignal — Daily Report\nNo closed trades available.'
  };
}

function getWeekStart(date) {
  const d = new Date(`${date}T00:00:00Z`);
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() - day + 1);
  return d.toISOString().slice(0, 10);
}
function addDays(date, days) {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
function buildGeneratedWeeklyReports(closedTrades) {
  const groups = {};
  closedTrades.forEach(t => {
    const d = reportTradeDate(t);
    if (!d) return;
    const ws = getWeekStart(d);
    const we = addDays(ws, 6);
    const key = `${ws}__${we}`;
    if (!groups[key]) groups[key] = { weekStart: ws, weekEnd: we, trades: [] };
    groups[key].trades.push(t);
  });
  return Object.values(groups).sort((a, b) => b.weekStart.localeCompare(a.weekStart)).map(group => {
    const trades = group.trades;
    const wins = trades.filter(t => Number(t.pnl) > 0).length;
    const losses = trades.filter(t => Number(t.pnl) < 0).length;
    const be = trades.filter(t => Number(t.pnl) === 0).length;
    const net = trades.reduce((s, t) => s + (Number(t.pnl) || 0), 0);
    const winRate = trades.length ? (wins / trades.length) * 100 : 0;
    const bySymbol = {};
    trades.forEach(t => { bySymbol[t.symbol || 'Unknown'] = (bySymbol[t.symbol || 'Unknown'] || 0) + (Number(t.pnl) || 0); });
    const lines = [
      'SmartSignal — Weekly Report',
      `Week: ${group.weekStart} → ${group.weekEnd}`,
      '────────────────────',
      'SUMMARY',
      `  Closed Trades: ${trades.length}`,
      `  Wins: ${wins} | Losses: ${losses} | BE: ${be}`,
      `  Win Rate: ${winRate.toFixed(1)}%`,
      '',
      'PERFORMANCE',
      `  Net: ${net >= 0 ? '+' : ''}${net.toFixed(1)} pts`,
      '',
      'BY SYMBOL',
      ...Object.entries(bySymbol).map(([sym, pnl]) => `  ${sym}: ${pnl >= 0 ? '+' : ''}${Number(pnl).toFixed(1)} pts`),
      '',
      'Source: Generated live from closed trades grouped by open week.',
    ];
    return {
      id: `generated-weekly-${group.weekStart}`,
      generated: true,
      report_type: 'weekly',
      week_start: group.weekStart,
      week_end: group.weekEnd,
      month: group.weekStart.slice(0, 7),
      status: 'GENERATED',
      closed_trades: trades.length,
      wins,
      losses,
      break_even: be,
      net_pnl_points: Number(net.toFixed(1)),
      win_rate: Number(winRate.toFixed(1)),
      created_at: new Date().toISOString(),
      report_text: lines.join('\n'),
    };
  });
}
function buildGeneratedWeeklyReport(closedTrades) {
  return buildGeneratedWeeklyReports(closedTrades)[0] || {
    id: 'generated-weekly-empty', generated: true, report_type: 'weekly', week_start: new Date().toISOString().slice(0,10),
    week_end: new Date().toISOString().slice(0,10), month: new Date().toISOString().slice(0,7), status: 'GENERATED',
    created_at: new Date().toISOString(), report_text: 'SmartSignal — Weekly Report\nNo closed trades available.'
  };
}

function summarize(closedTrades, liveTrades) {
  const total = closedTrades.length;
  const wins = closedTrades.filter(t => Number(t.pnl) > 0).length;
  const losses = closedTrades.filter(t => Number(t.pnl) < 0).length;
  const net = closedTrades.reduce((s, t) => s + (Number(t.pnl) || 0), 0);
  const gp = closedTrades.filter(t => Number(t.pnl) > 0).reduce((s, t) => s + Number(t.pnl), 0);
  const gl = Math.abs(closedTrades.filter(t => Number(t.pnl) < 0).reduce((s, t) => s + Number(t.pnl), 0));
  return {
    closedTrades: total,
    liveTrades: liveTrades.length,
    tp1Live: liveTrades.filter(t => t.status === 'TP1_HIT').length,
    winRate: total ? (wins / total) * 100 : 0,
    netPoints: net,
    profitFactor: gl > 0 ? gp / gl : gp > 0 ? null : 0,
    wins,
    losses,
  };
}

module.exports = async function handler(req, res) {
  if (req.method !== 'GET') return json(res, 405, { ok: false, error: 'Method not allowed' });

  try {
    const limit = Math.min(Math.max(parseInt(req.query.limit || '150', 10) || 150, 20), 500);
    const closedFilter = `in.(${OUTCOME_STATUSES.join(',')})`;
    const liveFilter = `in.(OPEN)`;

    const [closedRaw, liveRaw, dailyReports, weeklyReports, agentWeights] = await Promise.all([
      supabaseGet('trades', {
        select: '*',
        status: closedFilter,
        order: 'created_at.desc',
        limit,
      }),
      Promise.resolve([]),
      supabaseGet('daily_reports', {
        select: '*',
        order: 'report_date.desc',
        limit: 7,
      }).catch(() => []),
      supabaseGet('weekly_reports', {
        select: '*',
        order: 'week_start.desc',
        limit: 4,
      }).catch(() => []),
      supabaseGet('agent_weights', {
        select: '*',
        order: 'agent_name.asc',
        limit: 20,
      }).catch(() => []),
    ]);

    const closedTrades = (closedRaw || []).map(normalizeTrade);
    const liveTrades = (liveRaw || []).map(normalizeTrade);
    const agentPerformance = computeAgentPerformance(closedTrades, agentWeights || []);

    return json(res, 200, {
      ok: true,
      source: 'supabase',
      generatedAt: new Date().toISOString(),
      summary: summarize(closedTrades, liveTrades),
      closedTrades,
      liveTrades,
      dailyReports: (dailyReports && dailyReports.length ? dailyReports.map(r => ({ ...r, report_type: 'daily', month: String(r.report_date || r.created_at || '').slice(0, 7), report_text: r.report_text || buildDailyReport(r) })) : buildGeneratedDailyReports(closedTrades)),
      weeklyReports: (weeklyReports && weeklyReports.length ? weeklyReports.map(r => ({ ...r, report_type: 'weekly', month: String(r.week_start || r.created_at || '').slice(0, 7) })) : buildGeneratedWeeklyReports(closedTrades)),
      agentPerformance,
      agentWeights: agentWeights || [],
    });
  } catch (error) {
    const status = error.code === 'MISSING_ENV' ? 500 : 502;
    return json(res, status, {
      ok: false,
      error: error.message || 'Dashboard API error',
      code: error.code || 'API_ERROR',
      generatedAt: new Date().toISOString(),
    });
  }
};
