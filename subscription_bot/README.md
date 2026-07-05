# 🤖 Nabil Gold – Telegram Subscription Manager Bot

Private channel/group subscription manager – integrated with **Nabil Gold** repo.
**Admin-only bot – subscribers are 100% silent.**

> Golden Rule – subscriber receives **exactly 2 messages** in lifetime:
> 1. ⏰ Reminder 3 days before expiry (EN)
> 2. ❌ Expired / kicked message (EN)
>
> Everything else: silent. No commands visible, no lists, no status query.
> `/start` → mandatory activation button → “✅ Activation successful” – technical only to enable DM.

Built for: **Nabil Gold – XAU/USD SmartSignal**
- Repo: `nabiloashgaqr/Nabil-gold`
- DB: **same Supabase** as Nabil Gold (`SUPABASE_URL` / `SUPABASE_KEY`)
- Channel: same `TELEGRAM_CHAT_ID` / `TARGET_CHAT_ID`
- Admin contact: **@Smart_Pro2026**

---

## ✨ Features

- ✅ Auto-capture `ChatMemberUpdated` – new member join
- ✅ Supabase store: full_name, telegram_username, telegram_id, join_date, pending_duration
- ✅ Admin panel Inline – private only – English UI
- ✅ Duration buttons: 1 week / 1 month / 3 / 6 months / 1 year / custom days
- ✅ 3-day reminder → admin + 1 DM to subscriber (EN)
- ✅ 1-day urgent alert → admin only
- ✅ Auto-kick on expiry day → ban + immediate unban (allows re-join)
- ✅ Expired DM to subscriber (EN) + admin notice + [Renew & Re-invite]
- ✅ Notification deduplication via `notifications_log`
- ✅ Renew after kick → `create_chat_invite_link` 1-use, 24h
- ✅ Search, report, edit expiry, delete, manual kick
- ✅ **Mandatory /start activation button** – new users only
- ✅ 100% silent for non-admins
- ✅ Scheduler every 6h – Asia/Hebron
- ✅ Full logging

---

## 📁 Project Structure

```
subscription_bot/        # copy into Nabil-gold/ as subscription_bot/
├── main.py
├── config.py            # reads BOT_TOKEN / TELEGRAM_BOT_TOKEN, SUPABASE_URL, TELEGRAM_CHAT_ID
├── database.py          # Supabase – subscribers / admins / notifications_log / settings
├── scheduler.py
├── handlers/
│   ├── member_handler.py
│   ├── admin_handler.py
│   ├── callback_handler.py
│   └── silent_handler.py   # /start + mandatory activate button + silent drop
├── services/
│   ├── notification_service.py  # EN messages only
│   ├── kick_service.py
│   └── invite_service.py
├── supabase_schema.sql
├── requirements.txt
└── README.md
```

To integrate into **Nabil Gold** monorepo:

```
Nabil-gold/
├── agents/
├── services/
├── subscription_bot/   ← copy here
│   ├── main.py
│   ├── ...
└── ...
```

It uses the **same** `.env` keys as Nabil Gold:
```
TELEGRAM_BOT_TOKEN=...
SUPABASE_URL=...
SUPABASE_KEY=...
TELEGRAM_CHAT_ID=-100xxxxxxxxxx   # ← used as TARGET_CHAT_ID automatically
ADMIN_IDS=...
ADMIN_CONTACT=@Smart_Pro2026
```

No conflict – subscription tables are separate: `subscribers`, `admins`, `notifications_log`, `settings`.

---

## 🚀 Quick Start – Nabil Gold Integrated

### 1. Supabase – add tables

In Supabase SQL Editor (same project as Nabil Gold trades):
run `subscription_bot/supabase_schema.sql`

Creates:
- `subscribers`
- `admins`
- `notifications_log`
- `settings`

Existing Nabil Gold tables (`trades`, `trade_snapshots`…) are untouched.

### 2. BotFather

- `@BotFather` → `/newbot` – or **reuse Nabil Gold bot token** if you want 1 bot for both signals + subscription management (recommended – less quota confusion)
  - If reuse: the subscription bot will share `TELEGRAM_BOT_TOKEN` – handlers are separate, no conflict (different update types)
  - If separate bot: create new token → set `BOT_TOKEN` in `.env`
- Set:
  ```
  /setprivacy → Disable
  /setjoingroups → Enable
  ```

### 3. Add bot admin to private channel

Target channel = same Nabil Gold signals channel (`TELEGRAM_CHAT_ID`)

Admin rights needed:
- ✅ Ban users
- ✅ Invite users via link

Get channel ID:
- temporarily add `@RawDataBot` → copy `chat.id` like `-100...` → set `TARGET_CHAT_ID` or just use existing `TELEGRAM_CHAT_ID`

Bot is **100% silent inside the channel** – never posts there.

### 4. .env – Nabil Gold unified

```
# Reuse Nabil Gold keys:
TELEGRAM_BOT_TOKEN=...
SUPABASE_URL=...
SUPABASE_KEY=...
TELEGRAM_CHAT_ID=-100xxxxxxxxxx

# subscription specific:
ADMIN_IDS=123456789
ADMIN_CONTACT=@Smart_Pro2026
TIMEZONE=Asia/Hebron

# optional overrides:
# BOT_TOKEN=...           # if separate bot, else uses TELEGRAM_BOT_TOKEN
# TARGET_CHAT_ID=...      # defaults to TELEGRAM_CHAT_ID
# ADMIN_GROUP_ID=...
```

### 5. Run

```bash
cd Nabil-gold/subscription_bot
pip install -r requirements.txt
python main.py
```

Expected log:
```
✅ Nabil Gold – Subscription Bot started
Target chat: -100...
Admin contact: @Smart_Pro2026
Bot status in target: administrator – can_restrict_members=True
Scheduler configured – every 6 hours – tz Asia/Hebron
```

---

## 📱 Subscriber Flow – English Only

| Step | Subscriber sees | Admin sees |
|---|---|---|
| Joins channel via invite | **nothing** | 🆕 New member joined – choose duration buttons |
| First DM /start | Button: **🔔 Activate Alerts** → after click: **✅ Activation successful – You will now receive subscription alerts.** | – |
| Any other message to bot | **silence – no reply** | – |
| 3 days before expiry | **ONE DM only:**<br>`⏰ Your subscription expires in 3 days on 2026-07-20`<br>`To renew, contact admin: @Smart_Pro2026` | Alert + [Renew][Ignore] |
| 1 day before | **nothing** | `⚠️ URGENT – Subscription expires tomorrow!` + [Renew now] |
| Expiry day | **Auto-kicked**<br>then **ONE DM only:**<br>`❌ Your subscription has expired and you have been removed from the channel`<br>`To renew, contact admin: @Smart_Pro2026` | `❌ Member auto-kicked – subscription expired` + [Renew & Re-invite] |

**If subscriber never pressed /start → Activate:**
- 3-day DM fails → Bot auto-notifies admin:
  `⚠️ Failed to send 3-day reminder to <name> (<id>) – User has not activated bot (/start). Notify manually.`
  + Contact: @Smart_Pro2026

---

## 👮 Admin Panel

Private chat → `/admin`

```
🛠 Subscription Admin Panel
━━━━━━━━━━━━━━
Nabil Gold – Private Channel Manager
Admin: @Smart_Pro2026

[📋 Pending Duration] [✅ Active]
[⏰ Expiring Soon]    [❌ Expired]
[🔄 Renew]            [✏️ Edit Expiry]
[🗑 Delete]           [🚫 Kick Manual]
[🔍 Search]           [📊 Report]
```

Text quick commands (admin private only):
```
custom_<subscriber_id>_<days>
renew_<subscriber_id>_<days>
edit_<subscriber_id>_YYYY-MM-DD
delete_<subscriber_id>
kick_<telegram_id>
search <name or @username or ID>
```

Renew after kick → auto `create_chat_invite_link` member_limit=1, expire 24h

---

## 🗄 Supabase Schema

See `supabase_schema.sql` – 4 tables:
- `subscribers (id, full_name, telegram_username, telegram_id UNIQUE, can_dm, join_date, subscription_duration, duration_type, expiry_date, status CHECK pending_duration|active|expired|cancelled, kicked, …)`
- `admins`
- `notifications_log` – prevents duplicate alerts
- `settings`

Coexists safely with Nabil Gold tables (`trades`, `trade_snapshots`, `performance_logs` …)

---

## ⏰ Scheduler

- APScheduler AsyncIOScheduler
- every **6 hours**, timezone `Asia/Hebron`
- order: expired kick first → 1-day admin → 3-day admin+subscriber
- deduplication via `notifications_log`

---

## 🔒 Privacy – Golden Rule enforced

- Non-admin:
  - `/start` → shows **mandatory Activate button** once → after click: `✅ Activation successful`
  - already activated → `/start` → `✅ Activated – You will receive subscription alerts.`
  - **any other message / command / callback → complete silent drop – no reply, no “not authorized”**
- Admin commands (`/admin`, callbacks `dur:`, `renew:`, …) → `if not is_admin → silent ignore`
- Bot **never posts in target channel** – 0 messages
- All subscriber DMs: **exactly 2 in lifetime** – 3-day reminder (EN), expired-kicked (EN)
- Activation DM: 1 extra technical message `✅ Activation successful` – allowed per spec (“/start first time technical enable DM”)

---

## 🧪 Test Plan

1. Join test account → Admin gets “New member joined” with duration buttons
2. Test account → /start → see **Activate Alerts** button → press → `✅ Activation successful`
3. In Supabase set `expiry_date = today + 3 days` → restart bot (scheduler runs on start) → check:
   - Admin gets 3-day alert + [Renew][Ignore]
   - Test account gets 1 DM: `Your subscription expires in 3 days… @Smart_Pro2026`
4. Set `expiry_date = today` → restart → auto-kick → check:
   - Admin: `Member auto-kicked`
   - Test account: `Your subscription has expired… @Smart_Pro2026`
5. Renew via admin button → get new 1-use invite link
6. Send random text to bot as non-admin → **no reply** ✓
7. Send /admin as non-admin → **silence** ✓

---

## 📦 Requirements

```
python-telegram-bot[rate-limiter]==21.7
supabase==2.9.1
APScheduler==3.10.4
python-dotenv==1.0.1
pytz==2024.1
postgrest==0.13.2
```

Python 3.10+

---

## 🔗 Nabil Gold Integration

- **Same repo:** copy folder to `Nabil-gold/subscription_bot/`
- **Same DB:** uses `SUPABASE_URL` / `SUPABASE_KEY` – tables are `subscribers_*` – no collision with `trades`
- **Same Telegram:** `TARGET_CHAT_ID` defaults to `TELEGRAM_CHAT_ID` – uses Nabil Gold private signals channel
- **Same admin contact:** `@Smart_Pro2026`
- Can run **alongside** `main.py` (signals bot) – either:
  - **Option A (recommended): separate bot token** → run `subscription_bot/main.py` as second process – GitHub Actions separate workflow
  - **Option B: shared bot token** – merge Application handlers – possible but requires Dispatcher merge – keep separate for now to respect “subscriber sees 0 messages except 2” golden rule

---

**Project:** Nabil Gold – Telegram Subscription Manager  
**Version:** 2.0 – Macro-integrated – English subscriber UX – Mandatory /start activation  
**Admin:** @Smart_Pro2026  
**Location:** Nablus, Palestine 🇵🇸  
**Timezone:** Asia/Hebron
