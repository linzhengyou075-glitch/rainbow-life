"""Rainbow Bot V18 Step 1：四款遊戲完整玩法。"""
import json
import random
import threading
from datetime import datetime, timedelta, timezone

from linebot.models import FlexSendMessage, TextSendMessage

from database import get_connection
from game_database import ensure_game_center_tables, get_game_setting
from game_room import active_room, room_players, create_room, join_room, start_room, dissolve_room
from game_reward import apply_game_result, daily_status, game_date
from game_achievement import unlock_available_game_achievements

TW = timezone(timedelta(hours=8))
_LINE_API = None
_TIMERS = {}
_EXPIRY_TIMERS = {}
_TIMER_LOCK = threading.Lock()


TEST_BOT_PREFIX = "__RAINBOW_TEST_BOT__"
TEST_BOT_NAMES = ["🤖 彩虹測試員 A", "🤖 彩虹測試員 B", "🤖 彩虹測試員 C"]


def _is_test_bot(user_id):
    return str(user_id or "").startswith(TEST_BOT_PREFIX)


def _test_mode_enabled(group_id):
    return get_game_setting(group_id, "test_mode_enabled", "0") == "1"


def _add_test_bots(room):
    """測試模式建立多人房間時，自動加入 3 位不計獎勵的模擬玩家。"""
    if not room or not _test_mode_enabled(room["group_id"]):
        return 0
    existing = {str(row["user_id"]) for row in room_players(room["id"])}
    conn = get_connection()
    added = 0
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT COALESCE(MAX(seat_no),0) AS max_seat "
                "FROM game_room_players WHERE room_id=%s",
                (room["id"],),
            )
            seat = int((c.fetchone() or {}).get("max_seat") or 0)
            for index, name in enumerate(TEST_BOT_NAMES, start=1):
                user_id = f"{TEST_BOT_PREFIX}{index}"
                if user_id in existing:
                    continue
                seat += 1
                c.execute("""
                    INSERT INTO game_room_players(
                        room_id,group_id,user_id,player_name,seat_no,
                        is_host,is_spectator,is_ready,turn_done
                    ) VALUES(%s,%s,%s,%s,%s,0,0,1,0)
                    ON CONFLICT(room_id,user_id) DO NOTHING
                """, (room["id"], room["group_id"], user_id, name, seat))
                added += c.rowcount
        conn.commit()
    finally:
        conn.close()
    return added


def add_test_bots_to_waiting_room(group_id):
    room = active_room(group_id)
    if not room:
        return False, "目前沒有等待中的遊戲室。"
    if room.get("status") != "waiting":
        return False, "遊戲已開始，不能再加入測試玩家。"
    count = _add_test_bots(room)
    return True, f"已加入 {count} 位測試玩家。" if count else "測試玩家已在房間內。"


def _schedule_test_bot_action(room_id, delay=1.2):
    """輪到模擬玩家時自動操作，方便群長單人測試多人遊戲。"""
    def callback():
        room = _db_one("SELECT * FROM game_rooms WHERE id=%s", (room_id,))
        if not room or room.get("status") != "playing":
            return
        players = room_players(room_id)
        if not players:
            return
        state = _load_state(room)

        if room.get("game_type") == "ultimate_password":
            current = players[int(state.get("turn_index") or 0) % len(players)]
            if not _is_test_bot(current.get("user_id")):
                return
            low, high = int(state.get("low") or 1), int(state.get("high") or 100)
            secret = int(state.get("secret") or low)
            # 測試玩家多數猜區間中間，偶爾猜中，方便測區間與結算。
            if random.random() < 0.16:
                value = secret
            else:
                value = (low + high) // 2
                if value == secret and low < high:
                    value = value - 1 if value > low else value + 1
            message = handle_ultimate_guess(
                room["group_id"], current["user_id"], str(value)
            )
            if message is not None:
                _push(room["group_id"], message)

        elif room.get("game_type") == "dice_pk":
            current = players[int(state.get("turn_index") or 0) % len(players)]
            if not _is_test_bot(current.get("user_id")):
                return
            message = handle_dice_roll(room["group_id"], current["user_id"])
            if message is not None:
                _push(room["group_id"], message)

    timer = threading.Timer(max(0.3, float(delay)), callback)
    timer.daemon = True
    timer.start()


def _schedule_quiz_test_answers(room_id):
    """每題讓測試玩家在不同秒數自動選答案，並保留真人作答空間。"""
    room = _db_one("SELECT * FROM game_rooms WHERE id=%s", (room_id,))
    if not room or not _test_mode_enabled(room.get("group_id")):
        return
    bots = [
        row for row in room_players(room_id)
        if _is_test_bot(row.get("user_id")) and not int(row.get("is_spectator") or 0)
    ]
    choices = ["A", "B", "C", "D"]

    for index, bot in enumerate(bots):
        def callback(bot_user_id=str(bot["user_id"]), order=index):
            current = _db_one("SELECT * FROM game_rooms WHERE id=%s", (room_id,))
            if not current or current.get("status") != "playing":
                return
            state = _load_state(current)
            question = _quiz_question(current, state)
            if not question:
                return
            # A 測試員較常答對，其餘會隨機答錯，用來測積分與排名。
            if order == 0 or random.random() < 0.45:
                answer = str(question.get("correct_option") or "A")
            else:
                wrong = [x for x in choices if x != str(question.get("correct_option") or "A")]
                answer = random.choice(wrong)
            message = handle_quiz_answer(current["group_id"], bot_user_id, answer)
            # 只有當最後一位作答觸發下一題／結算時才推播卡片，避免洗版。
            if isinstance(message, FlexSendMessage):
                _push(current["group_id"], message)

        timer = threading.Timer(1.3 + index * 0.8, callback)
        timer.daemon = True
        timer.start()



GAME_LABELS = {
    "ultimate_password": "🎯 終極密碼",
    "dice_pk": "🎲 骰子 PK",
    "quick_quiz": "🧠 快問快答",
}


def configure_line_bot_api(api):
    global _LINE_API
    _LINE_API = api


def _db_one(sql, params=()):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(sql, params)
            return c.fetchone()
    finally:
        conn.close()


def _db_all(sql, params=()):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(sql, params)
            return c.fetchall() or []
    finally:
        conn.close()


def _db_exec(sql, params=()):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _load_state(room):
    try:
        return json.loads(room.get("state_json") or "{}")
    except Exception:
        return {}


def _save_state(room_id, state):
    _db_exec(
        "UPDATE game_rooms SET state_json=%s WHERE id=%s",
        (json.dumps(state, ensure_ascii=False), room_id),
    )


def _finish_room(room_id):
    _cancel_room_expiry(room_id)
    _db_exec(
        "UPDATE game_rooms SET status='finished',ended_at=CURRENT_TIMESTAMP WHERE id=%s",
        (room_id,),
    )
    _cancel_timer(room_id)


def _push(group_id, message):
    if _LINE_API and group_id and group_id != "PRIVATE":
        try:
            _LINE_API.push_message(group_id, message)
        except Exception:
            pass


def _timer_key(room_id):
    return f"room:{room_id}"


def _cancel_timer(room_id):
    key = _timer_key(room_id)
    with _TIMER_LOCK:
        timer = _TIMERS.pop(key, None)
    if timer:
        timer.cancel()


def _schedule(room_id, seconds, callback):
    _cancel_timer(room_id)
    key = _timer_key(room_id)
    timer = threading.Timer(seconds, callback)
    timer.daemon = True
    with _TIMER_LOCK:
        _TIMERS[key] = timer
    timer.start()


def _cancel_room_expiry(room_id):
    with _TIMER_LOCK:
        timer = _EXPIRY_TIMERS.pop(int(room_id), None)
    if timer:
        timer.cancel()


def _schedule_room_expiry(room):
    """建立房間後排程真正的 10 分鐘自動解散，不必等下一個指令觸發清理。"""
    _cancel_room_expiry(room["id"])
    try:
        expires_at = room.get("expires_at")
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        now = datetime.now(expires_at.tzinfo or TW) if expires_at else datetime.now(TW)
        seconds = max(1.0, (expires_at - now).total_seconds()) if expires_at else 600.0
    except Exception:
        seconds = 600.0

    def callback():
        current = _db_one("SELECT * FROM game_rooms WHERE id=%s", (room["id"],))
        if not current or current.get("status") != "waiting":
            return
        _db_exec(
            "UPDATE game_rooms SET status='expired',ended_at=CURRENT_TIMESTAMP "
            "WHERE id=%s AND status='waiting'",
            (room["id"],),
        )
        _push(
            room["group_id"],
            TextSendMessage(
                text=(
                    "⏰ 遊戲室已自動解散\n\n"
                    "此房間建立後 10 分鐘內未開始，已釋放遊戲室。\n"
                    "群友現在可以重新建立新的遊戲。"
                )
            ),
        )
        with _TIMER_LOCK:
            _EXPIRY_TIMERS.pop(int(room["id"]), None)

    timer = threading.Timer(seconds, callback)
    timer.daemon = True
    with _TIMER_LOCK:
        _EXPIRY_TIMERS[int(room["id"])] = timer
    timer.start()


def _button(label, command, style="secondary", color=None):
    row = {
        "type": "button", "style": style, "height": "sm",
        "action": {"type": "postback", "label": label, "data": f"cmd={command}", "displayText": label},
    }
    if color:
        row["color"] = color
    return row


def _card(title, subtitle, body_lines=None, buttons=None, accent="#8A5CFF"):
    contents = [
        {"type": "text", "text": title, "size": "xl", "weight": "bold", "color": "#FFFFFF", "wrap": True},
        {"type": "text", "text": subtitle, "size": "xs", "color": "#D7E9FF", "margin": "sm", "wrap": True},
        {"type": "separator", "margin": "lg", "color": "#55769B"},
    ]
    for line in body_lines or []:
        contents.append({"type": "text", "text": str(line), "size": "sm", "color": "#FFFFFF", "margin": "md", "wrap": True})
    if buttons:
        contents.append({"type": "box", "layout": "vertical", "spacing": "sm", "margin": "lg", "contents": buttons})
    return FlexSendMessage(
        alt_text=title,
        contents={
            "type": "bubble",
            "styles": {"body": {"backgroundColor": "#102B4E"}},
            "body": {"type": "box", "layout": "vertical", "paddingAll": "18px", "contents": contents},
        },
    )


def _player_name(group_id, user_id):
    row = _db_one("SELECT COALESCE(name,'玩家') AS name FROM players WHERE group_id=%s AND user_id=%s", (group_id, user_id)) or {}
    return str(row.get("name") or "玩家")


def _player_map(room_id):
    return {str(row["user_id"]): row for row in room_players(room_id)}


def _result_text(result):
    if result.get("reward_locked"):
        return "今日遊戲獎勵已達上限，本場仍計入戰績。"
    c = int(result.get("coin_delta") or 0)
    e = int(result.get("exp_delta") or 0)
    return f"🌈 {c:+d} 彩虹幣　⭐ {e:+d} EXP"


def _settle_players(room, winner_ids, scores=None):
    scores = scores or {}
    results = []
    for row in room_players(room["id"]):
        if int(row.get("is_spectator") or 0):
            continue
        uid = str(row["user_id"])
        won = uid in {str(x) for x in winner_ids}
        if _is_test_bot(uid):
            result = {
                "coin_delta": 0, "exp_delta": 0,
                "reward_locked": True, "activity_gain": 0,
            }
            achievements = []
        else:
            result = apply_game_result(
                room["group_id"], uid, row.get("player_name") or "玩家",
                room["game_type"], won, int(scores.get(uid, 0)), room.get("room_code") or "",
            )
            achievements = unlock_available_game_achievements(room["group_id"], uid)
        results.append((row.get("player_name") or "玩家", won, result, achievements))
    return results


# ── 遊戲大廳 ────────────────────────────────────────────────────────────────
def ultimate_lobby():
    return _card(
        "🎯 終極密碼", "建立房間後選擇範圍，玩家加入後由房主開始。",
        ["• 每位玩家 10 秒", "• 開始時隨機順序", "• 系統自動提示最新區間"],
        [
            _button("建立 1～100", "建立終極密碼 100", "primary", "#FF6FAF"),
            _button("建立 1～500", "建立終極密碼 500"),
            _button("建立 1～1000", "建立終極密碼 1000"),
            _button("查看目前遊戲室", "目前遊戲室"),
        ],
    )


def dice_lobby():
    return _card(
        "🎲 骰子 PK", "選擇骰子數與回合數後建立多人遊戲室。",
        ["• 每回合 10 秒按下擲骰", "• 平手冠軍共同獲勝", "• 累積總分最高者勝"],
        [
            _button("1 顆・1 回合", "建立骰子PK 1 1", "primary", "#7D7CFF"),
            _button("2 顆・3 回合", "建立骰子PK 2 3"),
            _button("3 顆・5 回合", "建立骰子PK 3 5"),
            _button("查看目前遊戲室", "目前遊戲室"),
        ],
    )


def quiz_lobby():
    return _card(
        "🧠 快問快答", "多元題庫、按鍵作答，每題 15 秒。",
        ["題庫：Pokémon、生活、美食、科技、娛樂、世界知識", "答對 +10 分，最快答對額外 +2 分"],
        [
            _button("建立 5 題綜合場", "建立快問快答 綜合 5", "primary", "#28B8A8"),
            _button("建立 10 題 Pokémon 場", "建立快問快答 Pokémon 10"),
            _button("建立 10 題生活場", "建立快問快答 生活百科 10"),
            _button("查看目前遊戲室", "目前遊戲室"),
        ],
    )


def waiting_room_card(group_id, prefix=""):
    room = active_room(group_id)
    if not room:
        return TextSendMessage(text="🎮 目前沒有等待中的遊戲室。")
    if room.get("status") != "waiting":
        return TextSendMessage(text="🎮 遊戲已開始，無法再加入。")
    extra = prefix
    try:
        settings = json.loads(room.get("settings_json") or "{}")
        if room.get("game_type") == "ultimate_password":
            extra = (prefix + "\n" if prefix else "") + f"數字範圍：{settings.get('min',1)}～{settings.get('max',100)}"
        elif room.get("game_type") == "dice_pk":
            extra = (prefix + "\n" if prefix else "") + f"每人 {settings.get('dice_count',1)} 顆骰子・{settings.get('rounds',1)} 回合"
        elif room.get("game_type") == "quick_quiz":
            extra = (prefix + "\n" if prefix else "") + f"題庫：{settings.get('category','綜合')}・{settings.get('count',5)} 題"
    except Exception:
        pass
    return _room_waiting_card(room, extra)



# ── 終極密碼 ────────────────────────────────────────────────────────────────
def create_ultimate(group_id, user_id, name, max_value):
    max_value = max(10, min(int(max_value), 100000))
    ok, msg, room = create_room(group_id, "ultimate_password", user_id, name, {"min": 1, "max": max_value})
    if not ok:
        return TextSendMessage(text=f"❌ {msg}")
    state = {"secret": random.randint(1, max_value), "low": 1, "high": max_value, "turn_index": 0, "guessed": [], "deadline": ""}
    _save_state(room["id"], state)
    _add_test_bots(room)
    _schedule_room_expiry(room)
    return _room_waiting_card(room, f"數字範圍：1～{max_value}")


def _room_waiting_card(room, extra=""):
    players = room_players(room["id"])
    names = "、".join(("👑 " if int(p.get("is_host") or 0) else "") + str(p.get("player_name") or "玩家") for p in players)
    return _card(
        f"{GAME_LABELS.get(room['game_type'], room['game_type'])} 遊戲室",
        f"房號 #{room['room_code']}｜10 分鐘未開始自動解散",
        [extra, f"玩家（{len(players)}）：{names}", "等待房主開始…"],
        [
            _button("➕ 加入遊戲", "加入遊戲", "primary", "#2ABF9E"),
            _button("▶️ 房主開始", "開始遊戲"),
            _button("🚪 離開遊戲", "離開遊戲"),
            _button("❌ 解散房間", "解散遊戲室"),
        ],
    )


def start_current_game(group_id, user_id):
    room = active_room(group_id)
    if not room:
        return TextSendMessage(text="❌ 目前沒有遊戲室。")
    ok, msg, room = start_room(group_id, user_id, 2)
    if not ok:
        return TextSendMessage(text=f"❌ {msg}")
    _cancel_room_expiry(room["id"])
    if room["game_type"] == "ultimate_password":
        state = _load_state(room)
        state["turn_index"] = 0
        _save_state(room["id"], state)
        message = _ultimate_turn_message(room, state)
        _schedule_ultimate_timeout(room["id"])
        return message
    if room["game_type"] == "dice_pk":
        state = _load_state(room)
        state.update({"round": 1, "turn_index": 0, "scores": {}, "rolls": {}})
        _save_state(room["id"], state)
        message = _dice_turn_message(room, state)
        _schedule_dice_timeout(room["id"])
        return message
    if room["game_type"] == "quick_quiz":
        return _start_quiz(room)
    return TextSendMessage(text="❌ 尚未支援此遊戲。")


def _ultimate_turn_message(room, state, prefix=""):
    players = room_players(room["id"])
    if not players:
        return TextSendMessage(text="遊戲室沒有玩家。")
    index = int(state.get("turn_index") or 0) % len(players)
    current = players[index]
    state["deadline"] = (datetime.now(TW) + timedelta(seconds=10)).isoformat()
    _save_state(room["id"], state)
    order = " → ".join(str(p.get("player_name") or "玩家") for p in players)
    if _is_test_bot(current.get("user_id")):
        _schedule_test_bot_action(room["id"])
    return _card(
        "🎯 終極密碼進行中", f"玩家順序：{order}",
        ([prefix] if prefix else []) + [
            f"目前區間：{int(state['low'])} ～ {int(state['high'])}",
            f"👉 輪到：{current.get('player_name') or '玩家'}",
            "⏳ 請在 10 秒內直接輸入數字",
        ],
    )


def handle_ultimate_guess(group_id, user_id, text):
    room = active_room(group_id)
    if not room or room["game_type"] != "ultimate_password" or room["status"] != "playing":
        return None
    if not str(text).strip().isdigit():
        return None
    players = room_players(room["id"])
    if not players:
        _finish_room(room["id"])
        return TextSendMessage(text="❌ 遊戲室已沒有玩家，遊戲自動結束。")
    state = _load_state(room)
    current = players[int(state.get("turn_index") or 0) % len(players)]
    if str(current["user_id"]) != str(user_id):
        return TextSendMessage(text=f"⏳ 現在輪到 {current.get('player_name') or '玩家'}，請等待你的回合。")
    value = int(str(text).strip())
    low, high = int(state["low"]), int(state["high"])
    if value < low or value > high:
        return TextSendMessage(text=f"❌ 不在目前區間內！\n只能猜 {low}～{high}，本回合不換人。")
    if value in set(state.get("guessed") or []):
        return TextSendMessage(text=f"❌ {value} 已經猜過，請重新輸入。")
    state.setdefault("guessed", []).append(value)
    secret = int(state["secret"])
    if value == secret:
        _cancel_timer(room["id"])
        winner = str(user_id)
        results = _settle_players(room, [winner])
        _finish_room(room["id"])
        lines = [f"🎉 {current.get('player_name') or '玩家'} 猜中答案 {secret}！"]
        for name, won, result, achievements in results:
            lines.append(f"{'🏆' if won else '😭'} {name}：{_result_text(result)}")
            if achievements:
                lines.append("🏅 解鎖：" + "、".join(achievements))
        return _card("🎯 終極密碼結束", "本局已完成結算", lines)
    if value < secret:
        state["low"] = value + 1
        hint = f"❌ {value} 太小，區間更新為 {state['low']}～{state['high']}"
    else:
        state["high"] = value - 1
        hint = f"❌ {value} 太大，區間更新為 {state['low']}～{state['high']}"
    state["turn_index"] = (int(state.get("turn_index") or 0) + 1) % len(players)
    _save_state(room["id"], state)
    message = _ultimate_turn_message(room, state, hint)
    _schedule_ultimate_timeout(room["id"])
    return message


def _schedule_ultimate_timeout(room_id):
    def callback():
        room = _db_one("SELECT * FROM game_rooms WHERE id=%s", (room_id,))
        if not room or room.get("status") != "playing" or room.get("game_type") != "ultimate_password":
            return
        state = _load_state(room)
        players = room_players(room_id)
        if not players:
            return
        current = players[int(state.get("turn_index") or 0) % len(players)]
        state["turn_index"] = (int(state.get("turn_index") or 0) + 1) % len(players)
        _save_state(room_id, state)
        msg = _ultimate_turn_message(room, state, f"⏰ {current.get('player_name') or '玩家'} 超時，本回合跳過。")
        _push(room["group_id"], msg)
        _schedule_ultimate_timeout(room_id)
    room = _db_one("SELECT group_id FROM game_rooms WHERE id=%s", (room_id,)) or {}
    seconds = max(5, int(get_game_setting(room.get("group_id"), "ultimate_turn_seconds", "10") or 10))
    _schedule(room_id, seconds, callback)


# ── 骰子 PK ─────────────────────────────────────────────────────────────────
def create_dice(group_id, user_id, name, dice_count, rounds):
    dice_count = max(1, min(int(dice_count), 3))
    rounds = 1 if int(rounds) not in {1, 3, 5} else int(rounds)
    ok, msg, room = create_room(group_id, "dice_pk", user_id, name, {"dice_count": dice_count, "rounds": rounds})
    if not ok:
        return TextSendMessage(text=f"❌ {msg}")
    _save_state(room["id"], {"dice_count": dice_count, "rounds": rounds, "round": 0, "turn_index": 0, "scores": {}, "rolls": {}})
    _add_test_bots(room)
    _schedule_room_expiry(room)
    return _room_waiting_card(room, f"每人 {dice_count} 顆骰子・共 {rounds} 回合")


def _dice_turn_message(room, state, prefix=""):
    players = room_players(room["id"])
    idx = int(state.get("turn_index") or 0) % len(players)
    current = players[idx]
    if _is_test_bot(current.get("user_id")):
        _schedule_test_bot_action(room["id"])
    return _card(
        "🎲 骰子 PK", f"第 {state.get('round',1)} / {state.get('rounds',1)} 回合",
        ([prefix] if prefix else []) + [
            f"👉 輪到：{current.get('player_name') or '玩家'}",
            f"目前總分：{int((state.get('scores') or {}).get(str(current['user_id']),0))}",
            "⏳ 10 秒內按下擲骰",
        ],
        [_button("🎲 擲骰", "骰子擲骰", "primary", "#7D7CFF")],
    )


def handle_dice_roll(group_id, user_id):
    room = active_room(group_id)
    if not room or room["game_type"] != "dice_pk" or room["status"] != "playing":
        return TextSendMessage(text="❌ 目前沒有進行中的骰子 PK。")
    players = room_players(room["id"])
    if not players:
        _finish_room(room["id"])
        return TextSendMessage(text="❌ 遊戲室已沒有玩家，遊戲自動結束。")
    state = _load_state(room)
    idx = int(state.get("turn_index") or 0) % len(players)
    current = players[idx]
    if str(current["user_id"]) != str(user_id):
        return TextSendMessage(text=f"⏳ 現在輪到 {current.get('player_name') or '玩家'}。")
    _cancel_timer(room["id"])
    values = [random.randint(1, 6) for _ in range(int(state.get("dice_count") or 1))]
    total = sum(values)
    uid = str(user_id)
    state.setdefault("scores", {})[uid] = int(state.get("scores", {}).get(uid, 0)) + total
    state.setdefault("rolls", {}).setdefault(uid, []).append(values)
    return _advance_dice(room, state, f"🎲 {current.get('player_name')}：{'、'.join(map(str,values))}（本回合 {total} 分）")


def _advance_dice(room, state, prefix):
    players = room_players(room["id"])
    next_index = int(state.get("turn_index") or 0) + 1
    if next_index >= len(players):
        next_round = int(state.get("round") or 1) + 1
        if next_round > int(state.get("rounds") or 1):
            scores = {str(k): int(v) for k, v in (state.get("scores") or {}).items()}
            best = max(scores.values()) if scores else 0
            winners = [uid for uid, score in scores.items() if score == best]
            results = _settle_players(room, winners, scores)
            _finish_room(room["id"])
            names = _player_map(room["id"])
            ranking = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            lines = [prefix, "🏆 最終排名"]
            for i, (uid, score) in enumerate(ranking, 1):
                lines.append(f"{i}. {(names.get(uid) or {}).get('player_name','玩家')}｜{score} 分")
            for name, won, result, achievements in results:
                lines.append(f"{'🏆' if won else '😭'} {name}：{_result_text(result)}")
            return _card("🎲 骰子 PK 結束", "最高總分者獲勝；平手共同獲勝", lines)
        state["round"] = next_round
        state["turn_index"] = 0
    else:
        state["turn_index"] = next_index
    _save_state(room["id"], state)
    msg = _dice_turn_message(room, state, prefix)
    _schedule_dice_timeout(room["id"])
    return msg


def _schedule_dice_timeout(room_id):
    def callback():
        room = _db_one("SELECT * FROM game_rooms WHERE id=%s", (room_id,))
        if not room or room.get("status") != "playing" or room.get("game_type") != "dice_pk":
            return
        state = _load_state(room)
        players = room_players(room_id)
        current = players[int(state.get("turn_index") or 0) % len(players)]
        msg = _advance_dice(room, state, f"⏰ {current.get('player_name') or '玩家'} 超時，本回合 0 分。")
        _push(room["group_id"], msg)
    room = _db_one("SELECT group_id FROM game_rooms WHERE id=%s", (room_id,)) or {}
    seconds = max(5, int(get_game_setting(room.get("group_id"), "dice_turn_seconds", "10") or 10))
    _schedule(room_id, seconds, callback)


# ── 彩虹拉霸 ────────────────────────────────────────────────────────────────
SLOT_SYMBOLS = ["🍌", "🍓", "⭐", "🦊", "💎", "🌈", "👑"]
SLOT_WEIGHTS = [30, 25, 18, 12, 8, 5, 2]


def slots_card(group_id, user_id):
    status = daily_status(group_id, user_id)
    used = int(status.get("slot_plays") or 0)
    daily_plays = max(1, int(get_game_setting(group_id, "slot_daily_plays", "3") or 3))
    remain = max(0, daily_plays - used)
    buttons = (
        [_button("🎰 開始拉霸", "開始拉霸", "primary", "#FF6FAF")]
        if remain > 0 else
        [_button("🎮 返回遊戲中心", "遊戲中心")]
    )
    return _card(
        "🎰 彩虹拉霸", "個人遊戲・每日凌晨 5:00 重置",
        [f"今日剩餘次數：{remain} / {daily_plays}",
         "🌈🌈🌈 為 JACKPOT，👑👑👑 為彩虹至尊獎",
         "次數用完後明天凌晨 5:00 重置。"],
        buttons,
    )


def play_slots(group_id, user_id, name):
    ensure_game_center_tables()
    status = daily_status(group_id, user_id)
    if int(status.get("slot_plays") or 0) >= 3:
        return TextSendMessage(text="🎰 今日 3 次拉霸機會已用完，請於明天凌晨 5:00 後再來。")
    reels = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)
    same = len(set(reels)) == 1
    win = False
    score = 0
    special = ""
    if same:
        symbol = reels[0]
        score_map = {"🍌":100, "🍓":150, "⭐":200, "🦊":300, "💎":500, "🌈":1000, "👑":2000}
        score = score_map[symbol]
        win = True
        special = "💥 JACKPOT！" if symbol == "🌈" else ("👑 彩虹至尊獎！" if symbol == "👑" else "🎉 三連線！")
    elif len(set(reels)) == 2:
        score = 30
        win = True
        special = "✨ 雙圖案小獎"
    result = apply_game_result(group_id, user_id, name, "rainbow_slots", win, score, "SLOT")
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE game_daily_limits SET slot_plays=slot_plays+1 WHERE group_id=%s AND user_id=%s AND game_date=%s", (group_id, user_id, game_date()))
        conn.commit()
    finally:
        conn.close()
    achievements = unlock_available_game_achievements(group_id, user_id)
    remain = max(0, 2 - int(status.get("slot_plays") or 0))
    lines = ["　".join(reels), special or "😢 本次未連線", _result_text(result), f"今日剩餘：{remain} / 3"]
    if achievements:
        lines.append("🏅 解鎖：" + "、".join(achievements))
    buttons = (
        [_button("再拉一次", "開始拉霸", "primary", "#FF6FAF")]
        if remain > 0 else
        [_button("🎮 返回遊戲中心", "遊戲中心")]
    )
    return _card("🎰 彩虹拉霸結果", "每日三次，獎勵達上限後仍可遊玩", lines, buttons)


# ── 快問快答 ────────────────────────────────────────────────────────────────
DEFAULT_QUESTIONS = [
    ("Pokémon",1,"皮卡丘是什麼屬性？","電","火","水","草","A","皮卡丘是電屬性寶可夢。"),
    ("Pokémon",1,"妙蛙種子的全國圖鑑編號是？","001","004","007","025","A","妙蛙種子是全國圖鑑第 001 號。"),
    ("Pokémon",1,"Pokémon GO 中，捕捉寶可夢主要使用哪種道具？","精靈球","傷藥","薰香","星星碎片","A","捕捉時主要使用精靈球。"),
    ("Pokémon",2,"水屬性招式通常對哪個屬性效果絕佳？","火","草","電","龍","A","水剋火。"),
    ("Pokémon",2,"伊布可以進化成下列哪一隻？","水伊布","噴火龍","卡比獸","耿鬼","A","水伊布是伊布的進化型之一。"),
    ("生活百科",1,"冰箱冷藏室通常應維持在哪個溫度範圍？","0～7°C","15～20°C","25～30°C","-20°C以下","A","一般冷藏建議約 0～7°C。"),
    ("生活百科",1,"遇到油鍋起火時，不應該使用什麼滅火？","水","鍋蓋","滅火毯","乾粉滅火器","A","加水可能造成油火噴濺。"),
    ("生活百科",1,"台灣常用的緊急報案電話是？","110","1120","1199","168","A","110 為警察報案電話。"),
    ("生活百科",2,"人體缺水時常見的早期訊號是？","口渴","頭髮變長","指甲變藍","耳朵發熱","A","口渴是常見的缺水訊號。"),
    ("美食",1,"珍珠奶茶中的珍珠主要由什麼製成？","樹薯粉","麵粉","玉米粒","糯米","A","珍珠主要使用樹薯澱粉。"),
    ("美食",1,"壽司飯通常會加入哪種調味？","醋","醬油膏","辣椒油","奶油","A","壽司飯會拌入壽司醋。"),
    ("科技",1,"Android 是由哪家公司主導開發？","Google","Apple","Nintendo","Netflix","A","Android 由 Google 主導。"),
    ("科技",1,"Wi‑Fi 主要用來做什麼？","無線網路連線","加熱食物","測量體溫","列印照片","A","Wi‑Fi 是無線網路技術。"),
    ("世界知識",1,"日本的首都是？","東京","大阪","京都","名古屋","A","日本首都是東京。"),
    ("世界知識",1,"世界上面積最大的海洋是？","太平洋","大西洋","印度洋","北冰洋","A","太平洋面積最大。"),
    ("娛樂",1,"電影通常以每秒多張什麼連續播放形成動態影像？","畫格","歌曲","文字","地圖","A","連續畫格形成動態影像。"),
    ("運動",1,"籃球比賽投進三分線外的球可得幾分？","3 分","1 分","2 分","4 分","A","三分線外投進得 3 分。"),
    ("便利商店",1,"便利商店常見的條碼主要用於？","辨識商品與結帳","測量溫度","播放音樂","充電","A","條碼可辨識商品與價格。"),
    ("多元文化",1,"彩虹旗常被用來象徵什麼？","多元與 Pride","交通警示","天氣預報","學校制服","A","彩虹旗常象徵 LGBTQ+ Pride 與多元。"),
    ("Rainbow Life",1,"Rainbow Life 的遊戲獎勵達每日上限後會怎樣？","仍可玩但無資源獎勵","禁止使用 Bot","帳號歸零","永久禁言","A","達上限後仍可娛樂並累積戰績。"),
    ("Pokémon",1,"傑尼龜是什麼屬性？","水","火","草","電","A","傑尼龜是水屬性寶可夢。"),
    ("Pokémon",1,"小火龍最終可以進化成哪一隻？","噴火龍","水箭龜","妙蛙花","皮卡丘","A","小火龍最終進化為噴火龍。"),
    ("Pokémon",2,"一般系招式對哪個屬性無效？","幽靈","水","草","飛行","A","一般系招式對幽靈系無效。"),
    ("Pokémon",2,"Pokémon GO 中用來吸引附近寶可夢的道具是？","薰香","傷藥","活力碎片","銀色凰梨果","A","薰香能吸引寶可夢出現。"),
    ("Pokémon",2,"超級進化通常需要哪一種能量？","超級能量","星星沙子","糖果 XL","寶可幣","A","Pokémon GO 的超級進化需要超級能量。"),
    ("生活百科",1,"洗手時使用肥皂的主要作用是？","幫助帶走污垢與微生物","讓手變冷","增加指紋","改變膚色","A","肥皂能乳化油脂並幫助沖走污垢。"),
    ("生活百科",1,"電器插頭或電線冒煙時，第一步應該怎麼做？","安全切斷電源","用手碰觸","潑水","繼續使用","A","應先在安全前提下切斷電源。"),
    ("生活百科",1,"曬傷風險通常在哪個時段較高？","接近中午前後","午夜","清晨四點","深夜","A","紫外線通常在中午前後較強。"),
    ("生活百科",2,"食品標示中的有效日期主要代表什麼？","建議安全食用期限","製造地點","售價","重量","A","有效日期代表建議安全食用期限。"),
    ("美食",1,"豆腐主要以哪一種原料製作？","黃豆","小麥","玉米","花生","A","豆腐主要由黃豆製成。"),
    ("美食",1,"鳳梨酥常見的外皮主要使用哪類材料？","麵粉與奶油","白米","海苔","馬鈴薯","A","鳳梨酥外皮常以麵粉、奶油等製作。"),
    ("便利商店",1,"超商微波食品加熱前，最重要的是先做什麼？","依包裝指示處理","連金屬包裝一起加熱","完全密封","放入水中","A","應依包裝上的微波指示處理。"),
    ("便利商店",1,"發票載具的主要用途是？","儲存電子發票","計算步數","開啟門鎖","測量溫度","A","載具可用來歸戶或儲存電子發票。"),
    ("科技",1,"QR Code 屬於哪一種工具？","二維條碼","音訊格式","電池規格","螢幕面板","A","QR Code 是二維條碼。"),
    ("科技",2,"雙重驗證的主要好處是？","增加帳號安全性","讓密碼變短","取消登入","提升螢幕亮度","A","雙重驗證增加額外的身分確認。"),
    ("娛樂",1,"動畫作品中負責角色聲音演出的工作通常稱為？","聲優或配音員","攝影師","燈光師","剪票員","A","聲優或配音員負責角色聲音演出。"),
    ("世界知識",1,"法國的首都是？","巴黎","倫敦","羅馬","柏林","A","法國首都是巴黎。"),
    ("世界知識",1,"地球上最大的洲是？","亞洲","歐洲","非洲","南美洲","A","亞洲是面積最大的洲。"),
    ("運動",1,"羽球比賽中使用的球稱為？","羽球","壘球","曲棍球","橄欖球","A","羽球運動使用羽球。"),
    ("多元文化",1,"尊重他人的稱謂與代名詞，主要展現什麼？","尊重與包容","競爭","懲罰","嘲笑","A","尊重稱謂與代名詞是包容的表現。"),
    ("Rainbow Life",1,"遊戲室建立後多久未開始會自動解散？","10 分鐘","1 分鐘","1 小時","永不解散","A","目前規則為 10 分鐘未開始自動解散。"),
    ("Rainbow Life",1,"彩虹拉霸每位成員每天可玩幾次？","3 次","1 次","10 次","不限","A","每位成員每天可玩 3 次。"),
]


def seed_quiz_questions():
    ensure_game_center_tables()
    row = _db_one("SELECT COUNT(*) AS total FROM quiz_questions WHERE source_type='official'") or {}
    if int(row.get("total") or 0) >= len(DEFAULT_QUESTIONS):
        return
    conn = get_connection()
    try:
        with conn.cursor() as c:
            for q in DEFAULT_QUESTIONS:
                c.execute("""
                    INSERT INTO quiz_questions(category,difficulty,question,option_a,option_b,option_c,option_d,correct_option,explanation,source_type)
                    SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,'official'
                    WHERE NOT EXISTS (SELECT 1 FROM quiz_questions WHERE question=%s)
                """, (*q, q[2]))
        conn.commit()
    finally:
        conn.close()


def create_quiz(group_id, user_id, name, category, count):
    seed_quiz_questions()
    count = 5 if int(count) not in {5, 10, 20} else int(count)
    ok, msg, room = create_room(group_id, "quick_quiz", user_id, name, {"category": category, "count": count, "seconds": 15})
    if not ok:
        return TextSendMessage(text=f"❌ {msg}")
    _save_state(room["id"], {"category": category, "count": count, "question_index": 0, "scores": {}, "answers": {}, "question_ids": []})
    _add_test_bots(room)
    _schedule_room_expiry(room)
    return _room_waiting_card(room, f"題庫：{category}・共 {count} 題・每題 15 秒")


def _select_questions(category, count):
    if category == "綜合":
        rows = _db_all("SELECT * FROM quiz_questions WHERE is_active=1 ORDER BY RANDOM() LIMIT %s", (count,))
    elif category == "Pokémon":
        rows = _db_all("SELECT * FROM quiz_questions WHERE is_active=1 AND category='Pokémon' ORDER BY RANDOM() LIMIT %s", (count,))
    else:
        rows = _db_all("SELECT * FROM quiz_questions WHERE is_active=1 AND category=%s ORDER BY RANDOM() LIMIT %s", (category, count))
    if len(rows) < count:
        extra = _db_all("SELECT * FROM quiz_questions WHERE is_active=1 ORDER BY RANDOM() LIMIT %s", (count - len(rows),))
        ids = {r["id"] for r in rows}
        rows += [r for r in extra if r["id"] not in ids][:count-len(rows)]
    return rows


def _start_quiz(room):
    seed_quiz_questions()
    state = _load_state(room)
    questions = _select_questions(state.get("category") or "綜合", int(state.get("count") or 5))
    if not questions:
        _finish_room(room["id"])
        return TextSendMessage(text="❌ 題庫目前沒有可用題目。")
    state["question_ids"] = [int(q["id"]) for q in questions]
    state["question_index"] = 0
    state["scores"] = {str(p["user_id"]): 0 for p in room_players(room["id"])}
    state["answers"] = {}
    _save_state(room["id"], state)
    msg = _quiz_question_message(room, state)
    _schedule_quiz_timeout(room["id"])
    return msg


def _quiz_question(room, state):
    ids = state.get("question_ids") or []
    index = int(state.get("question_index") or 0)
    if index >= len(ids):
        return None
    return _db_one("SELECT * FROM quiz_questions WHERE id=%s", (ids[index],))


def _quiz_question_message(room, state):
    q = _quiz_question(room, state)
    if not q:
        return _finish_quiz(room, state)
    index = int(state.get("question_index") or 0)
    total = len(state.get("question_ids") or [])
    state["answers"] = {}
    state["question_started_at"] = datetime.now(TW).isoformat()
    _save_state(room["id"], state)
    _schedule_quiz_test_answers(room["id"])
    return _card(
        f"🧠 第 {index+1} / {total} 題", f"{q.get('category')}・難度 {'⭐'*int(q.get('difficulty') or 1)}",
        [q.get("question")],
        [
            _button(f"A．{q.get('option_a')}", "快問作答 A", "primary", "#28B8A8"),
            _button(f"B．{q.get('option_b')}", "快問作答 B"),
            _button(f"C．{q.get('option_c')}", "快問作答 C"),
            _button(f"D．{q.get('option_d')}", "快問作答 D"),
        ],
    )


def handle_quiz_answer(group_id, user_id, option):
    room = active_room(group_id)
    if not room or room["game_type"] != "quick_quiz" or room["status"] != "playing":
        return TextSendMessage(text="❌ 目前沒有進行中的快問快答。")
    players = _player_map(room["id"])
    if str(user_id) not in players:
        return TextSendMessage(text="❌ 你沒有加入這個遊戲室。")
    state = _load_state(room)
    uid = str(user_id)
    if uid in (state.get("answers") or {}):
        return TextSendMessage(text="✅ 你已經作答，不能修改答案。")
    q = _quiz_question(room, state)
    if not q:
        return TextSendMessage(text="本題已結束。")
    elapsed = 99.0
    try:
        elapsed = (datetime.now(TW) - datetime.fromisoformat(state.get("question_started_at"))).total_seconds()
    except Exception:
        pass
    correct = str(option).upper() == str(q.get("correct_option") or "").upper()
    state.setdefault("answers", {})[uid] = {"option": str(option).upper(), "correct": correct, "elapsed": elapsed}
    if correct:
        bonus = 2 if not any(v.get("correct") for k,v in state["answers"].items() if k != uid) else 0
        state.setdefault("scores", {})[uid] = int(state.get("scores", {}).get(uid, 0)) + 10 + bonus
    _save_state(room["id"], state)
    expected = {
        str(row["user_id"]) for row in room_players(room["id"])
        if not int(row.get("is_spectator") or 0)
    }
    if expected and expected.issubset(set(state.get("answers") or {})):
        _cancel_timer(room["id"])
        _, message = _complete_quiz_question(room["id"])
        if message is not None:
            return message
    return TextSendMessage(text="✅ 已送出答案，等待其他玩家或倒數結束。")


def _complete_quiz_question(room_id):
    room = _db_one("SELECT * FROM game_rooms WHERE id=%s", (room_id,))
    if not room or room.get("status") != "playing" or room.get("game_type") != "quick_quiz":
        return None, None
    state = _load_state(room)
    q = _quiz_question(room, state)
    if not q:
        return room, None
    answers = state.get("answers") or {}
    players = _player_map(room_id)
    correct_names = [
        players[uid].get("player_name")
        for uid, answer in answers.items()
        if answer.get("correct") and uid in players
    ]
    option_key = "option_" + str(q.get("correct_option") or "A").lower()
    prefix = f"⏰ 正確答案：{q.get('correct_option')}．{q.get(option_key)}"
    if correct_names:
        prefix += "\n✅ 答對：" + "、".join(correct_names)
    else:
        prefix += "\n本題無人答對"
    state["question_index"] = int(state.get("question_index") or 0) + 1
    _save_state(room_id, state)
    if int(state["question_index"]) >= len(state.get("question_ids") or []):
        return room, _finish_quiz(room, state, prefix)
    _push(room["group_id"], TextSendMessage(text=prefix))
    next_message = _quiz_question_message(room, state)
    _schedule_quiz_timeout(room_id)
    return room, next_message


def _schedule_quiz_timeout(room_id):
    def callback():
        _, message = _complete_quiz_question(room_id)
        if message is not None:
            room = _db_one("SELECT * FROM game_rooms WHERE id=%s", (room_id,))
            if room:
                _push(room["group_id"], message)
    room = _db_one("SELECT group_id FROM game_rooms WHERE id=%s", (room_id,)) or {}
    seconds = max(5, int(get_game_setting(room.get("group_id"), "quiz_answer_seconds", "15") or 15))
    _schedule(room_id, seconds, callback)


def _finish_quiz(room, state, prefix=""):
    scores = {str(k): int(v) for k,v in (state.get("scores") or {}).items()}
    best = max(scores.values()) if scores else 0
    winners = [uid for uid, score in scores.items() if score == best]
    players = _player_map(room["id"])
    results = _settle_players(room, winners, scores)
    _finish_room(room["id"])
    ranking = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    lines = [prefix] if prefix else []
    lines.append("🏆 最終排名")
    for i,(uid,score) in enumerate(ranking,1):
        lines.append(f"{i}. {(players.get(uid) or {}).get('player_name','玩家')}｜{score} 分")
    for name, won, result, achievements in results:
        lines.append(f"{'🏆' if won else '😭'} {name}：{_result_text(result)}")
    return _card("🧠 快問快答結束", "最高積分者獲勝；平手共同獲勝", lines)
