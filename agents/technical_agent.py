"""
🤖 Technical Agent - Gold AI Signals
وكيل التحليل الفني المدعوم بالذكاء الاصطناعي
"""

import logging
from typing import Dict, Any, Optional
from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class TechnicalAgent(BaseAgent):
    """
    🤖 وكيل التحليل الفني
    
    يجمع بين:
    1️⃣ المؤشرات الفنية الكلاسيكية (RSI, EMA, MACD)
    2️⃣ الذكاء الاصطناعي (ChatGPT/Claude/Grok/Gemini)
    
    للحصول على تحليل دقيق ومتكامل
    """
    
    def __init__(self, config: Dict, ai_service=None):
        super().__init__(config)
        self.ai_service = ai_service
        self.weight = config.get('agent_weights', {}).get('technical', 0.2)
    
    def analyze(self, data: Dict) -> Dict[str, Any]:
        """
        🔍 تحليل الشارت باستخدام المؤشرات + AI (sync version)
        
        Args:
            data: {
                'candles': [...],
                'price_data': {...},
                'indicators': {...},
                'timeframe': '1h',
                'symbol': 'XAUUSD'
            }
        
        Returns:
            {
                'signal': 'BUY' | 'SELL' | 'WAIT',
                'confidence': 0-100,
                'reasoning': '...',
                'indicators': {...},
                'ai_analysis': {...},
                'final_decision': '...'
            }
        """
        # Use synchronous fallback if AI not available
        return self._sync_analyze(data)
    
    def _sync_analyze(self, data: Dict) -> Dict[str, Any]:
        """التحليل المتزامن (بدون AI)"""
        
        candles = data.get('data', data.get('candles', []))
        indicators = data.get('indicators', {})
        
        # التحليل الفني الكلاسيكي فقط
        technical_analysis = self._technical_analysis(candles, indicators)
        
        return {
            'agent': 'technical',
            'signal': technical_analysis.get('classic_signal', 'WAIT'),
            'confidence': self._calculate_classic_confidence(technical_analysis),
            'weight': self.weight,
            'technical': technical_analysis,
            'ai': {'available': False, 'reason': 'sync mode'},
            'reasoning': technical_analysis.get('trend', 'N/A'),
            'timestamp': self.now_iso()
        }
    
    async def analyze_async(self, data: Dict) -> Dict[str, Any]:
        """
        🔍 تحليل الشارت باستخدام المؤشرات + AI (async version)
        """
        
        symbol = data.get('symbol', 'XAUUSD')
        candles = data.get('data', data.get('candles', []))
        price_data = data.get('price_data', {})
        indicators = data.get('indicators', {})
        timeframe = data.get('timeframe', '1h')
        
        # 1️⃣ التحليل الفني الكلاسيكي
        technical_analysis = self._technical_analysis(candles, indicators)
        
        # 2️⃣ التحليل بالذكاء الاصطناعي (async)
        ai_analysis = {}
        if self.ai_service:
            ai_analysis = await self._ai_analysis(
                symbol, price_data, indicators, timeframe
            )
        
        # 3️⃣ دمج التحليلات
        final_signal, final_confidence = self._combine_analysis(
            technical_analysis, ai_analysis
        )
        
        return {
            'agent': 'technical',
            'signal': final_signal,
            'confidence': final_confidence,
            'weight': self.weight,
            'technical': technical_analysis,
            'ai': ai_analysis,
            'reasoning': ai_analysis.get('reasoning', technical_analysis.get('trend', 'N/A')),
            'timestamp': self.now_iso()
        }
    
    def _technical_analysis(self, candles: list, indicators: Dict) -> Dict:
        """
        📊 التحليل الفني الكلاسيكي
        """
        
        # المؤشرات من البيانات
        rsi = indicators.get('rsi', 50)
        ema_50 = indicators.get('ema_50', 0)
        ema_200 = indicators.get('ema_200', 0)
        macd_hist = indicators.get('macd_histogram', 0)
        atr = indicators.get('atr', 10)
        current_price = indicators.get('current_price', 0)
        support = indicators.get('support', 0)
        resistance = indicators.get('resistance', 0)
        
        # تحديد الاتجاه
        if ema_50 > ema_200:
            ema_trend = "UP"
            trend_score = 1
        elif ema_50 < ema_200:
            ema_trend = "DOWN"
            trend_score = -1
        else:
            ema_trend = "SIDEWAYS"
            trend_score = 0
        
        # تحليل RSI
        if rsi > 70:
            rsi_signal = "OVERBOUGHT"
            rsi_score = -1
        elif rsi < 30:
            rsi_signal = "OVERSOLD"
            rsi_score = 1
        else:
            rsi_signal = "NEUTRAL"
            rsi_score = 0
        
        # تحليل MACD
        if macd_hist > 0:
            macd_signal = "BULLISH"
            macd_score = 1
        else:
            macd_signal = "BEARISH"
            macd_score = -1
        
        # النتيجة الكلاسيكية
        total_score = trend_score + rsi_score + macd_score
        
        if total_score >= 2:
            classic_signal = "BUY"
        elif total_score <= -2:
            classic_signal = "SELL"
        else:
            classic_signal = "WAIT"
        
        return {
            'trend': ema_trend,
            'rsi': rsi,
            'rsi_signal': rsi_signal,
            'macd': macd_signal,
            'atr': atr,
            'support': support,
            'resistance': resistance,
            'classic_signal': classic_signal,
            'classic_score': total_score,
            'current_price': current_price
        }
    
    async def _ai_analysis(
        self,
        symbol: str,
        price_data: Dict,
        indicators: Dict,
        timeframe: str
    ) -> Dict:
        """
        🤖 التحليل بالذكاء الاصطناعي
        """
        
        if not self.ai_service:
            return {'available': False, 'error': 'AI service not configured'}
        
        try:
            response = await self.ai_service.analyze_chart(
                symbol=symbol,
                price_data=price_data,
                technical_indicators=indicators,
                timeframe=timeframe,
                agent_type='technical'
            )
            
            if response.success:
                parsed = self.ai_service.parse_json_response(response.content)
                
                if parsed:
                    return {
                        'available': True,
                        'signal': parsed.get('signal', 'WAIT'),
                        'confidence': parsed.get('confidence', 50),
                        'reasoning': parsed.get('reasoning', ''),
                        'entry_zone': parsed.get('entry_zone', ''),
                        'stop_loss': parsed.get('stop_loss', ''),
                        'take_profit_1': parsed.get('take_profit_1', ''),
                        'take_profit_2': parsed.get('take_profit_2', ''),
                        'risk_reward': parsed.get('risk_reward', ''),
                        'provider': response.provider,
                        'model': response.model,
                        'tokens_used': response.tokens_used,
                        'cost': response.cost
                    }
            
            return {
                'available': True,
                'error': response.error,
                'signal': 'WAIT',
                'confidence': 50
            }
            
        except Exception as e:
            logger.error(f"❌ خطأ في تحليل AI: {e}")
            return {
                'available': False,
                'error': str(e),
                'signal': 'WAIT',
                'confidence': 50
            }
    
    def _combine_analysis(
        self,
        technical: Dict,
        ai: Dict
    ) -> tuple:
        """
        🔄 دمج التحليل الكلاسيكي مع AI
        """
        
        classic_signal = technical.get('classic_signal', 'WAIT')
        classic_confidence = self._calculate_classic_confidence(technical)
        
        ai_signal = ai.get('signal', 'WAIT') if ai.get('available') else 'WAIT'
        ai_confidence = ai.get('confidence', 50) if ai.get('available') else 0
        
        # إذا AI متاح، نعطيه وزن أكبر
        if ai.get('available'):
            # 60% AI + 40% كلاسيكي
            final_signal = ai_signal if ai_confidence > 60 else classic_signal
            final_confidence = (ai_confidence * 0.6) + (classic_confidence * 0.4)
        else:
            # بدون AI، نستخدم الكلاسيكي فقط
            final_signal = classic_signal
            final_confidence = classic_confidence
        
        return final_signal, round(final_confidence, 1)
    
    def _calculate_classic_confidence(self, technical: Dict) -> float:
        """حساب ثقة التحليل الكلاسيكي"""
        
        score = abs(technical.get('classic_score', 0))
        
        # إضافة مؤثرات أخرى
        rsi = technical.get('rsi', 50)
        if 35 <= rsi <= 65:
            score += 0.5  # RSI في منطقة محايدة
        
        # تحويل للنسبة (0-100)
        confidence = min(score * 20 + 50, 95)
        
        return confidence
    
    def get_analysis_summary(self, result: Dict) -> str:
        """تلخيص التحليل لرسالة تيليجرام"""
        
        technical = result.get('technical', {})
        ai = result.get('ai', {})
        
        lines = [
            f"📊 *التحليل الفني*",
            f"├ الاتجاه: {technical.get('trend', 'N/A')}",
            f"├ RSI: {technical.get('rsi', 'N/A')}",
            f"├ MACD: {technical.get('macd', 'N/A')}",
            f"├ الدعم: {technical.get('support', 'N/A')}",
            f"└ المقاومة: {technical.get('resistance', 'N/A')}"
        ]
        
        if ai.get('available'):
            lines.extend([
                "",
                f"🤖 *تحليل AI ({ai.get('provider', 'AI')})*",
                f"├ الإشارة: {ai.get('signal', 'N/A')}",
                f"├ الثقة: {ai.get('confidence', 'N/A')}%",
                f"├ SL: {ai.get('stop_loss', 'N/A')}",
                f"├ TP1: {ai.get('take_profit_1', 'N/A')}",
                f"└ R/R: {ai.get('risk_reward', 'N/A')}"
            ])
        
        return "\n".join(lines)