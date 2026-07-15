"""
📊 لوحة الأداء - Gold AI Signals
تقيس Win Rate لكل وكيل + أداء حسب الجلسة + تنبيهات Drawdown
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

from services.analyst_distillation import AnalystDistillationService
from utils.sessions import session_label_from_trade

logger = logging.getLogger(__name__)

class AlertLevel(Enum):
    """مستويات التنبيه"""
    INFO = "ℹ️"
    WARNING = "⚠️"
    CRITICAL = "🚨"

@dataclass
class AgentPerformance:
    """أداء الوكيل"""
    agent_name: str
    total_signals: int = 0
    buy_signals: int = 0
    sell_signals: int = 0
    wait_signals: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_confidence: float = 0.0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    total_pnl: float = 0.0
    
    def calculate_win_rate(self) -> float:
        total_trades = self.winning_trades + self.losing_trades
        if total_trades == 0:
            return 0.0
        return (self.winning_trades / total_trades) * 100

@dataclass
class SessionPerformance:
    """أداء الجلسة"""
    session_name: str
    quality: str
    total_signals: int = 0
    avg_confidence: float = 0.0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    trades_count: int = 0

@dataclass
class DrawdownAlert:
    """تنبيه السحب"""
    level: AlertLevel
    current_drawdown: float
    max_allowed: float
    message: str
    recommendations: List[str] = field(default_factory=list)

class PerformanceDashboard:
    """
    📊 لوحة الأداء
    - Win Rate لكل وكيل
    - أداء حسب الجلسة
    - تنبيهات Drawdown
    """
    
    def __init__(self, database_service, config: Dict):
        self.db = database_service
        self.config = config
        self.max_drawdown_threshold = config.get('risk_management', {}).get('max_drawdown_stop', 10)

    def _use_trade_snapshots(self) -> bool:
        """Use DatabaseService trade snapshots instead of legacy raw SQL."""
        return self.db.__class__.__name__ == "DatabaseService" and callable(getattr(self.db, "get_recent_trades", None))

    def _pnl(self, trade: Dict[str, Any]) -> float:
        for key in ("final_pnl", "current_pnl_points", "current_pnl", "pnl", "pnl_points"):
            value = trade.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _closed(self, trade: Dict[str, Any]) -> bool:
        return str(trade.get("status", "")).upper() not in {"OPEN", "PARTIAL", "TP1_HIT", "PENDING"}

    def _snapshot(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        snap = trade.get("signal_snapshot", {}) or {}
        return snap if isinstance(snap, dict) else {}

    def _agent_performance_from_trades(self, trades: List[Dict[str, Any]]) -> Dict[str, AgentPerformance]:
        stats: Dict[str, AgentPerformance] = {}
        confidence_sums: Dict[str, float] = {}
        confidence_counts: Dict[str, int] = {}
        for trade in trades:
            snap = self._snapshot(trade)
            votes = snap.get("votes", {}) or {}
            pnl = self._pnl(trade)
            closed = self._closed(trade)
            for side in ("BUY", "SELL", "WAIT"):
                for vote in votes.get(side, []) or []:
                    name = str(vote.get("agent", "unknown"))
                    perf = stats.setdefault(name, AgentPerformance(agent_name=name))
                    perf.total_signals += 1
                    if side == "BUY":
                        perf.buy_signals += 1
                    elif side == "SELL":
                        perf.sell_signals += 1
                    else:
                        perf.wait_signals += 1
                    try:
                        confidence_sums[name] = confidence_sums.get(name, 0.0) + float(vote.get("confidence", 0) or 0)
                        confidence_counts[name] = confidence_counts.get(name, 0) + 1
                    except (TypeError, ValueError):
                        pass
                    if closed and side in {"BUY", "SELL"}:
                        if pnl > 0:
                            perf.winning_trades += 1
                        elif pnl < 0:
                            perf.losing_trades += 1
                        perf.total_pnl += pnl
        for name, perf in stats.items():
            count = confidence_counts.get(name, 0)
            perf.avg_confidence = confidence_sums.get(name, 0.0) / count if count else 0.0
            closed_count = perf.winning_trades + perf.losing_trades
            perf.avg_pnl = perf.total_pnl / closed_count if closed_count else 0.0
            perf.win_rate = perf.calculate_win_rate()
        return stats

    def _session_performance_from_trades(self, trades: List[Dict[str, Any]]) -> List[SessionPerformance]:
        sessions: Dict[str, SessionPerformance] = {}
        conf_sums: Dict[str, float] = {}
        conf_counts: Dict[str, int] = {}
        wins: Dict[str, int] = {}
        for trade in trades:
            snap = self._snapshot(trade)
            info = snap.get("session_info", {}) or {}
            name = session_label_from_trade(trade)
            quality = str(info.get("session_quality") or info.get("quality") or "UNKNOWN")
            session = sessions.setdefault(name, SessionPerformance(session_name=name, quality=quality))
            session.total_signals += 1
            conf_sums[name] = conf_sums.get(name, 0.0) + float(trade.get("confidence", snap.get("confidence", 0)) or 0)
            conf_counts[name] = conf_counts.get(name, 0) + 1
            if self._closed(trade):
                pnl = self._pnl(trade)
                session.trades_count += 1
                session.total_pnl += pnl
                if pnl > 0:
                    wins[name] = wins.get(name, 0) + 1
        for name, session in sessions.items():
            session.avg_confidence = conf_sums.get(name, 0.0) / max(conf_counts.get(name, 0), 1)
            session.win_rate = wins.get(name, 0) / session.trades_count * 100 if session.trades_count else 0.0
        return sorted(sessions.values(), key=lambda item: item.total_signals, reverse=True)
        
    async def get_agent_performance(self, days: int = 7) -> Dict[str, AgentPerformance]:
        """
        📊 حساب Win Rate لكل وكيل
        """
        try:
            if self._use_trade_snapshots():
                return self._agent_performance_from_trades(self.db.get_recent_trades(limit=max(days * 50, 150)))

            # Legacy/mocked path for tests and older services.
            query = f"""
                SELECT 
                    agent_name,
                    COUNT(*) as total_signals,
                    COUNT(*) FILTER (WHERE signal_type = 'BUY') as buy_signals,
                    COUNT(*) FILTER (WHERE signal_type = 'SELL') as sell_signals,
                    COUNT(*) FILTER (WHERE signal_type = 'WAIT') as wait_signals,
                    AVG(confidence_score) as avg_confidence
                FROM signals
                WHERE created_at >= NOW() - INTERVAL '{days} days'
                GROUP BY agent_name
            """
            
            results = await self.db.execute_query(query)
            
            agent_stats = {}
            for row in results:
                agent_name = row.get('agent_name', 'unknown')
                agent_stats[agent_name] = AgentPerformance(
                    agent_name=agent_name,
                    total_signals=row.get('total_signals', 0),
                    buy_signals=row.get('buy_signals', 0),
                    sell_signals=row.get('sell_signals', 0),
                    wait_signals=row.get('wait_signals', 0),
                    avg_confidence=row.get('avg_confidence', 0)
                )
            
            # حساب win rate من الصفقات
            trade_query = """
                SELECT 
                    s.agent_name,
                    COUNT(*) FILTER (WHERE t.pnl > 0) as winning,
                    COUNT(*) FILTER (WHERE t.pnl < 0) as losing,
                    SUM(t.pnl) as total_pnl,
                    AVG(t.pnl) as avg_pnl
                FROM trades t
                JOIN signals s ON t.signal_id = s.id
                WHERE t.closed_at >= NOW() - INTERVAL '7 days'
                GROUP BY s.agent_name
            """
            
            trade_results = await self.db.execute_query(trade_query)
            
            for row in trade_results:
                agent_name = row.get('agent_name')
                if agent_name in agent_stats:
                    agent_stats[agent_name].winning_trades = row.get('winning', 0)
                    agent_stats[agent_name].losing_trades = row.get('losing', 0)
                    agent_stats[agent_name].total_pnl = row.get('total_pnl', 0)
                    agent_stats[agent_name].avg_pnl = row.get('avg_pnl', 0)
                    agent_stats[agent_name].win_rate = agent_stats[agent_name].calculate_win_rate()
            
            return agent_stats
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب أداء الوكلاء: {e}")
            return {}
    
    async def get_session_performance(self, days: int = 30) -> List[SessionPerformance]:
        """
        📈 أداء كل جلسة تداول
        """
        try:
            if self._use_trade_snapshots():
                return self._session_performance_from_trades(self.db.get_recent_trades(limit=max(days * 50, 150)))

            query = f"""
                SELECT 
                    session_name,
                    session_quality,
                    COUNT(*) as total_signals,
                    AVG(confidence_score) as avg_confidence,
                    COUNT(*) FILTER (WHERE signal_type IN ('BUY', 'SELL')) as actual_signals
                FROM signals
                WHERE created_at >= NOW() - INTERVAL '{days} days'
                    AND session_name IS NOT NULL
                GROUP BY session_name, session_quality
                ORDER BY actual_signals DESC
            """
            
            results = await self.db.execute_query(query)
            
            sessions = []
            for row in results:
                session = SessionPerformance(
                    session_name=row.get('session_name', 'Unknown'),
                    quality=row.get('session_quality', 'UNKNOWN'),
                    total_signals=row.get('total_signals', 0),
                    avg_confidence=row.get('avg_confidence', 0)
                )
                
                # حساب win rate للجلسة
                trade_query = f"""
                    SELECT 
                        COUNT(*) as trades,
                        COUNT(*) FILTER (WHERE t.pnl > 0) as winning,
                        SUM(t.pnl) as total_pnl
                    FROM trades t
                    JOIN signals s ON t.signal_id = s.id
                    WHERE s.session_name = '{session.session_name}'
                        AND t.closed_at >= NOW() - INTERVAL '{days} days'
                """
                
                trade_result = await self.db.execute_query(trade_query)
                if trade_result:
                    session.trades_count = trade_result[0].get('trades', 0)
                    winning = trade_result[0].get('winning', 0)
                    if session.trades_count > 0:
                        session.win_rate = (winning / session.trades_count) * 100
                    session.total_pnl = trade_result[0].get('total_pnl', 0)
                
                sessions.append(session)
            
            return sessions
            
        except Exception as e:
            logger.error(f"❌ خطأ في حساب أداء الجلسات: {e}")
            return []
    
    async def check_drawdown_alerts(self) -> List[DrawdownAlert]:
        """
        🚨 فحص تنبيهات السحب
        """
        alerts = []
        
        try:
            # الحصول على بيانات المحفظة
            portfolio = await self.get_portfolio_summary()
            
            if not portfolio:
                return alerts
            
            current_balance = portfolio.get('balance', 10000)
            peak_balance = portfolio.get('peak_balance', current_balance)
            current_drawdown = ((peak_balance - current_balance) / peak_balance) * 100 if peak_balance > 0 else 0
            
            logger.info(f"📉 السحب الحالي: {current_drawdown:.2f}% | الحد الأقصى: {self.max_drawdown_threshold}%")
            
            # تحديد مستوى التنبيه
            if current_drawdown >= self.max_drawdown_threshold * 0.5:
                level = AlertLevel.WARNING
            if current_drawdown >= self.max_drawdown_threshold:
                level = AlertLevel.CRITICAL
            
            if current_drawdown >= self.max_drawdown_threshold * 0.5:
                recommendations = []
                
                if current_drawdown >= self.max_drawdown_threshold * 0.75:
                    recommendations.append("🛑 Stop trading immediately")
                    recommendations.append("📋 Review losing trades")
                    
                if current_drawdown >= self.max_drawdown_threshold * 0.5:
                    recommendations.append("⚠️ Reduce position size by 50%")
                    recommendations.append("📉 Focus on high-quality signals only")
                    recommendations.append("🔍 Review risk management")
                
                alerts.append(DrawdownAlert(
                    level=level,
                    current_drawdown=current_drawdown,
                    max_allowed=self.max_drawdown_threshold,
                    message=f"⚠️ Current drawdown: {current_drawdown:.2f}% (limit: {self.max_drawdown_threshold}%)",
                    recommendations=recommendations
                ))
            
            return alerts
            
        except Exception as e:
            logger.error(f"❌ خطأ في فحص السحب: {e}")
            return []
    
    async def get_portfolio_summary(self) -> Dict:
        """ملخص المحفظة"""
        try:
            if self._use_trade_snapshots():
                trades = self.db.get_recent_trades(limit=500)
                starting = float(self.config.get('paper_trading', {}).get('starting_balance', 10000) or 10000)
                total_pnl = sum(self._pnl(t) for t in trades if self._closed(t))
                closed = [t for t in trades if self._closed(t)]
                wins = len([t for t in closed if self._pnl(t) > 0])
                losses = len([t for t in closed if self._pnl(t) < 0])
                balance = starting + total_pnl
                return {
                    'balance': balance,
                    'equity': balance,
                    'max_drawdown': 0,
                    'total_trades': len(closed),
                    'winning_trades': wins,
                    'losing_trades': losses,
                    'win_rate': (wins / len(closed) * 100) if closed else 0,
                    'total_pnl': total_pnl,
                    'peak_balance': max(starting, balance),
                }

            query = """
                SELECT 
                    balance,
                    equity,
                    max_drawdown,
                    total_trades,
                    winning_trades,
                    losing_trades,
                    win_rate,
                    total_pnl
                FROM portfolio
                ORDER BY id DESC
                LIMIT 1
            """
            
            results = await self.db.execute_query(query)
            
            if results:
                return {
                    'balance': results[0].get('balance', 10000),
                    'equity': results[0].get('equity', 10000),
                    'max_drawdown': results[0].get('max_drawdown', 0),
                    'total_trades': results[0].get('total_trades', 0),
                    'winning_trades': results[0].get('winning_trades', 0),
                    'losing_trades': results[0].get('losing_trades', 0),
                    'win_rate': results[0].get('win_rate', 0),
                    'total_pnl': results[0].get('total_pnl', 0),
                    'peak_balance': 10000  # يمكن حسابه من البيانات التاريخية
                }
            
            return {'balance': 10000, 'peak_balance': 10000}
            
        except Exception as e:
            logger.error(f"❌ خطأ في جلب ملخص المحفظة: {e}")
            return {'balance': 10000, 'peak_balance': 10000}
    
    def _analyst_overlap_summary(self) -> Dict[str, Any]:
        """Best-effort analyst-vs-bot overlap snapshot for dashboard/reporting."""
        try:
            cfg = self.config.get("analyst_distillation", {}) or {}
            if not bool(cfg.get("enabled", True)):
                return {}
            limit = int(cfg.get("dashboard_compare_limit", 30) or 30)
            service = AnalystDistillationService(self.db, self.config)
            summary = service.compare_recent(symbol=self.config.get("symbol", "XAU/USD"), limit=limit)
            return {
                "labels_considered": summary.get("labels_considered", 0),
                "matched_labels": summary.get("matched_labels", 0),
                "partial_matches": summary.get("partial_matches", 0),
                "missed_labels": summary.get("missed_labels", 0),
                "extra_bot_setups": summary.get("extra_bot_setups", 0),
                "match_rate_pct": summary.get("match_rate_pct", 0.0),
                "coverage_rate_pct": summary.get("coverage_rate_pct", 0.0),
                "avg_entry_distance_points": summary.get("avg_entry_distance_points"),
                "top_missed_reasons": summary.get("top_missed_reasons", []),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Analyst overlap summary unavailable: %s", exc)
            return {}

    async def generate_performance_report(self, days: int = 7) -> Dict[str, Any]:
        """
        📊 تقرير الأداء الشامل
        """
        report = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'period_days': days,
            'portfolio': await self.get_portfolio_summary(),
            'agents': {},
            'sessions': [],
            'alerts': [],
            'summary': {},
            'analyst_overlap': self._analyst_overlap_summary(),
        }
        
        # أداء الوكلاء
        agent_perf = await self.get_agent_performance(days)
        for name, perf in agent_perf.items():
            report['agents'][name] = {
                'total_signals': perf.total_signals,
                'win_rate': f"{perf.win_rate:.1f}%",
                'avg_confidence': f"{perf.avg_confidence:.1f}%",
                'total_pnl': f"${perf.total_pnl:.2f}",
                'trades': perf.winning_trades + perf.losing_trades
            }
        
        # أداء الجلسات
        report['sessions'] = [
            {
                'name': s.session_name,
                'quality': s.quality,
                'signals': s.total_signals,
                'win_rate': f"{s.win_rate:.1f}%",
                'pnl': f"${s.total_pnl:.2f}"
            }
            for s in await self.get_session_performance(days)
        ]
        
        # تنبيهات السحب
        alerts = await self.check_drawdown_alerts()
        report['alerts'] = [
            {
                'level': a.level.value,
                'message': a.message,
                'recommendations': a.recommendations
            }
            for a in alerts
        ]
        
        # الملخص العام
        total_trades = sum(a.winning_trades + a.losing_trades for a in agent_perf.values())
        total_winning = sum(a.winning_trades for a in agent_perf.values())
        total_pnl = sum(a.total_pnl for a in agent_perf.values())
        
        report['summary'] = {
            'total_signals': sum(a.total_signals for a in agent_perf.values()),
            'total_trades': total_trades,
            'overall_win_rate': f"{(total_winning/total_trades*100) if total_trades > 0 else 0:.1f}%",
            'total_pnl': f"${total_pnl:.2f}"
        }
        
        return report
    
    def format_telegram_report(self, report: Dict[str, Any]) -> str:
        """
        📱 تنسيق تقرير الأداء لتيليجرام
        """
        lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            "📊 *Comprehensive Performance Report*",
            f"📅 Period: last {report['period_days']} days",
            "━━━━━━━━━━━━━━━━━━━━",
            ""
        ]
        
        # المحفظة
        portfolio = report.get('portfolio', {})
        lines.extend([
            "💰 *Portfolio*",
            f"├ Balance: ${portfolio.get('balance', 0):.2f}",
            f"├ Total PnL: ${portfolio.get('total_pnl', 0):.2f}",
            f"├ Win rate: {portfolio.get('win_rate', 0):.1f}%",
            f"└ Total trades: {portfolio.get('total_trades', 0)}",
            ""
        ])
        
        # الوكلاء
        if report.get('agents'):
            lines.extend([
                "🤖 *Agent performance*",
            ])
            for name, data in sorted(report['agents'].items(), key=lambda x: -x[1].get('total_signals', 0)):
                lines.append(
                    f"├ {name}: {data['win_rate']} ({data['total_signals']} signals)"
                )
            lines.append("")
        
        # الجلسات
        if report.get('sessions'):
            lines.extend([
                "🕐 *Session performance*",
            ])
            for session in report['sessions'][:3]:
                lines.append(
                    f"├ {session['name']}: {session['win_rate']}"
                )
            lines.append("")
        
        # التنبيهات
        if report.get('alerts'):
            lines.extend([
                "🚨 *Alerts*",
            ])
            for alert in report['alerts']:
                lines.append(f"{alert['level']} {alert['message']}")
            lines.append("")

        # analyst overlap snapshot
        overlap = report.get('analyst_overlap') or {}
        if overlap and int(overlap.get('labels_considered', 0) or 0) > 0:
            lines.extend([
                "🧠 *Analyst overlap*",
                f"├ Labels: {overlap.get('labels_considered', 0)}",
                f"├ Matched: {overlap.get('matched_labels', 0)} · Partial: {overlap.get('partial_matches', 0)} · Missed: {overlap.get('missed_labels', 0)}",
                f"├ Coverage: {overlap.get('coverage_rate_pct', 0)}% · Match: {overlap.get('match_rate_pct', 0)}%",
            ])
            if overlap.get('avg_entry_distance_points') is not None:
                lines.append(f"├ Avg entry distance: {overlap.get('avg_entry_distance_points')} pts")
            reasons = overlap.get('top_missed_reasons') or []
            if reasons:
                first = reasons[0]
                lines.append(f"└ Top miss reason: {first.get('reason_code', 'N/A')} ({first.get('count', 0)})")
            else:
                lines.append("└ Top miss reason: —")
            lines.append("")
        
        # الملخص
        summary = report.get('summary', {})
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            "📈 *Summary*",
            f"├ Total signals: {summary.get('total_signals', 0)}",
            f"├ Total trades: {summary.get('total_trades', 0)}",
            f"├ Overall win rate: {summary.get('overall_win_rate', '0%')}",
            f"└ PnL: {summary.get('total_pnl', '$0')}",
            "━━━━━━━━━━━━━━━━━━━━"
        ])
        
        return "\n".join(lines)

# Singleton instance
_dashboard_instance: Optional[PerformanceDashboard] = None

def get_performance_dashboard(db, config: Dict) -> PerformanceDashboard:
    """الحصول على instance لوحة الأداء"""
    global _dashboard_instance
    if _dashboard_instance is None:
        _dashboard_instance = PerformanceDashboard(db, config)
    return _dashboard_instance