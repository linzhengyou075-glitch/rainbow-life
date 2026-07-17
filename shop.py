from database import get_connection
import datetime
from zoneinfo import ZoneInfo
from config import VIP_7_PRICE, VIP_30_PRICE, VIP_FOREVER_PRICE
from commerce import ensure_commerce_tables, record_purchase_in_transaction, transaction_code, consume_pending_vault_announcements
from vip_settings import get_vip_settings, effective_vip_price

DEFAULT_SHOP_ITEMS = [
    ("VIP7天", "VIP", VIP_7_PRICE, "vip_7", "開通 7 天 VIP"),
    ("VIP30天", "VIP", VIP_30_PRICE, "vip_30", "開通 30 天 VIP"),
    ("VIP永久", "VIP", VIP_FOREVER_PRICE, "vip_forever", "永久 VIP"),
]

DEFAULT_TITLES = [
    ("⚔️ 弒神之刃", 10000, False),
    ("🖤 暗黑彩虹支配者", 12000, False),
    ("🪙 金庫掠奪者", 15000, False),
    ("⚖️ 終焉之刻審判長", 18000, False),
    ("👤 創世神的影子", 20000, False),
]

DEFAULT_VIP_TITLES = [
    ("💎 永恆彩虹至尊", 0, True),
    ("💎 彩虹創世神", 0, True),
    ("💎 星耀VIP", 0, True),
    ("💎 永恆守護者", 0, True),
    ("💎 至尊榮耀", 0, True),
]


def ensure_shop_tables():
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS shop_items (
                name TEXT UNIQUE NOT NULL,
                category TEXT DEFAULT '其他',
                price INTEGER DEFAULT 0,
                item_type TEXT DEFAULT 'normal',
                description TEXT DEFAULT '',
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS titles (
                title_name TEXT UNIQUE NOT NULL,
                price INTEGER DEFAULT 500,
                is_vip BOOLEAN DEFAULT FALSE
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_titles (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                title_name TEXT NOT NULL,
                PRIMARY KEY(group_id, user_id, title_name)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_inventory (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                item_name TEXT NOT NULL,
                item_type TEXT NOT NULL DEFAULT 'normal',
                quantity INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id, user_id, item_name)
            )
        """)
        c.execute("ALTER TABLE user_inventory ADD COLUMN IF NOT EXISTS item_type TEXT NOT NULL DEFAULT 'normal'")
        c.execute("ALTER TABLE user_inventory ADD COLUMN IF NOT EXISTS quantity INTEGER NOT NULL DEFAULT 0")
        c.execute("ALTER TABLE user_inventory ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        c.execute("ALTER TABLE shop_items ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
        # 舊版相容：有些舊程式或資料庫可能還有 available_titles
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_title_sources (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                title_name TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '未知',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id, user_id, title_name, source)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS title_logs (
                id SERIAL PRIMARY KEY,
                group_id TEXT NOT NULL,
                target_user_id TEXT NOT NULL,
                actor_user_id TEXT DEFAULT '',
                title_name TEXT NOT NULL,
                action TEXT NOT NULL,
                source TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS available_titles (
                title_name TEXT UNIQUE NOT NULL
            )
        """)
        conn.commit()
    conn.close()


def ensure_default_data():
    ensure_shop_tables()
    ensure_commerce_tables()
    conn = get_connection()
    with conn.cursor() as c:
        for name, category, price, item_type, desc in DEFAULT_SHOP_ITEMS:
            c.execute("""
                INSERT INTO shop_items(name, category, price, item_type, description)
                VALUES(%s, %s, %s, %s, %s)
                ON CONFLICT(name)
                DO UPDATE SET category=EXCLUDED.category,
                              price=EXCLUDED.price,
                              item_type=EXCLUDED.item_type,
                              description=EXCLUDED.description
            """, (name, category, price, item_type, desc))

        for title_name, price, is_vip in DEFAULT_TITLES + DEFAULT_VIP_TITLES:
            c.execute("""
                INSERT INTO titles(title_name, price, is_vip)
                VALUES(%s, %s, %s)
                ON CONFLICT(title_name)
                DO UPDATE SET price=EXCLUDED.price,
                              is_vip=EXCLUDED.is_vip
            """, (title_name, price, is_vip))
            c.execute("""
                INSERT INTO available_titles(title_name)
                VALUES(%s)
                ON CONFLICT(title_name) DO NOTHING
            """, (title_name,))
        conn.commit()
    conn.close()


def normalize_keyword(text):
    return text.strip().replace("　", " ")


def _find_title_row(c, keyword):
    key = normalize_keyword(keyword)
    c.execute("SELECT title_name, price, is_vip FROM titles WHERE title_name=%s", (key,))
    row = c.fetchone()
    if row:
        return row, None

    c.execute("""
        SELECT title_name, price, is_vip
        FROM titles
        WHERE title_name LIKE %s
        ORDER BY is_vip ASC, price ASC, title_name ASC
        LIMIT 5
    """, (f"%{key}%",))
    rows = c.fetchall()
    if not rows:
        return None, f"❌ 找不到稱號：{keyword}"
    if len(rows) > 1:
        names = "、".join([r["title_name"] for r in rows])
        return None, f"⚠️ 找到多個稱號：{names}\n請輸入更完整的名稱。"
    return rows[0], None


def _find_shop_item_row(c, keyword):
    key = normalize_keyword(keyword)
    c.execute("SELECT name, category, price, item_type, description, is_active FROM shop_items WHERE name=%s", (key,))
    row = c.fetchone()
    if row:
        return row, None

    c.execute("""
        SELECT name, category, price, item_type, description, is_active
        FROM shop_items
        WHERE name LIKE %s
        ORDER BY category ASC, price ASC, name ASC
        LIMIT 5
    """, (f"%{key}%",))
    rows = c.fetchall()
    if not rows:
        return None, f"❌ 找不到商品：{keyword}"
    if len(rows) > 1:
        names = "、".join([r["name"] for r in rows])
        return None, f"⚠️ 找到多個商品：{names}\n請輸入更完整的名稱。"
    return rows[0], None


def list_shop_items(category=None, active_only=False):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        where = []
        params = []
        if category:
            where.append("category=%s")
            params.append(category)
        if active_only:
            where.append("COALESCE(is_active, TRUE)=TRUE")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        c.execute(f"""
            SELECT name, category, price, item_type, description, COALESCE(is_active, TRUE) AS is_active
            FROM shop_items{clause}
            ORDER BY category ASC, price ASC, name ASC
        """, tuple(params))
        rows = c.fetchall()
    conn.close()
    return rows


def format_shop_home():
    ensure_default_data()
    return (
        "🛍️━━━━ 彩虹商店 V2.0 ━━━━\n\n"
        "💎【VIP】\n"
        "輸入：查看 VIP\n\n"
        "🏷️【稱號】\n"
        "輸入：查看 稱號 或 稱號商店\n\n"
        "🎁【道具】\n"
        "輸入：查看 道具\n\n"
        "🎫【活動】\n"
        "輸入：查看 活動\n\n"
        "━━━━━━━━━━━━\n"
        "購買商品：購買 商品名稱\n"
        "購買稱號：挑選稱號 稱號名稱"
    )


def format_shop_category(category, group_id=None):
    category_map = {
        "VIP": "💎 VIP 商店",
        "稱號": "🏷️ 稱號專區",
        "道具": "🎁 道具商店",
        "活動": "🎫 活動商店",
        "其他": "🌈 其他商品",
    }
    rows = list_shop_items(category, active_only=True)
    if category == "VIP" and group_id:
        vip_settings = get_vip_settings(group_id)
        if not vip_settings["shop_enabled"]:
            return "💎 VIP 商店\n\n⛔ 目前暫停開放。"
        adjusted = []
        for row in rows:
            item = dict(row)
            item["price"] = effective_vip_price(group_id, item.get("item_type"), item.get("price"))
            adjusted.append(item)
        rows = adjusted
    title = category_map.get(category, f"🛍️ {category} 商店")
    msg = f"{title}\n\n"
    if not rows:
        msg += "目前沒有商品。"
        return msg
    for i, r in enumerate(rows, start=1):
        msg += f"{i}. {r['name']}\n💰 {r['price']} 彩虹幣"
        if r.get("description"):
            msg += f"\n📌 {r['description']}"
        msg += "\n\n"
    msg += "━━━━━━━━━━━━\n輸入：購買 商品名稱"
    return msg


def format_shop_management():
    rows = list_shop_items()
    msg = "📦 商品管理\n\n"
    if not rows:
        return msg + "目前沒有商品。"
    for i, r in enumerate(rows, start=1):
        status='上架' if bool(r.get('is_active', True)) else '下架'
        msg += f"{i}.【{r['category']}】{r['name']}｜{r['price']} 彩虹幣｜{status}\n"
    msg += "\n新增商品 名稱 價格 分類\n刪除商品 名稱\n修改商品價格 名稱 價格"
    return msg


def get_shop_item(keyword):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        row, error = _find_shop_item_row(c, keyword)
    conn.close()
    return row, error


def add_shop_item(name, price, category="其他", item_type="normal", description=""):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO shop_items(name, category, price, item_type, description, is_active)
            VALUES(%s, %s, %s, %s, %s, TRUE)
            ON CONFLICT(name)
            DO UPDATE SET category=EXCLUDED.category,
                          price=EXCLUDED.price,
                          item_type=EXCLUDED.item_type,
                          description=EXCLUDED.description,
                          is_active=TRUE
        """, (name, category, price, item_type, description))
        conn.commit()
    conn.close()
    return f"✅ 商品已新增/更新：{name}｜{price} 彩虹幣｜分類：{category}"


def delete_shop_item(name):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        row, error = _find_shop_item_row(c, name)
        if error:
            conn.close()
            return error
        c.execute("DELETE FROM shop_items WHERE name=%s", (row["name"],))
        conn.commit()
    conn.close()
    return f"🗑️ 已刪除商品：{row['name']}"


def update_shop_item_price(name, price):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        row, error = _find_shop_item_row(c, name)
        if error:
            conn.close()
            return error
        c.execute("UPDATE shop_items SET price=%s WHERE name=%s", (price, row["name"]))
        conn.commit()
    conn.close()
    return f"✅ 已修改商品價格：{row['name']}｜{price} 彩虹幣"


def update_shop_item(name, price=None, category=None, description=None):
    ensure_default_data()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            row, error = _find_shop_item_row(c, name)
            if error:
                return error
            fields=[]; values=[]
            if price is not None:
                fields.append("price=%s"); values.append(int(price))
            if category is not None:
                fields.append("category=%s"); values.append(category)
            if description is not None:
                fields.append("description=%s"); values.append(description)
            if not fields:
                return "❌ 沒有可修改的內容。"
            values.append(row["name"])
            c.execute("UPDATE shop_items SET "+", ".join(fields)+" WHERE name=%s", tuple(values))
        conn.commit()
        return f"✅ 商品已修改：{row['name']}"
    finally:
        conn.close()

def set_shop_item_active(name, active):
    ensure_default_data()
    conn=get_connection()
    try:
        with conn.cursor() as c:
            row,error=_find_shop_item_row(c,name)
            if error: return error
            c.execute("UPDATE shop_items SET is_active=%s WHERE name=%s", (bool(active), row['name']))
        conn.commit()
        return f"{'✅ 已上架' if active else '⏸️ 已下架'}商品：{row['name']}"
    finally:
        conn.close()


def _vip_is_permanent(value):
    raw = str(value or "").strip()
    upper = raw.upper()
    return upper in {"PERMANENT", "FOREVER", "永久", "永久VIP"} or raw[:10] in {"9999-12-31", "9999-12-30"}


def _vip_date(value):
    raw = str(value or "").strip()
    if not raw or _vip_is_permanent(raw):
        return None
    try:
        return datetime.date.fromisoformat(raw[:10])
    except Exception:
        return None


def _taipei_today():
    return datetime.datetime.now(ZoneInfo("Asia/Taipei")).date()


def _apply_vip_purchase_in_transaction(c, group_id, user_id, item_type):
    """在同一筆購買交易內啟用或延長 VIP，避免扣款成功但 VIP 開通失敗。"""
    c.execute("""
        SELECT COALESCE(is_vip,0) AS is_vip, COALESCE(vip_until,'') AS vip_until
        FROM players
        WHERE group_id=%s AND user_id=%s
        FOR UPDATE
    """, (group_id, user_id))
    row = c.fetchone() or {"is_vip": 0, "vip_until": ""}
    old_until = str(row.get("vip_until") or "").strip()

    if _vip_is_permanent(old_until):
        raise ValueError("PERMANENT_VIP_ALREADY_OWNED")

    if item_type == "vip_forever":
        new_until = "PERMANENT"
        days = None
    elif item_type == "vip_7":
        days = 7
    elif item_type == "vip_30":
        days = 30
    else:
        return old_until, old_until

    if item_type != "vip_forever":
        today = _taipei_today()
        old_date = _vip_date(old_until)
        base = old_date if old_date and old_date >= today else today
        new_until = (base + datetime.timedelta(days=days)).isoformat()

    c.execute("""
        UPDATE players
        SET is_vip=1, vip_until=%s
        WHERE group_id=%s AND user_id=%s
    """, (new_until, group_id, user_id))

    # VIP 紀錄表存在時同步寫入；不存在不影響核心購買交易。
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
        INSERT INTO vip_logs_v231(group_id,target_user_id,actor_user_id,action,days_text,vip_until)
        VALUES(%s,%s,%s,'purchase',%s,%s)
    """, (group_id, user_id, user_id, "永久" if days is None else f"{days}天", new_until))
    return old_until, new_until


def _grant_shop_item_in_transaction(c, group_id, user_id, item):
    """在同一筆購買交易中發放商品，避免扣款成功但商品未入帳。"""
    item_name = str(item.get("name") or "").strip()
    item_type = str(item.get("item_type") or "normal").strip().lower()
    category = str(item.get("category") or "其他").strip()

    if item_type == "title" or category == "稱號":
        price = max(0, int(item.get("price") or 0))
        c.execute("""
            INSERT INTO titles(title_name, price, is_vip)
            VALUES(%s, %s, FALSE)
            ON CONFLICT(title_name)
            DO UPDATE SET price=EXCLUDED.price, is_vip=FALSE
        """, (item_name, price))
        c.execute("""
            INSERT INTO available_titles(title_name)
            VALUES(%s) ON CONFLICT(title_name) DO NOTHING
        """, (item_name,))
        c.execute("""
            INSERT INTO user_titles(group_id, user_id, title_name)
            VALUES(%s, %s, %s)
            ON CONFLICT(group_id, user_id, title_name) DO NOTHING
        """, (group_id, user_id, item_name))
        c.execute("""
            INSERT INTO user_title_sources(group_id, user_id, title_name, source)
            VALUES(%s, %s, %s, '商店購買')
            ON CONFLICT(group_id, user_id, title_name, source) DO NOTHING
        """, (group_id, user_id, item_name))
        c.execute("""
            UPDATE players SET custom_title=%s
            WHERE group_id=%s AND user_id=%s
        """, (item_name, group_id, user_id))
        return {"grant_type": "title", "quantity": 1, "detail": f"🏷️ 已取得並裝備稱號：{item_name}"}

    # 一般商品、道具、特效及自訂類型都存入同一份持有清單。
    c.execute("""
        INSERT INTO user_inventory(group_id, user_id, item_name, item_type, quantity, updated_at)
        VALUES(%s, %s, %s, %s, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(group_id, user_id, item_name)
        DO UPDATE SET quantity=user_inventory.quantity+1,
                      item_type=EXCLUDED.item_type,
                      updated_at=CURRENT_TIMESTAMP
        RETURNING quantity
    """, (group_id, user_id, item_name, item_type or "normal"))
    row = c.fetchone() or {}
    quantity = int(row.get("quantity") or 1)
    label = "特效" if item_type == "effect" or category == "特效" else "商品"
    return {"grant_type": item_type or "normal", "quantity": quantity,
            "detail": f"🎁 {label}已入帳：{item_name}（持有 {quantity}）"}


def get_user_inventory(group_id, user_id):
    """供後台與前台共用的持有商品清單。"""
    ensure_default_data()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT item_name, item_type, quantity, updated_at
                FROM user_inventory
                WHERE group_id=%s AND user_id=%s AND quantity>0
                ORDER BY updated_at DESC, item_name ASC
            """, (group_id, user_id))
            return c.fetchall()
    finally:
        conn.close()


def buy_shop_item(group_id, user_id, item_name):
    ensure_default_data()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            item, error = _find_shop_item_row(c, item_name)
            if error:
                return False, error, None
            item = dict(item)
            if not bool(item.get('is_active', True)):
                return False, f"❌ 商品目前已下架：{item['name']}", item
            item_type = str(item.get("item_type") or "normal")
            if item_type in {"vip_7", "vip_30", "vip_forever"}:
                vip_settings = get_vip_settings(group_id)
                if not vip_settings["shop_enabled"]:
                    return False, "⛔ 此群組目前暫停開放 VIP 商店。", item
                price = effective_vip_price(group_id, item_type, item.get("price"))
                item["price"] = price
            else:
                price = int(item["price"] or 0)

            # 先鎖定玩家，VIP 商品可在扣款前阻擋永久 VIP 重複購買。
            c.execute("""
                SELECT COALESCE(coins,0) AS coins, COALESCE(vip_until,'') AS vip_until
                FROM players
                WHERE group_id=%s AND user_id=%s
                FOR UPDATE
            """, (group_id, user_id))
            player = c.fetchone()
            if not player:
                return False, "❌ 找不到玩家資料，請先在群組使用一次機器人功能。", item

            if item_type in {"vip_7", "vip_30", "vip_forever"} and _vip_is_permanent(player.get("vip_until")):
                return False, "❌ 你已擁有永久 VIP，無法再購買任何 VIP 方案。", item

            balance = int(player.get("coins") or 0)
            if balance < price:
                return False, (f"❌ 彩虹幣不足\n\n需要：{price:,}\n目前：{balance:,}\n"
                               f"還差：{max(price-balance,0):,}"), item

            c.execute("""
                UPDATE players
                SET coins = coins - %s
                WHERE group_id=%s AND user_id=%s
            """, (price, group_id, user_id))

            old_until = new_until = ""
            grant_result = None
            if item_type in {"vip_7", "vip_30", "vip_forever"}:
                old_until, new_until = _apply_vip_purchase_in_transaction(
                    c, group_id, user_id, item_type
                )
                item["old_vip_until"] = old_until
                item["new_vip_until"] = new_until
            else:
                grant_result = _grant_shop_item_in_transaction(c, group_id, user_id, item)
                item["grant_result"] = grant_result

            purchase_id = record_purchase_in_transaction(
                c, group_id, user_id, item.get("category") or "其他", item["name"], price
            )
            conn.commit()

        notices = consume_pending_vault_announcements(group_id)
        extra = ("\n\n" + "\n\n".join(notices)) if notices else ""
        msg = (f"✅ 購買成功：{item['name']}\n"
               f"🌈 彩虹幣 -{price:,}\n"
               f"🏦 金庫 +{price:,}\n"
               f"🔖 交易編號：#{transaction_code(purchase_id)}")
        if item_type == "vip_forever":
            msg += "\n\n💎 VIP：永久 VIP ♾️"
        elif item_type in {"vip_7", "vip_30"}:
            old_display = old_until if old_until else "尚未開通"
            msg += (f"\n\n📅 原到期日：{old_display}"
                    f"\n📅 新到期日：{new_until}")
        elif item.get("grant_result"):
            msg += "\n\n" + str(item["grant_result"].get("detail") or "🎁 商品已入帳")
        return True, msg + extra, item
    except ValueError as e:
        conn.rollback()
        if str(e) == "PERMANENT_VIP_ALREADY_OWNED":
            return False, "❌ 你已擁有永久 VIP，無法再購買任何 VIP 方案。", None
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def list_titles(include_vip=False):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        if include_vip:
            c.execute("SELECT title_name, price, is_vip FROM titles ORDER BY is_vip ASC, price ASC, title_name ASC")
        else:
            c.execute("SELECT title_name, price, is_vip FROM titles WHERE is_vip=FALSE ORDER BY price ASC, title_name ASC")
        rows = c.fetchall()
    conn.close()
    return rows


def format_title_shop(is_vip_member=False):
    rows = list_titles(include_vip=False)
    msg = "🏷️━━━━ 神魔稱號商店 ━━━━🏷️\n\n"
    for r in rows:
        msg += f"{r['title_name']}\n💰 {r['price']} 彩虹幣\n\n"
    msg += "━━━━━━━━━━━━\n輸入：挑選稱號 名稱\nVIP稱號請輸入：VIP稱號"
    return msg


def format_vip_title_shop(is_vip_member=False):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("SELECT title_name FROM titles WHERE is_vip=TRUE ORDER BY title_name ASC")
        rows = c.fetchall()
    conn.close()
    msg = "💎━━━━ VIP 專屬稱號 ━━━━💎\n\n"
    for r in rows:
        status = "可免費使用" if is_vip_member else "VIP限定"
        msg += f"{r['title_name']}｜{status}\n"
    msg += "\n輸入：裝備稱號 名稱"
    return msg


def format_title_management(include_vip=False):
    rows = list_titles(include_vip=True)
    msg = "🏷️ 稱號管理\n\n"
    for r in rows:
        tag = "💎VIP" if r["is_vip"] else "一般"
        msg += f"【{tag}】{r['title_name']}｜{r['price']} 彩虹幣\n"
    msg += "\n新增稱號 名稱 價格\n新增VIP稱號 名稱\n刪除稱號 名稱\n修改稱號價格 名稱 價格"
    return msg


def format_vip_title_management():
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("SELECT title_name FROM titles WHERE is_vip=TRUE ORDER BY title_name ASC")
        rows = c.fetchall()
    conn.close()
    msg = "💎 VIP稱號管理\n\n"
    if not rows:
        return msg + "目前沒有 VIP 稱號。"
    for i, r in enumerate(rows, start=1):
        msg += f"{i}. {r['title_name']}\n"
    msg += "\n新增VIP稱號 名稱\n刪除VIP稱號 名稱"
    return msg


def buy_title(group_id, user_id, title_name, is_vip_member=False):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        item, error = _find_title_row(c, title_name)
        if error:
            conn.close()
            return False, error

        real_title = item["title_name"]
        price = item["price"] or 0
        vip_only = bool(item["is_vip"])

        if vip_only and not is_vip_member:
            conn.close()
            return False, "❌ 這是 VIP 專屬稱號，請先開通 VIP。"

        c.execute("""
            SELECT 1 FROM user_titles
            WHERE group_id=%s AND user_id=%s AND title_name=%s
        """, (group_id, user_id, real_title))
        owned = c.fetchone() is not None

        if not owned and not vip_only:
            c.execute("""
                UPDATE players
                SET coins = coins - %s
                WHERE group_id=%s AND user_id=%s AND coins >= %s
            """, (price, group_id, user_id, price))
            if c.rowcount == 0:
                conn.rollback()
                return False, f"❌ 彩虹幣不足，需要 {price} 彩虹幣。"
            purchase_id = record_purchase_in_transaction(c, group_id, user_id, "稱號", real_title, price)
        else:
            purchase_id = None

        c.execute("""
            INSERT INTO user_titles(group_id, user_id, title_name)
            VALUES(%s, %s, %s)
            ON CONFLICT(group_id, user_id, title_name) DO NOTHING
        """, (group_id, user_id, real_title))
        c.execute("""
            INSERT INTO user_title_sources(group_id, user_id, title_name, source)
            VALUES(%s, %s, %s, %s)
            ON CONFLICT(group_id, user_id, title_name, source) DO NOTHING
        """, (group_id, user_id, real_title, 'VIP稱號' if vip_only else '商店購買'))
        c.execute("""
            UPDATE players
            SET custom_title=%s
            WHERE group_id=%s AND user_id=%s
        """, (real_title, group_id, user_id))
        conn.commit()
    conn.close()

    if owned:
        return True, f"✅ 已裝備稱號：{real_title}"
    if vip_only:
        return True, f"💎 已裝備 VIP 稱號：{real_title}"
    notices = consume_pending_vault_announcements(group_id)
    extra = ("\n\n" + "\n\n".join(notices)) if notices else ""
    return True, f"🎉 恭喜成功購買！\n\n🏷️ 新稱號：\n{real_title}\n\n🌈 彩虹幣 -{price:,}\n🏦 金庫 +{price:,}\n🔖 交易編號：#{transaction_code(purchase_id)}" + extra


def equip_title(group_id, user_id, title_name, is_vip_member=False):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        item, error = _find_title_row(c, title_name)
        if error:
            conn.close()
            return False, error

        real_title = item["title_name"]
        vip_only = bool(item["is_vip"])
        if vip_only and not is_vip_member:
            conn.close()
            return False, "❌ 這是 VIP 專屬稱號，請先開通 VIP。"

        c.execute("""
            SELECT 1 FROM user_titles
            WHERE group_id=%s AND user_id=%s AND title_name=%s
        """, (group_id, user_id, real_title))
        owned = c.fetchone() is not None
        if not owned and not vip_only:
            conn.close()
            return False, "❌ 你尚未擁有這個稱號，請先購買。"

        c.execute("""
            INSERT INTO user_titles(group_id, user_id, title_name)
            VALUES(%s, %s, %s)
            ON CONFLICT(group_id, user_id, title_name) DO NOTHING
        """, (group_id, user_id, real_title))
        c.execute("UPDATE players SET custom_title=%s WHERE group_id=%s AND user_id=%s", (real_title, group_id, user_id))
        conn.commit()
    conn.close()
    return True, f"✅ 已裝備稱號：{real_title}"


def my_titles_message(group_id, user_id):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("SELECT custom_title FROM players WHERE group_id=%s AND user_id=%s", (group_id, user_id))
        player = c.fetchone()
        c.execute("""
            SELECT title_name
            FROM user_titles
            WHERE group_id=%s AND user_id=%s
            ORDER BY title_name ASC
        """, (group_id, user_id))
        rows = c.fetchall()
    conn.close()
    current = player["custom_title"] if player and player.get("custom_title") else "無"
    msg = f"🏷️ 我的稱號\n\n目前裝備：{current}\n\n已擁有：\n"
    if not rows:
        msg += "尚未擁有稱號。"
    else:
        for r in rows:
            msg += f"・{r['title_name']}\n"
    msg += "\n輸入：裝備稱號 名稱"
    return msg


def add_title(title_name, price=500, is_vip=False):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO titles(title_name, price, is_vip)
            VALUES(%s, %s, %s)
            ON CONFLICT(title_name)
            DO UPDATE SET price=EXCLUDED.price, is_vip=EXCLUDED.is_vip
        """, (title_name, price, is_vip))
        c.execute("INSERT INTO available_titles(title_name) VALUES(%s) ON CONFLICT(title_name) DO NOTHING", (title_name,))
        conn.commit()
    conn.close()
    tag = "VIP稱號" if is_vip else "稱號"
    return f"✅ 已新增/更新{tag}：{title_name}｜{price} 彩虹幣"


def delete_title(title_name):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        item, error = _find_title_row(c, title_name)
        if error:
            conn.close()
            return error
        real_title = item["title_name"]
        c.execute("DELETE FROM titles WHERE title_name=%s", (real_title,))
        c.execute("DELETE FROM user_titles WHERE title_name=%s", (real_title,))
        c.execute("UPDATE players SET custom_title='' WHERE custom_title=%s", (real_title,))
        conn.commit()
    conn.close()
    return f"🗑️ 已刪除稱號：{real_title}"


def update_title_price(title_name, price):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        item, error = _find_title_row(c, title_name)
        if error:
            conn.close()
            return error
        c.execute("UPDATE titles SET price=%s WHERE title_name=%s", (price, item["title_name"]))
        conn.commit()
    conn.close()
    return f"✅ 已修改稱號價格：{item['title_name']}｜{price} 彩虹幣"


# ===== V2.2.9 稱號授予 / 回收 / 紀錄 =====
def grant_title_to_user(group_id, user_id, title_name, actor_user_id="", source="群長授予"):
    ensure_default_data()
    title_name = normalize_keyword(title_name)
    if not title_name:
        return False, "❌ 稱號名稱不可空白。"

    conn = get_connection()
    with conn.cursor() as c:
        item, error = _find_title_row(c, title_name)
        if error:
            c.execute("""
                INSERT INTO titles(title_name, price, is_vip)
                VALUES(%s, 0, FALSE)
                ON CONFLICT(title_name) DO NOTHING
            """, (title_name,))
            c.execute("INSERT INTO available_titles(title_name) VALUES(%s) ON CONFLICT(title_name) DO NOTHING", (title_name,))
            real_title = title_name
        else:
            real_title = item["title_name"]

        c.execute("""
            INSERT INTO user_titles(group_id, user_id, title_name)
            VALUES(%s, %s, %s)
            ON CONFLICT(group_id, user_id, title_name) DO NOTHING
        """, (group_id, user_id, real_title))
        c.execute("""
            INSERT INTO user_title_sources(group_id, user_id, title_name, source)
            VALUES(%s, %s, %s, %s)
            ON CONFLICT(group_id, user_id, title_name, source) DO NOTHING
        """, (group_id, user_id, real_title, source))
        c.execute("""
            INSERT INTO title_logs(group_id, target_user_id, actor_user_id, title_name, action, source)
            VALUES(%s, %s, %s, %s, 'grant', %s)
        """, (group_id, user_id, actor_user_id, real_title, source))
        conn.commit()
    conn.close()
    return True, f"✅ 已給予稱號：{real_title}"


def revoke_title_from_user(group_id, user_id, title_name, actor_user_id="", source="群長授予"):
    ensure_default_data()
    conn = get_connection()
    with conn.cursor() as c:
        item, error = _find_title_row(c, title_name)
        if error:
            conn.close()
            return False, error
        real_title = item["title_name"]

        c.execute("""
            DELETE FROM user_title_sources
            WHERE group_id=%s AND user_id=%s AND title_name=%s AND source=%s
        """, (group_id, user_id, real_title, source))
        removed = c.rowcount

        c.execute("""
            SELECT 1 FROM user_title_sources
            WHERE group_id=%s AND user_id=%s AND title_name=%s
            LIMIT 1
        """, (group_id, user_id, real_title))
        has_other_source = c.fetchone() is not None

        if removed > 0 and not has_other_source:
            c.execute("""
                DELETE FROM user_titles
                WHERE group_id=%s AND user_id=%s AND title_name=%s
            """, (group_id, user_id, real_title))
            c.execute("""
                UPDATE players
                SET custom_title=''
                WHERE group_id=%s AND user_id=%s AND custom_title=%s
            """, (group_id, user_id, real_title))

        c.execute("""
            INSERT INTO title_logs(group_id, target_user_id, actor_user_id, title_name, action, source)
            VALUES(%s, %s, %s, %s, 'revoke', %s)
        """, (group_id, user_id, actor_user_id, real_title, source))
        conn.commit()
    conn.close()

    if removed == 0:
        return False, f"⚠️ 找不到可收回的群長授予稱號：{real_title}"
    return True, f"✅ 已收回群長授予稱號：{real_title}"


def unequip_title(group_id, user_id):
    ensure_shop_tables()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("UPDATE players SET custom_title='' WHERE group_id=%s AND user_id=%s", (group_id, user_id))
        conn.commit()
    conn.close()
    return "✅ 已卸下目前稱號。"


def title_record_message(group_id, user_id, display_name="玩家"):
    ensure_shop_tables()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT title_name, action, source, created_at
            FROM title_logs
            WHERE group_id=%s AND target_user_id=%s
            ORDER BY created_at DESC
            LIMIT 20
        """, (group_id, user_id))
        rows = c.fetchall()
    conn.close()
    msg = f"📜 稱號紀錄｜{display_name}\n\n"
    if not rows:
        return msg + "目前沒有稱號紀錄。"
    for r in rows:
        action = "給予" if r["action"] == "grant" else "收回"
        msg += f"・{r['created_at']}｜{action}｜{r['title_name']}｜{r['source']}\n"
    return msg
