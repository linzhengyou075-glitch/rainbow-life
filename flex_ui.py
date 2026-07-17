import threading
from linebot.models import FlexSendMessage
from urllib.parse import quote_plus
from rainbow_theme import flex_palette

_notification_context = threading.local()

def set_notification_member_name(name: str):
    _notification_context.member_name = str(name or "").strip()

def clear_notification_member_name():
    _notification_context.member_name = ""

def _notification_member_name() -> str:
    return str(getattr(_notification_context, "member_name", "") or "").strip()

def _notification_member_row():
    name = _notification_member_name()
    if not name:
        return None
    return {"type":"text","text":f"👤 成員：{name}","size":"sm","weight":"bold","color":"#5E5368","wrap":True}

from progression import level_title, rank_title, progress_info


def _message_action(label: str, text: str):
    return {
        "type": "button",
        "style": "secondary",
        "height": "sm",
        "margin": "sm",
        "action": {
            "type": "postback",
            "label": label[:20],
            "data": "cmd=" + quote_plus(text),
        },
    }



def _uri_action(label: str, uri: str):
    return {
        "type": "button",
        "style": "primary",
        "height": "sm",
        "margin": "sm",
        "action": {"type": "uri", "label": label[:20], "uri": uri},
    }

def _grid_row(left, right=None):
    contents = [left]
    if right is not None:
        contents.append(right)
    return {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "contents": contents,
    }


def _bubble(title: str, subtitle: str, rows, footer_text: str):
    p = flex_palette()
    body_contents = [
        {"type":"text","text":title,"weight":"bold","size":"xl","color":p["text"],"wrap":True},
        {"type":"text","text":subtitle,"size":"sm","color":p["sub"],"margin":"sm","wrap":True},
        {"type":"separator","margin":"lg","color":p["border"]},
    ]
    body_contents.extend(rows)
    if str(footer_text or "").strip():
        body_contents.extend([
            {"type":"separator","margin":"lg","color":p["border"]},
            {"type":"text","text":str(footer_text),"size":"xs","color":p["sub"],"margin":"md","wrap":True,"align":"center"},
        ])
    return {
        "type":"bubble","size":"mega",
        "styles":{"body":{"backgroundColor":p["card"]},"footer":{"backgroundColor":p["card"]}},
        "header":{"type":"box","layout":"vertical","backgroundColor":p["head"],"paddingAll":"lg","contents":[
            {"type":"text","text":"🌈 Rainbow Life","weight":"bold","size":"md","color":p["text"],"wrap":True},
            {"type":"text","text":p["label"],"size":"xs","color":p["sub"],"margin":"xs","wrap":True},
        ]},
        "body":{"type":"box","layout":"vertical","backgroundColor":p["card"],"paddingAll":"lg","spacing":"md","contents":body_contents},
    }

def _front_nav_rows(back_text: str = "!功能", refresh_text: str | None = None):
    """前台所有 Flex 頁面共用的 App 式導覽列。"""
    rows = [
        _grid_row(_message_action("◀ 返回", back_text), _message_action("🏠 功能中心", "!功能")),
        _grid_row(_message_action("👤 我的狀態", "!我的狀態"), _message_action("🏦 彩虹金庫", "!金庫")),
        _grid_row(_message_action("🛍️ 商店", "!商店")),
    ]
    if refresh_text:
        rows.append(_grid_row(_message_action("🔄 重新整理", refresh_text)))
    return rows


def _home_only_rows():
    """通知／結果卡片統一只顯示主頁按鍵，避免卡片過長。"""
    return [_grid_row(_message_action("🏠 主頁", "!功能"))]


def _admin_nav_rows(back_text: str = "!後台", refresh_text: str | None = None):
    """後台所有 Flex 頁面共用的 App 式導覽列。"""
    rows = [
        _grid_row(_message_action("◀ 返回", back_text), _message_action("👑 後台首頁", "!後台")),
        _grid_row(_message_action("👥 成員管理", "!成員管理"), _message_action("⚙️ 設定中心", "!設定中心")),
        _grid_row(_message_action("🏠 前台功能", "!功能"), _message_action("📊 群組總覽", "!群組總覽")),
    ]
    if refresh_text:
        rows.append(_grid_row(_message_action("🔄 重新整理", refresh_text)))
    return rows

def function_home_flex(*args, **kwargs):
    """V6.4.6 相容入口：舊呼叫不再顯示整頁功能中心。"""
    rows = [
        {"type":"text","text":"前台功能已移至個人中心網頁。","size":"sm","wrap":True},
        {"type":"text","text":"請重新輸入：個人中心","size":"sm","weight":"bold","color":"#6A35A5","margin":"sm","wrap":True},
    ]
    return FlexSendMessage(alt_text="Rainbow Life 個人中心", contents=_bubble("🌈 個人中心", "V6.4.6 網頁版入口", rows, ""))


def shop_home_flex():
    """V5.4.3：商店入口只保留綜合商店與活動商店。"""
    rows = [
        _grid_row(_message_action("🛍️ 綜合商店", "!商店 1"), _message_action("🌈 活動商店", "!活動商店")),
        _grid_row(_message_action("👤 我的狀態", "!我的狀態"), _message_action("🏦 彩虹金庫", "!金庫")),
    ]
    rows.extend(_front_nav_rows("!功能", "!商店"))
    contents = _bubble(
        "🛍️ 商店",
        "VIP、稱號、道具與一般商品已整合；活動商品仍保留獨立活動商店。",
        rows,
        "所有消費會留下紀錄並自動進入群組金庫。",
    )
    return FlexSendMessage(alt_text="Rainbow Life 商店", contents=contents)


def unified_shop_flex(shop_items, titles, page=1, page_size=8):
    """統一顯示 VIP、稱號、道具與其他商品；活動商品不在此頁。"""
    entries = []
    for item in list(shop_items or []):
        category = str(item.get("category") or "其他")
        if category == "活動":
            continue
        entries.append({
            "kind": "item",
            "name": str(item.get("name") or "商品"),
            "price": int(item.get("price") or 0),
            "category": category,
            "description": str(item.get("description") or ""),
            "item_type": str(item.get("item_type") or "normal"),
        })
    for title in list(titles or []):
        entries.append({
            "kind": "title",
            "name": str(title.get("title_name") or title.get("name") or "稱號"),
            "price": int(title.get("price") or 0),
            "category": "VIP稱號" if bool(title.get("is_vip")) else "稱號",
            "description": "VIP 專屬免費裝備" if bool(title.get("is_vip")) else "購買後立即裝備",
            "is_vip": bool(title.get("is_vip")),
        })
    order = {"VIP": 0, "稱號": 1, "VIP稱號": 2, "道具": 3, "其他": 4}
    entries.sort(key=lambda x: (order.get(x["category"], 9), x["price"], x["name"]))
    total = len(entries)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(int(page or 1), pages))
    shown = entries[(page-1)*page_size:page*page_size]
    icons = {"VIP": "💎", "稱號": "🎖️", "VIP稱號": "💎🎖️", "道具": "🎁", "其他": "🛍️"}
    rows = []
    for entry in shown:
        name = entry["name"]
        cat = entry["category"]
        price = entry["price"]
        if entry["kind"] == "title":
            action_text = f"!裝備稱號 {name}" if entry.get("is_vip") else f"!挑選稱號 {name}"
            button_label = "裝備" if entry.get("is_vip") else "選擇"
            price_text = "VIP限定" if entry.get("is_vip") else f"🌈{price:,}"
        else:
            action_text = f"!購買 {name}"
            button_label = "購買"
            price_text = f"🌈{price:,}"
        rows.append({
            "type": "box", "layout": "horizontal", "spacing": "sm", "margin": "xs",
            "contents": [
                {"type": "box", "layout": "vertical", "flex": 3, "contents": [
                    {"type": "text", "text": f"{icons.get(cat, '🛍️')} {name}", "weight": "bold", "size": "sm", "wrap": True},
                    {"type": "text", "text": f"{cat}｜{price_text}", "size": "xs", "color": "#6A35A5", "margin": "xs", "wrap": True},
                    {"type": "text", "text": entry.get("description") or "一般商品", "size": "xs", "color": "#777777", "margin": "xs", "wrap": True},
                ]},
                {"type": "button", "style": "secondary", "height": "sm", "flex": 1,
                 "action": {"type": "postback", "label": button_label, "data": "cmd=" + quote_plus(action_text)}},
            ]
        })
    if not rows:
        rows.append({"type": "text", "text": "目前沒有上架商品。", "size": "sm", "color": "#777777"})
    nav = []
    if page > 1:
        nav.append(_message_action("⬅️ 上一頁", f"!商店 {page-1}"))
    if page < pages:
        nav.append(_message_action("下一頁 ➡️", f"!商店 {page+1}"))
    if nav:
        rows.append(_grid_row(*nav))
    rows.append(_grid_row(_message_action("🌈 活動商店", "!活動商店"), _message_action("🏠 主頁", "!功能")))
    return FlexSendMessage(
        alt_text="Rainbow Life 綜合商店",
        contents=_bubble("🛍️ 綜合商店", f"VIP、稱號、道具與一般商品｜第 {page}/{pages} 頁", rows,
                         "活動限定商品請前往活動商店。"),
    )


def admin_home_flex(group_name: str):
    rows = [
        _grid_row(_message_action("👥 切換群組", "!群組列表"), _message_action("🏆 排行榜", "!排行榜")),
        _grid_row(_message_action("👤 成員管理", "!成員管理"), _message_action("🛡️ 管理員", "!管理員管理")),
        _grid_row(_message_action("💎 VIP 管理", "!VIP管理"), _message_action("🛍️ 商店管理", "!商店管理")),
        _grid_row(_message_action("📅 簽到設定", "!簽到設定"), _message_action("📜 消費明細", "!消費管理")),
        _grid_row(_message_action("🏦 金庫管理", "!金庫管理"), _message_action("⚙️ 設定中心", "!設定中心")),
        _grid_row(_message_action("🏠 前台功能", "!功能")),
        _grid_row(_message_action("🔄 重新整理", "!後台")),
    ]
    contents = _bubble(
        "👑 管理中心",
        f"目前管理群組：{group_name}\n管理功能僅能在與 Bot 的一對一聊天室操作。",
        rows,
        "直接點選按鈕管理，不需要輸入後台指令。",
    )
    return FlexSendMessage(alt_text="Rainbow Life 管理中心", contents=contents)


def admin_group_switch_flex(groups, selected_group_id: str):
    rows = []
    for index, row in enumerate(groups[:10], 1):
        selected = row.get("group_id") == selected_group_id
        label = ("✅ " if selected else "📁 ") + str(row.get("group_name") or "未命名群組")
        rows.append(_grid_row(_message_action(label, f"!切換群組 {index}")))
    rows.extend(_admin_nav_rows("!後台"))
    contents = _bubble(
        "👥 切換群組",
        "請選擇要管理的群組。每個群組的成員、商店、消費與金庫資料都獨立保存。",
        rows,
        "最多顯示前 10 個群組。",
    )
    return FlexSendMessage(alt_text="切換管理群組", contents=contents)


def admin_member_menu_flex(group_name: str):
    rows = [
        _grid_row(_message_action("📋 成員列表", "!成員列表"), _message_action("🔍 搜尋成員", "!搜尋成員說明")),
        _grid_row(_message_action("💎 VIP 會員", "!VIP會員"), _message_action("📜 消費查詢", "!消費查詢說明")),
        *_admin_nav_rows("!後台"),
    ]
    contents = _bubble("👤 成員管理", f"目前群組：{group_name}", rows, "直接從頁面選擇成員與操作項目。")
    return FlexSendMessage(alt_text="成員管理", contents=contents)


def admin_vip_menu_flex(group_name: str):
    rows = [
        _grid_row(_message_action("📋 VIP 會員", "!VIP會員"), _message_action("➕ 設定 VIP", "!設定VIP說明")),
        _grid_row(_message_action("⏳ 延長 VIP", "!延長VIP說明"), _message_action("♾️ 永久 VIP", "!永久VIP說明")),
        _grid_row(_message_action("❌ 移除 VIP", "!移除VIP說明"), _message_action("📊 VIP 概況", "!VIP概況")),
        *_admin_nav_rows("!後台"),
    ]
    contents = _bubble("💎 VIP 管理", f"目前群組：{group_name}", rows, "VIP 設定與延長會保留原有紀錄。")
    return FlexSendMessage(alt_text="VIP 管理", contents=contents)


def admin_shop_menu_flex(group_name: str):
    rows = [
        _grid_row(_message_action("📦 管理商品", "!商品管理列表"), _message_action("➕ 新增商品", "!新增商品說明")),
        _grid_row(_message_action("✏️ 修改商品", "!修改商品說明"), _message_action("💲 修改價格", "!修改價格說明")),
        _grid_row(_message_action("👁️ 上架商品", "!上架商品說明"), _message_action("🚫 下架商品", "!下架商品說明")),
        *_admin_nav_rows("!後台"),
    ]
    contents = _bubble("🛍️ 商店管理", f"目前群組：{group_name}", rows, "商品修改會立即寫入資料庫。")
    return FlexSendMessage(alt_text="商店管理", contents=contents)


def admin_consumption_menu_flex(group_name: str):
    rows = [
        _grid_row(_message_action("📅 今日消費", "!今日消費"), _message_action("🕘 昨日消費", "!昨日消費")),
        _grid_row(_message_action("📊 總消費", "!總消費"), _message_action("🔍 成員消費", "!消費查詢說明")),
        *_admin_nav_rows("!後台"),
    ]
    contents = _bubble("📜 消費明細", f"目前群組：{group_name}", rows, "消費紀錄僅限群長在私訊中查看。")
    return FlexSendMessage(alt_text="消費明細", contents=contents)


def admin_vault_menu_flex(group_name: str):
    rows = [
        _grid_row(_message_action("🏦 金庫狀態", "!金庫"), _message_action("📜 金庫紀錄", "!金庫紀錄")),
        *_admin_nav_rows("!後台"),
    ]
    contents = _bubble("🏦 金庫管理", f"目前群組：{group_name}", rows, "金庫只能查看，任何人包含群長都無法手動修改。")
    return FlexSendMessage(alt_text="金庫管理", contents=contents)


def admin_sign_menu_flex(group_name: str):
    rows = [
        _grid_row(_message_action("📋 查看簽到設定", "!查看簽到設定"), _message_action("👥 成員簽到", "!成員列表")),
        *_admin_nav_rows("!後台"),
    ]
    contents = _bubble("📅 簽到設定", f"目前群組：{group_name}\n每日 05:00（台灣時間）換日。", rows, "補簽上限與價格維持目前規則。")
    return FlexSendMessage(alt_text="簽到設定", contents=contents)



def admin_settings_center_flex(group_name: str):
    """V3.4：私訊設定中心首頁。第一小版先完成導覽與目前設定查看。"""
    rows = [
        _grid_row(_message_action("📅 簽到設定", "!簽到設定"), _message_action("💎 VIP 設定", "!VIP設定")),
        _grid_row(_message_action("🛍️ 商店設定", "!商店管理"), _message_action("🎖️ 稱號設定", "!稱號設定")),
        _grid_row(_message_action("🎁 抽獎設定", "!抽獎設定"), _message_action("🎉 活動設定", "!活動設定")),
        _grid_row(_message_action("📢 公告設定", "!公告管理"), _message_action("⚙️ 群組設定", "!群組資料設定")),
        *_admin_nav_rows("!後台"),
    ]
    contents = _bubble(
        "⚙️ 設定中心",
        f"目前群組：{group_name}\n請選擇要查看或調整的設定分類。",
        rows,
        "本小版先完成設定入口；各項修改流程會分批加入。",
    )
    return FlexSendMessage(alt_text="Rainbow Life 設定中心", contents=contents)


def admin_vip_settings_flex(group_name: str, settings=None, vip_count: int = 0, permanent_count: int = 0):
    settings = settings or {}
    price_7 = int(settings.get("price_7") or 2000)
    price_30 = int(settings.get("price_30") or 8000)
    price_forever = int(settings.get("price_forever") or 20000)
    enabled = bool(settings.get("shop_enabled", True))
    multiplier = float(settings.get("exp_multiplier") or 2)
    rows = [
        _grid_row(_message_action("💲 修改 7 天價格", "!VIP價格7說明"), _message_action("💲 修改 30 天價格", "!VIP價格30說明")),
        _grid_row(_message_action("💲 修改永久價格", "!VIP價格永久說明"), _message_action("✨ 設定 EXP 倍率", "!VIP倍率說明")),
        _grid_row(_message_action("⛔ 關閉 VIP 商店" if enabled else "✅ 開啟 VIP 商店", "!關閉VIP商店" if enabled else "!開啟VIP商店"), _message_action("📋 VIP 會員", "!VIP會員")),
        _grid_row(_message_action("💎 VIP 管理", "!VIP管理"), _message_action("🔄 重新整理", "!VIP設定")),
        _grid_row(_message_action("⬅️ 返回設定中心", "!設定中心"), _message_action("🏠 管理中心", "!後台")),
    ]
    contents = _bubble(
        "💎 VIP 設定",
        f"目前群組：{group_name}\n"
        f"商店：{'🟢 開啟' if enabled else '🔴 關閉'}\n"
        f"7 天：🌈{price_7:,}\n30 天：🌈{price_30:,}\n永久：🌈{price_forever:,}\n"
        f"VIP EXP：×{multiplier:g}\n"
        f"啟用會員：{int(vip_count)} 人｜永久：{int(permanent_count)} 人",
        rows,
        "價格與倍率修改後立即套用目前群組；VIP 時間仍可累加。",
    )
    return FlexSendMessage(alt_text="VIP 設定", contents=contents)

def admin_placeholder_settings_flex(title: str, group_name: str, description: str, back_text: str = "!設定中心"):
    rows = [
        _grid_row(_message_action("⬅️ 返回設定中心", back_text), _message_action("🏠 管理中心", "!後台")),
    ]
    contents = _bubble(title, f"目前群組：{group_name}\n{description}", rows, "此分類的可修改流程會在下一小版逐步加入。")
    return FlexSendMessage(alt_text=title, contents=contents)

def admin_shop_items_flex(group_name: str, items):
    """V3.2：私訊商店商品管理清單，點商品即可進入單品操作頁。"""
    body_rows = []
    for item in list(items)[:12]:
        name = str(item.get("name") or "未命名商品")
        category = str(item.get("category") or "其他")
        price = int(item.get("price") or 0)
        active = bool(item.get("is_active", True))
        status = "🟢 上架" if active else "🔴 下架"
        body_rows.append({
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "margin": "sm",
            "contents": [
                {
                    "type": "box",
                    "layout": "vertical",
                    "flex": 3,
                    "contents": [
                        {"type": "text", "text": name, "weight": "bold", "size": "sm", "wrap": True},
                        {"type": "text", "text": f"{category}｜🌈{price:,}｜{status}", "size": "xs", "color": "#777777", "wrap": True},
                    ],
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "flex": 1,
                    "action": {"type": "postback", "label": "管理", "data": "cmd=" + quote_plus(f"!管理商品 {name}")},
                },
            ],
        })
    if not body_rows:
        body_rows.append({"type": "text", "text": "目前沒有商品。", "size": "sm", "color": "#777777"})
    body_rows.append(_grid_row(_message_action("➕ 新增商品", "!新增商品說明"), _message_action("⬅️ 返回商店管理", "!商店管理")))
    contents = _bubble(
        "📦 商品管理列表",
        f"目前群組：{group_name}\n點選商品右側的「管理」進入操作頁。",
        body_rows,
        "最多顯示前 12 項商品；完整文字清單仍可用 !商品列表。",
    )
    return FlexSendMessage(alt_text="商品管理列表", contents=contents)


def admin_shop_item_actions_flex(group_name: str, item):
    """V3.2：單一商品操作頁。"""
    name = str(item.get("name") or "未命名商品")
    category = str(item.get("category") or "其他")
    price = int(item.get("price") or 0)
    desc = str(item.get("description") or "無說明")
    active = bool(item.get("is_active", True))
    toggle_label = "🚫 下架商品" if active else "👁️ 上架商品"
    toggle_text = f"!下架商品 {name}" if active else f"!上架商品 {name}"
    rows = [
        {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F6F0FC",
            "cornerRadius": "md",
            "paddingAll": "md",
            "contents": [
                {"type": "text", "text": name, "weight": "bold", "size": "lg", "wrap": True},
                {"type": "text", "text": f"分類：{category}", "size": "sm", "margin": "sm", "wrap": True},
                {"type": "text", "text": f"價格：🌈{price:,}", "size": "sm", "wrap": True},
                {"type": "text", "text": f"狀態：{'上架中' if active else '已下架'}", "size": "sm", "wrap": True},
                {"type": "text", "text": f"說明：{desc}", "size": "sm", "color": "#666666", "margin": "sm", "wrap": True},
            ],
        },
        _grid_row(_message_action("💲 修改價格", f"!修改商品價格說明 {name}"), _message_action("✏️ 修改資料", f"!修改商品說明 {name}")),
        _grid_row(_message_action(toggle_label, toggle_text)),
        _grid_row(_message_action("⬅️ 返回商品列表", "!商品管理列表"), _message_action("🏠 管理中心", "!後台")),
    ]
    contents = _bubble("🛍️ 商品操作", f"目前群組：{group_name}", rows, "商品變更會立即寫入資料庫。")
    return FlexSendMessage(alt_text=f"管理商品：{name}", contents=contents)


def admin_member_list_flex(group_name: str, members, page: int, total_pages: int, total_count: int):
    """V3.3.1：精簡成員清單，只顯示名稱與連續簽到天數。"""
    rows = []
    for member in members:
        name = str(member.get("name") or "未命名成員")
        user_id = str(member.get("user_id") or "")
        streak = int(member.get("streak_count") or 0)
        rows.append({
            "type": "button",
            "style": "secondary",
            "height": "sm",
            "margin": "xs",
            "action": {
                "type": "postback",
                "label": f"👤 {name[:12]}　🔥{streak}天　›",
                "data": "cmd=" + quote_plus(f"!查看成員ID {user_id}|{page}"),
            },
        })
    if not rows:
        rows.append({"type": "text", "text": "目前沒有成員資料。", "size": "sm", "color": "#777777"})

    nav = []
    if page > 1:
        nav.append(_message_action("⬅ 上一頁", f"!成員頁 {page - 1}"))
    if page < total_pages:
        nav.append(_message_action("下一頁 ➡", f"!成員頁 {page + 1}"))
    if nav:
        rows.append(_grid_row(nav[0], nav[1] if len(nav) > 1 else None))
    rows.append(_grid_row(_message_action("🔍 搜尋", "!搜尋成員說明"), _message_action("🏠 管理中心", "!後台")))

    contents = _bubble(
        "👥 成員管理",
        f"目前群組：{group_name}\n共 {total_count} 位成員｜第 {page}/{total_pages} 頁",
        rows,
        "點擊成員即可查看完整資料。",
    )
    return FlexSendMessage(alt_text=f"成員管理 第 {page} 頁", contents=contents)


def _identity_card_rows(data, admin=False, user_id="", return_page=1):
    """一般狀態與後台成員資料共用同一套資訊區塊。"""
    equipped = str(data.get("equipped_title") or "").strip()
    identity_contents = [
        {"type":"text","text":str(data.get("name_line") or "👤 成員"),"weight":"bold","size":"lg","wrap":True},
        {"type":"text","text":f"🏆 階級：{data.get('rank_title','-')}","size":"sm","margin":"sm","wrap":True},
        {"type":"text","text":f"🏅 Lv.{data.get('level',1)}｜{data.get('level_title','-')}","size":"sm","wrap":True},
    ]
    if equipped:
        identity_contents.append({"type":"text","text":f"🎖️ 佩戴稱號：{equipped}","size":"sm","wrap":True})
    identity_contents.append(
        {"type":"text","text":f"🎂 生日：{data.get('birthday','尚未設定')}","size":"sm","margin":"sm","wrap":True}
    )
    identity_contents.append(
        {"type":"text","text":f"💎 VIP：{data.get('vip_text','未啟用')}","size":"sm","margin":"sm","wrap":True}
    )
    vip_detail = str(data.get('vip_detail') or '').strip()
    if vip_detail:
        identity_contents.append(
            {"type":"text","text":vip_detail,"size":"xs","color":"#777777","wrap":True}
        )
    rows=[
        {"type":"box","layout":"vertical","backgroundColor":"#F6F0FC","cornerRadius":"lg","paddingAll":"md","contents":identity_contents},
        {"type":"box","layout":"vertical","backgroundColor":"#FFFFFF","cornerRadius":"lg","paddingAll":"md","contents":[
            {"type":"text","text":"⭐ 經驗進度","weight":"bold","size":"sm"},
            {"type":"text","text":str(data.get('exp_bar','')),"size":"sm","margin":"sm","color":"#6A35A5"},
            {"type":"text","text":str(data.get('exp_line','')),"size":"sm","wrap":True},
            {"type":"text","text":str(data.get('exp_need','')),"size":"xs","color":"#777777","margin":"xs","wrap":True},
        ]},
        {"type":"box","layout":"horizontal","spacing":"sm","contents":[
            {"type":"box","layout":"vertical","flex":1,"backgroundColor":"#FFFFFF","cornerRadius":"lg","paddingAll":"md","contents":[
                {"type":"text","text":"🌈 彩虹幣","size":"xs","weight":"bold","align":"center"},
                {"type":"text","text":f"{int(data.get('coins',0)):,}","size":"lg","weight":"bold","align":"center","margin":"xs"}]},
            {"type":"box","layout":"vertical","flex":1,"backgroundColor":"#FFFFFF","cornerRadius":"lg","paddingAll":"md","contents":[
                {"type":"text","text":"🔥 簽到","size":"xs","weight":"bold","align":"center"},
                {"type":"text","text":f"連續 {data.get('streak',0)} 天\n累積 {data.get('total_sign',0)} 天","size":"sm","align":"center","margin":"xs"}]},
        ]},
        {"type":"box","layout":"horizontal","spacing":"sm","contents":[
            {"type":"box","layout":"vertical","flex":1,"backgroundColor":"#FFFFFF","cornerRadius":"lg","paddingAll":"md","contents":[
                {"type":"text","text":"💬 今日活躍","size":"xs","weight":"bold","align":"center"},
                {"type":"text","text":f"聊天 {data.get('today_msg',0)}\n貼圖 {data.get('today_sticker',0)}","size":"sm","align":"center","margin":"xs"}]},
            {"type":"box","layout":"vertical","flex":1,"backgroundColor":"#FFFFFF","cornerRadius":"lg","paddingAll":"md","contents":[
                {"type":"text","text":"🏆 群內排名","size":"xs","weight":"bold","align":"center"},
                {"type":"text","text":f"等級 #{data.get('level_rank','-')}\n簽到 #{data.get('sign_rank','-')}","size":"sm","align":"center","margin":"xs"}]},
        ]},
    ]
    if data.get('next_sign'):
        rows.append({"type":"box","layout":"vertical","backgroundColor":"#F6F0FC","cornerRadius":"lg","paddingAll":"md","contents":[
            {"type":"text","text":"⏰ 下次可簽到","size":"xs","weight":"bold"},
            {"type":"text","text":str(data.get('next_sign')),"size":"sm","margin":"xs","wrap":True}]})
    if admin:
        rows.append(_grid_row(_message_action("💎 VIP 管理", f"!VIP操作ID {user_id}|{return_page}"), _message_action("📜 消費紀錄", f"!消費紀錄 {data.get('plain_name','成員')}")))
        rows.append(_grid_row(_message_action("⬅ 返回本頁列表", f"!成員頁 {return_page}"), _message_action("🏠 管理中心", "!後台")))
    else:
        # 一般成員「我的狀態」只保留三個常用入口，避免卡片過長。
        rows.append(_grid_row(_message_action("🛍️ 商店", "!商店"), _message_action("📅 簽到", "!簽到")))
        rows.append(_grid_row(_message_action("🏠 主頁", "!功能")))
    return rows


def admin_member_detail_flex(group_name: str, member, return_page: int = 1):
    name=str(member.get("name") or "未命名成員"); level=int(member.get("level") or 1); exp=int(member.get("exp") or 0)
    level_exp=int(member.get("level_exp") or (exp % 100)); prog=progress_info(level,level_exp,exp)
    vip_until=str(member.get("vip_until") or ""); is_vip=int(member.get("is_vip") or 0)==1
    if not is_vip: vip_text="未啟用"; vip_detail=""
    elif vip_until.upper() in ("PERMANENT","FOREVER","永久","永久VIP"): vip_text="永久 VIP ♾️"; vip_detail=""
    else: vip_text="啟用中"; vip_detail=f"📅 到期：{vip_until[:10]}" if vip_until else ""
    data={
        "plain_name":name,"name_line":f"👤 {name}"+(f" 🎂{member.get('birthday')}" if member.get('birthday') else ""),
        "level":level,"rank_title":rank_title(level),"level_title":level_title(level),"equipped_title":str(member.get('custom_title') or ''),
        "vip_text":vip_text,"vip_detail":vip_detail,"exp_bar":f"{prog['bar']} {prog['percent']}%",
        "exp_line":f"{prog['current']:,} / {prog['needed']:,} EXP｜累積 {exp:,}","exp_need":f"距離 Lv.{level+1}：{prog['remaining']:,} EXP",
        "coins":int(member.get('coins') or 0),"streak":int(member.get('streak_count') or 0),
        "total_sign":int(member.get('total_sign') or member.get('sign_month_count') or 0),"today_msg":int(member.get('today_msg_count') or 0),
        "today_sticker":int(member.get('today_sticker_count') or 0),"level_rank":member.get('level_rank','-'),"sign_rank":member.get('sign_rank','-'),
    }
    rows=_identity_card_rows(data,True,str(member.get('user_id') or ''),return_page)
    return FlexSendMessage(alt_text=f"成員資料：{name}",contents=_bubble("👤 成員詳細資料",f"目前群組：{group_name}",rows,"資料僅限群長在 Bot 私訊中查看。"))


def admin_member_vip_actions_flex(group_name: str, member, return_page: int = 1):
    name = str(member.get("name") or "未命名成員")
    user_id = str(member.get("user_id") or "")
    rows = [
        _grid_row(_message_action("設定 7 天", f"!設定VIPID {user_id}|7天"), _message_action("設定 30 天", f"!設定VIPID {user_id}|30天")),
        _grid_row(_message_action("延長 7 天", f"!延長VIPID {user_id}|7天"), _message_action("延長 30 天", f"!延長VIPID {user_id}|30天")),
        _grid_row(_message_action("♾️ 永久 VIP", f"!設定VIPID {user_id}|永久"), _message_action("❌ 移除 VIP", f"!移除VIPID {user_id}")),
        _grid_row(_message_action("⬅ 返回成員資料", f"!查看成員ID {user_id}|{return_page}"), _message_action("🏠 管理中心", "!後台")),
    ]
    contents = _bubble("💎 VIP 快速管理", f"目前群組：{group_name}\n成員：{name}", rows, "所有 VIP 操作都會留下紀錄。")
    return FlexSendMessage(alt_text=f"VIP 管理：{name}", contents=contents)


def admin_announcement_settings_flex(group_name: str, enabled: bool, push_time: str, content: str):
    """V3.4.1：公告設定按鈕頁。"""
    status_text = "✅ 已開啟" if enabled else "❌ 已關閉"
    preview = (content or "尚未設定公告內容").strip()
    if len(preview) > 180:
        preview = preview[:177] + "..."
    toggle_label = "🔕 關閉推播" if enabled else "🔔 開啟推播"
    toggle_text = "!關閉公告推播" if enabled else "!開啟公告推播"
    rows = [
        {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F6F0FC",
            "cornerRadius": "md",
            "paddingAll": "md",
            "contents": [
                {"type": "text", "text": f"推播狀態：{status_text}", "weight": "bold", "size": "sm", "wrap": True},
                {"type": "text", "text": f"每日時間：{push_time}", "size": "sm", "margin": "sm", "wrap": True},
                {"type": "text", "text": "目前公告內容", "weight": "bold", "size": "sm", "margin": "md"},
                {"type": "text", "text": preview, "size": "sm", "color": "#666666", "margin": "sm", "wrap": True},
            ],
        },
        _grid_row(_message_action("✏️ 設定公告", "!設定公告說明"), _message_action("⏰ 設定時間", "!設定公告時間說明")),
        _grid_row(_message_action(toggle_label, toggle_text), _message_action("📣 立即推播", "!立即推播公告")),
        _grid_row(_message_action("👁️ 查看公告", "!查看公告"), _message_action("🗑️ 刪除公告", "!刪除公告")),
        _grid_row(_message_action("⬅️ 返回設定中心", "!設定中心"), _message_action("🏠 管理中心", "!後台")),
    ]
    contents = _bubble(
        "📢 公告設定",
        f"目前群組：{group_name}\n公告會推播到目前選擇的群組。",
        rows,
        "排程會在群組有活動時檢查；立即推播則會馬上送出。",
    )
    return FlexSendMessage(alt_text="公告設定", contents=contents)


def admin_activity_settings_flex(group_name: str, manual_left: int, chest_left: int, wheel_left: int):
    """V3.4.2：活動設定實際操作頁。"""
    def status_line(icon: str, name: str, seconds: int) -> str:
        if seconds and seconds > 0:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            remain = f"{hours}小時{minutes}分" if hours else f"{minutes}分"
            return f"{icon} {name}：🟢 進行中（剩餘 {remain}）"
        return f"{icon} {name}：⚪ 未開啟"

    rows = [
        {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F6F0FC",
            "cornerRadius": "md",
            "paddingAll": "md",
            "contents": [
                {"type": "text", "text": status_line("🌈", "狂歡模式", manual_left), "size": "sm", "wrap": True},
                {"type": "text", "text": status_line("🎁", "寶箱雨", chest_left), "size": "sm", "margin": "sm", "wrap": True},
                {"type": "text", "text": status_line("🎰", "輪盤大暴送", wheel_left), "size": "sm", "margin": "sm", "wrap": True},
            ],
        },
        _grid_row(_message_action("🌈 狂歡 1 小時", "!私訊開始狂歡 1"), _message_action("🌈 狂歡 2 小時", "!私訊開始狂歡 2")),
        _grid_row(_message_action("⛔ 停止狂歡", "!私訊停止狂歡"), _message_action("🎁 開啟寶箱雨", "!私訊開始寶箱雨")),
        _grid_row(_message_action("⛔ 停止寶箱雨", "!私訊停止寶箱雨"), _message_action("🎰 開啟輪盤", "!私訊開始輪盤")),
        _grid_row(_message_action("⛔ 停止輪盤", "!私訊停止輪盤"), _message_action("🔄 重新整理", "!活動設定")),
        _grid_row(_message_action("⬅️ 返回設定中心", "!設定中心"), _message_action("🏠 管理中心", "!後台")),
    ]
    contents = _bubble(
        "🎉 活動設定",
        f"目前群組：{group_name}\n活動會直接套用到目前選擇的群組。",
        rows,
        "狂歡：聊天 EXP ×2｜寶箱雨：掉落率 15%｜輪盤：250～1000 彩虹幣",
    )
    return FlexSendMessage(alt_text="活動設定", contents=contents)

# ===== V4.0 integrated navigation =====
def admin_sign_settings_v4_flex(group_name, settings):
    prices=settings.get('makeup_prices') or {}
    rows=[
        _grid_row(_message_action('🌈 修改簽到彩虹幣','!簽到彩虹幣說明'),_message_action('⭐ 修改簽到 EXP','!簽到EXP說明')),
        _grid_row(_message_action('📝 修改補簽價格','!補簽價格說明'),_message_action('🎁 連續獎勵','!連續獎勵說明')),
        _grid_row(_message_action('🔄 重新整理','!簽到設定V4'),_message_action('⬅ 返回設定中心','!設定中心')),
    ]
    desc=(f'目前群組：{group_name}\n每日換日：05:00\n簽到彩虹幣：🌈{settings.get("coin",300):,}\n'
          f'簽到 EXP：⭐{settings.get("exp",150):,}\n補簽：1天 {prices.get("1",100)}／7天 {prices.get("7",1000)}')
    return FlexSendMessage(alt_text='簽到設定',contents=_bubble('📅 簽到設定',desc,rows,'所有修改只影響目前群組。'))

def admin_v4_center_flex(group_name):
    rows=[
      _grid_row(_message_action('📅 簽到設定','!簽到設定V4'),_message_action('💎 VIP設定','!VIP設定')),
      _grid_row(_message_action('🛍 商店管理','!商店管理'),_message_action('🎖 稱號管理','!稱號管理V4')),
      _grid_row(_message_action('🎁 抽獎設定','!抽獎設定V4'),_message_action('🎉 活動設定','!活動設定')),
      _grid_row(_message_action('📢 公告設定','!公告管理'),_message_action('🔔 提醒中心','!提醒中心')),
      _grid_row(_message_action('📊 數據分析','!數據分析'),_message_action('📜 操作日誌','!操作日誌')),
      _grid_row(_message_action('🏦 金庫唯讀','!金庫管理'),_message_action('🏠 管理首頁','!後台')),
    ]
    return FlexSendMessage(alt_text='V4.0 管理設定中心',contents=_bubble('🌈 V4.0 管理中心',f'目前群組：{group_name}\n功能設定與統計集中在此。',rows,'金庫仍為唯讀，任何人都不能人工修改。'))

def personal_center_v4_flex():
    rows=[
      _grid_row(_message_action('👤 我的狀態','!我的狀態'),_message_action('📅 簽到','!簽到')),
      _grid_row(_message_action('📝 補簽','!補簽'),_message_action('🏦 彩虹金庫','!金庫')),
      _grid_row(_message_action('🛍 商店','!商店'),_message_action('🌈 活動商店','!活動商店')),
      _grid_row(_message_action('🎖 我的稱號','!我的稱號'),_message_action('🏦 彩虹金庫','!金庫')),
      _grid_row(_message_action('🎂 生日設定','!生日設定'),_message_action('📖 功能說明','!指令')),
    ]
    return FlexSendMessage(alt_text='Rainbow Life 個人中心',contents=_bubble('🌈 個人中心','所有常用功能都可以直接點擊。',rows,'文字指令仍完整保留。'))

def group_portal_v4_flex():
    rows=[
      _grid_row(_message_action('📢 最新公告','!查看公告'),_message_action('📜 群規','!群規')),
      _grid_row(_message_action('🎉 目前活動','!活動'),_message_action('📜 群規','!群規')),
      _grid_row(_message_action('📅 簽到','!簽到'),_message_action('👤 個人中心','!個人中心')),
      _grid_row(_message_action('🛍 商店','!商店'),_message_action('🏦 金庫','!金庫')),
    ]
    return FlexSendMessage(alt_text='Rainbow Life 群組首頁',contents=_bubble('🏳️‍🌈 群組首頁','公告、群規、活動與常用功能集中顯示。',rows,'可輸入 !功能 返回完整功能中心。'))


# ===== V4.1 / V4.2 UI =====
def ranking_menu_flex():
    rows = [
        _grid_row(_message_action("🏅 等級排行榜", "!等級排行榜"), _message_action("💬 今日聊天榜", "!今日聊天榜")),
        _grid_row(_message_action("📅 昨日聊天榜", "!昨日聊天榜"), _message_action("🖼️ 貼圖排行榜", "!貼圖排行榜")),
        _grid_row(_message_action("🔥 連續簽到榜", "!連續簽到榜"), _message_action("📆 累積簽到榜", "!累積簽到榜")),
        _grid_row(_message_action("🌈 彩虹幣榜", "!金幣排行榜")),
    ]
    rows.extend(_front_nav_rows("!功能", "!排行榜"))
    return FlexSendMessage(
        alt_text="Rainbow Life 排行榜",
        contents=_bubble("🏆 排行榜", "請選擇要查看的排行榜。", rows, "點擊後才會顯示榜單資料。"),
    )


def vip_shop_flex(items):
    rows=[]
    for item in list(items)[:6]:
        name=str(item.get('name') or 'VIP方案')
        price=int(item.get('price') or 0)
        desc=str(item.get('description') or '')
        rows.append({
            'type':'box','layout':'horizontal','spacing':'sm','margin':'sm',
            'contents':[
                {'type':'box','layout':'vertical','flex':3,'contents':[
                    {'type':'text','text':f'💎 {name}','weight':'bold','size':'md','wrap':True},
                    {'type':'text','text':f'🌈 {price:,} 彩虹幣','size':'sm','color':'#6A35A5','margin':'xs'},
                    {'type':'text','text':desc or 'VIP 會員方案','size':'xs','color':'#777777','wrap':True,'margin':'xs'},
                ]},
                {'type':'button','style':'primary','height':'sm','flex':1,'color':'#7E57C2',
                 'action':{'type':'message','label':'購買','text':f'!購買 {name}'}}
            ]
        })
    if not rows:
        rows.append({'type':'text','text':'目前沒有 VIP 商品。','size':'sm','color':'#777777'})
    rows.append(_grid_row(_message_action('👤 查看我的狀態','!我的狀態'),_message_action('⬅️ 返回商店','!商店')))
    return FlexSendMessage(alt_text='VIP 商店',contents=_bubble('💎 VIP 商店','選擇方案後會使用安全交易流程。',rows,'VIP 時間可累加；永久 VIP 不可重複購買。'))


def title_shop_flex(items, is_vip=False):
    rows=[]
    for item in list(items)[:10]:
        name=str(item.get('title_name') or item.get('name') or '稱號')
        price=int(item.get('price') or 0)
        rows.append({
            'type':'box','layout':'horizontal','spacing':'sm','margin':'xs',
            'contents':[
                {'type':'text','text':name,'weight':'bold','size':'sm','wrap':True,'flex':3},
                {'type':'text','text':('VIP限定' if is_vip else f'🌈{price:,}'),'size':'xs','align':'end','color':'#6A35A5','flex':1},
                {'type':'button','style':'secondary','height':'sm','flex':1,
                 'action':{'type':'message','label':'選擇','text':(('!裝備稱號 ' if is_vip else '!挑選稱號 ')+name)}}
            ]
        })
    if not rows:
        rows.append({'type':'text','text':'目前沒有稱號。','size':'sm','color':'#777777'})
    rows.append(_grid_row(_message_action('💎 VIP稱號','!VIP稱號'),_message_action('⬅️ 返回商店','!商店')))
    return FlexSendMessage(alt_text='稱號商店',contents=_bubble('🎖️ 稱號商店','點擊稱號即可選擇或購買。',rows,'最多顯示前 10 個稱號。'))


def status_card_flex(data):
    """一般成員與後台一致的狀態版型。"""
    rows=_identity_card_rows(data,False)
    return FlexSendMessage(alt_text="我的狀態",contents=_bubble("🌈 我的狀態","目前只顯示個人狀態；底部可返回或切換其他介面。",rows,"資料依目前群組即時更新。"))


def level_up_flex(name: str, result):
    """統一的升級通知卡。"""
    rows=[
        {"type":"box","layout":"vertical","backgroundColor":"#F6F0FC","cornerRadius":"lg","paddingAll":"md","contents":[
            {"type":"text","text":f"👤 {name}","weight":"bold","size":"lg","wrap":True},
            {"type":"text","text":f"🏆 階級：{result.get('rank','-')}","size":"sm","margin":"sm","wrap":True},
            {"type":"text","text":f"🏅 Lv.{result.get('old_level',1)} → Lv.{result.get('new_level',1)}","weight":"bold","size":"md","margin":"sm"},
            {"type":"text","text":f"🌟 等級稱號：{result.get('level_title','-')}","size":"sm","wrap":True},
        ]},
        {"type":"box","layout":"vertical","backgroundColor":"#FFFFFF","cornerRadius":"lg","paddingAll":"md","contents":[
            {"type":"text","text":f"⭐ 本次獲得：+{int(result.get('gained',0)):,} EXP","size":"sm","weight":"bold"},
            {"type":"text","text":f"📈 新進度：{int(result.get('level_exp',0)):,} / {int(result.get('needed',0)):,} EXP","size":"sm","margin":"xs"},
        ]},
    ]
    return FlexSendMessage(alt_text=f"恭喜 {name} 升級",contents=_bubble("🎉 升級成功！","新的等級與稱號已生效。",rows,""))


def vault_card_flex(data):
    rows=[
        {'type':'box','layout':'vertical','backgroundColor':'#F7F2FC','cornerRadius':'md','paddingAll':'md','contents':[
            {'type':'text','text':f"🌈 {int(data.get('balance',0)):,}",'weight':'bold','size':'xxl','align':'center','color':'#6A35A5'},
            {'type':'text','text':f"下一目標：{int(data.get('target',50000)):,}",'size':'sm','align':'center','margin':'sm'},
            {'type':'text','text':data.get('bar','░░░░░░░░░░ 0%'),'size':'sm','align':'center','margin':'sm','color':'#6A35A5'},
            {'type':'text','text':f"還差：{int(data.get('remaining',0)):,}",'size':'sm','align':'center'},
        ]},
        {'type':'box','layout':'horizontal','spacing':'sm','contents':[
            {'type':'box','layout':'vertical','flex':1,'backgroundColor':'#FFFFFF','cornerRadius':'md','paddingAll':'sm','contents':[{'type':'text','text':'今日收入','size':'xs','align':'center'},{'type':'text','text':f"🌈{int(data.get('today',0)):,}",'weight':'bold','align':'center','margin':'xs'}]},
            {'type':'box','layout':'vertical','flex':1,'backgroundColor':'#FFFFFF','cornerRadius':'md','paddingAll':'sm','contents':[{'type':'text','text':'昨日收入','size':'xs','align':'center'},{'type':'text','text':f"🌈{int(data.get('yesterday',0)):,}",'weight':'bold','align':'center','margin':'xs'}]},
            {'type':'box','layout':'vertical','flex':1,'backgroundColor':'#FFFFFF','cornerRadius':'md','paddingAll':'sm','contents':[{'type':'text','text':'本週收入','size':'xs','align':'center'},{'type':'text','text':f"🌈{int(data.get('week',0)):,}",'weight':'bold','align':'center','margin':'xs'}]},
        ]},
        {'type':'text','text':f"📈 歷史累積：🌈{int(data.get('lifetime',0)):,}\n🎁 達標獎勵：全體 🌈+300、⭐+100",'size':'sm','wrap':True},
        _grid_row(_message_action('📜 金庫紀錄','!金庫紀錄'),_message_action('🔄 重新整理','!金庫')),
    ]
    rows.extend(_front_nav_rows('!功能'))
    return FlexSendMessage(alt_text='彩虹金庫',contents=_bubble('🏦 彩虹金庫','所有成員消費會自動進入金庫。',rows,'🔒 任何人包含群長都不能手動修改金庫。'))


def shop_category_flex(title, items, back_text="!商店"):
    rows=[]
    for item in list(items)[:10]:
        name=str(item.get('name') or '商品')
        price=int(item.get('price') or 0)
        desc=str(item.get('description') or '')
        rows.append({
            'type':'box','layout':'horizontal','spacing':'sm','margin':'xs',
            'contents':[
                {'type':'box','layout':'vertical','flex':3,'contents':[
                    {'type':'text','text':name,'weight':'bold','size':'sm','wrap':True},
                    {'type':'text','text':f'🌈{price:,}｜{desc or "一般商品"}','size':'xs','color':'#777777','wrap':True,'margin':'xs'},
                ]},
                {'type':'button','style':'secondary','height':'sm','flex':1,
                 'action':{'type':'message','label':'購買','text':f'!購買 {name}'}}
            ]
        })
    if not rows:
        rows.append({'type':'text','text':'目前沒有商品。','size':'sm','color':'#777777'})
    rows.append(_grid_row(_message_action('⬅️ 返回商店',back_text),_message_action('🏠 功能中心','!功能')))
    return FlexSendMessage(alt_text=title,contents=_bubble(title,'點擊商品即可購買。',rows,'所有消費都會留下交易紀錄並進入金庫。'))

# ===== V5.0 Phase 2：前台資訊頁卡片化 =====
def birthday_center_flex(current_birthday: str = "尚未設定"):
    locked = bool(current_birthday and current_birthday != "尚未設定")
    rows = [
        {"type":"box","layout":"vertical","backgroundColor":"#F7F0FF","cornerRadius":"md","paddingAll":"md","contents":[
            {"type":"text","text":"目前生日","size":"xs","color":"#777777"},
            {"type":"text","text":current_birthday or "尚未設定","weight":"bold","size":"xl","color":"#6A35A5","margin":"xs"},
            {"type":"text","text":"🔒 設定完成後不可再次修改" if locked else "請確認日期正確後再設定","size":"xs","color":"#777777","margin":"sm","wrap":True},
        ]},
    ]
    if not locked:
        rows.append(_grid_row(_message_action("✏️ 設定生日", "!生日輸入說明")))
    rows.append(_grid_row(_message_action("🎉 今日壽星", "!今日壽星"), _message_action("📅 即將生日", "!生日名單")))
    rows.extend(_front_nav_rows("!功能", "!生日設定"))
    footer = "生日已鎖定，不可由本人重新設定。" if locked else "設定時可輸入 MM/DD，也可包含出生年份。"
    return FlexSendMessage(alt_text="生日中心", contents=_bubble("🎂 生日中心", "生日資料與查詢功能集中在這一頁。", rows, footer))


def birthday_input_help_flex():
    rows = [
        {"type":"text","text":"請在聊天室輸入：","weight":"bold","size":"sm"},
        {"type":"text","text":"!生日設定 11/05","size":"lg","weight":"bold","color":"#6A35A5","wrap":True},
        {"type":"text","text":"也可輸入：!生日設定 2000/11/05","size":"sm","color":"#666666","wrap":True},
        _grid_row(_message_action("🎂 返回生日中心", "!生日設定"), _message_action("🏠 功能中心", "!功能")),
    ]
    return FlexSendMessage(alt_text="設定生日說明", contents=_bubble("✏️ 設定生日", "依照下方格式輸入生日。", rows, "生日資料會依目前群組分開保存。"))


def announcement_view_flex(content: str, enabled: bool = False, time_text: str = "20:00"):
    preview = (content or "目前尚未設定公告。").strip()
    if len(preview) > 700:
        preview = preview[:697] + "..."
    rows = [
        {"type":"box","layout":"vertical","backgroundColor":"#F7F0FF","cornerRadius":"md","paddingAll":"md","contents":[
            {"type":"text","text":preview,"size":"md","wrap":True,"color":"#333333"},
        ]},
        {"type":"text","text":f"推播：{'開啟' if enabled else '關閉'}｜時間：{time_text}","size":"xs","color":"#777777","wrap":True},
    ]
    rows.extend(_front_nav_rows("!功能", "!查看公告"))
    return FlexSendMessage(alt_text="最新公告", contents=_bubble("📢 最新公告", "查看目前群組公告內容。", rows, "公告內容由群長在管理後台設定。"))


def activity_center_flex(summary: str, show_admin: bool = False):
    summary = (summary or "目前沒有進行中的活動。").strip()
    if len(summary) > 800:
        summary = summary[:797] + "..."
    rows = [
        {"type":"box","layout":"vertical","backgroundColor":"#F7F0FF","cornerRadius":"md","paddingAll":"md","contents":[
            {"type":"text","text":summary,"size":"sm","wrap":True,"color":"#333333"},
        ]},
        _grid_row(_message_action("📋 活動任務", "!活動任務"), _message_action("🎊 我的活動", "!我的活動")),
        _grid_row(_message_action("🎒 活動背包", "!活動背包"), _message_action("🛍️ 活動商店", "!活動商店")),
    ]
    if show_admin:
        rows.append(_grid_row(_message_action("🏆 活動排行", "!活動排行")))
    rows.extend(_front_nav_rows("!功能", "!活動資訊"))
    return FlexSendMessage(alt_text="活動中心", contents=_bubble("🎉 活動中心", "目前活動與活動功能集中顯示。", rows, "點擊功能後仍可返回活動中心或功能中心。"))


def command_help_flex(show_admin: bool = False):
    rows = [
        _grid_row(_message_action("🌈 基本功能", "!基本指令"), _message_action("📅 簽到生日", "!簽到生日指令")),
        _grid_row(_message_action("🛍️ 商店功能", "!商店"), _message_action("🎉 活動功能", "!活動資訊")),
        _grid_row(_message_action("👤 我的狀態", "!我的狀態")),
    ]
    if show_admin:
        rows.append(_grid_row(_message_action("🏆 管理排行榜", "!排行榜"), _message_action("👑 群長後台", "!後台")))
    rows.extend(_front_nav_rows("!功能", "!指令"))
    return FlexSendMessage(alt_text="功能說明", contents=_bubble("📖 功能說明", "不用記住全部指令，主要功能都能直接點按鈕。", rows, "原本的文字指令仍然保留。"))


def simple_help_page_flex(title: str, lines: list[str], back_text: str = "!指令"):
    rows = [{"type":"text","text":str(line),"size":"sm","wrap":True,"color":"#333333"} for line in lines]
    rows.extend(_front_nav_rows(back_text, None))
    return FlexSendMessage(alt_text=title, contents=_bubble(title, "常用文字指令整理。", rows, "也可以直接返回功能中心使用按鈕。"))


def admin_input_help_flex(title: str, instruction: str, example: str, back_text: str):
    rows = [
        {"type":"text","text":instruction,"weight":"bold","size":"sm","wrap":True},
        {"type":"box","layout":"vertical","backgroundColor":"#F7F0FF","cornerRadius":"md","paddingAll":"md","contents":[
            {"type":"text","text":example,"size":"sm","weight":"bold","color":"#6A35A5","wrap":True}
        ]},
    ]
    rows.extend(_admin_nav_rows(back_text))
    return FlexSendMessage(alt_text=title, contents=_bubble(title, "請依照格式在與 Bot 的私訊聊天室輸入。", rows, "完成後會自動返回相關管理頁。"))



def player_center_entry_flex(player_url: str, member_name: str = "成員"):
    """個人中心網頁入口卡；不再顯示舊版功能中心按鈕。"""
    rows = []
    if member_name:
        rows.append({"type":"text","text":f"👤 {member_name}","size":"sm","weight":"bold","wrap":True})
    rows.append({
        "type":"box","layout":"vertical","backgroundColor":"#F7F0FF",
        "cornerRadius":"lg","paddingAll":"md","contents":[
            {"type":"text","text":"點擊後會自動辨識目前使用的 LINE 帳號。", "size":"sm","wrap":True},
            {"type":"text","text":"即使點的是別人傳出的入口，也只會顯示你自己的資料。", "size":"xs","color":"#666666","margin":"sm","wrap":True}
        ]
    })
    if player_url:
        rows.append(_uri_action("🌈 前往個人中心", player_url))
    else:
        rows.append({"type":"text","text":"⚠️ 暫時無法產生個人中心連結，請稍後再試。","size":"sm","color":"#B3261E","wrap":True})
    return FlexSendMessage(
        alt_text="🌈 個人中心",
        contents=_bubble("🌈 個人中心", "所有成員共用入口・只顯示自己的資料", rows, "")
    )

def _sign_theme_palette(theme_key=""):
    """Return readable Flex colors for season/day-night previews and real sign cards."""
    import datetime as _dt
    key = str(theme_key or "").strip().lower()
    aliases = {
        "春白":"spring_day", "春夜":"spring_night", "夏白":"summer_day", "夏夜":"summer_night",
        "秋白":"autumn_day", "秋夜":"autumn_night", "冬白":"winter_day", "冬夜":"winter_night",
        "vip":"vip", "春季白天":"spring_day", "春季夜晚":"spring_night", "夏季白天":"summer_day",
        "夏季夜晚":"summer_night", "秋季白天":"autumn_day", "秋季夜晚":"autumn_night",
        "冬季白天":"winter_day", "冬季夜晚":"winter_night",
    }
    key = aliases.get(key, key)
    if not key:
        now = _dt.datetime.utcnow() + _dt.timedelta(hours=8)
        season = "spring" if 3 <= now.month <= 5 else "summer" if 6 <= now.month <= 8 else "autumn" if 9 <= now.month <= 11 else "winter"
        key = f"{season}_{'day' if 6 <= now.hour < 18 else 'night'}"
    palettes = {
        "spring_day": dict(label="🌸 春櫻・白天", bg="#FFF1F7", card="#FFFFFF", accent="#EC5FA3", text="#54223D", sub="#87566F", border="#F4A7CD", head="#FFD6E9"),
        "spring_night": dict(label="🌸 夜櫻・夜晚", bg="#20102F", card="#321747", accent="#FF78C6", text="#FFFFFF", sub="#EAC9F5", border="#B455D4", head="#421B58"),
        "summer_day": dict(label="🌊 夏海・白天", bg="#E9FBFF", card="#FFFFFF", accent="#13B9DC", text="#123E50", sub="#467384", border="#36CCE8", head="#CFF7FF"),
        "summer_night": dict(label="🌊 夏海・夜晚", bg="#071C35", card="#0B2B4A", accent="#39C8FF", text="#FFFFFF", sub="#B8E8FF", border="#2DB7EE", head="#0A365B"),
        "autumn_day": dict(label="🍁 秋楓・白天", bg="#FFF4DD", card="#FFFFFF", accent="#E88620", text="#573216", sub="#8B6547", border="#F0AD55", head="#FFE2B6"),
        "autumn_night": dict(label="🍁 秋楓・夜晚", bg="#2B160F", card="#452217", accent="#FF9B35", text="#FFFFFF", sub="#FFD3A5", border="#D96B26", head="#5B2B19"),
        "winter_day": dict(label="❄️ 冬雪・白天", bg="#EEF8FF", card="#FFFFFF", accent="#4C9CEB", text="#203B58", sub="#5F7890", border="#8BC8F5", head="#DDF1FF"),
        "winter_night": dict(label="❄️ 冬雪・夜晚", bg="#08172D", card="#102A4B", accent="#88B9FF", text="#FFFFFF", sub="#C8DEFF", border="#5C91DC", head="#163A65"),
        "vip": dict(label="💎 VIP 幻彩", bg="#120D2A", card="#25164D", accent="#F3B13C", text="#FFFFFF", sub="#E8D8FF", border="#B86CFF", head="#3A2168"),
    }
    return palettes.get(key, palettes["summer_day"]), key


def sign_result_flex(data):
    """Season/day-night themed sign card. Preview mode never changes player data."""
    already = bool(data.get("already"))
    preview = bool(data.get("preview"))
    palette, theme_key = _sign_theme_palette(data.get("theme"))
    title = "🧪 測試簽到範本" if preview else ("✅ 今日已完成簽到" if already else "🎉 簽到成功！")
    subtitle = f"{palette['label']}｜僅供預覽，不會發放獎勵。" if preview else ("今天的簽到紀錄已存在。" if already else "獎勵已加入你的個人資料。")

    def txt(text, size="sm", weight=None, color=None, margin=None, wrap=True):
        out={"type":"text","text":str(text),"size":size,"color":color or palette["text"],"wrap":wrap}
        if weight: out["weight"]=weight
        if margin: out["margin"]=margin
        return out

    info_contents = [
        txt(f"📅 日期：{data.get('date','-')}", weight="bold"),
        txt(f"🕒 時間：{data.get('time','-')}", margin="xs"),
        txt(f"🔥 連續簽到：{int(data.get('streak',0)):,} 天", margin="xs"),
        txt(f"📆 累積簽到：{int(data.get('total',0)):,} 天", margin="xs"),
    ]
    rows=[]
    member_row=_notification_member_row()
    if member_row: rows.append(member_row)
    rows.append({"type":"text","text":palette["label"],"size":"sm","weight":"bold","color":palette["accent"],"align":"end"})
    rows.append({"type":"box","layout":"vertical","backgroundColor":palette["card"],"cornerRadius":"xl","borderWidth":"2px","borderColor":palette["border"],"paddingAll":"md","contents":info_contents})
    if not already or preview:
        reward_contents=[txt("本次獎勵",weight="bold",color=palette["accent"]),txt(f"🌈 彩虹幣：+{int(data.get('coins',300)):,}",margin="sm"),txt(f"⭐ EXP：+{int(data.get('exp',150)):,}",margin="xs")]
        for line in list(data.get("bonus_lines") or [])[:5]: reward_contents.append(txt(line,size="xs",margin="xs",color=palette["sub"]))
        rows.append({"type":"box","layout":"vertical","backgroundColor":palette["card"],"cornerRadius":"xl","borderWidth":"2px","borderColor":palette["border"],"paddingAll":"md","contents":reward_contents})
    if data.get("next_time"): rows.append(txt(f"⏰ 下次可簽到\n{data.get('next_time')}",color=palette["sub"]))
    if preview: rows.append(txt("✅ 測試指令正常，這張卡沒有寫入簽到紀錄。",size="xs",color=palette["accent"]))
    player_url=str(data.get("player_url") or "").strip()
    if player_url: rows.append(_uri_action("🌈 前往個人中心",player_url))
    bubble={
      "type":"bubble","size":"mega",
      "styles":{"header":{"backgroundColor":palette["head"]},"body":{"backgroundColor":palette["bg"]},"footer":{"backgroundColor":palette["bg"]}},
      "header":{"type":"box","layout":"vertical","paddingAll":"lg","contents":[txt("🌈 Rainbow LifeBot",weight="bold",color=palette["text"])]},
      "body":{"type":"box","layout":"vertical","paddingAll":"lg","spacing":"md","contents":[txt(title,size="xl",weight="bold",color=palette["accent"]),txt(subtitle,size="sm",color=palette["sub"]),{"type":"separator","color":palette["border"]}]+rows}
    }
    return FlexSendMessage(alt_text=title,contents=bubble)


def operation_notice_flex(title: str, message: str, success: bool = True, admin: bool = False, back_text: str = "!功能"):
    """供後續功能逐步套用的成功／失敗通知卡。"""
    icon_title = ("✅ " if success else "⚠️ ") + title
    rows = []
    member_row = _notification_member_row()
    if member_row:
        rows.append(member_row)
    rows.append(
        {"type":"box","layout":"vertical","backgroundColor":"#F7F0FF","cornerRadius":"lg","paddingAll":"md","contents":[
            {"type":"text","text":str(message),"size":"sm","wrap":True,"color":"#333333"}
        ]}
    )
    return FlexSendMessage(alt_text=icon_title, contents=_bubble(icon_title, "操作結果", rows, ""))


def ranking_result_flex(title: str, rows, value_label: str = "", note: str = ""):
    """統一排行榜結果卡片。rows 支援 dict(name, score, detail) 或 tuple。"""
    body_rows = []
    medals = ["🥇", "🥈", "🥉"]
    normalized = list(rows or [])[:10]
    if not normalized:
        body_rows.append({
            "type": "box", "layout": "vertical", "backgroundColor": "#F7F0FF",
            "cornerRadius": "lg", "paddingAll": "md", "contents": [
                {"type": "text", "text": "目前還沒有排行榜資料。", "size": "sm", "color": "#555555", "wrap": True}
            ]
        })
    else:
        for index, item in enumerate(normalized, 1):
            if isinstance(item, dict):
                name = str(item.get("name") or "未命名成員")
                score = item.get("score", 0)
                detail = str(item.get("detail") or "")
            else:
                vals = list(item) if isinstance(item, (list, tuple)) else [item]
                name = str(vals[0] if vals else "未命名成員")
                score = vals[1] if len(vals) > 1 else 0
                detail = str(vals[2] if len(vals) > 2 else "")
            rank_mark = medals[index-1] if index <= 3 else f"{index}."
            score_text = f"{score}{value_label}" if value_label else str(score)
            line_contents = [
                {"type": "text", "text": f"{rank_mark} {name}", "weight": "bold" if index <= 3 else "regular", "size": "sm", "color": "#30233D", "wrap": True, "flex": 5},
                {"type": "text", "text": score_text, "weight": "bold", "size": "sm", "color": "#6A35A5", "align": "end", "flex": 2, "wrap": True},
            ]
            body_rows.append({"type": "box", "layout": "horizontal", "alignItems": "center", "paddingAll": "md", "backgroundColor": "#FFFFFF", "cornerRadius": "lg", "margin": "sm" if body_rows else "none", "contents": line_contents})
            if detail:
                body_rows.append({"type": "text", "text": detail, "size": "xs", "color": "#81778C", "wrap": True, "margin": "xs"})
    if note:
        body_rows.append({"type": "text", "text": str(note), "size": "xs", "color": "#81778C", "wrap": True, "margin": "md"})
    body_rows.extend(_home_only_rows())
    return FlexSendMessage(alt_text=title[:400], contents=_bubble(title, "即時排名結果", body_rows, "所有排行榜皆使用統一卡片顯示。"))

# ===== V5 Phase 4：所有舊文字回覆的通用卡片轉換 =====
def universal_text_card(text: str, alt_text: str = "🌈 Rainbow Life 通知", member_name: str = ""):
    """把尚未專屬設計的舊文字訊息包成統一 Flex 卡片，避免回到黑底純文字泡泡。"""
    p = flex_palette()
    raw = str(text or "").strip()
    if not raw:
        raw = "目前沒有可顯示的內容。"

    lines = [line.strip() for line in raw.splitlines()]
    non_empty = [line for line in lines if line]
    first = non_empty[0] if non_empty else "通知"

    # 第一行短且像標題時，取作卡片標題；否則使用通用標題。
    clean_first = first.lstrip("🌈🎂🎉🏆🎁💎🎡🔮📢📋⚙️👤🏦📖✅❌⚠️ℹ️ ")
    if len(first) <= 34:
        title = first[:40]
        body_lines = lines[1:]
    else:
        title = "🌈 Rainbow Life 通知"
        body_lines = lines

    # Flex 單一文字元件及 Bubble 都有限制，分段並控制總長度。
    contents = []
    total = 0
    paragraph = []

    def flush_paragraph():
        nonlocal paragraph, total
        if not paragraph:
            return
        chunk = "\n".join(paragraph).strip()
        paragraph = []
        if not chunk:
            return
        if total >= 6500:
            return
        chunk = chunk[:1800]
        total += len(chunk)
        contents.append({
            "type": "text",
            "text": chunk,
            "size": "md",
            "color": p["text"],
            "wrap": True,
            "margin": "md" if contents else "none",
        })

    for line in body_lines:
        if total >= 6500 or len(contents) >= 18:
            break
        if not line:
            flush_paragraph()
            continue
        paragraph.append(line)
        if sum(len(x) for x in paragraph) >= 900:
            flush_paragraph()
    flush_paragraph()

    if not contents:
        contents.append({
            "type": "text",
            "text": clean_first or "操作已完成。",
            "size": "md",
            "color": p["text"],
            "wrap": True,
        })

    # 若內容遭截斷，明確提示，避免 Flex 因內容過長整張失敗。
    if len(raw) > total + len(first) + 50:
        contents.append({
            "type": "text",
            "text": "內容較長，已顯示主要資訊。",
            "size": "xs",
            "color": p["sub"],
            "wrap": True,
            "margin": "md",
        })

    shown_name = str(member_name or _notification_member_name() or "").strip()
    if shown_name and not any(("👤" in str(item.get("text", "")) or shown_name in str(item.get("text", ""))) for item in contents if isinstance(item, dict)):
        contents.insert(0, {
            "type": "text",
            "text": f"👤 成員：{shown_name}",
            "size": "sm",
            "weight": "bold",
            "color": p["sub"],
            "wrap": True,
        })

    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": p["head"],
            "paddingAll": "18px",
            "contents": [
                {
                    "type": "text",
                    "text": title,
                    "weight": "bold",
                    "size": "xl",
                    "color": p["text"],
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "18px",
            "backgroundColor": p["card"],
            "contents": contents,
        },
    }
    return FlexSendMessage(alt_text=(alt_text or title)[:400], contents=bubble)



def _stars_bar(score: int):
    score = max(0, min(100, int(score or 0)))
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


def fortune_result_flex(data):
    rows = []
    if not data.get("name") and _notification_member_name():
        data = dict(data)
        data["name"] = _notification_member_name()
    if data.get("used_today"):
        rows.append({
            "type":"box","layout":"vertical","backgroundColor":"#FFF4D8","cornerRadius":"lg","paddingAll":"md",
            "contents":[
                {"type":"text","text":"✅ 今日已使用，明天再來","weight":"bold","size":"sm","color":"#9A6700","wrap":True},
                {"type":"text","text":"🕔 每日 05:00 更新","size":"xs","color":"#7A6A45","margin":"xs"},
            ]
        })
    rows.extend([
        {"type":"text","text":f"👤 {data.get('name','成員')}　{data.get('zodiac','')}","size":"sm","color":"#555555","wrap":True},
        {"type":"text","text":f"{data.get('icon','🔮')} 今日運勢：{data.get('level','平')}","weight":"bold","size":"lg","margin":"md","wrap":True},
        {"type":"text","text":f"🍀 幸運值 {data.get('score',50)}%","weight":"bold","size":"md","margin":"md"},
        {"type":"text","text":_stars_bar(data.get('score',50)),"size":"sm","color":"#6A35A5"},
        {"type":"separator","margin":"lg"},
        {"type":"text","text":f"❤️ 桃花運　{data.get('love','★★★☆☆')}","size":"sm","margin":"md"},
        {"type":"text","text":f"💰 財運　　{data.get('money','★★★☆☆')}","size":"sm"},
        {"type":"text","text":f"💼 工作運　{data.get('work','★★★☆☆')}","size":"sm"},
        {"type":"text","text":f"🎨 幸運色：{data.get('color','彩虹色')}","size":"sm","margin":"md"},
        {"type":"text","text":f"🔢 幸運數字：{data.get('number',7)}　🍀 幸運物：{data.get('item','四葉草')}","size":"sm","wrap":True},
        {"type":"separator","margin":"lg"},
        {"type":"text","text":"🎁 今日實際效果","weight":"bold","size":"md","margin":"md"},
        {"type":"text","text":f"⭐ EXP ×{float(data.get('exp_mult',1)):.2f}\n🌈 彩虹幣 ×{float(data.get('coin_mult',1)):.2f}\n🎡 轉盤稀有率 +{int(data.get('wheel_bonus',0))}%","size":"sm","wrap":True},
        {"type":"text","text":f"💬 {data.get('tip','保持好心情。')}","size":"sm","color":"#5E5368","margin":"lg","wrap":True},
    ])
    if data.get("special"):
        rows.append({"type":"text","text":f"✨ 稀有事件\n{data['special']}","weight":"bold","size":"sm","color":"#B06A00","margin":"md","wrap":True})
    return FlexSendMessage(alt_text="每日運勢結果", contents=_bubble("🔮 豪華每日運勢", "幸運值已直接套用到今日獎勵與轉盤。", rows, ""))


def wheel_result_flex(data):
    if not data.get("name") and _notification_member_name():
        data = dict(data)
        data["name"] = _notification_member_name()
    history = data.get("history") or []
    issued = bool(data.get("issued"))
    rows = [
        {"type":"text","text":f"👤 {data.get('name','成員')}","size":"sm","color":"#666666"},
    ]
    if history:
        rows.append({"type":"text","text":"🎁 今日已抽中獎勵","weight":"bold","size":"lg","align":"center","margin":"lg"})
        for index, item in enumerate(history):
            if index > 0:
                rows.append({"type":"separator","margin":"md"})
            issued_text = "（已發放）" if issued else ""
            rows.extend([
                {"type":"text","text":f"第 {int(item.get('spin', index + 1))} 次｜{item.get('icon','🎁')} {item.get('rarity','普通')}獎勵","weight":"bold","size":"sm","margin":"md","wrap":True},
                {"type":"text","text":f"{item.get('reward','獲得獎勵')} {issued_text}","weight":"bold","size":"md","color":"#6A35A5","margin":"sm","wrap":True},
                {"type":"text","text":f"🌈 彩虹幣：+{int(item.get('coins',0))}　⭐ EXP：+{int(item.get('exp',0))}","size":"sm","wrap":True},
            ])
    else:
        issued_text = "（已發放）" if issued else ""
        rows.extend([
            {"type":"text","text":f"{data.get('icon','🎁')} {data.get('rarity','普通')}獎勵","weight":"bold","size":"xl","align":"center","margin":"lg"},
            {"type":"text","text":f"{data.get('reward','獲得獎勵')} {issued_text}","weight":"bold","size":"lg","color":"#6A35A5","align":"center","margin":"md","wrap":True},
            {"type":"separator","margin":"lg"},
            {"type":"text","text":f"🌈 實得彩虹幣：+{int(data.get('coins',0))}\n⭐ 實得 EXP：+{int(data.get('exp',0))}","size":"sm","margin":"md","wrap":True},
        ])
    rows.extend([
        {"type":"text","text":f"🍀 今日幸運值：{int(data.get('luck_score',50))}%\n🎯 運勢稀有加成：+{int(data.get('luck_bonus',0))}%","size":"sm","margin":"md","wrap":True},
        {"type":"text","text":f"🎡 今日次數：{int(data.get('used',1))}/{int(data.get('max_spins',1))}","size":"sm","weight":"bold","margin":"md"},
    ])
    if issued:
        rows.append({"type":"text","text":"✅ 上述獎勵均已發放，不會重複入帳。","size":"sm","color":"#2E7D32","weight":"bold","margin":"md","wrap":True})
    if data.get("boost"):
        rows.append({"type":"text","text":"🔥 輪盤大暴送進行中，稀有率再提升！","size":"sm","color":"#B06A00","wrap":True})
    for line in data.get("activity_lines") or []:
        rows.append({"type":"text","text":line,"size":"sm","wrap":True})
    return FlexSendMessage(alt_text="豪華幸運轉盤結果", contents=_bubble("🎡 豪華幸運轉盤", "幸運值越高，越容易抽中高稀有獎勵。", rows, ""))



# ===== V5.5.3 全功能單訊息入口（降低洗版） =====
def _compact_home_bubble(show_admin: bool = False):
    rows = [
        _grid_row(_message_action("📅 簽到", "!簽到"), _message_action("👤 我的狀態", "!我的狀態")),
        
        _grid_row(_message_action("🏦 彩虹金庫", "!金庫"), _message_action("🎖️ 我的稱號", "!我的稱號")),
        _grid_row(_message_action("🎂 生日中心", "!生日設定"), _message_action("🎉 目前活動", "!活動資訊")),
        _grid_row(_message_action("📢 最新公告", "!查看公告"), _message_action("📜 群規", "!群規")),
        _grid_row(_message_action("📖 功能說明", "!指令")),
    ]
    if show_admin:
        rows.append(_grid_row(_message_action("🏆 管理排行榜", "!排行榜"), _message_action("👑 管理中心", "!後台")))
    return _bubble(
        "🌈 功能中心",
        "所有主要功能已整合在同一則滑動頁面中。",
        rows,
        "左右滑動即可切換功能分類，不必重複返回首頁。",
    )


def _daily_features_bubble(show_admin: bool = False):
    rows = [
        _grid_row(_message_action("📅 每日簽到", "!簽到"), _message_action("📝 補簽", "!補簽")),
        _grid_row(_message_action("📆 簽到紀錄", "!簽到紀錄"), _message_action("📦 我的任務", "!我的任務")),
        
        _grid_row(_message_action("📋 每日任務", "!每日任務"), _message_action("🗓️ 每週任務", "!每週任務")),
    ]
    if show_admin:
        rows.append(_grid_row(_message_action("🔥 連續簽到榜", "!連續簽到榜"), _message_action("🏅 任務排行", "!任務排行榜")))
    return _bubble("☀️ 每日功能", "簽到、運勢、轉盤與任務集中在此。", rows, "每日功能依系統 05:00 換日。")


def _ranking_features_bubble():
    rows = [
        _grid_row(_message_action("🏆 綜合排行榜", "!排行榜"), _message_action("💬 今日聊天榜", "!今日聊天榜")),
        _grid_row(_message_action("🕘 昨日聊天榜", "!昨日聊天榜"), _message_action("🖼️ 貼圖排行榜", "!貼圖排行榜")),
        _grid_row(_message_action("🔥 連續簽到榜", "!連續簽到榜"), _message_action("📆 累積簽到榜", "!累積簽到榜")),
        _grid_row(_message_action("🌈 彩虹幣排行", "!金幣排行榜"), _message_action("🎯 我的排名", "!我的排名")),
        _grid_row(_message_action("🎉 活動排行", "!活動排行"), _message_action("🏅 任務排行", "!任務排行榜")),
    ]
    return _bubble("🏆 排行榜中心", "所有排行榜集中在同一頁。", rows, "點選後只顯示對應結果卡片。")


def _profile_features_bubble():
    rows = [
        _grid_row(_message_action("👤 我的狀態", "!我的狀態"), _message_action("💎 我的 VIP", "!我的VIP")),
        _grid_row(_message_action("🎖️ 我的稱號", "!我的稱號"), _message_action("🏅 我的徽章", "!我的徽章")),
        _grid_row(_message_action("📚 徽章圖鑑", "!徽章圖鑑"), _message_action("🎂 我的生日", "!我的生日")),
        _grid_row(_message_action("🎂 生日中心", "!生日設定"), _message_action("🎉 今日壽星", "!今日壽星")),
        _grid_row(_message_action("📅 即將生日", "!即將生日"), _message_action("📜 消費紀錄", "!總消費")),
    ]
    return _bubble("👤 個人中心", "個人資料、稱號、徽章與生日集中管理。", rows, "生日設定完成後將鎖定。")


def _activity_features_bubble(show_admin: bool = False):
    rows = [
        _grid_row(_message_action("🎉 活動資訊", "!活動資訊"), _message_action("📋 活動任務", "!活動任務")),
        _grid_row(_message_action("📊 我的活動", "!我的活動"), _message_action("🎒 活動背包", "!活動背包")),
        _grid_row(_message_action("🌈 活動商店", "!活動商店"), _message_action("⏰ 今日狂歡", "!今日活動")),
        _grid_row(_message_action("📢 最新公告", "!查看公告")),
    ]
    if show_admin:
        rows.append(_grid_row(_message_action("🏆 活動排行", "!活動排行")))
    return _bubble("🎉 活動中心", "活動內容、任務、背包與商店集中在此。", rows, "活動商店只在節日或季節活動期間開放。")


def _community_features_bubble():
    rows = [
        _grid_row(_message_action("📢 最新公告", "!查看公告"), _message_action("📜 群規", "!群規")),
        _grid_row(_message_action("📖 基本說明", "!基本指令"), _message_action("🎂 簽到生日說明", "!簽到生日指令")),
        _grid_row(_message_action("🤖 機器人狀態", "!機器人狀態"), _message_action("🎉 活動狀態", "!活動狀態")),
        _grid_row(_message_action("📅 排程狀態", "!排程狀態"), _message_action("📮 完整說明", "!指令")),
    ]
    return _bubble("📚 資訊中心", "公告、群規與系統說明集中在此。", rows, "所有按鈕採 Postback，不會在聊天室顯示指令。")


def _compact_shop_bubble(shop_items, titles, page=1, page_size=6):
    msg = unified_shop_flex(shop_items, titles, page=page, page_size=page_size)
    bubble = msg.contents
    try:
        contents = bubble["body"]["contents"]
        filtered = []
        for item in contents:
            if item.get("type") == "box" and item.get("layout") == "horizontal":
                labels = []
                for btn in item.get("contents", []):
                    action = btn.get("action", {}) if isinstance(btn, dict) else {}
                    labels.append(str(action.get("label") or ""))
                if any(label in ["🌈 活動商店", "🏠 主頁"] for label in labels):
                    continue
            filtered.append(item)
        bubble["body"]["contents"] = filtered
    except Exception:
        pass
    return bubble


def front_portal_flex(show_admin, shop_items, titles, start="home", page_size=6):
    """一則 Carousel 整合所有前台分類與一般商店，最多 12 頁。"""
    entries = [item for item in list(shop_items or []) if str(item.get("category") or "").strip() != "活動"]
    title_count = len(list(titles or []))
    shop_pages = max(1, (len(entries) + title_count + page_size - 1) // page_size)
    fixed = [
        _compact_home_bubble(bool(show_admin)),
        _daily_features_bubble(bool(show_admin)),
    ]
    if show_admin:
        fixed.append(_ranking_features_bubble())
    fixed.extend([
        _profile_features_bubble(),
        _activity_features_bubble(bool(show_admin)),
        _community_features_bubble(),
    ])
    # LINE Carousel 上限 12 頁，保留 6 頁功能分類，其餘最多放 6 頁商店。
    shop_bubbles = [_compact_shop_bubble(entries, titles, page=i, page_size=page_size) for i in range(1, min(shop_pages, 6) + 1)]
    bubbles = (shop_bubbles + fixed) if str(start) == "shop" else (fixed + shop_bubbles)
    return FlexSendMessage(alt_text="Rainbow Life 全功能入口", contents={"type": "carousel", "contents": bubbles[:12]})



def web_admin_entry_flex(group_name: str, web_admin_url: str, role_label: str = "管理員"):
    """V6.0.3：LINE 私訊中的網頁後台快捷入口。"""
    rows = []
    if web_admin_url:
        rows.append(_grid_row(_uri_action("🌐 開啟網頁後台", web_admin_url)))
    rows.append(_grid_row(_message_action("⚙️ 完整管理中心", "!管理中心"), _message_action("👥 切換群組", "!群組列表")))
    return FlexSendMessage(
        alt_text="Rainbow Life 網頁後台入口",
        contents=_bubble(
            "🌈 Rainbow Life 網頁後台",
            f"身分：{role_label}\n管理群組：{group_name}\n\n點擊下方按鈕即可免密碼登入。",
            rows,
            "登入連結為短效連結，請勿轉傳給其他人。",
        ),
    )

def admin_portal_flex(group_name: str, web_admin_url: str = ""):
    """後台所有主要功能整合為一則 Carousel。"""
    overview_rows = []
    if web_admin_url:
        # Always place the real web-login button first and full-width.
        overview_rows.append(_grid_row(_uri_action("🌐 開啟網頁後台", web_admin_url)))
    else:
        # Keep the entry visible for diagnostics instead of silently hiding it.
        overview_rows.append(_grid_row(_message_action("🌐 開啟網頁後台", "網頁後台")))
    overview_rows += [
        _grid_row(_message_action("👥 切換群組", "!群組列表"), _message_action("📊 群組總覽", "!群組總覽")),
        _grid_row(_message_action("👤 成員管理", "!成員管理"), _message_action("🛡️ 管理員管理", "!管理員管理")),
        _grid_row(_message_action("💎 VIP 管理", "!VIP管理"), _message_action("🔇 禁言管理", "!禁言管理")),
        _grid_row(_message_action("🛍️ 商店管理", "!商店管理"), _message_action("⚙️ 設定中心", "!設定中心")),
    ]
    member_rows = [
        _grid_row(_message_action("🔎 搜尋成員", "!搜尋成員說明"), _message_action("📋 成員列表", "!成員管理")),
        _grid_row(_message_action("🔇 禁言管理", "!禁言管理"), _message_action("📋 禁言名單", "!禁言名單")),
        _grid_row(_message_action("🛡️ 管理員管理", "!管理員管理"), _message_action("📜 管理紀錄", "!管理紀錄")),
        _grid_row(_message_action("💎 VIP 管理", "!VIP管理"), _message_action("📊 消費查詢", "!消費管理")),
    ]
    economy_rows = [
        _grid_row(_message_action("🌈 彩虹幣管理", "!彩虹幣管理"), _message_action("⭐ EXP 管理", "!經驗管理")),
        _grid_row(_message_action("💎 VIP 管理", "!VIP管理"), _message_action("🏦 金庫管理", "!金庫管理")),
        _grid_row(_message_action("📜 消費明細", "!消費管理"), _message_action("🎖️ 稱號管理", "!稱號管理")),
        _grid_row(_message_action("🏅 徽章管理", "!徽章管理"), _message_action("🎁 發放福利", "!福利管理")),
    ]
    shop_rows = [
        _grid_row(_message_action("🛍️ 商品管理", "!商店管理"), _message_action("📦 商品列表", "!商品管理列表")),
        _grid_row(_message_action("➕ 新增商品", "!新增商品說明"), _message_action("✏️ 修改商品", "!修改商品說明")),
        _grid_row(_message_action("💲 修改價格", "!修改商品價格說明"), _message_action("📤 上架商品", "!上架商品說明")),
        _grid_row(_message_action("📥 下架商品", "!下架商品說明"), _message_action("🗑️ 刪除商品", "!刪除商品說明")),
    ]
    notice_rows = [
        _grid_row(_message_action("📢 公告設定", "!公告設定"), _message_action("👁️ 查看公告", "!查看公告")),
        _grid_row(_message_action("✏️ 設定公告", "!設定公告說明"), _message_action("⏰ 公告時間", "!設定公告時間說明")),
        _grid_row(_message_action("📤 立即推播", "!立即推播公告"), _message_action("🗑️ 刪除公告", "!刪除公告")),
        _grid_row(_message_action("📜 群規設定", "!群組資料設定"), _message_action("🎉 活動設定", "!活動設定")),
    ]
    task_rows = [
        _grid_row(_message_action("📋 任務管理", "!任務管理"), _message_action("📅 簽到設定", "!簽到設定")),
        _grid_row(_message_action("🎉 活動設定", "!活動設定"), _message_action("⏰ 排程中心", "!排程中心")),
        _grid_row(_message_action("🔥 狂歡控制", "!活動設定"), _message_action("🎁 寶箱控制", "!活動設定")),
        _grid_row(_message_action("🎡 轉盤設定", "!活動設定"), _message_action("🔮 運勢設定", "!設定中心")),
    ]
    system_rows = [
        _grid_row(_message_action("🤖 機器人模式", "!機器人模式"), _message_action("⚙️ 設定中心", "!設定中心")),
        _grid_row(_message_action("📅 簽到設定", "!簽到設定"), _message_action("💎 VIP 設定", "!VIP設定")),
        _grid_row(_message_action("📢 公告設定", "!公告設定"), _message_action("🎉 活動設定", "!活動設定")),
        _grid_row(_message_action("📅 排程狀態", "!排程狀態"), _message_action("🔄 立即檢查", "!立即檢查排程")),
    ]
    bubbles = [
        _bubble("👑 管理中心", f"目前管理群組：{group_name}", overview_rows, "左右滑動可開啟所有後台分類。"),
        _bubble("👥 成員與權限", "成員、禁言、管理員與 VIP。", member_rows, "設定功能可點按後直接在下方輸入。"),
        _bubble("💰 資源與福利", "彩虹幣、EXP、VIP、金庫、稱號與徽章。", economy_rows, "權限不足的管理員會被自動阻擋。"),
        _bubble("🛍️ 商店後台", "一般商店商品完整管理。", shop_rows, "活動商店由節日與季節自動替換，不可手動新增。"),
        _bubble("📢 公告與活動", "公告、群規與活動管理。", notice_rows, "所有主動通知受機器人模式控制。"),
        _bubble("📋 任務與排程", "任務、簽到、狂歡與排程管理。", task_rows, "常用活動控制集中在此。"),
        _bubble("⚙️ 系統設定", "機器人模式與各項系統設定。", system_rows, "維護模式下僅群長可操作。"),
    ]
    return FlexSendMessage(alt_text="Rainbow Life 完整管理中心", contents={"type": "carousel", "contents": bubbles})

