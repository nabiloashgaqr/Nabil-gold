"""
🔬 اختبارات Learning Service v2.0
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
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
            'min_predictions_for_adjustment': 3,  # ↓ من 5
            'max_weight_change': 0.25,  # ↑ من 0.15
            'momentum_weight': 0.4,  # ↑ من 0.3
            'decay_factor': 0.90,  # ↓ من 0.95
            'performance_threshold': 0.6,
            'aggressive_mode': True,
            'streak_bonus': 0.10,
            'recent_trades_weight': 0.6
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


class TestLearningConfigV2:
    """اختبارات إعدادات التعلم v2.0"""
    
    def test_aggressive_defaults(self):
        """اختبار: الإعدادات الافتراضية العدوانية"""
        config = LearningConfig()
        
        assert config.aggressive_mode == True
        assert config.min_predictions_for_adjustment == 3
        assert config.max_weight_change == 0.25
        assert config.momentum_weight == 0.4
        assert config.streak_bonus == 0.10
        assert config.recent_trades_weight == 0.6
    
    def test_custom_aggressive_config(self):
        """اختبار: إعدادات عدوانية مخصصة"""
        config = LearningConfig(
            enabled=True,
            min_predictions_for_adjustment=2,
            max_weight_change=0.30,
            aggressive_mode=True
        )
        
        assert config.aggressive_mode == True
        assert config.min_predictions_for_adjustment == 2


class TestAgentPerformanceRecordV2:
    """اختبارات سجل الأداء مع التتابع"""
    
    def test_record_with_streak(self):
        """اختبار: سجل مع تتابع"""
        record = AgentPerformanceRecord(
            agent_name='smc',
            total_predictions=10,
            correct_predictions=7,
            win_rate=70.0,
            consecutive_wins=3,
            consecutive_losses=0,
            accuracy_trend=[1.0, 1.0, 1.0, 0.0, 1.0],
            current_weight=0.25
        )
        
        assert record.consecutive_wins == 3
        # الاتجاه يحسب من _calculate_trend - نختبر فقط السجل
        assert record.accuracy_trend == [1.0, 1.0, 1.0, 0.0, 1.0]
    
    def test_record_with_losses_streak(self):
        """اختبار: سجل مع تتابع خسائر"""
        record = AgentPerformanceRecord(
            agent_name='technical',
            total_predictions=10,
            correct_predictions=3,
            win_rate=30.0,
            consecutive_wins=0,
            consecutive_losses=3,
            accuracy_trend=[0.0, 0.0, 0.0, 1.0, 0.0],
            current_weight=0.20
        )
        
        assert record.consecutive_losses == 3
        # الاتجاه يحسب من _calculate_trend - نختبر فقط السجل
        assert record.accuracy_trend == [0.0, 0.0, 0.0, 1.0, 0.0]


class TestLearningServiceV2:
    """اختبارات خدمة التعلم v2.0"""
    
    def test_init_v2(self, learning_service):
        """اختبار: تهيئة v2"""
        assert learning_service.learning_config.aggressive_mode == True
        assert learning_service.learning_config.streak_bonus == 0.10
    
    def test_failed_signals_memory_init(self, learning_service):
        """اختبار: تهيئة ذاكرة الصفقات الفاشلة"""
        assert hasattr(learning_service, 'failed_signals_memory')
        assert isinstance(learning_service.failed_signals_memory, list)
    
    def test_calculate_trend_improving(self, learning_service):
        """اختبار: حساب اتجاه متحسن"""
        record = AgentPerformanceRecord(
            agent_name='test',
            accuracy_trend=[1.0, 1.0, 1.0]  # 100%
        )
        
        trend = learning_service._calculate_trend(record)
        assert trend == "IMPROVING"
    
    def test_calculate_trend_declining(self, learning_service):
        """اختبار: حساب اتجاه متراجع"""
        record = AgentPerformanceRecord(
            agent_name='test',
            accuracy_trend=[0.0, 0.0, 0.0]  # 0%
        )
        
        trend = learning_service._calculate_trend(record)
        assert trend == "DECLINING"
    
    def test_calculate_trend_stable(self, learning_service):
        """اختبار: حساب اتجاه ثابت"""
        record = AgentPerformanceRecord(
            agent_name='test',
            accuracy_trend=[1.0, 0.0, 1.0]  # 66%
        )
        
        trend = learning_service._calculate_trend(record)
        assert trend == "STABLE"
    
    @pytest.mark.asyncio
    async def test_analyze_with_few_trades_v2(self, learning_service, mock_db):
        """اختبار: التحليل مع صفقات قليلة (الآن 3 بدل 5)"""
        mock_db.execute_query = AsyncMock(return_value=[
            {'id': 't1', 'pnl': 10.0},
            {'id': 't2', 'pnl': -5.0},
            {'id': 't3', 'pnl': 15.0}
        ])
        
        # min_predictions = 3، وعندنا 3 صفقات
        report = await learning_service.analyze_and_update_weights()
        
        # يجب أن يعمل (ليس فارغاً)
        assert report is not None
    
    def test_calculate_adjusted_weights_v2(self, learning_service):
        """اختبار: حساب الأوزان v2 مع التتابع"""
        agent_stats = {
            'good_agent': AgentPerformanceRecord(
                agent_name='good_agent',
                win_rate=80.0,
                avg_confidence=80.0,
                current_weight=0.15,
                trend='IMPROVING',
                consecutive_wins=4,
                accuracy_trend=[1.0, 1.0, 1.0, 1.0]
            ),
            'bad_agent': AgentPerformanceRecord(
                agent_name='bad_agent',
                win_rate=40.0,
                avg_confidence=60.0,
                current_weight=0.30,
                trend='DECLINING',
                consecutive_losses=4,
                accuracy_trend=[0.0, 0.0, 0.0, 0.0]
            ),
            'neutral_agent': AgentPerformanceRecord(
                agent_name='neutral_agent',
                win_rate=55.0,
                avg_confidence=70.0,
                current_weight=0.25,
                trend='STABLE',
                consecutive_wins=0,
                accuracy_trend=[1.0, 0.0, 1.0, 0.0]
            )
        }
        
        adjusted = learning_service._calculate_adjusted_weights_v2(agent_stats)
        
        # التحقق من أن المجموع = 1.0
        assert abs(sum(adjusted.values()) - 1.0) < 0.01
        
        # العامل الجيد يجب أن يزيد (streak bonus)
        assert adjusted['good_agent'] > 0.15
        
        # العامل السيئ يجب أن ينقص
        assert adjusted['bad_agent'] < 0.30
    
    def test_streak_bonus_increase(self, learning_service):
        """اختبار: تأثير streak bonus على الوزن"""
        agent_stats = {
            'streak_winner': AgentPerformanceRecord(
                agent_name='streak_winner',
                win_rate=60.0,  # عادي
                avg_confidence=70.0,
                current_weight=0.20,
                trend='STABLE',
                consecutive_wins=5,  # تتابع طويل!
                accuracy_trend=[1.0] * 5
            )
        }
        
        adjusted = learning_service._calculate_adjusted_weights_v2(agent_stats)
        
        # مع streak bonus، الوزن يجب أن يزيد
        assert adjusted['streak_winner'] > 0.20
    
    def test_get_agent_recommendation_streak(self, learning_service):
        """اختبار: توصية بناءً على التتابع"""
        # إضافة سجل وهمي مع تتابع
        learning_service.learning_history.append(
            LearningReport(
                report_date=datetime.now(timezone.utc).isoformat(),
                agents_performance={
                    'hot_agent': AgentPerformanceRecord(
                        agent_name='hot_agent',
                        trend='IMPROVING',
                        consecutive_wins=4,
                        win_rate=75.0
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
        
        rec = learning_service.get_agent_recommendation('hot_agent')
        assert rec == "INCREASE_CONFIDENCE"
    
    def test_failed_signals_insights(self, learning_service):
        """اختبار: الحصول على رؤى الصفقات الفاشلة"""
        # إضافة إشارة فاشلة
        learning_service._add_to_failed_memory(
            {'id': 't1', 'pnl': -10},
            'technical'
        )
        
        insights = learning_service.get_failed_signals_insights()
        assert len(insights) >= 1
        assert insights[0]['agent'] == 'technical'


class TestWeightBoundsV2:
    """اختبارات حدود الأوزان v2"""
    
    def test_weight_bounds_extended(self, learning_service):
        """اختبار: حدود الأوزان (5% - 45%)"""
        agent_stats = {
            'a': AgentPerformanceRecord(
                agent_name='a', win_rate=70.0, avg_confidence=80.0,
                current_weight=0.25, trend='IMPROVING',
                consecutive_wins=3,
                accuracy_trend=[1.0, 1.0, 1.0, 0.0]
            ),
            'b': AgentPerformanceRecord(
                agent_name='b', win_rate=50.0, avg_confidence=65.0,
                current_weight=0.25, trend='STABLE',
                accuracy_trend=[1.0, 0.0, 1.0, 0.0]
            ),
            'c': AgentPerformanceRecord(
                agent_name='c', win_rate=55.0, avg_confidence=70.0,
                current_weight=0.25, trend='STABLE',
                accuracy_trend=[0.0, 1.0, 0.0, 1.0]
            ),
            'd': AgentPerformanceRecord(
                agent_name='d', win_rate=45.0, avg_confidence=60.0,
                current_weight=0.25, trend='DECLINING',
                consecutive_losses=2,
                accuracy_trend=[0.0, 0.0, 1.0, 0.0]
            )
        }
        
        adjusted = learning_service._calculate_adjusted_weights_v2(agent_stats)
        
        # لا وزن يقل عن 5% أو يزيد عن 45%
        for weight in adjusted.values():
            assert 0.05 <= weight <= 0.45
        
        assert abs(sum(adjusted.values()) - 1.0) < 0.01


if __name__ == "__main__":
    pytest.main([__file__, "-v"])