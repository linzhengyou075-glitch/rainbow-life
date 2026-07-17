"""Rainbow Bot V18 遊戲戰績與排行榜。"""
from database import get_connection
from game_database import ensure_game_center_tables


def player_game_stats(group_id, user_id, game_type="all"):
    ensure_game_center_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT * FROM game_player_stats
                WHERE group_id=%s AND user_id=%s AND game_type=%s
            """, (group_id, user_id, game_type))
            return c.fetchone() or {}
    finally:
        conn.close()


def game_leaderboard(group_id, game_type="all", limit=10):
    ensure_game_center_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT s.*,COALESCE(p.name,s.user_id) AS player_name
                FROM game_player_stats s
                LEFT JOIN players p ON p.group_id=s.group_id AND p.user_id=s.user_id
                WHERE s.group_id=%s AND s.game_type=%s
                ORDER BY s.wins DESC,s.longest_streak DESC,s.games_played DESC
                LIMIT %s
            """, (group_id, game_type, int(limit)))
            return c.fetchall() or []
    finally:
        conn.close()
