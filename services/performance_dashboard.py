"""
📊 لوحة الأداء - Gold AI Signals
تقيس Win Rate لكل وكيل + أداء حسب الجلسة + تنبيهات Drawdown
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

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
        
    async def get_agent_performance(self, days: int = 7) -> Dict[str, AgentPerformance]:
        """
        📊 حساب Win Rate لكل وكيل
        """
        try:
            # استعلام البيانات من Supabase
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
                    recommendations.append("🛑 إيقاف التداول فوراً")
                    recommendations.append("📋 مراجعة الصفقات الخاسرة")
                    
                if current_drawdown >= self.max_drawdown_threshold * 0.5:
                    recommendations.append("⚠️ تقليل حجم الصفقات 50%")
                    recommendations.append("📉 التركيز على الإشارات عالية الجودة فقط")
                    recommendations.append("🔍 مراجعة إدارة المخاطر")
                
                alerts.append(DrawdownAlert(
                    level=level,
                    current_drawdown=current_drawdown,
                    max_allowed=self.max_drawdown_threshold,
                    message=f"⚠️ السحب الحالي: {current_drawdown:.2f}% (الحد: {self.max_drawdown_threshold}%)",
                    recommendations=recommendations
                ))
            
            return alerts
            
        except Exception as e:
            logger.error(f"❌ خطأ في فحص السحب: {e}")
            return []
    
    async def get_portfolio_summary(self) -> Dict:
        """ملخص المحفظة"""
        try:
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
    
    async def generate_performance_report(self, days: int = 7) -> Dict[str, Any]:
        """
        📊 تقرير الأداء الشامل
        """
        report = {
            'generated_at': datetime.utcnow().isoformat(),
            'period_days': days,
            'portfolio': await self.get_portfolio_summary(),
            'agents': {},
            'sessions': [],
            'alerts': [],
            'summary': {}
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
            "📊 *تقرير الأداء الشامل*",
            f"📅 الفترة: آخر {report['period_days']} أيام",
            "━━━━━━━━━━━━━━━━━━━━",
            ""
        ]
        
        # المحفظة
        portfolio = report.get('portfolio', {})
        lines.extend([
            "💰 *المحفظة*",
            f"├ الرصيد: ${portfolio.get('balance', 0):.2f}",
            f"├ إجمالي الربح/الخسارة: ${portfolio.get('total_pnl', 0):.2f}",
            f"├ نسبة الربح: {portfolio.get('win_rate', 0):.1f}%",
            f"└ إجمالي الصفقات: {portfolio.get('total_trades', 0)}",
            ""
        ])
        
        # الوكلاء
        if report.get('agents'):
            lines.extend([
                "🤖 *أداء الوكلاء*",
            ])
            for name, data in sorted(report['agents'].items(), key=lambda x: -x[1].get('total_signals', 0)):
                lines.append(
                    f"├ {name}: {data['win_rate']} ({data['total_signals']} إشارة)"
                )
            lines.append("")
        
        # الجلسات
        if report.get('sessions'):
            lines.extend([
                "🕐 *أداء الجلسات*",
            ])
            for session in report['sessions'][:3]:
                lines.append(
                    f"├ {session['name']}: {session['win_rate']}"
                )
            lines.append("")
        
        # التنبيهات
        if report.get('alerts'):
            lines.extend([
                "🚨 *التنبيهات*",
            ])
            for alert in report['alerts']:
                lines.append(f"{alert['level']} {alert['message']}")
            lines.append("")
        
        # الملخص
        summary = report.get('summary', {})
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            "📈 *الملخص*",
            f"├ إجمالي الإشارات: {summary.get('total_signals', 0)}",
            f"├ إجمالي الصفقات: {summary.get('total_trades', 0)}",
            f"├ نسبة الربح الإجمالية: {summary.get('overall_win_rate', '0%')}",
            f"└ الربح/الخسارة: {summary.get('total_pnl', '$0')}",
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