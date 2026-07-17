from database import get_connection
from commerce import format_purchase_details, format_member_purchase_details, format_vault, format_vault_history


def ensure_control_center_tables():
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS managed_groups (
                    group_id TEXT PRIMARY KEY,
                    group_name TEXT NOT NULL DEFAULT '未命名群組',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    user_id TEXT PRIMARY KEY,
                    selected_group_id TEXT DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
        conn.commit()
    finally:
        conn.close()


def register_group(group_id, group_name):
    if not group_id or group_id == "PRIVATE":
        return
    ensure_control_center_tables()
    clean_name = (group_name or "未命名群組").strip() or "未命名群組"
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO managed_groups(group_id, group_name, updated_at)
                VALUES(%s,%s,CURRENT_TIMESTAMP)
                ON CONFLICT(group_id) DO UPDATE
                SET group_name=EXCLUDED.group_name, updated_at=CURRENT_TIMESTAMP
            """, (group_id, clean_name))
        conn.commit()
    finally:
        conn.close()


def list_admin_groups(user_id):
    ensure_control_center_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT a.group_id, COALESCE(g.group_name, '未命名群組') AS group_name, a.role,
                       (SELECT COUNT(*) FROM players p WHERE p.group_id=a.group_id) AS member_count
                FROM admins a
                LEFT JOIN managed_groups g ON g.group_id=a.group_id
                WHERE a.user_id=%s
                ORDER BY COALESCE(g.group_name, a.group_id)
            """, (user_id,))
            return c.fetchall()
    finally:
        conn.close()


def get_selected_group(user_id):
    ensure_control_center_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT selected_group_id FROM admin_sessions WHERE user_id=%s", (user_id,))
            row = c.fetchone()
            selected = (row or {}).get("selected_group_id") or ""
            if selected:
                c.execute("SELECT 1 FROM admins WHERE user_id=%s AND group_id=%s", (user_id, selected))
                if c.fetchone():
                    return selected
            c.execute("SELECT group_id FROM admins WHERE user_id=%s ORDER BY group_id LIMIT 1", (user_id,))
            first = c.fetchone()
            return (first or {}).get("group_id") or ""
    finally:
        conn.close()


def set_selected_group(user_id, group_id):
    ensure_control_center_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM admins WHERE user_id=%s AND group_id=%s", (user_id, group_id))
            if not c.fetchone():
                return False
            c.execute("""
                INSERT INTO admin_sessions(user_id, selected_group_id, updated_at)
                VALUES(%s,%s,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE
                SET selected_group_id=EXCLUDED.selected_group_id, updated_at=CURRENT_TIMESTAMP
            """, (user_id, group_id))
        conn.commit()
        return True
    finally:
        conn.close()


def group_name(group_id):
    ensure_control_center_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT group_name FROM managed_groups WHERE group_id=%s", (group_id,))
            row = c.fetchone()
            return (row or {}).get("group_name") or "未命名群組"
    finally:
        conn.close()


def group_list_message(user_id):
    rows = list_admin_groups(user_id)
    if not rows:
        return "🔒 尚未找到你管理的群組。\n\n請先在群組內輸入：\n綁定 群長 你的名稱"
    selected = get_selected_group(user_id)
    lines = ["🌈 你管理的群組", ""]
    for i, row in enumerate(rows, 1):
        mark = "✅" if row["group_id"] == selected else "▫️"
        lines.append(f"{mark} {i}. {row['group_name']}")
        lines.append(f"   👥 已建檔成員：{int(row['member_count'] or 0)} 人")
    lines += ["", "輸入：!切換群組 編號", "例如：!切換群組 2"]
    return "\n".join(lines)


def switch_group_by_index(user_id, index):
    rows = list_admin_groups(user_id)
    if index < 1 or index > len(rows):
        return False, "❌ 群組編號不存在。\n\n" + group_list_message(user_id)
    row = rows[index - 1]
    set_selected_group(user_id, row["group_id"])
    return True, f"✅ 已切換管理群組\n\n📂 {row['group_name']}"


def control_center_message(user_id):
    selected = get_selected_group(user_id)
    if not selected:
        return group_list_message(user_id)
    return (
        "🌈 Rainbow Life 管理中心\n\n"
        f"📂 目前群組：{group_name(selected)}\n\n"
        "📊 !群組總覽\n👥 !成員管理\n🔍 !搜尋成員 名稱\n"
        "💎 !VIP管理／!VIP會員\n🛍️ !商店管理／!商品列表\n"
        "📅 !簽到設定\n📜 !今日消費／!昨日消費／!總消費\n"
        "🏦 !金庫／!金庫紀錄\n⚙️ !群組設定\n\n"
        "🔄 !群組列表／!切換群組 1"
    )


def group_overview_message(group_id):
    from commerce import ensure_commerce_tables
    ensure_commerce_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT COUNT(*) AS members,
                       COALESCE(SUM(today_msg_count),0) AS messages,
                       COALESCE(SUM(today_sticker_count),0) AS stickers,
                       COUNT(*) FILTER (WHERE COALESCE(is_vip,0)=1) AS vip_count,
                       COUNT(*) FILTER (WHERE last_sign_in <> '') AS signed_members
                FROM players WHERE group_id=%s
            """, (group_id,))
            stat = c.fetchone() or {}
            c.execute("""
                SELECT COALESCE(SUM(cost),0) AS today_spend FROM purchase_history
                WHERE group_id=%s AND (created_at AT TIME ZONE 'Asia/Taipei')::date =
                (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Taipei')::date
            """, (group_id,))
            spend = c.fetchone() or {}
            c.execute("SELECT COALESCE(balance,0) AS balance FROM group_vaults WHERE group_id=%s", (group_id,))
            vault = c.fetchone() or {}
    finally:
        conn.close()
    return (
        "📊 群組總覽\n\n" f"📂 {group_name(group_id)}\n"
        f"👥 已建檔成員：{int(stat.get('members') or 0)} 人\n"
        f"💬 今日聊天：{int(stat.get('messages') or 0):,} 則\n"
        f"🖼️ 今日貼圖：{int(stat.get('stickers') or 0):,} 張\n"
        f"💎 VIP 成員：{int(stat.get('vip_count') or 0)} 人\n"
        f"📆 有簽到紀錄：{int(stat.get('signed_members') or 0)} 人\n"
        f"💸 今日消費：{int(spend.get('today_spend') or 0):,}\n"
        f"🏦 金庫：{int(vault.get('balance') or 0):,}"
    )


def _member_total_count(group_id):
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) AS n FROM players WHERE group_id=%s",(group_id,))
            return int((c.fetchone() or {}).get('n') or 0)
    finally: conn.close()


def member_management_message(group_id, limit=20):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT name, level, coins, is_vip, vip_until, streak_count, sign_month_count
                FROM players WHERE group_id=%s
                ORDER BY level DESC, exp DESC, name ASC LIMIT %s
            """, (group_id, limit))
            rows = c.fetchall()
    finally:
        conn.close()
    lines = ["👥 成員管理", "", f"📂 {group_name(group_id)}", ""]
    if not rows: lines.append("目前沒有成員資料。")
    for i, row in enumerate(rows, 1):
        vip = "💎" if int(row.get("is_vip") or 0) else ""
        lines.append(f"{i}. {vip}{row['name']}｜Lv.{int(row.get('level') or 1)}")
        lines.append(f"   🌈{int(row.get('coins') or 0):,}｜🔥{int(row.get('streak_count') or 0)}｜📆{int(row.get('sign_month_count') or 0)}")
    lines += ["", f"目前顯示前 {min(limit, len(rows))} 名／共 {_member_total_count(group_id)} 名。", "搜尋：!搜尋成員 名稱"]
    return "\n".join(lines)


def search_member_message(group_id, keyword):
    key=(keyword or '').strip()
    if not key: return "❌ 請輸入成員名稱。\n例如：!搜尋成員 佑佑"
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT name,level,exp,coins,custom_title,is_vip,vip_until,streak_count,sign_month_count,
                         today_msg_count,today_sticker_count,birthday FROM players
                         WHERE group_id=%s AND name ILIKE %s ORDER BY name LIMIT 6""",(group_id,f"%{key}%"))
            rows=c.fetchall()
    finally: conn.close()
    if not rows: return f"❌ 找不到成員：{key}"
    if len(rows)>1:
        return "🔍 找到多位相似成員\n\n"+"\n".join(f"• {r['name']}｜Lv.{int(r.get('level') or 1)}" for r in rows)+"\n\n請輸入更完整名稱。"
    r=rows[0]
    vip='未啟用'
    vu=str(r.get('vip_until') or '')
    if int(r.get('is_vip') or 0): vip='永久 ♾️' if vu.upper() in ('PERMANENT','FOREVER','永久','永久VIP') else (vu or '啟用中')
    title=str(r.get('custom_title') or '依等級稱號')
    b=str(r.get('birthday') or '未設定')
    return (f"👤 成員資料\n\n📂 {group_name(group_id)}\n\n👤 名稱：{r['name']}\n🏅 等級：Lv.{int(r.get('level') or 1)}\n"
            f"⭐ EXP：{int(r.get('exp') or 0):,}\n🌟 稱號：{title}\n💎 VIP：{vip}\n🌈 彩虹幣：{int(r.get('coins') or 0):,}\n"
            f"🔥 連續簽到：{int(r.get('streak_count') or 0)} 天\n📆 累積簽到：{int(r.get('sign_month_count') or 0)} 天\n"
            f"💬 今日聊天：{int(r.get('today_msg_count') or 0)}\n🖼️ 今日貼圖：{int(r.get('today_sticker_count') or 0)}\n🎂 生日：{b}")


def vip_management_message(group_id):
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT COUNT(*) FILTER (WHERE COALESCE(is_vip,0)=1) AS active,
                       COUNT(*) FILTER (WHERE UPPER(COALESCE(vip_until,'')) IN ('PERMANENT','FOREVER','永久','永久VIP')) AS permanent
                       FROM players WHERE group_id=%s""",(group_id,))
            row=c.fetchone() or {}
    finally: conn.close()
    return (f"💎 VIP 管理\n\n📂 {group_name(group_id)}\n💎 啟用中：{int(row.get('active') or 0)} 人\n♾️ 永久 VIP：{int(row.get('permanent') or 0)} 人\n\n"
            "目前方案\n7 天：🌈2,000\n30 天：🌈8,000\n永久：🌈20,000\n\n查看名單：!VIP會員\n\n設定：!設定VIP 成員名稱|30天\n延長：!延長VIP 成員名稱|7天\n移除：!移除VIP 成員名稱")


def vip_member_list_message(group_id, limit=30):
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT name,vip_until FROM players WHERE group_id=%s AND COALESCE(is_vip,0)=1
                         ORDER BY CASE WHEN UPPER(COALESCE(vip_until,'')) IN ('PERMANENT','FOREVER','永久','永久VIP') THEN 0 ELSE 1 END,
                         vip_until ASC,name ASC LIMIT %s""",(group_id,limit))
            rows=c.fetchall()
    finally: conn.close()
    lines=["💎 VIP 會員名單","",f"📂 {group_name(group_id)}",""]
    if not rows: lines.append("目前沒有啟用中的 VIP。")
    for i,r in enumerate(rows,1):
        vu=str(r.get('vip_until') or '')
        show='永久 ♾️' if vu.upper() in ('PERMANENT','FOREVER','永久','永久VIP') else (vu or '啟用中')
        lines.append(f"{i}. {r['name']}｜{show}")
    lines += ["",f"目前顯示 {len(rows)} 名。"]
    return "\n".join(lines)


def shop_management_message(group_id):
    from shop import ensure_default_data, list_shop_items
    ensure_default_data(); rows=list_shop_items()
    cats={'VIP':0,'稱號':0,'道具':0,'活動':0,'其他':0}
    for r in rows: cats[r.get('category') if r.get('category') in cats else '其他'] += 1
    return (f"🛍️ 商店管理\n\n📂 {group_name(group_id)}\n\n💎 VIP 商店：{cats['VIP']} 項\n🎖️ 稱號商店：{cats['稱號']} 項\n"
            f"🎁 一般／道具：{cats['道具']+cats['其他']} 項\n\n查看：!商品列表\n新增：!新增商品 名稱|價格|分類|說明\n修改：!修改商品 名稱|價格|分類|說明\n上下架：!上架商品 名稱／!下架商品 名稱\n\n🌈 活動商店由節日／季節自動替換，不可手動管理。")


def product_list_message(group_id):
    from shop import ensure_default_data, list_shop_items
    ensure_default_data(); rows=list_shop_items()
    lines=["📦 商品列表","",f"📂 {group_name(group_id)}",""]
    if not rows: lines.append('目前沒有商品。')
    for i,r in enumerate(rows,1):
        lines.append(f"{i}.【{r.get('category') or '其他'}】{r['name']}")
        status='上架中' if bool(r.get('is_active', True)) else '已下架'
        lines.append(f"   🌈{int(r.get('price') or 0):,}｜{status}｜{r.get('description') or '無說明'}")
    return "\n".join(lines)


def sign_settings_message(group_id):
    return (f"📅 簽到設定\n\n📂 {group_name(group_id)}\n\n補簽上限：7 天\n"
            "補簽價格：\n1天 100／2天 200／3天 300／4天 450\n5天 600／6天 800／7天 1,000\n\n"
            "換日時間：每日 05:00（台灣時間）")


def simple_settings_message(group_id, section):
    if section=='shop': return shop_management_message(group_id)
    if section=='sign': return sign_settings_message(group_id)
    return (f"⚙️ 群組設定\n\n📂 {group_name(group_id)}\n\n📅 簽到設定\n💎 VIP 設定\n🛍️ 商店設定\n🎖️ 稱號設定\n"
            "🎁 抽獎設定\n🎉 活動設定\n📢 公告設定\n\n設定會依群組分開保存。")


def _resolve_member_for_admin(group_id, keyword):
    key=(keyword or '').strip()
    if not key:
        return None, "❌ 請輸入成員名稱。"
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT user_id,name FROM players WHERE group_id=%s AND name=%s LIMIT 1""",(group_id,key))
            exact=c.fetchone()
            if exact: return exact, None
            c.execute("""SELECT user_id,name FROM players WHERE group_id=%s AND name ILIKE %s ORDER BY name LIMIT 6""",(group_id,f"%{key}%"))
            rows=c.fetchall()
    finally: conn.close()
    if not rows: return None, f"❌ 找不到成員：{key}"
    if len(rows)>1:
        return None, "🔍 找到多位相似成員\n\n"+"\n".join(f"• {r['name']}" for r in rows)+"\n\n請輸入更完整名稱。"
    return rows[0], None


def _parse_pipe_args(text, command, minimum=1):
    raw=text[len(command):].strip()
    parts=[p.strip() for p in raw.split('|')]
    if len(parts)<minimum or any(not p for p in parts[:minimum]):
        return None
    return parts


def private_shop_write_command(text, selected):
    from shop import add_shop_item, update_shop_item, update_shop_item_price, set_shop_item_active
    if text.startswith('新增商品'):
        parts=_parse_pipe_args(text,'新增商品',3)
        if not parts: return "格式：!新增商品 名稱|價格|分類|說明\n例如：!新增商品 彩虹煙火|500|活動|限定特效"
        try: price=int(parts[1])
        except ValueError: return "❌ 商品價格請輸入整數。"
        if price<0: return "❌ 商品價格不能小於 0。"
        desc=parts[3] if len(parts)>3 else ''
        return add_shop_item(parts[0],price,parts[2],description=desc)+f"\n📂 套用管理群組：{group_name(selected)}"
    if text.startswith('修改商品價格'):
        parts=_parse_pipe_args(text,'修改商品價格',2)
        if not parts: return "格式：!修改商品價格 商品名稱|新價格"
        try: price=int(parts[1])
        except ValueError: return "❌ 商品價格請輸入整數。"
        if price<0: return "❌ 商品價格不能小於 0。"
        return update_shop_item_price(parts[0],price)
    if text.startswith('修改商品'):
        parts=_parse_pipe_args(text,'修改商品',2)
        if not parts: return "格式：!修改商品 名稱|價格|分類|說明"
        try: price=int(parts[1])
        except ValueError: return "❌ 商品價格請輸入整數。"
        category=parts[2] if len(parts)>2 and parts[2] else None
        desc=parts[3] if len(parts)>3 else None
        return update_shop_item(parts[0],price,category,desc)
    if text.startswith('上架商品'):
        name=text[len('上架商品'):].strip()
        return set_shop_item_active(name,True) if name else "格式：!上架商品 商品名稱"
    if text.startswith('下架商品'):
        name=text[len('下架商品'):].strip()
        return set_shop_item_active(name,False) if name else "格式：!下架商品 商品名稱"
    return None


def private_vip_write_command(text, selected, actor_user_id):
    from vip import grant_vip, extend_vip, cancel_vip
    if text.startswith('設定VIP'):
        parts=_parse_pipe_args(text,'設定VIP',2)
        if not parts: return "格式：!設定VIP 成員名稱|30天\n永久 VIP：!設定VIP 成員名稱|永久"
        member,error=_resolve_member_for_admin(selected,parts[0])
        if error: return error
        ok,msg=grant_vip(selected,member['user_id'],parts[1],actor_user_id)
        return f"{msg}\n👤 成員：{member['name']}" if ok else msg
    if text.startswith('延長VIP'):
        parts=_parse_pipe_args(text,'延長VIP',2)
        if not parts: return "格式：!延長VIP 成員名稱|7天"
        member,error=_resolve_member_for_admin(selected,parts[0])
        if error: return error
        ok,msg=extend_vip(selected,member['user_id'],parts[1],actor_user_id)
        return f"{msg}\n👤 成員：{member['name']}" if ok else msg
    if text.startswith('移除VIP') or text.startswith('收回VIP'):
        cmd='移除VIP' if text.startswith('移除VIP') else '收回VIP'
        name=text[len(cmd):].strip()
        member,error=_resolve_member_for_admin(selected,name)
        if error: return error
        ok,msg=cancel_vip(selected,member['user_id'],actor_user_id)
        return f"{msg}\n👤 成員：{member['name']}" if ok else msg
    return None


def handle_private_control_command(text, user_id):
    from commerce import ensure_commerce_tables
    ensure_commerce_tables()
    if text in ["後台","管理中心","群組後台"]: return control_center_message(user_id)
    if text in ["群組列表","我的群組"]: return group_list_message(user_id)
    if text.startswith("切換群組"):
        raw=text.replace("切換群組","",1).strip()
        try: index=int(raw)
        except ValueError: return "格式：!切換群組 1\n\n"+group_list_message(user_id)
        return switch_group_by_index(user_id,index)[1]
    selected=get_selected_group(user_id)
    if text=='目前群組': return f"📂 目前管理群組\n\n{group_name(selected)}" if selected else group_list_message(user_id)
    if not selected: return group_list_message(user_id)
    shop_write=private_shop_write_command(text,selected)
    if shop_write is not None: return shop_write
    vip_write=private_vip_write_command(text,selected,user_id)
    if vip_write is not None: return vip_write
    if text=='群組總覽': return group_overview_message(selected)
    if text in ['成員管理','成員列表']: return member_management_message(selected)
    if text.startswith('搜尋成員') or text.startswith('查詢成員'):
        key=text.replace('搜尋成員','',1).replace('查詢成員','',1).strip(); return search_member_message(selected,key)
    if text in ['VIP管理','VIP 管理']: return vip_management_message(selected)
    if text in ['VIP會員','VIP名單']: return vip_member_list_message(selected)
    if text in ['商店管理','商城管理']: return shop_management_message(selected)
    if text=='商品列表': return product_list_message(selected)
    if text in ['簽到設定','補簽設定']: return sign_settings_message(selected)
    if text in ['群組設定','系統設定']: return simple_settings_message(selected,'group')
    if text=='今日消費': return format_purchase_details(selected,'today')
    if text=='昨日消費': return format_purchase_details(selected,'yesterday')
    if text=='總消費': return format_purchase_details(selected,'all')
    if text.startswith('消費紀錄'): return format_member_purchase_details(selected,text[len('消費紀錄'):].strip())
    if text=='金庫': return format_vault(selected)
    if text=='金庫紀錄': return format_vault_history(selected)
    return None


def get_member_page(group_id, page=1, page_size=15):
    page = max(int(page or 1), 1)
    page_size = max(1, min(int(page_size or 15), 20))
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) AS n FROM players WHERE group_id=%s", (group_id,))
            total = int((c.fetchone() or {}).get('n') or 0)
            total_pages = max((total + page_size - 1) // page_size, 1)
            page = min(page, total_pages)
            offset = (page - 1) * page_size
            c.execute("""
                SELECT user_id, name, streak_count
                FROM players
                WHERE group_id=%s
                ORDER BY name ASC
                LIMIT %s OFFSET %s
            """, (group_id, page_size, offset))
            members = c.fetchall()
        return members, page, total_pages, total
    finally:
        conn.close()


def get_member_by_id(group_id, user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT p.*,
                       (SELECT COUNT(*) FROM sign_records s WHERE s.group_id=p.group_id AND s.user_id=p.user_id) AS total_sign
                FROM players p
                WHERE p.group_id=%s AND p.user_id=%s
                LIMIT 1
            """, (group_id, user_id))
            return c.fetchone()
    finally:
        conn.close()
