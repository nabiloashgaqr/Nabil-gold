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
function buildGeneratedDailyReport(closedTrades) {
  const today = new Date().toISOString().slice(0, 10);
  let trades = closedTrades.filter(t => reportTradeDate(t) === today);
  let period = today;
  if (!trades.length && closedTrades.length) {
    period = reportTradeDate(closedTrades[0]) || today;
    trades = closedTrades.filter(t => reportTradeDate(t) === period);
  }
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
    ...trades.slice(0, 12).map(t => `  [${Number(t.pnl) >= 0 ? '+' : '-'}] ${t.type || ''} ${t.symbol || ''} | Entry ${t.entry_price ?? '-'} | ${Number(t.pnl) >= 0 ? '+' : ''}${Number(t.pnl || 0).toFixed(1)} pts | ${t.status}`),
    '',
    trades.length ? 'Source: Generated live from closed trades.' : 'No closed trades available for this period.',
  ];
  return { id: 'generated-daily', generated: true, report_date: period, created_at: new Date().toISOString(), report_text: lines.join('\n') };
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
function buildGeneratedWeeklyReport(closedTrades) {
  const today = new Date().toISOString().slice(0, 10);
  let weekStart = getWeekStart(today);
  let weekEnd = addDays(weekStart, 6);
  let trades = closedTrades.filter(t => {
    const d = reportTradeDate(t);
    return d >= weekStart && d <= weekEnd;
  });
  if (!trades.length && closedTrades.length) {
    const base = reportTradeDate(closedTrades[0]) || today;
    weekStart = getWeekStart(base);
    weekEnd = addDays(weekStart, 6);
    trades = closedTrades.filter(t => {
      const d = reportTradeDate(t);
      return d >= weekStart && d <= weekEnd;
    });
  }
  const wins = trades.filter(t => Number(t.pnl) > 0).length;
  const losses = trades.filter(t => Number(t.pnl) < 0).length;
  const be = trades.filter(t => Number(t.pnl) === 0).length;
  const net = trades.reduce((s, t) => s + (Number(t.pnl) || 0), 0);
  const winRate = trades.length ? (wins / trades.length) * 100 : 0;
  const bySymbol = {};
  trades.forEach(t => { bySymbol[t.symbol || 'Unknown'] = (bySymbol[t.symbol || 'Unknown'] || 0) + (Number(t.pnl) || 0); });
  const lines = [
    'SmartSignal — Weekly Report',
    `Week: ${weekStart} → ${weekEnd}`,
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
    trades.length ? 'Source: Generated live from closed trades.' : 'No closed trades available for this week.',
  ];
  return { id: 'generated-weekly', generated: true, week_start: weekStart, week_end: weekEnd, status: 'GENERATED', created_at: new Date().toISOString(), report_text: lines.join('\n') };
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


function extractAgentVotes(trade) {
  const ss = trade.signal_snapshot || {};
  const votes = ss.votes || {};
  const out = [];
  for (const direction of ['BUY', 'SELL']) {
    const arr = Array.isArray(votes[direction]) ? votes[direction] : [];
    for (const v of arr) {
      if (!v || !v.agent) continue;
      out.push({
        agent: String(v.agent).toLowerCase(),
        signal: direction,
        confidence: Number(v.adjusted_confidence ?? v.confidence ?? 0) || 0,
        weight: Number(v.weight ?? 0) || 0,
        score: Number(v.score ?? 0) || 0,
      });
    }
  }
  // Fallback for old / one-agent records if votes were not stored.
  const ctx = ss.agent_context || ss.classic?.strongest_directional;
  if (!out.length && ctx && ctx.agent && ctx.signal && ['BUY', 'SELL'].includes(String(ctx.signal).toUpperCase())) {
    out.push({
      agent: String(ctx.agent).toLowerCase(),
      signal: String(ctx.signal).toUpperCase(),
      confidence: Number(ctx.adjusted_confidence ?? ctx.confidence ?? 0) || 0,
      weight: Number(ctx.weight ?? 0) || 0,
      score: Number(ctx.score ?? 0) || 0,
    });
  }
  return out;
}
function computeAgentPerformance(closedTrades, agentWeights = []) {
  const defaultAgents = ['technical', 'classical', 'smc', 'price_action', 'multitimeframe'];
  const stats = {};
  function ensure(agent) {
    if (!stats[agent]) {
      const w = (agentWeights || []).find(a => String(a.agent_name).toLowerCase() === agent);
      stats[agent] = {
        agent_name: agent,
        weight: Number(w?.weight ?? 0) || 0,
        predictions: 0,
        wins: 0,
        losses: 0,
        net_pnl: 0,
        avg_confidence: 0,
        confidence_sum: 0,
        last_signal: null,
        source: 'computed_from_closed_trades',
      };
    }
    return stats[agent];
  }
  defaultAgents.forEach(ensure);
  closedTrades.forEach(trade => {
    const pnl = Number(trade.pnl ?? trade.final_pnl ?? trade.current_pnl ?? 0) || 0;
    const tradeSide = String(trade.type || trade.side || trade.trade_type || '').toUpperCase();
    const votes = extractAgentVotes(trade);
    votes.forEach(v => {
      const st = ensure(v.agent);
      st.predictions += 1;
      st.confidence_sum += v.confidence;
      st.last_signal = v.signal;
      if (!st.weight && v.weight) st.weight = v.weight;
      // If an agent voted with the executed trade side, profit means correct.
      // If it voted against the executed side, a loss would mean the opposing view was correct.
      const correct = v.signal === tradeSide ? pnl > 0 : pnl < 0;
      if (correct) st.wins += 1;
      else st.losses += 1;
      st.net_pnl += v.signal === tradeSide ? pnl : -pnl;
    });
  });
  return Object.values(stats).map(st => {
    const winRate = st.predictions ? (st.wins / st.predictions) * 100 : null;
    return {
      ...st,
      win_rate: winRate,
      total_predictions: st.predictions,
      avg_confidence: st.predictions ? st.confidence_sum / st.predictions : 0,
      net_pnl: Number(st.net_pnl.toFixed(1)),
      trend: st.predictions < 2 ? 'INSUFFICIENT_DATA' : winRate >= 60 ? 'IMPROVING' : winRate >= 45 ? 'STABLE' : 'DECLINING',
    };
  }).sort((a, b) => (b.predictions - a.predictions) || String(a.agent_name).localeCompare(String(b.agent_name)));
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
      dailyReports: (dailyReports && dailyReports.length ? dailyReports.map(r => ({ ...r, report_text: r.report_text || buildDailyReport(r) })) : [buildGeneratedDailyReport(closedTrades)]),
      weeklyReports: (weeklyReports && weeklyReports.length ? weeklyReports : [buildGeneratedWeeklyReport(closedTrades)]),
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
