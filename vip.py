import datetime
import traceback
from database import get_connection

PERMANENT = "PERMANENT"


def _today():
    return datetime.date.today()


def ensure_vip_tables():
    """VIP 專用資料表。只新增安全表，不破壞舊資料。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS vip_logs_v231 (
                    id SERIAL PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    target_user_id TEXT NOT NULL,
                    actor_user_id TEXT DEFAULT '',
                    action TEXT NOT NULL,
                    days_text TEXT DEFAULT '',
                    vip_until TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS vip_gifts_v231 (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    gift_date TEXT NOT NULL,
                    PRIMARY KEY(group_id, user_id, gift_date)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS vip_states_v240 (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    last_vip_badge TEXT DEFAULT '',
                    last_normal_badge TEXT DEFAULT '',
                    last_vip_title TEXT DEFAULT '',
                    last_normal_title TEXT DEFAULT '',
                    active BOOLEAN DEFAULT FALSE,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(group_id, user_id)
                )
            """)
            conn.commit()
    finally:
        conn.close()


def _is_vip_enabled(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    raw = str(value).strip().lower()
    return raw in ["1", "true", "t", "yes", "y", "on", "vip", "永久"]


def _is_permanent_until(value):
    raw = str(value or "").strip()
    raw10 = raw[:10]
    return raw in [PERMANENT, "永久", "99991231"] or raw10 in ["9999-12-31", "9999-12-30"]


def _parse_until_date(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if _is_permanent_until(raw):
        return datetime.date(9999, 12, 31)
    try:
        if raw.isdigit() and len(raw) == 8:
            return datetime.date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        return datetime.date.fromisoformat(raw[:10])
    except Exception:
        return None


def parse_vip_days(text):
    raw = str(text or "").strip().replace(" ", "").replace("　", "")
    raw = raw.replace("VIP", "").replace("vip", "").strip()
    if raw in ["永久", "永遠", "PERMANENT", "permanent", "FOREVER", "forever"]:
        return None, "永久"
    raw = raw.replace("天", "").strip()
    try:
        days = int(raw)
        if days <= 0:
            return 0, "錯誤"
        return days, f"{days}天"
    except Exception:
        return 0, "錯誤"


def _calculate_until(current_until, days):
    if days is None:
        return PERMANENT
    if _is_permanent_until(current_until):
        return PERMANENT
    today = _today()
    base = today
    old = _parse_until_date(current_until)
    if old and old > today and old.year < 9999:
        base = old
    return (base + datetime.timedelta(days=days)).isoformat()


def _get_player_vip_row(group_id, user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT is_vip, vip_until FROM players WHERE group_id=%s AND user_id=%s", (group_id, user_id))
            return c.fetchone()
    finally:
        conn.close()


def is_vip_active(group_id, user_id):
    row = _get_player_vip_row(group_id, user_id)
    if not row or not _is_vip_enabled(row.get("is_vip")):
        return False
    until = row.get("vip_until")
    if _is_permanent_until(until):
        return True
    end = _parse_until_date(until)
    if not end:
        return True
    return _today() <= end


def _write_vip_log(group_id, user_id, actor_user_id, action, days_text, vip_until):
    try:
        ensure_vip_tables()
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO vip_logs_v231(group_id, target_user_id, actor_user_id, action, days_text, vip_until)
                    VALUES(%s, %s, %s, %s, %s, %s)
                """, (group_id, user_id, actor_user_id or "", action, days_text or "", str(vip_until or "")))
                conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print("VIP LOG SKIPPED:", repr(e))


def _set_player_vip(group_id, user_id, enabled, vip_until):
    """players.is_vip 固定使用 INTEGER：1=VIP、0=非 VIP。

    這裡刻意不再自動偵測 BOOLEAN，避免 information_schema 查到其他 schema
    的同名資料表後，把 Python bool 傳給 INTEGER 欄位。
    """
    vip_value = 1 if bool(enabled) else 0
    until_value = str(vip_until or "")
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                UPDATE players
                SET is_vip=%s::integer,
                    vip_until=%s
                WHERE group_id=%s AND user_id=%s
            """, (vip_value, until_value, group_id, user_id))
            if c.rowcount == 0:
                c.execute("""
                    INSERT INTO players(group_id, user_id, name, exp, level, coins, is_vip, vip_until)
                    VALUES(%s, %s, %s, 0, 1, 0, %s::integer, %s)
                    ON CONFLICT (group_id, user_id) DO UPDATE
                    SET is_vip=EXCLUDED.is_vip,
                        vip_until=EXCLUDED.vip_until
                """, (group_id, user_id, "成員", vip_value, until_value))
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        traceback.print_exc()
        raise
    finally:
        conn.close()

def _set_vip_state(group_id, user_id, active):
    ensure_vip_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO vip_states_v240(group_id, user_id, active, updated_at)
                VALUES(%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT(group_id, user_id) DO UPDATE
                SET active=EXCLUDED.active,
                    updated_at=CURRENT_TIMESTAMP
            """, (group_id, user_id, bool(active)))
            conn.commit()
    finally:
        conn.close()


def set_vip(group_id, user_id, days=None, actor_user_id="", action="set"):
    row = _get_player_vip_row(group_id, user_id)
    current_until = row.get("vip_until") if row else ""
    vip_until = _calculate_until(current_until, days)
    _set_player_vip(group_id, user_id, True, vip_until)
    _set_vip_state(group_id, user_id, True)
    _write_vip_log(group_id, user_id, actor_user_id, action, "永久" if days is None else f"{days}天", vip_until)
    return vip_until


def grant_vip(group_id, user_id, days_text, actor_user_id=""):
    days, label = parse_vip_days(days_text)
    if label == "錯誤":
        return False, "❌ VIP 天數格式錯誤，請輸入：7天、30天、90天、永久。"
    set_vip(group_id, user_id, days, actor_user_id, "grant")
    return True, f"💎 已成功給予 Diamond VIP！\n期限：{label}\n\n✅ 已恢復 VIP 功能、福利與專屬任務。"


def extend_vip(group_id, user_id, days_text, actor_user_id=""):
    days, label = parse_vip_days(days_text)
    if label == "錯誤" or days is None:
        return False, "❌ 延長 VIP 請輸入天數，例如：7天、30天、90天。"
    set_vip(group_id, user_id, days, actor_user_id, "extend")
    return True, f"💎 已成功延長 Diamond VIP！\n期限：{label}"


def cancel_vip(group_id, user_id, actor_user_id=""):
    _set_player_vip(group_id, user_id, False, "")
    _set_vip_state(group_id, user_id, False)
    _write_vip_log(group_id, user_id, actor_user_id, "cancel", "", "")
    return True, (
        "✅ 已收回 Diamond VIP。\n\n"
        "已解除：\n"
        "• 💎 VIP 標誌\n"
        "• VIP 專屬任務\n"
        "• VIP 專屬商店\n"
        "• VIP 禮包與福利\n"
        "• VIP 專屬徽章 / 稱號使用權\n\n"
        "系統會自動恢復原本可用的最高徽章。"
    )


def check_vip_expired(group_id, user_id):
    row = _get_player_vip_row(group_id, user_id)
    if not row or not _is_vip_enabled(row.get("is_vip")):
        return False
    until = row.get("vip_until")
    if not until or _is_permanent_until(until):
        return False
    end = _parse_until_date(until)
    if end and _today() > end:
        _set_player_vip(group_id, user_id, False, "")
        _set_vip_state(group_id, user_id, False)
        _write_vip_log(group_id, user_id, "SYSTEM", "expired", "", until)
        return True
    return False


def vip_status_message(group_id, user_id, display_name="玩家"):
    row = _get_player_vip_row(group_id, user_id)
    if not row or not _is_vip_enabled(row.get("is_vip")) or not is_vip_active(group_id, user_id):
        return f"💎 VIP 資訊\n\n👤 {display_name}\n狀態：未啟用"
    until = row.get("vip_until") or ""
    if _is_permanent_until(until):
        return f"💎 VIP 資訊\n\n👤 {display_name}\n狀態：已啟用\n類型：永久 Diamond VIP"
    end = _parse_until_date(until)
    if end:
        left = max(0, (end - _today()).days)
        return f"💎 VIP 資訊\n\n👤 {display_name}\n狀態：已啟用\n到期：{end.isoformat()}\n剩餘：{left} 天"
    return f"💎 VIP 資訊\n\n👤 {display_name}\n狀態：已啟用\n到期：{until}"


def vip_record_message(group_id, user_id, display_name="玩家"):
    try:
        ensure_vip_tables()
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute("""
                    SELECT action, days_text, vip_until, created_at
                    FROM vip_logs_v231
                    WHERE group_id=%s AND target_user_id=%s
                    ORDER BY created_at DESC
                    LIMIT 20
                """, (group_id, user_id))
                rows = c.fetchall()
        finally:
            conn.close()
    except Exception:
        rows = []
    msg = f"📜 VIP 紀錄｜{display_name}\n\n"
    if not rows:
        return msg + "目前沒有 VIP 紀錄。"
    action_map = {"grant": "給予", "extend": "延長", "cancel": "收回", "set": "設定", "expired": "到期解除"}
    for r in rows:
        msg += f"・{r['created_at']}｜{action_map.get(r['action'], r['action'])}｜{r['days_text'] or '-'}｜到期：{r['vip_until'] or '-'}\n"
    return msg.rstrip()


def claim_vip_gift(group_id, user_id):
    ensure_vip_tables()
    if not is_vip_active(group_id, user_id):
        return False, "❌ 你目前不是 VIP，無法領取 VIP 禮包。"
    today = _today().isoformat()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO vip_gifts_v231(group_id, user_id, gift_date)
                VALUES(%s, %s, %s)
                ON CONFLICT(group_id, user_id, gift_date) DO NOTHING
            """, (group_id, user_id, today))
            if c.rowcount == 0:
                conn.rollback()
                return False, "⚠️ 今日 VIP 禮包已領取過囉。"
            c.execute("""
                UPDATE players
                SET coins = COALESCE(coins, 0) + 1000,
                    exp = COALESCE(exp, 0) + 500
                WHERE group_id=%s AND user_id=%s
            """, (group_id, user_id))
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        traceback.print_exc()
        raise
    finally:
        conn.close()
    return True, "🎁 今日 VIP 禮包領取成功！\n\n🌈 彩虹幣 +1000\n⭐ EXP +500"
