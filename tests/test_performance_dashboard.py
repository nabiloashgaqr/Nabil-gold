"""
🧪 اختبارات لوحة الأداء - Performance Dashboard
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.performance_dashboard import (
    PerformanceDashboard, AgentPerformance, SessionPerformance,
    DrawdownAlert, AlertLevel, get_performance_dashboard
)


@pytest.fixture
def mock_db():
    """قاعدة بيانات وهمية"""
    db = AsyncMock()
    db.execute_query = AsyncMock(return_value=[])
    return db


@pytest.fixture
def config():
    """إعدادات وهمية"""
    return {
        'risk_management': {
            'max_drawdown_stop': 10
        },
        'performance': {
            'report_interval_days': 7
        }
    }


@pytest.fixture
def dashboard(mock_db, config):
    """لوحة الأداء"""
    return PerformanceDashboard(mock_db, config)


class TestAgentPerformance:
    """اختبارات أداء الوكيل"""
    
    def test_agent_performance_init(self):
        """اختبار تهيئة أداء الوكيل"""
        agent = AgentPerformance(
            agent_name="technical",
            total_signals=100,
            winning_trades=60,
            losing_trades=40
        )
        
        # حساب win_rate يدوياً
        agent.win_rate = agent.calculate_win_rate()
        
        assert agent.agent_name == "technical"
        assert agent.total_signals == 100
        assert agent.win_rate == 60.0  # 60/100 * 100
    
    def test_calculate_win_rate_zero_trades(self):
        """اختبار حساب win rate بدون صفقات"""
        agent = AgentPerformance(agent_name="test", total_signals=0)
        
        assert agent.calculate_win_rate() == 0.0
    
    def test_calculate_win_rate_with_trades(self):
        """اختبار حساب win rate مع صفقات"""
        agent = AgentPerformance(
            agent_name="test",
            total_signals=50,
            winning_trades=35,
            losing_trades=15
        )
        
        assert agent.calculate_win_rate() == 70.0


class TestSessionPerformance:
    """اختبارات أداء الجلسة"""
    
    def test_session_performance_init(self):
        """اختبار تهيئة أداء الجلسة"""
        session = SessionPerformance(
            session_name="London-NY Trading",
            quality="HIGH",
            total_signals=50,
            win_rate=65.0
        )
        
        assert session.session_name == "London-NY Trading"
        assert session.quality == "HIGH"
        assert session.win_rate == 65.0


class TestDrawdownAlert:
    """اختبارات تنبيهات السحب"""
    
    def test_warning_alert(self):
        """اختبار تنبيه تحذير"""
        alert = DrawdownAlert(
            level=AlertLevel.WARNING,
            current_drawdown=7.5,
            max_allowed=10,
            message="⚠️ السحب: 7.5%",
            recommendations=["تقليل حجم الصفقات"]
        )
        
        assert alert.level == AlertLevel.WARNING
        assert alert.current_drawdown == 7.5
        assert len(alert.recommendations) == 1
    
    def test_critical_alert(self):
        """اختبار تنبيه حرج"""
        alert = DrawdownAlert(
            level=AlertLevel.CRITICAL,
            current_drawdown=12.0,
            max_allowed=10,
            message="🚨 السحب: 12%",
            recommendations=["إيقاف التداول", "مراجعة الصفقات"]
        )
        
        assert alert.level == AlertLevel.CRITICAL
        assert alert.current_drawdown > alert.max_allowed


class TestPerformanceDashboard:
    """اختبارات لوحة الأداء"""
    
    @pytest.mark.asyncio
    async def test_get_agent_performance(self, dashboard, mock_db):
        """اختبار جلب أداء الوكلاء"""
        # Mock الإجابة المتوقعة
        mock_db.execute_query = AsyncMock(return_value=[
            {
                'agent_name': 'technical',
                'total_signals': 50,
                'buy_signals': 30,
                'sell_signals': 15,
                'wait_signals': 5,
                'avg_confidence': 75.5
            },
            {
                'agent_name': 'classical',
                'total_signals': 40,
                'buy_signals': 25,
                'sell_signals': 10,
                'wait_signals': 5,
                'avg_confidence': 70.0
            }
        ])
        
        result = await dashboard.get_agent_performance(days=7)
        
        assert 'technical' in result
        assert result['technical'].total_signals == 50
        assert result['technical'].avg_confidence == 75.5
    
    @pytest.mark.asyncio
    async def test_get_session_performance(self, dashboard, mock_db):
        """اختبار جلب أداء الجلسات"""
        mock_db.execute_query = AsyncMock(return_value=[
            {
                'session_name': 'London-NY Trading',
                'session_quality': 'HIGH',
                'total_signals': 100,
                'avg_confidence': 78.5,
                'actual_signals': 80
            }
        ])
        
        result = await dashboard.get_session_performance(days=30)
        
        assert len(result) > 0
        assert result[0].session_name == "London-NY Trading"
    
    @pytest.mark.asyncio
    async def test_check_drawdown_alerts_low(self, dashboard):
        """اختبار فحص السحب - بدون تنبيه (أقل من 5%)"""
        dashboard.get_portfolio_summary = AsyncMock(return_value={
            'balance': 9900,  # 1% drawdown فقط
            'peak_balance': 10000,
            'max_drawdown': 0
        })
        alerts = await dashboard.check_drawdown_alerts()
        
        assert len(alerts) == 0  # لا تنبيهات - أقل من 5%
    
    @pytest.mark.asyncio
    async def test_check_drawdown_alerts_warning(self, dashboard):
        """اختبار فحص السحب - تحذير"""
        dashboard.get_portfolio_summary = AsyncMock(return_value={
            'balance': 9400,
            'peak_balance': 10000,
            'max_drawdown': 6
        })
        alerts = await dashboard.check_drawdown_alerts()
        
        # 6% >= 10% * 0.5 = 5% → تنبيه
        assert len(alerts) > 0
    
    @pytest.mark.asyncio
    async def test_generate_performance_report(self, dashboard, mock_db):
        """اختبار توليد تقرير الأداء"""
        mock_db.execute_query = AsyncMock(side_effect=[
            [{'agent_name': 'technical', 'total_signals': 20, 'buy_signals': 15,
              'sell_signals': 5, 'wait_signals': 0, 'avg_confidence': 75}],
            [{'session_name': 'London', 'session_quality': 'HIGH',
              'total_signals': 20, 'avg_confidence': 75, 'actual_signals': 18}],
        ])
        
        dashboard.get_portfolio_summary = AsyncMock(return_value={
            'balance': 10500, 'total_pnl': 500, 'win_rate': 65
        })
        dashboard._analyst_overlap_summary = MagicMock(return_value={
            'labels_considered': 5,
            'matched_labels': 3,
            'partial_matches': 1,
            'missed_labels': 1,
            'extra_bot_setups': 2,
            'match_rate_pct': 60.0,
            'coverage_rate_pct': 80.0,
            'avg_entry_distance_points': 42.0,
            'top_missed_reasons': [{'reason_code': 'MISSED_ENTRY_TOO_FAR', 'count': 1}],
        })
        
        report = await dashboard.generate_performance_report(days=7)
        
        assert 'generated_at' in report
        assert 'agents' in report
        assert 'sessions' in report
        assert 'summary' in report
        assert report['analyst_overlap']['matched_labels'] == 3
    
    def test_format_telegram_report(self, dashboard):
        """اختبار تنسيق تقرير تيليجرام"""
        report = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'period_days': 7,
            'portfolio': {
                'balance': 10500,
                'total_pnl': 500,
                'win_rate': 65,
                'total_trades': 50
            },
            'agents': {
                'technical': {
                    'total_signals': 20,
                    'win_rate': '70%',
                    'avg_confidence': '75.5%',
                    'total_pnl': '$250',
                    'trades': 30
                }
            },
            'sessions': [
                {'name': 'London-NY', 'quality': 'HIGH',
                 'signals': 15, 'win_rate': '68%', 'pnl': '$200'}
            ],
            'alerts': [],
            'analyst_overlap': {
                'labels_considered': 5,
                'matched_labels': 3,
                'partial_matches': 1,
                'missed_labels': 1,
                'coverage_rate_pct': 80.0,
                'match_rate_pct': 60.0,
                'avg_entry_distance_points': 42.0,
                'top_missed_reasons': [{'reason_code': 'MISSED_ENTRY_TOO_FAR', 'count': 1}],
            },
            'summary': {
                'total_signals': 20,
                'total_trades': 30,
                'overall_win_rate': '70%',
                'total_pnl': '$500'
            }
        }
        
        formatted = dashboard.format_telegram_report(report)
        
        # التحقق من وجود المحتوى (ليس التطابق الكامل)
        assert 'technical' in formatted
        assert '$500' in formatted
        assert 'Analyst overlap' in formatted
        assert 'Coverage: 80.0%' in formatted
        assert 'MISSED_ENTRY_TOO_FAR' in formatted
        assert 'المحفظة' in formatted or 'Portfolio' in formatted or '💰' in formatted
        assert 'الملخص' in formatted or 'Summary' in formatted or '📈' in formatted


class TestSingletonInstance:
    """اختبارات الـ Singleton"""
    
    def test_get_dashboard_instance(self, mock_db, config):
        """اختبار الحصول على instance واحد"""
        dashboard1 = get_performance_dashboard(mock_db, config)
        dashboard2 = get_performance_dashboard(mock_db, config)
        
        # يجب أن يكون نفس الـ instance
        assert dashboard1 is dashboard2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])