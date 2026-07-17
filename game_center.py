"""Rainbow Bot V18 Step 1：遊戲中心完整入口與命令路由。"""
from linebot.models import FlexSendMessage, TextSendMessage
from game_database import ensure_game_center_tables, seed_game_settings, get_game_setting, set_game_setting
from game_room import active_room, room_players, join_room, leave_room, dissolve_room
from game_rank import player_game_stats, game_leaderboard
from admin import is_owner, is_admin
from game_games import (
    configure_line_bot_api, ultimate_lobby, dice_lobby, quiz_lobby,
    create_ultimate, create_dice, create_quiz, start_current_game, waiting_room_card,
    handle_ultimate_guess, handle_dice_roll, slots_card, play_slots,
    handle_quiz_answer, add_test_bots_to_waiting_room,
)

def _can_manage_games(group_id, user_id):
    """避免權限查詢異常時讓遊戲指令整段無回應。"""
    try:
        return bool(is_owner(group_id, user_id) or is_admin(group_id, user_id))
    except Exception:
        return False



def _game_is_enabled(group_id, setting_key):
    return (
        get_game_setting(group_id, "enabled", "1") == "1"
        and get_game_setting(group_id, setting_key, "1") == "1"
    )


GAME_LABELS = {
    "ultimate_password": "🎯 終極密碼",
    "dice_pk": "🎲 骰子 PK",
    "rainbow_slots": "🎰 彩虹拉霸",
    "quick_quiz": "🧠 快問快答",
}


def ensure_game_center(group_id):
    ensure_game_center_tables()
    if group_id and group_id != "PRIVATE":
        seed_game_settings(group_id)


def _button(label, command, style="secondary", color=None):
    row = {
        "type": "button", "style": style, "height": "sm",
        "action": {"type": "postback", "label": label, "data": f"cmd={command}", "displayText": label},
    }
    if color:
        row["color"] = color
    return row


def game_center_flex(group_id, user_id=""):
    ensure_game_center(group_id)
    room = active_room(group_id)
    room_text = "目前沒有遊戲室"
    if room:
        room_text = f"{GAME_LABELS.get(room['game_type'],room['game_type'])}・{room['status']}・房號 #{room['room_code']}"
    body = [
        {"type": "text", "text": "🎮 Rainbow 遊戲中心", "size": "xl", "weight": "bold", "color": "#FFFFFF"},
        {"type": "text", "text": "終極密碼・骰子 PK・彩虹拉霸・快問快答", "size": "xs", "color": "#CDE7FF", "margin": "sm"},
        {"type": "separator", "margin": "lg", "color": "#5379A7"},
        {"type": "text", "text": room_text, "size": "sm", "color": "#FFFFFF", "margin": "lg", "wrap": True},
        {"type": "box", "layout": "vertical", "spacing": "sm", "margin": "lg", "contents": [
            _button("🎯 終極密碼", "終極密碼大廳", "primary", "#FF6FAF"),
            _button("🎲 骰子 PK", "骰子PK大廳"),
            _button("🎰 彩虹拉霸", "彩虹拉霸"),
            _button("🧠 快問快答", "快問快答大廳"),
        ]},
        {"type": "box", "layout": "horizontal", "spacing": "sm", "margin": "md", "contents": [
            _button("📊 我的戰績", "我的遊戲戰績"), _button("🏆 排行榜", "遊戲排行榜")
        ]},
        {"type": "box", "layout": "horizontal", "spacing": "sm", "margin": "sm", "contents": [
            _button("🏅 成就", "遊戲成就"), _button("📖 規則", "遊戲規則")
        ]},
    ]
    if _can_manage_games(group_id, user_id):
        test_enabled = get_game_setting(group_id, "test_mode_enabled", "0") == "1"
        body.append({
            "type": "box", "layout": "vertical", "spacing": "sm", "margin": "lg",
            "paddingAll": "10px", "cornerRadius": "14px",
            "backgroundColor": "#173A65",
            "contents": [
                {"type": "text",
                 "text": "🧪 單人測試模式：" + ("已開啟" if test_enabled else "已關閉"),
                 "size": "sm", "weight": "bold", "color": "#FFFFFF"},
                _button(
                    "關閉測試模式" if test_enabled else "開啟測試模式",
                    "測試模式 關" if test_enabled else "測試模式 開",
                    "primary", "#8B72FF"
                ),
                _button("🤖 加入測試玩家", "加入測試玩家"),
            ],
        })
    return FlexSendMessage(alt_text="🎮 Rainbow 遊戲中心", contents={
        "type": "bubble", "styles": {"body": {"backgroundColor": "#102B4E"}},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "18px", "contents": body},
    })


def room_status_flex(group_id):
    room = active_room(group_id)
    if not room:
        return TextSendMessage(text="🎮 目前沒有等待中或進行中的遊戲室。")
    players = room_players(room["id"])
    names = "\n".join(f"{i}. {'👑 ' if int(r.get('is_host') or 0) else ''}{r.get('player_name') or '玩家'}" for i,r in enumerate(players,1)) or "尚無玩家"
    return TextSendMessage(text=(
        f"{GAME_LABELS.get(room['game_type'],room['game_type'])}\n\n房號：#{room['room_code']}\n"
        f"狀態：{room['status']}\n玩家：{len(players)} 人\n\n{names}"
    ))


def stats_message(group_id, user_id):
    row = player_game_stats(group_id, user_id, "all")
    games, wins, losses = int(row.get("games_played") or 0), int(row.get("wins") or 0), int(row.get("losses") or 0)
    rate = round(wins*100/games,1) if games else 0
    return TextSendMessage(text=(f"📊 我的遊戲戰績\n\n🎮 總場次：{games}\n🏆 勝場：{wins}\n😭 敗場：{losses}\n📈 勝率：{rate}%\n🔥 最長連勝：{int(row.get('longest_streak') or 0)}"))


def leaderboard_message(group_id):
    rows = game_leaderboard(group_id, "all", 10)
    if not rows:
        return TextSendMessage(text="🏆 目前還沒有遊戲排行榜資料。")
    medals = ["🥇","🥈","🥉"]
    lines = ["🏆 遊戲排行榜", ""]
    for i,row in enumerate(rows,1):
        mark = medals[i-1] if i<=3 else f"{i}."
        lines.append(f"{mark} {row.get('player_name') or '玩家'}｜{int(row.get('wins') or 0)} 勝｜最長 {int(row.get('longest_streak') or 0)} 連勝")
    return TextSendMessage(text="\n".join(lines))


def handle_game_center_command(text, group_id, user_id, display_name):
    if group_id == "PRIVATE":
        return None
    text = str(text or "").strip()
    compact = text.replace(" ", "")
    aliases = {
        "測試模式開": "測試模式 開", "開啟測試模式": "測試模式 開",
        "遊戲測試模式開": "測試模式 開", "遊戲測試開": "測試模式 開",
        "測試模式關": "測試模式 關", "關閉測試模式": "測試模式 關",
        "遊戲測試模式關": "測試模式 關", "遊戲測試關": "測試模式 關",
    }
    text = aliases.get(compact, text)
    ensure_game_center(group_id)

    # 進行中的終極密碼接受純數字輸入。
    guess_message = handle_ultimate_guess(group_id, user_id, text)
    if guess_message is not None:
        return guess_message

    if text in {"遊戲", "遊戲中心", "小遊戲", "Rainbow遊戲中心"}:
        return game_center_flex(group_id, user_id)
    if text in {"遊戲診斷", "遊戲測試診斷"}:
        enabled = get_game_setting(group_id, "enabled", "1")
        test = get_game_setting(group_id, "test_mode_enabled", "0")
        role = "管理者" if _can_manage_games(group_id, user_id) else "一般成員"
        return TextSendMessage(text=f"✅ V18.4 遊戲模組已載入\n身分：{role}\n遊戲中心：{enabled}\n測試模式：{test}")
    if text == "測試模式狀態":
        if not _can_manage_games(group_id, user_id):
            return TextSendMessage(text="❌ 只有群長或管理員可以查看測試模式。")
        enabled = get_game_setting(group_id, "test_mode_enabled", "0") == "1"
        return TextSendMessage(text=f"🧪 單人測試模式：{'已開啟' if enabled else '已關閉'}")
    if text in {"測試模式 開", "測試模式 關"}:
        if not _can_manage_games(group_id, user_id):
            return TextSendMessage(text="❌ 只有群長可以切換遊戲測試模式。")
        enabled = text.endswith("開")
        set_game_setting(group_id, "test_mode_enabled", "1" if enabled else "0")
        return TextSendMessage(text=(
            "🧪 單人測試模式已開啟\n\n"
            "建立終極密碼、骰子 PK 或快問快答時，"
            "系統會自動加入 3 位測試玩家並代替操作。\n"
            "測試玩家不會獲得獎勵，也不會進入正式排行榜。"
            if enabled else
            "✅ 單人測試模式已關閉。\n之後建立的房間不會再自動加入測試玩家。"
        ))
    if text == "加入測試玩家":
        if not _can_manage_games(group_id, user_id):
            return TextSendMessage(text="❌ 只有群長可以加入測試玩家。")
        if get_game_setting(group_id, "test_mode_enabled", "0") != "1":
            return TextSendMessage(text="❌ 請先開啟單人測試模式。")
        ok, message = add_test_bots_to_waiting_room(group_id)
        if ok:
            return waiting_room_card(group_id, "🧪 " + message)
        return TextSendMessage(text="❌ " + message)
    if text in {"目前遊戲室", "遊戲室", "查看遊戲室"}:
        return room_status_flex(group_id)
    if text == "終極密碼大廳":
        return ultimate_lobby() if _game_is_enabled(group_id, "ultimate_password_enabled") else TextSendMessage(text="❌ 終極密碼目前未開放。")
    if text == "骰子PK大廳":
        return dice_lobby() if _game_is_enabled(group_id, "dice_pk_enabled") else TextSendMessage(text="❌ 骰子 PK 目前未開放。")
    if text == "快問快答大廳":
        return quiz_lobby() if _game_is_enabled(group_id, "quick_quiz_enabled") else TextSendMessage(text="❌ 快問快答目前未開放。")
    if text == "彩虹拉霸":
        return slots_card(group_id, user_id) if _game_is_enabled(group_id, "rainbow_slots_enabled") else TextSendMessage(text="❌ 彩虹拉霸目前未開放。")
    if text == "開始拉霸":
        return play_slots(group_id, user_id, display_name) if _game_is_enabled(group_id, "rainbow_slots_enabled") else TextSendMessage(text="❌ 彩虹拉霸目前未開放。")

    if text.startswith("建立終極密碼 "):
        if not _game_is_enabled(group_id, "ultimate_password_enabled"):
            return TextSendMessage(text="❌ 終極密碼目前未開放。")
        try: return create_ultimate(group_id, user_id, display_name, int(text.split()[-1]))
        except Exception: return TextSendMessage(text="❌ 範圍設定錯誤。")
    if text.startswith("建立骰子PK "):
        if not _game_is_enabled(group_id, "dice_pk_enabled"):
            return TextSendMessage(text="❌ 骰子 PK 目前未開放。")
        try:
            _, dice, rounds = text.rsplit(" ", 2)
            return create_dice(group_id, user_id, display_name, int(dice), int(rounds))
        except Exception: return TextSendMessage(text="❌ 骰子設定錯誤。")
    if text.startswith("建立快問快答 "):
        if not _game_is_enabled(group_id, "quick_quiz_enabled"):
            return TextSendMessage(text="❌ 快問快答目前未開放。")
        try:
            parts = text.split()
            return create_quiz(group_id, user_id, display_name, parts[-2], int(parts[-1]))
        except Exception: return TextSendMessage(text="❌ 題庫設定錯誤。")

    if text == "加入遊戲":
        ok,msg,_ = join_room(group_id,user_id,display_name)
        if ok:
            return waiting_room_card(group_id, "✅ " + msg)
        return TextSendMessage(text="❌ " + msg)
    if text == "離開遊戲":
        ok,msg = leave_room(group_id,user_id)
        return TextSendMessage(text=("✅ " if ok else "❌ ")+msg)
    if text == "解散遊戲室":
        ok,msg = dissolve_room(group_id,user_id)
        return TextSendMessage(text=("✅ " if ok else "❌ ")+msg)
    if text == "開始遊戲": return start_current_game(group_id,user_id)
    if text == "骰子擲骰": return handle_dice_roll(group_id,user_id)
    if text.startswith("快問作答 "): return handle_quiz_answer(group_id,user_id,text.split()[-1])

    if text == "我的遊戲戰績": return stats_message(group_id,user_id)
    if text == "遊戲排行榜": return leaderboard_message(group_id)
    if text == "遊戲成就": return TextSendMessage(text="🏅 遊戲成就會依場次、勝場與連勝自動解鎖。")
    if text == "遊戲規則":
        return TextSendMessage(text=(
            "📖 遊戲中心規則\n\n• 多人遊戲同群同時只能一間房\n• 10 分鐘未開始自動解散\n"
            "• 遊戲開始後不能中途加入\n• 勝利固定獎勵，失敗固定懲罰\n"
            "• 每日資源獎勵達上限後仍可遊玩並累積戰績\n• 拉霸每人每天 3 次"
        ))
    return None
