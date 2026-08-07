-- تعديل وقت الخروج للصفقة TRADE_20260807_110106_552214_1c3213ba
-- (BUY · دخول 4317.06 · خروج تريلنج 4352.06 · +350.0 نقطة)
-- طلب المشغّل: وقت الخروج = 16:00 بتوقيت القدس = 13:00 UTC يوم 2026-08-07.

UPDATE trades SET
  closed_at    = '2026-08-07T13:00:00+00:00',
  close_time   = '2026-08-07T13:00:00+00:00',
  last_updated = NOW()
WHERE id = 'TRADE_20260807_110106_552214_1c3213ba';

-- تحقق: يجب أن يعيد سطراً واحداً بوقتي الخروج 13:00 UTC
SELECT id, status, close_price, final_pnl_points, closed_at, close_time
FROM trades
WHERE id = 'TRADE_20260807_110106_552214_1c3213ba';
