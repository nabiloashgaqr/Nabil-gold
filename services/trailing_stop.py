"""
📈 Trailing Stop - Gold AI Signals
تفعيل بعد TP1 + إغلاق جزئي 50%
"""

import logging
from datetime import datetime
from typing import Dict, Optional, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class TrailingState(Enum):
    """حالات التتبع"""
    INACTIVE = "inactive"
    TP1_HIT = "tp1_hit"
    PARTIAL_CLOSE = "partial_close"
    TRAILING_TO_TP2 = "trailing_to_tp2"
    CLOSED_AT_TP2 = "closed_at_tp2"
    STOPPED_OUT = "stopped_out"

@dataclass
class TrailingConfig:
    """إعدادات التتبع"""
    # TP1 config
    tp1_trigger_pct: float = 50.0  # تفعيل التتبع عند وصول الربح 50%
    
    # Partial close
    partial_close_pct: float = 50.0  # إغلاق 50% عند TP1
    
    # Trailing config
    trailing_distance: float = 20.0  # المسافة بين السعر الحالي ووقف الخسارة (نقاط)
    trailing_step: float = 5.0  # خطوة التحديث (نقاط)
    
    # TP2
    tp2_trigger_pct: float = 100.0  # TP2 عند ربح 100%
    tp2_distance: float = 15.0  # وقف TP2 أقرب
    
    # Safety
    min_profit_lock: float = 0.0  # الحد الأدنى للربح المؤكد

@dataclass
class TrailingStatus:
    """حالة التتبع"""
    trade_id: str
    state: TrailingState
    entry_price: float
    current_price: float
    stop_loss: float
    tp1_price: float
    tp2_price: float
    position_size: float
    remaining_size: float
    profit_pct: float
    locked_profit: float
    last_updated: str
    actions_log: List[str]

class TrailingStopManager:
    """
    📈 Trailing Stop Manager
    - يتبع السعر بعد الوصول لـ TP1
    - إغلاق جزئي 50% عند TP1
    - تحريك وقف الخسارة للأعلى
    """
    
    def __init__(self, database_service, config: Dict):
        self.db = database_service
        self.config = config
        self.trailing_config = self._load_trailing_config()
        
    def _load_trailing_config(self) -> TrailingConfig:
        """تحميل إعدادات التتبع من config"""
        ts_config = self.config.get('trailing_stop', {})
        
        return TrailingConfig(
            tp1_trigger_pct=ts_config.get('tp1_trigger_pct', 50.0),
            partial_close_pct=ts_config.get('partial_close_pct', 50.0),
            trailing_distance=ts_config.get('trailing_distance', 20.0),
            trailing_step=ts_config.get('trailing_step', 5.0),
            tp2_trigger_pct=ts_config.get('tp2_trigger_pct', 100.0),
            tp2_distance=ts_config.get('tp2_distance', 15.0),
            min_profit_lock=ts_config.get('min_profit_lock', 0.0)
        )
    
    async def check_and_update_trade(self, trade: Dict) -> Optional[Dict]:
        """
        🔍 فحص وتحديث صفقة واحدة
        """
        try:
            trade_id = trade.get('id')
            trade_type = trade.get('trade_type')  # BUY or SELL
            entry_price = float(trade.get('entry_price', 0))
            current_price = float(trade.get('current_price', 0))
            stop_loss = float(trade.get('stop_loss', 0))
            take_profit = float(trade.get('take_profit', 0))
            position_size = float(trade.get('quantity', 0.01))
            
            if not all([entry_price, current_price, trade_type]):
                return None
            
            # حساب نسبة الربح الحالي
            if trade_type == 'BUY':
                profit_pct = ((current_price - entry_price) / entry_price) * 100
                tp1_price = (entry_price + take_profit) / 2  # halfway to TP
                tp2_price = take_profit
            else:  # SELL
                profit_pct = ((entry_price - current_price) / entry_price) * 100
                tp1_price = (entry_price + take_profit) / 2
                tp2_price = take_profit
            
            # التحقق من التفعيل
            if profit_pct >= self.trailing_config.tp1_trigger_pct:
                return await self._handle_trailing_activation(
                    trade_id, trade, trade_type, entry_price, current_price,
                    stop_loss, tp1_price, tp2_price, profit_pct, position_size
                )
            
            return None
            
        except Exception as e:
            logger.error(f"❌ خطأ في فحص الصفقة: {e}")
            return None
    
    async def _handle_trailing_activation(
        self, trade_id: str, trade: Dict, trade_type: str,
        entry_price: float, current_price: float, stop_loss: float,
        tp1_price: float, tp2_price: float, profit_pct: float,
        position_size: float
    ) -> Dict:
        """
        🚀 تفعيل التتبع عند وصول TP1
        """
        actions = []
        
        # 1️⃣ إغلاق جزئي 50%
        partial_size = position_size * (self.trailing_config.partial_close_pct / 100)
        remaining_size = position_size - partial_size
        
        actions.append(
            f"✅ إغلاق جزئي: {partial_size:.4f} lot (50% من {position_size:.4f})"
        )
        
        # 2️⃣ تحريك وقف الخسارة
        if trade_type == 'BUY':
            new_stop_loss = current_price - self.trailing_config.trailing_distance
            # التأكد من أن وقف الخسارة أعلى من نقطة التعادل
            new_stop_loss = max(new_stop_loss, entry_price + self.trailing_config.min_profit_lock)
        else:
            new_stop_loss = current_price + self.trailing_config.trailing_distance
            new_stop_loss = min(new_stop_loss, entry_price - self.trailing_config.min_profit_lock)
        
        actions.append(f"📈 وقف الخسارة الجديد: {new_stop_loss:.2f}")
        
        # تحديث وقف الخسارة في قاعدة البيانات
        await self._update_trade_stop_loss(trade_id, new_stop_loss)
        
        # تسجيل الحدث
        status = TrailingStatus(
            trade_id=trade_id,
            state=TrailingState.TP1_HIT,
            entry_price=entry_price,
            current_price=current_price,
            stop_loss=new_stop_loss,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            position_size=position_size,
            remaining_size=remaining_size,
            profit_pct=profit_pct,
            locked_profit=partial_size * abs(current_price - entry_price),
            last_updated=datetime.utcnow().isoformat(),
            actions_log=actions
        )
        
        logger.info(f"📈 Trailing Stop فعال للصفقة {trade_id}: ربح {profit_pct:.1f}%")
        
        return {
            'trade_id': trade_id,
            'action': 'trailing_activated',
            'partial_close': partial_size,
            'new_stop_loss': new_stop_loss,
            'remaining_position': remaining_size,
            'profit_locked': status.locked_profit,
            'messages': actions
        }
    
    async def update_trailing_stop(
        self, trade_id: str, trade_type: str, current_price: float,
        entry_price: float, current_stop_loss: float, position_size: float
    ) -> Optional[Dict]:
        """
        🔄 تحديث وقف الخسارة المتحرك
        """
        try:
            actions = []
            
            if trade_type == 'BUY':
                # وقف الخسارة المتحرك للأعلى فقط
                potential_new_sl = current_price - self.trailing_config.trailing_distance
                
                if potential_new_sl > current_stop_loss:
                    # تحديث وقف الخسارة
                    new_stop_loss = potential_new_sl
                    distance_moved = new_stop_loss - current_stop_loss
                    
                    actions.append(
                        f"📈 تحديث Trailing: SL {current_stop_loss:.2f} → {new_stop_loss:.2f} "
                        f"(+{distance_moved:.2f})"
                    )
                    
                    await self._update_trade_stop_loss(trade_id, new_stop_loss)
                    
                    return {
                        'trade_id': trade_id,
                        'action': 'stop_loss_updated',
                        'old_stop_loss': current_stop_loss,
                        'new_stop_loss': new_stop_loss,
                        'messages': actions
                    }
                    
            else:  # SELL
                potential_new_sl = current_price + self.trailing_config.trailing_distance
                
                if potential_new_sl < current_stop_loss:
                    new_stop_loss = potential_new_sl
                    distance_moved = current_stop_loss - new_stop_loss
                    
                    actions.append(
                        f"📈 تحديث Trailing: SL {current_stop_loss:.2f} → {new_stop_loss:.2f} "
                        f"(+{distance_moved:.2f})"
                    )
                    
                    await self._update_trade_stop_loss(trade_id, new_stop_loss)
                    
                    return {
                        'trade_id': trade_id,
                        'action': 'stop_loss_updated',
                        'old_stop_loss': current_stop_loss,
                        'new_stop_loss': new_stop_loss,
                        'messages': actions
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ خطأ في تحديث Trailing Stop: {e}")
            return None
    
    async def check_tp2_reached(
        self, trade_id: str, trade_type: str, current_price: float, tp2_price: float
    ) -> bool:
        """التحقق من الوصول لـ TP2 وإغلاق الصفقة"""
        try:
            if trade_type == 'BUY' and current_price >= tp2_price:
                return True
            elif trade_type == 'SELL' and current_price <= tp2_price:
                return True
            return False
        except Exception as e:
            logger.error(f"❌ خطأ في فحص TP2: {e}")
            return False
    
    async def _update_trade_stop_loss(self, trade_id: str, new_stop_loss: float):
        """تحديث وقف الخسارة في قاعدة البيانات"""
        try:
            query = """
                UPDATE trades 
                SET stop_loss = $1, updated_at = NOW()
                WHERE id = $2
            """
            await self.db.execute_query(query, [new_stop_loss, trade_id])
            logger.info(f"✅ تم تحديث SL للصفقة {trade_id}: {new_stop_loss}")
        except Exception as e:
            logger.error(f"❌ فشل تحديث SL: {e}")
    
    async def process_all_trades(self, open_trades: List[Dict]) -> List[Dict]:
        """
        🔄 معالجة جميع الصفقات المفتوحة
        """
        results = []
        
        for trade in open_trades:
            if trade.get('status') != 'OPEN':
                continue
            
            result = await self.check_and_update_trade(trade)
            if result:
                results.append(result)
            
            # تحديث التتبع للصفقات النشطة
            if trade.get('trailing_active'):
                trailing_result = await self.update_trailing_stop(
                    trade_id=trade.get('id'),
                    trade_type=trade.get('trade_type'),
                    current_price=float(trade.get('current_price', 0)),
                    entry_price=float(trade.get('entry_price', 0)),
                    current_stop_loss=float(trade.get('stop_loss', 0)),
                    position_size=float(trade.get('quantity', 0.01))
                )
                if trailing_result:
                    results.append(trailing_result)
        
        return results
    
    def format_trailing_message(self, result: Dict) -> str:
        """تنسيق رسالة التتبع لتيليجرام"""
        action = result.get('action', '')

        lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            "📈 *Trailing Stop激活*",
            "━━━━━━━━━━━━━━━━━━━━"
        ]
        
        if action == 'trailing_activated':
            lines.extend([
                f"🆔 الصفقة: `{result.get('trade_id')}`",
                f"💰 الإغلاق الجزئي: {result.get('partial_close', 0):.4f} lot",
                f"📈 وقف الخسارة الجديد: {result.get('new_stop_loss', 0):.2f}",
                f"✅ ربح مؤمن: ${result.get('profit_locked', 0):.2f}",
                f"📊 الحجم المتبقي: {result.get('remaining_position', 0):.4f} lot"
            ])
        elif action == 'stop_loss_updated':
            lines.extend([
                f"🆔 الصفقة: `{result.get('trade_id')}`",
                f"🔄 تحديث الوقف: {result.get('old_stop_loss'):.2f} → {result.get('new_stop_loss'):.2f}"
            ])
        
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)

# Singleton instance
_trailing_manager: Optional[TrailingStopManager] = None

def get_trailing_stop_manager(db, config: Dict) -> TrailingStopManager:
    """الحصول على instance مدير التتبع"""
    global _trailing_manager
    if _trailing_manager is None:
        _trailing_manager = TrailingStopManager(db, config)
    return _trailing_manager