"""
🧠 Learning Service - Gold AI Signals v2.0
خدمة التعلم الذكي المحسّنة

✅ التغييرات:
- تعلّم أسرع (aggressive learning)
- وزن أكبر للأداء الأخير
- تعديلات أكثر ذكاءً
- حفظ الإشارات الفاشلة للتعلم منها
"""

import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

from utils.sessions import session_label_from_trade

logger = logging.getLogger(__name__)

@dataclass
class AgentPerformanceRecord:
    """سجل أداء الوكيل"""
    agent_name: str
    total_predictions: int = 0
    correct_predictions: int = 0
    win_rate: float = 0.0
    avg_confidence: float = 0.0
    total_pnl: float = 0.0
    win_pnl: float = 0.0
    loss_pnl: float = 0.0
    current_weight: float = 0.0
    adjusted_weight: float = 0.0
    learning_score: float = 0.0
    trend: str = "STABLE"  # IMPROVING, STABLE, DECLINING
    last_updated: str = ""
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    accuracy_trend: List[float] = field(default_factory=list)  # آخر 10 نسبة
    regime_breakdown: Dict[str, Dict[str, Any]] = field(default_factory=dict)

@dataclass
class LearningConfig:
    """إعدادات التعلم المحسّنة"""
    enabled: bool = True
    auto_update_weights: bool = False  # False = توصيات فقط بدون تحديث تلقائي
    update_frequency_days: int = 1
    min_predictions_for_adjustment: int = 3  # تقليل من 5 إلى 3
    max_weight_change: float = 0.25  # زيادة من 0.15
    momentum_weight: float = 0.4  # زيادة من 0.3
    decay_factor: float = 0.90  # أسرع في نسيان القديم
    performance_threshold: float = 0.6  # 60% win rate
    aggressive_mode: bool = True  # وضع عدواني
    streak_bonus: float = 0.10  # مكافأة التتابع
    recent_trades_weight: float = 0.6  # 60% للصفقات الأخيرة

@dataclass
class LearningReport:
    """تقرير التعلم"""
    report_date: str
    agents_performance: Dict[str, AgentPerformanceRecord]
    adjusted_weights: Dict[str, float]
    total_trades_analyzed: int
    overall_win_rate: float
    recommendations: List[str]
    previous_weights: Dict[str, float]
    changes_summary: str
    top_performers: List[str] = field(default_factory=list)
    bottom_performers: List[str] = field(default_factory=list)
    session_breakdown: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    day_of_week_breakdown: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    rr_efficiency: Dict[str, Any] = field(default_factory=dict)
    news_proximity: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    regime_fit: Dict[str, Dict[str, Any]] = field(default_factory=dict)

class LearningService:
    """
    🧠 خدمة التعلم الذكي - الإصدار 2.0
    
    ✅ التحسينات:
    - تعلّم أسرع (أقل صفقات للتعديل)
    - وزن أكبر للأداء الأخير
    - مكافأة التتابع (winning/losing streaks)
    - تحليل الاتجاه (accuracy_trend)
    - تعديلات أكثر ذكاءً
    """
    
    def __init__(self, database_service, config: Dict):
        self.db = database_service
        self.config = config
        self.learning_config = self._load_learning_config()
        
        # الأوزان الافتراضية — مصدر وحيد من get_agent_weights
        from utils.helpers import get_agent_weights
        self.default_weights = get_agent_weights(config)
        
        # الأوزان الحالية
        self.current_weights = self.default_weights.copy()
        
        # سجل التعلم
        self.learning_history: List[LearningReport] = []
        
        # 🚀 ذاكرة الصفقات الفاشلة للتعلم منها
        self.failed_signals_memory: List[Dict] = []
    
    def _load_learning_config(self) -> LearningConfig:
        """تحميل إعدادات التعلم المحسّنة"""
        learning = self.config.get('learning', {})
        return LearningConfig(
            enabled=learning.get('enabled', True),
            auto_update_weights=learning.get('auto_update_weights', False),
            update_frequency_days=learning.get('update_frequency_days', 1),
            min_predictions_for_adjustment=learning.get('min_predictions_for_adjustment', 3),  # ↓ من 5
            max_weight_change=learning.get('max_weight_change', 0.25),  # ↑ من 0.15
            momentum_weight=learning.get('momentum_weight', 0.4),  # ↑ من 0.3
            decay_factor=learning.get('decay_factor', 0.90),  # ↓ من 0.95
            performance_threshold=learning.get('performance_threshold', 0.6),
            aggressive_mode=learning.get('aggressive_mode', True),
            streak_bonus=learning.get('streak_bonus', 0.10),
            recent_trades_weight=learning.get('recent_trades_weight', 0.6)
        )
    
    async def analyze_and_update_weights(self) -> LearningReport:
        """🧠 تحليل أداء الوكلاء وتقديم توصيات الأوزان
        
        auto_update_weights=False (الافتراضي): يحلل ويولّد توصيات بدون
        تحديث الأوزان في قاعدة البيانات. التوصيات تظهر في التقرير
        والإدارة تحدّث الأوزان يدوياً.
        
        auto_update_weights=True: يحلل ويحدّث الأوزان تلقائياً.
        """
        
        if not self.learning_config.enabled:
            logger.info("التعلم الذكي معطل")
            return self._empty_report()
        
        try:
            logger.info("🔄 بدء تحليل أداء الوكلاء (v2.0)...")
            
            # 1️⃣ جلب الصفقات المغلقة
            closed_trades = await self._get_closed_trades(days=5)  # آخر 5 أيام بدل 7
            
            min_required = self.learning_config.min_predictions_for_adjustment
            if len(closed_trades) < min_required:
                logger.info(f"صفقات قليلة جداً: {len(closed_trades)}/{min_required}")
                return self._empty_report()
            
            # 2️⃣ تحليل أداء كل وكيل
            agent_stats = await self._analyze_agent_performance(closed_trades)
            
            # 3️⃣ حساب الأوزان المقترحة (محسّن)
            adjusted_weights = self._calculate_adjusted_weights_v2(agent_stats)
            
            # 4️⃣ حفظ الأوزان — فقط إذا auto_update_weights=True
            if self.learning_config.auto_update_weights:
                await self._save_adjusted_weights(adjusted_weights)
                self.current_weights = adjusted_weights
                logger.info("✅ تم تحديث الأوزان تلقائياً في قاعدة البيانات")
            else:
                logger.info("📋 وضع التوصيات فقط: الأوزان المقترحة في التقرير بدون تحديث تلقائي")
                logger.info("📋 الأوزان المقترحة: %s", {k: f"{v:.3f}" for k, v in adjusted_weights.items()})
            
            # 5️⃣ توليد التقرير (دائماً)
            report = self._generate_report_v2(
                agent_stats, adjusted_weights, closed_trades
            )
            
            self.learning_history.append(report)
            
            self._log_weight_changes(adjusted_weights)
            
            return report
            
        except Exception as e:
            logger.error(f"❌ خطأ في التعلم: {e}")
            return self._empty_report()
    
    async def _get_closed_trades(self, days: int = 5) -> List[Dict]:
        """جلب الصفقات المغلقة"""
        try:
            query = f"""
                SELECT 
                    id, signal_id, type, trade_type, symbol,
                    entry_price, stop_loss, initial_stop_loss, close_price,
                    final_pnl, final_pnl_points, current_pnl, current_pnl_points,
                    planned_risk_points, planned_tp2_points, planned_rr,
                    session_label, session_quality, entry_day_of_week, entry_hour_local,
                    news_status_at_entry, news_risk_at_entry,
                    volatility_regime, trend_strength, daily_bias_at_entry,
                    primary_entry_driver, entry_failure_mode, macro_bias_at_entry,
                    status, entry_time, opened_at, closed_at, created_at, signal_snapshot
                FROM trades
                WHERE closed_at >= NOW() - INTERVAL '{days} days'
                    AND status IN ('CLOSED', 'TP1_HIT', 'TP2_HIT', 'SL_HIT', 'BE_HIT', 'EXPIRED', 'MANUAL_CLOSE')
                ORDER BY closed_at DESC
            """
            
            result = await self.db.execute_query(query)
            return result if result else []
            
        except Exception as e:
            logger.error(f"❌ خطأ في جلب الصفقات: {e}")
            return []
    
    def _trade_pnl(self, trade: Dict[str, Any]) -> float:
        """Read trade PnL from modern and legacy fields."""
        for key in ('final_pnl', 'current_pnl_points', 'current_pnl', 'pnl', 'pnl_points'):
            value = trade.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    async def _analyze_agent_performance(
        self, 
        closed_trades: List[Dict]
    ) -> Dict[str, AgentPerformanceRecord]:
        """تحليل أداء كل وكيل مع تتبع التتابع"""
        
        agent_records = {}
        
        # تحليل الأداء من الصفقات المغلقة
        for trade in closed_trades:
            pnl = self._trade_pnl(trade)
            is_win = pnl > 0
            
            agent_configs = self._trade_agent_configs(trade)
            regime_label = self._trade_regime_label(trade)
            
            for config in agent_configs:
                name = config['name']
                if name not in agent_records:
                    agent_records[name] = AgentPerformanceRecord(
                        agent_name=name,
                        current_weight=self.current_weights.get(name, 0.15)
                    )
                
                record = agent_records[name]
                record.total_predictions += 1
                
                # حساب win rate
                agent_wr = config['base_rate'] + (pnl / 100 if is_win else -0.05)
                agent_wr = max(0.3, min(0.9, agent_wr))  # حدود
                
                if is_win:
                    record.correct_predictions += 1
                    record.consecutive_wins += 1
                    record.consecutive_losses = 0
                else:
                    record.consecutive_losses += 1
                    record.consecutive_wins = 0
                    # 🚀 حفظ الصفقات الفاشلة للتعلم
                    self._add_to_failed_memory(trade, name)
                
                record.win_rate = (record.correct_predictions / record.total_predictions) * 100
                record.avg_confidence = 70 + (record.win_rate / 5)
                record.total_pnl += pnl * self.current_weights.get(name, 0.15)
                bucket = record.regime_breakdown.setdefault(regime_label, {"count": 0, "pnl": 0.0, "wins": 0})
                bucket["count"] += 1
                bucket["pnl"] += pnl
                if is_win:
                    bucket["wins"] += 1
                
                # تحديث الاتجاه
                record.trend = self._calculate_trend(record)
                
                # تتبع الاتجاه الأخير (آخر 10 صفقة)
                record.accuracy_trend.append(1.0 if is_win else 0.0)
                if len(record.accuracy_trend) > 10:
                    record.accuracy_trend.pop(0)
        
        return agent_records
    
    def _trade_agent_configs(self, trade: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Use stored attribution first, then fall back to all legacy agents."""
        snap = self._snapshot(trade)
        attr = snap.get("entry_attribution") or {}
        base_rates = {
            "technical": 0.55,
            "classical": 0.52,
            "smc": 0.58,
            "price_action": 0.50,
            "multitimeframe": 0.54,
        }
        agents = list(attr.get("supporting_agents") or [])
        agents += list(attr.get("opposing_agents") or [])
        primary = trade.get("primary_entry_driver") or attr.get("primary_entry_driver")
        if primary:
            agents.insert(0, str(primary))
        cleaned: List[str] = []
        for name in agents:
            name = str(name)
            if name in base_rates and name not in cleaned:
                cleaned.append(name)
        if not cleaned:
            structured = snap.get("agent_structured") or {}
            cleaned = [name for name in base_rates if name in structured] or list(base_rates)
        return [{"name": name, "base_rate": base_rates.get(name, 0.52)} for name in cleaned]

    def _add_to_failed_memory(self, trade: Dict, agent_name: str):
        """🚀 حفظ الصفقة الفاشلة للتعلم منها"""
        self.failed_signals_memory.append({
            'agent': agent_name,
            'trade': trade,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'reason': self._analyze_failure_reason(trade)
        })
        
        # حذف الأقدم من 30 يوم
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        self.failed_signals_memory = [
            f for f in self.failed_signals_memory
            if datetime.fromisoformat(f['timestamp']) > cutoff
        ]
    
    def _analyze_failure_reason(self, trade: Dict) -> str:
        """تحليل سبب الفشل"""
        pnl = self._trade_pnl(trade)
        if pnl < -20:
            return "SL_hit_large_loss"
        elif pnl < -5:
            return "SL_hit_small_loss"
        else:
            return "mixed_signals"

    def _snapshot(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        snap = trade.get("signal_snapshot") or {}
        return snap if isinstance(snap, dict) else {}

    def _planned_risk_points(self, trade: Dict[str, Any]) -> float:
        try:
            value = trade.get("planned_risk_points")
            if value is not None:
                return abs(float(value))
        except (TypeError, ValueError):
            pass
        try:
            entry = float(trade.get("entry_price") or 0)
            sl = float(trade.get("initial_stop_loss") or trade.get("stop_loss") or 0)
            if entry and sl:
                return abs(entry - sl) * 10.0
        except (TypeError, ValueError):
            pass
        return 0.0

    def _planned_rr(self, trade: Dict[str, Any]) -> float:
        try:
            value = trade.get("planned_rr")
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
        sig = self._snapshot(trade).get("signal") or {}
        for key in ("rr_ratio", "tp2_rr"):
            try:
                value = sig.get(key)
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                pass
        return 0.0

    def _bucket_metric(self, trades: List[Dict[str, Any]], key_func) -> Dict[str, Dict[str, Any]]:
        buckets: Dict[str, Dict[str, Any]] = {}
        for trade in trades:
            key = str(key_func(trade) or "unknown")
            pnl = self._trade_pnl(trade)
            bucket = buckets.setdefault(key, {"count": 0, "pnl": 0.0, "wins": 0, "losses": 0})
            bucket["count"] += 1
            bucket["pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
            elif pnl < 0:
                bucket["losses"] += 1
        return {
            k: {
                **v,
                "pnl": round(v["pnl"], 1),
                "win_rate_pct": round(v["wins"] / v["count"] * 100, 1) if v["count"] else 0.0,
            }
            for k, v in buckets.items()
        }

    def _trade_session_label(self, trade: Dict[str, Any]) -> str:
        """Return a standardised session label for a trade.

        Uses the unified session classifier from utils.sessions so that
        all learning data shows consistent names.
        """
        return session_label_from_trade(trade)

    def _trade_regime_label(self, trade: Dict[str, Any]) -> str:
        snap = self._snapshot(trade)
        mc = snap.get("market_context") or {}
        tech = mc.get("technical_regime") or {}
        return str(trade.get("volatility_regime") or tech.get("volatility_regime") or "unknown").upper()

    def _trade_news_label(self, trade: Dict[str, Any]) -> str:
        snap = self._snapshot(trade)
        nc = snap.get("news_context") or {}
        rule = nc.get("rule_based") or {}
        return str(trade.get("news_status_at_entry") or rule.get("market_status") or rule.get("status") or "unknown").upper()

    def _trade_macro_label(self, trade: Dict[str, Any]) -> str:
        snap = self._snapshot(trade)
        attr = snap.get("entry_attribution") or {}
        macro = attr.get("macro_direction") or (snap.get("market_context") or {}).get("macro_direction") or {}
        return str(trade.get("macro_bias_at_entry") or macro.get("bias") or "unknown").upper()

    def _trade_primary_driver(self, trade: Dict[str, Any]) -> str:
        snap = self._snapshot(trade)
        attr = snap.get("entry_attribution") or {}
        return str(trade.get("primary_entry_driver") or attr.get("primary_entry_driver") or "unknown")

    def _rr_efficiency(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        actual_r: List[float] = []
        planned: List[float] = []
        for trade in trades:
            risk = self._planned_risk_points(trade)
            if risk <= 0:
                continue
            actual_r.append(self._trade_pnl(trade) / risk)
            rr = self._planned_rr(trade)
            if rr > 0:
                planned.append(rr)
        wins = [x for x in actual_r if x > 0]
        if not wins:
            return {"sample": 0}
        return {
            "sample": len(wins),
            "avg_actual_r": round(sum(wins) / len(wins), 2),
            "avg_winner_r": round(sum(wins) / len(wins), 2),
            "avg_planned_rr": round(sum(planned) / len(planned), 2) if planned else 0.0,
            "rr_capture_pct": round((sum(wins) / len(wins)) / (sum(planned) / len(planned)) * 100, 1) if planned and sum(planned) else 0.0,
        }

    def _enrichment_breakdowns(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "session_breakdown": self._bucket_metric(trades, self._trade_session_label),
            "day_of_week_breakdown": self._bucket_metric(trades, lambda t: t.get("entry_day_of_week") or str(t.get("entry_time") or t.get("created_at") or "unknown")[:10]),
            "rr_efficiency": self._rr_efficiency(trades),
            "news_proximity": self._bucket_metric(trades, self._trade_news_label),
            "regime_fit": self._bucket_metric(trades, self._trade_regime_label),
            "macro_bias": self._bucket_metric(trades, self._trade_macro_label),
            "entry_driver": self._bucket_metric(trades, self._trade_primary_driver),
        }
    
    def _calculate_trend(self, record: AgentPerformanceRecord) -> str:
        """حساب اتجاه الوكيل"""
        if len(record.accuracy_trend) < 3:
            return "STABLE"
        
        recent = record.accuracy_trend[-3:]
        avg = sum(recent) / len(recent)
        
        if avg >= 0.7:
            return "IMPROVING"
        elif avg <= 0.4:
            return "DECLINING"
        else:
            return "STABLE"
    
    def _calculate_adjusted_weights_v2(
        self, 
        agent_stats: Dict[str, AgentPerformanceRecord]
    ) -> Dict[str, float]:
        """🚀 حساب الأوزان الجديدة - الإصدار 2.0"""
        
        scores = {}
        for name, record in agent_stats.items():
            # Score الأساسي
            win_rate_score = record.win_rate / 100
            confidence_factor = record.avg_confidence / 100
            
            # 🚀 1. وزن التتابع (streak bonus)
            streak_bonus = 0
            if record.consecutive_wins >= 3:
                streak_bonus = self.learning_config.streak_bonus * (record.consecutive_wins / 3)
            elif record.consecutive_losses >= 3:
                streak_bonus = -self.learning_config.streak_bonus * (record.consecutive_losses / 3)
            
            # 🚀 2. وزن الصفقات الأخيرة (momentum)
            recent_perf = sum(record.accuracy_trend[-3:]) / min(3, len(record.accuracy_trend))
            momentum = 1.0 + (recent_perf - 0.5) * self.learning_config.momentum_weight
            
            # 🚀 3. الاتجاه العام
            trend_multiplier = {
                "IMPROVING": 1.15,
                "STABLE": 1.0,
                "DECLINING": 0.85
            }.get(record.trend, 1.0)
            
            # حساب الـ score النهائي
            learning_score = win_rate_score * confidence_factor * momentum * trend_multiplier
            learning_score += streak_bonus
            
            scores[name] = max(0.05, learning_score)  # حد أدنى 5%
            record.learning_score = learning_score
        
        # Normalize to sum to 1.0
        total_score = sum(scores.values())
        if total_score == 0:
            return self.default_weights.copy()
        
        raw_weights = {name: score / total_score for name, score in scores.items()}
        
        # تطبيق حدود max_change
        adjusted = {}
        for name, raw in raw_weights.items():
            current = self.current_weights.get(name, 0.15)
            change = raw - current
            
            max_change = self.learning_config.max_weight_change
            
            # تطبيق التغيير
            if change > max_change:
                change = max_change
            elif change < -max_change:
                change = -max_change
            
            new_weight = current + change
            new_weight = max(0.05, min(0.45, new_weight))  # 5% to 45%
            
            adjusted[name] = new_weight
            agent_stats[name].adjusted_weight = new_weight
        
        # Final normalization
        total = sum(adjusted.values())
        if abs(total - 1.0) > 0.01:
            for name in adjusted:
                adjusted[name] = adjusted[name] / total
        
        return adjusted
    
    async def _save_adjusted_weights(self, weights: Dict[str, float]):
        """حفظ الأوزان في قاعدة البيانات"""
        try:
            for agent_name, weight in weights.items():
                query = """
                    INSERT INTO agent_weights (agent_name, weight, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (agent_name) 
                    DO UPDATE SET weight = $2, updated_at = NOW()
                """
                await self.db.execute_query(query, [agent_name, weight])
            
            logger.info("✅ تم حفظ الأوزان في قاعدة البيانات")
            
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ الأوزان: {e}")
    
    async def load_current_weights(self) -> Dict[str, float]:
        """تحميل الأوزان الحالية — config.json هو المصدر الوحيد.

        DB agent_weights يُستخدم فقط للعرض في Dashboard.
        الأوزان الفعّالة تأتي من config.json فقط.
        """
        from utils.helpers import get_agent_weights
        weights = get_agent_weights(self.config)
        self.current_weights = weights
        return weights.copy()
    
    def _generate_report_v2(
        self,
        agent_stats: Dict[str, AgentPerformanceRecord],
        adjusted_weights: Dict[str, float],
        closed_trades: List[Dict]
    ) -> LearningReport:
        """🚀 توليد تقرير التعلم - الإصدار 2.0"""
        
        total_trades = len(closed_trades)
        winning = sum(1 for t in closed_trades if self._trade_pnl(t) > 0)
        overall_wr = (winning / total_trades * 100) if total_trades > 0 else 0
        
        # ترتيب الوكلاء
        sorted_agents = sorted(
            agent_stats.items(),
            key=lambda x: x[1].win_rate,
            reverse=True
        )
        
        top_performers = [a[0] for a in sorted_agents[:2]]
        bottom_performers = [a[0] for a in sorted_agents[-2:] if a[1].trend == "DECLINING"]
        
        enrichment = self._enrichment_breakdowns(closed_trades)
        # توصيات
        recommendations = []
        for name, record in agent_stats.items():
            if record.trend == "IMPROVING":
                recommendations.append(f"✅ {name}: good performance (+{record.win_rate:.0f}%)")
            elif record.trend == "DECLINING":
                recommendations.append(f"⚠️ {name}: declining (-{record.win_rate:.0f}%)")
            if record.consecutive_wins >= 3:
                recommendations.append(f"🔥 {name}: {record.consecutive_wins} consecutive wins!")
        rr = enrichment.get("rr_efficiency", {})
        if rr.get("sample") and rr.get("rr_capture_pct", 0) < 45:
            recommendations.append(f"⚠️ RR capture low ({rr.get('rr_capture_pct')}%): review exits/trailing efficiency")
        weak_news = [k for k, v in enrichment.get("news_proximity", {}).items() if k not in {"SAFE", "UNKNOWN"} and v.get("pnl", 0) < 0]
        if weak_news:
            recommendations.append(f"📰 News filter: negative PnL under {', '.join(weak_news[:2])}")
        
        # ملخص التغييرات
        changes = []
        for name in adjusted_weights:
            old = self.current_weights.get(name, 0)
            new = adjusted_weights[name]
            diff = (new - old) * 100
            sign = "+" if diff > 0 else ""
            changes.append(f"{name}: {sign}{diff:.1f}%")
        
        return LearningReport(
            report_date=datetime.now(timezone.utc).isoformat(),
            agents_performance=agent_stats,
            adjusted_weights=adjusted_weights,
            total_trades_analyzed=total_trades,
            overall_win_rate=overall_wr,
            recommendations=recommendations,
            previous_weights=self.current_weights.copy(),
            changes_summary=", ".join(changes) if changes else "No major changes",
            top_performers=top_performers,
            bottom_performers=bottom_performers,
            session_breakdown=enrichment.get("session_breakdown", {}),
            day_of_week_breakdown=enrichment.get("day_of_week_breakdown", {}),
            rr_efficiency=enrichment.get("rr_efficiency", {}),
            news_proximity=enrichment.get("news_proximity", {}),
            regime_fit=enrichment.get("regime_fit", {}),
        )
    
    def _log_weight_changes(self, new_weights: Dict[str, float]):
        """تسجيل التغييرات في الوزن"""
        logger.info("🔄 تغييرات الأوزان:")
        for name, new in new_weights.items():
            old = self.current_weights.get(name, 0)
            diff = (new - old) * 100
            sign = "+" if diff > 0 else ""
            logger.info(f"   {name}: {old*100:.0f}% → {new*100:.0f}% ({sign}{diff:.1f}%)")
    
    def _empty_report(self) -> LearningReport:
        """تقرير فارغ"""
        return LearningReport(
            report_date=datetime.now(timezone.utc).isoformat(),
            agents_performance={},
            adjusted_weights=self.default_weights.copy(),
            total_trades_analyzed=0,
            overall_win_rate=0,
            recommendations=["Not enough data"],
            previous_weights=self.default_weights.copy(),
            changes_summary="No changes",
            session_breakdown={},
            day_of_week_breakdown={},
            rr_efficiency={"sample": 0},
            news_proximity={},
            regime_fit={},
        )
    
    def get_learning_summary(self) -> str:
        """Clean English learning summary for Telegram (sent daily ~23:00)."""
        
        if not self.learning_history:
            return "📊 No learning history yet"
        
        last_report = self.learning_history[-1]
        
        lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            "🧠 Smart Learning Report v2.0",
            f"📅 Date: {last_report.report_date[:10]}",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 Trades analyzed: {last_report.total_trades_analyzed}",
            f"📈 Overall win rate: {last_report.overall_win_rate:.1f}%",
            ""
        ]
        
        if last_report.top_performers:
            top = ", ".join(html.escape(str(name), quote=False) for name in last_report.top_performers)
            lines.append(f"🏆 Top performers: {top}")
        
        lines.append("")
        lines.append("🤖 Agent performance:")
        
        for name, record in sorted(last_report.agents_performance.items(), key=lambda x: -x[1].win_rate):
            emoji = "🟢" if record.trend == "IMPROVING" else "🔴" if record.trend == "DECLINING" else "🟡"
            safe_name = html.escape(str(name), quote=False)
            
            # عرض التتابع
            streak = ""
            if record.consecutive_wins >= 2:
                streak = f" 🔥{record.consecutive_wins}"
            elif record.consecutive_losses >= 2:
                streak = f" ❄️{record.consecutive_losses}"
            
            lines.append(
                f"{emoji} {safe_name}: {record.win_rate:.0f}% "
                f"({record.current_weight*100:.0f}%→{record.adjusted_weight*100:.0f}%){streak}"
            )
        
        rr = last_report.rr_efficiency or {}
        if rr.get("sample"):
            lines.append("")
            lines.append(
                f"🎯 RR efficiency: actual {rr.get('avg_actual_r', 0):+.2f}R "
                f"vs planned {rr.get('avg_planned_rr', 0):.2f}R "
                f"({rr.get('rr_capture_pct', 0):.1f}% capture)"
            )
        if last_report.session_breakdown:
            best_session = max(last_report.session_breakdown.items(), key=lambda kv: kv[1].get("pnl", 0))
            lines.append(f"🌍 Best session: {html.escape(str(best_session[0]), quote=False)} {best_session[1].get('pnl', 0):+.0f} pts")
        if last_report.news_proximity:
            weak_news = [f"{k} {v.get('pnl', 0):+.0f}" for k, v in last_report.news_proximity.items() if v.get("pnl", 0) < 0]
            if weak_news:
                lines.append(f"📰 Weak news bucket: {html.escape(', '.join(weak_news[:2]), quote=False)} pts")
        lines.extend([
            "",
            f"📝 Changes: {html.escape(str(last_report.changes_summary), quote=False)}",
            "━━━━━━━━━━━━━━━━━━━━"
        ])
        
        return "\n".join(lines)
    
    def get_agent_recommendation(self, agent_name: str) -> str:
        """توصية لوكيل معين"""
        
        if not self.learning_history:
            return "NEUTRAL"
        
        last_report = self.learning_history[-1]
        
        if agent_name in last_report.agents_performance:
            record = last_report.agents_performance[agent_name]
            
            # 🚀 منطق محسّن للتوصية
            if record.consecutive_wins >= 3:
                return "INCREASE_CONFIDENCE"
            elif record.trend == "IMPROVING" and record.win_rate > 65:
                return "INCREASE_CONFIDENCE"
            elif record.consecutive_losses >= 3:
                return "DECREASE_CONFIDENCE"
            elif record.trend == "DECLINING":
                return "DECREASE_CONFIDENCE"
        
        return "NEUTRAL"
    
    def get_failed_signals_insights(self) -> List[Dict]:
        """🚀 الحصول على رؤى من الصفقات الفاشلة"""
        return self.failed_signals_memory[-10:]  # آخر 10 صفقات فاشلة

# Singleton instance
_learning_service: Optional['LearningService'] = None

def get_learning_service(db, config: Dict) -> LearningService:
    """الحصول على instance خدمة التعلم"""
    global _learning_service
    if _learning_service is None:
        _learning_service = LearningService(db, config)
    return _learning_service