# Nabil-gold – Finnhub Migration

Migriert von Twelve Data → Finnhub

## الملفات المعدلة (9 ملفات)

1. `services/market_data.py` – Finnhub primary, Twelve fallback
2. `config.json` – data_source.primary = "finnhub"
3. `scripts/validate_setup.py` – يطلب FINNHUB_API_KEY
4. `.env.example` – أضيف FINNHUB_API_KEY
5. `.github/workflows/analyze.yml`
6. `.github/workflows/update_trades.yml`
7. `.github/workflows/backtest.yml`
8. `.github/workflows/daily_report.yml`
9. `.github/workflows/weekly_report.yml`

## التركيب

انسخ هذه الملفات فوق مجلد الريبو بنفس المسارات:

```
cp -r services/ /path/to/Nabil-gold/
cp config.json /path/to/Nabil-gold/
cp .env.example /path/to/Nabil-gold/
cp scripts/validate_setup.py /path/to/Nabil-gold/scripts/
cp -r .github/ /path/to/Nabil-gold/
```

أو فك الضغط مباشرة داخل الريبو:
```
unzip Nabil-gold-finnhub.zip -d /path/to/Nabil-gold/ -o
```

## GitHub Secrets المطلوبة

Settings → Secrets → Actions → New repository secret

```
FINNHUB_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
SUPABASE_URL
SUPABASE_KEY
```

الحصول على مفتاح مجاني: https://finnhub.io/register
Free tier: 60 calls/min = 86,400/day

## OANDA Symbols

- XAU/USD → OANDA:XAU_USD
- EUR/USD → OANDA:EUR_USD
- GBP/USD → OANDA:GBP_USD
- USD/JPY → OANDA:USD_JPY
- USD/CHF → OANDA:USD_CHF
- USD/CAD → OANDA:USD_CAD
- AUD/USD → OANDA:AUD_USD
- WTI/USD → OANDA:WTICO_USD (+ 4 fallbacks)

## الاختبار

```
pytest -q
# 299 passed
```

Commit: 62e75b7
Date: 2026-06-26
