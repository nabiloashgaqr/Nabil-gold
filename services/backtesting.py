"""Backtesting utilities for Gold AI Signals.

This first backtesting engine intentionally runs in classic/offline mode by
default. It reuses the existing analytical agents and risk engine, then simulates
TP2/SL/expiry on future candles. AI/Groq is not called by default to avoid high
API usage across many historical candles.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from agents.classical_agent import ClassicalAgent
from agents.decision_agent import DecisionAgent
from agents.multitimeframe_agent import MultiTimeframeAgent
from agents.price_action_agent import PriceActionAgent
from agents.risk_management_agent import RiskManagementAgent
from agents.smc_agent import SMCAgent
from agents.technical_agent import TechnicalAgent

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """One simulated historical trade."""

    id: str
    signal: str
    entry_index: int
    exit_index: int
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    stop_loss: float
    tp1: float
    tp2: float
    confidence: float
    quality_grade: str
    quality_score: float
    result: str
    pnl_points: float
    rr_ratio: float
    reason: str


class BacktestEngine:
    """Run lightweight historical simulation using existing project agents."""

    def __init__(self, config: Dict[str, Any], candles: List[Dict[str, Any]]) -> None:
        self.original_config = config
        self.config = self._backtest_config(config)
        self.candles = candles
        self.logger = logging.getLogger(self.__class__.__name__)

    def _backtest_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        cfg = copy.deepcopy(config)
        # Backtesting many candles with Groq is expensive; use classic mode unless
        # a future explicit AI backtest option is added.
        cfg.setdefault("ai_service", {})["enabled"] = False
        cfg["ai_service"]["fallback_to_classic"] = True
        cfg.setdefault("trading_hours", {})["enabled"] = False
        cfg.setdefault("news_feed", {})["enabled"] = False
        return cfg

    def run(
        self,
        window: int = 160,
        step: int = 12,
        horizon: int = 32,
        max_trades: int = 60,
    ) -> Dict[str, Any]:
        """Run backtest and return a report dictionary."""
        if len(self.candles) < window + horizon + 5:
            raise ValueError(f"Not enough candles for backtest: {len(self.candles)}")

        trades: List[BacktestTrade] = []
        i = window
        while i < len(self.candles) - horizon and len(trades) < max_trades:
            try:
                context = self._build_context(i, window)
                decision = self._make_decision(context)
                signal = str(decision.get("decision", "WAIT")).upper()
                risk = context.get("risk", {}) or {}
                if signal not in {"BUY", "SELL"} or not risk.get("approved", False):
                    i += step
                    continue

                trade = self._simulate_trade(i, horizon, decision, risk, trade_no=len(trades) + 1)
                trades.append(trade)
                # Avoid overlapping simulated trades.
                i = max(trade.exit_index + 1, i + step)
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("Backtest step failed at index %s: %s", i, exc)
                i += step

        return self._report(trades, window=window, step=step, horizon=horizon, max_trades=max_trades)

    def _build_context(self, end_index: int, window: int) -> Dict[str, Any]:
        sample = self.candles[end_index - window : end_index]
        current_price = float(sample[-1]["close"])
        payload = {
            "symbol": self.config.get("symbol", "XAU/USD"),
            "timeframe": self.config.get("primary_timeframe", "15m"),
            "data": sample,
            "timeframes": {
                "5m": {"data": sample[-min(len(sample), 80) :]},
                "15m": {"data": sample},
                "1H": {"data": sample},
                "4H": {"data": sample},
            },
            "current_price": current_price,
            "spread_points": 2.0,
            "source": "backtest",
        }
        results: Dict[str, Any] = {
            "technical": TechnicalAgent(self.config, ai_service=None).analyze(payload),
            "classical": ClassicalAgent(self.config, ai_service=None).analyze(payload),
            "smc": SMCAgent(self.config, ai_service=None).analyze(payload),
            "price_action": PriceActionAgent(self.config, ai_service=None).analyze(payload),
            "multitimeframe": MultiTimeframeAgent(self.config, ai_service=None).analyze(payload),
            "current_price": current_price,
            "spread_points": 2.0,
            "portfolio": {"open_trades_count": 0, "today_signals_count": 0, "consecutive_losses": 0},
            "session": {"trading_allowed": True, "allow_signals": True, "session_quality": "HIGH", "current_session": "Backtest"},
            "news": {"market_status": "SAFE", "can_trade": True, "summary": "backtest_no_news"},
        }
        results["risk"] = RiskManagementAgent(self.config).evaluate(results)
        return results

    def _make_decision(self, context: Dict[str, Any]) -> Dict[str, Any]:
        agent = DecisionAgent(self.config, ai_service=None)
        analysis = agent.analyze(context)
        return agent._to_trade_decision(analysis, context)  # Internal formatter reused intentionally.

    def _simulate_trade(
        self,
        entry_index: int,
        horizon: int,
        decision: Dict[str, Any],
        risk: Dict[str, Any],
        trade_no: int,
    ) -> BacktestTrade:
        signal = str(decision["decision"]).upper()
        entry = float((risk.get("entry", {}) or {}).get("price") or self.candles[entry_index - 1]["close"])
        stop_loss = float((risk.get("stop_loss", {}) or {}).get("price") or 0)
        tp = risk.get("take_profit", {}) or {}
        tp1 = float((tp.get("tp1", {}) or {}).get("price") or 0)
        tp2 = float((tp.get("tp2", {}) or {}).get("price") or 0)
        rr = float((tp.get("tp2", {}) or {}).get("rr_ratio") or 0)

        exit_index = min(entry_index + horizon, len(self.candles) - 1)
        exit_price = float(self.candles[exit_index]["close"])
        result = "EXPIRED"
        reason = "expired_before_tp_or_sl"

        for j in range(entry_index, min(entry_index + horizon, len(self.candles))):
            candle = self.candles[j]
            high = float(candle["high"])
            low = float(candle["low"])
            # Conservative same-candle assumption: SL before TP2.
            if signal == "BUY":
                if stop_loss and low <= stop_loss:
                    exit_index, exit_price, result, reason = j, stop_loss, "SL_HIT", "stop_loss_hit"
                    break
                if tp2 and high >= tp2:
                    exit_index, exit_price, result, reason = j, tp2, "TP2_HIT", "take_profit_2_hit"
                    break
            else:
                if stop_loss and high >= stop_loss:
                    exit_index, exit_price, result, reason = j, stop_loss, "SL_HIT", "stop_loss_hit"
                    break
                if tp2 and low <= tp2:
                    exit_index, exit_price, result, reason = j, tp2, "TP2_HIT", "take_profit_2_hit"
                    break

        pnl = exit_price - entry if signal == "BUY" else entry - exit_price
        quality = decision.get("quality", {}) or {}
        return BacktestTrade(
            id=f"BT_{trade_no:04d}",
            signal=signal,
            entry_index=entry_index,
            exit_index=exit_index,
            entry_time=str(self.candles[entry_index - 1].get("time", entry_index)),
            exit_time=str(self.candles[exit_index].get("time", exit_index)),
            entry_price=round(entry, 2),
            exit_price=round(exit_price, 2),
            stop_loss=round(stop_loss, 2),
            tp1=round(tp1, 2),
            tp2=round(tp2, 2),
            confidence=float(decision.get("confidence") or 0),
            quality_grade=str(quality.get("grade", "N/A")),
            quality_score=float(quality.get("score") or 0),
            result=result,
            pnl_points=round(pnl, 2),
            rr_ratio=round(rr, 2),
            reason=reason,
        )

    def _report(self, trades: List[BacktestTrade], **params: Any) -> Dict[str, Any]:
        wins = [t for t in trades if t.pnl_points > 0]
        losses = [t for t in trades if t.pnl_points < 0]
        gross_profit = sum(t.pnl_points for t in wins)
        gross_loss = abs(sum(t.pnl_points for t in losses))
        net = sum(t.pnl_points for t in trades)
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for trade in trades:
            equity += trade.pnl_points
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        by_signal = {
            side: {
                "count": len([t for t in trades if t.signal == side]),
                "net_points": round(sum(t.pnl_points for t in trades if t.signal == side), 2),
            }
            for side in ["BUY", "SELL"]
        }
        report = {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "symbol": self.config.get("symbol", "XAU/USD"),
            "candles": len(self.candles),
            "params": params,
            "summary": {
                "total_trades": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round((len(wins) / len(trades) * 100) if trades else 0, 2),
                "net_points": round(net, 2),
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
                "profit_factor": round((gross_profit / gross_loss) if gross_loss else 0, 2),
                "max_drawdown_points": round(max_dd, 2),
                "avg_quality_score": round(sum(t.quality_score for t in trades) / len(trades), 2) if trades else 0,
                "by_signal": by_signal,
            },
            "trades": [asdict(t) for t in trades],
        }
        return report


def save_backtest_report(report: Dict[str, Any], path: str | Path = "storage/backtest_report.json") -> Path:
    """Save report as JSON and return path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def format_backtest_telegram(report: Dict[str, Any]) -> str:
    """Format a compact Arabic Telegram summary."""
    s = report.get("summary", {})
    by_signal = s.get("by_signal", {})
    return "\n".join(
        [
            "🧪 <b>Backtesting Report - XAU/USD</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 الصفقات: {s.get('total_trades', 0)}",
            f"✅ الرابحة: {s.get('wins', 0)} | ❌ الخاسرة: {s.get('losses', 0)}",
            f"📈 Win Rate: {s.get('win_rate', 0)}%",
            f"💰 Net Points: {s.get('net_points', 0):+}",
            f"⚖️ Profit Factor: {s.get('profit_factor', 0)}",
            f"📉 Max DD: {s.get('max_drawdown_points', 0)} points",
            f"⭐ Avg Quality: {s.get('avg_quality_score', 0)}%",
            "",
            f"BUY: {by_signal.get('BUY', {}).get('count', 0)} | Net {by_signal.get('BUY', {}).get('net_points', 0):+}",
            f"SELL: {by_signal.get('SELL', {}).get('count', 0)} | Net {by_signal.get('SELL', {}).get('net_points', 0):+}",
            "━━━━━━━━━━━━━━━━━━━━",
            "ملاحظة: الاختبار التاريخي Classic/offline ولا يستخدم Groq افتراضياً لتجنب استهلاك API.",
            "إذا كان عدد الصفقات 0 فهذا يعني أن الشروط الحالية لم تنتج إشارات مؤهلة على العينة المختبرة.",
        ]
    )
