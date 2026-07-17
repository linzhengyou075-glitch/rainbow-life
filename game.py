from database import get_connection
from config import SIGN_EXP, SIGN_COIN, CHAT_EXP
from vip_settings import get_vip_settings, vip_is_active_value
from progression import exp_needed, level_title, rank_title


def ensure_player(group_id, user_id, name="成員"):
    """
    確保玩家存在，並在成功取得 LINE 暱稱時同步更新名稱。
    注意：如果 LINE API 暫時抓不到名稱，傳入「成員 / 冒險者」時不會覆蓋原本名稱。
    """
    safe_name = (name or "").strip() or "成員"
    is_real_name = safe_name not in ["成員", "冒險者", "未知成員"]

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO players(group_id, user_id, name)
            VALUES(%s, %s, %s)
            ON CONFLICT(group_id, user_id) DO UPDATE
            SET name = CASE
                WHEN %s THEN EXCLUDED.name
                ELSE players.name
            END
        """, (group_id, user_id, safe_name, is_real_name))
        conn.commit()
    conn.close()


def get_player(group_id, user_id):
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT *
            FROM players
            WHERE group_id=%s AND user_id=%s
        """, (group_id, user_id))
        row = c.fetchone()
    conn.close()
    return row



def _reward_blocked_by_mute(group_id, user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT COALESCE(mute_until,'') AS mute_until FROM players WHERE group_id=%s AND user_id=%s", (group_id, user_id))
            row = c.fetchone() or {}
        value = str(row.get("mute_until") or "")
        if not value: return False
        if value.upper() == "PERMANENT": return True
        from datetime import datetime, timezone, timedelta
        end = datetime.fromisoformat(value)
        if end.tzinfo is None: end = end.replace(tzinfo=timezone(timedelta(hours=8)))
        return end > datetime.now(timezone(timedelta(hours=8)))
    except Exception:
        return False
    finally:
        conn.close()



def add_exp(group_id, user_id, amount, source=""):
    """增加經驗並以動態需求升級；每日聊天 EXP 不設上限。回傳升級資訊。"""
    if _reward_blocked_by_mute(group_id, user_id):
        return {"gained":0,"old_level":1,"new_level":1,"level_exp":0,"needed":0,"leveled_up":False,"rank":"","level_title":""}
    base_amount = max(0, int(amount or 0))
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS level_exp BIGINT DEFAULT 0")
            c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS exp_system_version INTEGER DEFAULT 1")
            c.execute("""
                SELECT COALESCE(level,1) AS level, COALESCE(exp,0) AS exp,
                       COALESCE(level_exp,0) AS level_exp,
                       COALESCE(exp_system_version,1) AS exp_system_version,
                       COALESCE(is_vip,0) AS is_vip, COALESCE(vip_until,'') AS vip_until
                FROM players WHERE group_id=%s AND user_id=%s FOR UPDATE
            """, (group_id, user_id))
            player = c.fetchone() or {}
            old_level = max(1, int(player.get("level") or 1))
            total_exp = max(0, int(player.get("exp") or 0))
            level_exp = max(0, int(player.get("level_exp") or 0))
            # 首次升級到新版：保留原等級，只把舊制尾數當成本級進度，避免成員被降級。
            if int(player.get("exp_system_version") or 1) < 2:
                level_exp = total_exp % 100
            gained = int(base_amount)
            if gained > 0 and vip_is_active_value(player.get("is_vip"), player.get("vip_until")):
                gained = max(0, round(gained * float(get_vip_settings(group_id)["exp_multiplier"])))
            total_exp += gained
            level_exp += gained
            level = old_level
            while level_exp >= exp_needed(level):
                level_exp -= exp_needed(level)
                level += 1
            c.execute("""
                UPDATE players SET exp=%s, level=%s, level_exp=%s, exp_system_version=2
                WHERE group_id=%s AND user_id=%s
            """, (total_exp, level, level_exp, group_id, user_id))
        conn.commit()
        return {
            "gained": gained, "old_level": old_level, "new_level": level,
            "level_exp": level_exp, "needed": exp_needed(level),
            "leveled_up": level > old_level,
            "rank": rank_title(level), "level_title": level_title(level),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_coins(group_id, user_id, amount, source=""):
    if _reward_blocked_by_mute(group_id, user_id):
        return 0
    conn = get_connection()
    with conn.cursor() as c:
        gained = int(amount)
        c.execute("""
            UPDATE players
            SET coins = COALESCE(coins, 0) + %s
            WHERE group_id=%s AND user_id=%s
        """, (gained, group_id, user_id))
        conn.commit()
    conn.close()
    return gained
