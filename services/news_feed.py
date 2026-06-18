"""
🔗 Forex Factory News Feed - Gold AI Signals
جلب الأخبار تلقائياً من Forex Factory
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
import re

logger = logging.getLogger(__name__)


class NewsImpact(Enum):
    """تأثير الأخبار"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Sentiment(Enum):
    """معنويات السوق"""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class ForexNews:
    """خبر فوركس"""
    title: str
    date: datetime
    time: str
    currency: str
    impact: NewsImpact
    forecast: Optional[str]
    previous: Optional[str]
    actual: Optional[str]
    sentiment: Sentiment
    trading_impact: str  # POSITIVE, NEGATIVE, NEUTRAL
    confidence_adjustment: float


class ForexFactoryScraper:
    """
    🔗 Forex Factory News Scraper
    يجلب الأخبار من Forex Factory Calendar
    """
    
    BASE_URL = "https://www.forexfactory.com"
    
    def __init__(self, database_service, config: Dict):
        self.db = database_service
        self.config = config
        self.news_config = self._load_news_config()
        
        # العملات المؤثرة على الذهب
        self.gold_currencies = ['USD', 'EUR', 'GBP', 'AUD', 'JPY', 'CAD', 'CHF']
        
    def _load_news_config(self) -> Dict:
        """تحميل إعدادات الأخبار"""
        return self.config.get('news_feed', {
            'enabled': True,
            'hours_before': 2,  # أخبار قبل ساعتين
            'hours_after': 1,   # أخبار بعد ساعة
            'min_impact': 'medium',  # الحد الأدنى للتأثير
            'auto_block_on_high': True
        })
    
    async def fetch_calendar(self, days: int = 3) -> List[ForexNews]:
        """
        📡 جلب تقويم Forex Factory
        """
        try:
            # TODO: يمكن استخدام BeautifulSoup أو Selenium لجلب البيانات
            # حالياً نستخدم HTTP request بسيط
            
            from services.market_data import get_market_data_service
            market = get_market_data_service(self.config)
            
            # جلب الصفحة
            url = f"{self.BASE_URL}/calendar.php"
            
            # محاولة الجلب (placeholder - يتطلب implementation حقيقي)
            # في الإنتاج، يجب استخدام:
            # 1. BeautifulSoup للـ HTML parsing
            # 2. أو Selenium للـ JavaScript rendering
            
            logger.info(f"📡 جلب تقويم Forex Factory - الأيام: {days}")
            
            # إرجاع بيانات تجريبية للاختبار
            return self._generate_mock_news(days)
            
        except Exception as e:
            logger.error(f"❌ خطأ في جلب التقويم: {e}")
            return []
    
    def _generate_mock_news(self, days: int) -> List[ForexNews]:
        """توليد بيانات تجريبية للأخبار"""
        mock_events = [
            {
                'title': 'USD - Non-Farm Employment Change',
                'currency': 'USD',
                'impact': NewsImpact.HIGH,
                'time': '14:30',
                'forecast': '180K',
                'previous': '175K',
                'sentiment': Sentiment.BULLISH
            },
            {
                'title': 'USD - Unemployment Rate',
                'currency': 'USD',
                'impact': NewsImpact.HIGH,
                'time': '14:30',
                'forecast': '3.8%',
                'previous': '3.9%',
                'sentiment': Sentiment.BULLISH
            },
            {
                'title': 'EUR - ECB Interest Rate Decision',
                'currency': 'EUR',
                'impact': NewsImpact.HIGH,
                'time': '14:45',
                'forecast': '4.25%',
                'previous': '4.50%',
                'sentiment': Sentiment.BEARISH
            },
            {
                'title': 'GBP - CPI y/y',
                'currency': 'GBP',
                'impact': NewsImpact.MEDIUM,
                'time': '07:00',
                'forecast': '4.0%',
                'previous': '4.1%',
                'sentiment': Sentiment.BEARISH
            },
            {
                'title': 'USD - Core Retail Sales m/m',
                'currency': 'USD',
                'impact': NewsImpact.MEDIUM,
                'time': '12:30',
                'forecast': '0.3%',
                'previous': '0.1%',
                'sentiment': Sentiment.BULLISH
            },
            {
                'title': 'XAU - Fed Chair Speech',
                'currency': 'USD',
                'impact': NewsImpact.HIGH,
                'time': '20:00',
                'forecast': None,
                'previous': None,
                'sentiment': Sentiment.NEUTRAL
            },
            {
                'title': 'AUD - Employment Change',
                'currency': 'AUD',
                'impact': NewsImpact.MEDIUM,
                'time': '01:30',
                'forecast': '25.0K',
                'previous': '32.6K',
                'sentiment': Sentiment.BULLISH
            },
            {
                'title': 'CAD - BoC Rate Statement',
                'currency': 'CAD',
                'impact': NewsImpact.HIGH,
                'time': '14:00',
                'forecast': '5.0%',
                'previous': '5.0%',
                'sentiment': Sentiment.NEUTRAL
            }
        ]
        
        news_list = []
        now = datetime.utcnow()
        
        for i, event in enumerate(mock_events):
            news = ForexNews(
                title=event['title'],
                date=now + timedelta(hours=i*4),
                time=event['time'],
                currency=event['currency'],
                impact=event['impact'],
                forecast=event.get('forecast'),
                previous=event.get('previous'),
                actual=event.get('actual'),
                sentiment=event['sentiment'],
                trading_impact=self._determine_trading_impact(event),
                confidence_adjustment=self._calculate_confidence_adjustment(event)
            )
            news_list.append(news)
        
        return news_list
    
    def _determine_trading_impact(self, event: Dict) -> str:
        """تحديد تأثير التداول"""
        sentiment = event['sentiment']
        currency = event['currency']
        impact = event['impact']
        
        # تأثير الأخبار على الذهب
        if currency == 'USD':
            if sentiment == Sentiment.BEARISH:
                return 'POSITIVE'  # دولار أضعف = ذهب أقوى
            elif sentiment == Sentiment.BULLISH:
                return 'NEGATIVE'
        elif currency in ['EUR', 'GBP', 'AUD', 'CAD']:
            if sentiment == Sentiment.BULLISH:
                return 'POSITIVE'
        
        return 'NEUTRAL'
    
    def _calculate_confidence_adjustment(self, event: Dict) -> float:
        """حساب تعديل مستوى الثقة"""
        base_adjustment = 0
        
        # تعديل حسب التأثير
        if event['impact'] == NewsImpact.HIGH:
            base_adjustment -= 15  # تقليص الثقة عند أخبار عالية
        elif event['impact'] == NewsImpact.MEDIUM:
            base_adjustment -= 8
        
        # تعديل حسب العملة
        if event['currency'] == 'USD':
            base_adjustment -= 5  # أخبار USD تؤثر على الذهب
        
        # تعديل حسب المعنويات
        if event['sentiment'] == Sentiment.NEUTRAL:
            base_adjustment -= 3
        
        return max(base_adjustment, -30)  # الحد الأدنى -30%
    
    def is_relevant_to_gold(self, news: ForexNews) -> bool:
        """هل الخبر ذو صلة بالذهب؟"""
        return news.currency in self.gold_currencies
    
    def is_within_trading_window(self, news: ForexNews) -> bool:
        """هل الخبر ضمن نافذة التداول؟"""
        hours_before = self.news_config.get('hours_before', 2)
        hours_after = self.news_config.get('hours_after', 1)
        
        now = datetime.utcnow()
        
        # استخدام تاريخ الخبر مع وقت الخبر الفعلي
        hour, minute = map(int, news.time.split(':'))
        news_time = news.date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # حساب الفرق بين وقت الخبر والوقت الحالي
        time_diff = (news_time - now).total_seconds() / 3600
        
        return -hours_before <= time_diff <= hours_after
    
    async def check_upcoming_news(self, hours: int = 3) -> List[Dict]:
        """
        🔍 فحص الأخبار القادمة
        """
        try:
            news_list = await self.fetch_calendar(days=1)
            
            relevant_news = []
            for news in news_list:
                if not self.is_relevant_to_gold(news):
                    continue
                if not self.is_within_trading_window(news):
                    continue
                
                # إضافة تنبيه
                relevant_news.append({
                    'title': news.title,
                    'currency': news.currency,
                    'impact': news.impact.value,
                    'time': news.time,
                    'sentiment': news.sentiment.value,
                    'trading_impact': news.trading_impact,
                    'confidence_adjustment': news.confidence_adjustment,
                    'alert_level': self._get_alert_level(news)
                })
                
                # حفظ في قاعدة البيانات
                await self._log_news(news)
            
            return relevant_news
            
        except Exception as e:
            logger.error(f"❌ خطأ في فحص الأخبار: {e}")
            return []
    
    def _get_alert_level(self, news: ForexNews) -> str:
        """تحديد مستوى التنبيه"""
        if news.impact == NewsImpact.HIGH and self.news_config.get('auto_block_on_high'):
            return "🔴 HIGH_IMPACT"
        elif news.impact == NewsImpact.MEDIUM:
            return "🟡 MEDIUM_IMPACT"
        return "🟢 LOW_IMPACT"
    
    async def _log_news(self, news: ForexNews):
        """حفظ الخبر في قاعدة البيانات"""
        try:
            query = """
                INSERT INTO news_log 
                (headline, source, impact, affected_pairs, trading_impact, 
                 confidence_adjustment, sentiment, logged_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT DO NOTHING
            """
            
            await self.db.execute_query(
                query,
                [
                    news.title,
                    'ForexFactory',
                    news.impact.value,
                    [news.currency],
                    news.trading_impact,
                    news.confidence_adjustment,
                    news.sentiment.value
                ]
            )
            
            logger.info(f"✅ تم تسجيل الخبر: {news.title}")
            
        except Exception as e:
            logger.error(f"❌ خطأ في تسجيل الخبر: {e}")
    
    def should_block_trading(self, news: ForexNews) -> bool:
        """هل يجب إيقاف التداول بسبب هذا الخبر؟"""
        if not self.news_config.get('auto_block_on_high', True):
            return False
        
        return (
            news.impact == NewsImpact.HIGH and
            news.currency in self.gold_currencies and
            self.is_within_trading_window(news)
        )
    
    def get_news_summary(self, news_list: List[ForexNews]) -> str:
        """ملخص الأخبار"""
        high_impact = [n for n in news_list if n.impact == NewsImpact.HIGH]
        medium_impact = [n for n in news_list if n.impact == NewsImpact.MEDIUM]
        
        summary = []
        
        if high_impact:
            summary.append(f"🔴 أخبار عالية التأثير: {len(high_impact)}")
            for n in high_impact[:3]:
                summary.append(f"   • {n.title} ({n.currency})")
        
        if medium_impact:
            summary.append(f"\n🟡 أخبار متوسطة التأثير: {len(medium_impact)}")
            for n in medium_impact[:3]:
                summary.append(f"   • {n.title} ({n.currency})")
        
        return "\n".join(summary) if summary else "📰 لا توجد أخبار مهمة"
    
    def format_telegram_alert(self, news_list: List[Dict]) -> str:
        """تنسيق تنبيه الأخبار لتيليجرام"""
        if not news_list:
            return "📰 لا توجد أخبار مؤثرة في الساعات القادمة"
        
        lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            "📰 *تقويم Forex Factory*",
            "━━━━━━━━━━━━━━━━━━━━",
            ""
        ]
        
        # أخبار عالية التأثير
        high = [n for n in news_list if n['impact'] == 'high']
        if high:
            lines.append("🔴 *أخبار عالية التأثير*")
            for n in high:
                lines.append(f"├ {n['time']} - {n['title']}")
                lines.append(f"│  💱 {n['currency']} | 📊 {n['alert_level']}")
                lines.append(f"│  📈 تأثير: {n['trading_impact']}")
            lines.append("")
        
        # أخبار متوسطة التأثير
        medium = [n for n in news_list if n['impact'] == 'medium']
        if medium:
            lines.append("🟡 *أخبار متوسطة التأثير*")
            for n in medium[:5]:
                lines.append(f"├ {n['time']} - {n['title']}")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "💡 *توصية*",
            "• 🔴 أخبار HIGH = تقليص الثقة" if high else "",
            "• ⏰ تجنب فتح صفقات جديدة قبل 30 دقيقة من الخبر",
            "━━━━━━━━━━━━━━━━━━━━"
        ])
        
        return "\n".join(filter(None, lines))


class NewsRiskManager:
    """
    🛡️ مدير مخاطر الأخبار
    يطبق قواعد الأخبار على التداول
    """
    
    def __init__(self, news_feed: ForexFactoryScraper, config: Dict):
        self.news_feed = news_feed
        self.config = config
    
    async def should_block_new_trades(self) -> Dict:
        """هل يجب إيقاف الصفقات الجديدة؟"""
        upcoming_news = await self.news_feed.check_upcoming_news(hours=1)
        
        blocking_reasons = []
        confidence_penalty = 0
        
        for news in upcoming_news:
            if news['alert_level'] == '🔴 HIGH_IMPACT':
                blocking_reasons.append(news['title'])
                confidence_penalty += abs(news['confidence_adjustment'])
        
        return {
            'block_trades': len(blocking_reasons) > 0,
            'reasons': blocking_reasons,
            'confidence_penalty': min(confidence_penalty, 30),
            'message': f"🚫 إيقاف مؤقت بسبب {len(blocking_reasons)} خبر عالي التأثير" 
                       if blocking_reasons else "✅ يمكن التداول"
        }
    
    async def adjust_signal_confidence(
        self, base_confidence: float, current_news: List[Dict]
    ) -> float:
        """تعديل ثقة الإشارة بناءً على الأخبار"""
        penalty = 0
        
        for news in current_news:
            penalty += abs(news.get('confidence_adjustment', 0))
        
        adjusted = base_confidence - penalty  # خصم وليس جمع!
        return max(adjusted, 0)  # لا يقل عن 0


# Singleton instances
_news_feed: Optional[ForexFactoryScraper] = None
_news_risk_manager: Optional[NewsRiskManager] = None


def get_news_feed(db, config: Dict) -> ForexFactoryScraper:
    """الحصول على instance Forex Factory"""
    global _news_feed
    if _news_feed is None:
        _news_feed = ForexFactoryScraper(db, config)
    return _news_feed


def get_news_risk_manager(db, config: Dict) -> NewsRiskManager:
    """الحصول على instance مدير المخاطر"""
    global _news_risk_manager
    if _news_risk_manager is None:
        _news_risk_manager = NewsRiskManager(get_news_feed(db, config), config)
    return _news_risk_manager