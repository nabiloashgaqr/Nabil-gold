"""
🧪 اختبارات خدمة التعلم الذكي
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.learning_service import (
    LearningService, LearningConfig, AgentPerformanceRecord, LearningReport,
    get_learning_service
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
        'learning': {
            'enabled': True,
            'update_frequency_days': 1,
            'min_predictions_for_adjustment': 5,
            'max_weight_change': 0.15,
            'momentum_weight': 0.3,
            'decay_factor': 0.95,
            'performance_threshold': 0.6
        },
        'agent_weights': {
            'technical': 0.20,
            'classical': 0.20,
            'smc': 0.25,
            'price_action': 0.15,
            'multitimeframe': 0.15
        }
    }


@pytest.fixture
def learning_service(mock_db, config):
    """خدمة التعلم"""
    return LearningService(mock_db, config)


class TestLearningConfig:
    """اختبارات إعدادات التعلم"""
    
    def test_default_config(self):
        """اختبار الإعدادات الافتراضية"""
        config = LearningConfig()
        
        assert config.enabled is True
        assert config.update_frequency_days == 1
        assert config.min_predictions_for_adjustment == 3  # وضع عدواني
        assert config.max_weight_change == 0.25
        assert config.momentum_weight == 0.4
        assert config.aggressive_mode == True
        assert config.streak_bonus == 0.10  # مكافأة التتابع
        assert config.recent_trades_weight == 0.6
        assert config.performance_threshold == 0.6
    
    def test_custom_config(self):
        """اختبار إعدادات مخصصة"""
        config = LearningConfig(
            enabled=False,
            update_frequency_days=7,
            min_predictions_for_adjustment=10,
            max_weight_change=0.20
        )
        
        assert config.enabled is False
        assert config.update_frequency_days == 7
        assert config.min_predictions_for_adjustment == 10


class TestAgentPerformanceRecord:
    """اختبارات سجل أداء الوكيل"""
    
    def test_record_init(self):
        """اختبار تهيئة السجل"""
        record = AgentPerformanceRecord(
            agent_name='technical',
            total_predictions=100,
            correct_predictions=65,
            win_rate=65.0,
            current_weight=0.20,
            adjusted_weight=0.22,
            trend='IMPROVING'
        )
        
        assert record.agent_name == 'technical'
        assert record.win_rate == 65.0
        assert record.trend == 'IMPROVING'


class TestLearningService:
    """اختبارات خدمة التعلم"""
    
    def test_init(self, learning_service):
        """اختبار التهيئة"""
        assert learning_service.learning_config.enabled is True
        assert len(learning_service.current_weights) == 5
    
    def test_default_weights(self, learning_service):
        """اختبار الأوزان الافتراضية"""
        weights = learning_service.default_weights
        
        assert weights['technical'] == 0.20
        assert weights['classical'] == 0.25
        assert weights['smc'] == 0.20
        assert weights['price_action'] == 0.20
        assert weights['multitimeframe'] == 0.15
        # مجموع الأوزان يجب أن يكون قريب من 1.0
        total = sum(weights.values())
        assert 0.99 <= total <= 1.01
    
    @pytest.mark.asyncio
    async def test_analyze_disabled(self):
        """اختبار التعلم المعطل"""
        config = {'learning': {'enabled': False}}
        service = LearningService(AsyncMock(), config)
        
        report = await service.analyze_and_update_weights()
        
        assert report.total_trades_analyzed == 0
        assert report.overall_win_rate == 0
    
    @pytest.mark.asyncio
    async def test_analyze_with_few_trades(self, learning_service, mock_db):
        """اختبار التحليل مع صفقات قليلة"""
        mock_db.execute_query = AsyncMock(return_value=[
            {'id': 't1', 'pnl': 10.0},
            {'id': 't2', 'pnl': -5.0}
        ])
        
        # min_predictions = 5، لكن عندنا 2 فقط
        report = await learning_service.analyze_and_update_weights()
        
        assert report.total_trades_analyzed == 0  # لا تغيير
    
    def test_calculate_adjusted_weights(self, learning_service):
        """اختبار حساب الأوزان الجديدة v2"""
        agent_stats = {
            'technical': AgentPerformanceRecord(
                agent_name='technical',
                win_rate=70.0,
                avg_confidence=80.0,
                current_weight=0.20,
                trend='IMPROVING',
                accuracy_trend=[1.0, 1.0, 0.0]
            ),
            'smc': AgentPerformanceRecord(
                agent_name='smc',
                win_rate=65.0,
                avg_confidence=75.0,
                current_weight=0.25,
                trend='STABLE',
                accuracy_trend=[1.0, 0.0, 1.0]
            ),
            'classical': AgentPerformanceRecord(
                agent_name='classical',
                win_rate=50.0,
                avg_confidence=70.0,
                current_weight=0.20,
                trend='DECLINING',
                accuracy_trend=[0.0, 0.0, 1.0]
            )
        }
        
        adjusted = learning_service._calculate_adjusted_weights_v2(agent_stats)
        
        # التحقق من أن المجموع = 1.0
        assert abs(sum(adjusted.values()) - 1.0) < 0.01
        
        # التحقق من حدود التغيير (5% - 45%)
        for name, new_weight in adjusted.items():
            assert 0.05 <= new_weight <= 0.45
    
    def test_improving_agent_gets_higher_weight(self, learning_service):
        """اختبار أن الوكيل المتحسن يحصل على وزن أعلى"""
        agent_stats = {
            'good': AgentPerformanceRecord(
                agent_name='good',
                win_rate=80.0,
                avg_confidence=80.0,
                current_weight=0.15,
                trend='IMPROVING',
                consecutive_wins=3,
                accuracy_trend=[1.0, 1.0, 1.0]
            ),
            'bad': AgentPerformanceRecord(
                agent_name='bad',
                win_rate=40.0,
                avg_confidence=60.0,
                current_weight=0.25,
                trend='DECLINING',
                consecutive_losses=3,
                accuracy_trend=[0.0, 0.0, 0.0]
            )
        }
        
        adjusted = learning_service._calculate_adjusted_weights_v2(agent_stats)
        
        # العامل الجيد يجب أن يزيد
        assert adjusted['good'] > 0.15
        # العامل السيء يجب أن ينقص
        assert adjusted['bad'] < 0.25
        # الوزن ضمن نطاق معقول (5% - 45%)
        assert 0.05 <= adjusted["bad"] <= 0.45
        # مجموع الأوزان = 1.0
        assert abs(sum(adjusted.values()) - 1.0) < 0.01
    
    def test_generate_empty_report(self, learning_service):
        """اختبار توليد تقرير فارغ"""
        report = learning_service._empty_report()
        
        assert report.total_trades_analyzed == 0
        assert len(report.adjusted_weights) > 0
    
    def test_get_learning_summary(self, learning_service):
        """اختبار ملخص التعلم"""
        # بدون سجل → تقرير فارغ
        summary = learning_service.get_learning_summary()
        
        assert "لا يوجد سجل تعلم" in summary or "📊" in summary
    
    def test_get_agent_recommendation_improving(self, learning_service):
        """اختبار توصية وكيل متحسن مع تتابع نجاح"""
        # إضافة سجل وهمي مع تتابع نجاح
        learning_service.learning_history.append(
            LearningReport(
                report_date=datetime.now(timezone.utc).isoformat(),
                agents_performance={
                    'technical': AgentPerformanceRecord(
                        agent_name='technical',
                        trend='IMPROVING',
                        win_rate=75.0,
                        consecutive_wins=4,
                        accuracy_trend=[1.0, 1.0, 1.0, 1.0]
                    )
                },
                adjusted_weights={},
                total_trades_analyzed=10,
                overall_win_rate=65,
                recommendations=[],
                previous_weights={},
                changes_summary=""
            )
        )
        
        rec = learning_service.get_agent_recommendation('technical')
        
        assert rec == "INCREASE_CONFIDENCE"
    
    def test_get_agent_recommendation_declining(self, learning_service):
        """اختبار توصية وكيل متراجع"""
        learning_service.learning_history.append(
            LearningReport(
                report_date=datetime.now(timezone.utc).isoformat(),
                agents_performance={
                    'technical': AgentPerformanceRecord(
                        agent_name='technical',
                        trend='DECLINING'
                    )
                },
                adjusted_weights={},
                total_trades_analyzed=10,
                overall_win_rate=45,
                recommendations=[],
                previous_weights={},
                changes_summary=""
            )
        )
        
        rec = learning_service.get_agent_recommendation('technical')
        
        assert rec == "DECREASE_CONFIDENCE"
    
    def test_get_agent_recommendation_no_history(self, learning_service):
        """اختبار توصية بدون سجل"""
        rec = learning_service.get_agent_recommendation('technical')
        
        assert rec == "NEUTRAL"
    
    def test_weight_bounds(self, learning_service):
        """اختبار حدود الأوزان v2 (5% - 45%)"""
        agent_stats = {
            'a': AgentPerformanceRecord(
                agent_name='a', win_rate=75.0, avg_confidence=80.0,
                current_weight=0.25, trend='IMPROVING',
                consecutive_wins=2,
                accuracy_trend=[1.0, 1.0, 1.0]
            ),
            'b': AgentPerformanceRecord(
                agent_name='b', win_rate=55.0, avg_confidence=70.0,
                current_weight=0.25, trend='STABLE',
                accuracy_trend=[1.0, 0.0, 1.0]
            ),
            'c': AgentPerformanceRecord(
                agent_name='c', win_rate=65.0, avg_confidence=75.0,
                current_weight=0.25, trend='STABLE',
                accuracy_trend=[0.0, 1.0, 0.0]
            ),
            'd': AgentPerformanceRecord(
                agent_name='d', win_rate=45.0, avg_confidence=60.0,
                current_weight=0.25, trend='DECLINING',
                consecutive_losses=2,
                accuracy_trend=[0.0, 0.0, 1.0]
            )
        }
        
        adjusted = learning_service._calculate_adjusted_weights_v2(agent_stats)
        
        # لا وزن يقل عن 5% أو يزيد عن 45% (حد أوسع في v2)
        for weight in adjusted.values():
            assert 0.05 <= weight <= 0.45
        # مجموع الأوزان = 1.0
        assert abs(sum(adjusted.values()) - 1.0) < 0.01


class TestLearningReport:
    """اختبارات تقرير التعلم"""
    
    def test_report_init(self):
        """اختبار تهيئة التقرير"""
        report = LearningReport(
            report_date="2024-01-15",
            agents_performance={},
            adjusted_weights={'technical': 0.20},
            total_trades_analyzed=50,
            overall_win_rate=62.5,
            recommendations=["SMC improving"],
            previous_weights={'technical': 0.18},
            changes_summary="technical: +2%"
        )
        
        assert report.report_date == "2024-01-15"
        assert report.total_trades_analyzed == 50
        assert report.overall_win_rate == 62.5


class TestSingleton:
    """اختبارات Singleton"""
    
    def test_get_learning_service(self, mock_db, config):
        """اختبار الحصول على instance واحد"""
        service1 = get_learning_service(mock_db, config)
        service2 = get_learning_service(mock_db, config)
        
        # يجب أن يكون نفس الـ instance
        assert service1 is service2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])