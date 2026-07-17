import datetime
from database import get_connection


def now_tw():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


DEFAULT_BADGES = [
    # 任務成長徽章：永久
    ("task_bronze", "🥉", "勤勞銅章", "task", "普通", 10, False, 0),
    ("task_silver", "🥈", "勤勞銀章", "task", "稀有", 20, False, 0),
    ("task_gold", "🥇", "勤勞金章", "task", "史詩", 30, False, 0),
    ("task_rainbow", "🌈", "彩虹榮耀", "task", "神話", 40, False, 0),

    # 特殊挑戰徽章：永久
    ("challenge_100_days", "🔥", "百日毅力", "challenge", "傳說", 60, False, 0),
    ("challenge_chat_king", "💬", "話題王", "challenge", "傳說", 61, False, 0),
    ("challenge_full_attendance", "📅", "全勤王", "challenge", "傳說", 62, False, 0),
    ("challenge_chest_collector", "🎁", "寶箱收藏家", "challenge", "傳說", 63, False, 0),
    ("challenge_lucky_god", "🎰", "幸運之神", "challenge", "傳說", 64, False, 0),
    ("challenge_rainbow_legend", "🌈", "彩虹傳奇", "challenge", "神話", 70, False, 0),

    # 榮譽徽章：群長頒發，永久
    ("honor_month_star", "⭐", "本月之星", "honor", "史詩", 80, False, 0),
    ("honor_helper", "🤝", "熱心幫手", "honor", "史詩", 81, False, 0),
    ("honor_popular", "❤️", "最佳人氣", "honor", "史詩", 82, False, 0),
    ("honor_host", "🎤", "活動主持", "honor", "史詩", 83, False, 0),
    ("honor_contribution", "🏅", "特別貢獻", "honor", "傳說", 84, False, 0),

    # 隱藏徽章：不顯示取得條件，永久
    ("hidden_night_owl", "👻", "夜貓子", "hidden", "稀有", 90, True, 0),
    ("hidden_chosen_one", "🍀", "天選之人", "hidden", "傳說", 91, True, 0),
    ("hidden_easter_egg", "🥚", "彩蛋發現者", "hidden", "史詩", 92, True, 0),
    ("hidden_star_traveler", "🌌", "星空旅人", "hidden", "傳說", 93, True, 0),

    # 活動徽章範例：取得後 30 天可顯示，之後永久收藏但不再顯示
    ("event_christmas", "🎄", "聖誕限定", "event", "活動", 100, False, 30),
    ("event_new_year", "🧧", "新春限定", "event", "活動", 100, False, 30),
    ("event_halloween", "🎃", "萬聖限定", "event", "活動", 100, False, 30),
    ("event_spring", "🌸", "春季活動", "event", "活動", 100, False, 30),
    ("event_anniversary", "🎆", "週年慶限定", "event", "活動", 100, False, 30),
]


def ensure_badge_tables():
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS badges (
                id SERIAL PRIMARY KEY,
                badge_key TEXT UNIQUE NOT NULL,
                emoji TEXT NOT NULL,
                name TEXT NOT NULL,
                badge_type TEXT NOT NULL DEFAULT 'task',
                rarity TEXT DEFAULT '普通',
                priority INTEGER DEFAULT 0,
                is_hidden BOOLEAN DEFAULT FALSE,
                display_days INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS user_badges (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                badge_id INTEGER NOT NULL REFERENCES badges(id) ON DELETE CASCADE,
                earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                display_until TIMESTAMP NULL,
                PRIMARY KEY(group_id, user_id, badge_id)
            )
        """)

        conn.commit()
    conn.close()


def seed_default_badges():
    ensure_badge_tables()
    conn = get_connection()
    with conn.cursor() as c:
        for badge_key, emoji, name, badge_type, rarity, priority, is_hidden, display_days in DEFAULT_BADGES:
            c.execute("""
                INSERT INTO badges (
                    badge_key, emoji, name, badge_type, rarity,
                    priority, is_hidden, display_days, is_active
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
                ON CONFLICT(badge_key)
                DO UPDATE SET
                    emoji=EXCLUDED.emoji,
                    name=EXCLUDED.name,
                    badge_type=EXCLUDED.badge_type,
                    rarity=EXCLUDED.rarity,
                    priority=EXCLUDED.priority,
                    is_hidden=EXCLUDED.is_hidden,
                    display_days=EXCLUDED.display_days,
                    is_active=TRUE
            """, (
                badge_key, emoji, name, badge_type, rarity,
                priority, is_hidden, display_days
            ))
        conn.commit()
    conn.close()


def grant_badge(group_id, user_id, badge_key):
    """
    發放徽章。
    活動徽章依 display_days 自動設定可顯示期限；期限後仍永久收藏，但不會顯示在名字前。
    """
    seed_default_badges()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT id, name, emoji, display_days
            FROM badges
            WHERE badge_key=%s AND is_active=TRUE
        """, (badge_key,))
        badge = c.fetchone()

        if not badge:
            conn.close()
            return False, "❌ 找不到這個徽章。"

        display_until = None
        if badge["display_days"] and badge["display_days"] > 0:
            display_until = now_tw() + datetime.timedelta(days=int(badge["display_days"]))

        c.execute("""
            INSERT INTO user_badges(group_id, user_id, badge_id, display_until)
            VALUES(%s,%s,%s,%s)
            ON CONFLICT(group_id, user_id, badge_id)
            DO UPDATE SET
                display_until=EXCLUDED.display_until,
                earned_at=CURRENT_TIMESTAMP
        """, (group_id, user_id, badge["id"], display_until))

        conn.commit()
    conn.close()

    return True, f"🏅 已獲得徽章：{badge['emoji']} {badge['name']}"


def grant_badge_by_name(group_id, user_id, badge_name):
    seed_default_badges()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT badge_key
            FROM badges
            WHERE name=%s AND is_active=TRUE
        """, (badge_name,))
        row = c.fetchone()
    conn.close()

    if not row:
        return False, "❌ 找不到這個徽章名稱。"

    return grant_badge(group_id, user_id, row["badge_key"])


def revoke_badge_by_name(group_id, user_id, badge_name):
    seed_default_badges()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            DELETE FROM user_badges ub
            USING badges b
            WHERE ub.badge_id=b.id
              AND ub.group_id=%s
              AND ub.user_id=%s
              AND b.name=%s
        """, (group_id, user_id, badge_name))
        changed = c.rowcount
        conn.commit()
    conn.close()

    if changed:
        return True, f"✅ 已收回徽章：{badge_name}"
    return False, f"❌ 對方沒有徽章：{badge_name}"


def get_highest_badge(group_id, user_id):
    """
    取得目前可顯示的最高徽章。
    活動徽章若 display_until 過期，只保留收藏，不參與名字顯示。
    """
    seed_default_badges()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT b.emoji, b.name, b.badge_type, b.rarity, b.priority
            FROM user_badges ub
            JOIN badges b ON ub.badge_id=b.id
            WHERE ub.group_id=%s
              AND ub.user_id=%s
              AND b.is_active=TRUE
              AND (
                    ub.display_until IS NULL
                    OR ub.display_until >= CURRENT_TIMESTAMP
              )
            ORDER BY b.priority DESC, ub.earned_at DESC
            LIMIT 1
        """, (group_id, user_id))
        row = c.fetchone()
    conn.close()
    return row


def display_name_with_badge(group_id, user_id, name):
    badge = get_highest_badge(group_id, user_id)
    if not badge:
        return name
    return f"{badge['emoji']} {name}"


def my_badges_message(group_id, user_id):
    seed_default_badges()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT
                b.emoji,
                b.name,
                b.badge_type,
                b.rarity,
                b.priority,
                b.is_hidden,
                ub.earned_at,
                ub.display_until,
                CASE
                    WHEN ub.display_until IS NULL THEN TRUE
                    WHEN ub.display_until >= CURRENT_TIMESTAMP THEN TRUE
                    ELSE FALSE
                END AS can_display
            FROM user_badges ub
            JOIN badges b ON ub.badge_id=b.id
            WHERE ub.group_id=%s
              AND ub.user_id=%s
            ORDER BY b.priority DESC, ub.earned_at DESC
        """, (group_id, user_id))
        rows = c.fetchall()
    conn.close()

    if not rows:
        return "🏅 我的徽章\n\n目前尚未獲得任何徽章。"

    highest = get_highest_badge(group_id, user_id)

    msg = "🏅 我的徽章\n\n"
    if highest:
        msg += f"目前名字顯示：{highest['emoji']} {highest['name']}\n\n"

    msg += "────────────\n"

    for r in rows:
        status = "可顯示" if r["can_display"] else "收藏"
        if r["display_until"] and not r["can_display"]:
            status = "收藏｜顯示期限已結束"

        msg += (
            f"{r['emoji']} {r['name']}\n"
            f"稀有度：{r['rarity']}\n"
            f"狀態：{status}\n"
            "────────────\n"
        )

    return msg


def badge_catalog_message(show_hidden=False):
    seed_default_badges()
    conn = get_connection()
    with conn.cursor() as c:
        if show_hidden:
            c.execute("""
                SELECT emoji, name, badge_type, rarity, is_hidden
                FROM badges
                WHERE is_active=TRUE
                ORDER BY priority DESC, id ASC
            """)
        else:
            c.execute("""
                SELECT emoji, name, badge_type, rarity, is_hidden
                FROM badges
                WHERE is_active=TRUE
                  AND is_hidden=FALSE
                ORDER BY priority DESC, id ASC
            """)
        rows = c.fetchall()
    conn.close()

    if not rows:
        return "目前沒有徽章資料。"

    type_name = {
        "task": "任務徽章",
        "challenge": "挑戰徽章",
        "honor": "榮譽徽章",
        "event": "活動徽章",
        "hidden": "隱藏徽章",
    }

    msg = "🏅 徽章列表\n\n"
    current_type = None

    for r in rows:
        label = type_name.get(r["badge_type"], r["badge_type"])
        if label != current_type:
            current_type = label
            msg += f"\n【{label}】\n"

        hidden_note = "（隱藏）" if r["is_hidden"] else ""
        msg += f"{r['emoji']} {r['name']}｜{r['rarity']}{hidden_note}\n"

    return msg.strip()


def add_custom_badge(badge_key, emoji, name, badge_type, rarity="普通", priority=0, is_hidden=False, display_days=0):
    ensure_badge_tables()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO badges (
                badge_key, emoji, name, badge_type, rarity,
                priority, is_hidden, display_days, is_active
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
            ON CONFLICT(badge_key)
            DO UPDATE SET
                emoji=EXCLUDED.emoji,
                name=EXCLUDED.name,
                badge_type=EXCLUDED.badge_type,
                rarity=EXCLUDED.rarity,
                priority=EXCLUDED.priority,
                is_hidden=EXCLUDED.is_hidden,
                display_days=EXCLUDED.display_days,
                is_active=TRUE
        """, (badge_key, emoji, name, badge_type, rarity, priority, is_hidden, display_days))
        conn.commit()
    conn.close()
    return f"✅ 已新增／更新徽章：{emoji} {name}"


def deactivate_badge(name):
    ensure_badge_tables()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            UPDATE badges
            SET is_active=FALSE
            WHERE name=%s
        """, (name,))
        changed = c.rowcount
        conn.commit()
    conn.close()

    if changed:
        return f"✅ 已停用徽章：{name}"
    return f"❌ 找不到徽章：{name}"
