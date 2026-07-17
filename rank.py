from database import get_connection


def coin_rank(group_id, limit=10):
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT name, COALESCE(coins, 0) AS coins
            FROM players
            WHERE group_id=%s
            ORDER BY coins DESC
            LIMIT %s
        """, (group_id, limit))
        rows = c.fetchall()
    conn.close()
    return rows


def level_rank(group_id, limit=10):
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT name, COALESCE(level, 1) AS level, COALESCE(exp, 0) AS exp
            FROM players
            WHERE group_id=%s
            ORDER BY COALESCE(level, 1) DESC, COALESCE(exp, 0) DESC
            LIMIT %s
        """, (group_id, limit))
        rows = c.fetchall()
    conn.close()
    return rows
