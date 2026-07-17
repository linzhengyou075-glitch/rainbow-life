import datetime
from database import get_connection

TAIPEI_TZ = "Asia/Taipei"

# V2.8.4 第一階段：固定循環金庫目標。
# 達標後扣除門檻、保留超額，再自動發放全群獎勵。
VAULT_MILESTONE_TARGET = 50000
VAULT_REWARD_COINS = 300
VAULT_REWARD_EXP = 100
EXP_PER_LEVEL = 100


def ensure_commerce_tables(conn=None):
    own = conn is None
    conn = conn or get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS purchase_history (
                    id BIGSERIAL PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    player_name TEXT DEFAULT '',
                    item_type TEXT NOT NULL DEFAULT '其他',
                    item_name TEXT NOT NULL,
                    cost INTEGER NOT NULL CHECK (cost >= 0),
                    vault_added INTEGER NOT NULL DEFAULT 0 CHECK (vault_added >= 0),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_purchase_group_time ON purchase_history(group_id, created_at DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_purchase_group_user ON purchase_history(group_id, user_id, created_at DESC)")
            c.execute("""
                CREATE TABLE IF NOT EXISTS group_vaults (
                    group_id TEXT PRIMARY KEY,
                    balance BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
                    lifetime_income BIGINT NOT NULL DEFAULT 0 CHECK (lifetime_income >= 0),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS vault_milestone_events (
                    id BIGSERIAL PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    target_amount BIGINT NOT NULL,
                    reward_coins INTEGER NOT NULL DEFAULT 0,
                    reward_exp INTEGER NOT NULL DEFAULT 0,
                    rewarded_members INTEGER NOT NULL DEFAULT 0,
                    balance_before BIGINT NOT NULL DEFAULT 0,
                    balance_after BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_vault_events_group_time ON vault_milestone_events(group_id, created_at DESC)")
            c.execute("""
                CREATE TABLE IF NOT EXISTS vault_pending_announcements (
                    id BIGSERIAL PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    event_id BIGINT NOT NULL REFERENCES vault_milestone_events(id),
                    message TEXT NOT NULL,
                    consumed BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_vault_pending_group ON vault_pending_announcements(group_id, consumed, id)")
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def record_purchase_in_transaction(c, group_id, user_id, item_type, item_name, cost):
    """必須在既有交易內呼叫：消費 100% 進入該群金庫。"""
    amount = max(int(cost or 0), 0)
    c.execute("SELECT name FROM players WHERE group_id=%s AND user_id=%s", (group_id, user_id))
    row = c.fetchone()
    name = (row or {}).get("name") or "成員"
    c.execute("""
        INSERT INTO purchase_history(group_id,user_id,player_name,item_type,item_name,cost,vault_added)
        VALUES(%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (group_id, user_id, name, item_type, item_name, amount, amount))
    purchase_id = int(c.fetchone()["id"])
    c.execute("""
        INSERT INTO group_vaults(group_id,balance,lifetime_income,updated_at)
        VALUES(%s,%s,%s,CURRENT_TIMESTAMP)
        ON CONFLICT(group_id) DO UPDATE
        SET balance=group_vaults.balance+EXCLUDED.balance,
            lifetime_income=group_vaults.lifetime_income+EXCLUDED.lifetime_income,
            updated_at=CURRENT_TIMESTAMP
    """, (group_id, amount, amount))
    _trigger_vault_milestones_in_transaction(c, group_id)
    return purchase_id



def _trigger_vault_milestones_in_transaction(c, group_id):
    """同一筆消費交易內自動處理達標，任何人都不能手動呼叫修改金庫。"""
    while True:
        c.execute("SELECT balance FROM group_vaults WHERE group_id=%s FOR UPDATE", (group_id,))
        row = c.fetchone() or {"balance": 0}
        before = int(row.get("balance") or 0)
        if before < VAULT_MILESTONE_TARGET:
            break

        c.execute("SELECT COUNT(*) AS count FROM players WHERE group_id=%s", (group_id,))
        member_count = int((c.fetchone() or {}).get("count") or 0)

        if member_count > 0:
            c.execute("""
                UPDATE players
                SET coins = COALESCE(coins,0) + %s,
                    exp = COALESCE(exp,0) + %s,
                    level = GREATEST(1, ((COALESCE(exp,0) + %s) / %s) + 1)
                WHERE group_id=%s
            """, (VAULT_REWARD_COINS, VAULT_REWARD_EXP, VAULT_REWARD_EXP, EXP_PER_LEVEL, group_id))

        after = before - VAULT_MILESTONE_TARGET
        c.execute("""
            UPDATE group_vaults
            SET balance=%s, updated_at=CURRENT_TIMESTAMP
            WHERE group_id=%s
        """, (after, group_id))
        c.execute("""
            INSERT INTO vault_milestone_events(
                group_id,target_amount,reward_coins,reward_exp,rewarded_members,balance_before,balance_after
            ) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (group_id, VAULT_MILESTONE_TARGET, VAULT_REWARD_COINS, VAULT_REWARD_EXP, member_count, before, after))
        event_id = int(c.fetchone()["id"])
        msg = (
            "🎉 彩虹金庫自動達標！\n\n"
            f"🏦 達成目標：{VAULT_MILESTONE_TARGET:,}\n"
            f"👥 發放成員：{member_count} 人\n\n"
            "🎁 全體獎勵\n"
            f"🌈 彩虹幣 +{VAULT_REWARD_COINS:,}\n"
            f"⭐ EXP +{VAULT_REWARD_EXP:,}\n\n"
            f"💰 保留超額金額：{after:,}\n"
            "金庫已自動開始下一輪累積。"
        )
        c.execute("""
            INSERT INTO vault_pending_announcements(group_id,event_id,message)
            VALUES(%s,%s,%s)
        """, (group_id, event_id, msg))


def consume_pending_vault_announcements(group_id):
    """交易提交後取得尚未顯示的自動達標公告，並標記已顯示。"""
    ensure_commerce_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT id,message FROM vault_pending_announcements
                WHERE group_id=%s AND consumed=FALSE
                ORDER BY id ASC
                FOR UPDATE
            """, (group_id,))
            rows = c.fetchall()
            if rows:
                c.execute("""
                    UPDATE vault_pending_announcements SET consumed=TRUE
                    WHERE id = ANY(%s)
                """, ([int(r["id"]) for r in rows],))
        conn.commit()
        return [r["message"] for r in rows]
    finally:
        conn.close()


def format_vault_history(group_id, limit=10):
    ensure_commerce_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT target_amount,reward_coins,reward_exp,rewarded_members,balance_after,
                       to_char(created_at AT TIME ZONE 'Asia/Taipei','YYYY/MM/DD HH24:MI') AS local_time
                FROM vault_milestone_events
                WHERE group_id=%s
                ORDER BY created_at DESC,id DESC
                LIMIT %s
            """, (group_id, limit))
            rows = c.fetchall()
    finally:
        conn.close()
    lines = ["🏦 金庫自動活動紀錄", ""]
    if not rows:
        lines.append("目前尚未觸發金庫達標活動。")
    for i, r in enumerate(rows, 1):
        lines += [
            f"{i}. {r['local_time']}",
            f"🎯 達標：{int(r['target_amount']):,}",
            f"👥 發放：{int(r['rewarded_members'])} 人",
            f"🎁 每人：🌈{int(r['reward_coins']):,}＋⭐{int(r['reward_exp']):,}",
            f"💰 結餘：{int(r['balance_after']):,}",
            ""
        ]
    lines.append("🔒 紀錄由系統自動產生，任何人都不能修改或刪除。")
    return "\n".join(lines)

def transaction_code(purchase_id, created_date=None):
    day = created_date or datetime.date.today().strftime("%Y%m%d")
    return f"RB{day}{int(purchase_id):06d}"


def get_vault(group_id):
    ensure_commerce_tables()
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT balance,lifetime_income FROM group_vaults WHERE group_id=%s", (group_id,))
            row=c.fetchone() or {"balance":0,"lifetime_income":0}
            return int(row["balance"] or 0), int(row["lifetime_income"] or 0)
    finally:
        conn.close()


def _period_clause(period):
    if period == "today":
        return "(created_at AT TIME ZONE 'Asia/Taipei')::date = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Taipei')::date"
    if period == "yesterday":
        return "(created_at AT TIME ZONE 'Asia/Taipei')::date = ((CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Taipei')::date - 1)"
    return "TRUE"


def format_purchase_details(group_id, period="today", limit=20):
    ensure_commerce_tables()
    clause=_period_clause(period)
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute(f"""
                SELECT id,player_name,item_type,item_name,cost,vault_added,
                       to_char(created_at AT TIME ZONE 'Asia/Taipei','YYYY/MM/DD HH24:MI') AS local_time,
                       to_char(created_at AT TIME ZONE 'Asia/Taipei','YYYYMMDD') AS code_day
                FROM purchase_history
                WHERE group_id=%s AND {clause}
                ORDER BY created_at DESC,id DESC
                LIMIT %s
            """, (group_id, limit))
            rows=c.fetchall()
            c.execute(f"""
                SELECT COUNT(*) AS tx_count, COUNT(DISTINCT user_id) AS people,
                       COALESCE(SUM(cost),0) AS total, COALESCE(SUM(vault_added),0) AS vault_total
                FROM purchase_history WHERE group_id=%s AND {clause}
            """, (group_id,))
            total=c.fetchone()
    finally:
        conn.close()
    labels={"today":"今日消費明細","yesterday":"昨日消費明細","all":"全部消費明細"}
    lines=[f"📊 {labels.get(period,'消費明細')}",""]
    if period == "all" and int(total['tx_count'] or 0) > limit:
        lines.append(f"目前顯示最近 {limit} 筆，共 {int(total['tx_count'] or 0)} 筆。")
        lines.append("")
    if not rows:
        lines.append("目前沒有消費紀錄。")
    for i,r in enumerate(rows,1):
        code=transaction_code(r['id'],r['code_day'])
        lines += [
            f"{i}. {r['local_time']}",
            f"👤 {r['player_name']}",
            f"📦 {r['item_type']}｜{r['item_name']}",
            f"🌈 消費：-{int(r['cost']):,}",
            f"🏦 金庫：+{int(r['vault_added']):,}",
            f"🔖 交易編號：#{code}",
            ""
        ]
    lines += ["━━━━━━━━━━━━",f"🧾 交易：{int(total['tx_count'] or 0)} 筆",f"👥 消費人數：{int(total['people'] or 0)} 人",f"🌈 總消費：{int(total['total'] or 0):,}",f"🏦 金庫增加：{int(total['vault_total'] or 0):,}"]
    return "\n".join(lines)


def format_member_purchase_details(group_id, member_query, limit=20):
    """群長專用：依群組搜尋成員並顯示最近消費與累積統計。"""
    ensure_commerce_tables()
    query = (member_query or "").strip()
    if not query:
        return (
            "❌ 請輸入要查詢的成員名稱。\n\n"
            "正確格式：\n"
            "!消費紀錄 成員名稱"
        )

    conn = get_connection()
    try:
        with conn.cursor() as c:
            # 先找完全相同名稱；找不到才使用包含搜尋。
            c.execute(
                """
                SELECT user_id,name FROM players
                WHERE group_id=%s AND LOWER(COALESCE(name,''))=LOWER(%s)
                ORDER BY name,user_id
                """,
                (group_id, query),
            )
            members = c.fetchall()
            if not members:
                c.execute(
                    """
                    SELECT user_id,name FROM players
                    WHERE group_id=%s AND COALESCE(name,'') ILIKE %s
                    ORDER BY name,user_id
                    LIMIT 10
                    """,
                    (group_id, f"%{query}%"),
                )
                members = c.fetchall()

            if not members:
                return f"❌ 找不到成員「{query}」。"
            if len(members) > 1:
                names = [f"{i}. {m.get('name') or '未命名成員'}" for i, m in enumerate(members, 1)]
                return (
                    f"🔎 找到多位符合「{query}」的成員：\n\n"
                    + "\n".join(names)
                    + "\n\n請輸入更完整的成員名稱。"
                )

            member = members[0]
            user_id = member['user_id']
            member_name = member.get('name') or '未命名成員'
            c.execute(
                """
                SELECT id,item_type,item_name,cost,vault_added,
                       to_char(created_at AT TIME ZONE 'Asia/Taipei','YYYY/MM/DD HH24:MI') AS local_time,
                       to_char(created_at AT TIME ZONE 'Asia/Taipei','YYYYMMDD') AS code_day
                FROM purchase_history
                WHERE group_id=%s AND user_id=%s
                ORDER BY created_at DESC,id DESC
                LIMIT %s
                """,
                (group_id, user_id, limit),
            )
            rows = c.fetchall()
            c.execute(
                """
                SELECT COUNT(*) AS tx_count,COALESCE(SUM(cost),0) AS total,
                       COALESCE(SUM(vault_added),0) AS vault_total
                FROM purchase_history
                WHERE group_id=%s AND user_id=%s
                """,
                (group_id, user_id),
            )
            totals = c.fetchone() or {}
    finally:
        conn.close()

    tx_count = int(totals.get('tx_count') or 0)
    lines = [
        "📜 成員消費紀錄",
        f"👤 {member_name}",
        "",
        f"🌈 累積消費：{int(totals.get('total') or 0):,}",
        f"🏦 累積金庫貢獻：{int(totals.get('vault_total') or 0):,}",
        f"🧾 累積交易：{tx_count} 筆",
        "",
    ]
    if tx_count > limit:
        lines += [f"以下顯示最近 {limit} 筆：", ""]
    if not rows:
        lines.append("目前沒有消費紀錄。")
    for i, r in enumerate(rows, 1):
        code = transaction_code(r['id'], r['code_day'])
        lines += [
            f"{i}. {r['local_time']}",
            f"📦 {r['item_type']}｜{r['item_name']}",
            f"🌈 消費：-{int(r['cost']):,}",
            f"🏦 金庫：+{int(r['vault_added']):,}",
            f"🔖 交易編號：#{code}",
            "",
        ]
    return "\n".join(lines).rstrip()


def format_vault(group_id):
    balance,lifetime=get_vault(group_id)
    percent = min(int((balance / VAULT_MILESTONE_TARGET) * 100), 100) if VAULT_MILESTONE_TARGET else 0
    remaining = max(VAULT_MILESTONE_TARGET - balance, 0)
    filled = min(percent // 10, 10)
    bar = "█" * filled + "░" * (10 - filled)
    return ("🏦 彩虹金庫\n\n"
            f"🌈 目前金額：{balance:,}\n"
            f"🎯 下一目標：{VAULT_MILESTONE_TARGET:,}\n"
            f"📊 {bar} {percent}%\n"
            f"💰 還差：{remaining:,}\n\n"
            f"🎁 達標後全體：🌈+{VAULT_REWARD_COINS:,}、⭐+{VAULT_REWARD_EXP:,}\n"
            f"📈 歷史累積收入：{lifetime:,}\n\n"
            "🔒 金庫只會由成員消費增加，達標後由系統自動發放；任何人（包含群長）都不能手動修改。")
