"""Rainbow Bot V18 共用遊戲室。"""
import json
import random
import string
from datetime import datetime, timedelta, timezone
from database import get_connection
from game_database import ensure_game_center_tables, get_game_setting


TW = timezone(timedelta(hours=8))


def _room_code():
    return "".join(random.choice(string.digits) for _ in range(6))


def cleanup_expired_rooms(group_id=None):
    ensure_game_center_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            if group_id:
                c.execute("""
                    UPDATE game_rooms SET status='expired',ended_at=CURRENT_TIMESTAMP
                    WHERE group_id=%s AND status='waiting' AND expires_at<=CURRENT_TIMESTAMP
                    RETURNING id,group_id,room_code,game_type
                """, (group_id,))
            else:
                c.execute("""
                    UPDATE game_rooms SET status='expired',ended_at=CURRENT_TIMESTAMP
                    WHERE status='waiting' AND expires_at<=CURRENT_TIMESTAMP
                    RETURNING id,group_id,room_code,game_type
                """)
            rows = c.fetchall() or []
        conn.commit()
        return rows
    finally:
        conn.close()


def active_room(group_id):
    cleanup_expired_rooms(group_id)
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT * FROM game_rooms
                WHERE group_id=%s AND status IN ('waiting','playing')
                ORDER BY id DESC LIMIT 1
            """, (group_id,))
            return c.fetchone()
    finally:
        conn.close()


def room_players(room_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT * FROM game_room_players
                WHERE room_id=%s ORDER BY seat_no,joined_at
            """, (room_id,))
            return c.fetchall() or []
    finally:
        conn.close()


def create_room(group_id, game_type, host_user_id, host_name, settings=None):
    ensure_game_center_tables()
    cleanup_expired_rooms(group_id)
    if active_room(group_id):
        return False, "目前群組已有進行中或等待中的遊戲室。", None

    expire_minutes = max(1, int(get_game_setting(group_id, "room_expire_minutes", "10") or 10))
    conn = get_connection()
    try:
        with conn.cursor() as c:
            code = _room_code()
            expires_at = datetime.now(TW) + timedelta(minutes=expire_minutes)
            c.execute("""
                INSERT INTO game_rooms(
                    group_id,room_code,game_type,host_user_id,status,
                    settings_json,state_json,expires_at
                ) VALUES(%s,%s,%s,%s,'waiting',%s,'{}',%s)
                RETURNING *
            """, (group_id, code, game_type, host_user_id,
                  json.dumps(settings or {}, ensure_ascii=False), expires_at))
            room = c.fetchone()
            c.execute("""
                INSERT INTO game_room_players(
                    room_id,group_id,user_id,player_name,seat_no,is_host,is_ready
                ) VALUES(%s,%s,%s,%s,1,1,1)
            """, (room["id"], group_id, host_user_id, host_name))
        conn.commit()
        return True, "遊戲室建立成功。", room
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def join_room(group_id, user_id, player_name):
    room = active_room(group_id)
    if not room:
        return False, "目前沒有可加入的遊戲室。", None
    if room["status"] != "waiting":
        return False, "遊戲已開始，無法中途加入。", room
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT 1 FROM game_room_players
                WHERE room_id=%s AND user_id=%s
            """, (room["id"], user_id))
            if c.fetchone():
                return False, "你已經在遊戲室內。", room
            c.execute("SELECT COALESCE(MAX(seat_no),0)+1 AS next_seat FROM game_room_players WHERE room_id=%s", (room["id"],))
            seat = int((c.fetchone() or {}).get("next_seat") or 1)
            c.execute("""
                INSERT INTO game_room_players(
                    room_id,group_id,user_id,player_name,seat_no,is_host,is_ready
                ) VALUES(%s,%s,%s,%s,%s,0,1)
            """, (room["id"], group_id, user_id, player_name, seat))
        conn.commit()
        return True, "已加入遊戲室。", room
    finally:
        conn.close()


def leave_room(group_id, user_id):
    room = active_room(group_id)
    if not room:
        return False, "目前沒有遊戲室。"
    if room["status"] == "playing":
        return False, "遊戲已開始，暫時不能離開。"
    if str(room["host_user_id"]) == str(user_id):
        dissolve_room(group_id, user_id, force=True)
        return True, "房主已離開，遊戲室同步解散。"
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM game_room_players WHERE room_id=%s AND user_id=%s", (room["id"], user_id))
            removed = c.rowcount > 0
        conn.commit()
        return removed, "已離開遊戲室。" if removed else "你不在遊戲室內。"
    finally:
        conn.close()


def start_room(group_id, host_user_id, minimum_players=2):
    room = active_room(group_id)
    if not room:
        return False, "目前沒有遊戲室。", None
    if str(room["host_user_id"]) != str(host_user_id):
        return False, "只有房主可以開始遊戲。", room
    players = room_players(room["id"])
    active_players = [p for p in players if not int(p.get("is_spectator") or 0)]
    if len(active_players) < int(minimum_players):
        return False, f"至少需要 {minimum_players} 位玩家才能開始。", room
    random.SystemRandom().shuffle(active_players)
    conn = get_connection()
    try:
        with conn.cursor() as c:
            for index, row in enumerate(active_players, start=1):
                c.execute("""
                    UPDATE game_room_players SET seat_no=%s,turn_done=0
                    WHERE room_id=%s AND user_id=%s
                """, (index, room["id"], row["user_id"]))
            c.execute("""
                UPDATE game_rooms
                SET status='playing',started_at=CURRENT_TIMESTAMP
                WHERE id=%s
                RETURNING *
            """, (room["id"],))
            room = c.fetchone()
        conn.commit()
        return True, "遊戲開始。", room
    finally:
        conn.close()


def dissolve_room(group_id, actor_user_id="", force=False):
    room = active_room(group_id)
    if not room:
        return False, "目前沒有遊戲室。"
    if not force and str(room["host_user_id"]) != str(actor_user_id):
        return False, "只有房主可以解散遊戲室。"
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                UPDATE game_rooms SET status='cancelled',ended_at=CURRENT_TIMESTAMP
                WHERE id=%s
            """, (room["id"],))
        conn.commit()
        return True, "遊戲室已解散。"
    finally:
        conn.close()
