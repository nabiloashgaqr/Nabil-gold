"""
🤖 Technical Agent - Gold AI Signals
وكيل التحليل الفني المدعوم بالذكاء الاصطناعي
"""

import logging
from typing import Dict, Any, List, Tuple
from .base_agent import BaseAgent
from utils.indicators import calculate_ema, calculate_rsi, calculate_macd, calculate_atr, calculate_bollinger_bands, detect_support_resistance, detect_swing_points

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
        📊 التحليل الفني الكلاسيكي المطور v3.0

        يحسب المؤشرات من الشموع إذا لم تصل جاهزة، ويضيف:
        - EMA ribbon 8/21/50/100/200
        - RSI-14 + RSI-7 momentum
        - MACD histogram slope
        - ATR percentile / volatility regime
        - Bollinger squeeze / %B
        - RSI divergence مبسط
        - ADX مبسط لقوة الترند
        """
        closes = [self._f(c.get('close')) for c in candles if isinstance(c, dict)]
        if not closes:
            closes = []
        current_price = self._f(indicators.get('current_price'), closes[-1] if closes else 0)

        ema_values = self._ema_pack(closes)
        ema_8 = self._value_or_indicator(indicators, 'ema_8', ema_values.get(8, 0))
        ema_21 = self._value_or_indicator(indicators, 'ema_21', ema_values.get(21, 0))
        ema_50 = self._value_or_indicator(indicators, 'ema_50', ema_values.get(50, 0))
        ema_100 = self._value_or_indicator(indicators, 'ema_100', ema_values.get(100, 0))
        ema_200 = self._value_or_indicator(indicators, 'ema_200', ema_values.get(200, 0))

        rsi_14_series = calculate_rsi(closes, 14) if closes else []
        rsi_7_series = calculate_rsi(closes, 7) if closes else []
        rsi = self._value_or_indicator(indicators, 'rsi', self._last(rsi_14_series, 50))
        rsi_7 = self._value_or_indicator(indicators, 'rsi_7', self._last(rsi_7_series, rsi))

        macd = calculate_macd(closes) if closes else {'latest': {'histogram': 0, 'macd': 0, 'signal': 0}, 'histogram': []}
        macd_hist = self._value_or_indicator(indicators, 'macd_histogram', macd.get('latest', {}).get('histogram', 0))
        macd_slope = self._slope([x for x in macd.get('histogram', []) if x is not None], lookback=5)

        atr_series = calculate_atr(candles, 14) if candles else []
        atr = self._value_or_indicator(indicators, 'atr', self._last(atr_series, 1.5))
        atr_percentile = self._percentile_rank([x for x in atr_series if x is not None], atr)
        volatility_regime = 'HIGH' if atr_percentile >= 80 else 'LOW' if atr_percentile <= 20 else 'NORMAL'

        bb = calculate_bollinger_bands(closes, 20, 2) if closes else {'latest': {'upper': 0, 'middle': 0, 'lower': 0}}
        bb_latest = bb.get('latest', {})
        bb_upper = self._f(bb_latest.get('upper'))
        bb_middle = self._f(bb_latest.get('middle'))
        bb_lower = self._f(bb_latest.get('lower'))
        bb_width = (bb_upper - bb_lower) / max(abs(bb_middle), 0.01) if bb_middle else 0
        bollinger_squeeze = bb_width < 0.004 if current_price > 1000 else bb_width < 0.002
        bollinger_percent_b = (current_price - bb_lower) / max(bb_upper - bb_lower, 0.01) if bb_upper and bb_lower else 0.5

        levels = detect_support_resistance(candles[-120:], lookback=80) if candles else {'supports': [], 'resistances': []}
        support = self._value_or_indicator(indicators, 'support', self._nearest_below(current_price, levels.get('supports', [])))
        resistance = self._value_or_indicator(indicators, 'resistance', self._nearest_above(current_price, levels.get('resistances', [])))

        adx_value, adx_signal = self._calculate_adx_proxy(candles)
        divergence = self._rsi_divergence(candles, rsi_14_series)
        ema_ribbon = self._ema_ribbon_state(current_price, [ema_8, ema_21, ema_50, ema_100, ema_200])

        score = 0.0
        reasons: List[str] = []

        # EMA trend / ribbon
        if ema_ribbon['state'] == 'BULLISH_ALIGNMENT':
            score += 2.0; reasons.append('EMA ribbon bullish')
        elif ema_ribbon['state'] == 'BEARISH_ALIGNMENT':
            score -= 2.0; reasons.append('EMA ribbon bearish')
        elif ema_50 > ema_200:
            score += 1.0; reasons.append('EMA50 above EMA200')
        elif ema_50 < ema_200:
            score -= 1.0; reasons.append('EMA50 below EMA200')

        # RSI range and divergence
        if divergence == 'REGULAR_BULLISH' or divergence == 'HIDDEN_BULLISH':
            score += 1.4; reasons.append(f'RSI divergence {divergence}')
        elif divergence == 'REGULAR_BEARISH' or divergence == 'HIDDEN_BEARISH':
            score -= 1.4; reasons.append(f'RSI divergence {divergence}')
        if 40 <= rsi <= 80 and ema_50 >= ema_200:
            score += 0.6; reasons.append('RSI bullish range')
        elif 20 <= rsi <= 60 and ema_50 <= ema_200:
            score -= 0.6; reasons.append('RSI bearish range')
        elif rsi > 75:
            score -= 0.8; reasons.append('RSI overbought warning')
        elif rsi < 25:
            score += 0.8; reasons.append('RSI oversold bounce potential')

        # MACD
        if macd_hist > 0 and macd_slope >= 0:
            score += 1.0; reasons.append('MACD bullish and improving')
        elif macd_hist < 0 and macd_slope <= 0:
            score -= 1.0; reasons.append('MACD bearish and weakening')
        elif macd_hist > 0:
            score += 0.5; reasons.append('MACD bullish')
        elif macd_hist < 0:
            score -= 0.5; reasons.append('MACD bearish')

        # Bollinger / volatility
        if bollinger_squeeze:
            reasons.append('Bollinger squeeze - wait for breakout confirmation')
            score *= 0.8
        if bollinger_percent_b < 0.10 and score >= 0:
            score += 0.5; reasons.append('near lower Bollinger band')
        elif bollinger_percent_b > 0.90 and score <= 0:
            score -= 0.5; reasons.append('near upper Bollinger band')

        # ADX trend strength
        if adx_value >= 25 and score > 0:
            score += 0.7; reasons.append('ADX confirms trend strength')
        elif adx_value >= 25 and score < 0:
            score -= 0.7; reasons.append('ADX confirms trend strength')
        elif adx_value < 15:
            score *= 0.75; reasons.append('ADX weak/no-trend')

        # ATR regime
        if volatility_regime == 'LOW':
            score *= 0.85; reasons.append('low ATR regime')
        elif volatility_regime == 'HIGH':
            reasons.append('high ATR regime - wider stops required')

        if score >= 2.5:
            classic_signal = 'BUY'
        elif score <= -2.5:
            classic_signal = 'SELL'
        else:
            classic_signal = 'WAIT'

        ema_trend = 'UP' if ema_50 > ema_200 else 'DOWN' if ema_50 < ema_200 else 'SIDEWAYS'
        rsi_signal = 'OVERBOUGHT' if rsi > 70 else 'OVERSOLD' if rsi < 30 else 'NEUTRAL'
        macd_signal = 'BULLISH' if macd_hist > 0 else 'BEARISH' if macd_hist < 0 else 'NEUTRAL'

        return {
            'trend': ema_trend,
            'market_regime': {
                'trend_direction': ema_trend,
                'trend_strength': 'STRONG' if adx_value >= 25 else 'WEAK' if adx_value < 15 else 'MODERATE',
                'volatility_regime': volatility_regime,
                'market_phase': 'SQUEEZE' if bollinger_squeeze else 'TRENDING' if adx_value >= 25 else 'RANGING',
                'adx_value': round(adx_value, 2),
                'atr_percentile': round(atr_percentile, 1),
            },
            'rsi': round(rsi, 2),
            'rsi_7': round(rsi_7, 2),
            'rsi_signal': rsi_signal,
            'rsi_divergence': divergence,
            'macd': macd_signal,
            'macd_histogram': round(macd_hist, 4),
            'macd_histogram_slope': round(macd_slope, 4),
            'ema_ribbon': ema_ribbon,
            'ema_values': {'ema_8': round(ema_8, 2), 'ema_21': round(ema_21, 2), 'ema_50': round(ema_50, 2), 'ema_100': round(ema_100, 2), 'ema_200': round(ema_200, 2)},
            'bollinger': {'upper': round(bb_upper, 2), 'middle': round(bb_middle, 2), 'lower': round(bb_lower, 2), 'width': round(bb_width, 5), 'percent_b': round(bollinger_percent_b, 2), 'squeeze': bollinger_squeeze},
            'atr': round(atr, 2),
            'support': round(support, 2) if support else 0,
            'resistance': round(resistance, 2) if resistance else 0,
            'classic_signal': classic_signal,
            'classic_score': round(score, 2),
            'current_price': current_price,
            'reasons': reasons[:10],
            'indicators_raw': {'atr': atr, 'rsi': rsi, 'macd_histogram': macd_hist},
            'key_levels': {'nearest_support': round(support, 2) if support else 0, 'nearest_resistance': round(resistance, 2) if resistance else 0},
        }

    def _ema_pack(self, closes: List[float]) -> Dict[int, float]:
        return {period: self._last(calculate_ema(closes, period), closes[-1] if closes else 0) for period in [8, 21, 50, 100, 200]}

    def _value_or_indicator(self, indicators: Dict, key: str, fallback: float) -> float:
        value = indicators.get(key)
        return self._f(value, fallback) if value not in (None, '') else self._f(fallback)

    def _last(self, values: List[Any], default: float = 0.0) -> float:
        for value in reversed(values):
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return default

    def _slope(self, values: List[float], lookback: int = 5) -> float:
        if len(values) < 2:
            return 0.0
        lookback = min(lookback, len(values) - 1)
        return float(values[-1]) - float(values[-1 - lookback])

    def _percentile_rank(self, values: List[float], current: float) -> float:
        valid = [float(v) for v in values if v is not None]
        if not valid:
            return 50.0
        below = len([v for v in valid if v <= current])
        return below / len(valid) * 100

    def _nearest_below(self, price: float, levels: List[float]) -> float:
        below = sorted([self._f(x) for x in levels if self._f(x) < price], reverse=True)
        return below[0] if below else 0.0

    def _nearest_above(self, price: float, levels: List[float]) -> float:
        above = sorted([self._f(x) for x in levels if self._f(x) > price])
        return above[0] if above else 0.0

    def _ema_ribbon_state(self, price: float, emas: List[float]) -> Dict[str, Any]:
        valid = [e for e in emas if e > 0]
        bullish = len(valid) == len(emas) and price > valid[0] and all(valid[i] > valid[i + 1] for i in range(len(valid) - 1))
        bearish = len(valid) == len(emas) and price < valid[0] and all(valid[i] < valid[i + 1] for i in range(len(valid) - 1))
        spread = (max(valid) - min(valid)) / max(abs(price), 0.01) if valid else 0
        return {'state': 'BULLISH_ALIGNMENT' if bullish else 'BEARISH_ALIGNMENT' if bearish else 'MIXED', 'spread': round(spread, 5), 'expansion': 'EXPANDING' if spread > 0.003 else 'CONTRACTING'}

    def _calculate_adx_proxy(self, candles: List[Dict[str, Any]], period: int = 14) -> Tuple[float, str]:
        if len(candles) < period + 2:
            return 0.0, 'INSUFFICIENT_DATA'
        plus_dm = []
        minus_dm = []
        tr_values = []
        for i in range(1, len(candles)):
            high = self._f(candles[i].get('high')); low = self._f(candles[i].get('low')); close_prev = self._f(candles[i-1].get('close'))
            high_prev = self._f(candles[i-1].get('high')); low_prev = self._f(candles[i-1].get('low'))
            up_move = high - high_prev
            down_move = low_prev - low
            plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
            minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
            tr_values.append(max(high - low, abs(high - close_prev), abs(low - close_prev)))
        tr = sum(tr_values[-period:]) or 0.0001
        pdi = 100 * sum(plus_dm[-period:]) / tr
        mdi = 100 * sum(minus_dm[-period:]) / tr
        dx = abs(pdi - mdi) / max(pdi + mdi, 0.0001) * 100
        return dx, 'BULLISH' if pdi > mdi else 'BEARISH' if mdi > pdi else 'NEUTRAL'

    def _rsi_divergence(self, candles: List[Dict[str, Any]], rsi_series: List[Any]) -> str:
        if len(candles) < 40 or len(rsi_series) < len(candles):
            return 'NONE'
        swings = detect_swing_points(candles[-80:], lookback=3)
        offset = len(candles) - len(candles[-80:])
        lows = swings.get('lows', [])[-2:]
        highs = swings.get('highs', [])[-2:]
        try:
            if len(lows) >= 2:
                a, b = lows[-2], lows[-1]
                ia, ib = offset + int(a['index']), offset + int(b['index'])
                pa, pb = float(a['price']), float(b['price'])
                ra, rb = float(rsi_series[ia] or 50), float(rsi_series[ib] or 50)
                if pb < pa and rb > ra:
                    return 'REGULAR_BULLISH'
                if pb > pa and rb < ra:
                    return 'HIDDEN_BULLISH'
            if len(highs) >= 2:
                a, b = highs[-2], highs[-1]
                ia, ib = offset + int(a['index']), offset + int(b['index'])
                pa, pb = float(a['price']), float(b['price'])
                ra, rb = float(rsi_series[ia] or 50), float(rsi_series[ib] or 50)
                if pb > pa and rb < ra:
                    return 'REGULAR_BEARISH'
                if pb < pa and rb > ra:
                    return 'HIDDEN_BEARISH'
        except Exception:
            return 'NONE'
        return 'NONE'

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
        
        score = abs(float(technical.get('classic_score', 0) or 0))
        signal = str(technical.get('classic_signal', 'WAIT')).upper()

        # لا نسمح لـ WAIT أن يظهر بثقة عالية؛ الثقة العالية يجب أن تكون فقط لإشارة اتجاهية.
        if signal == 'WAIT':
            return min(55, round(30 + score * 8, 1))

        # إضافة مؤثرات أخرى
        rsi = technical.get('rsi', 50)
        if 35 <= rsi <= 65:
            score += 0.3  # RSI في منطقة غير متطرفة
        if technical.get('market_regime', {}).get('trend_strength') == 'STRONG':
            score += 0.4

        # تحويل للنسبة (0-100)
        confidence = min(score * 16 + 45, 92)

        return round(confidence, 1)
    

    def _f(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def get_analysis_summary(self, result: Dict) -> str:
        """تلخيص التحليل لرسالة تيليجرام"""
        
        technical = result.get('technical', {})
        ai = result.get('ai', {})
        
        lines = [
            "📊 *التحليل الفني*",
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