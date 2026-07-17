import datetime
from database import get_connection


def task_now():
    return datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ) - datetime.timedelta(hours=5)


def get_period_key(task_type):
    now = task_now().date()

    if task_type == "daily":
        return now.isoformat()

    if task_type == "weekly":
        year, week, _ = now.isocalendar()
        return f"{year}-W{week:02d}"

    if task_type == "monthly":
        return f"{now.year}-{now.month:02d}"

    if task_type == "quarterly":
        quarter = (now.month - 1) // 3 + 1
        return f"{now.year}-Q{quarter}"

    return now.isoformat()


def ensure_task_tables():
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                group_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                name TEXT NOT NULL,
                condition_type TEXT NOT NULL,
                target_value INTEGER DEFAULT 1,
                reward_coins INTEGER DEFAULT 0,
                reward_exp INTEGER DEFAULT 0,
                reward_title TEXT DEFAULT '',
                is_active BOOLEAN DEFAULT TRUE,
                is_official BOOLEAN DEFAULT FALSE,
                is_hidden BOOLEAN DEFAULT FALSE
            )
        """)

        c.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_official BOOLEAN DEFAULT FALSE")
        c.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN DEFAULT FALSE")

        c.execute("""
            CREATE TABLE IF NOT EXISTS user_task_progress (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                task_id INTEGER NOT NULL,
                period_key TEXT NOT NULL,
                progress INTEGER DEFAULT 0,
                is_claimed BOOLEAN DEFAULT FALSE,
                PRIMARY KEY (group_id, user_id, task_id, period_key)
            )
        """)

        conn.commit()
    conn.close()


# 官方任務：可停用、可改獎勵，但不可刪除。
OFFICIAL_TASKS = [
    # 每日任務
    ("daily", "今日簽到", "sign", 1, 300, 100, "", False),
    ("daily", "活躍聊天", "chat", 30, 500, 200, "", False),
    ("daily", "貼圖高手", "sticker", 5, 300, 100, "", False),
    ("daily", "每日運勢", "fortune", 1, 300, 0, "", False),
    ("daily", "幸運輪盤", "wheel", 1, 300, 0, "", False),
    ("daily", "寶箱獵人", "chest", 1, 500, 0, "", False),
    ("daily", "今日消費", "spend", 1000, 0, 300, "", False),
    ("daily", "每日全完成", "daily_complete", 7, 2000, 1000, "任務達人", False),

    # 每週任務
    ("weekly", "一週簽到", "sign", 5, 3000, 0, "", False),
    ("weekly", "聊天達人", "chat", 500, 0, 3000, "", False),
    ("weekly", "貼圖狂熱", "sticker", 100, 1500, 0, "", False),
    ("weekly", "命運挑戰", "fortune", 5, 2000, 0, "", False),
    ("weekly", "輪盤高手", "wheel", 10, 3000, 0, "", False),
    ("weekly", "購物狂", "spend", 10000, 0, 5000, "", False),
    ("weekly", "每週全完成", "weekly_complete", 6, 10000, 5000, "每週征服者", False),

    # 每月任務
    ("monthly", "月簽王", "sign", 25, 10000, 0, "月簽王", False),
    ("monthly", "超級活躍", "chat", 5000, 0, 10000, "超級活躍王", False),
    ("monthly", "貼圖大師", "sticker", 1000, 5000, 0, "貼圖大師", False),
    ("monthly", "輪盤王", "wheel", 30, 5000, 0, "輪盤王", False),
    ("monthly", "寶箱收藏", "chest", 30, 5000, 0, "寶箱收藏家", False),
    ("monthly", "消費王", "spend", 50000, 0, 10000, "消費王", False),
    ("monthly", "每月全完成", "monthly_complete", 6, 20000, 10000, "月光傳說", False),

    # 每季任務
    ("quarterly", "季度簽到", "sign", 70, 50000, 0, "季度守護者", False),
    ("quarterly", "社群傳奇", "chat", 20000, 0, 50000, "社群傳奇", False),
    ("quarterly", "收藏家", "title", 20, 10000, 0, "稱號收藏家", False),
    ("quarterly", "尊爵會員", "vip", 30, 0, 0, "尊爵會員", False),
    ("quarterly", "寶箱王", "chest", 100, 20000, 0, "寶箱王", False),
    ("quarterly", "每季全完成", "quarterly_complete", 5, 100000, 50000, "彩虹神話", False),
]


HIDDEN_OFFICIAL_TASKS = [
    ("quarterly", "彩虹之神", "chat", 100000, 100000, 50000, "彩虹之神", True),
    ("quarterly", "永恆守護者", "sign", 365, 200000, 100000, "永恆守護者", True),
    ("quarterly", "彩虹財神", "coin", 1000000, 300000, 0, "彩虹財神", True),
    ("quarterly", "百戰王者", "task_complete", 1000, 100000, 100000, "百戰王者", True),
    ("quarterly", "傳說降臨", "title", 50, 100000, 50000, "傳說降臨", True),
]


def ensure_official_tasks(group_id):
    """建立官方基本任務與隱藏任務；不會刪除或覆蓋群長自訂任務。"""
    ensure_task_tables()
    conn = get_connection()
    with conn.cursor() as c:
        for task in OFFICIAL_TASKS + HIDDEN_OFFICIAL_TASKS:
            task_type, name, condition_type, target_value, reward_coins, reward_exp, reward_title, is_hidden = task
            c.execute("""
                SELECT id
                FROM tasks
                WHERE group_id=%s AND task_type=%s AND name=%s
                LIMIT 1
            """, (group_id, task_type, name))
            exists = c.fetchone()

            if exists:
                c.execute("""
                    UPDATE tasks
                    SET is_official=TRUE,
                        is_hidden=%s
                    WHERE id=%s
                """, (is_hidden, exists["id"]))
                continue

            c.execute("""
                INSERT INTO tasks (
                    group_id, task_type, name, condition_type,
                    target_value, reward_coins, reward_exp, reward_title,
                    is_active, is_official, is_hidden
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE,TRUE,%s)
            """, (
                group_id, task_type, name, condition_type,
                target_value, reward_coins, reward_exp, reward_title, is_hidden
            ))
        conn.commit()
    conn.close()


def add_task(group_id, task_type, name, condition_type, target_value, reward_coins, reward_exp, reward_title=""):
    ensure_task_tables()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO tasks (
                group_id, task_type, name, condition_type,
                target_value, reward_coins, reward_exp, reward_title,
                is_official, is_hidden
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,FALSE,FALSE)
        """, (
            group_id, task_type, name, condition_type,
            target_value, reward_coins, reward_exp, reward_title
        ))
        conn.commit()
    conn.close()


def delete_task(group_id, task_name):
    """回傳：-1=官方任務不可刪除；0=找不到；1+=刪除成功。"""
    ensure_task_tables()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT is_official
            FROM tasks
            WHERE group_id=%s AND name=%s
            LIMIT 1
        """, (group_id, task_name))
        row = c.fetchone()

        if not row:
            conn.close()
            return 0

        if row["is_official"]:
            conn.close()
            return -1

        c.execute("""
            DELETE FROM tasks
            WHERE group_id=%s AND name=%s AND COALESCE(is_official,FALSE)=FALSE
        """, (group_id, task_name))
        changed = c.rowcount
        conn.commit()
    conn.close()
    return changed


def set_task_active(group_id, task_name, active=True):
    ensure_task_tables()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            UPDATE tasks
            SET is_active=%s
            WHERE group_id=%s AND name=%s
        """, (active, group_id, task_name))
        changed = c.rowcount
        conn.commit()
    conn.close()
    return changed


def list_all_tasks(group_id):
    ensure_task_tables()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT *
            FROM tasks
            WHERE group_id=%s
            ORDER BY
                is_hidden ASC,
                is_official DESC,
                CASE task_type
                    WHEN 'daily' THEN 1
                    WHEN 'weekly' THEN 2
                    WHEN 'monthly' THEN 3
                    WHEN 'quarterly' THEN 4
                    ELSE 9
                END,
                id ASC
        """, (group_id,))
        rows = c.fetchall()
    conn.close()
    return rows


def get_user_tasks(group_id, user_id, task_type):
    ensure_task_tables()
    period_key = get_period_key(task_type)

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT
                t.id,
                t.name,
                t.task_type,
                t.condition_type,
                t.target_value,
                t.reward_coins,
                t.reward_exp,
                t.reward_title,
                COALESCE(t.is_official, FALSE) AS is_official,
                COALESCE(t.is_hidden, FALSE) AS is_hidden,
                COALESCE(p.progress, 0) AS progress,
                COALESCE(p.is_claimed, FALSE) AS is_claimed
            FROM tasks t
            LEFT JOIN user_task_progress p
              ON t.id = p.task_id
             AND p.group_id=%s
             AND p.user_id=%s
             AND p.period_key=%s
            WHERE t.group_id=%s
              AND t.task_type=%s
              AND t.is_active=TRUE
              AND (
                    COALESCE(t.is_hidden,FALSE)=FALSE
                    OR COALESCE(p.progress,0) >= t.target_value
                    OR COALESCE(p.is_claimed,FALSE)=TRUE
                  )
            ORDER BY t.is_hidden ASC, t.id ASC
        """, (group_id, user_id, period_key, group_id, task_type))
        rows = c.fetchall()
    conn.close()
    return rows


def update_task_progress(group_id, user_id, condition_type, amount=1):
    ensure_task_tables()

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT id, task_type, target_value
            FROM tasks
            WHERE group_id=%s
              AND condition_type=%s
              AND is_active=TRUE
        """, (group_id, condition_type))
        tasks = c.fetchall()

        for task in tasks:
            period_key = get_period_key(task["task_type"])

            c.execute("""
                INSERT INTO user_task_progress (
                    group_id, user_id, task_id, period_key, progress, is_claimed
                )
                VALUES (%s,%s,%s,%s,%s,FALSE)
                ON CONFLICT(group_id, user_id, task_id, period_key)
                DO UPDATE SET progress = LEAST(
                    user_task_progress.progress + EXCLUDED.progress,
                    %s
                )
            """, (
                group_id,
                user_id,
                task["id"],
                period_key,
                amount,
                task["target_value"]
            ))

        conn.commit()
    conn.close()


def claim_task_reward(group_id, user_id, task_id):
    ensure_task_tables()

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT *
            FROM tasks
            WHERE id=%s AND group_id=%s AND is_active=TRUE
        """, (task_id, group_id))
        task = c.fetchone()

        if not task:
            conn.close()
            return False, "❌ 找不到這個任務。"

        period_key = get_period_key(task["task_type"])

        c.execute("""
            SELECT progress, is_claimed
            FROM user_task_progress
            WHERE group_id=%s
              AND user_id=%s
              AND task_id=%s
              AND period_key=%s
        """, (group_id, user_id, task_id, period_key))
        progress = c.fetchone()

        if not progress or progress["progress"] < task["target_value"]:
            conn.close()
            return False, "❌ 任務尚未完成。"

        if progress["is_claimed"]:
            conn.close()
            return False, "⚠️ 這個任務獎勵已領取過。"

        c.execute("""
            UPDATE user_task_progress
            SET is_claimed=TRUE
            WHERE group_id=%s
              AND user_id=%s
              AND task_id=%s
              AND period_key=%s
        """, (group_id, user_id, task_id, period_key))

        c.execute("""
            UPDATE players
            SET coins = coins + %s,
                exp = exp + %s
            WHERE group_id=%s AND user_id=%s
        """, (
            task["reward_coins"],
            task["reward_exp"],
            group_id,
            user_id
        ))

        if task["reward_title"]:
            c.execute("""
                UPDATE players
                SET custom_title=%s
                WHERE group_id=%s AND user_id=%s
            """, (task["reward_title"], group_id, user_id))

        # 完成任務領獎也計入任務完成數，用於隱藏任務/成就型任務。
        c.execute("""
            SELECT id, task_type, target_value
            FROM tasks
            WHERE group_id=%s
              AND condition_type='task_complete'
              AND is_active=TRUE
        """, (group_id,))
        complete_tasks = c.fetchall()
        for ct in complete_tasks:
            ct_period = get_period_key(ct["task_type"])
            c.execute("""
                INSERT INTO user_task_progress (
                    group_id, user_id, task_id, period_key, progress, is_claimed
                )
                VALUES (%s,%s,%s,%s,1,FALSE)
                ON CONFLICT(group_id, user_id, task_id, period_key)
                DO UPDATE SET progress = LEAST(user_task_progress.progress + 1, %s)
            """, (group_id, user_id, ct["id"], ct_period, ct["target_value"]))

        conn.commit()

    conn.close()

    msg = (
        f"🎁 任務獎勵領取成功！\n\n"
        f"📌 任務：{task['name']}\n"
        f"🌈 彩虹幣 +{task['reward_coins']}\n"
        f"⭐ EXP +{task['reward_exp']}"
    )

    if task["reward_title"]:
        hidden_text = "\n✨ 隱藏任務解鎖！" if task.get("is_hidden") else ""
        msg += f"{hidden_text}\n🏷️ 稱號：{task['reward_title']}"

    return True, msg


def task_rank_message(group_id, user_id, task_type):
    ensure_task_tables()
    period_key = get_period_key(task_type)

    title_map = {
        "daily": "📅 每日任務排行榜",
        "weekly": "📆 每週任務排行榜",
        "monthly": "🗓️ 每月任務排行榜",
        "quarterly": "🌸 每季任務排行榜",
    }
    title = title_map.get(task_type, "📊 任務排行榜")

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT
                p.name,
                COUNT(*) AS done_count
            FROM user_task_progress up
            JOIN tasks t ON up.task_id = t.id
            JOIN players p
              ON p.group_id = up.group_id
             AND p.user_id = up.user_id
            WHERE up.group_id=%s
              AND up.period_key=%s
              AND t.task_type=%s
              AND up.progress >= t.target_value
              AND COALESCE(t.is_hidden,FALSE)=FALSE
            GROUP BY p.name
            ORDER BY done_count DESC, p.name ASC
            LIMIT 10
        """, (group_id, period_key, task_type))
        rows = c.fetchall()

        c.execute("""
            SELECT rank_no, done_count
            FROM (
                SELECT
                    up.user_id,
                    COUNT(*) AS done_count,
                    ROW_NUMBER() OVER (
                        ORDER BY COUNT(*) DESC, MAX(p.name) ASC
                    ) AS rank_no
                FROM user_task_progress up
                JOIN tasks t ON up.task_id = t.id
                JOIN players p
                  ON p.group_id = up.group_id
                 AND p.user_id = up.user_id
                WHERE up.group_id=%s
                  AND up.period_key=%s
                  AND t.task_type=%s
                  AND up.progress >= t.target_value
                  AND COALESCE(t.is_hidden,FALSE)=FALSE
                GROUP BY up.user_id
            ) ranked
            WHERE user_id=%s
        """, (group_id, period_key, task_type, user_id))
        my_row = c.fetchone()
    conn.close()

    msg = f"{title}\n\n"

    if not rows:
        msg += "目前還沒有完成紀錄。\n"
    else:
        for i, r in enumerate(rows, start=1):
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"{i}."
            msg += f"{medal} {r['name']}｜完成 {r['done_count']} 個\n"

    if my_row:
        msg += (
            "\n────────────\n"
            f"👤 你的名次：#{my_row['rank_no']}\n"
            f"📌 完成任務：{my_row['done_count']} 個"
        )
    else:
        msg += (
            "\n────────────\n"
            "👤 你目前尚未完成任務"
        )

    return msg
