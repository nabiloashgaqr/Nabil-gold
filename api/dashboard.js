// Secure Vercel API for SmartSignal dashboard.
// Reads Supabase using server-side environment variables only.
// Required env in Vercel: SUPABASE_URL and SUPABASE_SERVICE_KEY (or SUPABASE_KEY).

const OUTCOME_STATUSES = ['TP2_HIT', 'SL_HIT', 'BE_HIT', 'EXPIRED', 'MANUAL_CLOSE', 'CLOSED'];
const LIVE_STATUSES = ['OPEN', 'TP1_HIT', 'PARTIAL', 'PENDING'];

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
  // Daily realized PnL is grouped by CLOSE date.  If a trade opened yesterday
  // and closed today, it must count in today's dashboard report/chart.
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
    const decisive = wins + losses;
    const net = trades.reduce((s, t) => s + (Number(t.pnl) || 0), 0);
    const winRate = decisive ? (wins / decisive) * 100 : 0;
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
      'Source: Generated live from closed trades grouped by close date.'
    ];
    const textEn = lines.join('\n');
    const textAr = [
      'سمارت سيجنال — التقرير اليومي',
      `التاريخ: ${period}`,
      '────────────────────',
      'الملخص',
      `  الصفقات المغلقة: ${trades.length}`,
      `  رابحة: ${wins} | خاسرة: ${losses} | تعادل: ${be}`,
      `  نسبة الربح: ${winRate.toFixed(1)}%`,
      '',
      'الأداء',
      `  الصافي: ${net >= 0 ? '+' : ''}${net.toFixed(1)} نقطة`,
      '',
      'تفاصيل الصفقات',
      ...trades.slice(0, 20).map(t => `  ${Number(t.pnl) >= 0 ? '[+]' : '[-]'} ${t.type || ''} ${t.symbol || ''} | دخول ${t.entry_price ?? '-'} | ${Number(t.pnl) >= 0 ? '+' : ''}${Number(t.pnl || 0).toFixed(1)} نقطة | ${t.status}`),
      '',
      'المصدر: تقرير مولد من الصفقات المغلقة حسب تاريخ الإغلاق.'
    ].join('\n');
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
      report_text: textEn,
      report_text_en: textEn,
      report_text_ar: textAr,
    };
  });
}
function buildGeneratedDailyReport(closedTrades) {
  return buildGeneratedDailyReports(closedTrades)[0] || {
    id: 'generated-daily-empty', generated: true, report_type: 'daily', report_date: new Date().toISOString().slice(0,10),
    month: new Date().toISOString().slice(0,7), closed_trades: 0, daily_pnl: 0, win_rate: 0,
    created_at: new Date().toISOString(), report_text: 'SmartSignal — Daily Report\nNo closed trades available.', report_text_en: 'SmartSignal — Daily Report\nNo closed trades available.', report_text_ar: 'سمارت سيجنال — التقرير اليومي\nلا توجد صفقات مغلقة.'
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
    const decisive = wins + losses;
    const net = trades.reduce((s, t) => s + (Number(t.pnl) || 0), 0);
    const winRate = decisive ? (wins / decisive) * 100 : 0;
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
      'Source: Generated live from closed trades grouped by close week.',
    ];
    const textEn = lines.join('\n');
    const textAr = [
      'سمارت سيجنال — التقرير الأسبوعي',
      `الأسبوع: ${group.weekStart} → ${group.weekEnd}`,
      '────────────────────',
      'الملخص',
      `  الصفقات المغلقة: ${trades.length}`,
      `  رابحة: ${wins} | خاسرة: ${losses} | تعادل: ${be}`,
      `  نسبة الربح: ${winRate.toFixed(1)}%`,
      '',
      'الأداء',
      `  الصافي: ${net >= 0 ? '+' : ''}${net.toFixed(1)} نقطة`,
      '',
      'حسب الرمز',
      ...Object.entries(bySymbol).map(([sym, pnl]) => `  ${sym}: ${pnl >= 0 ? '+' : ''}${Number(pnl).toFixed(1)} نقطة`),
      '',
      'المصدر: تقرير مولد من الصفقات المغلقة حسب أسبوع الإغلاق.'
    ].join('\n');
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
      report_text: textEn,
      report_text_en: textEn,
      report_text_ar: textAr,
    };
  });
}
function buildGeneratedWeeklyReport(closedTrades) {
  return buildGeneratedWeeklyReports(closedTrades)[0] || {
    id: 'generated-weekly-empty', generated: true, report_type: 'weekly', week_start: new Date().toISOString().slice(0,10),
    week_end: new Date().toISOString().slice(0,10), month: new Date().toISOString().slice(0,7), status: 'GENERATED',
    created_at: new Date().toISOString(), report_text: 'SmartSignal — Weekly Report\nNo closed trades available.', report_text_en: 'SmartSignal — Weekly Report\nNo closed trades available.', report_text_ar: 'سمارت سيجنال — التقرير الأسبوعي\nلا توجد صفقات مغلقة.'
  };
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
  // Fallback weights — must match config.json::agent_weights and utils/helpers.py::get_agent_weights
  const CURRENT_WEIGHTS = {
    technical: 0.20,
    classical: 0.25,
    smc: 0.20,
    price_action: 0.20,
    multitimeframe: 0.15,
  };
  const stats = {};
  function ensure(agent) {
    if (!stats[agent]) {
      // Prefer live DB weight over code fallback so config changes propagate
      const dbEntry = (agentWeights || []).find(a => String(a.agent_name || '').toLowerCase() === agent);
      const dbWeight = dbEntry ? Number(dbEntry.weight ?? 0) : 0;
      const codeWeight = CURRENT_WEIGHTS[agent];
      stats[agent] = {
        agent_name: agent,
        weight: dbWeight || codeWeight || 0,
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
      // Don't let old trade snapshot weights override current authoritative weights
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
    // Win rate excludes breakeven trades (neither win nor loss)
    beCount: total - wins - losses,
    winRate: (wins + losses) ? (wins / (wins + losses)) * 100 : 0,
    netPoints: net,
    profitFactor: gl > 0 ? gp / gl : gp > 0 ? null : 0,
    wins,
    losses,
  };
}

function buildAnalystOverlap(comparisons) {
  const rows = Array.isArray(comparisons) ? comparisons : [];
  const labelRows = rows.filter(r => r && r.analyst_label_id);
  const labelIds = [...new Set(labelRows.map(r => String(r.analyst_label_id)).filter(Boolean))];
  const matched = labelRows.filter(r => String(r.classification || '').toUpperCase() === 'MATCHED');
  const partial = labelRows.filter(r => String(r.classification || '').toUpperCase() === 'PARTIAL_MATCH');
  const missed = labelRows.filter(r => String(r.classification || '').toUpperCase() === 'MISSED_BY_BOT');
  const extra = rows.filter(r => String(r.classification || '').toUpperCase() === 'EXTRA_BOT_SETUP');
  const entryDistances = labelRows
    .map(r => Number((r.payload || {}).entry_distance_points))
    .filter(v => Number.isFinite(v));
  const reasonCounts = {};
  rows.forEach(r => {
    const reason = String(r.reason_code || '').trim();
    if (!reason) return;
    reasonCounts[reason] = (reasonCounts[reason] || 0) + 1;
  });
  const topReasons = Object.entries(reasonCounts)
    .sort((a, b) => (b[1] - a[1]) || String(a[0]).localeCompare(String(b[0])))
    .slice(0, 5)
    .map(([reason_code, count]) => ({ reason_code, count }));
  const labelsConsidered = labelIds.length;
  const coverage = labelsConsidered ? ((matched.length + partial.length) / labelsConsidered) * 100 : 0;
  const match = labelsConsidered ? (matched.length / labelsConsidered) * 100 : 0;
  return {
    labels_considered: labelsConsidered,
    matched_labels: matched.length,
    partial_matches: partial.length,
    missed_labels: missed.length,
    extra_bot_setups: extra.length,
    coverage_rate_pct: Number(coverage.toFixed(1)),
    match_rate_pct: Number(match.toFixed(1)),
    avg_entry_distance_points: entryDistances.length
      ? Number((entryDistances.reduce((a, b) => a + b, 0) / entryDistances.length).toFixed(1))
      : null,
    top_missed_reasons: topReasons,
    comparisons: rows,
  };
}

function buildAnalystOverlap(comparisons) {
  const rows = Array.isArray(comparisons) ? comparisons : [];
  const labelRows = rows.filter(r => r.analyst_label_id);
  const labelIds = [...new Set(labelRows.map(r => String(r.analyst_label_id)).filter(Boolean))];
  const matched = labelRows.filter(r => String(r.classification || '').toUpperCase() === 'MATCHED');
  const partial = labelRows.filter(r => String(r.classification || '').toUpperCase() === 'PARTIAL_MATCH');
  const missed = labelRows.filter(r => String(r.classification || '').toUpperCase() === 'MISSED_BY_BOT');
  const extra = rows.filter(r => String(r.classification || '').toUpperCase() === 'EXTRA_BOT_SETUP');
  const entryDistances = labelRows.map(r => Number((r.payload || {}).entry_distance_points)).filter(v => Number.isFinite(v));
  const reasonCounts = {};
  rows.forEach(r => {
    const reason = String(r.reason_code || '').trim();
    if (!reason) return;
    reasonCounts[reason] = (reasonCounts[reason] || 0) + 1;
  });
  const topReasons = Object.entries(reasonCounts)
    .sort((a, b) => (b[1] - a[1]) || String(a[0]).localeCompare(String(b[0])))
    .slice(0, 5)
    .map(([reason_code, count]) => ({ reason_code, count }));
  const labelsConsidered = labelIds.length;
  const coverage = labelsConsidered ? ((matched.length + partial.length) / labelsConsidered) * 100 : 0;
  const match = labelsConsidered ? (matched.length / labelsConsidered) * 100 : 0;
  return {
    labels_considered: labelsConsidered,
    matched_labels: matched.length,
    partial_matches: partial.length,
    missed_labels: missed.length,
    extra_bot_setups: extra.length,
    coverage_rate_pct: Number(coverage.toFixed(1)),
    match_rate_pct: Number(match.toFixed(1)),
    avg_entry_distance_points: entryDistances.length ? Number((entryDistances.reduce((a,b)=>a+b,0) / entryDistances.length).toFixed(1)) : null,
    top_missed_reasons: topReasons,
    comparisons: rows,
  };
}

module.exports = async function handler(req, res) {
  if (req.method !== 'GET') return json(res, 405, { ok: false, error: 'Method not allowed' });

  try {
    const limit = Math.min(Math.max(parseInt(req.query.limit || '150', 10) || 150, 20), 500);
    const closedFilter = `in.(${OUTCOME_STATUSES.join(',')})`;
    const liveFilter = `in.(${LIVE_STATUSES.join(',')})`;

    const [closedRaw, liveRaw, dailyReports, weeklyReports, agentWeights, analystComparisons] = await Promise.all([
      supabaseGet('trades', {
        select: '*',
        status: closedFilter,
        order: 'closed_at.desc.nullslast,created_at.desc',
        limit,
      }),
      supabaseGet('trades', {
        select: '*',
        status: liveFilter,
        order: 'created_at.desc',
        limit: 50,
      }).catch(() => []),
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
      supabaseGet('analyst_comparisons', {
        select: '*',
        order: 'created_at.desc',
        limit: 120,
      }).catch(() => []),
    ]);

    const closedTrades = (closedRaw || []).map(normalizeTrade);
    const liveTrades = (liveRaw || []).map(normalizeTrade);
    const agentPerformance = computeAgentPerformance(closedTrades, agentWeights || []);
    const analystOverlap = buildAnalystOverlap(analystComparisons || []);

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
      analystOverlap,
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
