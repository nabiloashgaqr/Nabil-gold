"""Technical Agent - classic indicator-based analysis."""

import logging
from typing import Dict, Any, List, Tuple
from .base_agent import BaseAgent
from utils.helpers import get_agent_weights
from utils.indicators import calculate_ema, calculate_rsi, calculate_macd, calculate_atr, calculate_bollinger_bands, detect_support_resistance, detect_swing_points
from services.market_snapshot import build_market_snapshot

logger = logging.getLogger(__name__)

class TechnicalAgent(BaseAgent):
    """Classic technical-analysis agent using indicators and key levels."""
    
    def __init__(self, config: Dict, **_kwargs):
        super().__init__(config)
        self.weight = get_agent_weights(config).get('technical', 0.2)
    
    def analyze(self, data: Dict) -> Dict[str, Any]:
        """
        🔍 تحليل الشارت باستخدام المؤشرات
        
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
                'final_decision': '...'
            }
        """
        return self._sync_analyze(data)
    
    def _sync_analyze(self, data: Dict) -> Dict[str, Any]:
        """التحليل الفني المتزامن"""
        
        candles = data.get('data', data.get('candles', []))
        indicators = data.get('indicators', {})
        snapshot = build_market_snapshot(data, self.config)
        
        # التحليل الفني الكلاسيكي فقط
        technical_analysis = self._technical_analysis(candles, indicators)
        confidence_breakdown = self._confidence_breakdown(technical_analysis)
        confidence = self._calculate_classic_confidence(technical_analysis)
        signal = technical_analysis.get('classic_signal', 'WAIT')
        return {
            'agent': 'technical',
            'signal': signal,
            'confidence': confidence,
            'weight': self.weight,
            'technical': technical_analysis,
            'summary': self._structured_summary(technical_analysis, signal),
            'reasoning': technical_analysis.get('trend', 'N/A'),
            'reasons': technical_analysis.get('reasons', [])[:6],
            'evidence': technical_analysis.get('evidence', []),
            'invalidations': technical_analysis.get('invalidations', []),
            'key_levels': technical_analysis.get('key_levels', {}),
            'market_regime': technical_analysis.get('market_regime', {}),
            'data_quality': snapshot.get('data_quality', {}),
            'verified_snapshot': snapshot,
            'reason_codes': technical_analysis.get('reason_codes', []),
            'confidence_breakdown': confidence_breakdown,
            'warnings': technical_analysis.get('warnings', []),
            'timestamp': self.now_iso()
        }
    
    async def analyze_async(self, data: Dict) -> Dict[str, Any]:
        """Async compatibility wrapper; uses the same classic analysis."""
        return self._sync_analyze(data)

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

        market_phase = 'SQUEEZE' if bollinger_squeeze else 'TRENDING' if adx_value >= 25 else 'RANGING'
        base_score = score
        regime_scoring = self._regime_aware_score(
            score=score,
            market_phase=market_phase,
            ema_ribbon=ema_ribbon,
            macd_hist=macd_hist,
            macd_slope=macd_slope,
            rsi=rsi,
            bollinger_percent_b=bollinger_percent_b,
            adx_value=adx_value,
        )
        score = float(regime_scoring.get('adjusted_score', score))
        if regime_scoring.get('notes'):
            reasons.extend(regime_scoring.get('notes', [])[:3])

        if score >= 2.5:
            classic_signal = 'BUY'
        elif score <= -2.5:
            classic_signal = 'SELL'
        else:
            classic_signal = 'WAIT'

        ema_trend = 'UP' if ema_50 > ema_200 else 'DOWN' if ema_50 < ema_200 else 'SIDEWAYS'
        rsi_signal = 'OVERBOUGHT' if rsi > 70 else 'OVERSOLD' if rsi < 30 else 'NEUTRAL'
        macd_signal = 'BULLISH' if macd_hist > 0 else 'BEARISH' if macd_hist < 0 else 'NEUTRAL'
        reason_codes = self._technical_reason_codes(ema_ribbon, ema_50, ema_200, rsi, macd_hist, macd_slope, bollinger_squeeze, volatility_regime, classic_signal)
        evidence = [
            {'name': 'EMA ribbon', 'value': ema_ribbon.get('state'), 'bias': 'BULLISH' if ema_ribbon.get('state') == 'BULLISH_ALIGNMENT' else 'BEARISH' if ema_ribbon.get('state') == 'BEARISH_ALIGNMENT' else 'NEUTRAL'},
            {'name': 'RSI', 'value': round(rsi, 2), 'bias': 'BULLISH' if rsi >= 55 else 'BEARISH' if rsi <= 45 else 'NEUTRAL'},
            {'name': 'MACD histogram', 'value': round(macd_hist, 4), 'bias': macd_signal},
            {'name': 'ADX proxy', 'value': round(adx_value, 2), 'bias': 'TRENDING' if adx_value >= 25 else 'RANGING' if adx_value < 15 else 'MODERATE'},
            {'name': 'ATR regime', 'value': volatility_regime, 'bias': volatility_regime},
        ]
        invalidations = []
        if classic_signal == 'BUY':
            if support: invalidations.append(f'Close below nearest support {support:.2f}')
            if ema_50: invalidations.append(f'Return below EMA50 {ema_50:.2f}')
        elif classic_signal == 'SELL':
            if resistance: invalidations.append(f'Close above nearest resistance {resistance:.2f}')
            if ema_50: invalidations.append(f'Return above EMA50 {ema_50:.2f}')

        return {
            'trend': ema_trend,
            'market_regime': {
                'trend_direction': ema_trend,
                'trend_strength': 'STRONG' if adx_value >= 25 else 'WEAK' if adx_value < 15 else 'MODERATE',
                'volatility_regime': volatility_regime,
                'market_phase': market_phase,
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
            'base_score': round(base_score, 2),
            'regime_scoring': regime_scoring,
            'current_price': current_price,
            'reasons': reasons[:10],
            'reason_codes': reason_codes,
            'evidence': evidence,
            'invalidations': invalidations,
            'warnings': ['Bollinger squeeze needs breakout confirmation'] if bollinger_squeeze else [],
            'indicators_raw': {'atr': atr, 'rsi': rsi, 'macd_histogram': macd_hist},
            'key_levels': {'nearest_support': round(support, 2) if support else 0, 'nearest_resistance': round(resistance, 2) if resistance else 0},
        }

    def _regime_aware_score(self, score: float, market_phase: str, ema_ribbon: Dict[str, Any], macd_hist: float, macd_slope: float, rsi: float, bollinger_percent_b: float, adx_value: float) -> Dict[str, Any]:
        """Adjust technical score by market regime without changing orchestration.

        Trending markets reward trend/momentum evidence. Ranges reward mean-
        reversion near Bollinger extremes. Squeeze regimes reduce directional
        conviction until breakout confirmation appears.
        """
        adjusted = float(score)
        notes: List[str] = []
        weights: Dict[str, float] = {}
        if market_phase == 'TRENDING':
            trend_bonus = 0.0
            if ema_ribbon.get('state') in {'BULLISH_ALIGNMENT', 'BEARISH_ALIGNMENT'}:
                trend_bonus += 0.35 if adjusted >= 0 else -0.35
            if (macd_hist > 0 and macd_slope > 0 and adjusted > 0) or (macd_hist < 0 and macd_slope < 0 and adjusted < 0):
                trend_bonus += 0.25 if adjusted >= 0 else -0.25
            adjusted += trend_bonus
            weights = {'trend_following': 1.25, 'momentum': 1.15, 'mean_reversion': 0.75}
            if trend_bonus:
                notes.append('regime-aware: trend evidence boosted')
        elif market_phase == 'RANGING':
            # In ranges, overextended trend-following scores should be damped;
            # Bollinger extremes in the same direction get a small mean-reversion boost.
            adjusted *= 0.88
            if bollinger_percent_b < 0.15 and adjusted > 0:
                adjusted += 0.35; notes.append('regime-aware: range lower-band bounce support')
            elif bollinger_percent_b > 0.85 and adjusted < 0:
                adjusted -= 0.35; notes.append('regime-aware: range upper-band rejection support')
            else:
                notes.append('regime-aware: range dampens trend conviction')
            weights = {'trend_following': 0.75, 'momentum': 0.85, 'mean_reversion': 1.25}
        elif market_phase == 'SQUEEZE':
            adjusted *= 0.72
            if abs(macd_slope) > 0 and adx_value >= 18:
                adjusted += 0.15 if adjusted > 0 else -0.15 if adjusted < 0 else 0
            notes.append('regime-aware: squeeze requires breakout confirmation')
            weights = {'trend_following': 0.85, 'momentum': 1.05, 'breakout_readiness': 1.30}
        return {'market_phase': market_phase, 'base_score': round(float(score), 2), 'adjusted_score': round(adjusted, 2), 'adjustment': round(adjusted - float(score), 2), 'weights': weights, 'notes': notes}

    def _technical_reason_codes(self, ema_ribbon: Dict[str, Any], ema_50: float, ema_200: float, rsi: float, macd_hist: float, macd_slope: float, squeeze: bool, volatility: str, signal: str) -> List[str]:
        codes: List[str] = []
        state = ema_ribbon.get('state')
        if state == 'BULLISH_ALIGNMENT' or ema_50 > ema_200:
            codes.append('EMA_BULL_ALIGN')
        if state == 'BEARISH_ALIGNMENT' or ema_50 < ema_200:
            codes.append('EMA_BEAR_ALIGN')
        if rsi >= 55:
            codes.append('RSI_BULL_RANGE')
        elif rsi <= 45:
            codes.append('RSI_BEAR_RANGE')
        if macd_hist > 0 and macd_slope >= 0:
            codes.append('MACD_POS_SLOPE')
        elif macd_hist < 0 and macd_slope <= 0:
            codes.append('MACD_NEG_SLOPE')
        if squeeze:
            codes.append('BB_SQUEEZE')
        if volatility == 'HIGH':
            codes.append('ATR_HIGH_VOL')
        elif volatility == 'LOW':
            codes.append('ATR_LOW_VOL')
        if signal == 'WAIT':
            codes.append('TECH_WAIT_NO_EDGE')
        return codes[:10]

    def _confidence_breakdown(self, technical: Dict[str, Any]) -> Dict[str, float]:
        score = float(technical.get('classic_score', 0) or 0)
        regime = technical.get('market_regime', {}) or {}
        trend = min(25.0, abs(score) * 5.0)
        momentum = 10.0 if technical.get('macd') in {'BULLISH', 'BEARISH'} else 0.0
        structure = 8.0 if technical.get('support') or technical.get('resistance') else 0.0
        volatility_fit = 8.0 if regime.get('volatility_regime') == 'NORMAL' else 4.0 if regime.get('volatility_regime') == 'HIGH' else 3.0
        penalties = 0.0
        if regime.get('market_phase') == 'SQUEEZE':
            penalties -= 8.0
        if technical.get('classic_signal') == 'WAIT':
            penalties -= 5.0
        return {'trend': round(trend, 1), 'momentum': round(momentum, 1), 'structure': round(structure, 1), 'volatility_fit': round(volatility_fit, 1), 'penalties': round(penalties, 1)}

    def _structured_summary(self, technical: Dict[str, Any], signal: str) -> str:
        regime = technical.get('market_regime', {}) or {}
        return (
            f"Structure: {technical.get('trend', 'N/A')}; "
            f"Momentum: RSI {technical.get('rsi', 'N/A')}, MACD {technical.get('macd', 'N/A')}; "
            f"Regime: {regime.get('market_phase', 'UNKNOWN')} / {regime.get('volatility_regime', 'UNKNOWN')}; "
            f"Decision: {signal}"
        )

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
                # FIX: bounds-check rsi_series access to prevent IndexError
                # when swing index extends beyond the indicator series.
                if ia >= len(rsi_series) or ib >= len(rsi_series):
                    return 'NONE'
                pa, pb = float(a['price']), float(b['price'])
                ra, rb = float(rsi_series[ia] or 50), float(rsi_series[ib] or 50)
                if pb < pa and rb > ra:
                    return 'REGULAR_BULLISH'
                if pb > pa and rb < ra:
                    return 'HIDDEN_BULLISH'
            if len(highs) >= 2:
                a, b = highs[-2], highs[-1]
                ia, ib = offset + int(a['index']), offset + int(b['index'])
                if ia >= len(rsi_series) or ib >= len(rsi_series):
                    return 'NONE'
                pa, pb = float(a['price']), float(b['price'])
                ra, rb = float(rsi_series[ia] or 50), float(rsi_series[ib] or 50)
                if pb > pa and rb < ra:
                    return 'REGULAR_BEARISH'
                if pb < pa and rb > ra:
                    return 'HIDDEN_BEARISH'
        except Exception:
            return 'NONE'
        return 'NONE'

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
        lines = [
            "📊 *Technical Analysis*",
            f"├ Trend: {technical.get('trend', 'N/A')}",
            f"├ RSI: {technical.get('rsi', 'N/A')}",
            f"├ MACD: {technical.get('macd', 'N/A')}",
            f"├ Support: {technical.get('support', 'N/A')}",
            f"└ Resistance: {technical.get('resistance', 'N/A')}"
        ]
        
        return "\n".join(lines)