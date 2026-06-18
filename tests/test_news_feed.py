"""
🧪 اختبارات Forex Factory News Feed
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.news_feed import (
    ForexFactoryScraper, ForexNews, NewsRiskManager,
    NewsImpact, Sentiment,
    get_news_feed, get_news_risk_manager
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
        'news_feed': {
            'enabled': True,
            'hours_before': 2,
            'hours_after': 1,
            'min_impact': 'medium',
            'auto_block_on_high': True
        },
        'risk_management': {
            'max_drawdown_stop': 10
        }
    }


@pytest.fixture
def news_feed(mock_db, config):
    """Forex Factory Scraper"""
    return ForexFactoryScraper(mock_db, config)


@pytest.fixture
def news_risk_manager(mock_db, config):
    """مدير مخاطر الأخبار"""
    return NewsRiskManager(get_news_feed(mock_db, config), config)


class TestForexNews:
    """اختبارات خبر الفوركس"""
    
    def test_news_init_high_impact(self):
        """اختبار تهيئة خبر عالي التأثير"""
        news = ForexNews(
            title="USD - Non-Farm Employment Change",
            date=datetime.utcnow(),
            time="14:30",
            currency="USD",
            impact=NewsImpact.HIGH,
            forecast="180K",
            previous="175K",
            actual=None,
            sentiment=Sentiment.BULLISH,
            trading_impact="NEGATIVE",
            confidence_adjustment=-15.0
        )
        
        assert news.title == "USD - Non-Farm Employment Change"
        assert news.currency == "USD"
        assert news.impact == NewsImpact.HIGH
        assert news.confidence_adjustment == -15.0
    
    def test_news_init_medium_impact(self):
        """اختبار تهيئة خبر متوسط التأثير"""
        news = ForexNews(
            title="GBP - CPI y/y",
            date=datetime.utcnow(),
            time="07:00",
            currency="GBP",
            impact=NewsImpact.MEDIUM,
            forecast="4.0%",
            previous="4.1%",
            actual=None,
            sentiment=Sentiment.BEARISH,
            trading_impact="POSITIVE",
            confidence_adjustment=-8.0
        )
        
        assert news.impact == NewsImpact.MEDIUM
        assert news.confidence_adjustment == -8.0


class TestNewsImpact:
    """اختبارات تأثير الأخبار"""
    
    def test_all_impacts(self):
        """اختبار جميع مستويات التأثير"""
        impacts = [NewsImpact.HIGH, NewsImpact.MEDIUM, NewsImpact.LOW]
        
        assert len(impacts) == 3
        assert NewsImpact.HIGH.value == "high"
        assert NewsImpact.MEDIUM.value == "medium"
        assert NewsImpact.LOW.value == "low"


class TestSentiment:
    """اختبارات معنويات السوق"""
    
    def test_all_sentiments(self):
        """اختبار جميع المعنويات"""
        sentiments = [Sentiment.BULLISH, Sentiment.BEARISH, Sentiment.NEUTRAL]
        
        assert len(sentiments) == 3
        assert Sentiment.BULLISH.value == "bullish"
        assert Sentiment.BEARISH.value == "bearish"
        assert Sentiment.NEUTRAL.value == "neutral"


class TestForexFactoryScraper:
    """اختبارات Forex Factory Scraper"""
    
    def test_is_relevant_to_gold_usd(self, news_feed):
        """اختبار صلة الخبر بالذهب - USD"""
        news = ForexNews(
            title="Test",
            date=datetime.utcnow(),
            time="14:30",
            currency="USD",
            impact=NewsImpact.HIGH,
            forecast=None,
            previous=None,
            actual=None,
            sentiment=Sentiment.NEUTRAL,
            trading_impact="NEUTRAL",
            confidence_adjustment=0
        )
        
        assert news_feed.is_relevant_to_gold(news) is True
    
    def test_is_relevant_to_gold_eur(self, news_feed):
        """اختبار صلة الخبر بالذهب - EUR"""
        news = ForexNews(
            title="Test",
            date=datetime.utcnow(),
            time="10:00",
            currency="EUR",
            impact=NewsImpact.MEDIUM,
            forecast=None,
            previous=None,
            actual=None,
            sentiment=Sentiment.NEUTRAL,
            trading_impact="NEUTRAL",
            confidence_adjustment=0
        )
        
        assert news_feed.is_relevant_to_gold(news) is True
    
    def test_not_relevant_to_gold_cny(self, news_feed):
        """اختبار عدم صلة الخبر بالذهب - CNY"""
        news = ForexNews(
            title="Test",
            date=datetime.utcnow(),
            time="10:00",
            currency="CNY",  # غير مدرج في الذهب
            impact=NewsImpact.HIGH,
            forecast=None,
            previous=None,
            actual=None,
            sentiment=Sentiment.NEUTRAL,
            trading_impact="NEUTRAL",
            confidence_adjustment=0
        )
        
        assert news_feed.is_relevant_to_gold(news) is False
    
    def test_should_block_trading_high_impact(self, news_feed):
        """اختبار إيقاف التداول عند خبر عالي التأثير"""
        now = datetime.utcnow()
        
        # إنشاء خبر بوقت مستقبلي (داخل النافذة 2 ساعة)
        future_time = now + timedelta(hours=1)  # بعد ساعة من الآن
        
        news = ForexNews(
            title="Test",
            date=future_time,
            time=f"{future_time.hour:02d}:{future_time.minute:02d}",  # وقت ديناميكي
            currency="USD",
            impact=NewsImpact.HIGH,
            forecast=None,
            previous=None,
            actual=None,
            sentiment=Sentiment.NEUTRAL,
            trading_impact="NEUTRAL",
            confidence_adjustment=0
        )
        
        result = news_feed.should_block_trading(news)
        
        # يجب أن يُرجع True لأن:
        # 1. التأثير HIGH
        # 2. العملة USD (في gold_currencies)
        # 3. الوقت ضمن النافذة (1 ساعة في المستقبل)
        assert result is True
    
    def test_should_not_block_trading_low_impact(self, news_feed):
        """اختبار عدم إيقاف التداول عند خبر منخفض التأثير"""
        news = ForexNews(
            title="Test",
            date=datetime.utcnow(),
            time="14:30",
            currency="USD",
            impact=NewsImpact.LOW,
            forecast=None,
            previous=None,
            actual=None,
            sentiment=Sentiment.NEUTRAL,
            trading_impact="NEUTRAL",
            confidence_adjustment=0
        )
        
        result = news_feed.should_block_trading(news)
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_fetch_calendar(self, news_feed):
        """اختبار جلب التقويم"""
        news_list = await news_feed.fetch_calendar(days=1)
        
        assert isinstance(news_list, list)
    
    def test_determine_trading_impact_usd_bearish(self, news_feed):
        """اختبار تأثير التداول - USD هابط"""
        event = {
            'currency': 'USD',
            'impact': NewsImpact.HIGH,
            'sentiment': Sentiment.BEARISH
        }
        
        result = news_feed._determine_trading_impact(event)
        
        assert result == 'POSITIVE'  # دولار أضعف = ذهب أقوى
    
    def test_determine_trading_impact_usd_bullish(self, news_feed):
        """اختبار تأثير التداول - USD صاعد"""
        event = {
            'currency': 'USD',
            'impact': NewsImpact.HIGH,
            'sentiment': Sentiment.BULLISH
        }
        
        result = news_feed._determine_trading_impact(event)
        
        assert result == 'NEGATIVE'
    
    def test_calculate_confidence_adjustment_high(self, news_feed):
        """اختبار تعديل الثقة - عالي التأثير"""
        event = {
            'impact': NewsImpact.HIGH,
            'currency': 'USD',
            'sentiment': Sentiment.NEUTRAL
        }
        
        result = news_feed._calculate_confidence_adjustment(event)
        
        assert result < 0  # يجب أن يكون سالب
        assert result <= -15  # الحد الأدنى للأثر العالي
    
    def test_calculate_confidence_adjustment_low(self, news_feed):
        """اختبار تعديل الثقة - منخفض التأثير"""
        event = {
            'impact': NewsImpact.LOW,
            'currency': 'GBP',
            'sentiment': Sentiment.BULLISH
        }
        
        result = news_feed._calculate_confidence_adjustment(event)
        
        assert result >= -20  # نطاق معقول
    
    def test_get_news_summary(self, news_feed):
        """اختبار ملخص الأخبار"""
        news_list = [
            ForexNews(
                title="High Impact 1", date=datetime.utcnow(), time="14:30",
                currency="USD", impact=NewsImpact.HIGH,
                forecast=None, previous=None, actual=None,
                sentiment=Sentiment.BULLISH, trading_impact="NEGATIVE",
                confidence_adjustment=-15
            ),
            ForexNews(
                title="Medium Impact 1", date=datetime.utcnow(), time="10:00",
                currency="EUR", impact=NewsImpact.MEDIUM,
                forecast=None, previous=None, actual=None,
                sentiment=Sentiment.NEUTRAL, trading_impact="NEUTRAL",
                confidence_adjustment=-8
            )
        ]
        
        summary = news_feed.get_news_summary(news_list)
        
        assert "High Impact" in summary
        assert "Medium Impact" in summary
    
    def test_format_telegram_alert(self, news_feed):
        """اختبار تنسيق تنبيه تيليجرام"""
        alerts = [
            {
                'title': 'USD - NFP',
                'currency': 'USD',
                'impact': 'high',
                'time': '14:30',
                'sentiment': 'bullish',
                'trading_impact': 'NEGATIVE',
                'confidence_adjustment': -15,
                'alert_level': '🔴 HIGH_IMPACT'
            },
            {
                'title': 'EUR - CPI',
                'currency': 'EUR',
                'impact': 'medium',
                'time': '10:00',
                'sentiment': 'bearish',
                'trading_impact': 'POSITIVE',
                'confidence_adjustment': -8,
                'alert_level': '🟡 MEDIUM_IMPACT'
            }
        ]
        
        formatted = news_feed.format_telegram_alert(alerts)
        
        assert 'Forex Factory' in formatted
        assert 'NFP' in formatted
        assert 'HIGH_IMPACT' in formatted


class TestNewsRiskManager:
    """اختبارات مدير مخاطر الأخبار"""
    
    @pytest.mark.asyncio
    async def test_should_block_new_trades_no_news(self, news_risk_manager, mock_db):
        """اختبار عدم إيقاف التداول بدون أخبار"""
        mock_db.execute_query = AsyncMock(return_value=[])
        news_risk_manager.news_feed.check_upcoming_news = AsyncMock(return_value=[])
        
        result = await news_risk_manager.should_block_new_trades()
        
        assert result['block_trades'] is False
    
    @pytest.mark.asyncio
    async def test_should_block_new_trades_with_high_impact(self, news_risk_manager):
        """اختبار إيقاف التداول مع أخبار عالية التأثير"""
        news_risk_manager.news_feed.check_upcoming_news = AsyncMock(return_value=[
            {
                'title': 'USD - NFP',
                'currency': 'USD',
                'impact': 'high',
                'time': '14:30',
                'confidence_adjustment': -15,
                'alert_level': '🔴 HIGH_IMPACT'
            }
        ])
        
        result = await news_risk_manager.should_block_new_trades()
        
        assert result['block_trades'] is True
        assert len(result['reasons']) > 0
    
    @pytest.mark.asyncio
    async def test_adjust_signal_confidence(self, news_risk_manager):
        """اختبار تعديل ثقة الإشارة"""
        base_confidence = 80.0
        current_news = [
            {'confidence_adjustment': -10},
            {'confidence_adjustment': -5}
        ]
        
        adjusted = await news_risk_manager.adjust_signal_confidence(
            base_confidence, current_news
        )
        
        assert adjusted < base_confidence
        assert adjusted >= 65  # 80 - 10 - 5
    
    @pytest.mark.asyncio
    async def test_adjust_signal_confidence_no_news(self, news_risk_manager):
        """اختبار تعديل الثقة بدون أخبار"""
        base_confidence = 80.0
        
        adjusted = await news_risk_manager.adjust_signal_confidence(
            base_confidence, []
        )
        
        assert adjusted == 80.0


class TestGoldCurrencies:
    """اختبارات عملات الذهب"""
    
    def test_gold_currencies_list(self, news_feed):
        """اختبار قائمة عملات الذهب"""
        expected = ['USD', 'EUR', 'GBP', 'AUD', 'JPY', 'CAD', 'CHF']
        
        assert news_feed.gold_currencies == expected
    
    def test_usd_in_gold_currencies(self, news_feed):
        """اختبار USD في عملات الذهب"""
        assert 'USD' in news_feed.gold_currencies
    
    def test_cny_not_in_gold_currencies(self, news_feed):
        """اختبار CNY ليست في عملات الذهب"""
        assert 'CNY' not in news_feed.gold_currencies


if __name__ == "__main__":
    pytest.main([__file__, "-v"])