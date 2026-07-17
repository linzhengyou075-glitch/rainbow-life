"""Rainbow Bot V18 遊戲中心共用資料庫結構。"""
from database import get_connection


DEFAULT_SETTINGS = {
    "enabled": "1",
    "test_mode_enabled": "0",
    "room_expire_minutes": "10",
    "daily_coin_cap": "5000",
    "daily_exp_cap": "3000",
    "daily_activity_cap": "20",
    "winner_coin": "100",
    "winner_exp": "80",
    "loser_coin": "20",
    "loser_exp": "10",
    "ultimate_password_enabled": "1",
    "dice_pk_enabled": "1",
    "rainbow_slots_enabled": "1",
    "quick_quiz_enabled": "1",
    "slot_daily_plays": "3",
    "ultimate_turn_seconds": "10",
    "dice_turn_seconds": "10",
    "quiz_answer_seconds": "15",
}


def ensure_game_center_tables():
    """建立並遷移遊戲資料表；相容先前不完整的 V18 資料表。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS game_rooms (
                    id BIGSERIAL PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    room_code TEXT NOT NULL,
                    game_type TEXT NOT NULL,
                    host_user_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'waiting',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    state_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMPTZ,
                    ended_at TIMESTAMPTZ,
                    expires_at TIMESTAMPTZ,
                    UNIQUE(group_id, room_code)
                )
            """)
            # 舊版本可能已有同名表但缺欄位，逐欄補齊。
            alters = [
                "ALTER TABLE game_rooms ADD COLUMN IF NOT EXISTS room_code TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE game_rooms ADD COLUMN IF NOT EXISTS game_type TEXT NOT NULL DEFAULT 'unknown'",
                "ALTER TABLE game_rooms ADD COLUMN IF NOT EXISTS host_user_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE game_rooms ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'waiting'",
                "ALTER TABLE game_rooms ADD COLUMN IF NOT EXISTS settings_json TEXT NOT NULL DEFAULT '{}'",
                "ALTER TABLE game_rooms ADD COLUMN IF NOT EXISTS state_json TEXT NOT NULL DEFAULT '{}'",
                "ALTER TABLE game_rooms ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
                "ALTER TABLE game_rooms ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
                "ALTER TABLE game_rooms ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ",
                "ALTER TABLE game_rooms ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
            ]
            for sql in alters:
                c.execute(sql)
            c.execute("UPDATE game_rooms SET expires_at=COALESCE(expires_at,created_at + INTERVAL '10 minutes') WHERE expires_at IS NULL")
            # 清理舊版重複活動房，保留最新一間，避免唯一索引建立失敗。
            c.execute("""
                WITH ranked AS (
                    SELECT id, ROW_NUMBER() OVER(PARTITION BY group_id ORDER BY id DESC) AS rn
                    FROM game_rooms WHERE status IN ('waiting','playing')
                )
                UPDATE game_rooms SET status='cancelled',ended_at=CURRENT_TIMESTAMP
                WHERE id IN (SELECT id FROM ranked WHERE rn>1)
            """)
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_game_rooms_one_active_per_group ON game_rooms(group_id) WHERE status IN ('waiting','playing')")
            c.execute("CREATE INDEX IF NOT EXISTS idx_game_rooms_group_status ON game_rooms(group_id,status)")
            c.execute("""
                CREATE TABLE IF NOT EXISTS game_room_players (
                    room_id BIGINT NOT NULL REFERENCES game_rooms(id) ON DELETE CASCADE,
                    group_id TEXT NOT NULL,user_id TEXT NOT NULL,player_name TEXT NOT NULL DEFAULT '',
                    seat_no INTEGER NOT NULL DEFAULT 0,is_host INTEGER NOT NULL DEFAULT 0,
                    is_spectator INTEGER NOT NULL DEFAULT 0,is_ready INTEGER NOT NULL DEFAULT 1,
                    turn_done INTEGER NOT NULL DEFAULT 0,joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(room_id,user_id)
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_game_room_players_user ON game_room_players(group_id,user_id)")
            c.execute("""
                CREATE TABLE IF NOT EXISTS game_player_stats (
                    group_id TEXT NOT NULL,user_id TEXT NOT NULL,game_type TEXT NOT NULL DEFAULT 'all',
                    games_played INTEGER NOT NULL DEFAULT 0,wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,championships INTEGER NOT NULL DEFAULT 0,
                    current_streak INTEGER NOT NULL DEFAULT 0,longest_streak INTEGER NOT NULL DEFAULT 0,
                    total_score BIGINT NOT NULL DEFAULT 0,total_coin_delta BIGINT NOT NULL DEFAULT 0,
                    total_exp_delta BIGINT NOT NULL DEFAULT 0,updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(group_id,user_id,game_type)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS game_daily_limits (
                    group_id TEXT NOT NULL,user_id TEXT NOT NULL,game_date TEXT NOT NULL,
                    reward_coins INTEGER NOT NULL DEFAULT 0,reward_exp INTEGER NOT NULL DEFAULT 0,
                    activity_points INTEGER NOT NULL DEFAULT 0,slot_plays INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(group_id,user_id,game_date)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS game_history (
                    id BIGSERIAL PRIMARY KEY,group_id TEXT NOT NULL,room_code TEXT NOT NULL DEFAULT '',
                    game_type TEXT NOT NULL,user_id TEXT NOT NULL,player_name TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '',score INTEGER NOT NULL DEFAULT 0,
                    coin_delta INTEGER NOT NULL DEFAULT 0,exp_delta INTEGER NOT NULL DEFAULT 0,
                    detail_json TEXT NOT NULL DEFAULT '{}',created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_game_history_group_time ON game_history(group_id,created_at DESC)")
            c.execute("""
                CREATE TABLE IF NOT EXISTS game_achievement_claims (
                    group_id TEXT NOT NULL,user_id TEXT NOT NULL,achievement_key TEXT NOT NULL,
                    unlocked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(group_id,user_id,achievement_key)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS game_settings (
                    group_id TEXT NOT NULL,setting_key TEXT NOT NULL,setting_value TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(group_id,setting_key)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS quiz_questions (
                    id BIGSERIAL PRIMARY KEY,group_id TEXT NOT NULL DEFAULT 'GLOBAL',
                    category TEXT NOT NULL DEFAULT '生活百科',difficulty INTEGER NOT NULL DEFAULT 1,
                    question TEXT NOT NULL,option_a TEXT NOT NULL,option_b TEXT NOT NULL,
                    option_c TEXT NOT NULL,option_d TEXT NOT NULL,correct_option TEXT NOT NULL,
                    explanation TEXT NOT NULL DEFAULT '',source_type TEXT NOT NULL DEFAULT 'official',
                    is_active INTEGER NOT NULL DEFAULT 1,times_used INTEGER NOT NULL DEFAULT 0,
                    correct_count INTEGER NOT NULL DEFAULT 0,wrong_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_quiz_questions_category_active ON quiz_questions(category,is_active)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def seed_game_settings(group_id):
    if not group_id or group_id == "PRIVATE":
        return
    ensure_game_center_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            for key, value in DEFAULT_SETTINGS.items():
                c.execute("""
                    INSERT INTO game_settings(group_id,setting_key,setting_value)
                    VALUES(%s,%s,%s)
                    ON CONFLICT(group_id,setting_key) DO NOTHING
                """, (group_id, key, value))
        conn.commit()
    finally:
        conn.close()


def get_game_setting(group_id, key, default=""):
    seed_game_settings(group_id)
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT setting_value FROM game_settings
                WHERE group_id=%s AND setting_key=%s
            """, (group_id, key))
            row = c.fetchone() or {}
            return str(row.get("setting_value") or default)
    finally:
        conn.close()


def set_game_setting(group_id, key, value):
    ensure_game_center_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO game_settings(group_id,setting_key,setting_value,updated_at)
                VALUES(%s,%s,%s,CURRENT_TIMESTAMP)
                ON CONFLICT(group_id,setting_key)
                DO UPDATE SET setting_value=EXCLUDED.setting_value,updated_at=CURRENT_TIMESTAMP
            """, (group_id, key, str(value)))
        conn.commit()
    finally:
        conn.close()
