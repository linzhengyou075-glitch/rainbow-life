CREATE TABLE IF NOT EXISTS players (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    exp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    coins INTEGER DEFAULT 0,
    last_sign_in TEXT DEFAULT '',
    last_fortune_date TEXT DEFAULT '',
    last_wheel_date TEXT DEFAULT '',
    custom_title TEXT DEFAULT '',
    is_vip INTEGER DEFAULT 0,
    vip_until TEXT DEFAULT '',
    today_msg_count INTEGER DEFAULT 0,
    today_sticker_count INTEGER DEFAULT 0,
    streak_count INTEGER DEFAULT 0,
    sign_month TEXT DEFAULT '',
    sign_month_count INTEGER DEFAULT 0,
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS admins (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT DEFAULT 'admin',
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS shop_items (
    name TEXT UNIQUE NOT NULL,
    category TEXT DEFAULT '其他',
    price INTEGER DEFAULT 0,
    item_type TEXT DEFAULT 'normal',
    description TEXT DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS titles (
    title_name TEXT UNIQUE NOT NULL,
    price INTEGER DEFAULT 500,
    is_vip BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS user_titles (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title_name TEXT NOT NULL,
    PRIMARY KEY(group_id, user_id, title_name)
);

CREATE TABLE IF NOT EXISTS available_titles (
    title_name TEXT UNIQUE NOT NULL
);


CREATE TABLE IF NOT EXISTS sign_records (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    sign_date TEXT NOT NULL,
    source TEXT DEFAULT 'normal',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (group_id, user_id, sign_date)
);

CREATE TABLE IF NOT EXISTS admin_permissions (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    permission_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(group_id, user_id, permission_key)
);

CREATE TABLE IF NOT EXISTS admin_logs (
    id BIGSERIAL PRIMARY KEY,
    group_id TEXT NOT NULL,
    operator_user_id TEXT NOT NULL,
    target_user_id TEXT DEFAULT '',
    action TEXT NOT NULL,
    detail TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS admin_audit_log (
    id BIGSERIAL PRIMARY KEY,
    group_id TEXT NOT NULL DEFAULT '',
    actor_user_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS group_runtime_settings (
    group_id TEXT NOT NULL,
    setting_key TEXT NOT NULL,
    setting_value TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(group_id, setting_key)
);
