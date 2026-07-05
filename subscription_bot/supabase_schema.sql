-- Telegram Subscription Bot – Supabase PostgreSQL schema
-- يشمل: subscribers, admins, notifications_log, settings

-- Enable uuid extension if not enabled
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- subscribers
CREATE TABLE IF NOT EXISTS subscribers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name TEXT NOT NULL,
    telegram_username TEXT,
    telegram_id BIGINT UNIQUE NOT NULL,
    can_dm BOOLEAN DEFAULT FALSE,
    join_date DATE NOT NULL,
    subscription_duration INTEGER,
    duration_type TEXT CHECK (duration_type IN ('day','week','month','year')),
    expiry_date DATE,
    status TEXT DEFAULT 'pending_duration'
        CHECK (status IN ('pending_duration','active','expired','cancelled')),
    kicked BOOLEAN DEFAULT FALSE,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- admins
CREATE TABLE IF NOT EXISTS admins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id BIGINT UNIQUE NOT NULL,
    name TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- notifications_log
CREATE TABLE IF NOT EXISTS notifications_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscriber_id UUID REFERENCES subscribers(id) ON DELETE CASCADE,
    notification_type TEXT NOT NULL CHECK (notification_type IN (
        'new_member',
        'before_3_days_admin',
        'before_3_days_subscriber',
        'before_3_days_subscriber_failed',
        'before_1_day_admin',
        'expired_admin',
        'expired_subscriber',
        'expired_subscriber_failed',
        'kicked'
    )),
    sent_to BIGINT,
    message TEXT,
    sent_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- settings
CREATE TABLE IF NOT EXISTS settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_chat_id BIGINT NOT NULL,
    admin_group_id BIGINT,
    admin_contact TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- indexes
CREATE INDEX IF NOT EXISTS idx_subscribers_status ON subscribers(status);
CREATE INDEX IF NOT EXISTS idx_subscribers_expiry ON subscribers(expiry_date);
CREATE INDEX IF NOT EXISTS idx_subscribers_tid ON subscribers(telegram_id);
CREATE INDEX IF NOT EXISTS idx_notifications_sub ON notifications_log(subscriber_id);
CREATE INDEX IF NOT EXISTS idx_notifications_type ON notifications_log(notification_type);

-- updated_at trigger
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_subscribers_updated_at ON subscribers;
CREATE TRIGGER trg_subscribers_updated_at
BEFORE UPDATE ON subscribers
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- seed first admin (عدّل الرقم)
-- INSERT INTO admins (telegram_id, name) VALUES (123456789, 'Owner') ON CONFLICT DO NOTHING;

-- seed settings
-- INSERT INTO settings (target_chat_id, admin_group_id, admin_contact)
-- VALUES (-1001234567890, NULL, '@admin_username');
