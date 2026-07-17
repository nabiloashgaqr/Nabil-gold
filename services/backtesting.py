"""Backtesting utilities for Gold AI Signals.

Runs the existing analytical agents and risk engine, then simulates TP2/SL/expiry
on future candles.
"""

from __future__ import annotations

import copy
import csv
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from utils.sessions import session_label_from_utc

from agents.classical_agent import ClassicalAgent
from agents.decision_agent import DecisionAgent
from agents.multitimeframe_agent import MultiTimeframeAgent
from agents.price_action_agent import PriceActionAgent
from agents.risk_management_agent import RiskManagementAgent
from agents.smc_agent import SMCAgent
from agents.technical_agent import TechnicalAgent
from services.session_planner import SessionPlannerService

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """One simulated historical trade or pending candidate."""

    id: str
    variant: str
    signal: str
    profile_name: str
    setup_type: str
    lead_agent: str
    management_profile: str
    poi_type: str
    trigger_state: str
    trigger_score: float
    selection_role: str
    session_plan_ready: bool
    planner_confidence: float
    scenario_id: str
    standby_available: bool
    return_probability_score: float
    thesis_dominance_score: float
    expected_revisit_window: str
    entry_kind: str
    order_type: str
    filled: bool
    entry_index: int
    fill_index: int
    exit_index: int
    entry_time: str
    fill_time: str
    exit_time: str
    session_label: str
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

    def __init__(self, config: Dict[str, Any], candles: List[Dict[str, Any]], variant_name: str = "current_engine") -> None:
        self.original_config = config
        self.variant_name = variant_name
        self.config = self._backtest_config(config, variant_name=variant_name)
        self.candles = candles
        self.logger = logging.getLogger(self.__class__.__name__)

    def _backtest_config(self, config: Dict[str, Any], variant_name: str = "current_engine") -> Dict[str, Any]:
        cfg = copy.deepcopy(config)
        cfg.setdefault("trading_hours", {})["enabled"] = False
        cfg.setdefault("news_feed", {})["enabled"] = False
        if variant_name == "baseline_classic_market":
            cfg["strategy_profiles_enabled"] = False
            cfg.setdefault("learning", {})["contextual_weights_enabled"] = False
            cfg.setdefault("order_execution", {})["entry_style"] = "market"
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
        plan_stats: Dict[str, Any] = {
            "windows_analyzed": 0,
            "plans_ready": 0,
            "standby_ready": 0,
            "blocked_reasons": {},
            "scenario_types": {},
        }
        i = window
        while i < len(self.candles) - horizon and len(trades) < max_trades:
            try:
                context = self._build_context(i, window)
                self._observe_plan(context.get("session_plan", {}), plan_stats)
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

        return self._report(trades, planning=plan_stats, window=window, step=step, horizon=horizon, max_trades=max_trades)

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
            "technical": TechnicalAgent(self.config).analyze(payload),
            "classical": ClassicalAgent(self.config).analyze(payload),
            "smc": SMCAgent(self.config).analyze(payload),
            "price_action": PriceActionAgent(self.config).analyze(payload),
            "multitimeframe": MultiTimeframeAgent(self.config).analyze(payload),
            "current_price": current_price,
            "spread_points": 2.0,
            "portfolio": {"open_trades_count": 0, "today_signals_count": 0, "consecutive_losses": 0},
            "session": {"trading_allowed": True, "allow_signals": True, "session_quality": "HIGH", "current_session": "Backtest"},
            "news": {"market_status": "SAFE", "can_trade": True, "summary": "backtest_no_news"},
        }
        try:
            results["session_plan"] = SessionPlannerService(self.config).build_plan(results, persist=False)
        except Exception:  # noqa: BLE001
            results["session_plan"] = {"enabled": True, "plan_ready": False, "plan_status": "ERROR", "plan_reason": "planner_failed"}
        results["risk"] = RiskManagementAgent(self.config).evaluate(results)
        return results

    def _make_decision(self, context: Dict[str, Any]) -> Dict[str, Any]:
        agent = DecisionAgent(self.config)
        analysis = agent.analyze(context)
        decision = agent._to_trade_decision(analysis, context)  # Internal formatter reused intentionally.
        decision["session_plan"] = context.get("session_plan", {})
        return decision

    def _simulate_trade(
        self,
        entry_index: int,
        horizon: int,
        decision: Dict[str, Any],
        risk: Dict[str, Any],
        trade_no: int,
    ) -> BacktestTrade:
        signal = str(decision["decision"]).upper()
        entry_info = risk.get("entry", {}) or {}
        entry = float(entry_info.get("price") or self.candles[entry_index - 1]["close"])
        order_type = str(entry_info.get("order_type") or f"{signal}_MARKET").upper()
        entry_kind = str(entry_info.get("kind") or ("MARKET" if order_type.endswith("MARKET") else order_type.split("_")[-1])).upper()
        stop_loss = float((risk.get("stop_loss", {}) or {}).get("price") or 0)
        tp = risk.get("take_profit", {}) or {}
        tp1 = float((tp.get("tp1", {}) or {}).get("price") or 0)
        tp2 = float((tp.get("tp2", {}) or {}).get("price") or 0)
        rr = float((tp.get("tp2", {}) or {}).get("rr_ratio") or 0)
        profile = (decision.get("strategy_profile") or {}).get("name") or "classic_consensus"
        setup_context = decision.get("setup_context") or {}
        quality = decision.get("quality", {}) or {}
        plan = decision.get("session_plan") or {}
        selection_role = str(setup_context.get("selection_role") or "UNSPECIFIED")
        session_plan_ready = bool(plan.get("plan_ready", False))
        planner_confidence = float(plan.get("planner_confidence") or 0)
        scenario_id = str(plan.get("scenario_id") or setup_context.get("scenario_id") or "")
        standby_available = bool((plan.get("standby_poi") if isinstance(plan, dict) else None))
        return_probability_score = float(setup_context.get("return_probability_score") or 0)
        thesis_dominance_score = float(setup_context.get("thesis_dominance_score") or 0)
        expected_revisit_window = str(setup_context.get("expected_revisit_window") or "UNKNOWN")

        fill_index = entry_index
        fill_time = str(self.candles[entry_index - 1].get("time", entry_index))
        filled = order_type.endswith("MARKET")
        if not filled:
            for j in range(entry_index, min(entry_index + horizon, len(self.candles))):
                candle = self.candles[j]
                high = float(candle["high"])
                low = float(candle["low"])
                if self._pending_filled(order_type, signal, entry, high, low):
                    fill_index = j
                    fill_time = str(candle.get("time", j))
                    filled = True
                    break
        if not filled:
            pending_exit_index = min(entry_index + horizon, len(self.candles) - 1)
            return BacktestTrade(
                id=f"BT_{trade_no:04d}",
                variant=self.variant_name,
                signal=signal,
                profile_name=str(profile),
                setup_type=str(setup_context.get("setup_type") or "UNKNOWN"),
                lead_agent=str(setup_context.get("lead_agent") or ""),
                management_profile=str(risk.get("management_profile") or "default_profile"),
                poi_type=str(setup_context.get("poi_type") or ""),
                trigger_state=str(setup_context.get("trigger_state") or ""),
                trigger_score=float(setup_context.get("trigger_score") or 0),
                selection_role=selection_role,
                session_plan_ready=session_plan_ready,
                planner_confidence=planner_confidence,
                scenario_id=scenario_id,
                standby_available=standby_available,
                return_probability_score=return_probability_score,
                thesis_dominance_score=thesis_dominance_score,
                expected_revisit_window=expected_revisit_window,
                entry_kind=entry_kind,
                order_type=order_type,
                filled=False,
                entry_index=entry_index,
                fill_index=-1,
                exit_index=pending_exit_index,
                entry_time=str(self.candles[entry_index - 1].get("time", entry_index)),
                fill_time="",
                exit_time=str(self.candles[pending_exit_index].get("time", pending_exit_index)),
                session_label=session_label_from_utc(self.candles[entry_index - 1].get("time")),
                entry_price=round(entry, 2),
                exit_price=round(entry, 2),
                stop_loss=round(stop_loss, 2),
                tp1=round(tp1, 2),
                tp2=round(tp2, 2),
                confidence=float(decision.get("confidence") or 0),
                quality_grade=str(quality.get("grade", "N/A")),
                quality_score=float(quality.get("score") or 0),
                result="NOT_FILLED",
                pnl_points=0.0,
                rr_ratio=round(rr, 2),
                reason="pending_not_filled_within_horizon",
            )

        exit_index = min(fill_index + horizon, len(self.candles) - 1)
        exit_price = float(self.candles[exit_index]["close"])
        result = "EXPIRED"
        reason = "expired_before_tp_or_sl"

        for j in range(fill_index, min(fill_index + horizon, len(self.candles))):
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
        return BacktestTrade(
            id=f"BT_{trade_no:04d}",
            variant=self.variant_name,
            signal=signal,
            profile_name=str(profile),
            setup_type=str(setup_context.get("setup_type") or "UNKNOWN"),
            lead_agent=str(setup_context.get("lead_agent") or ""),
            management_profile=str(risk.get("management_profile") or "default_profile"),
            poi_type=str(setup_context.get("poi_type") or ""),
            trigger_state=str(setup_context.get("trigger_state") or ""),
            trigger_score=float(setup_context.get("trigger_score") or 0),
            selection_role=selection_role,
            session_plan_ready=session_plan_ready,
            planner_confidence=planner_confidence,
            scenario_id=scenario_id,
            standby_available=standby_available,
            return_probability_score=return_probability_score,
            thesis_dominance_score=thesis_dominance_score,
            expected_revisit_window=expected_revisit_window,
            entry_kind=entry_kind,
            order_type=order_type,
            filled=True,
            entry_index=entry_index,
            fill_index=fill_index,
            exit_index=exit_index,
            entry_time=str(self.candles[entry_index - 1].get("time", entry_index)),
            fill_time=fill_time,
            exit_time=str(self.candles[exit_index].get("time", exit_index)),
            session_label=session_label_from_utc(self.candles[entry_index - 1].get("time")),
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

    def _pending_filled(self, order_type: str, signal: str, entry: float, high: float, low: float) -> bool:
        order_type = str(order_type or "").upper()
        if order_type.endswith("MARKET"):
            return True
        if order_type == "BUY_LIMIT":
            return low <= entry
        if order_type == "SELL_LIMIT":
            return high >= entry
        if order_type == "BUY_STOP":
            return high >= entry
        if order_type == "SELL_STOP":
            return low <= entry
        if signal == "BUY":
            return low <= entry
        return high >= entry

    def _observe_plan(self, plan: Dict[str, Any], stats: Dict[str, Any]) -> None:
        stats["windows_analyzed"] = int(stats.get("windows_analyzed", 0) or 0) + 1
        if not isinstance(plan, dict):
            reason = "planner_missing"
            stats.setdefault("blocked_reasons", {})[reason] = stats.setdefault("blocked_reasons", {}).get(reason, 0) + 1
            return
        if plan.get("plan_ready"):
            stats["plans_ready"] = int(stats.get("plans_ready", 0) or 0) + 1
            if plan.get("standby_poi"):
                stats["standby_ready"] = int(stats.get("standby_ready", 0) or 0) + 1
            scenario = str(plan.get("scenario_type") or "UNKNOWN")
            stats.setdefault("scenario_types", {})[scenario] = stats.setdefault("scenario_types", {}).get(scenario, 0) + 1
            return
        reason = str(plan.get("plan_reason") or plan.get("plan_status") or "not_ready")
        stats.setdefault("blocked_reasons", {})[reason] = stats.setdefault("blocked_reasons", {}).get(reason, 0) + 1

    def _bucket_breakdown(self, trades: List[BacktestTrade], attr: str) -> Dict[str, Dict[str, Any]]:
        buckets: Dict[str, Dict[str, Any]] = {}
        for trade in trades:
            key = str(getattr(trade, attr) or "UNKNOWN")
            bucket = buckets.setdefault(key, {"count": 0, "filled": 0, "wins": 0, "losses": 0, "not_filled": 0, "net_points": 0.0})
            bucket["count"] += 1
            bucket["net_points"] += float(trade.pnl_points)
            if trade.filled:
                bucket["filled"] += 1
                if trade.pnl_points > 0:
                    bucket["wins"] += 1
                elif trade.pnl_points < 0:
                    bucket["losses"] += 1
            else:
                bucket["not_filled"] += 1
        for bucket in buckets.values():
            decisive = bucket["wins"] + bucket["losses"]
            bucket["win_rate"] = round((bucket["wins"] / decisive * 100) if decisive else 0.0, 2)
            bucket["net_points"] = round(bucket["net_points"], 2)
        return buckets

    def _report(self, trades: List[BacktestTrade], **params: Any) -> Dict[str, Any]:
        filled = [t for t in trades if t.filled]
        wins = [t for t in filled if t.pnl_points > 0]
        losses = [t for t in filled if t.pnl_points < 0]
        not_filled = [t for t in trades if not t.filled]
        gross_profit = sum(t.pnl_points for t in wins)
        gross_loss = abs(sum(t.pnl_points for t in losses))
        net = sum(t.pnl_points for t in filled)
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for trade in filled:
            equity += trade.pnl_points
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        by_signal = {
            side: {
                "count": len([t for t in trades if t.signal == side]),
                "filled": len([t for t in filled if t.signal == side]),
                "net_points": round(sum(t.pnl_points for t in filled if t.signal == side), 2),
            }
            for side in ["BUY", "SELL"]
        }
        by_grade: Dict[str, Dict[str, Any]] = {}
        for grade in sorted({t.quality_grade for t in trades}):
            subset = [t for t in trades if t.quality_grade == grade]
            by_grade[grade] = {
                "count": len(subset),
                "wins": len([t for t in subset if t.pnl_points > 0]),
                "not_filled": len([t for t in subset if not t.filled]),
                "net_points": round(sum(t.pnl_points for t in subset), 2),
            }
        primary = [t for t in trades if str(t.selection_role).upper() == "PRIMARY"]
        standby = [t for t in trades if str(t.selection_role).upper() == "STANDBY"]
        primary_filled = [t for t in primary if t.filled]
        standby_filled = [t for t in standby if t.filled]
        planning = dict(params.get("planning") or {})
        windows_analyzed = int(planning.get("windows_analyzed", 0) or 0)
        plans_ready = int(planning.get("plans_ready", 0) or 0)
        standby_ready = int(planning.get("standby_ready", 0) or 0)
        plan_ready_rate_pct = round((plans_ready / windows_analyzed * 100) if windows_analyzed else 0, 2)
        standby_ready_rate_pct = round((standby_ready / plans_ready * 100) if plans_ready else 0, 2)
        report = {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "variant": self.variant_name,
            "symbol": self.config.get("symbol", "XAU/USD"),
            "candles": len(self.candles),
            "params": params,
            "summary": {
                "total_candidates": len(trades),
                "total_trades": len(filled),
                "not_filled": len(not_filled),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round((len(wins) / len(filled) * 100) if filled else 0, 2),
                "net_points": round(net, 2),
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
                "profit_factor": round((gross_profit / gross_loss) if gross_loss else 0, 2),
                "max_drawdown_points": round(max_dd, 2),
                "avg_quality_score": round(sum(t.quality_score for t in trades) / len(trades), 2) if trades else 0,
                "avg_trigger_score": round(sum(t.trigger_score for t in trades) / len(trades), 2) if trades else 0,
                "avg_return_probability_score": round(sum(t.return_probability_score for t in trades) / len(trades), 2) if trades else 0,
                "avg_thesis_dominance_score": round(sum(t.thesis_dominance_score for t in trades) / len(trades), 2) if trades else 0,
                "plan_ready_rate_pct": plan_ready_rate_pct,
                "standby_ready_rate_pct": standby_ready_rate_pct,
                "primary_fill_rate_pct": round((len(primary_filled) / len(primary) * 100) if primary else 0, 2),
                "primary_win_rate_pct": round((len([t for t in primary_filled if t.pnl_points > 0]) / len(primary_filled) * 100) if primary_filled else 0, 2),
                "standby_fill_rate_pct": round((len(standby_filled) / len(standby) * 100) if standby else 0, 2),
                "planning": {
                    "windows_analyzed": windows_analyzed,
                    "plans_ready": plans_ready,
                    "standby_ready": standby_ready,
                    "plan_ready_rate_pct": plan_ready_rate_pct,
                    "standby_ready_rate_pct": standby_ready_rate_pct,
                    "blocked_reasons": planning.get("blocked_reasons", {}),
                    "scenario_types": planning.get("scenario_types", {}),
                },
                "pending_governance": {
                    "primary_candidates": len(primary),
                    "standby_candidates": len(standby),
                    "rejected_candidates": len([t for t in trades if str(t.selection_role).upper() == "REJECTED"]),
                    "primary_fill_rate_pct": round((len(primary_filled) / len(primary) * 100) if primary else 0, 2),
                    "standby_fill_rate_pct": round((len(standby_filled) / len(standby) * 100) if standby else 0, 2),
                    "avg_primary_dominance": round(sum(t.thesis_dominance_score for t in primary) / len(primary), 2) if primary else 0,
                    "avg_standby_dominance": round(sum(t.thesis_dominance_score for t in standby) / len(standby), 2) if standby else 0,
                },
                "by_signal": by_signal,
                "by_grade": by_grade,
                "by_setup_type": self._bucket_breakdown(trades, "setup_type"),
                "by_profile": self._bucket_breakdown(trades, "profile_name"),
                "by_management_profile": self._bucket_breakdown(trades, "management_profile"),
                "by_trigger_state": self._bucket_breakdown(trades, "trigger_state"),
                "by_session": self._bucket_breakdown(trades, "session_label"),
                "by_entry_kind": self._bucket_breakdown(trades, "entry_kind"),
                "by_selection_role": self._bucket_breakdown(trades, "selection_role"),
                "by_revisit_window": self._bucket_breakdown(trades, "expected_revisit_window"),
            },
            "trades": [asdict(t) for t in trades],
        }
        return report


def benchmark_backtests(
    config: Dict[str, Any],
    candles: List[Dict[str, Any]],
    *,
    window: int = 160,
    step: int = 12,
    horizon: int = 32,
    max_trades: int = 60,
) -> Dict[str, Any]:
    """Run current engine vs a conservative baseline and compare deltas."""
    variants = {
        "current_engine": BacktestEngine(config, candles, variant_name="current_engine").run(
            window=window, step=step, horizon=horizon, max_trades=max_trades
        ),
        "baseline_classic_market": BacktestEngine(config, candles, variant_name="baseline_classic_market").run(
            window=window, step=step, horizon=horizon, max_trades=max_trades
        ),
    }
    current = variants["current_engine"].get("summary", {})
    baseline = variants["baseline_classic_market"].get("summary", {})
    comparison = {
        "win_rate_delta": round(float(current.get("win_rate", 0)) - float(baseline.get("win_rate", 0)), 2),
        "net_points_delta": round(float(current.get("net_points", 0)) - float(baseline.get("net_points", 0)), 2),
        "profit_factor_delta": round(float(current.get("profit_factor", 0)) - float(baseline.get("profit_factor", 0)), 2),
        "filled_trades_delta": int(current.get("total_trades", 0) or 0) - int(baseline.get("total_trades", 0) or 0),
        "not_filled_delta": int(current.get("not_filled", 0) or 0) - int(baseline.get("not_filled", 0) or 0),
        "primary_fill_rate_delta": round(float(current.get("primary_fill_rate_pct", 0)) - float(baseline.get("primary_fill_rate_pct", 0)), 2),
        "avg_dominance_delta": round(float(current.get("avg_thesis_dominance_score", 0)) - float(baseline.get("avg_thesis_dominance_score", 0)), 2),
        "plan_ready_rate_delta": round(float(current.get("plan_ready_rate_pct", 0)) - float(baseline.get("plan_ready_rate_pct", 0)), 2),
        "standby_ready_rate_delta": round(float(current.get("standby_ready_rate_pct", 0)) - float(baseline.get("standby_ready_rate_pct", 0)), 2),
    }
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": config.get("symbol", "XAU/USD"),
        "params": {"window": window, "step": step, "horizon": horizon, "max_trades": max_trades},
        "variants": variants,
        "comparison": comparison,
    }


def save_backtest_report(report: Dict[str, Any], path: str | Path = "storage/backtest_report.json") -> Path:
    """Save report as JSON and return path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target



def save_backtest_csv(report: Dict[str, Any], path: str | Path = "storage/backtest_trades.csv") -> Path:
    """Save backtest trades as CSV."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    trades = report.get("trades", []) or []
    fieldnames = [
        "id", "signal", "entry_time", "exit_time", "entry_price", "exit_price",
        "stop_loss", "tp1", "tp2", "confidence", "quality_grade", "quality_score",
        "result", "pnl_points", "rr_ratio", "reason",
    ]
    with target.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for trade in trades:
            writer.writerow({key: trade.get(key) for key in fieldnames})
    return target

def format_backtest_telegram(report: Dict[str, Any]) -> str:
    """Format a compact Arabic Telegram summary."""
    if report.get("variants"):
        current = (report.get("variants", {}) or {}).get("current_engine", {}).get("summary", {})
        baseline = (report.get("variants", {}) or {}).get("baseline_classic_market", {}).get("summary", {})
        cmp = report.get("comparison", {}) or {}
        return "\n".join(
            [
                "🧪 <b>Backtest Benchmark - XAU/USD</b>",
                "━━━━━━━━━━━━━━━━━━━━",
                f"Current: {current.get('total_trades', 0)} filled / {current.get('not_filled', 0)} not-filled | WR {current.get('win_rate', 0)}% | Net {float(current.get('net_points', 0)):+.0f}",
                f"Baseline: {baseline.get('total_trades', 0)} filled / {baseline.get('not_filled', 0)} not-filled | WR {baseline.get('win_rate', 0)}% | Net {float(baseline.get('net_points', 0)):+.0f}",
                f"Δ Win Rate: {float(cmp.get('win_rate_delta', 0)):+.2f}%",
                f"Δ Net Points: {float(cmp.get('net_points_delta', 0)):+.0f}",
                f"Δ Profit Factor: {float(cmp.get('profit_factor_delta', 0)):+.2f}",
                f"Δ Filled Trades: {int(cmp.get('filled_trades_delta', 0)):+d} | Δ Not-filled: {int(cmp.get('not_filled_delta', 0)):+d}",
                f"Δ Primary Fill Rate: {float(cmp.get('primary_fill_rate_delta', 0)):+.2f}% | Δ Dominance: {float(cmp.get('avg_dominance_delta', 0)):+.2f}",
                f"Δ Plan Ready: {float(cmp.get('plan_ready_rate_delta', 0)):+.2f}% | Δ Standby Ready: {float(cmp.get('standby_ready_rate_delta', 0)):+.2f}%",
                "━━━━━━━━━━━━━━━━━━━━",
                "Baseline = classic consensus + market execution only.",
            ]
        )
    s = report.get("summary", {})
    by_signal = s.get("by_signal", {})
    return "\n".join(
        [
            "🧪 <b>Backtesting Report - XAU/USD</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 Filled Trades: {s.get('total_trades', 0)} | Pending Expired: {s.get('not_filled', 0)}",
            f"✅ Wins: {s.get('wins', 0)} | ❌ Losses: {s.get('losses', 0)}",
            f"📈 Win Rate: {s.get('win_rate', 0)}%",
            f"💰 Net Points: {float(s.get('net_points', 0)):+.0f}",
            f"⚖️ Profit Factor: {s.get('profit_factor', 0)}",
            f"📉 Max DD: {s.get('max_drawdown_points', 0)} points",
            f"⭐ Avg Quality: {s.get('avg_quality_score', 0)}% | Trigger {s.get('avg_trigger_score', 0)}",
            f"🗺️ Plan Ready: {s.get('plan_ready_rate_pct', 0)}% | Standby Ready: {s.get('standby_ready_rate_pct', 0)}%",
            "",
            f"BUY: {by_signal.get('BUY', {}).get('filled', 0)} filled | Net {float(by_signal.get('BUY', {}).get('net_points', 0)):+.0f}",
            f"SELL: {by_signal.get('SELL', {}).get('filled', 0)} filled | Net {float(by_signal.get('SELL', {}).get('net_points', 0)):+.0f}",
            f"Setups: {report.get('summary', {}).get('by_setup_type', {})}",
            "━━━━━━━━━━━━━━━━━━━━",
            "If the trade count is 0, the current conditions produced no qualified signals on the tested sample.",
        ]
    )
