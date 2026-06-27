// Secure Vercel API for SmartSignal dashboard.
// Reads Supabase using server-side environment variables only.
// Required env in Vercel: SUPABASE_URL and SUPABASE_SERVICE_KEY (or SUPABASE_KEY).

const CLOSED_EXCLUDED = ['OPEN', 'PARTIAL', 'TP1_HIT', 'PENDING'];
const LIVE_STATUSES = ['OPEN', 'PARTIAL', 'TP1_HIT', 'PENDING'];

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

function normalizeTrade(t) {
  const status = String(t.status || 'UNKNOWN').toUpperCase();
  const pnl = Number(t.final_pnl ?? t.current_pnl_points ?? t.current_pnl ?? t.pnl ?? 0) || 0;
  return {
    ...t,
    id: t.id || '',
    symbol: t.symbol || 'XAU/USD',
    type: String(t.type || t.side || t.trade_type || '').toUpperCase(),
    status,
    pnl,
    created_at: t.created_at || t.entry_time || t.opened_at || t.updated_at || '',
    closed_at: t.closed_at || t.close_time || '',
  };
}

function buildDailyReport(r) {
  if (!r) return null;
  const date = r.report_date || (r.created_at || '').slice(0, 10) || '-';
  const total = Number(r.closed_trades ?? r.new_trades ?? r.total_signals ?? 0) || 0;
  const wins = Number(r.winning_trades ?? 0) || 0;
  const losses = Number(r.losing_trades ?? 0) || 0;
  const pnl = Number(r.daily_pnl ?? 0) || 0;
  const wr = Number(r.win_rate ?? (total ? (wins / total) * 100 : 0)) || 0;
  return [
    `SmartSignal — Daily Report`,
    `Date: ${date}`,
    `────────────────────`,
    `Closed Trades: ${total}`,
    `Wins: ${wins} | Losses: ${losses}`,
    `Win Rate: ${wr.toFixed(1)}%`,
    `Daily PnL: ${pnl >= 0 ? '+' : ''}${pnl.toFixed(0)} pts`,
    r.market_summary ? `\nMarket: ${r.market_summary}` : '',
    r.technical_summary ? `Technical: ${r.technical_summary}` : '',
    r.recommendations ? `Recommendations: ${r.recommendations}` : '',
  ].filter(Boolean).join('\n');
}

function summarize(closedTrades, liveTrades) {
  const total = closedTrades.length;
  const wins = closedTrades.filter(t => Number(t.pnl) > 0 || ['TP2_HIT', 'TP1_HIT'].includes(t.status)).length;
  const losses = closedTrades.filter(t => Number(t.pnl) < 0 || t.status === 'SL_HIT').length;
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
    const closedFilter = `not.in.(${CLOSED_EXCLUDED.join(',')})`;
    const liveFilter = `in.(${LIVE_STATUSES.join(',')})`;

    const [closedRaw, liveRaw, dailyReports, weeklyReports, agentWeights] = await Promise.all([
      supabaseGet('trades', {
        select: '*',
        status: closedFilter,
        order: 'created_at.desc',
        limit,
      }),
      supabaseGet('trades', {
        select: '*',
        status: liveFilter,
        order: 'created_at.desc',
        limit: 80,
      }),
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

    return json(res, 200, {
      ok: true,
      source: 'supabase',
      generatedAt: new Date().toISOString(),
      summary: summarize(closedTrades, liveTrades),
      closedTrades,
      liveTrades,
      dailyReports: (dailyReports || []).map(r => ({ ...r, report_text: r.report_text || buildDailyReport(r) })),
      weeklyReports: weeklyReports || [],
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
