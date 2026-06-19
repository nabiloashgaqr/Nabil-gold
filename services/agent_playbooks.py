"""Agent Playbooks v3.0.

Condensed operational rules extracted from the uploaded Arena benchmark/session
file. These playbooks are injected into Groq's final decision prompt so every
agent is judged according to its role and constraints, even when the underlying
classic Python implementation is intentionally lightweight.
"""

from __future__ import annotations

from typing import Dict, List


AGENT_PLAYBOOKS: Dict[str, Dict[str, object]] = {
    "technical": {
        "role": "Technical indicator specialist: RSI, MACD, EMA, ATR, Bollinger, ADX, Stochastic, Ichimoku/Fibonacci when available.",
        "must_check": [
            "RSI-14 and short momentum RSI where available; overbought/oversold and divergences",
            "MACD signal/histogram direction and momentum acceleration/deceleration",
            "EMA 8/21/50/100/200 alignment and price distance from major EMAs",
            "ATR regime and whether volatility is sufficient for SL/TP",
            "Bollinger squeeze/band walk or volatility compression when available",
            "At least three timeframes when data is available",
        ],
        "trade_conditions": [
            "BUY only with positive indicator confluence and no extreme overbought conflict",
            "SELL only with negative indicator confluence and no extreme oversold conflict",
            "WAIT/NO_TRADE if indicators conflict by more than 40% or data is insufficient",
            "Never allow a high-confidence signal from one indicator alone",
        ],
    },
    "classical": {
        "role": "Classical chart pattern and support/resistance architect.",
        "must_check": [
            "Head & Shoulders / Inverse H&S with neckline validation",
            "Double/Triple Top/Bottom with neckline break",
            "Triangles, flags, pennants, rectangles, wedges",
            "Trendlines and channels with touch-count validation",
            "Support/resistance must have at least two historical touches",
            "Role reversal after breakout/retest",
        ],
        "trade_conditions": [
            "Do not force patterns; use NO_CLEAR_PATTERN if unclear",
            "Pattern completion below 75% is FORMING, not COMPLETE",
            "Prioritize higher-timeframe structures",
            "Do not project targets beyond next major S/R without warning",
        ],
    },
    "smc": {
        "role": "Smart Money Concepts and institutional order-flow specialist.",
        "must_check": [
            "Bullish/Bearish Order Blocks with displacement and structure break",
            "Liquidity pools above highs/below lows and equal highs/lows",
            "Liquidity sweeps and reclaim/failure behavior",
            "Fair Value Gaps and mitigation status",
            "Premium/discount zones and market structure BOS/CHoCH",
            "Fresh/tested/mitigated/invalidated status of zones",
        ],
        "trade_conditions": [
            "BUY favored from discount, bullish OB/FVG, liquidity sweep below lows then reclaim",
            "SELL favored from premium, bearish OB/FVG, sweep above highs then rejection",
            "WAIT if price is mid-range or institutional footprint is unclear",
            "Invalidated OB/FVG should not support a trade",
        ],
    },
    "price_action": {
        "role": "Raw price action and Japanese candlestick specialist.",
        "must_check": [
            "Hammer, Inverted Hammer, Hanging Man, Shooting Star",
            "Engulfing, Harami, Piercing, Dark Cloud, Tweezer, Marubozu, Spinning Top, Doji variants",
            "Morning/Evening Star and Three Soldiers/Crows",
            "Breakout, false breakout, rejection, fakey/inside bar context when visible",
            "Pattern location relative to support/resistance and prior trend",
        ],
        "trade_conditions": [
            "Bullish patterns are strongest after downtrend and near support/demand",
            "Bearish patterns are strongest after uptrend and near resistance/supply",
            "Doji/spinning top in mid-range reduces quality",
            "Breakout requires strong close and preferably retest; weak breakouts are WAIT/REJECT",
        ],
    },
    "multitimeframe": {
        "role": "Multi-timeframe alignment officer.",
        "must_check": [
            "Higher timeframe bias first, lower timeframe entry second",
            "4H/1H/15m alignment for intraday gold signals",
            "Conflicts between entry timeframe and trend timeframe",
            "Whether lower timeframe signal is pullback or true reversal",
        ],
        "trade_conditions": [
            "Prefer trades aligned with higher timeframe",
            "Counter-trend trades require substantially higher confidence",
            "WAIT when higher and lower timeframes strongly conflict",
        ],
    },
    "news_risk": {
        "role": "Fundamental news and event risk radar.",
        "must_check": [
            "Central bank decisions, CPI, NFP, jobs data, GDP, PMI, Powell/Fed speeches",
            "High/medium/low impact and currency relevance to gold",
            "Time to event before/after release",
            "High-risk day clustering and Friday/NFP special handling",
        ],
        "trade_conditions": [
            "Tier 1 events create mandatory caution/no-trade windows",
            "Do not trade into major events without explicit risk justification",
            "If AI news allows one direction only, block the opposite direction",
        ],
    },
    "risk_management": {
        "role": "Capital guardian with veto power.",
        "must_check": [
            "Position sizing from account risk and SL distance",
            "ATR-based SL/TP and volatility-adjusted sizing",
            "Minimum R:R, maximum open trades, consecutive loss rules",
            "Daily loss and drawdown constraints",
        ],
        "trade_conditions": [
            "Risk veto cannot be overridden by DecisionAgent",
            "No trade if R:R is below minimum or SL/target structure is invalid",
            "Reduce/stop trading after loss streaks or drawdown limits",
        ],
    },
    "decision": {
        "role": "Final command: synthesize all agents into TRADE or NO_TRADE.",
        "must_check": [
            "Minimum 3 directional agents agreement",
            "Groq AI decision must be available because Groq is mandatory",
            "RiskManagement veto, DailyBias, AI News, Dynamic Risk, Memory Rules, Duplicate Filter",
            "Signal quality grade and confidence threshold",
            "Alternative scenario and invalidation scenario",
        ],
        "trade_conditions": [
            "Never override risk veto",
            "Never issue Grade D/F trades",
            "Grade C trades should be reduced/treated cautiously",
            "If confidence is exactly neutral or evidence conflicts, choose WAIT",
            "Quality over quantity; avoid forcing trades",
        ],
    },
    "open_trades_manager": {
        "role": "Open position pilot and monitoring specialist.",
        "must_check": [
            "Current price vs entry/SL/TP1/TP2",
            "MFE/MAE style movement where available",
            "TP1 partial close and break-even move",
            "Trailing/expiration/long-running conditions",
        ],
        "trade_conditions": [
            "Protect capital after TP1 by moving SL to entry when configured",
            "Notify on TP1, TP2, SL, BE, near target, long-running, expiry",
            "Do not repeat the same informational event repeatedly",
        ],
    },
    "daily_report": {
        "role": "Daily performance historian and improvement analyst.",
        "must_check": [
            "Daily PnL, wins/losses/open trades, win rate, profit factor",
            "Agent performance and learning updates",
            "AI trade reviews and memory rules",
            "Recommendations for next sessions",
        ],
        "trade_conditions": [
            "Report must be honest: include losses, drawdown, and weaknesses",
            "Highlight action items rather than only statistics",
        ],
    },
}


def format_agent_playbooks_for_prompt(max_items_per_agent: int = 4) -> str:
    """Return compact playbook text for Groq DecisionAgent prompt."""
    lines: List[str] = []
    for name, data in AGENT_PLAYBOOKS.items():
        lines.append(f"### {name}: {data['role']}")
        checks = list(data.get("must_check", []))[:max_items_per_agent]
        conditions = list(data.get("trade_conditions", []))[:max_items_per_agent]
        if checks:
            lines.append("Must check: " + "; ".join(str(x) for x in checks))
        if conditions:
            lines.append("Rules: " + "; ".join(str(x) for x in conditions))
    return "\n".join(lines)
