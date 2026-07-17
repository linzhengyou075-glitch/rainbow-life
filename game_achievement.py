"""Rainbow Bot V18 共用遊戲成就。"""
from database import get_connection
from game_database import ensure_game_center_tables
from game_rank import player_game_stats


ACHIEVEMENTS = [
    ("first_game", "🎮 初次遊玩", lambda s: int(s.get("games_played") or 0) >= 1),
    ("first_win", "🏆 首次勝利", lambda s: int(s.get("wins") or 0) >= 1),
    ("games_10", "🎯 十場玩家", lambda s: int(s.get("games_played") or 0) >= 10),
    ("games_50", "🌈 五十場玩家", lambda s: int(s.get("games_played") or 0) >= 50),
    ("games_100", "👑 百場玩家", lambda s: int(s.get("games_played") or 0) >= 100),
    ("streak_5", "🔥 五連勝", lambda s: int(s.get("longest_streak") or 0) >= 5),
    ("streak_10", "💫 十連勝", lambda s: int(s.get("longest_streak") or 0) >= 10),
]


def unlock_available_game_achievements(group_id, user_id):
    ensure_game_center_tables()
    stats = player_game_stats(group_id, user_id, "all")
    unlocked = []
    conn = get_connection()
    try:
        with conn.cursor() as c:
            for key, label, condition in ACHIEVEMENTS:
                if not condition(stats):
                    continue
                c.execute("""
                    INSERT INTO game_achievement_claims(group_id,user_id,achievement_key)
                    VALUES(%s,%s,%s)
                    ON CONFLICT DO NOTHING RETURNING achievement_key
                """, (group_id, user_id, key))
                if c.fetchone():
                    unlocked.append(label)
        conn.commit()
    finally:
        conn.close()
    return unlocked
