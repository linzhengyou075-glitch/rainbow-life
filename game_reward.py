"""Rainbow Bot V18 統一賞罰與每日上限。"""
from datetime import datetime, timedelta, timezone
from database import get_connection
from game_database import ensure_game_center_tables, get_game_setting


TW = timezone(timedelta(hours=8))


def game_date():
    return (datetime.now(TW) - timedelta(hours=5)).date().isoformat()


def daily_status(group_id, user_id):
    ensure_game_center_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO game_daily_limits(group_id,user_id,game_date)
                VALUES(%s,%s,%s) ON CONFLICT DO NOTHING
            """, (group_id, user_id, game_date()))
            c.execute("""
                SELECT * FROM game_daily_limits
                WHERE group_id=%s AND user_id=%s AND game_date=%s
            """, (group_id, user_id, game_date()))
            row = c.fetchone() or {}
        conn.commit()
        return row
    finally:
        conn.close()


def apply_game_result(group_id, user_id, player_name, game_type, won, score=0, room_code=""):
    """套用固定賞罰。達每日獎勵上限後仍記錄勝負，但不再加減資源。"""
    ensure_game_center_tables()
    status = daily_status(group_id, user_id)
    coin_cap = max(0, int(get_game_setting(group_id, "daily_coin_cap", "5000") or 5000))
    exp_cap = max(0, int(get_game_setting(group_id, "daily_exp_cap", "3000") or 3000))
    activity_cap = max(0, int(get_game_setting(group_id, "daily_activity_cap", "20") or 20))
    # 彩虹幣與 EXP 均達上限後切換為純娛樂模式：
    # 仍記錄戰績、勝場與成就，但勝負不再加減資源。
    reward_locked = (
        int(status.get("reward_coins") or 0) >= coin_cap
        and int(status.get("reward_exp") or 0) >= exp_cap
    )

    coin_delta = 0
    exp_delta = 0
    if not reward_locked:
        if won:
            coin_delta = max(0, int(get_game_setting(group_id, "winner_coin", "100") or 100))
            exp_delta = max(0, int(get_game_setting(group_id, "winner_exp", "80") or 80))
            coin_delta = min(coin_delta, max(0, coin_cap - int(status.get("reward_coins") or 0)))
            exp_delta = min(exp_delta, max(0, exp_cap - int(status.get("reward_exp") or 0)))
        else:
            coin_delta = -max(0, int(get_game_setting(group_id, "loser_coin", "20") or 20))
            exp_delta = -max(0, int(get_game_setting(group_id, "loser_exp", "10") or 10))

    conn = get_connection()
    try:
        with conn.cursor() as c:
            if coin_delta or exp_delta:
                c.execute("""
                    UPDATE players
                    SET coins=GREATEST(0,COALESCE(coins,0)+%s),
                        exp=GREATEST(0,COALESCE(exp,0)+%s)
                    WHERE group_id=%s AND user_id=%s
                """, (coin_delta, exp_delta, group_id, user_id))
            activity_gain = 1 if int(status.get("activity_points") or 0) < activity_cap else 0
            c.execute("""
                UPDATE game_daily_limits
                SET reward_coins=reward_coins+%s,
                    reward_exp=reward_exp+%s,
                    activity_points=LEAST(%s,activity_points+%s),
                    updated_at=CURRENT_TIMESTAMP
                WHERE group_id=%s AND user_id=%s AND game_date=%s
            """, (max(0, coin_delta), max(0, exp_delta), activity_cap, activity_gain,
                  group_id, user_id, game_date()))
            for stat_type in ("all", game_type):
                c.execute("""
                    INSERT INTO game_player_stats(
                        group_id,user_id,game_type,games_played,wins,losses,
                        championships,current_streak,longest_streak,total_score,
                        total_coin_delta,total_exp_delta
                    ) VALUES(%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(group_id,user_id,game_type) DO UPDATE SET
                        games_played=game_player_stats.games_played+1,
                        wins=game_player_stats.wins+EXCLUDED.wins,
                        losses=game_player_stats.losses+EXCLUDED.losses,
                        championships=game_player_stats.championships+EXCLUDED.championships,
                        current_streak=CASE WHEN EXCLUDED.wins=1
                            THEN game_player_stats.current_streak+1 ELSE 0 END,
                        longest_streak=GREATEST(
                            game_player_stats.longest_streak,
                            CASE WHEN EXCLUDED.wins=1
                                THEN game_player_stats.current_streak+1 ELSE game_player_stats.longest_streak END
                        ),
                        total_score=game_player_stats.total_score+EXCLUDED.total_score,
                        total_coin_delta=game_player_stats.total_coin_delta+EXCLUDED.total_coin_delta,
                        total_exp_delta=game_player_stats.total_exp_delta+EXCLUDED.total_exp_delta,
                        updated_at=CURRENT_TIMESTAMP
                """, (
                    group_id, user_id, stat_type,
                    1 if won else 0, 0 if won else 1, 1 if won else 0,
                    1 if won else 0, 1 if won else 0, int(score or 0),
                    coin_delta, exp_delta,
                ))
            c.execute("""
                INSERT INTO game_history(
                    group_id,room_code,game_type,user_id,player_name,result,
                    score,coin_delta,exp_delta
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (group_id, room_code, game_type, user_id, player_name,
                  "win" if won else "loss", int(score or 0), coin_delta, exp_delta))
        conn.commit()
    finally:
        conn.close()

    return {
        "coin_delta": coin_delta,
        "exp_delta": exp_delta,
        "reward_locked": reward_locked,
        "activity_gain": activity_gain,
    }
