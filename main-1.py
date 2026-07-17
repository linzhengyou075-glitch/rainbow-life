import datetime
import os
import hashlib
import json
import random
import time
import re
import traceback
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs
import uvicorn
from fastapi import FastAPI, Request, Header, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, PostbackEvent, TextMessage, StickerMessage, TextSendMessage, FlexSendMessage

from config import (
    LINE_CHANNEL_ACCESS_TOKEN,
    LINE_CHANNEL_SECRET,
    SIGN_EXP,
    SIGN_COIN,
    CHAT_EXP,
    CHAT_CHEST_RATE,
    CHAT_CHEST_COIN,
    WHEEL_MIN,
    WHEEL_MAX,
    VIP_7_PRICE,
    VIP_30_PRICE,
    VIP_FOREVER_PRICE,
)
from database import get_connection
from commerce import ensure_commerce_tables, record_purchase_in_transaction, transaction_code, format_purchase_details, format_member_purchase_details, format_vault, format_vault_history, consume_pending_vault_announcements
from flex_ui import (
    function_home_flex, shop_home_flex, admin_home_flex, admin_group_switch_flex,
    admin_member_menu_flex, admin_vip_menu_flex, admin_shop_menu_flex,
    admin_consumption_menu_flex, admin_vault_menu_flex, admin_sign_menu_flex,
    admin_shop_items_flex, admin_shop_item_actions_flex,
    admin_member_list_flex, admin_member_detail_flex, admin_member_vip_actions_flex,
    admin_settings_center_flex, admin_vip_settings_flex, admin_placeholder_settings_flex,
    admin_announcement_settings_flex, admin_activity_settings_flex,
    admin_sign_settings_v4_flex, admin_v4_center_flex, personal_center_v4_flex, group_portal_v4_flex,
    ranking_menu_flex, vip_shop_flex, title_shop_flex, unified_shop_flex, status_card_flex, vault_card_flex, shop_category_flex, level_up_flex,
    birthday_center_flex, birthday_input_help_flex, announcement_view_flex, activity_center_flex,
    command_help_flex, simple_help_page_flex, admin_input_help_flex,
    sign_result_flex, operation_notice_flex, universal_text_card, ranking_result_flex,
    fortune_result_flex, wheel_result_flex, front_portal_flex, admin_portal_flex, player_center_entry_flex,
    set_notification_member_name, clear_notification_member_name,
)
from vip_settings import get_vip_settings, update_vip_price, set_vip_shop_enabled, set_vip_exp_multiplier, effective_vip_price
from roadmap_v4 import (ensure_v4_tables, get_setting, set_setting, get_sign_settings, set_makeup_price, analytics_message, audit_message, reminder_summary, add_audit)
from progression import exp_needed, level_title, rank_title, progress_info
from control_center import (
    ensure_control_center_tables, register_group, handle_private_control_command,
    control_center_message, group_list_message, list_admin_groups,
    get_selected_group, set_selected_group, group_name, get_member_page, get_member_by_id,
)



def get_vip_setting_counts(group_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT COUNT(*) FILTER (
                           WHERE COALESCE(is_vip,0)=1
                             AND (
                                 UPPER(COALESCE(vip_until,'')) IN ('PERMANENT','FOREVER','永久','永久VIP')
                                 OR LEFT(COALESCE(vip_until,''),10)::date >= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Taipei')::date
                             )
                       ) AS vip_count,
                       COUNT(*) FILTER (
                           WHERE COALESCE(is_vip,0)=1
                             AND UPPER(COALESCE(vip_until,'')) IN ('PERMANENT','FOREVER','永久','永久VIP')
                       ) AS permanent_count
                FROM players WHERE group_id=%s
            """, (group_id,))
            row = c.fetchone() or {}
            return int(row.get("vip_count") or 0), int(row.get("permanent_count") or 0)
    finally:
        conn.close()

# ===== 連續簽到獎勵 =====
# milestone: 天數 -> (彩虹幣, EXP, 名稱)
STREAK_MILESTONE_REWARDS = {
    3: (100, 50, "初次連續"),
    7: (300, 150, "一週達成"),
    14: (600, 300, "雙週達成"),
    30: (1500, 800, "滿月達成"),
    60: (3000, 1500, "雙月達成"),
    100: (6000, 3000, "百日達成"),
}


def get_streak_reward(streak_days):
    """回傳每日連續加成與里程碑獎勵，100 天後每 30 天仍可持續領獎。"""
    streak_days = max(int(streak_days or 0), 0)

    # 每連續 5 天，之後每日多 10 幣；最高每日加成 100 幣，避免經濟失控。
    daily_bonus_coin = min((max(streak_days - 1, 0) // 5) * 10, 100)

    milestone_coin = 0
    milestone_exp = 0
    milestone_name = ""

    if streak_days in STREAK_MILESTONE_REWARDS:
        milestone_coin, milestone_exp, milestone_name = STREAK_MILESTONE_REWARDS[streak_days]
    elif streak_days >= 120 and streak_days % 30 == 0:
        # 120 天後，每滿 30 天持續有獎；獎勵緩慢成長並設上限。
        milestone_coin = min(3000 + (streak_days - 120) * 20, 10000)
        milestone_exp = min(1500 + (streak_days - 120) * 10, 5000)
        milestone_name = f"{streak_days}日長期守護"

    return daily_bonus_coin, milestone_coin, milestone_exp, milestone_name


def get_next_streak_milestone(streak_days):
    """取得下一個可領取里程碑天數。"""
    streak_days = max(int(streak_days or 0), 0)
    for day in sorted(STREAK_MILESTONE_REWARDS):
        if day > streak_days:
            return day
    if streak_days < 120:
        return 120
    return ((streak_days // 30) + 1) * 30


# ===== 補簽系統 =====
MAKEUP_SIGN_PRICES = {1: 100, 2: 200, 3: 300, 4: 450, 5: 600, 6: 800, 7: 1000}


def normalize_command_text(raw_text):
    """統一清理 LINE 文字指令，兼容半形／全形驚嘆號與卡片裝飾括號。"""
    value = str(raw_text or "").strip()
    had_prefix = value.startswith(("!", "！"))
    if had_prefix:
        value = value[1:].lstrip()

    # 某些舊卡片或輸入法會送出「【功能】」「〔功能〕」等裝飾格式。
    # 只移除包住整個指令的外層括號，不動指令內容中的正常符號。
    wrapper_pairs = [("【", "】"), ("〔", "〕"), ("[", "]"), ("（", "）"), ("(", ")")]
    changed = True
    while changed and value:
        changed = False
        for left, right in wrapper_pairs:
            if value.startswith(left) and value.endswith(right) and len(value) > len(left) + len(right):
                value = value[len(left):-len(right)].strip()
                changed = True
                break

    # 合併多餘空白，避免「機器人  正常」無法辨識。
    value = " ".join(value.split())
    aliases = {
        "占卜": "每日運勢",
        "今日占卜": "每日運勢",
        "轉盤": "幸運輪盤",
        "每日轉盤": "幸運輪盤",
        "等級排行榜": "排行榜資料",
        "等級榜": "排行榜資料",
        "彩虹幣排行榜": "金幣排行榜",
        "彩虹幣榜": "金幣排行榜",
        "金幣榜": "金幣排行榜",
        "累積簽到排行榜": "累積簽到榜",
        "簽到排行榜": "累積簽到榜",
        "貼圖榜": "貼圖排行榜",
        "今日貼圖榜": "貼圖排行榜",
        "群規": "查看群規",
        "規則": "查看群規",
        "返回": "功能",
        "重新整理": "功能",
        "機器人 正常": "機器人模式 正常",
        "機器人 靜音": "機器人模式 靜音",
        "機器人 維護": "機器人模式 維護",
        "正常模式": "機器人模式 正常",
        "靜音模式": "機器人模式 靜音",
        "維護模式": "機器人模式 維護",
    }
    value = aliases.get(value, value)
    return value, had_prefix


def ensure_sign_history_for_player(group_id, user_id):
    """將舊版的最後簽到與連續天數補成逐日紀錄，避免升級後連續天數遺失。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT last_sign_in, streak_count
                FROM players WHERE group_id=%s AND user_id=%s
            """, (group_id, user_id))
            row = c.fetchone()
            if not row or not row["last_sign_in"]:
                return
            try:
                last_date = datetime.date.fromisoformat(row["last_sign_in"])
            except (TypeError, ValueError):
                return
            streak = max(int(row["streak_count"] or 0), 1)
            for offset in range(streak):
                sign_date = (last_date - datetime.timedelta(days=offset)).isoformat()
                c.execute("""
                    INSERT INTO sign_records (group_id, user_id, sign_date, source)
                    VALUES (%s, %s, %s, 'legacy')
                    ON CONFLICT (group_id, user_id, sign_date) DO NOTHING
                """, (group_id, user_id, sign_date))
            conn.commit()
    finally:
        conn.close()


def recalculate_current_streak(group_id, user_id):
    """依逐日紀錄重新計算目前有效的連續簽到。

    只有連續到今天，或至少連續到昨天，才算目前連續天數；
    很久以前已中斷的歷史紀錄不會被誤顯示成目前連續簽到。
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT sign_date FROM sign_records
                WHERE group_id=%s AND user_id=%s
                ORDER BY sign_date DESC
            """, (group_id, user_id))
            rows = c.fetchall()
            dates = set()
            for row in rows:
                try:
                    dates.add(datetime.date.fromisoformat(str(row["sign_date"])))
                except (TypeError, ValueError):
                    pass

            today = datetime.date.fromisoformat(get_sign_date())
            if today in dates:
                cursor_date = today
            elif (today - datetime.timedelta(days=1)) in dates:
                cursor_date = today - datetime.timedelta(days=1)
            else:
                cursor_date = None

            streak = 0
            while cursor_date is not None and cursor_date in dates:
                streak += 1
                cursor_date -= datetime.timedelta(days=1)

            c.execute("""
                UPDATE players SET streak_count=%s
                WHERE group_id=%s AND user_id=%s
            """, (streak, group_id, user_id))
            conn.commit()
            return streak
    finally:
        conn.close()




WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]

def format_date_zh(date_value):
    """將 ISO 日期顯示為 YYYY/MM/DD（週X）。"""
    if isinstance(date_value, str):
        date_value = datetime.date.fromisoformat(date_value)
    return f"{date_value.strftime('%Y/%m/%d')}（週{WEEKDAY_ZH[date_value.weekday()]}）"

def format_exp_progress(player, gained_exp=0):
    """以目前固定每 100 EXP 升級的規則，產生清楚的經驗值進度資訊。"""
    total_exp = max(0, int((player or {}).get("exp") or 0))
    level = max(1, int((player or {}).get("level") or (total_exp // 100 + 1)))
    exp_per_level = 100
    current_exp = total_exp % exp_per_level
    remaining = exp_per_level - current_exp
    percent = int((current_exp / exp_per_level) * 100)
    filled = min(10, max(0, percent // 10))
    bar = "█" * filled + "░" * (10 - filled)
    lines = []
    if gained_exp:
        lines.append(f"⭐ 本次獲得：+{int(gained_exp):,} EXP")
    lines.extend([
        f"🏅 目前等級：Lv.{level}",
        f"📈 經驗進度：{current_exp:,} / {exp_per_level:,} EXP",
        f"▰ {bar} {percent}%",
        f"🚀 距離 Lv.{level + 1}：還差 {remaining:,} EXP",
        f"✨ 累積經驗：{total_exp:,} EXP",
    ])
    return "\n".join(lines)


def get_level_title(level):
    return level_title(level)


def get_display_title(player):
    """舊相容函式：等級稱號永遠由系統決定，不再被購買稱號覆蓋。"""
    return level_title((player or {}).get("level") or 1)


def get_equipped_title(player):
    return str((player or {}).get("custom_title") or "").strip()


def taiwan_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


SIGN_RESET_HOUR = 5

def get_sign_date():
    """簽到以台灣時間每日 05:00 換日；05:00 前仍算前一個簽到日。"""
    shifted = taiwan_now() - datetime.timedelta(hours=SIGN_RESET_HOUR)
    return shifted.date().isoformat()


def get_sign_month():
    d = taiwan_now().date()
    return f"{d.year}-{d.month:02d}"


def next_sign_time_text():
    next_date = datetime.date.fromisoformat(get_sign_date()) + datetime.timedelta(days=1)
    return f"{format_date_zh(next_date)} {SIGN_RESET_HOUR:02d}:00"


def current_sign_time_text():
    return taiwan_now().strftime("%H:%M")


def birthday_inline(player):
    birthday = str((player or {}).get("birthday") or "").strip()
    if not birthday:
        return ""
    try:
        parts = birthday.replace("/", "-").split("-")
        if len(parts) >= 2:
            month, day = int(parts[-2]), int(parts[-1])
            suffix = " 🎉今天生日！" if is_birthday_today(birthday) else ""
            return f" 🎂{month:02d}/{day:02d}{suffix}"
    except (TypeError, ValueError):
        pass
    return ""


def vip_status_lines(player, group_id, user_id):
    if not player or not is_vip_active(group_id, user_id):
        return ["💎 VIP：未啟用"]
    raw_until = str(player.get("vip_until") or "").strip()
    if not raw_until or raw_until.upper() in {"PERMANENT", "FOREVER", "永久", "永久VIP"}:
        return ["💎 VIP：永久 VIP ♾️"]
    try:
        end = datetime.date.fromisoformat(raw_until[:10])
        today_real = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()
        remaining = max(0, (end - today_real).days)
        warning = "⚠️" if remaining <= 7 else "⏳"
        return [
            "💎 VIP：啟用中",
            f"📅 到期日：{end.strftime('%Y/%m/%d')}",
            f"{warning} 剩餘：{remaining} 天",
        ]
    except ValueError:
        return ["💎 VIP：啟用中", f"📅 到期日：{raw_until}"]


def get_player_ranks(group_id, user_id):
    """取得群內等級與累積簽到排名；同分同名次。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                WITH totals AS (
                    SELECT p.user_id,
                           COALESCE(p.level, 1) AS level,
                           COALESCE(p.exp, 0) AS exp,
                           COALESCE(s.total_signs, 0) AS total_signs,
                           COALESCE(p.streak_count, 0) AS streak_count
                    FROM players p
                    LEFT JOIN (
                        SELECT group_id, user_id, COUNT(*) AS total_signs
                        FROM sign_records
                        WHERE group_id=%s
                        GROUP BY group_id, user_id
                    ) s ON s.group_id=p.group_id AND s.user_id=p.user_id
                    WHERE p.group_id=%s
                ), ranked AS (
                    SELECT user_id,
                           RANK() OVER (ORDER BY level DESC, exp DESC) AS level_rank,
                           RANK() OVER (ORDER BY total_signs DESC, streak_count DESC) AS sign_rank
                    FROM totals
                )
                SELECT level_rank, sign_rank
                FROM ranked
                WHERE user_id=%s
            """, (group_id, group_id, user_id))
            row = c.fetchone()
            if not row:
                return "-", "-"
            return row["level_rank"], row["sign_rank"]
    finally:
        conn.close()


def get_status_sign_data(group_id, user_id):
    """以逐日簽到紀錄為準，避免舊欄位與實際紀錄不同步。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT COUNT(*) AS total_signs,
                       MAX(CASE WHEN sign_date=%s THEN 1 ELSE 0 END) AS signed_today
                FROM sign_records
                WHERE group_id=%s AND user_id=%s
            """, (get_sign_date(), group_id, user_id))
            row = c.fetchone() or {}
            c.execute("""
                SELECT COALESCE(streak_count,0) AS streak_count,
                       COALESCE(today_msg_count,0) AS today_msg_count,
                       COALESCE(today_sticker_count,0) AS today_sticker_count
                FROM players
                WHERE group_id=%s AND user_id=%s
            """, (group_id, user_id))
            player_row = c.fetchone() or {}
        return {
            "total_signs": int(row.get("total_signs") or 0),
            "signed_today": bool(row.get("signed_today") or 0),
            "streak_count": int(player_row.get("streak_count") or 0),
            "today_msg_count": int(player_row.get("today_msg_count") or 0),
            "today_sticker_count": int(player_row.get("today_sticker_count") or 0),
        }
    finally:
        conn.close()


def status_next_sign_text(signed_today):
    return next_sign_time_text() if signed_today else "現在即可簽到"


def format_status_exp(player):
    total_exp = max(0, int((player or {}).get("exp") or 0))
    level = max(1, int((player or {}).get("level") or (total_exp // 100 + 1)))
    current = total_exp % 100
    percent = current
    filled = min(10, max(0, percent // 10))
    bar = "█" * filled + "░" * (10 - filled)
    remaining = 100 - current
    return (
        "⭐ 經驗值\n"
        f"▰ {bar} {percent}%\n"
        f"{current:,} / 100 EXP（累積 {total_exp:,}）\n"
        f"🚀 距離 Lv.{level + 1}：{remaining:,} EXP"
    )


def makeup_sign_help(group_id, user_id):
    player = get_player(group_id, user_id)
    balance = int((player or {}).get("coins") or 0)
    lines = [
        "📝 補簽說明", "", "最多可補最近 7 天內漏掉的簽到。", "補簽只增加累積簽到與一般簽到 EXP，不會發放簽到彩虹幣或里程碑獎勵。", "",
        "💰 補簽費用"
    ]
    for days, price in sorted(((int(k), int(v)) for k,v in (get_sign_settings(group_id).get("makeup_prices") or {}).items())):
        lines.append(f"{days}天前：{price} 彩虹幣")
    lines += ["", "正確格式：", "!補簽 1天", "！補簽 1天", "補簽 1天", "", f"🌈 目前餘額：{balance} 彩虹幣"]
    return "\n".join(lines)


def handle_makeup_sign(group_id, user_id, days):
    ensure_commerce_tables()
    ensure_control_center_tables()
    ensure_v4_tables()
    makeup_prices = {int(k): int(v) for k,v in (get_sign_settings(group_id).get("makeup_prices") or {}).items()}
    if days not in makeup_prices:
        return "❌ 補簽天數錯誤\n\n最多只能補最近 7 天。\n正確格式：!補簽 1天"

    ensure_sign_history_for_player(group_id, user_id)
    target_date = datetime.date.fromisoformat(get_sign_date()) - datetime.timedelta(days=days)
    target_iso = target_date.isoformat()
    price = makeup_prices[days]

    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT name, coins, sign_month_count
                FROM players WHERE group_id=%s AND user_id=%s
                FOR UPDATE
            """, (group_id, user_id))
            player = c.fetchone()
            if not player:
                return "❌ 找不到你的玩家資料，請先輸入簽到建立資料。"

            c.execute("""
                SELECT 1 FROM sign_records
                WHERE group_id=%s AND user_id=%s AND sign_date=%s
            """, (group_id, user_id, target_iso))
            if c.fetchone():
                return f"⚠️ {target_date.strftime('%Y/%m/%d')} 已經簽到過，不能重複補簽。"

            balance = int(player["coins"] or 0)
            if balance < price:
                return (
                    "❌ 彩虹幣不足\n\n"
                    f"補簽 {days} 天前需要：{price} 彩虹幣\n"
                    f"目前餘額：{balance} 彩虹幣\n"
                    f"還差：{price - balance} 彩虹幣"
                )

            c.execute("""
                INSERT INTO sign_records (group_id, user_id, sign_date, source)
                VALUES (%s, %s, %s, 'makeup')
            """, (group_id, user_id, target_iso))
            c.execute("""
                UPDATE players
                SET coins=COALESCE(coins, 0)-%s,
                    sign_month_count=COALESCE(sign_month_count, 0)+1
                WHERE group_id=%s AND user_id=%s
            """, (price, group_id, user_id))
            purchase_id = record_purchase_in_transaction(c, group_id, user_id, "補簽", f"補簽 {days}天前", price)
            conn.commit()
    finally:
        conn.close()

    # 補簽只給一般 SIGN_EXP，不觸發連續加成、里程碑、活動掉落或轉盤。
    makeup_exp = int(get_sign_settings(group_id).get("exp") or SIGN_EXP)
    add_exp(group_id, user_id, makeup_exp)
    new_streak = recalculate_current_streak(group_id, user_id)
    updated = get_player(group_id, user_id)
    exp_display = format_exp_progress(updated, makeup_exp)
    notices = consume_pending_vault_announcements(group_id)
    vault_notice = ("\n\n" + "\n\n".join(notices)) if notices else ""
    return (
        "✅ 補簽成功！\n\n"
        f"📅 補簽日期：{format_date_zh(target_date)}（{days}天前）\n"
        f"💰 扣除：{price} 彩虹幣\n🏦 金庫 +{price}\n🔖 交易編號：#{transaction_code(purchase_id)}\n\n"
        f"{exp_display}\n\n"
        f"🔥 連續簽到：{new_streak} 天\n"
        f"📆 累積簽到：{int((updated or {}).get('sign_month_count') or 0)} 天\n"
        f"🌈 剩餘彩虹幣：{int((updated or {}).get('coins') or 0)}"
        f"{vault_notice}"
    )
from game import ensure_player, get_player, add_exp, add_coins
from admin import (
    is_admin, is_owner, set_owner, list_admins, add_admin, remove_admin,
    get_admin_badge, get_admin_benefits, has_admin_permission,
    set_admin_permission, list_admin_permissions, PERMISSION_LABELS, add_admin_log, get_admin_role,
)
from shop import (
    list_titles,
    list_shop_items,
    buy_title,
    equip_title,
    my_titles_message,
    format_shop_home,
    format_shop_category,
    format_shop_management,
    format_title_shop,
    format_vip_title_shop,
    format_title_management,
    format_vip_title_management,
    buy_shop_item,
    get_shop_item,
    add_shop_item,
    delete_shop_item,
    update_shop_item_price,
    update_shop_item,
    set_shop_item_active,
    add_title,
    delete_title,
    update_title_price,
    grant_title_to_user,
    revoke_title_from_user,
    unequip_title,
    title_record_message,
)
try:
    from vip import (
        check_vip_expired,
        set_vip,
        grant_vip,
        extend_vip,
        cancel_vip,
        vip_status_message,
        vip_record_message,
        claim_vip_gift,
        is_vip_active,
    )
except ImportError:
    from vip import check_vip_expired, set_vip

    def _parse_vip_days_fallback(days_text):
        raw = str(days_text or "").strip().upper().replace(" ", "")
        if raw in ["永久", "PERMANENT", "FOREVER", "VIP永久"]:
            return None, "永久VIP"
        raw = raw.replace("天", "").replace("DAY", "").replace("DAYS", "")
        try:
            days = int(raw)
        except Exception:
            return "ERR", "錯誤"
        if days <= 0:
            return "ERR", "錯誤"
        return days, f"{days}天VIP"

    def grant_vip(group_id, user_id, days_text, actor_user_id=""):
        days, label = _parse_vip_days_fallback(days_text)
        if days == "ERR":
            return False, "❌ VIP 天數格式錯誤，請輸入：7天、30天、90天、永久。"
        set_vip(group_id, user_id, days)
        return True, f"💎 已成功給予 VIP！\n期限：{label}"

    def extend_vip(group_id, user_id, days_text, actor_user_id=""):
        days, label = _parse_vip_days_fallback(days_text)
        if days == "ERR" or days is None:
            return False, "❌ 延長 VIP 請輸入天數，例如：7天、30天、90天。"
        set_vip(group_id, user_id, days)
        return True, f"💎 已成功延長 VIP！\n期限：{label}"

    def cancel_vip(group_id, user_id, actor_user_id=""):
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute("UPDATE players SET is_vip=0, vip_until='' WHERE group_id=%s AND user_id=%s", (group_id, user_id))
                conn.commit()
        finally:
            conn.close()
        return True, "✅ 已收回 VIP。"

    def vip_status_message(group_id, user_id, display_name="玩家"):
        row = get_player(group_id, user_id)
        if not row or not row.get("is_vip"):
            return f"💎 VIP 資訊\n\n👤 {display_name}\n狀態：未啟用"
        until = row.get("vip_until") or "永久"
        return f"💎 VIP 資訊\n\n👤 {display_name}\n狀態：已啟用\n期限：{until}"

    def vip_record_message(group_id, user_id, display_name="玩家"):
        return f"💎 VIP 紀錄\n\n👤 {display_name}\n目前版本尚無紀錄資料。"

    def claim_vip_gift(group_id, user_id):
        row = get_player(group_id, user_id)
        if not row or not row.get("is_vip"):
            return False, "❌ 你目前不是 VIP。"
        add_coins(group_id, user_id, 1000)
        return True, "🎁 今日 VIP 禮包\n\n🌈 彩虹幣 +1000"

    def is_vip_active(group_id, user_id):
        row = get_player(group_id, user_id)
        return bool(row and row.get("is_vip"))
from rank import coin_rank, level_rank
from events import (
    format_activity_info,
    format_current_activity_tasks,
    active_activities,
    ensure_event_tables,
    format_my_activity,
    format_event_bag,
    format_specific_activity_item,
    format_activity_rank,
    grant_active_event_items,
    format_activity_item_gain,
    grant_event_item_by_name,
    format_event_shop,
    exchange_event_shop_item,
    add_event_shop_item,
    delete_event_shop_item,
    set_event_shop_item_active,
    format_event_shop_management,
    seed_default_event_shop_items,
)
from tasks import (
    ensure_task_tables,
    ensure_official_tasks,
    add_task,
    delete_task,
    set_task_active,
    get_user_tasks,
    update_task_progress,
    claim_task_reward,
    list_all_tasks,
    task_rank_message,
)
from badges import (
    seed_default_badges,
    display_name_with_badge,
    my_badges_message,
    badge_catalog_message,
    grant_badge_by_name,
    revoke_badge_by_name,
    add_custom_badge,
    deactivate_badge,
)

from rainbow_theme import flex_palette

APP_VERSION = "Rainbow Life Final Deploy"
BUILD_ID = "rainbow-life-final-deploy"

app = FastAPI()

# Rainbow Life 全新個人中心＋後台（Phase 2）
from rainbow_web import register_rainbow_web, make_access_url, make_player_entry_url
from game_database import ensure_game_center_tables, seed_game_settings
from game_center import handle_game_center_command, configure_line_bot_api
from flex_ui import web_admin_entry_flex

def make_admin_access_url(user_id, group_id, path="/admin"):
    return make_access_url(user_id, group_id, path)

def make_player_access_url(user_id, group_id, path="/player"):
    return make_player_entry_url(group_id, path)

def make_public_card_url(group_id, user_id):
    return make_access_url(user_id, group_id, "/player")

def make_game_admin_url(base_url, user_id, group_id):
    return make_access_url(user_id, group_id, "/admin?tab=game")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
configure_line_bot_api(line_bot_api)
register_rainbow_web(app, line_bot_api)

def _game_admin_base_url():
    return (
        os.getenv("PUBLIC_BASE_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or ""
    ).rstrip("/")


def _game_admin_permission(group_id, user_id):
    try:
        return bool(is_owner(group_id, user_id) or is_admin(group_id, user_id))
    except Exception:
        return False




# ===== V5 Phase 4：全域卡片相容層 =====
# 專屬 Flex 頁面維持原樣；任何尚未重構的 TextSendMessage 會自動轉為
# 紫白彩虹卡片，因此所有功能、通知、錯誤提示與舊模組都不再出現純文字泡泡。
def _cardify_outgoing_message(message):
    if isinstance(message, TextSendMessage):
        card = universal_text_card(message.text)
        quick_reply = getattr(message, "quick_reply", None)
        if quick_reply is not None:
            try:
                card.quick_reply = quick_reply
            except Exception:
                pass
        return card
    if isinstance(message, (list, tuple)):
        return [_cardify_outgoing_message(item) for item in message]
    return message


_original_reply_message = line_bot_api.reply_message
_original_push_message = line_bot_api.push_message
_original_multicast = getattr(line_bot_api, "multicast", None)
_original_broadcast = getattr(line_bot_api, "broadcast", None)


def _reply_message_cardified(reply_token, messages, *args, **kwargs):
    return _original_reply_message(reply_token, _cardify_outgoing_message(messages), *args, **kwargs)


def _notification_fingerprint(messages):
    """建立穩定通知識別；同一目標、同一遊戲日、同一通知只推播一次。"""
    def normalize(item):
        if isinstance(item, TextSendMessage):
            return {"type": "text", "text": str(getattr(item, "text", ""))}
        if isinstance(item, FlexSendMessage):
            return {
                "type": "flex",
                "alt_text": str(getattr(item, "alt_text", "")),
                "contents": getattr(item, "contents", None),
            }
        if isinstance(item, (list, tuple)):
            return [normalize(x) for x in item]
        try:
            return item.as_json_dict()
        except Exception:
            return repr(item)
    raw = json.dumps(normalize(messages), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _claim_daily_notification(target_id, messages):
    if not target_id or target_id == "PRIVATE":
        return True
    key = _notification_fingerprint(messages)
    day = get_game_date()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """INSERT INTO daily_notification_log(target_id,notification_date,notification_key)
                   VALUES(%s,%s,%s) ON CONFLICT DO NOTHING RETURNING notification_key""",
                (target_id, day, key),
            )
            claimed = c.fetchone() is not None
            conn.commit()
            return claimed
    except Exception:
        return True
    finally:
        conn.close()


def _push_message_cardified(to, messages, *args, **kwargs):
    # V5.4.6：靜音／維護模式全面阻擋主動推播。
    try:
        if to and to != "PRIVATE" and get_system_mode(to) in ("silent", "maintenance"):
            return None
    except Exception:
        pass
    # V5.4.9：所有主動通知同一內容每日只推播一次。
    if not _claim_daily_notification(to, messages):
        return None
    return _original_push_message(to, _cardify_outgoing_message(messages), *args, **kwargs)


line_bot_api.reply_message = _reply_message_cardified
line_bot_api.push_message = _push_message_cardified

if _original_multicast is not None:
    def _multicast_cardified(to, messages, *args, **kwargs):
        return _original_multicast(to, _cardify_outgoing_message(messages), *args, **kwargs)
    line_bot_api.multicast = _multicast_cardified

if _original_broadcast is not None:
    def _broadcast_cardified(messages, *args, **kwargs):
        return _original_broadcast(_cardify_outgoing_message(messages), *args, **kwargs)
    line_bot_api.broadcast = _broadcast_cardified


# ===== 時間規則：台灣時間每日 05:00 換日 =====
def game_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))) - datetime.timedelta(hours=5)


def get_game_date():
    return game_now().date().isoformat()


def get_game_month():
    d = game_now().date()
    return f"{d.year}-{d.month:02d}"


# ===== 資料庫保護：缺表/缺欄位自動補齊 =====
def ensure_database_ready():
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS players (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                exp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                coins INTEGER DEFAULT 0,
                last_sign_in TEXT DEFAULT '',
                last_fortune_date TEXT DEFAULT '',
                last_wheel_date TEXT DEFAULT '',
                custom_title TEXT DEFAULT '',
                is_vip INTEGER DEFAULT 0,
                vip_until TEXT DEFAULT '',
                today_msg_count INTEGER DEFAULT 0,
                today_sticker_count INTEGER DEFAULT 0,
                PRIMARY KEY (group_id, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS available_titles (
                title_name TEXT UNIQUE NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT DEFAULT 'admin',
                PRIMARY KEY (group_id, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS bot_events (
                group_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                event_value TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_id, event_key)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS daily_notification_log (
                target_id TEXT NOT NULL,
                notification_date TEXT NOT NULL,
                notification_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (target_id, notification_date, notification_key)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS sign_records (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                sign_date TEXT NOT NULL,
                source TEXT DEFAULT 'normal',
                signed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_id, user_id, sign_date)
            )
        """)
        c.execute("ALTER TABLE sign_records ADD COLUMN IF NOT EXISTS signed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sign_records_user_date ON sign_records(group_id, user_id, sign_date)")

        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS streak_count INTEGER DEFAULT 0")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS sign_month TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS sign_month_count INTEGER DEFAULT 0")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS last_sign_in TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS last_fortune_date TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS last_wheel_date TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS custom_title TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS level_exp BIGINT DEFAULT 0")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS exp_system_version INTEGER DEFAULT 1")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS is_vip INTEGER DEFAULT 0")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS vip_until TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS today_msg_count INTEGER DEFAULT 0")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS today_sticker_count INTEGER DEFAULT 0")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS activity_stats_date TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS birthday TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS birthday_year INTEGER")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS birthday_reward_year INTEGER DEFAULT 0")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS fortune_result_date TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS fortune_level TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS fortune_message TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS fortune_exp_multiplier NUMERIC DEFAULT 1")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS fortune_coin_multiplier NUMERIC DEFAULT 1")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS fortune_luck_score INTEGER DEFAULT 50")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS fortune_wheel_bonus INTEGER DEFAULT 0")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS wheel_spin_date TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS wheel_spin_count INTEGER DEFAULT 0")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS wheel_reward_history TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS mute_until TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS mute_reason TEXT DEFAULT ''")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS muted_by TEXT DEFAULT ''")

        # 修復舊版資料：舊資料列可能保留 NULL，造成 EXP + 數字仍然是 NULL。
        c.execute("""
            UPDATE players
            SET exp = COALESCE(exp, 0),
                level = GREATEST(1, (COALESCE(exp, 0) / 100) + 1),
                coins = COALESCE(coins, 0),
                is_vip = COALESCE(is_vip, 0),
                today_msg_count = COALESCE(today_msg_count, 0),
                today_sticker_count = COALESCE(today_sticker_count, 0),
                streak_count = COALESCE(streak_count, 0),
                sign_month_count = COALESCE(sign_month_count, 0)
        """)
        c.execute("ALTER TABLE players ALTER COLUMN exp SET DEFAULT 0")
        c.execute("ALTER TABLE players ALTER COLUMN level SET DEFAULT 1")
        c.execute("ALTER TABLE players ALTER COLUMN coins SET DEFAULT 0")
        conn.commit()
    conn.close()
    ensure_game_center_tables()




def reset_monthly_sign_counts_if_needed():
    """保留相容函式：月份變更時只更新月份標記，永久累積簽到不歸零。"""
    this_month = get_game_month()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            UPDATE players
            SET sign_month=%s
            WHERE sign_month IS NULL
               OR sign_month=''
               OR sign_month<>%s
        """, (this_month, this_month))
        conn.commit()
    conn.close()



# ===== 生日系統 =====
BIRTHDAY_COIN_REWARD = 1000
BIRTHDAY_EXP_REWARD = 500


def parse_birthday_input(raw_text):
    raw = (raw_text or "").strip().replace("年", "/").replace("月", "/").replace("日", "")
    raw = raw.replace(".", "/").replace("-", "/")
    parts = [x.strip() for x in raw.split("/") if x.strip()]
    try:
        if len(parts) == 2:
            year = None
            month, day = map(int, parts)
            datetime.date(2000, month, day)
        elif len(parts) == 3:
            year, month, day = map(int, parts)
            if year < 1900 or year > tw_now_real().year:
                return None, None, "❌ 出生年份不正確。"
            datetime.date(year, month, day)
        else:
            return None, None, "❌ 格式錯誤，請輸入：!生日設定 07/10\n也可輸入：!生日設定 2000/07/10"
    except (ValueError, TypeError):
        return None, None, "❌ 日期不正確，請重新輸入。"
    return f"{month:02d}-{day:02d}", year, None


def birthday_display(birthday, year=None):
    if not birthday:
        return "尚未設定"
    month, day = birthday.split("-")
    return f"{year}/{int(month):02d}/{int(day):02d}" if year else f"{int(month):02d}/{int(day):02d}"


def set_player_birthday(group_id, user_id, birthday, year=None):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE players SET birthday=%s, birthday_year=%s WHERE group_id=%s AND user_id=%s", (birthday, year, group_id, user_id))
            conn.commit()
    finally:
        conn.close()


def clear_player_birthday(group_id, user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE players SET birthday='', birthday_year=NULL, birthday_reward_year=0 WHERE group_id=%s AND user_id=%s", (group_id, user_id))
            conn.commit()
    finally:
        conn.close()


def today_birthdays(group_id):
    today_mmdd = game_now().strftime("%m-%d")
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id, name, birthday, birthday_year, COALESCE(birthday_reward_year,0) AS birthday_reward_year FROM players WHERE group_id=%s AND birthday=%s ORDER BY name", (group_id, today_mmdd))
            return c.fetchall()
    finally:
        conn.close()


def upcoming_birthdays_message(group_id, limit=10):
    now = tw_now_real().date()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id, name, birthday, birthday_year FROM players WHERE group_id=%s AND COALESCE(birthday,'')<>''", (group_id,))
            rows = c.fetchall()
    finally:
        conn.close()
    items = []
    for row in rows:
        try:
            month, day = map(int, row["birthday"].split("-"))
            try:
                next_date = datetime.date(now.year, month, day)
            except ValueError:
                next_date = datetime.date(now.year, 2, 28)
            if next_date < now:
                try:
                    next_date = datetime.date(now.year + 1, month, day)
                except ValueError:
                    next_date = datetime.date(now.year + 1, 2, 28)
            items.append(((next_date-now).days, row))
        except Exception:
            continue
    items.sort(key=lambda x:(x[0], x[1]["name"]))
    if not items:
        return "🎂 生日名單\n\n目前還沒有人設定生日。\n輸入：!生日設定 07/10"
    lines=["🎂 即將到來的生日", ""]
    for days,row in items[:limit]:
        when = "今天" if days==0 else ("明天" if days==1 else f"還有 {days} 天")
        lines.append(f"🎈 {row['name']}｜{birthday_display(row['birthday'])}｜{when}")
    return "\n".join(lines)


def grant_birthday_rewards(group_id):
    year = game_now().year
    celebrants = today_birthdays(group_id)
    conn = get_connection()
    rewarded=[]
    try:
        with conn.cursor() as c:
            for row in celebrants:
                if int(row.get("birthday_reward_year") or 0)==year:
                    continue
                # 禁言期間不發放任何自動生日獎勵；解除後仍可於當日正常補發。
                if get_mute_status(group_id, row["user_id"]):
                    continue
                c.execute("""
                    UPDATE players
                    SET coins=COALESCE(coins,0)+%s,
                        exp=COALESCE(exp,0)+%s,
                        level=GREATEST(1,((COALESCE(exp,0)+%s)/100)+1),
                        birthday_reward_year=%s
                    WHERE group_id=%s AND user_id=%s AND COALESCE(birthday_reward_year,0)<>%s
                """,(BIRTHDAY_COIN_REWARD,BIRTHDAY_EXP_REWARD,BIRTHDAY_EXP_REWARD,year,group_id,row["user_id"],year))
                if c.rowcount:
                    rewarded.append(row)
            conn.commit()
    finally:
        conn.close()
    return celebrants, rewarded


def maybe_announce_birthdays(group_id):
    if group_id == "PRIVATE":
        return
    today_key = get_game_date()
    if get_event_value(group_id, "birthday_announcement_date", "") == today_key:
        return
    celebrants, _ = grant_birthday_rewards(group_id)
    if not celebrants:
        return
    names = "、".join(name_with_badge(group_id, r["user_id"], r["name"]) for r in celebrants)
    msg=("🎂 今日壽星登場！\n\n"
         f"🎉 祝 {names} 生日快樂！\n"
         "願今天充滿驚喜、歡笑和彩虹好心情 🌈\n\n"
         f"🎁 生日禮物：彩虹幣 +{BIRTHDAY_COIN_REWARD}\n"
         f"⭐ 生日經驗：EXP +{BIRTHDAY_EXP_REWARD}")
    if announce_group(group_id,msg):
        set_event_value(group_id,"birthday_announcement_date",today_key)



# ===== V2.6：星座、生日徽章與每日運勢 =====
ZODIAC_RANGES = [
    ("♑", "摩羯座", (1, 1), (1, 19)), ("♒", "水瓶座", (1, 20), (2, 18)),
    ("♓", "雙魚座", (2, 19), (3, 20)), ("♈", "牡羊座", (3, 21), (4, 19)),
    ("♉", "金牛座", (4, 20), (5, 20)), ("♊", "雙子座", (5, 21), (6, 21)),
    ("♋", "巨蟹座", (6, 22), (7, 22)), ("♌", "獅子座", (7, 23), (8, 22)),
    ("♍", "處女座", (8, 23), (9, 22)), ("♎", "天秤座", (9, 23), (10, 23)),
    ("♏", "天蠍座", (10, 24), (11, 22)), ("♐", "射手座", (11, 23), (12, 21)),
    ("♑", "摩羯座", (12, 22), (12, 31)),
]


def zodiac_from_birthday(birthday):
    if not birthday:
        return "", "尚未設定"
    try:
        month, day = map(int, birthday.split("-"))
    except Exception:
        return "", "尚未設定"
    for emoji, name, start, end in ZODIAC_RANGES:
        if start <= (month, day) <= end:
            return emoji, name
    return "", "尚未設定"


def next_birthday_days(birthday):
    if not birthday:
        return None
    today = game_now().date()
    try:
        month, day = map(int, birthday.split("-"))
        try:
            target = datetime.date(today.year, month, day)
        except ValueError:
            target = datetime.date(today.year, 2, 28)
        if target < today:
            try:
                target = datetime.date(today.year + 1, month, day)
            except ValueError:
                target = datetime.date(today.year + 1, 2, 28)
        return (target - today).days
    except Exception:
        return None


def is_birthday_today(birthday):
    return bool(birthday) and birthday == game_now().strftime("%m-%d")


FORTUNE_LEVELS = {
    "大吉": {"icon": "🌞", "min": 90, "exp": 1.40, "coin": 1.40, "wheel": 25},
    "吉":   {"icon": "🌤️", "min": 70, "exp": 1.20, "coin": 1.20, "wheel": 12},
    "平":   {"icon": "🌥️", "min": 40, "exp": 1.05, "coin": 1.05, "wheel": 4},
    "凶":   {"icon": "🌧️", "min": 0,  "exp": 0.90, "coin": 0.90, "wheel": 0},
}
FORTUNE_COLORS = ["紅色", "橘色", "黃色", "綠色", "藍色", "紫色", "粉紅色", "白色", "金色"]
FORTUNE_ITEMS = ["彩虹徽章", "幸運香蕉", "星星吊飾", "四葉草", "彩虹糖", "小翅膀"]
FORTUNE_TIPS_BY_LEVEL = {
    "大吉": ["今天整體氣勢很旺，適合主動爭取機會。", "把握好狀態，大膽行動容易得到好結果。"],
    "吉": ["保持現在的步調，穩穩前進就會有收穫。", "今天適合主動交流，也適合處理累積的事情。"],
    "平": ["今天適合按部就班，先把眼前的事做好。", "保持彈性，臨時變化可能藏著新的機會。"],
    "凶": ["今天容易遇到小阻礙，重要事情多確認一次。", "放慢步調、避免衝動，能減少不必要的失誤。"],
}

def fortune_level_from_score(score):
    score = max(1, min(100, int(score or 50)))
    for name in ("大吉", "吉", "平", "凶"):
        if score >= FORTUNE_LEVELS[name]["min"]:
            return name
    return "凶"


def build_fortune_data(player, score, level_name, special=""):
    data = FORTUNE_LEVELS[level_name]
    emoji, zodiac = zodiac_from_birthday(player.get("birthday") or "")
    rng = random.Random(f"{player.get('user_id','')}-{get_game_date()}-{score}")
    def stars(value):
        n = max(1, min(5, value))
        return "★" * n + "☆" * (5 - n)
    base = max(1, min(5, round(score / 20)))
    return {
        "name": player.get("name") or "成員", "zodiac": f"{emoji} {zodiac}".strip(),
        "level": level_name, "icon": data["icon"], "score": score,
        "love": stars(max(1, min(5, base + rng.choice([-1, 0, 0, 1])))),
        "money": stars(max(1, min(5, base + rng.choice([-1, 0, 1])))),
        "work": stars(max(1, min(5, base + rng.choice([-1, 0, 0, 1])))),
        "color": rng.choice(FORTUNE_COLORS), "number": rng.randint(1, 99),
        "item": rng.choice(FORTUNE_ITEMS), "tip": rng.choice(FORTUNE_TIPS_BY_LEVEL[level_name]),
        "exp_mult": data["exp"], "coin_mult": data["coin"], "wheel_bonus": data["wheel"],
        "special": special, "refresh": "明日 05:00 刷新",
    }


def build_fortune_message(player, level_name, special="", score=50):
    d = build_fortune_data(player, score, level_name, special)
    extra = f"\n✨ 稀有事件：{special}" if special else ""
    return (f"🌈 每日運勢\n\n👤 {d['name']}\n{d['zodiac']}\n"
            f"🔮 {d['icon']} {d['level']}｜幸運值 {d['score']}%\n\n"
            f"❤️ 桃花運：{d['love']}\n💰 財運：{d['money']}\n💼 工作／學業：{d['work']}\n"
            f"🎨 幸運色：{d['color']}\n🔢 幸運數字：{d['number']}\n🍀 幸運物：{d['item']}\n\n"
            f"💬 {d['tip']}\n\n🎁 EXP ×{d['exp_mult']:.2f}、彩虹幣 ×{d['coin_mult']:.2f}、轉盤稀有率 +{d['wheel_bonus']}%"
            f"{extra}\n\n🕔 {d['refresh']}")


def get_fortune_multipliers(player):
    if not player or player.get("fortune_result_date") != get_game_date():
        return 1.0, 1.0
    try:
        return float(player.get("fortune_exp_multiplier") or 1), float(player.get("fortune_coin_multiplier") or 1)
    except Exception:
        return 1.0, 1.0


def get_fortune_wheel_bonus(player):
    if not player or player.get("fortune_result_date") != get_game_date():
        return 0
    return max(0, int(player.get("fortune_wheel_bonus") or 0))


def choose_wheel_reward(luck_score, extra_bonus=0):
    # 幸運值會實際提高超稀有、傳說與彩虹獎勵權重。
    boost = max(0, min(40, int(extra_bonus or 0))) + (10 if luck_score >= 95 else 0)
    pool = [
        {"rarity":"普通", "icon":"⚪", "type":"coin", "amount":80, "label":"彩虹幣 +80", "weight":38},
        {"rarity":"普通", "icon":"⚪", "type":"exp", "amount":120, "label":"EXP +120", "weight":28},
        {"rarity":"稀有", "icon":"🟢", "type":"coin", "amount":250, "label":"彩虹幣 +250", "weight":18 + boost * .10},
        {"rarity":"稀有", "icon":"🟢", "type":"exp", "amount":350, "label":"EXP +350", "weight":10 + boost * .08},
        {"rarity":"超稀有", "icon":"🔵", "type":"both", "amount":600, "label":"彩虹幣 +600、EXP +600", "weight":4 + boost * .10},
        {"rarity":"傳說", "icon":"🟣", "type":"both", "amount":1500, "label":"彩虹幣 +1500、EXP +1500", "weight":1.5 + boost * .06},
        {"rarity":"彩虹", "icon":"🌈", "type":"both", "amount":5000, "label":"JACKPOT：彩虹幣 +5000、EXP +5000", "weight":0.5 + boost * .03},
    ]
    return random.choices(pool, weights=[x["weight"] for x in pool], k=1)[0]

# ===== V2.0.2：每日狂歡時段 =====
def tw_now_real():
    """台灣真實時間；狂歡時段用 12:00 / 18:00 / 20:00，不受 05:00 遊戲換日偏移影響。"""
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def get_event_value(group_id, key, default=""):
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT event_value
            FROM bot_events
            WHERE group_id=%s AND event_key=%s
        """, (group_id, key))
        row = c.fetchone()
    conn.close()
    return row["event_value"] if row else default


def set_event_value(group_id, key, value):
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO bot_events(group_id, event_key, event_value)
            VALUES(%s, %s, %s)
            ON CONFLICT(group_id, event_key)
            DO UPDATE SET event_value=EXCLUDED.event_value,
                          updated_at=CURRENT_TIMESTAMP
        """, (group_id, key, str(value)))
        conn.commit()
    conn.close()


def ensure_daily_carnival(group_id):
    """每天 05:00 遊戲日刷新後，抽 12 / 18 / 20 其中一小時。"""
    today = get_game_date()
    saved_date = get_event_value(group_id, "carnival_date", "")

    if saved_date != today:
        hour = random.choice([12, 18, 20])
        set_event_value(group_id, "carnival_date", today)
        set_event_value(group_id, "carnival_hour", hour)
        set_event_value(group_id, "carnival_start_announced", "0")
        set_event_value(group_id, "carnival_end_announced", "0")

    try:
        return int(get_event_value(group_id, "carnival_hour", "12"))
    except ValueError:
        return 12


def carnival_status(group_id):
    hour = ensure_daily_carnival(group_id)
    now = tw_now_real()
    start = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(hours=1)

    if start <= now < end:
        status = "🟢 進行中"
    elif now < start:
        status = "⏳ 尚未開始"
    else:
        status = "✅ 今日已結束"

    return hour, start, end, status


def is_carnival_active(group_id):
    _, start, end, _ = carnival_status(group_id)
    now = tw_now_real()
    return start <= now < end


def carnival_multiplier(group_id):
    return 2 if is_carnival_active(group_id) else 1


def maybe_announce_carnival(group_id):
    """檢查每日盲抽狂歡的開始與結束通知，並防止同一天重複推播。"""
    if group_id == "PRIVATE":
        return

    hour, start, end, status = carnival_status(group_id)
    now = tw_now_real()
    start_announced = get_event_value(group_id, "carnival_start_announced", "0") == "1"
    end_announced = get_event_value(group_id, "carnival_end_announced", "0") == "1"

    if start <= now < end and not start_announced:
        ok = announce_group(
            group_id,
            (
                "📢 🌈 全群狂歡時刻開始！\n\n"
                f"⏰ 時間：{hour:02d}:00～{hour+1:02d}:00\n"
                "✨ 聊天 EXP ×2\n"
                "🎁 寶箱掉落率 ×2\n\n"
                "快來聊天衝等吧！"
            ),
        )
        if ok:
            set_event_value(group_id, "carnival_start_announced", "1")
            start_announced = True

    # 即使服務在開始瞬間短暫重啟，結束後仍會補送結束通知，避免整天完全沒有通知。
    if now >= end and not end_announced:
        ok = announce_group(
            group_id,
            "📢 🌈 今日全群狂歡時段已結束！\n\n✨ 聊天 EXP 與寶箱掉落率已恢復正常。\n感謝大家熱情參與 ❤️",
        )
        if ok:
            set_event_value(group_id, "carnival_start_announced", "1")
            set_event_value(group_id, "carnival_end_announced", "1")


def maybe_announce_manual_carnival_end(group_id):
    """群長手動狂歡自然倒數結束時，自動推播一次結束通知。"""
    if group_id == "PRIVATE":
        return
    until = get_activity_until(group_id, "manual_carnival_until")
    tracked_until = int(float(get_event_value(group_id, "manual_carnival_tracked_until", "0") or 0))
    end_announced_until = int(float(get_event_value(group_id, "manual_carnival_end_announced_until", "0") or 0))

    if until > now_ts():
        if tracked_until != until:
            set_event_value(group_id, "manual_carnival_tracked_until", until)
        return

    effective_until = max(until, tracked_until)
    if effective_until > 0 and now_ts() >= effective_until and end_announced_until != effective_until:
        ok = announce_group(
            group_id,
            "📢 👑 群長狂歡模式已結束！\n\n✨ 聊天 EXP 與寶箱掉落率已恢復正常。",
        )
        if ok:
            set_event_value(group_id, "manual_carnival_end_announced_until", effective_until)


def format_today_activity(group_id):
    hour, start, end, status = carnival_status(group_id)
    return (
        "🌈 今日狂歡時段\n\n"
        f"⏰ 時間：{hour:02d}:00～{hour+1:02d}:00\n"
        f"目前狀態：{status}\n\n"
        "✨ 活動效果：聊天 EXP ×2\n"
        "🎁 寶箱掉落率 ×2\n\n"
        "📌 每天 05:00 後重新盲抽 12:00 / 18:00 / 20:00 其中一小時。"
    )


# ===== V2.0.3：群長活動系統 =====
def now_ts():
    return int(time.time())


def set_activity_until(group_id, key, seconds):
    set_event_value(group_id, key, now_ts() + int(seconds))


def clear_activity(group_id, key):
    set_event_value(group_id, key, "0")


def get_activity_until(group_id, key):
    try:
        return int(float(get_event_value(group_id, key, "0") or 0))
    except ValueError:
        return 0


def activity_remaining_seconds(group_id, key):
    remaining = get_activity_until(group_id, key) - now_ts()
    return max(0, remaining)


def is_activity_active(group_id, key):
    return activity_remaining_seconds(group_id, key) > 0


def format_remaining(seconds):
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    hours = minutes // 60
    minutes = minutes % 60
    if hours > 0:
        return f"{hours}小時{minutes}分鐘"
    return f"{minutes}分鐘"






def _get_mentionees(event):
    mention = getattr(getattr(event, "message", None), "mention", None)
    mentionees = getattr(mention, "mentionees", None) if mention else None
    return mentionees or []


def _mention_attr(mentionee, key, default=None):
    if isinstance(mentionee, dict):
        return mentionee.get(key, default)
    return getattr(mentionee, key, default)


def _remove_first_mention_text(raw_text, mentionee):
    text_value = raw_text or ""
    index = _mention_attr(mentionee, "index", None)
    length = _mention_attr(mentionee, "length", None)
    try:
        if index is not None and length is not None:
            index = int(index)
            length = int(length)
            return (text_value[:index] + text_value[index + length:]).strip()
    except Exception:
        pass
    return re.sub(r"@\S+", "", text_value, count=1).strip()


def _get_player_row_by_user_id(group_id, target_user_id, fallback_name="成員"):
    display_name = fallback_name or "成員"
    try:
        display_name = get_line_display_name(group_id, target_user_id) or display_name
    except Exception:
        pass
    ensure_player(group_id, target_user_id, display_name)
    try:
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("""
                UPDATE players
                SET name=%s
                WHERE group_id=%s AND user_id=%s
            """, (display_name, group_id, target_user_id))
            c.execute("""
                SELECT user_id, name
                FROM players
                WHERE group_id=%s AND user_id=%s
            """, (group_id, target_user_id))
            row = c.fetchone()
            conn.commit()
        conn.close()
        return row or {"user_id": target_user_id, "name": display_name}
    except Exception:
        return {"user_id": target_user_id, "name": display_name}


def resolve_target_and_rest(event, group_id, command_prefix):
    """解析管理指令目標。

    給別人：優先支援 LINE @tag，例如「給予VIP @小明 7天」。
    給自己：沒有 @tag 時使用名字搜尋，例如「給予VIP 政佑 永久」。
    回傳：(target_row, error_msg, rest_after_target)
    """
    raw = (getattr(event.message, "text", "") or "").replace("　", " ").strip()
    prefix = (command_prefix or "").replace("　", " ").strip()
    if raw.startswith(prefix):
        rest = raw[len(prefix):].strip()
    else:
        rest = re.sub(r"^" + re.escape(prefix) + r"\s*", "", raw).strip()

    mentionees = _get_mentionees(event)
    if mentionees:
        first = mentionees[0]
        target_user_id = _mention_attr(first, "user_id", None)
        if target_user_id:
            rest_without_mention = _remove_first_mention_text(rest, first)
            # 如果 index 是整則訊息的位置而不是 rest 的位置，fallback 再清一次 @名稱。
            if rest_without_mention == rest:
                rest_without_mention = re.sub(r"@\S+", "", rest, count=1).strip()
            target = _get_player_row_by_user_id(group_id, target_user_id, "成員")
            return target, None, rest_without_mention.strip()

    parts = rest.split(maxsplit=1)
    if not parts:
        return None, "❌ 請輸入玩家名稱，或直接 @tag 對方。", ""
    target_name = parts[0].strip()
    remaining = parts[1].strip() if len(parts) > 1 else ""
    target, error_msg = find_player_by_name(group_id, target_name)
    return target, error_msg, remaining


def parse_vip_duration_text(days_text):
    value = (days_text or "").replace(" ", "").replace("　", "").strip()
    if value.upper().startswith("VIP"):
        value = value[3:]
    if value in ["永遠", "永久VIP", "VIP永久"]:
        value = "永久"
    return value

def parse_vip_command_args(text, prefix):
    """解析 VIP 指令，支援半形/全形空白與 VIP永久/VIP7天。

    可用格式：
    給予VIP 政佑 7天
    給予VIP 政佑 30天
    給予VIP 政佑 90天
    給予VIP 政佑 永久
    給予VIP 政佑 VIP永久
    給予VIP　政佑　永久
    """
    raw = (text or "").replace("　", " ").strip()
    cmd = (prefix or "").strip()

    # 移除指令開頭，允許「給予VIP 政佑 永久」或「給予VIP政佑 永久」
    if raw.startswith(cmd):
        rest = raw[len(cmd):].strip()
    else:
        rest = re.sub(r"^\s*(給予VIP|延長VIP)\s*", "", raw).strip()

    if not rest:
        return "", ""

    parts = rest.split()
    if len(parts) < 2:
        return "", ""

    # 支援「90 天」這種分開輸入
    if len(parts) >= 3 and parts[-1] == "天" and parts[-2].isdigit():
        days_text = parts[-2] + "天"
        target_name = " ".join(parts[:-2]).strip()
    else:
        days_text = parts[-1].strip()
        target_name = " ".join(parts[:-1]).strip()

    days_text = days_text.replace(" ", "").strip()
    # 容錯：VIP永久 / VIP7天 / vip30天
    if days_text.upper().startswith("VIP"):
        days_text = days_text[3:]
    if days_text in ["永遠", "永久VIP", "VIP永久"]:
        days_text = "永久"

    return target_name, days_text



def _safe_reply(reply_token, text):
    try:
        line_bot_api.reply_message(reply_token, TextSendMessage(text=text))
    except Exception:
        pass


def _normalize_spaces(text):
    return (text or "").replace("　", " ").strip()


def _find_target_for_give(event, group_id, command_prefix):
    """
    給予系統專用解析：
    - 有 @tag：直接用 mention 的 user_id 找玩家，剩餘文字當參數。
    - 沒 @tag：用第一段當名字搜尋，剩餘文字當參數。
    """
    raw = _normalize_spaces(getattr(event.message, "text", ""))
    prefix = _normalize_spaces(command_prefix)
    rest = raw[len(prefix):].strip() if raw.startswith(prefix) else raw

    mentionees = _get_mentionees(event)
    if mentionees:
        first = mentionees[0]
        target_user_id = _mention_attr(first, "user_id", None)
        if target_user_id:
            # mention index 是整則文字的位置，要先從 raw 移除再去掉指令前綴
            cleaned_full = _remove_first_mention_text(raw, first)
            if cleaned_full == raw:
                cleaned_full = re.sub(r"@\S+", "", raw, count=1).strip()
            if cleaned_full.startswith(prefix):
                rest_after_target = cleaned_full[len(prefix):].strip()
            else:
                rest_after_target = cleaned_full.strip()
            target = _get_player_row_by_user_id(group_id, target_user_id, "成員")
            return target, None, rest_after_target

    parts = rest.split(maxsplit=1)
    if not parts:
        return None, "❌ 請輸入玩家名稱，或直接 @tag 對方。", ""
    target_name = parts[0].strip()
    rest_after_target = parts[1].strip() if len(parts) > 1 else ""
    target, err = find_player_by_name(group_id, target_name)
    return target, err, rest_after_target


def _display_target_name(group_id, target):
    try:
        return name_with_badge(group_id, target["user_id"], target.get("name") or "成員")
    except Exception:
        try:
            return target.get("name") or "成員"
        except Exception:
            return "成員"


def _handle_give_commands(event, text, group_id, user_id):
    """
    V2.3.1 統一給予系統。
    放在訊息分流前段，避免被其他 startswith 攔截。
    回傳 True 表示已處理。
    """
    give_prefixes = [
        "給予VIP", "延長VIP", "收回VIP", "查看VIP", "VIP紀錄",
        "給予稱號", "收回稱號", "稱號紀錄",
        "給予徽章", "收回徽章",
        "給予金幣", "給予彩虹幣", "給予經驗", "給予等級",
    ]
    if not any(text.startswith(prefix) for prefix in give_prefixes):
        return False

    try:
        if not is_admin(group_id, user_id):
            _safe_reply(event.reply_token, "❌ 你沒有群長權限。")
            return True

        # VIP
        if text.startswith("給予VIP"):
            target, err, days_text = _find_target_for_give(event, group_id, "給予VIP")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            days_text = parse_vip_duration_text(days_text)
            if not target or not days_text:
                _safe_reply(event.reply_token, "格式：給予VIP @對方 7天 / 30天 / 90天 / 永久\n給自己可用：給予VIP 名字 永久")
                return True
            ok, msg = grant_vip(group_id, target["user_id"], days_text, user_id)
            _safe_reply(event.reply_token, f"{msg}\n👤 {_display_target_name(group_id, target)}")
            return True

        if text.startswith("延長VIP"):
            target, err, days_text = _find_target_for_give(event, group_id, "延長VIP")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            days_text = parse_vip_duration_text(days_text)
            if not target or not days_text:
                _safe_reply(event.reply_token, "格式：延長VIP @對方 30天\n給自己可用：延長VIP 名字 30天")
                return True
            ok, msg = extend_vip(group_id, target["user_id"], days_text, user_id)
            _safe_reply(event.reply_token, f"{msg}\n👤 {_display_target_name(group_id, target)}")
            return True

        if text.startswith("收回VIP"):
            target, err, _ = _find_target_for_give(event, group_id, "收回VIP")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            if not target:
                _safe_reply(event.reply_token, "格式：收回VIP @對方\n收回自己可用：收回VIP 名字")
                return True
            cancel_vip(group_id, target["user_id"], user_id)
            _safe_reply(event.reply_token, f"✅ 已收回 VIP。\n👤 {_display_target_name(group_id, target)}")
            return True

        if text.startswith("查看VIP"):
            target, err, _ = _find_target_for_give(event, group_id, "查看VIP")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            if not target:
                _safe_reply(event.reply_token, "格式：查看VIP @對方\n查看自己可用：查看VIP 名字")
                return True
            _safe_reply(event.reply_token, vip_status_message(group_id, target["user_id"], _display_target_name(group_id, target)))
            return True

        if text.startswith("VIP紀錄"):
            target, err, _ = _find_target_for_give(event, group_id, "VIP紀錄")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            if not target:
                _safe_reply(event.reply_token, "格式：VIP紀錄 @對方\n查看自己可用：VIP紀錄 名字")
                return True
            _safe_reply(event.reply_token, vip_record_message(group_id, target["user_id"], _display_target_name(group_id, target)))
            return True

        # 稱號
        if text.startswith("給予稱號"):
            target, err, title_name = _find_target_for_give(event, group_id, "給予稱號")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            if not target or not title_name:
                _safe_reply(event.reply_token, "格式：給予稱號 @對方 稱號名稱\n給自己可用：給予稱號 名字 稱號名稱")
                return True
            ok, msg = grant_title_to_user(group_id, target["user_id"], title_name, user_id)
            _safe_reply(event.reply_token, f"{msg}\n👤 {_display_target_name(group_id, target)}")
            return True

        if text.startswith("收回稱號"):
            target, err, title_name = _find_target_for_give(event, group_id, "收回稱號")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            if not target or not title_name:
                _safe_reply(event.reply_token, "格式：收回稱號 @對方 稱號名稱\n收回自己可用：收回稱號 名字 稱號名稱")
                return True
            ok, msg = revoke_title_from_user(group_id, target["user_id"], title_name, user_id)
            _safe_reply(event.reply_token, f"{msg}\n👤 {_display_target_name(group_id, target)}")
            return True

        if text.startswith("稱號紀錄"):
            target, err, _ = _find_target_for_give(event, group_id, "稱號紀錄")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            if not target:
                _safe_reply(event.reply_token, "格式：稱號紀錄 @對方\n查看自己可用：稱號紀錄 名字")
                return True
            _safe_reply(event.reply_token, title_record_message(group_id, target["user_id"], _display_target_name(group_id, target)))
            return True

        # 徽章
        if text.startswith("給予徽章"):
            target, err, badge_name = _find_target_for_give(event, group_id, "給予徽章")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            if not target or not badge_name:
                _safe_reply(event.reply_token, "格式：給予徽章 @對方 徽章名稱\n給自己可用：給予徽章 名字 徽章名稱")
                return True
            ok, msg = grant_badge_by_name(group_id, target["user_id"], badge_name)
            if ok:
                msg += f"\n目前顯示：{_display_target_name(group_id, target)}"
            _safe_reply(event.reply_token, msg)
            return True

        if text.startswith("收回徽章"):
            target, err, badge_name = _find_target_for_give(event, group_id, "收回徽章")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            if not target or not badge_name:
                _safe_reply(event.reply_token, "格式：收回徽章 @對方 徽章名稱\n收回自己可用：收回徽章 名字 徽章名稱")
                return True
            ok, msg = revoke_badge_by_name(group_id, target["user_id"], badge_name)
            _safe_reply(event.reply_token, msg)
            return True

        # 數值
        if text.startswith("給予金幣") or text.startswith("給予彩虹幣"):
            prefix = "給予彩虹幣" if text.startswith("給予彩虹幣") else "給予金幣"
            target, err, amount_text = _find_target_for_give(event, group_id, prefix)
            if err:
                _safe_reply(event.reply_token, err)
                return True
            try:
                amount = int((amount_text or "").strip())
            except Exception:
                _safe_reply(event.reply_token, f"格式：{prefix} @對方 數量\n給自己可用：{prefix} 名字 數量")
                return True
            add_coins(group_id, target["user_id"], amount)
            _safe_reply(event.reply_token, f"👑 已給予 {_display_target_name(group_id, target)} {amount} 彩虹幣。")
            return True

        if text.startswith("給予經驗"):
            target, err, amount_text = _find_target_for_give(event, group_id, "給予經驗")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            try:
                amount = int((amount_text or "").strip())
            except Exception:
                _safe_reply(event.reply_token, "格式：給予經驗 @對方 數量\n給自己可用：給予經驗 名字 數量")
                return True
            add_exp(group_id, target["user_id"], amount)
            _safe_reply(event.reply_token, f"👑 已給予 {_display_target_name(group_id, target)} {amount} 經驗。")
            return True

        if text.startswith("給予等級"):
            target, err, level_text = _find_target_for_give(event, group_id, "給予等級")
            if err:
                _safe_reply(event.reply_token, err)
                return True
            try:
                level = int((level_text or "").strip())
            except Exception:
                _safe_reply(event.reply_token, "格式：給予等級 @對方 等級\n給自己可用：給予等級 名字 等級")
                return True
            conn = get_connection()
            with conn.cursor() as c:
                c.execute("UPDATE players SET level=%s WHERE group_id=%s AND user_id=%s", (level, group_id, target["user_id"]))
                conn.commit()
            conn.close()
            _safe_reply(event.reply_token, f"👑 已將 {_display_target_name(group_id, target)} 設為 Lv.{level}。")
            return True

        return False
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("GIVE SYSTEM ERROR:", repr(e))
        _safe_reply(event.reply_token, f"❌ 給予系統發生錯誤：{type(e).__name__}\n{str(e)[:120]}")
        return True


def parse_hours_from_text(text, default_hours=1):
    # 支援：開始狂歡、開始狂歡 2小時、開始狂歡 2
    parts = text.split()
    if len(parts) < 2:
        return default_hours
    raw = parts[1].replace("小時", "").strip()
    try:
        hours = int(raw)
        return max(1, min(hours, 6))
    except ValueError:
        return default_hours


def is_manual_carnival_active(group_id):
    return is_activity_active(group_id, "manual_carnival_until")


def is_chest_rain_active(group_id):
    return is_activity_active(group_id, "chest_rain_until")


def is_wheel_boost_active(group_id):
    return is_activity_active(group_id, "wheel_boost_until")


def format_activity_status(group_id):
    hour, start, end, daily_status = carnival_status(group_id)

    manual_left = activity_remaining_seconds(group_id, "manual_carnival_until")
    chest_left = activity_remaining_seconds(group_id, "chest_rain_until")
    wheel_left = activity_remaining_seconds(group_id, "wheel_boost_until")

    manual_status = f"🟢 進行中｜剩餘 {format_remaining(manual_left)}" if manual_left > 0 else "⚪ 未開啟"
    chest_status = f"🟢 進行中｜剩餘 {format_remaining(chest_left)}" if chest_left > 0 else "⚪ 未開啟"
    wheel_status = f"🟢 進行中｜剩餘 {format_remaining(wheel_left)}" if wheel_left > 0 else "⚪ 未開啟"

    return (
        "📢 目前活動狀態\n\n"
        f"🌈 每日盲抽狂歡：{hour:02d}:00～{hour+1:02d}:00\n"
        f"狀態：{daily_status}\n\n"
        f"👑 群長狂歡模式：{manual_status}\n"
        f"🎁 寶箱雨模式：{chest_status}\n"
        f"🎰 輪盤大暴送：{wheel_status}\n\n"
        "效果說明：\n"
        "🌈 狂歡：聊天 EXP ×2\n"
        "🎁 寶箱雨：聊天寶箱率提升至 15%\n"
        "🎰 輪盤：最低 250、最高 1000 彩虹幣"
    )


def announce_group(group_id, message):
    if group_id == "PRIVATE":
        return False
    try:
        line_bot_api.push_message(group_id, TextSendMessage(text=message))
        return True
    except Exception as e:
        # 不要靜默吃掉錯誤，先記錄到 bot_events，方便之後查問題。
        try:
            set_event_value(group_id, "last_push_error", str(e)[:300])
        except Exception:
            pass
        return False


def carnival_multiplier(group_id):
    return 2 if (is_carnival_active(group_id) or is_manual_carnival_active(group_id)) else 1


def get_chest_rate(group_id):
    if is_chest_rain_active(group_id):
        return 0.15
    return CHAT_CHEST_RATE * carnival_multiplier(group_id)


def get_wheel_range(group_id):
    if is_wheel_boost_active(group_id):
        return 250, 1000
    return WHEEL_MIN, WHEEL_MAX




# ===== V5.4.6：全域機器人模式 =====
def get_system_mode(group_id):
    mode = str(get_event_value(group_id, "system_mode", "normal") or "normal").strip().lower()
    return mode if mode in ("normal", "silent", "maintenance") else "normal"


def set_system_mode(group_id, mode):
    mode = str(mode or "normal").strip().lower()
    if mode not in ("normal", "silent", "maintenance"):
        raise ValueError("模式只支援：正常、靜音、維護")
    set_event_value(group_id, "system_mode", mode)


def system_mode_label(group_id):
    return {"normal": "🟢 正常模式", "silent": "🟡 靜音模式", "maintenance": "🔴 維護模式"}[get_system_mode(group_id)]


def parse_system_mode(value):
    value = str(value or "").strip().replace("模式", "")
    mapping = {"正常": "normal", "開啟": "normal", "靜音": "silent", "推播關閉": "silent", "維護": "maintenance"}
    return mapping.get(value)


# ===== 機器人回應開關 =====
def is_bot_response_enabled(group_id):
    return get_event_value(group_id, "bot_response_enabled", "1") != "0"


def set_bot_response_enabled(group_id, enabled):
    set_event_value(group_id, "bot_response_enabled", "1" if enabled else "0")


def get_bot_mode(group_id):
    # command：只回應指令；auto：一般聊天也可能觸發掉寶提示。
    mode = get_event_value(group_id, "bot_response_mode", "command")
    return mode if mode in ["command", "auto"] else "command"


def set_bot_mode(group_id, mode):
    if mode not in ["command", "auto"]:
        mode = "command"
    set_event_value(group_id, "bot_response_mode", mode)


def is_command_mode(group_id):
    return get_bot_mode(group_id) == "command"


def bot_response_status_message(group_id):
    status = "🟢 開啟中" if is_bot_response_enabled(group_id) else "🔴 已關閉"
    mode = "✅ 指令模式" if is_command_mode(group_id) else "🔔 全自動模式"
    return (
        "🤖 機器人回應狀態\n\n"
        f"目前：{status}\n"
        f"模式：{mode}\n\n"
        "群長指令：\n"
        "開啟機器人\n"
        "關閉機器人\n"
        "設定機器人模式 指令\n"
        "設定機器人模式 全自動\n"
        "機器人狀態"
    )




# ===== V2.2.4：控制中心與公告推播 =====
def control_center_message():
    return (
        "👑 Rainbow Life 控制中心\n\n"
        "1️⃣ 🤖 機器人設定\n"
        "2️⃣ 📢 公告推播\n"
        "3️⃣ 📋 任務管理\n"
        "4️⃣ 🏅 徽章管理\n"
        "5️⃣ 🏆 成就管理（預留）\n"
        "6️⃣ 🛍️ 商店管理\n"
        "7️⃣ 🏷️ 稱號管理\n"
        "8️⃣ 🎉 活動管理\n"
        "9️⃣ ⏰ 排程中心\n\n"
        "常用指令：\n"
        "機器人設定\n"
        "公告設定\n"
        "排程中心\n"
        "任務管理\n"
        "商品管理\n"
        "稱號管理\n"
        "指令"
    )


def command_list_message():
    return (
        "📖 指令總表\n\n"
        "👤 玩家指令\n"
        "簽到｜補簽 1天｜簽到紀錄｜我的狀態｜我的資料\n"
        "設定生日 07/10｜我的生日｜今日壽星｜生日名單｜刪除生日\n"
        "每日任務｜每週任務｜每月任務｜每季任務｜我的任務\n"
        "排行榜｜金幣排行榜｜簽到排行榜｜連續簽到榜\n\n"
        "🛍️ 商店指令\n"
        "商店｜活動商店｜我的稱號\n"
        "購買 商品名稱｜挑選稱號 名稱｜裝備稱號 名稱\n\n"
        "🎉 活動指令\n"
        "今日活動｜狂歡時段｜活動狀態｜每日運勢｜幸運輪盤\n\n"
        "👑 群長指令\n"
        "控制中心｜機器人設定｜公告設定｜排程中心｜任務管理\n"
        "開啟機器人｜關閉機器人\n"
        "設定機器人模式 指令｜設定機器人模式 全自動\n"
        "設定公告 內容｜查看公告｜設定公告時間 HH:MM｜開啟公告推播｜關閉公告推播｜立即推播公告\n"
        "開始狂歡｜停止狂歡｜開始寶箱雨｜停止寶箱雨｜開始輪盤｜停止輪盤"
    )


def bot_settings_message(group_id):
    status = "🟢 開啟中" if is_bot_response_enabled(group_id) else "🔴 已關閉"
    mode = "✅ 指令模式" if is_command_mode(group_id) else "🔔 全自動模式"
    drop_notice = "關閉（靜默）" if is_command_mode(group_id) else "開啟"
    return (
        "🤖 機器人設定\n\n"
        f"目前狀態：{status}\n"
        f"回應模式：{mode}\n"
        f"掉寶通知：{drop_notice}\n"
        f"活動公告：✅ 保留通知\n\n"
        "可用指令：\n"
        "開啟機器人\n"
        "關閉機器人\n"
        "設定機器人模式 指令\n"
        "設定機器人模式 全自動\n"
        "機器人狀態"
    )




def vip_management_message():
    return (
        "💎 VIP 管理\n\n"
        "群長指令：\n"
        "給予VIP 名字 7天\n"
        "給予VIP 名字 30天\n"
        "給予VIP 名字 90天\n"
        "給予VIP 名字 永久\n"
        "延長VIP 名字 30天\n"
        "收回VIP 名字\n"
        "查看VIP 名字\n"
        "VIP紀錄 名字\n\n"
        "玩家指令：\n"
        "我的VIP\n"
        "VIP禮包"
    )


def is_announcement_enabled(group_id):
    return get_event_value(group_id, "announcement_enabled", "0") == "1"


def set_announcement_enabled(group_id, enabled):
    set_event_value(group_id, "announcement_enabled", "1" if enabled else "0")


def get_announcement_content(group_id):
    return get_event_value(group_id, "announcement_content", "")


def set_announcement_content(group_id, content):
    set_event_value(group_id, "announcement_content", content)


def clear_announcement_content(group_id):
    set_event_value(group_id, "announcement_content", "")


def get_announcement_time(group_id):
    return get_event_value(group_id, "announcement_time", "20:00")


def set_announcement_time(group_id, time_text):
    set_event_value(group_id, "announcement_time", time_text)


def valid_hhmm(time_text):
    try:
        hh, mm = time_text.split(":")
        hh = int(hh)
        mm = int(mm)
        return 0 <= hh <= 23 and 0 <= mm <= 59
    except Exception:
        return False


def announcement_settings_message(group_id):
    enabled = "✅ 開啟" if is_announcement_enabled(group_id) else "❌ 關閉"
    content = get_announcement_content(group_id) or "尚未設定"
    push_time = get_announcement_time(group_id)
    return (
        "📢 公告推播設定\n\n"
        f"狀態：{enabled}\n"
        f"時間：{push_time}\n"
        f"內容：\n{content}\n\n"
        "可用指令：\n"
        "設定公告 內容\n"
        "查看公告\n"
        "設定公告時間 HH:MM\n"
        "開啟公告推播\n"
        "關閉公告推播\n"
        "立即推播公告"
    )


def format_announcement_message(content, title="📢 群長公告"):
    now = tw_now_real()
    return (
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{title}\n\n"
        f"🕒 {now.strftime('%Y/%m/%d %H:%M')}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{content}\n\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def push_announcement(group_id):
    content = get_announcement_content(group_id)
    if not content:
        return False, "❌ 尚未設定公告內容。", ""
    announcement_text = format_announcement_message(content)
    ok = announce_group(group_id, announcement_text)
    if ok:
        return True, "✅ 公告已推播。", announcement_text
    return False, "⚠️ 公告 Push 失敗，已改用回覆方式顯示公告。", announcement_text


def maybe_push_scheduled_announcement(group_id):
    if group_id == "PRIVATE":
        return
    if not is_announcement_enabled(group_id):
        return
    content = get_announcement_content(group_id)
    if not content:
        return
    time_text = get_announcement_time(group_id)
    if not valid_hhmm(time_text):
        return

    now = tw_now_real()
    today_key = now.date().isoformat()
    if get_event_value(group_id, "announcement_last_push_date", "") == today_key:
        return

    hh, mm = [int(x) for x in time_text.split(":")]
    now_minutes = now.hour * 60 + now.minute
    target_minutes = hh * 60 + mm
    # Render 無常駐排程，改成有人發言時檢查；只在設定時間後 30 分鐘內補推一次。
    if 0 <= now_minutes - target_minutes <= 30:
        ok = announce_group(group_id, format_announcement_message(content, title="📅 每日公告"))
        if ok:
            set_event_value(group_id, "announcement_last_push_date", today_key)


# ===== V2.2.7：排程中心 =====
def run_scheduler_tick(group_id):
    """
    Render Web Service 沒有常駐排程器，因此採用「事件觸發式排程」。
    只要群組有人傳訊息或使用指令，就會檢查公告與活動是否到點。
    防重複推播交給各功能自己的 last_push key 控制。
    """
    if group_id == "PRIVATE":
        return
    set_event_value(group_id, "scheduler_last_tick", tw_now_real().strftime("%Y/%m/%d %H:%M:%S"))
    maybe_push_scheduled_announcement(group_id)
    maybe_announce_carnival(group_id)
    maybe_announce_manual_carnival_end(group_id)
    maybe_announce_birthdays(group_id)


def scheduler_center_message(group_id):
    hour, start, end, daily_status = carnival_status(group_id)
    announcement_enabled = "✅ 開啟" if is_announcement_enabled(group_id) else "❌ 關閉"
    announcement_time = get_announcement_time(group_id)
    announcement_last = get_event_value(group_id, "announcement_last_push_date", "尚未推播")
    last_tick = get_event_value(group_id, "scheduler_last_tick", "尚未檢查")

    manual_left = activity_remaining_seconds(group_id, "manual_carnival_until")
    chest_left = activity_remaining_seconds(group_id, "chest_rain_until")
    wheel_left = activity_remaining_seconds(group_id, "wheel_boost_until")

    return (
        "⏰ Rainbow Life 排程中心\n\n"
        "目前採用：事件觸發式排程\n"
        "說明：Render 沒有常駐背景排程，所以群內有人發言或使用指令時會自動檢查是否到點。\n\n"
        "📢 公告推播\n"
        f"狀態：{announcement_enabled}\n"
        f"時間：{announcement_time}\n"
        f"今日推播紀錄：{announcement_last}\n\n"
        "🌈 每日狂歡\n"
        f"時間：{hour:02d}:00～{hour+1:02d}:00\n"
        f"狀態：{daily_status}\n\n"
        "👑 群長活動\n"
        f"狂歡模式：{'🟢 ' + format_remaining(manual_left) if manual_left > 0 else '⚪ 未開啟'}\n"
        f"寶箱雨：{'🟢 ' + format_remaining(chest_left) if chest_left > 0 else '⚪ 未開啟'}\n"
        f"輪盤大暴送：{'🟢 ' + format_remaining(wheel_left) if wheel_left > 0 else '⚪ 未開啟'}\n\n"
        f"🕒 上次排程檢查：{last_tick}\n\n"
        "可用指令：\n"
        "排程中心｜排程狀態｜立即檢查排程"
    )


def is_command_text(text):
    # 只有這些明確指令會觸發機器人回覆。
    exact_commands = {
        "測試", "功能", "選單", "首頁", "功能中心", "查看群規",
        "排行榜資料", "等級排行榜", "等級榜", "今日聊天榜", "昨日聊天榜", "貼圖排行榜",
        "控制中心", "指令", "機器人設定", "公告設定", "排程中心", "排程狀態", "立即檢查排程",
        "查看公告", "查看公告時間", "刪除公告", "立即推播公告", "推播公告",
        "開啟公告推播", "關閉公告推播",
        "機器人狀態", "回應狀態", "機器人模式",
        "開啟機器人", "機器人開啟", "開啟回應", "解除安靜",
        "關閉機器人", "機器人關閉", "關閉回應", "機器人安靜",
        "今日活動", "狂歡時段", "活動狀態",
        "開始寶箱雨", "寶箱雨模式", "停止寶箱雨",
        "開始輪盤", "開始輪盤活動", "輪盤大暴送", "停止輪盤",
        "停止狂歡",
        "簽到", "補簽", "簽到紀錄", "我的簽到",
        "生日設定", "生日輸入說明", "我的生日", "今日壽星", "生日名單", "即將生日", "刪除生日",
        "每日運勢", "幸運輪盤", "幸運轉盤",
        "商店", "活動商店",
        "VIP稱號", "查看 VIP稱號",
        "我的VIP", "VIP禮包", "VIP管理",
        "我的稱號", "卸下稱號", "商店管理", "商品管理列表", "稱號管理", "VIP稱號管理",
        "查看成員資料",
        "簽到排行榜", "累積簽到榜", "連續簽到榜", "我的排名", "簽到排名",
        "我的狀態", "我的資料", "金幣排行榜", "排行榜",
        "今日消費", "昨日消費", "總消費", "金庫", "金庫紀錄",
        "每日任務", "每週任務", "每月任務", "每季任務", "我的任務",
        "任務管理", "每日任務榜", "每週任務榜", "每月任務榜", "每季任務榜",
        "徽章列表", "我的徽章",
        "活動商店", "活動商品", "禁言名單",
    }

    prefixes = [
        "設定機器人模式 ", "設定群規 ",
        "設定公告 ", "修改公告 ", "設定公告時間 ",
        "設定VIP價格 ", "設定VIP倍率 ",
        "綁定 群長",
        "生日設定 ", "設定生日 ",
        "群長發福利 ", "群長發經驗 ", "群長發等級 ",
        "開始狂歡",
        "購買 ", "購買VIP", "購買 VIP",
        "挑選稱號 ", "購買稱號 ", "裝備稱號 ", "佩戴稱號 ",
        "給予稱號 ", "收回稱號 ", "稱號紀錄 ",
        "給予VIP", "延長VIP", "收回VIP", "查看VIP", "VIP紀錄",
        "新增商品 ", "刪除商品 ", "修改商品 ", "修改商品價格 ", "上架商品 ", "下架商品 ",
        "新增稱號 ", "新增VIP稱號 ", "刪除稱號 ", "刪除VIP稱號 ", "修改稱號價格 ",
        "給予金幣 ", "給予經驗 ", "給予等級 ",
        "新增每日任務 ", "新增每週任務 ", "新增每月任務 ", "新增每季任務 ",
        "刪除任務 ", "停用任務 ", "啟用任務 ", "領取任務 ",
        "給予徽章 ", "收回徽章 ", "新增徽章 ", "停用徽章 ",
        "給予活動道具 ",
        "消費紀錄 ",
        "兌換 ",
        "活動商品 ",
        "補簽 ",
        "禁言 ", "解除禁言 ", "查看禁言 ",
        "機器人模式 ",
    ]

    return text in exact_commands or any(text.startswith(prefix) for prefix in prefixes)


def handle_silent_chat(group_id, user_id, chat_text=""):

    """指令模式下處理一般聊天，不回覆訊息但仍累積獎勵。

    玩家資料必須在此函式內重新讀取，避免引用 handle_message() 的區域變數
    而造成 ``NameError: name 'player' is not defined``。
    """
    current_player = get_player(group_id, user_id)

    # 理論上 handle_message() 已建立玩家；這裡再加保護，避免舊資料或競態狀況。
    if current_player is None:
        ensure_player(group_id, user_id, "成員")
        current_player = get_player(group_id, user_id)

    mult = carnival_multiplier(group_id)
    fortune_exp_mult, fortune_coin_mult = get_fortune_multipliers(current_player or {})
    chat_exp_gain = max(0, round(CHAT_EXP * mult * fortune_exp_mult))
    chest_rate = get_chest_rate(group_id)

    exp_result = add_exp(group_id, user_id, chat_exp_gain, source="chat")
    update_task_progress(group_id, user_id, "chat", 1)

    chest_dropped = random.random() < chest_rate
    if chest_dropped:
        chest_coins = max(0, round(CHAT_CHEST_COIN * fortune_coin_mult))
        add_coins(group_id, user_id, chest_coins)
        update_task_progress(group_id, user_id, "chest", 1)
        grant_active_event_items(group_id, user_id, "chest")

def get_line_display_name(group_id, user_id):
    """
    取得 LINE 暱稱。
    成功：回傳最新 LINE 名稱，後續 ensure_player 會同步到資料庫。
    失敗：保留資料庫原本名稱，避免被覆蓋成「冒險者」。
    """
    try:
        if group_id != "PRIVATE":
            profile = line_bot_api.get_group_member_profile(group_id, user_id)
        else:
            profile = line_bot_api.get_profile(user_id)
        display_name = (profile.display_name or "").strip()
        if display_name:
            return display_name
    except Exception:
        pass

    # LINE API 暫時抓不到時，優先使用資料庫既有名稱
    try:
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("""
                SELECT name
                FROM players
                WHERE group_id=%s AND user_id=%s
            """, (group_id, user_id))
            row = c.fetchone()
        conn.close()
        if row and row.get("name") and row["name"] not in ["冒險者", "成員", "未知成員"]:
            return row["name"]
    except Exception:
        pass

    return "成員"




def _ensure_namecard_tables():
    """確保名片資料表存在；與個人中心的 player_profiles / player_privacy 共用。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS player_profiles(
                group_id TEXT NOT NULL,user_id TEXT NOT NULL,
                nickname TEXT DEFAULT '',gender TEXT DEFAULT '',region TEXT DEFAULT '',age TEXT DEFAULT '',
                relationship TEXT DEFAULT '',height TEXT DEFAULT '',weight TEXT DEFAULT '',occupation TEXT DEFAULT '',
                interests TEXT DEFAULT '',bio TEXT DEFAULT '',instagram TEXT DEFAULT '',threads TEXT DEFAULT '',
                mbti TEXT DEFAULT '',avatar_url TEXT DEFAULT '',theme TEXT DEFAULT 'rainbow_neon',
                notifications BOOLEAN DEFAULT TRUE,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,user_id))""")
            c.execute("""CREATE TABLE IF NOT EXISTS player_privacy(
                group_id TEXT NOT NULL,user_id TEXT NOT NULL,field_name TEXT NOT NULL,
                visibility TEXT DEFAULT 'group',PRIMARY KEY(group_id,user_id,field_name))""")
            c.execute("""CREATE TABLE IF NOT EXISTS player_card_likes(
                group_id TEXT NOT NULL,target_user_id TEXT NOT NULL,from_user_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,target_user_id,from_user_id))""")
            c.execute("""CREATE TABLE IF NOT EXISTS player_card_favorites(
                group_id TEXT NOT NULL,user_id TEXT NOT NULL,target_user_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,user_id,target_user_id))""")
            c.execute("""CREATE TABLE IF NOT EXISTS player_card_visitors(
                group_id TEXT NOT NULL,target_user_id TEXT NOT NULL,viewer_user_id TEXT NOT NULL,
                viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,target_user_id,viewer_user_id))""")
        conn.commit()
    finally:
        conn.close()


def _namecard_profile(group_id, user_id):
    _ensure_namecard_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT * FROM player_profiles WHERE group_id=%s AND user_id=%s", (group_id, user_id))
            profile = c.fetchone() or {}
            c.execute("SELECT field_name,visibility FROM player_privacy WHERE group_id=%s AND user_id=%s", (group_id, user_id))
            privacy = {str(r.get('field_name')): str(r.get('visibility') or 'group') for r in (c.fetchall() or [])}
        return profile, privacy
    finally:
        conn.close()


def _line_avatar_url(group_id, user_id, fallback=''):
    try:
        profile = line_bot_api.get_group_member_profile(group_id, user_id) if group_id != 'PRIVATE' else line_bot_api.get_profile(user_id)
        return str(getattr(profile, 'picture_url', '') or fallback or '')
    except Exception:
        return str(fallback or '')


def _safe_card_text(value, default='—', limit=120):
    value = str(value or '').strip()
    if not value:
        return default
    return value[:limit]


def _namecard_theme(profile=None):
    """聊天室名片與 Rainbow Life 全站主題保持一致。"""
    p = flex_palette()
    return {
        "label": p.get("label", "🌈 Rainbow Life"),
        "bg": p.get("bg", "#120D2A"),
        "card": p.get("card", "#25164D"),
        "panel": p.get("head", p.get("card", "#25164D")),
        "accent": p.get("accent", "#7557E8"),
        "accent2": p.get("accent2", "#FF69C9"),
        "text": p.get("text", "#FFFFFF"),
        "sub": p.get("sub", "#E4D9FF"),
        "border": p.get("border", "#A879FF"),
        "button": p.get("accent", "#7557E8"),
    }, p.get("key", "rainbow-starfield")

def _namecard_zodiac(birthday):
    raw = str(birthday or "").strip().replace("-", "/")
    if not raw:
        return ""
    try:
        parts = [int(part) for part in raw.split("/") if part != ""]
        if len(parts) == 3:
            _, month, day = parts
        elif len(parts) == 2:
            month, day = parts
        else:
            return ""
        starts = [
            ((1, 20), "水瓶座"), ((2, 19), "雙魚座"), ((3, 21), "牡羊座"),
            ((4, 20), "金牛座"), ((5, 21), "雙子座"), ((6, 22), "巨蟹座"),
            ((7, 23), "獅子座"), ((8, 23), "處女座"), ((9, 23), "天秤座"),
            ((10, 24), "天蠍座"), ((11, 23), "射手座"), ((12, 22), "摩羯座"),
        ]
        previous = "摩羯座"
        for start, name in starts:
            if (month, day) < start:
                return previous
            previous = name
        return "摩羯座"
    except (TypeError, ValueError):
        return ""



def _namecard_collection_summary(group_id, user_id):
    result = {"achievements": 0, "badges": []}
    try:
        conn = get_connection()
        try:
            with conn.cursor() as c:
                try:
                    c.execute(
                        "SELECT COUNT(*) AS total FROM player_achievement_claims "
                        "WHERE group_id=%s AND user_id=%s",
                        (group_id, user_id),
                    )
                    row = c.fetchone() or {}
                    result["achievements"] = int(row.get("total") or 0)
                except Exception:
                    conn.rollback()
                candidates = [
                    ("player_badges", "badge_name"),
                    ("user_badges", "badge_name"),
                    ("player_badge_inventory", "badge_name"),
                ]
                for table_name, column_name in candidates:
                    try:
                        c.execute(
                            f"SELECT {column_name} AS badge_name FROM {table_name} "
                            "WHERE group_id=%s AND user_id=%s LIMIT 5",
                            (group_id, user_id),
                        )
                        rows = c.fetchall() or []
                        if rows:
                            result["badges"] = [
                                str(row.get("badge_name") or "").strip()
                                for row in rows if str(row.get("badge_name") or "").strip()
                            ]
                            break
                    except Exception:
                        conn.rollback()
        finally:
            conn.close()
    except Exception:
        pass
    return result


def _namecard_flex(group_id, target_user_id, viewer_user_id):
    """V17 Ultimate LINE 名片：輸入名片後直接顯示完整摘要卡。"""
    player = get_player(group_id, target_user_id) or {}
    profile, privacy = _namecard_profile(group_id, target_user_id)
    theme, _ = _namecard_theme(profile)
    is_self = str(target_user_id) == str(viewer_user_id)

    def visible(field):
        return is_self or privacy.get(field, "group") != "private"

    def value(field, fallback=""):
        return profile.get(field) if visible(field) else fallback

    level = max(1, int(player.get("level") or 1))
    exp = max(0, int(player.get("exp") or 0))
    coins = max(0, int(player.get("coins") or 0))
    streak = max(0, int(player.get("streak_count") or 0))
    name = _safe_card_text(value("nickname") or player.get("name") or "成員", "成員", 24)
    gender_raw = str(value("gender") or "").strip()
    gender = "♂" if gender_raw in ("男生", "男性", "男") else ("♀" if gender_raw in ("女生", "女性", "女") else "")
    avatar = _line_avatar_url(group_id, target_user_id, profile.get("avatar_url"))
    equipped_frame = "rainbow_basic"
    try:
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute("SELECT equipped_frame FROM player_frame_settings WHERE group_id=%s AND user_id=%s", (group_id,target_user_id))
                fr = c.fetchone() or {}
                equipped_frame = str(fr.get("equipped_frame") or "rainbow_basic")
        finally:
            conn.close()
    except Exception:
        pass
    frame_colors = {"rainbow_basic":"#B76CFF","star_guard":"#7E71FF","ice_crystal":"#72DFFF","forest_guard":"#65CE70","sweet_heart":"#FF79B2","flame_soul":"#FF6338","diamond_crown":"#FFD45E","leader_glory":"#FFD45E"}
    frame_icons = {"rainbow_basic":"🌈","star_guard":"✨","ice_crystal":"❄️","forest_guard":"🍃","sweet_heart":"💗","flame_soul":"🔥","diamond_crown":"💎","leader_glory":"👑"}
    frame_color = frame_colors.get(equipped_frame, theme["accent"])
    frame_icon = frame_icons.get(equipped_frame, "🌈")
    main_title = _safe_card_text(player.get("custom_title"), "", 18) or rank_title(level)
    birthday_raw = player.get("birthday") if visible("birthday") else ""
    birthday = _safe_card_text(birthday_raw, "未設定", 12)
    zodiac = _namecard_zodiac(birthday_raw) or "未設定"
    region = _safe_card_text(value("region"), "未公開", 18)
    role_badge = get_admin_badge(group_id, target_user_id) or "👤 一般成員"
    collection = _namecard_collection_summary(group_id, target_user_id)

    if equipped_frame == "leader_glory" and avatar:
        frame_asset_url = f"{_game_admin_base_url()}/player/assets/leader-frame.png?v=2"
        avatar_box = {
            "type": "box", "layout": "vertical", "width": "104px", "height": "104px",
            "flex": 0, "position": "relative", "contents": [
                {
                    "type": "image", "url": avatar, "size": "full",
                    "aspectRatio": "1:1", "aspectMode": "cover",
                    "position": "absolute", "offsetTop": "18px", "offsetStart": "18px",
                    "offsetBottom": "18px", "offsetEnd": "18px", "cornerRadius": "40px"
                },
                {
                    "type": "image", "url": frame_asset_url, "size": "full",
                    "aspectRatio": "1:1", "aspectMode": "fit",
                    "position": "absolute", "offsetTop": "0px", "offsetStart": "0px",
                    "offsetBottom": "0px", "offsetEnd": "0px"
                },
            ],
        }
    else:
        avatar_box = ({
            "type": "box", "layout": "vertical", "width": "86px", "height": "86px",
            "cornerRadius": "43px", "backgroundColor": theme["panel"],
            "borderWidth": "3px", "borderColor": frame_color, "flex": 0,
            "contents": [{"type": "image", "url": avatar, "size": "full",
                          "aspectRatio": "1:1", "aspectMode": "cover", "cornerRadius": "43px"}]
        } if avatar else {
            "type": "box", "layout": "vertical", "width": "86px", "height": "86px",
            "cornerRadius": "43px", "backgroundColor": theme["panel"],
            "borderWidth": "3px", "borderColor": frame_color,
            "justifyContent": "center", "alignItems": "center", "flex": 0,
            "contents": [{"type": "text", "text": "👤", "size": "xxl", "align": "center"}]
        })

    body = [
        {
            "type": "box", "layout": "horizontal", "spacing": "md", "alignItems": "center",
            "contents": [
                avatar_box,
                {"type": "box", "layout": "vertical", "flex": 1, "contents": [
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": f"{name} {gender}".strip(), "size": "lg",
                         "weight": "bold", "color": theme["text"], "wrap": True, "flex": 1},
                        {"type": "text", "text": f"Lv.{level}", "size": "sm", "weight": "bold",
                         "color": theme["accent"], "align": "end", "flex": 0},
                    ]},
                    {"type": "text", "text": role_badge, "size": "xs", "weight": "bold",
                     "color": theme["accent2"], "margin": "sm"},
                    {"type": "text", "text": f"✨ {main_title}", "size": "sm", "weight": "bold",
                     "color": theme["text"], "margin": "xs", "wrap": True},
                    {"type": "text", "text": f"♈ {zodiac}　📍 {region}", "size": "xxs",
                     "color": theme["sub"], "margin": "xs", "wrap": True},
                ]},
            ],
        },
        {"type": "separator", "margin": "lg", "color": theme["border"]},
    ]

    body.append({
        "type": "box", "layout": "horizontal", "margin": "md", "paddingAll": "11px",
        "cornerRadius": "15px", "backgroundColor": theme["panel"],
        "contents": [{"type": "text", "text": f"{frame_icon} 已套用靜態頭像框",
                      "size": "xs", "color": theme["sub"], "flex": 1}],
    })

    stat_boxes = []
    for icon, value_text, label in [
        ("⭐", f"{exp:,}", "EXP"),
        ("🌈", f"{coins:,}", "彩虹幣"),
        ("🔥", f"{streak}天", "連續簽到"),
    ]:
        stat_boxes.append({
            "type": "box", "layout": "vertical", "alignItems": "center", "flex": 1,
            "paddingAll": "7px", "cornerRadius": "12px", "backgroundColor": theme["panel"],
            "contents": [
                {"type": "text", "text": f"{icon} {value_text}", "size": "sm", "weight": "bold",
                 "color": theme["text"], "align": "center"},
                {"type": "text", "text": label, "size": "xxs", "color": theme["sub"],
                 "align": "center", "margin": "xs"},
            ],
        })
    body.append({"type": "box", "layout": "horizontal", "spacing": "sm",
                 "margin": "md", "contents": stat_boxes})
    body.append({
        "type": "box", "layout": "horizontal", "margin": "md", "paddingAll": "10px",
        "cornerRadius": "14px", "backgroundColor": theme["panel"],
        "contents": [
            {"type": "text", "text": f"🏆 成就 {collection['achievements']}　🎂 {birthday}",
             "size": "xs", "color": theme["text"], "wrap": True, "flex": 1},
        ],
    })

    full_url = make_public_card_url(group_id, target_user_id)
    footer = []
    if full_url:
        footer.append({
            "type": "button", "style": "primary", "height": "sm", "color": theme["button"],
            "action": {"type": "uri", "label": "✨ 查看完整名片", "uri": full_url},
        })
    if is_self:
        personal_url = make_player_access_url(viewer_user_id, group_id, "/player")
        if personal_url:
            footer.append({
                "type": "button", "style": "secondary", "height": "sm",
                "action": {"type": "uri", "label": "👤 個人中心", "uri": personal_url},
            })

    bubble = {
        "type": "bubble", "size": "kilo",
        "styles": {
            "header": {"backgroundColor": theme["panel"]},
            "body": {"backgroundColor": theme["card"]},
            "footer": {"backgroundColor": theme["card"], "separator": True,
                       "separatorColor": theme["border"]},
        },
        "header": {"type": "box", "layout": "horizontal", "paddingAll": "10px", "contents": [
            {"type": "text", "text": "🌈 Rainbow Life", "size": "sm",
             "weight": "bold", "color": theme["text"], "flex": 1},
            {"type": "text", "text": theme["label"], "size": "xxs",
             "color": theme["accent"], "align": "end"},
        ]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "14px",
                 "backgroundColor": theme["card"], "contents": body},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm",
                   "paddingAll": "10px", "contents": footer or [
                       {"type": "text", "text": "完整名片網址尚未設定",
                        "size": "xs", "align": "center", "color": theme["sub"]}
                   ]},
    }
    return FlexSendMessage(alt_text=f"🌈 {name} 的 Ultimate 名片", contents=bubble)

def _resolve_namecard_target(group_id, query, current_user_id):
    query = str(query or '').strip()
    if not query:
        return current_user_id, None
    query = query.lstrip('@').strip()
    matches = find_player_by_name(group_id, query) or []
    if not matches:
        return None, f'找不到「{query}」的名片。'
    if isinstance(matches, dict):
        matches = [matches]
    exact = [m for m in matches if str(m.get('name') or '').strip().lower() == query.lower()]
    selected = exact[0] if exact else matches[0]
    return selected.get('user_id'), None


def _namecard_reaction(group_id, target_user_id, from_user_id, kind):
    _ensure_namecard_tables()
    conn=get_connection()
    try:
        with conn.cursor() as c:
            if kind=='like':
                c.execute("INSERT INTO player_card_likes(group_id,target_user_id,from_user_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",(group_id,target_user_id,from_user_id))
            elif kind=='favorite':
                c.execute("INSERT INTO player_card_favorites(group_id,user_id,target_user_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",(group_id,from_user_id,target_user_id))
        conn.commit()
    finally:
        conn.close()

def find_player_by_name(group_id, keyword):
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT user_id, name
            FROM players
            WHERE group_id=%s
              AND name LIKE %s
            ORDER BY name ASC
            LIMIT 5
        """, (group_id, f"%{keyword}%"))
        rows = c.fetchall()
    conn.close()

    if len(rows) == 0:
        return None, f"❌ 找不到成員：{keyword}"
    if len(rows) > 1:
        names = "、".join([r["name"] for r in rows])
        return None, f"⚠️ 找到多位成員：{names}\n請輸入更完整的名字。"
    return rows[0], None


def medal(rank):
    if rank == 1:
        return "🥇"
    if rank == 2:
        return "🥈"
    if rank == 3:
        return "🥉"
    return f"{rank}."


def name_with_badge(group_id, user_id, name):
    # 顯示規則：管理身分不覆蓋等級／購買稱號；只在名字前加上簡潔徽章。
    base = display_name_with_badge(group_id, user_id, name)
    role_badge = get_admin_badge(group_id, user_id)
    if role_badge:
        base = f"{role_badge}｜{base}"
    try:
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("SELECT birthday FROM players WHERE group_id=%s AND user_id=%s", (group_id, user_id))
            birthday_row = c.fetchone()
        conn.close()
        if birthday_row and is_birthday_today(birthday_row.get("birthday") or ""):
            base = f"🌈🎂 {base}"
    except Exception:
        pass
    try:
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("SELECT is_vip FROM players WHERE group_id=%s AND user_id=%s", (group_id, user_id))
            row = c.fetchone()
        conn.close()
        if row and is_vip_active(group_id, user_id):
            return f"💎{base}" if base != name else f"💎 {name}"
    except Exception:
        pass
    return base


def get_sign_rank_message(group_id, user_id, mode="month"):
    if mode == "streak":
        title = "🔥 連續簽到排行榜"
        count_col = "streak_count"
        count_label = "連續簽到"
        unit = "天"
    else:
        title = "🏆 累積簽到排行榜"
        count_col = "sign_month_count"
        count_label = "累積簽到"
        unit = "天"

    conn = get_connection()
    with conn.cursor() as c:
        c.execute(f"""
            SELECT user_id, name, {count_col} AS count_value
            FROM players
            WHERE group_id=%s
              AND COALESCE({count_col}, 0) > 0
            ORDER BY {count_col} DESC, name ASC
            LIMIT 10
        """, (group_id,))
        top_rows = c.fetchall()

        c.execute(f"""
            SELECT rank_no, count_value
            FROM (
                SELECT
                    user_id,
                    COALESCE({count_col}, 0) AS count_value,
                    ROW_NUMBER() OVER (
                        ORDER BY COALESCE({count_col}, 0) DESC, name ASC
                    ) AS rank_no
                FROM players
                WHERE group_id=%s
            ) ranked
            WHERE user_id=%s
        """, (group_id, user_id))
        my_row = c.fetchone()
    conn.close()

    msg = f"{title}\n\n"

    if not top_rows:
        msg += "目前還沒有排行榜資料。\n"
    else:
        for i, r in enumerate(top_rows, start=1):
            display_name = name_with_badge(group_id, r["user_id"], r["name"])
            msg += f"{medal(i)} {display_name}｜{r['count_value'] or 0} {unit}\n"

    if my_row:
        msg += (
            "\n────────────\n"
            f"👤 你的名次：#{my_row['rank_no']}\n"
            f"📌 {count_label}：{my_row['count_value'] or 0} {unit}"
        )

    return msg


def get_my_sign_rank_message(group_id, user_id):
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT month_rank, month_count, streak_rank, streak_count
            FROM (
                SELECT
                    user_id,
                    COALESCE(sign_month_count, 0) AS month_count,
                    COALESCE(streak_count, 0) AS streak_count,
                    ROW_NUMBER() OVER (
                        ORDER BY COALESCE(sign_month_count, 0) DESC, name ASC
                    ) AS month_rank,
                    ROW_NUMBER() OVER (
                        ORDER BY COALESCE(streak_count, 0) DESC, name ASC
                    ) AS streak_rank
                FROM players
                WHERE group_id=%s
            ) ranked
            WHERE user_id=%s
        """, (group_id, user_id))
        row = c.fetchone()
    conn.close()

    if not row:
        return "目前沒有你的簽到排名資料。"

    return (
        "📊 你的簽到排名\n\n"
        f"🏅 本月排名：#{row['month_rank']}\n"
        f"📅 累積簽到：{row['month_count'] or 0} 天\n\n"
        f"🔥 連續排名：#{row['streak_rank']}\n"
        f"🔥 連續簽到：{row['streak_count'] or 0} 天"
    )


# ===== V2.1.0：任務中心 =====
def task_type_zh(task_type):
    mapping = {
        "daily": "每日",
        "weekly": "每週",
        "monthly": "每月",
        "quarterly": "每季",
    }
    return mapping.get(task_type, task_type)


def condition_zh(condition_type):
    mapping = {
        "sign": "簽到",
        "chat": "聊天",
        "fortune": "每日運勢",
        "wheel": "幸運輪盤",
        "title": "購買稱號",
        "vip": "購買VIP",
        "coin": "彩虹幣",
        "level": "等級",
    }
    return mapping.get(condition_type, condition_type)


def task_icon(row):
    if row["is_claimed"]:
        return "🎁"
    if (row["progress"] or 0) >= row["target_value"]:
        return "✅"
    return "☐"


def format_user_tasks(group_id, user_id, task_type):
    rows = get_user_tasks(group_id, user_id, task_type)
    title = {
        "daily": "📅 每日任務",
        "weekly": "📆 每週任務",
        "monthly": "🗓️ 每月任務",
        "quarterly": "🌸 每季任務",
    }.get(task_type, "📋 任務")

    if not rows:
        return (
            f"{title}\n\n"
            "目前沒有任務。\n\n"
            "群長可使用：\n"
            f"新增{task_type_zh(task_type)}任務 名稱 條件 數量 金幣 經驗"
        )

    done_count = sum(1 for r in rows if (r["progress"] or 0) >= r["target_value"])
    msg = f"{title} ({done_count}/{len(rows)})\n\n"

    for r in rows:
        progress = r["progress"] or 0
        target = r["target_value"]
        progress_text = f"{progress}/{target}"
        if progress > target:
            progress_text = f"{target}/{target}"

        msg += (
            f"{task_icon(r)} #{r['id']} {r['name']}\n"
            f"條件：{condition_zh(r['condition_type'])} {progress_text}\n"
            f"獎勵：🌈{r['reward_coins']}｜⭐{r['reward_exp']}"
        )

        if r["reward_title"]:
            msg += f"｜🏷️{r['reward_title']}"

        if (r["progress"] or 0) >= r["target_value"] and not r["is_claimed"]:
            msg += f"\n👉 領取任務 {r['id']}"

        if r["is_claimed"]:
            msg += "\n✅ 已領取"

        msg += "\n────────────\n"

    return msg


def format_my_tasks(group_id, user_id):
    daily = get_user_tasks(group_id, user_id, "daily")
    weekly = get_user_tasks(group_id, user_id, "weekly")
    monthly = get_user_tasks(group_id, user_id, "monthly")
    quarterly = get_user_tasks(group_id, user_id, "quarterly")

    def count_done(rows):
        return sum(1 for r in rows if (r["progress"] or 0) >= r["target_value"])

    return (
        "📋 我的任務總覽\n\n"
        f"📅 每日任務：{count_done(daily)}/{len(daily)}\n"
        f"📆 每週任務：{count_done(weekly)}/{len(weekly)}\n"
        f"🗓️ 每月任務：{count_done(monthly)}/{len(monthly)}\n"
        f"🌸 每季任務：{count_done(quarterly)}/{len(quarterly)}\n\n"
        "輸入：每日任務 / 每週任務 / 每月任務 / 每季任務\n"
        "完成後輸入：領取任務 任務ID"
    )


def task_management_message(group_id):
    rows = list_all_tasks(group_id)

    if not rows:
        return (
            "👑 任務管理\n\n"
            "目前尚未建立任務。\n\n"
            "新增格式：\n"
            "新增每日任務 名稱 條件 數量 金幣 經驗\n"
            "新增每週任務 名稱 條件 數量 金幣 經驗\n"
            "新增每月任務 名稱 條件 數量 金幣 經驗\n"
            "新增每季任務 名稱 條件 數量 金幣 經驗\n\n"
            "條件可用：sign / chat / fortune / wheel / title / vip"
        )

    msg = "👑 任務管理\n\n"
    for r in rows:
        status = "啟用" if r["is_active"] else "停用"
        source = "📘 官方" if r.get("is_official") else "🛠️ 自訂"
        hidden = "｜🕵️ 隱藏" if r.get("is_hidden") else ""
        msg += (
            f"#{r['id']} [{task_type_zh(r['task_type'])}] {r['name']}\n"
            f"類型：{source}{hidden}\n"
            f"條件：{r['condition_type']} {r['target_value']}\n"
            f"獎勵：🌈{r['reward_coins']}｜⭐{r['reward_exp']}\n"
            f"狀態：{status}\n"
            "────────────\n"
        )

    msg += (
        "\n刪除：刪除任務 任務名稱"
        "\n停用：停用任務 任務名稱"
        "\n啟用：啟用任務 任務名稱"
        "\n📘 官方任務不可刪除，但可以停用。"
    )
    return msg


def parse_add_task_command(text, task_type):
    parts = text.split()
    if len(parts) < 6:
        return None, (
            f"格式：新增{task_type_zh(task_type)}任務 名稱 條件 數量 金幣 經驗\n"
            f"例如：新增{task_type_zh(task_type)}任務 聊天30句 chat 30 200 100\n\n"
            "條件可用：sign / chat / fortune / wheel / title / vip"
        )

    name = parts[1]
    condition_type = parts[2]

    try:
        target_value = int(parts[3])
        reward_coins = int(parts[4])
        reward_exp = int(parts[5])
    except ValueError:
        return None, "❌ 數量、金幣、經驗都必須是數字。"

    reward_title = parts[6] if len(parts) >= 7 else ""

    return {
        "task_type": task_type,
        "name": name,
        "condition_type": condition_type,
        "target_value": target_value,
        "reward_coins": reward_coins,
        "reward_exp": reward_exp,
        "reward_title": reward_title,
    }, None


_scheduler_stop_event = threading.Event()
_scheduler_thread = None


def list_scheduler_group_ids():
    """取得曾註冊過成員的群組，供背景排程逐群檢查。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT DISTINCT group_id FROM players WHERE group_id IS NOT NULL AND group_id <> 'PRIVATE'")
            return [row["group_id"] for row in (c.fetchall() or []) if row.get("group_id")]
    finally:
        conn.close()


def scheduler_background_loop():
    # 每 30 秒檢查一次，開始/結束通知不再依賴群友剛好發言。
    while not _scheduler_stop_event.wait(30):
        try:
            for gid in list_scheduler_group_ids():
                try:
                    run_scheduler_tick(gid)
                except Exception:
                    traceback.print_exc()
        except Exception:
            traceback.print_exc()


@app.on_event("startup")
def start_background_scheduler():
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _scheduler_stop_event.clear()
    _scheduler_thread = threading.Thread(target=scheduler_background_loop, name="rainbow-scheduler", daemon=True)
    _scheduler_thread.start()


@app.on_event("shutdown")
def stop_background_scheduler():
    _scheduler_stop_event.set()


@app.get("/health")
def health_check():
    return {"ok": True, "service": "Rainbow Life", "version": APP_VERSION, "build": BUILD_ID}


@app.get("/")
def home():
    return {"status": "ok", "version": APP_VERSION, "build": BUILD_ID}


@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing LINE signature")
    body = await request.body()
    body_text = body.decode("utf-8")
    try:
        handler.handle(body_text, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid LINE signature")
    return "OK"


def get_line_group_name(group_id):
    if not group_id or group_id == "PRIVATE":
        return ""
    try:
        summary = line_bot_api.get_group_summary(group_id)
        name = (getattr(summary, "group_name", "") or "").strip()
        return name or "未命名群組"
    except Exception:
        return "未命名群組"


PRIVATE_ADMIN_COMMANDS = {
    "後台", "網頁後台", "/admin", "admin", "管理中心", "群組後台", "群組列表", "我的群組", "目前群組",
    "群組總覽", "成員管理", "成員列表", "管理員管理", "管理員名單", "VIP管理", "VIP 管理",
    "商店管理", "商城管理", "商品列表", "簽到設定", "補簽設定", "群組設定", "系統設定",
    "VIP會員", "VIP名單", "今日消費", "昨日消費", "總消費", "金庫", "金庫紀錄",
    "消費管理", "金庫管理", "搜尋成員說明", "消費查詢說明", "設定VIP說明",
    "延長VIP說明", "永久VIP說明", "移除VIP說明", "VIP概況", "新增商品說明",
    "修改商品說明", "修改價格說明", "上架商品說明", "下架商品說明", "查看簽到設定",
    "商品管理列表", "成員頁 1", "設定中心", "VIP設定", "VIP價格7說明", "VIP價格30說明", "VIP價格永久說明", "VIP倍率說明", "開啟VIP商店", "關閉VIP商店", "稱號設定", "抽獎設定", "活動設定", "公告管理", "群組資料設定",
    "設定公告說明", "設定公告時間說明", "查看公告", "刪除公告", "立即推播公告", "推播公告",
    "開啟公告推播", "關閉公告推播",
    "私訊停止狂歡", "私訊開始寶箱雨", "私訊停止寶箱雨", "私訊開始輪盤", "私訊停止輪盤",
    "V4管理中心", "簽到設定V4", "稱號管理V4", "抽獎設定V4", "提醒中心", "數據分析", "操作日誌",
    "簽到彩虹幣說明", "簽到EXP說明", "補簽價格說明", "連續獎勵說明"
}


def is_private_admin_command(text):
    return (
        text in PRIVATE_ADMIN_COMMANDS
        or text.startswith("切換群組")
        or text.startswith("消費紀錄")
        or text.startswith("搜尋成員")
        or text.startswith("查詢成員")
        or text.startswith("新增管理員 ")
        or text.startswith("移除管理員 ")
        or text.startswith("新增商品")
        or text.startswith("修改商品")
        or text.startswith("修改商品價格")
        or text.startswith("上架商品")
        or text.startswith("下架商品")
        or text.startswith("管理商品")
        or text.startswith("成員頁")
        or text.startswith("查看成員ID ")
        or text.startswith("VIP操作ID ")
        or text.startswith("設定VIPID ")
        or text.startswith("延長VIPID ")
        or text.startswith("移除VIPID ")
        or text.startswith("修改商品價格說明 ")
        or text.startswith("修改商品說明 ")
        or text.startswith("設定VIP")
        or text.startswith("延長VIP")
        or text.startswith("移除VIP")
        or text.startswith("收回VIP")
        or text.startswith("設定公告 ")
        or text.startswith("設定群規 ")
        or text.startswith("修改公告 ")
        or text.startswith("設定公告時間 ")
        or text.startswith("私訊開始狂歡")
        or text.startswith("設定簽到彩虹幣 ")
        or text.startswith("設定簽到EXP ")
        or text.startswith("設定補簽價格 ")
        or text.startswith("設定提醒時間 ")
        or text.startswith("禁言 ")
        or text.startswith("解除禁言 ")
        or text.startswith("查看禁言 ")
        or text == "禁言名單"
        or text.startswith("機器人模式 ")
        or text == "機器人模式"
    )



def required_admin_permission(text):
    t = str(text or "").strip()

    # V6.6.5：一般前台功能不是管理設定，絕對不可套用管理員權限。
    # 管理員本人也應能正常使用每日轉盤／每日運勢，不可因缺少
    # wheel_manage 或 fortune_manage 而被誤擋成「功能未開放」。
    public_player_commands = {
        "功能", "選單", "menu", "首頁", "功能中心", "個人中心", "我的中心",
        "我的狀態", "我的資料", "狀態",
        "每日轉盤", "轉盤", "幸運輪盤", "抽獎",
        "每日運勢", "今日占卜", "占卜",
        "簽到", "每日簽到",
        "商店", "商店中心", "VIP商店", "道具商店", "稱號商店",
    }
    if t in public_player_commands:
        return ""

    if t.startswith("禁言 "):
        return "mute"
    if t.startswith("解除禁言 "):
        return "unmute"
    if t.startswith("查看禁言 "):
        return "view_mute"
    if t.startswith(("搜尋成員", "查詢成員", "查看成員", "成員列表", "成員管理")):
        return "view_member"
    if "公告" in t or t.startswith("設定群規"):
        return "announcement"
    if "VIP" in t:
        return "vip_manage"
    if "彩虹幣" in t or t.startswith(("加幣", "扣幣")):
        return "coins_manage"
    if "EXP" in t or "經驗" in t:
        return "exp_manage"
    if "商店" in t or "商城" in t or "商品" in t or "稱號" in t:
        return "shop_manage"
    if "活動" in t or "狂歡" in t or "寶箱雨" in t:
        return "event_manage"
    if "輪盤" in t or "轉盤" in t:
        return "wheel_manage"
    if "運勢" in t or "占卜" in t:
        return "fortune_manage"
    if "設定" in t or "金庫" in t or "日誌" in t or "數據" in t:
        return "system_manage"
    return ""


def deny_disabled_admin_permission(event, group_id, user_id, text):
    if is_owner(group_id, user_id) or not is_admin(group_id, user_id):
        return False
    permission_key = required_admin_permission(text)
    if permission_key and not has_admin_permission(group_id, user_id, permission_key):
        label = PERMISSION_LABELS.get(permission_key, permission_key)
        line_bot_api.reply_message(
            event.reply_token,
            operation_notice_flex("⛔ 功能未開放", f"群長尚未開放你的「{label}」權限。", False, False, "!功能"),
        )
        return True
    return False


def _parse_mute_duration(value):
    raw = str(value or "").strip().lower().replace(" ", "")
    if raw in ["永久", "永久禁言", "permanent", "forever"]:
        return "PERMANENT", "永久"
    m = re.fullmatch(r"(\d+)(分鐘|分|小時|時|天|日)", raw)
    if not m:
        raise ValueError("時間格式請使用：10分鐘、2小時、3天或永久")
    amount = max(1, int(m.group(1)))
    unit = m.group(2)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    if unit in ["分鐘", "分"]:
        end = now + datetime.timedelta(minutes=amount); label = f"{amount}分鐘"
    elif unit in ["小時", "時"]:
        end = now + datetime.timedelta(hours=amount); label = f"{amount}小時"
    else:
        end = now + datetime.timedelta(days=amount); label = f"{amount}天"
    return end.isoformat(), label


def _resolve_member(group_id, keyword):
    key = str(keyword or "").strip()
    # LINE 群組 @提及文字會帶有 @，查詢成員時自動移除。
    key = key.lstrip("@＠").strip()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT * FROM players WHERE group_id=%s AND (user_id=%s OR name=%s) ORDER BY CASE WHEN user_id=%s THEN 0 ELSE 1 END LIMIT 1""", (group_id, key, key, key))
            return c.fetchone()
    finally:
        conn.close()


def _split_mute_target_duration(payload):
    raw = str(payload or "").strip()
    if "|" in raw:
        parts = [x.strip() for x in raw.split("|", 1)]
        if len(parts) == 2 and all(parts):
            return parts[0], parts[1]
    m = re.fullmatch(r"(.+?)\s+(永久|永久禁言|\d+(?:分鐘|分|小時|時|天|日))", raw)
    if not m:
        raise ValueError("格式：!禁言 成員名稱 10分鐘")
    return m.group(1).strip(), m.group(2).strip()


def get_muted_members(group_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT user_id, name, COALESCE(mute_until,'') AS mute_until FROM players WHERE group_id=%s AND COALESCE(mute_until,'')<>'' ORDER BY name", (group_id,))
            rows = c.fetchall() or []
    finally:
        conn.close()
    result=[]
    for row in rows:
        st=get_mute_status(group_id,row['user_id'])
        if st:
            result.append((row.get('name') or row['user_id'], st['remaining']))
    return result


def set_member_mute(group_id, target_key, duration, admin_id):
    target = _resolve_member(group_id, target_key)
    if not target:
        return False, "❌ 找不到該成員，請輸入完整名稱或成員 ID。"
    until, label = _parse_mute_duration(duration)
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""UPDATE players SET mute_until=%s, mute_reason=%s, muted_by=%s WHERE group_id=%s AND user_id=%s""", (until, "", admin_id, group_id, target["user_id"]))
        conn.commit()
    finally:
        conn.close()
    return True, f"🔇 已禁言 {target['name']}\n⏳ 時間：{label}\n\n禁言期間無法使用所有功能，也不會獲得任何獎勵。"


def clear_member_mute(group_id, target_key):
    target = _resolve_member(group_id, target_key)
    if not target:
        return False, "❌ 找不到該成員。"
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE players SET mute_until='', mute_reason='', muted_by='' WHERE group_id=%s AND user_id=%s", (group_id, target["user_id"]))
        conn.commit()
    finally:
        conn.close()
    return True, f"🔊 已解除 {target['name']} 的禁言。"


def get_mute_status(group_id, user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT name, COALESCE(mute_until,'') AS mute_until, COALESCE(mute_reason,'') AS mute_reason FROM players WHERE group_id=%s AND user_id=%s", (group_id, user_id))
            row = c.fetchone()
    finally:
        conn.close()
    if not row or not row.get("mute_until"):
        return None
    value = str(row.get("mute_until") or "")
    if value.upper() == "PERMANENT":
        return {"permanent": True, "remaining": "永久"}
    try:
        end = datetime.datetime.fromisoformat(value)
        if end.tzinfo is None:
            end = end.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
        if end <= now:
            clear_member_mute(group_id, user_id)
            return None
        sec = int((end-now).total_seconds())
        days, sec = divmod(sec, 86400); hours, sec = divmod(sec, 3600); mins = max(1, sec//60)
        parts=[]
        if days: parts.append(f"{days}天")
        if hours: parts.append(f"{hours}小時")
        if mins and not days: parts.append(f"{mins}分鐘")
        return {"permanent": False, "remaining": "".join(parts), "until": end.strftime('%Y/%m/%d %H:%M')}
    except Exception:
        return None

def _reply_unified_shop(event, target_group_id, page=1):
    """統一商店唯一渲染入口；活動商品永遠排除。"""
    if not target_group_id or target_group_id == "PRIVATE":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請先在後台選擇要使用的群組。"))
        return
    settings = get_vip_settings(target_group_id)
    general_items = []
    for row in list_shop_items(active_only=True):
        item = dict(row)
        if str(item.get("category") or "").strip() == "活動":
            continue
        if str(item.get("category") or "").strip() == "VIP":
            if not settings.get("shop_enabled", True):
                continue
            item["price"] = effective_vip_price(target_group_id, item.get("item_type"), item.get("price"))
        general_items.append(item)
    line_bot_api.reply_message(
        event.reply_token,
        unified_shop_flex(general_items, list_titles(include_vip=True), page=max(1, int(page or 1))),
    )


# ===== V5.5.1 後台頁面等待輸入流程 =====
def ensure_pending_input_table():
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS pending_admin_inputs (
                    user_id TEXT PRIMARY KEY,
                    group_id TEXT,
                    action TEXT NOT NULL,
                    payload TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        conn.commit()
    finally:
        conn.close()


def set_pending_admin_input(user_id, group_id, action, payload=''):
    ensure_pending_input_table()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO pending_admin_inputs(user_id, group_id, action, payload, created_at)
                VALUES(%s,%s,%s,%s,CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    group_id=EXCLUDED.group_id,
                    action=EXCLUDED.action,
                    payload=EXCLUDED.payload,
                    created_at=CURRENT_TIMESTAMP
            """, (user_id, group_id, action, payload or ''))
        conn.commit()
    finally:
        conn.close()


def get_pending_admin_input(user_id):
    ensure_pending_input_table()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT group_id, action, payload FROM pending_admin_inputs WHERE user_id=%s", (user_id,))
            row = c.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def clear_pending_admin_input(user_id):
    ensure_pending_input_table()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM pending_admin_inputs WHERE user_id=%s", (user_id,))
        conn.commit()
    finally:
        conn.close()


def _join_input_fields(raw):
    lines = [x.strip() for x in str(raw or '').replace('\r', '').split('\n') if x.strip()]
    if len(lines) > 1:
        return '|'.join(lines)
    return str(raw or '').strip()


# ===== V6.2：全群機器人冷卻 =====
# 一般成員使用查詢功能後，全群一般成員冷卻 5 秒；
# 使用會異動資料的功能後，全群一般成員冷卻 10 秒。
# 群長與管理員不受限制，冷卻期間完全靜默。
_BOT_GLOBAL_COOLDOWN_UNTIL = {}
_BOT_GLOBAL_COOLDOWN_LOCK = threading.Lock()

_STATE_COMMAND_PREFIXES = (
    "簽到", "補簽", "購買", "兌換", "挑選稱號", "裝備稱號", "卸下稱號",
    "每日運勢", "今日占卜", "占卜", "幸運輪盤", "每日轉盤", "轉盤", "抽獎",
    "生日設定", "設定生日", "領取", "VIP禮包", "領VIP禮包",
)
_QUERY_COMMAND_PREFIXES = (
    "功能", "我的狀態", "狀態", "商店", "活動商店", "稱號商店", "VIP商店",
    "彩虹金庫", "金庫", "公告", "最新公告", "查看群規", "群規", "規則",
    "目前活動", "活動", "生日中心", "即將生日", "我的稱號", "我的徽章",
    "排行榜", "等級排行榜", "金幣排行榜", "累積簽到榜", "貼圖排行榜",
    "指令", "指令說明", "說明", "幫助", "查看成員資料",
)


def _bot_function_cooldown_seconds(text, had_prefix=False, is_postback=False):
    """回傳 5/10 秒；非 Bot 功能回傳 0，避免一般聊天被攔截。"""
    value = str(text or "").strip()
    if not value:
        return 0
    # 卡片按鍵一定是 Bot 操作；再依指令判斷是否會異動資料。
    for prefix in _STATE_COMMAND_PREFIXES:
        if value == prefix or value.startswith(prefix + " ") or value.startswith(prefix + "|"):
            return 10
    for prefix in _QUERY_COMMAND_PREFIXES:
        if value == prefix or value.startswith(prefix + " ") or value.startswith(prefix + "|"):
            return 5
    # 明確使用 !／！的文字，視為查詢功能；管理指令由管理員豁免。
    if had_prefix or is_postback:
        return 5
    return 0


def _global_bot_cooldown_blocked(group_id, user_id, seconds):
    if not group_id or group_id == "PRIVATE" or seconds <= 0:
        return False
    # 群長與管理員完全不受全域冷卻限制，也不會啟動冷卻。
    try:
        if is_admin(group_id, user_id) or is_owner(group_id, user_id):
            return False
    except Exception:
        pass
    now = time.monotonic()
    with _BOT_GLOBAL_COOLDOWN_LOCK:
        until = float(_BOT_GLOBAL_COOLDOWN_UNTIL.get(group_id, 0) or 0)
        if now < until:
            return True
        _BOT_GLOBAL_COOLDOWN_UNTIL[group_id] = now + int(seconds)
        return False


def pending_input_to_command(action, raw, payload=''):
    value = _join_input_fields(raw)
    if action == 'search_member': return f"搜尋成員 {value}"
    if action == 'member_consumption': return f"消費紀錄 {value}"
    if action == 'set_vip': return f"設定VIP {value}"
    if action == 'extend_vip': return f"延長VIP {value}"
    if action == 'permanent_vip': return f"設定VIP {value}|永久" if '|' not in value else f"設定VIP {value}"
    if action == 'remove_vip': return f"移除VIP {value}"
    if action == 'add_product': return f"新增商品 {value}"
    if action == 'edit_product': return f"修改商品 {value}"
    if action == 'edit_product_price':
        return f"修改商品價格 {payload}|{value}" if payload else f"修改商品價格 {value}"
    if action == 'enable_product': return f"上架商品 {value}"
    if action == 'disable_product': return f"下架商品 {value}"
    if action == 'announcement_content': return f"設定公告 {raw.strip()}"
    if action == 'announcement_time': return f"設定公告時間 {value}"
    if action == 'mute_member': return f"禁言 {value}"
    if action == 'unmute_member': return f"解除禁言 {value}"
    if action == 'add_admin': return f"新增管理員 {value}"
    if action == 'remove_admin': return f"移除管理員 {value}"
    return value

def record_group_activity(group_id, user_id, *, message_count=0, sticker_count=0):
    """記錄台灣時間 05:00 換日後的今日聊天／貼圖數。

    使用獨立日期欄位，避免昨天的累積數字被後台誤認為今日資料。
    """
    if not group_id or group_id == "PRIVATE" or not user_id:
        return
    stats_date = get_game_date()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                UPDATE players
                SET today_msg_count = CASE
                        WHEN COALESCE(activity_stats_date, '') = %s
                        THEN COALESCE(today_msg_count, 0) + %s
                        ELSE %s
                    END,
                    today_sticker_count = CASE
                        WHEN COALESCE(activity_stats_date, '') = %s
                        THEN COALESCE(today_sticker_count, 0) + %s
                        ELSE %s
                    END,
                    activity_stats_date = %s
                WHERE group_id=%s AND user_id=%s
            """, (
                stats_date, int(message_count or 0), int(message_count or 0),
                stats_date, int(sticker_count or 0), int(sticker_count or 0),
                stats_date, group_id, user_id,
            ))
            conn.commit()
    except Exception:
        conn.rollback()
        traceback.print_exc()
    finally:
        conn.close()


@handler.add(MessageEvent, message=StickerMessage)
def handle_sticker_message(event):
    """貼圖只記錄今日貼圖數，不產生額外回覆。"""
    try:
        if getattr(event.source, "type", "") != "group":
            return
        group_id = event.source.group_id
        user_id = event.source.user_id
        try:
            name = get_line_display_name(group_id, user_id) or "成員"
        except Exception:
            name = "成員"
        ensure_player(group_id, user_id, name)
        register_group(group_id, get_line_group_name(group_id))
        record_group_activity(group_id, user_id, sticker_count=1)
    except Exception:
        traceback.print_exc()


@handler.add(PostbackEvent)
def handle_postback(event):
    """V5.5.0：卡片按鈕改用 postback，不再把指令文字送到聊天室造成洗版。"""
    try:
        data = getattr(event.postback, "data", "") or ""
        params = parse_qs(data, keep_blank_values=True)
        command = (params.get("cmd") or [""])[0].strip()
        if not command:
            return
        synthetic_event = SimpleNamespace(
            reply_token=event.reply_token,
            source=event.source,
            message=SimpleNamespace(text=command),
            is_postback=True,
        )
        handle_message(synthetic_event)
    except Exception:
        traceback.print_exc()


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    clear_notification_member_name()
    raw_text = event.message.text.strip()
    text, had_command_prefix = normalize_command_text(raw_text)
    # V18.2：相容全形空白、連續空白與 LINE 貼上文字。
    text = re.sub(r"[\u3000\t\r\n]+", " ", str(text or "")).strip()
    text = re.sub(r" +", " ", text)
    # V18.3：測試模式指令別名統一，避免空白或口語寫法無法觸發。
    _game_test_aliases = {
        "測試模式開": "測試模式 開",
        "開啟測試模式": "測試模式 開",
        "遊戲測試模式開": "測試模式 開",
        "遊戲測試開": "測試模式 開",
        "測試模式關": "測試模式 關",
        "關閉測試模式": "測試模式 關",
        "遊戲測試模式關": "測試模式 關",
        "遊戲測試關": "測試模式 關",
    }
    text = _game_test_aliases.get(text.replace(" ", ""), text)
    user_id = event.source.user_id
    group_id = event.source.group_id if event.source.type == "group" else "PRIVATE"
    request_display_name = get_line_display_name(group_id, user_id)
    set_notification_member_name(request_display_name)
    today = get_game_date()
    is_postback_event = bool(getattr(event, "is_postback", False))

    # V18.4：遊戲／測試模式必須在聊天統計之前處理。
    # 舊版若 ensure_player 或 record_group_activity 發生資料庫錯誤，會讓指令完全無回覆。

    # V18.3：測試模式為高優先管理指令，避免被等待輸入、維護模式或舊路由吞掉。
    if group_id != "PRIVATE" and text in {"測試模式 開", "測試模式 關", "加入測試玩家", "測試模式狀態"}:
        try:
            ensure_game_center_tables()
            display_name = request_display_name or get_line_display_name(group_id, user_id) or "成員"
            try:
                ensure_player(group_id, user_id, display_name)
            except Exception as player_exc:
                print(f"[GAME PLAYER ENSURE SKIPPED] {player_exc}")
            seed_game_settings(group_id)
            if text == "測試模式狀態":
                from game_database import get_game_setting
                enabled = get_game_setting(group_id, "test_mode_enabled", "0") == "1"
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"🧪 單人測試模式：{'已開啟' if enabled else '已關閉'}")
                )
                return
            game_message = handle_game_center_command(text, group_id, user_id, display_name)
            if game_message is None:
                game_message = TextSendMessage(text="❌ 測試模式指令未被遊戲中心接收。")
            line_bot_api.reply_message(event.reply_token, game_message)
            return
        except Exception as game_exc:
            print(f"[V18.3 TEST COMMAND ERROR] group={group_id} user={user_id} text={text!r}: {game_exc}")
            traceback.print_exc()
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"❌ 測試模式啟動失敗：{type(game_exc).__name__}\n請將此訊息回傳。")
            )
            return


    # V18.3A-F：遊戲中心入口優先回覆，避免舊等待輸入狀態吞掉「遊戲」。
    if group_id != "PRIVATE" and text in {"遊戲", "遊戲中心", "小遊戲", "Rainbow遊戲中心"}:
        try:
            ensure_game_center_tables()
            display_name = request_display_name or get_line_display_name(group_id, user_id) or "成員"
            try:
                ensure_player(group_id, user_id, display_name)
            except Exception as player_exc:
                print(f"[GAME PLAYER ENSURE SKIPPED] {player_exc}")
            seed_game_settings(group_id)
            game_message = handle_game_center_command(text, group_id, user_id, display_name)
            if game_message is None:
                game_message = TextSendMessage(text="❌ 遊戲中心入口未正確載入。")
            line_bot_api.reply_message(event.reply_token, game_message)
            return
        except Exception as game_exc:
            print(f"[V18 GAME CENTER ERROR] group={group_id} user={user_id}: {game_exc}")
            traceback.print_exc()
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"❌ 遊戲中心載入失敗：{type(game_exc).__name__}")
            )
            return

    # 真實聊天統計：遊戲高優先指令處理完後才紀錄；失敗時不可中斷 Bot 回覆。
    if group_id != "PRIVATE" and not is_postback_event:
        try:
            name = request_display_name or get_line_display_name(group_id, user_id) or "成員"
            ensure_player(group_id, user_id, name)
            record_group_activity(group_id, user_id, message_count=1)
        except Exception as activity_exc:
            print(f"[ACTIVITY TRACKING SKIPPED] group={group_id} user={user_id}: {activity_exc}")

    # V6.2：一般成員觸發 Bot 功能時，套用全群 5/10 秒冷卻。
    # 冷卻期間所有一般成員的按鍵與指令完全不回應；一般聊天不受影響。
    cooldown_seconds = _bot_function_cooldown_seconds(text, had_command_prefix, is_postback_event)
    if _global_bot_cooldown_blocked(group_id, user_id, cooldown_seconds):
        return

    # V6.1.3：後台入口必須優先於「等待輸入」與維護模式判斷。
    # 避免使用者先前留有後台輸入流程時，將「後台／網頁後台／admin」誤當成設定內容吞掉；
    # 同時確保群長在維護模式仍能取得後台登入連結。
    admin_entry_commands = {"後台", "群組後台", "網頁後台", "/admin", "admin"}
    if group_id == "PRIVATE" and text in admin_entry_commands:
        ensure_control_center_tables()
        selected_group_id = get_selected_group(user_id)

        # 已選群組失效或使用者已被移除管理權時，不沿用舊選擇。
        if selected_group_id and not is_admin(selected_group_id, user_id):
            selected_group_id = None

        if not selected_group_id:
            groups = list_admin_groups(user_id)
            if not groups:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="❌ 你目前不是任何群組的群長或管理員，無法進入網頁後台。"),
                )
                return
            # 只有一個可管理群組時直接選取，減少多一步操作。
            if len(groups) == 1:
                only_group = groups[0]
                candidate_id = (
                    only_group.get("group_id") if isinstance(only_group, dict)
                    else getattr(only_group, "group_id", None)
                )
                if candidate_id:
                    set_selected_group(user_id, candidate_id)
                    selected_group_id = candidate_id
            if not selected_group_id:
                line_bot_api.reply_message(event.reply_token, admin_group_switch_flex(groups, ""))
                return

        selected_name = group_name(selected_group_id) or "目前群組"
        role_label = "👑 群長" if is_owner(selected_group_id, user_id) else "🛡️ 管理員"
        access_url = make_admin_access_url(user_id, selected_group_id)
        if not access_url:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=(
                        "❌ 無法產生網頁後台登入連結。\n\n"
                        "請確認 Render 服務已有公開網址，並設定 ADMIN_WEB_SECRET。"
                    )
                ),
            )
            return
        line_bot_api.reply_message(
            event.reply_token,
            web_admin_entry_flex(selected_name, access_url, role_label),
        )
        return

    # V5.5.1：點後台按鈕後，下一則私訊直接視為設定內容，不必輸入指令。
    if group_id == "PRIVATE" and not is_postback_event and not had_command_prefix:
        pending = get_pending_admin_input(user_id)
        if pending:
            if raw_text.strip() in ["取消", "取消設定", "返回"]:
                clear_pending_admin_input(user_id)
                line_bot_api.reply_message(event.reply_token, operation_notice_flex("已取消", "本次設定已取消。", True, False, "!後台"))
                return
            pending_group = str(pending.get("group_id") or "")
            selected_now = str(get_selected_group(user_id) or "")
            if pending_group and selected_now and pending_group != selected_now:
                clear_pending_admin_input(user_id)
                line_bot_api.reply_message(event.reply_token, operation_notice_flex("群組已切換", "偵測到你已切換管理群組，請重新點選要設定的功能。", False, False, "!後台"))
                return
            text = pending_input_to_command(pending.get("action"), raw_text, pending.get("payload") or "")
            had_command_prefix = True
            clear_pending_admin_input(user_id)

    # V2.8.3：群組資訊自動登記；管理操作集中在 Bot 私訊。
    ensure_control_center_tables()

    # V5.4.6：維護模式必須最先攔截。只有群長本人可以繼續操作；其他人完全不回應。
    active_group_id = group_id if group_id != "PRIVATE" else get_selected_group(user_id)
    if active_group_id and get_system_mode(active_group_id) == "maintenance" and not is_owner(active_group_id, user_id):
        return

    if group_id != "PRIVATE":
        register_group(group_id, get_line_group_name(group_id))

        # V5.4.6：群長專用模式切換。
        if text == "機器人模式":
            if not is_owner(group_id, user_id):
                return
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🤖 機器人模式\n\n目前：{system_mode_label(group_id)}\n\n操作：\n機器人 正常\n機器人 靜音\n機器人 維護")); return
        if text.startswith("機器人模式 "):
            if not is_owner(group_id, user_id):
                return
            mode = parse_system_mode(text[len("機器人模式 "):])
            if not mode:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請輸入：機器人 正常／靜音／維護")); return
            set_system_mode(group_id, mode)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已切換為 {system_mode_label(group_id)}")); return

        # V5.4.1：群組內可直接使用禁言指令，支援空格或 | 分隔。
        if text.startswith("禁言 "):
            if not is_admin(group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⛔ 只有群長或具備禁言權限的管理員可以使用。")); return
            if deny_disabled_admin_permission(event, group_id, user_id, text): return
            try:
                target_key, duration = _split_mute_target_duration(text[len("禁言 "):])
                target = _resolve_member(group_id, target_key)
                if target and (is_owner(group_id, target['user_id']) or is_admin(group_id, target['user_id'])) and not is_owner(group_id, user_id):
                    raise ValueError("管理員不能禁言群長或其他管理員")
                ok, msg = set_member_mute(group_id, target_key, duration, user_id)
            except Exception as exc:
                msg = f"❌ {exc}"
            line_bot_api.reply_message(event.reply_token, operation_notice_flex("🔇 禁言設定" if ok else "⚠️ 禁言失敗", msg, ok, False, "!功能")); return

        if text.startswith("解除禁言 "):
            if not is_admin(group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⛔ 只有群長或具備解除禁言權限的管理員可以使用。")); return
            if deny_disabled_admin_permission(event, group_id, user_id, text): return
            ok, msg = clear_member_mute(group_id, text[len("解除禁言 "):].strip())
            line_bot_api.reply_message(event.reply_token, operation_notice_flex("🔊 已解除禁言" if ok else "⚠️ 操作失敗", msg, ok, False, "!功能")); return

        if text.startswith("查看禁言 "):
            if not is_admin(group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⛔ 只有群長或管理員可以查看。")); return
            if deny_disabled_admin_permission(event, group_id, user_id, text): return
            target = _resolve_member(group_id, text[len("查看禁言 "):].strip())
            st = get_mute_status(group_id, target['user_id']) if target else None
            msg = (f"👤 {target['name']}\n🔇 目前禁言中\n⏳ 剩餘：{st['remaining']}" if st else ("✅ 該成員目前沒有被禁言。" if target else "❌ 找不到該成員。"))
            line_bot_api.reply_message(event.reply_token, operation_notice_flex("📋 禁言狀態", msg, bool(st), False, "!功能")); return

        if text == "禁言名單":
            if not is_admin(group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⛔ 只有群長或管理員可以查看。")); return
            rows = get_muted_members(group_id)
            body = "目前沒有被禁言的成員。" if not rows else "\n\n".join(f"{i}. {name}\n⏳ {remaining}" for i,(name,remaining) in enumerate(rows,1))
            line_bot_api.reply_message(event.reply_token, operation_notice_flex("🔇 禁言名單", body, True, False, "!功能")); return

        if not text.startswith(("新增管理員 ", "移除管理員 ", "管理員權限 ")) and deny_disabled_admin_permission(event, group_id, user_id, text):
            return

        # V5.3.7：管理員異動可直接在群組使用，並支援 LINE @提及顯示名稱。
        if text in ["管理員管理", "管理員名單"]:
            if not is_admin(group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⛔ 只有群長或管理員可以查看管理員名單。")); return
            rows = list_admins(group_id)
            lines = ["🛡️ 管理員名單", ""]
            for i, row in enumerate(rows, 1):
                role = "群長" if str(row.get("role") or "").lower() == "owner" else "管理員"
                lines.append(f"{i}. {row.get('name') or row.get('user_id')}｜{role}")
            if is_owner(group_id, user_id):
                lines += ["", "群長可使用：", "!新增管理員 @成員", "!移除管理員 @成員"]
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="\n".join(lines))); return

        if text.startswith("新增管理員 "):
            if not is_owner(group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⛔ 只有群長可以新增管理員。")); return
            target_key = text[len("新增管理員 "):].strip()
            target = _resolve_member(group_id, target_key)
            if not target:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到該成員。請先確認對方曾在群組發言，再使用：!新增管理員 @成員")); return
            try:
                add_admin(group_id, target["user_id"])
                msg = (
                    "🛡️ 管理員設定成功\n\n"
                    f"👤 成員：{target['name']}\n"
                    "🎖️ 身分：一般管理員\n"
                    "✅ 權限已立即生效\n\n"
                    "管理員可使用日常後台功能，但不能新增或移除其他管理員。"
                )
            except Exception as exc:
                msg = f"❌ {exc}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg)); return

        if text.startswith("管理員權限 "):
            if not is_owner(group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⛔ 只有群長可以查看或修改管理員權限。")); return
            raw = text[len("管理員權限 "):].strip()
            parts = [x.strip() for x in raw.split("|")]
            target = _resolve_member(group_id, parts[0]) if parts else None
            if not target:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到該成員。請使用：!管理員權限 @成員")); return
            if len(parts) == 1:
                rows = list_admin_permissions(group_id, target['user_id'])
                lines = [f"🛡️ {target['name']} 權限", ""] + [f"{'✅' if enabled else '❌'} {label}" for _, label, enabled in rows]
                lines += ["", "設定格式：", "!管理員權限 @成員|權限名稱|開", "!管理員權限 @成員|權限名稱|關"]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="\n".join(lines))); return
            if len(parts) != 3:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：!管理員權限 @成員|權限名稱|開/關")); return
            permission_key = next((k for k,v in PERMISSION_LABELS.items() if parts[1] in (k,v)), None)
            if not permission_key or parts[2] not in ("開","關","on","off","ON","OFF"):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 權限名稱或開關格式錯誤。")); return
            enabled = parts[2] in ("開","on","ON")
            try:
                set_admin_permission(group_id, target['user_id'], permission_key, enabled)
                add_admin_log(group_id, user_id, "設定管理員權限", target['user_id'], f"{PERMISSION_LABELS[permission_key]}={'開' if enabled else '關'}")
                line_bot_api.reply_message(event.reply_token, operation_notice_flex("🛡️ 權限設定成功", f"👤 {target['name']}\n{PERMISSION_LABELS[permission_key]}：{'✅ 已開啟' if enabled else '❌ 已關閉'}", True, False, "!功能")); return
            except Exception as e:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ {e}")); return

        if text.startswith("移除管理員 "):
            if not is_owner(group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⛔ 只有群長可以移除管理員。")); return
            target = _resolve_member(group_id, text[len("移除管理員 "):].strip())
            if not target:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到該成員。請先確認對方曾在群組發言。")); return
            try:
                removed = remove_admin(group_id, target["user_id"])
                msg = (f"🛡️ 管理員已移除\n\n👤 成員：{target['name']}\n❌ 管理員權限已立即取消"
                       if removed else "ℹ️ 該成員目前不是管理員。")
            except Exception as exc:
                msg = f"❌ {exc}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg)); return

    elif group_id == "PRIVATE" and had_command_prefix and (text == "商店" or text.startswith("商店 ")):
        selected_group_id = get_selected_group(user_id)
        page = 1
        if text.startswith("商店 "):
            try:
                page = max(1, int(text.split(maxsplit=1)[1]))
            except Exception:
                page = 1
        _reply_unified_shop(event, selected_group_id, page)
        return

    elif group_id == "PRIVATE" and had_command_prefix and text == "活動商店":
        selected_group_id = get_selected_group(user_id)
        if not selected_group_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請先輸入 !後台 並選擇群組。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_event_shop(selected_group_id, user_id)))
        return

    elif is_private_admin_command(text):
        ensure_commerce_tables()
        selected_group_id = get_selected_group(user_id)
        selected_name = group_name(selected_group_id) if selected_group_id else "尚未選擇群組"

        # V5.4.6：後台群長專用模式切換。
        if text == "機器人模式":
            if not selected_group_id or not is_owner(selected_group_id, user_id):
                return
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🤖 機器人模式\n\n群組：{selected_name}\n目前：{system_mode_label(selected_group_id)}\n\n操作：\n機器人 正常\n機器人 靜音\n機器人 維護")); return
        if text.startswith("機器人模式 "):
            if not selected_group_id or not is_owner(selected_group_id, user_id):
                return
            mode = parse_system_mode(text[len("機器人模式 "):])
            if not mode:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請輸入：機器人 正常／靜音／維護")); return
            set_system_mode(selected_group_id, mode)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ {selected_name} 已切換為 {system_mode_label(selected_group_id)}")); return

        if selected_group_id and not text.startswith(("新增管理員 ", "移除管理員 ", "管理員權限 ")) and deny_disabled_admin_permission(event, selected_group_id, user_id, text):
            return

        if text.startswith("禁言 "):
            if not selected_group_id or not is_admin(selected_group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請先在後台選擇你管理的群組。")); return
            try:
                target_key, duration = _split_mute_target_duration(text[len("禁言 "):])
                ok, msg = set_member_mute(selected_group_id, target_key, duration, user_id)
            except Exception as exc: msg = f"❌ {exc}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg)); return
        if text.startswith("解除禁言 "):
            if not selected_group_id or not is_admin(selected_group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請先選擇你管理的群組。")); return
            ok, msg = clear_member_mute(selected_group_id, text[len("解除禁言 "):].strip())
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg)); return
        if text.startswith("查看禁言 "):
            target = _resolve_member(selected_group_id, text[len("查看禁言 "):].strip()) if selected_group_id else None
            st = get_mute_status(selected_group_id, target["user_id"]) if target else None
            msg = (f"🔇 {target['name']} 目前禁言中\n⏳ 剩餘：{st['remaining']}" if st else "✅ 該成員目前沒有被禁言。")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg)); return

        # V3.1：私訊群長管理中心按鈕化。舊文字指令仍完整保留。
        if text in ["後台", "群組後台", "網頁後台", "/admin", "admin"]:
            if not selected_group_id:
                groups = list_admin_groups(user_id)
                if groups:
                    line_bot_api.reply_message(event.reply_token, admin_group_switch_flex(groups, ""))
                else:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你目前不是任何群組的群長或管理員。"))
            else:
                role = get_admin_role(selected_group_id, user_id)
                role_label = "👑 群長" if role == "owner" else "🛡️ 管理員"
                access_url = make_admin_access_url(user_id, selected_group_id)
                if not access_url:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 尚未設定後台公開網址。請在 Render 設定 PUBLIC_BASE_URL，或確認 RENDER_EXTERNAL_URL 可用。"))
                else:
                    line_bot_api.reply_message(event.reply_token, web_admin_entry_flex(selected_name, access_url, role_label))
            return
        if text == "管理中心":
            if not selected_group_id:
                groups = list_admin_groups(user_id)
                if groups:
                    line_bot_api.reply_message(event.reply_token, admin_group_switch_flex(groups, ""))
                else:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你目前不是任何群組的群長或管理員。"))
            else:
                line_bot_api.reply_message(event.reply_token, admin_portal_flex(selected_name, make_admin_access_url(user_id, selected_group_id)))
            return
        if text in ["群組列表", "我的群組"]:
            groups = list_admin_groups(user_id)
            if groups:
                line_bot_api.reply_message(event.reply_token, admin_group_switch_flex(groups, selected_group_id))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=group_list_message(user_id)))
            return
        if text in ["管理員管理", "管理員名單"]:
            if not selected_group_id or not is_admin(selected_group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請先選擇你有權限的群組。")); return
            rows = list_admins(selected_group_id)
            lines = [f"👑 {selected_name} 管理員名單", ""]
            for i, row in enumerate(rows, 1):
                role = "群長" if str(row.get("role") or "").lower() == "owner" else "管理員"
                lines.append(f"{i}. {row.get('name') or row.get('user_id')}｜{role}")
            if is_owner(selected_group_id, user_id):
                lines += ["", "群長可使用：", "!新增管理員 成員名稱", "!移除管理員 成員名稱"]
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="\n".join(lines))); return
        if text.startswith("新增管理員 "):
            if not selected_group_id or not is_owner(selected_group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⛔ 只有群長可以新增管理員。")); return
            target = _resolve_member(selected_group_id, text[len("新增管理員 "):].strip())
            if not target:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到該成員，請輸入完整名稱或 LINE ID。")); return
            try:
                add_admin(selected_group_id, target["user_id"])
                msg = f"✅ 已將 {target['name']} 設為管理員。\n\n管理員可使用日常後台功能，但不能新增或移除管理員，也不能變更群長權限。"
            except Exception as exc:
                msg = f"❌ {exc}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg)); return
        if text.startswith("移除管理員 "):
            if not selected_group_id or not is_owner(selected_group_id, user_id):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⛔ 只有群長可以移除管理員。")); return
            target = _resolve_member(selected_group_id, text[len("移除管理員 "):].strip())
            if not target:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到該成員，請輸入完整名稱或 LINE ID。")); return
            try:
                removed = remove_admin(selected_group_id, target["user_id"])
                msg = f"✅ 已移除 {target['name']} 的管理員權限。" if removed else "ℹ️ 該成員目前不是管理員。"
            except Exception as exc:
                msg = f"❌ {exc}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg)); return

        if text == "成員管理":
            members, page, total_pages, total = get_member_page(selected_group_id, 1, 15)
            line_bot_api.reply_message(event.reply_token, admin_member_list_flex(selected_name, members, page, total_pages, total))
            return
        if text in ["成員列表", "成員頁"] or text.startswith("成員頁 "):
            try:
                page_no = int(text.split()[-1]) if text.startswith("成員頁 ") else 1
            except Exception:
                page_no = 1
            members, page, total_pages, total = get_member_page(selected_group_id, page_no, 15)
            line_bot_api.reply_message(event.reply_token, admin_member_list_flex(selected_name, members, page, total_pages, total))
            return
        if text.startswith("查看成員ID "):
            payload = text[len("查看成員ID "):].strip()
            if "|" in payload:
                member_id, page_text = payload.rsplit("|", 1)
                try:
                    return_page = max(1, int(page_text))
                except Exception:
                    return_page = 1
            else:
                member_id, return_page = payload, 1
            member = get_member_by_id(selected_group_id, member_id.strip())
            if not member:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到該成員資料。"))
            else:
                line_bot_api.reply_message(event.reply_token, admin_member_detail_flex(selected_name, member, return_page))
            return
        if text.startswith("VIP操作ID "):
            payload = text[len("VIP操作ID "):].strip()
            if "|" in payload:
                member_id, page_text = payload.rsplit("|", 1)
                try:
                    return_page = max(1, int(page_text))
                except Exception:
                    return_page = 1
            else:
                member_id, return_page = payload, 1
            member = get_member_by_id(selected_group_id, member_id.strip())
            if not member:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到該成員資料。"))
            else:
                line_bot_api.reply_message(event.reply_token, admin_member_vip_actions_flex(selected_name, member, return_page))
            return
        if text.startswith("設定VIPID ") or text.startswith("延長VIPID ") or text.startswith("移除VIPID "):
            from vip import grant_vip, extend_vip, cancel_vip
            try:
                if text.startswith("設定VIPID "):
                    payload = text[len("設定VIPID "):].strip()
                    uid, plan = payload.split("|", 1)
                    ok, msg = grant_vip(selected_group_id, uid.strip(), plan.strip(), user_id)
                elif text.startswith("延長VIPID "):
                    payload = text[len("延長VIPID "):].strip()
                    uid, plan = payload.split("|", 1)
                    ok, msg = extend_vip(selected_group_id, uid.strip(), plan.strip(), user_id)
                else:
                    uid = text[len("移除VIPID "):].strip()
                    ok, msg = cancel_vip(selected_group_id, uid, user_id)
                member = get_member_by_id(selected_group_id, uid.strip())
                suffix = f"\n👤 成員：{member['name']}" if member else ""
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg + suffix))
            except Exception as exc:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ VIP 操作失敗：{exc}"))
            return
        if text in ["VIP管理", "VIP 管理"]:
            line_bot_api.reply_message(event.reply_token, admin_vip_menu_flex(selected_name))
            return
        if had_command_prefix and text == "商店管理":
            line_bot_api.reply_message(event.reply_token, admin_shop_menu_flex(selected_name))
            return
        if had_command_prefix and text == "商品管理列表":
            line_bot_api.reply_message(event.reply_token, admin_shop_items_flex(selected_name, list_shop_items()))
            return
        if had_command_prefix and text.startswith("管理商品 "):
            from shop import get_shop_item
            item_name = text[len("管理商品 "):].strip()
            item, error = get_shop_item(item_name)
            if error:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error))
            else:
                line_bot_api.reply_message(event.reply_token, admin_shop_item_actions_flex(selected_name, item))
            return
        if had_command_prefix and text.startswith("修改商品價格說明 "):
            item_name = text[len("修改商品價格說明 "):].strip()
            set_pending_admin_input(user_id, selected_group_id, "edit_product_price", item_name)
            line_bot_api.reply_message(event.reply_token, admin_input_help_flex("💲 修改商品價格", f"商品：{item_name}\n\n請直接輸入新價格。\n輸入「取消」可結束本次設定。", "例如：600", "!商店管理"))
            return
            # 舊流程保留於下方但不再執行
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"💲 修改商品價格\n\n請輸入：\n!修改商品價格 {item_name}|新價格\n\n例如：\n!修改商品價格 {item_name}|800"))
            return
        if had_command_prefix and text.startswith("修改商品說明 "):
            item_name = text[len("修改商品說明 "):].strip()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✏️ 修改商品資料\n\n請輸入：\n!修改商品 {item_name}|價格|分類|說明\n\n例如：\n!修改商品 {item_name}|800|活動|更新後的商品說明"))
            return
        if text == "消費管理":
            line_bot_api.reply_message(event.reply_token, admin_consumption_menu_flex(selected_name))
            return
        if text == "金庫管理":
            line_bot_api.reply_message(event.reply_token, admin_vault_menu_flex(selected_name))
            return
        if text == "V4管理中心":
            line_bot_api.reply_message(event.reply_token, admin_v4_center_flex(selected_name))
            return
        if text == "簽到設定V4":
            line_bot_api.reply_message(event.reply_token, admin_sign_settings_v4_flex(selected_name, get_sign_settings(selected_group_id)))
            return
        if text == "簽到彩虹幣說明":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🌈 修改每日簽到彩虹幣\n\n請輸入：!設定簽到彩虹幣 300"))
            return
        if text == "簽到EXP說明":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⭐ 修改每日簽到 EXP\n\n請輸入：!設定簽到EXP 150"))
            return
        if text == "補簽價格說明":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 修改補簽價格\n\n請輸入：!設定補簽價格 3|300\n代表 3 天前補簽需要 300 彩虹幣。"))
            return
        if text == "連續獎勵說明":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🎁 連續簽到獎勵目前沿用里程碑制：3、7、14、30、60、100 天，120 天後每 30 天持續獎勵。"))
            return
        if text.startswith("設定簽到彩虹幣 "):
            try:
                value=int(text.replace("設定簽到彩虹幣 ","",1).strip())
                if value<0: raise ValueError
                set_setting(selected_group_id,"sign_coin",value,user_id)
                msg=f"✅ 每日簽到彩虹幣已設定為 🌈{value:,}。"
            except Exception: msg="❌ 請輸入 0 以上整數，例如：!設定簽到彩虹幣 300"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return
        if text.startswith("設定簽到EXP "):
            try:
                value=int(text.replace("設定簽到EXP ","",1).strip())
                if value<0: raise ValueError
                set_setting(selected_group_id,"sign_exp",value,user_id)
                msg=f"✅ 每日簽到 EXP 已設定為 ⭐{value:,}。"
            except Exception: msg="❌ 請輸入 0 以上整數，例如：!設定簽到EXP 150"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return
        if text.startswith("設定補簽價格 "):
            payload=text.replace("設定補簽價格 ","",1).strip()
            try:
                d,p=payload.split("|",1); ok,msg=set_makeup_price(selected_group_id,d,p,user_id)
            except Exception: ok,msg=False,"❌ 格式：!設定補簽價格 天數|價格，例如：!設定補簽價格 3|300"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return
        if text == "數據分析":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=analytics_message(selected_group_id,selected_name)))
            return
        if text == "操作日誌":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=audit_message(selected_group_id)))
            return
        if text == "提醒中心":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reminder_summary(selected_group_id)+"\n\n設定：!設定提醒時間 20:00\n開關：!開啟簽到提醒／!關閉簽到提醒"))
            return
        if text.startswith("設定提醒時間 "):
            value=text.replace("設定提醒時間 ","",1).strip()
            if not valid_hhmm(value): msg="❌ 時間格式錯誤，例如：!設定提醒時間 20:00"
            else:
                set_setting(selected_group_id,"sign_reminder_time",value,user_id); msg=f"✅ 簽到提醒時間已設定為 {value}。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return
        if text in ["開啟簽到提醒","關閉簽到提醒"]:
            enabled=text.startswith("開啟")
            set_setting(selected_group_id,"sign_reminder",enabled,user_id)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 簽到提醒已{'開啟' if enabled else '關閉'}。"))
            return
        if text == "稱號管理V4":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🎖 稱號管理\n\n可使用：\n!新增稱號 名稱\n!刪除稱號 名稱\n!稱號商店\n\n限定／VIP／活動稱號將沿用商品分類管理。"))
            return
        if text == "抽獎設定V4":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🎁 抽獎設定\n\n目前維持既有抽獎與輪盤規則；可從活動設定開啟輪盤大暴送。"))
            return
        if text in ["設定中心", "群組設定", "系統設定"]:
            line_bot_api.reply_message(event.reply_token, admin_v4_center_flex(selected_name))
            return
        if text == "VIP設定":
            vip_count, permanent_count = get_vip_setting_counts(selected_group_id)
            line_bot_api.reply_message(
                event.reply_token,
                admin_vip_settings_flex(selected_name, get_vip_settings(selected_group_id), vip_count, permanent_count),
            )
            return
        if text in ["VIP價格7說明", "VIP價格30說明", "VIP價格永久說明"]:
            plan = {"VIP價格7說明": "7天", "VIP價格30說明": "30天", "VIP價格永久說明": "永久"}[text]
            msg = (
                f"💲 修改 VIP {plan}價格\n\n"
                f"請輸入：\n!設定VIP價格 {plan}|新價格\n\n"
                f"例如：\n!設定VIP價格 {plan}|2500"
            )
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return
        if text.startswith("設定VIP價格 "):
            payload = text[len("設定VIP價格 "):].strip()
            if "|" not in payload:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="❌ 格式錯誤\n\n請輸入：\n!設定VIP價格 7天|2500"),
                )
                return
            plan, price = [part.strip() for part in payload.split("|", 1)]
            ok, msg = update_vip_price(selected_group_id, plan, price)
            if ok:
                vip_count, permanent_count = get_vip_setting_counts(selected_group_id)
                line_bot_api.reply_message(
                    event.reply_token,
                    [
                        TextSendMessage(text=msg),
                        admin_vip_settings_flex(
                            selected_name,
                            get_vip_settings(selected_group_id),
                            vip_count,
                            permanent_count,
                        ),
                    ],
                )
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return
        if text == "VIP倍率說明":
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=(
                        "✨ 設定 VIP EXP 倍率\n\n"
                        "請輸入：\n!設定VIP倍率 倍率\n\n"
                        "例如：\n!設定VIP倍率 2\n!設定VIP倍率 1.5\n\n"
                        "可設定 1～10 倍。"
                    )
                ),
            )
            return
        if text.startswith("設定VIP倍率 "):
            value = text[len("設定VIP倍率 "):].strip()
            ok, msg = set_vip_exp_multiplier(selected_group_id, value)
            if ok:
                vip_count, permanent_count = get_vip_setting_counts(selected_group_id)
                line_bot_api.reply_message(
                    event.reply_token,
                    [
                        TextSendMessage(text=msg),
                        admin_vip_settings_flex(
                            selected_name,
                            get_vip_settings(selected_group_id),
                            vip_count,
                            permanent_count,
                        ),
                    ],
                )
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return
        if text in ["開啟VIP商店", "關閉VIP商店"]:
            enabled = text == "開啟VIP商店"
            msg = set_vip_shop_enabled(selected_group_id, enabled)
            vip_count, permanent_count = get_vip_setting_counts(selected_group_id)
            line_bot_api.reply_message(
                event.reply_token,
                [
                    TextSendMessage(text=msg),
                    admin_vip_settings_flex(
                        selected_name,
                        get_vip_settings(selected_group_id),
                        vip_count,
                        permanent_count,
                    ),
                ],
            )
            return
        if text == "稱號設定":
            line_bot_api.reply_message(event.reply_token, admin_placeholder_settings_flex("🎖️ 稱號設定", selected_name, "目前可透過稱號商店與既有稱號管理指令操作。"))
            return
        if text == "抽獎設定":
            line_bot_api.reply_message(event.reply_token, admin_placeholder_settings_flex("🎁 抽獎設定", selected_name, "目前維持既有抽獎規則。"))
            return
        if text == "活動設定":
            line_bot_api.reply_message(
                event.reply_token,
                admin_activity_settings_flex(
                    selected_name,
                    activity_remaining_seconds(selected_group_id, "manual_carnival_until"),
                    activity_remaining_seconds(selected_group_id, "chest_rain_until"),
                    activity_remaining_seconds(selected_group_id, "wheel_boost_until"),
                ),
            )
            return
        if text.startswith("私訊開始狂歡"):
            parts = text.split()
            hours = 1
            if len(parts) >= 2:
                try:
                    hours = max(1, min(int(parts[-1].replace("小時", "")), 6))
                except ValueError:
                    hours = 1
            set_activity_until(selected_group_id, "manual_carnival_until", hours * 3600)
            set_event_value(selected_group_id, "manual_carnival_tracked_until", get_activity_until(selected_group_id, "manual_carnival_until"))
            set_event_value(selected_group_id, "manual_carnival_end_announced_until", "0")
            announce_group(selected_group_id, f"📢 👑 群長開啟全群狂歡！\n\n⏰ 持續時間：{hours} 小時\n✨ 聊天 EXP ×2\n🌈 寶箱掉落率跟著加成\n\n快來聊天衝等吧！")
            line_bot_api.reply_message(event.reply_token, admin_activity_settings_flex(selected_name, hours * 3600, activity_remaining_seconds(selected_group_id, "chest_rain_until"), activity_remaining_seconds(selected_group_id, "wheel_boost_until")))
            return
        if text == "私訊停止狂歡":
            clear_activity(selected_group_id, "manual_carnival_until")
            announce_group(selected_group_id, "📢 群長狂歡模式已結束。")
            line_bot_api.reply_message(event.reply_token, admin_activity_settings_flex(selected_name, 0, activity_remaining_seconds(selected_group_id, "chest_rain_until"), activity_remaining_seconds(selected_group_id, "wheel_boost_until")))
            return
        if text == "私訊開始寶箱雨":
            set_activity_until(selected_group_id, "chest_rain_until", 3600)
            announce_group(selected_group_id, "📢 🎁 群長開啟寶箱雨！\n\n⏰ 持續時間：1 小時\n🎁 聊天寶箱掉落率提升至 15%\n\n聊天室刷起來，寶箱掉起來！")
            line_bot_api.reply_message(event.reply_token, admin_activity_settings_flex(selected_name, activity_remaining_seconds(selected_group_id, "manual_carnival_until"), 3600, activity_remaining_seconds(selected_group_id, "wheel_boost_until")))
            return
        if text == "私訊停止寶箱雨":
            clear_activity(selected_group_id, "chest_rain_until")
            announce_group(selected_group_id, "📢 寶箱雨模式已結束。")
            line_bot_api.reply_message(event.reply_token, admin_activity_settings_flex(selected_name, activity_remaining_seconds(selected_group_id, "manual_carnival_until"), 0, activity_remaining_seconds(selected_group_id, "wheel_boost_until")))
            return
        if text == "私訊開始輪盤":
            set_activity_until(selected_group_id, "wheel_boost_until", 3600)
            announce_group(selected_group_id, "📢 🎰 群長開啟輪盤大暴送！\n\n⏰ 持續時間：1 小時\n🎰 幸運輪盤最低 250、最高 1000 彩虹幣\n\n還沒轉輪盤的快衝！")
            line_bot_api.reply_message(event.reply_token, admin_activity_settings_flex(selected_name, activity_remaining_seconds(selected_group_id, "manual_carnival_until"), activity_remaining_seconds(selected_group_id, "chest_rain_until"), 3600))
            return
        if text == "私訊停止輪盤":
            clear_activity(selected_group_id, "wheel_boost_until")
            announce_group(selected_group_id, "📢 輪盤大暴送已結束。")
            line_bot_api.reply_message(event.reply_token, admin_activity_settings_flex(selected_name, activity_remaining_seconds(selected_group_id, "manual_carnival_until"), activity_remaining_seconds(selected_group_id, "chest_rain_until"), 0))
            return
        if text == "公告管理":
            line_bot_api.reply_message(
                event.reply_token,
                admin_announcement_settings_flex(
                    selected_name,
                    is_announcement_enabled(selected_group_id),
                    get_announcement_time(selected_group_id),
                    get_announcement_content(selected_group_id),
                ),
            )
            return
        if text == "設定公告說明":
            set_pending_admin_input(user_id, selected_group_id, "announcement_content")
            line_bot_api.reply_message(event.reply_token, admin_input_help_flex("✏️ 設定公告內容", "請直接輸入公告內容。\n\n輸入「取消」可結束本次設定。", "例如：明天晚上八點舉辦群組活動！", "!公告管理"))
            return
        if text == "設定公告時間說明":
            set_pending_admin_input(user_id, selected_group_id, "announcement_time")
            line_bot_api.reply_message(event.reply_token, admin_input_help_flex("⏰ 設定公告時間", "請直接輸入時間（HH:MM）。\n\n輸入「取消」可結束本次設定。", "例如：20:00", "!公告管理"))
            return
        if text.startswith("設定公告 ") or text.startswith("修改公告 "):
            prefix = "設定公告 " if text.startswith("設定公告 ") else "修改公告 "
            content = text.replace(prefix, "", 1).strip()
            if not content:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 公告內容不能空白。"))
            else:
                set_announcement_content(selected_group_id, content)
                line_bot_api.reply_message(event.reply_token, admin_announcement_settings_flex(selected_name, is_announcement_enabled(selected_group_id), get_announcement_time(selected_group_id), content))
            return
        if text.startswith("設定公告時間 "):
            time_text = text.replace("設定公告時間 ", "", 1).strip()
            if not valid_hhmm(time_text):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 時間格式錯誤。請輸入例如：!設定公告時間 20:00"))
            else:
                set_announcement_time(selected_group_id, time_text)
                line_bot_api.reply_message(event.reply_token, admin_announcement_settings_flex(selected_name, is_announcement_enabled(selected_group_id), time_text, get_announcement_content(selected_group_id)))
            return
        if text == "查看公告":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=announcement_settings_message(selected_group_id)))
            return
        if text == "刪除公告":
            clear_announcement_content(selected_group_id)
            line_bot_api.reply_message(event.reply_token, admin_announcement_settings_flex(selected_name, is_announcement_enabled(selected_group_id), get_announcement_time(selected_group_id), ""))
            return
        if text == "開啟公告推播":
            set_announcement_enabled(selected_group_id, True)
            line_bot_api.reply_message(event.reply_token, admin_announcement_settings_flex(selected_name, True, get_announcement_time(selected_group_id), get_announcement_content(selected_group_id)))
            return
        if text == "關閉公告推播":
            set_announcement_enabled(selected_group_id, False)
            line_bot_api.reply_message(event.reply_token, admin_announcement_settings_flex(selected_name, False, get_announcement_time(selected_group_id), get_announcement_content(selected_group_id)))
            return
        if text in ["立即推播公告", "推播公告"]:
            ok, msg, announcement_text = push_announcement(selected_group_id)
            replies = [TextSendMessage(text=msg)]
            if not ok and announcement_text:
                replies.insert(0, TextSendMessage(text=announcement_text))
            line_bot_api.reply_message(event.reply_token, replies)
            return
        if text.startswith("設定群規 "):
            if not selected_group_id:
                line_bot_api.reply_message(event.reply_token, operation_notice_flex("尚未選擇群組", "請先輸入「群組列表」並選擇要管理的群組。", False, False, "!群組列表"))
                return
            if not is_admin(selected_group_id, user_id):
                line_bot_api.reply_message(event.reply_token, operation_notice_flex("權限不足", "你不是目前所選群組的群長或管理員，請先切換到自己有權限的群組。", False, False, "!群組列表"))
                return
            rules_text = text.replace("設定群規 ", "", 1).strip()
            if not rules_text:
                line_bot_api.reply_message(event.reply_token, operation_notice_flex("設定群規", "格式：!設定群規 群規內容", False, False, "!後台"))
                return
            set_event_value(selected_group_id, "group_rules", rules_text)
            line_bot_api.reply_message(event.reply_token, operation_notice_flex("群規已更新", f"已更新「{selected_name}」的群規。\n\n{rules_text}", True, True, "!後台"))
            return
        if text in ["群規", "查看群規"]:
            if not selected_group_id:
                line_bot_api.reply_message(event.reply_token, operation_notice_flex("尚未選擇群組", "請先輸入「群組列表」並選擇要查看的群組。", False, False, "!群組列表"))
                return
            rules = str(get_event_value(selected_group_id, "group_rules", "") or "").strip()
            if not rules:
                rules = "目前尚未設定群規。"
            line_bot_api.reply_message(event.reply_token, operation_notice_flex(f"📜 {selected_name} 群規", rules, True, True, "!後台"))
            return

        if text == "群組資料設定":
            line_bot_api.reply_message(event.reply_token, admin_placeholder_settings_flex("⚙️ 群組設定", selected_name, "每個群組的設定會獨立保存，不會影響其他群組。"))
            return
        if text in ["簽到設定", "補簽設定"]:
            line_bot_api.reply_message(event.reply_token, admin_sign_menu_flex(selected_name))
            return

        # V5.5.1：後台按鈕進入等待輸入，不必再輸入 ! 指令。
        pending_help = {
            "搜尋成員說明": ("search_member", "🔍 搜尋成員", "請直接輸入成員名稱。", "例如：佑佑"),
            "消費查詢說明": ("member_consumption", "📜 查詢成員消費", "請直接輸入成員名稱。", "例如：佑佑"),
            "設定VIP說明": ("set_vip", "➕ 設定 VIP", "請輸入成員名稱與天數，可分行輸入。", "例如：\n佑佑\n30天"),
            "延長VIP說明": ("extend_vip", "⏳ 延長 VIP", "請輸入成員名稱與延長天數，可分行輸入。", "例如：\n佑佑\n7天"),
            "永久VIP說明": ("permanent_vip", "♾️ 設定永久 VIP", "請直接輸入成員名稱。", "例如：佑佑"),
            "移除VIP說明": ("remove_vip", "❌ 移除 VIP", "請直接輸入成員名稱。", "例如：佑佑"),
            "新增商品說明": ("add_product", "➕ 新增商品", "請依序輸入商品名稱、價格、分類、說明，可分行輸入。", "例如：\n彩虹煙火\n500\n特效\n限定特效"),
            "修改商品說明": ("edit_product", "✏️ 修改商品", "請依序輸入商品名稱、價格、分類、說明，可分行輸入。", "例如：\n彩虹煙火\n600\n特效\n新版說明"),
            "修改價格說明": ("edit_product_price", "💲 修改商品價格", "請輸入商品名稱與新價格，可分行輸入。", "例如：\n彩虹煙火\n600"),
            "上架商品說明": ("enable_product", "👁️ 上架商品", "請直接輸入商品名稱。", "例如：彩虹煙火"),
            "下架商品說明": ("disable_product", "🚫 下架商品", "請直接輸入商品名稱。", "例如：彩虹煙火"),
        }
        if text in pending_help:
            action, title, guide, example = pending_help[text]
            set_pending_admin_input(user_id, selected_group_id, action)
            line_bot_api.reply_message(event.reply_token, admin_input_help_flex(title, guide + "\n\n輸入「取消」可結束本次設定。", example, "!後台"))
            return

        # 按鈕引導說明
        help_map = {
            "搜尋成員說明": "🔍 搜尋成員\n\n請輸入：!搜尋成員 成員名稱\n例如：!搜尋成員 佑佑",
            "消費查詢說明": "📜 查詢成員消費\n\n請輸入：!消費紀錄 成員名稱\n例如：!消費紀錄 佑佑",
            "設定VIP說明": "➕ 設定 VIP\n\n請輸入：!設定VIP 成員名稱|30天\n例如：!設定VIP 佑佑|30天",
            "延長VIP說明": "⏳ 延長 VIP\n\n請輸入：!延長VIP 成員名稱|7天",
            "永久VIP說明": "♾️ 設定永久 VIP\n\n請輸入：!設定VIP 成員名稱|永久",
            "移除VIP說明": "❌ 移除 VIP\n\n請輸入：!移除VIP 成員名稱",
            "新增商品說明": "➕ 新增商品\n\n格式：!新增商品 名稱|價格|分類|說明\n例如：!新增商品 彩虹煙火|500|特效|限定特效",
            "修改商品說明": "✏️ 修改商品\n\n格式：!修改商品 名稱|價格|分類|說明",
            "修改價格說明": "💲 修改價格\n\n格式：!修改商品價格 商品名稱|新價格",
            "上架商品說明": "👁️ 上架商品\n\n格式：!上架商品 商品名稱",
            "下架商品說明": "🚫 下架商品\n\n格式：!下架商品 商品名稱",
        }
        if text in help_map:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=help_map[text]))
            return
        if text == "VIP概況":
            private_reply = handle_private_control_command("VIP管理", user_id)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=private_reply))
            return
        if text == "查看簽到設定":
            private_reply = handle_private_control_command("簽到設定", user_id)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=private_reply))
            return

        private_reply = handle_private_control_command(text, user_id)
        if private_reply is not None:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=private_reply))
            return
    # 禁言中的成員：封鎖所有功能、聊天 EXP、任務、活動與獎勵。
    if group_id != "PRIVATE":
        mute_status = get_mute_status(group_id, user_id)
        if mute_status:
            if is_command_text(text):
                detail = f"🔇 你目前處於禁言狀態\n\n⏳ 剩餘時間：{mute_status['remaining']}\n\n禁言期間無法使用任何功能，也不會獲得 EXP、彩虹幣、任務進度或活動獎勵。"
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=detail))
            return

    # V6.1.10：私訊管理指令一律套用「目前選擇的管理群組」。
    # 過去私訊時 group_id 會維持 PRIVATE，導致 is_admin(PRIVATE, user_id) 永遠失敗，
    # 因此即使網頁後台已顯示群長，給予 VIP／彩虹幣／EXP 等指令仍會被誤判無權限。
    private_admin_prefixes = (
        "給予VIP", "延長VIP", "收回VIP", "查看VIP", "VIP紀錄",
        "給予稱號", "收回稱號", "稱號紀錄", "給予徽章", "收回徽章",
        "給予金幣", "給予彩虹幣", "給予經驗", "給予等級",
        "管理員管理", "管理員名單", "新增管理員", "移除管理員", "管理員權限",
        "禁言 ", "解除禁言 ", "查看禁言 ", "禁言名單",
        "機器人模式", "關閉機器人", "開啟機器人",
        "綁定 群長",
    )
    if group_id == "PRIVATE" and text.startswith(private_admin_prefixes):
        selected_group_id = get_selected_group(user_id)
        if selected_group_id:
            group_id = selected_group_id
            register_group(group_id, get_line_group_name(group_id))

    # 相容舊用法：「綁定 名字 群長」自動轉成「綁定 群長 名字」。
    bind_reverse = re.fullmatch(r"綁定\s+(.+?)\s+群長", text)
    if bind_reverse:
        text = f"綁定 群長 {bind_reverse.group(1).strip()}"

    # V5.5.2：舊首頁名稱統一轉入同一個單訊息入口，避免重複建立空白頁。
    if text in ["群組首頁", "首頁卡片"]:
        text = "功能"
    this_month = get_game_month()

    ensure_database_ready()
    ensure_task_tables()
    ensure_official_tasks(group_id)
    seed_default_badges()
    ensure_event_tables()
    seed_default_event_shop_items()
    display_name = request_display_name or get_line_display_name(group_id, user_id)
    set_notification_member_name(display_name)
    ensure_player(group_id, user_id, display_name)
    reset_monthly_sign_counts_if_needed()
    ensure_daily_carnival(group_id)
    vip_expired_now = check_vip_expired(group_id, user_id)
    player = get_player(group_id, user_id)

    # V18.1：遊戲管理後台入口（群長／管理員）。
    if text in {"遊戲管理", "遊戲後台", "遊戲設定"}:
        target_group_id = group_id if group_id != "PRIVATE" else get_selected_group(user_id)
        if not target_group_id or not _game_admin_permission(target_group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有遊戲管理權限。"))
            return
        game_admin_url = make_game_admin_url(_game_admin_base_url(), user_id, target_group_id)
        if not game_admin_url:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 尚未設定 PUBLIC_BASE_URL。"))
            return
        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(
                alt_text="🎮 遊戲管理後台",
                contents={
                    "type":"bubble",
                    "body":{"type":"box","layout":"vertical","paddingAll":"18px","contents":[
                        {"type":"text","text":"🎮 遊戲管理後台","size":"xl","weight":"bold"},
                        {"type":"text","text":"管理測試模式、遊戲開關、賞罰、每日上限與目前房間。","wrap":True,"margin":"md","color":"#667085"},
                        {"type":"button","style":"primary","margin":"xl","action":{"type":"uri","label":"開啟遊戲管理","uri":game_admin_url}}
                    ]}
                }
            )
        )
        return

    # V18 Phase 1：遊戲中心共用核心。
    if group_id != "PRIVATE":
        try:
            seed_game_settings(group_id)
            game_message = handle_game_center_command(text, group_id, user_id, display_name)
            if game_message is not None:
                line_bot_api.reply_message(event.reply_token, game_message)
                return
        except Exception as game_exc:
            print(f"[V18.3 GAME ERROR] group={group_id} user={user_id} text={text!r}: {game_exc}")
            traceback.print_exc()
            if text in {"遊戲", "遊戲中心", "小遊戲", "測試模式 開", "測試模式 關", "加入測試玩家", "遊戲管理", "遊戲後台", "遊戲設定"}:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=f"❌ 遊戲中心載入失敗：{type(game_exc).__name__}\n請將此畫面回傳給群長。")
                )
                return

    # V6.1.7：所有排行榜僅限群長／管理員。一般成員不顯示提示、直接靜默。
    ranking_only_commands = {
        "排行榜", "排行榜資料", "等級排行榜", "等級榜",
        "今日聊天榜", "昨日聊天榜", "貼圖排行榜", "貼圖榜", "今日貼圖榜",
        "連續簽到榜", "累積簽到榜", "簽到排行榜", "本月簽到榜",
        "金幣排行榜", "彩虹幣排行榜", "彩虹幣榜", "金幣榜",
        "我的排名", "簽到排名", "活動排行", "活動排行榜", "任務排行榜", "每日任務榜",
        "排行榜中心", "管理排行榜",
    }
    if text in ranking_only_commands:
        ranking_group_id = group_id if group_id != "PRIVATE" else get_selected_group(user_id)
        if not ranking_group_id or not is_admin(ranking_group_id, user_id):
            return
        # 私訊管理員可使用目前選擇群組的排行榜。
        if group_id == "PRIVATE":
            group_id = ranking_group_id
            player = get_player(group_id, user_id)

    if group_id != "PRIVATE" and is_private_admin_command(text):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🔒 此功能請到與機器人的一對一聊天室操作，避免影響群組聊天。")
        )
        return

    # V6.8.11 第一階段：聊天室查詢名片時，直接顯示完整彩虹男生名片。
    namecard_match = re.fullmatch(r"(?:我的名片|個人名片|名片)(?:\s+(.+))?", text)
    if namecard_match:
        if group_id == "PRIVATE":
            selected_group = get_selected_group(user_id)
            if not selected_group:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請先在群組內輸入「名片」使用。"))
                return
            group_id = selected_group
        query = (namecard_match.group(1) or "").strip()
        target_user_id, target_error = _resolve_namecard_target(group_id, query, user_id)
        if target_error or not target_user_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=target_error or "找不到這張名片。"))
            return
        try:
            if str(target_user_id) != str(user_id):
                _ensure_namecard_tables(); conn=get_connection()
                try:
                    with conn.cursor() as c:
                        c.execute("INSERT INTO player_card_visitors(group_id,target_user_id,viewer_user_id,viewed_at) VALUES(%s,%s,%s,CURRENT_TIMESTAMP) ON CONFLICT(group_id,target_user_id,viewer_user_id) DO UPDATE SET viewed_at=CURRENT_TIMESTAMP",(group_id,target_user_id,user_id))
                    conn.commit()
                finally: conn.close()
        except Exception:
            pass
        line_bot_api.reply_message(event.reply_token, _namecard_flex(group_id, target_user_id, user_id))
        return

    reaction_match = re.fullmatch(r"(?:名片按讚|按讚名片|收藏名片)\s+(.+)", text)
    if reaction_match:
        if group_id == 'PRIVATE':
            group_id = get_selected_group(user_id) or 'PRIVATE'
        action = 'favorite' if text.startswith('收藏名片') else 'like'
        target_user_id, err = _resolve_namecard_target(group_id, reaction_match.group(1), user_id)
        if err or not target_user_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=err or '找不到這張名片。'))
            return
        _namecard_reaction(group_id,target_user_id,user_id,action)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text='❤️ 已收藏這張名片！' if action=='favorite' else '👍 已幫這張名片按讚！'))
        return

    # V6.4.6：所有一般前台入口統一導向個人網頁，不再產生舊版 Flex 功能頁。
    # Rainbow Life 前台只有單一入口 /player。
    # 各功能由網頁內的底部分頁與快捷按鈕切換，避免產生不存在的
    # /player/fortune、/player/wheel、/player/profile 等網址。
    web_entry_targets = {
        "功能": "/player", "選單": "/player", "menu": "/player", "首頁": "/player",
        "功能中心": "/player", "個人中心": "/player", "我的中心": "/player",
        "我的": "/player", "我的狀態": "/player", "我的資料": "/player", "狀態": "/player",
        "每日運勢": "/player", "今日占卜": "/player", "運勢": "/player",
        "幸運輪盤": "/player", "每日轉盤": "/player", "轉盤": "/player",
        "商店": "/player", "商店中心": "/player", "VIP商店": "/player",
        "道具商店": "/player", "稱號商店": "/player",
        "背包": "/player", "我的背包": "/player", "道具背包": "/player",
        "行事曆": "/player", "日曆": "/player", "提醒事項": "/player",
        "編輯個人資料": "/player", "個人資料設定": "/player", "隱私設定": "/player",
    }
    web_target = web_entry_targets.get(text.lower()) or web_entry_targets.get(text)
    if web_target:
        target_group_id = group_id if group_id != "PRIVATE" else get_selected_group(user_id)
        if not target_group_id:
            line_bot_api.reply_message(event.reply_token, operation_notice_flex(
                "無法開啟個人中心", "請先在群組內使用一次機器人，讓系統確認你的群組。", False, False, ""
            ))
            return

        player_url = make_player_access_url(user_id, target_group_id, web_target)
        # V6.5：入口只綁群組，不綁發送者。
        # 不論誰點這張卡片，都會由 LIFF 重新辨識點擊者並顯示自己的資料。
        entry_card = player_center_entry_flex(player_url, "")
        line_bot_api.reply_message(event.reply_token, entry_card)
        return

    # V5.1：群規正式功能（修正首頁按鈕 !群規 找不到指令）。
    if text == "查看群規":
        rules = get_event_value(group_id, "group_rules", "") if group_id != "PRIVATE" else ""
        if not str(rules or "").strip():
            rules = (
                "1. 尊重每位成員，禁止人身攻擊與歧視。\n"
                "2. 禁止洗版、惡意廣告與未經同意散布他人資料。\n"
                "3. 請勿任意叫出他人名片，避免造成困擾。\n"
                "4. 活動、交易與群內互動請遵守群長公告。"
            )
        line_bot_api.reply_message(
            event.reply_token,
            operation_notice_flex("群規", str(rules), True, False, "!功能"),
        )
        return

    # V4.1：按鈕修正與子選單。
    if text == "生日設定":
        line_bot_api.reply_message(event.reply_token, birthday_center_flex(birthday_display(player.get("birthday"), player.get("birthday_year"))))
        return
    if text == "生日輸入說明":
        line_bot_api.reply_message(event.reply_token, birthday_input_help_flex())
        return
    if text == "排行榜":
        line_bot_api.reply_message(event.reply_token, ranking_menu_flex())
        return

    if text in ["今日聊天榜", "貼圖排行榜", "連續簽到榜", "累積簽到榜"]:
        field_map = {
            "今日聊天榜": ("today_msg_count", "💬 今日聊天排行榜"),
            "貼圖排行榜": ("today_sticker_count", "🖼️ 今日貼圖排行榜"),
            "連續簽到榜": ("streak_count", "🔥 連續簽到排行榜"),
        }
        conn = get_connection()
        try:
            with conn.cursor() as c:
                if text == "累積簽到榜":
                    c.execute("""SELECT p.user_id,p.name,COUNT(h.sign_date) AS score
                                 FROM players p LEFT JOIN sign_history h ON p.group_id=h.group_id AND p.user_id=h.user_id
                                 WHERE p.group_id=%s GROUP BY p.user_id,p.name
                                 ORDER BY score DESC,p.name ASC LIMIT 10""", (group_id,))
                    rows = c.fetchall(); title = "📆 累積簽到排行榜"
                else:
                    field,title = field_map[text]
                    c.execute(f"SELECT user_id,name,COALESCE({field},0) AS score FROM players WHERE group_id=%s ORDER BY COALESCE({field},0) DESC,name ASC LIMIT 10", (group_id,))
                    rows = c.fetchall()
        finally:
            conn.close()
        card_rows=[]
        for r in rows:
            card_rows.append({"name": name_with_badge(group_id,r['user_id'],r['name']), "score": int(r.get('score') or 0)})
        unit = " 次" if text in ["今日聊天榜", "貼圖排行榜", "連續簽到榜", "累積簽到榜"] else ""
        line_bot_api.reply_message(event.reply_token, ranking_result_flex(title, card_rows, unit))
        return
    if text == "昨日聊天榜":
        line_bot_api.reply_message(event.reply_token, operation_notice_flex(
            "昨日聊天排行榜", "目前尚未保存每日聊天歷史快照。下一階段加入歷史快照後，這裡會直接顯示昨日排名。", False, False, "!排行榜"
        ))
        return

    # V2.3.1 修正：所有「給予/收回/查看」管理指令放在前段處理，避免被其他流程攔截。
    if _handle_give_commands(event, text, group_id, user_id):
        return


    if text in ["機器人狀態", "回應狀態"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=bot_response_status_message(group_id)))
        return

    if text in ["關閉機器人", "機器人關閉", "關閉回應", "機器人安靜"]:
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        set_bot_response_enabled(group_id, False)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🔕 機器人回應已關閉。\n之後只會回應：開啟機器人／機器人狀態。"))
        return

    if text in ["開啟機器人", "機器人開啟", "開啟回應", "解除安靜"]:
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        set_bot_response_enabled(group_id, True)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🔔 機器人回應已開啟。"))
        return

    if text == "機器人模式":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=bot_response_status_message(group_id)))
        return

    if text.startswith("設定機器人模式 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return

        mode_text = text.replace("設定機器人模式 ", "", 1).strip()
        if mode_text in ["指令", "指令模式", "安靜"]:
            set_bot_mode(group_id, "command")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=(
                    "✅ 已切換為指令模式。\n\n"
                    "一般聊天不會觸發機器人回覆。\n"
                    "聊天 EXP、任務進度、掉寶會靜默累積。"
                ))
            )
            return

        if mode_text in ["全自動", "自動", "全自動模式"]:
            set_bot_mode(group_id, "auto")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=(
                    "✅ 已切換為全自動模式。\n\n"
                    "一般聊天可能觸發寶箱掉落、活動通知等回覆。"
                ))
            )
            return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="格式：設定機器人模式 指令\n或：設定機器人模式 全自動")
        )
        return

    if text.startswith("設定群規 "):
        if group_id == "PRIVATE":
            line_bot_api.reply_message(event.reply_token, operation_notice_flex("請在群組內操作", "請先在群組內綁定群長，之後可在私訊後台設定群規。", False, False, "!功能"))
            return
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, operation_notice_flex("權限不足", "目前這個 LINE 帳號尚未綁定為此群組的群長或管理員。請先在同一個群組輸入：綁定 群長 名字", False, False, "!功能"))
            return
        rules_text = text.replace("設定群規 ", "", 1).strip()
        if not rules_text:
            line_bot_api.reply_message(event.reply_token, operation_notice_flex("設定群規", "格式：!設定群規 群規內容", False, False, "!查看群規"))
            return
        set_event_value(group_id, "group_rules", rules_text)
        line_bot_api.reply_message(event.reply_token, operation_notice_flex("群規已更新", rules_text, True, True, "!查看群規"))
        return

    if text == "測試":
        status = "開啟" if is_bot_response_enabled(group_id) else "關閉"
        mode = "指令模式" if is_command_mode(group_id) else "全自動模式"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ Rainbow Life {APP_VERSION} 正常運作｜Build：{BUILD_ID}｜機器人回應：{status}｜{mode}"))
        return


    # ===== 生日系統指令 =====
    if text.startswith("生日設定 ") or text.startswith("設定生日 "):
        current_player = get_player(group_id, user_id) or {}
        if str(current_player.get("birthday") or "").strip():
            line_bot_api.reply_message(event.reply_token, operation_notice_flex(
                "🎂 生日已鎖定",
                f"你的生日已設定為 {birthday_display(current_player.get('birthday'), current_player.get('birthday_year'))}。\n設定完成後不可再次修改。",
                True, False, "!我的狀態"
            ))
            return
        birthday_raw = text.split(" ", 1)[1].strip() if " " in text else ""
        birthday, birth_year, error = parse_birthday_input(birthday_raw)
        if error:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error))
            return
        set_player_birthday(group_id, user_id, birthday, birth_year)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=(
            "✅ 生日設定完成！\n\n"
            f"🎂 生日：{birthday_display(birthday, birth_year)}\n"
            f"🎁 生日當天：彩虹幣 +{BIRTHDAY_COIN_REWARD}、EXP +{BIRTHDAY_EXP_REWARD}\n"
            "📢 群內有人發言時，機器人會自動送上祝福。"
        )))
        return

    if text == "我的生日":
        player = get_player(group_id, user_id)
        birthday = player.get("birthday") or ""
        birth_year = player.get("birthday_year")
        if not birthday:
            msg = "🎂 你尚未設定生日。\n\n輸入：!生日設定 07/10\n也可輸入：!生日設定 2000/07/10"
        else:
            msg = f"🎂 我的生日\n\n日期：{birthday_display(birthday, birth_year)}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text == "刪除生日":
        player = get_player(group_id, user_id) or {}
        if player.get("birthday"):
            line_bot_api.reply_message(event.reply_token, operation_notice_flex(
                "🎂 生日已鎖定",
                "生日設定完成後不可由本人刪除或重新設定。",
                False, False, "!我的狀態"
            ))
        else:
            line_bot_api.reply_message(event.reply_token, operation_notice_flex(
                "🎂 尚未設定生日",
                "目前沒有可刪除的生日資料。",
                False, False, "!生日設定"
            ))
        return

    if text == "今日壽星":
        celebrants, _ = grant_birthday_rewards(group_id)
        if not celebrants:
            msg = "🎂 今日壽星\n\n今天目前沒有壽星。"
        else:
            names = "\n".join(f"🎉 {name_with_badge(group_id, r['user_id'], r['name'])}" for r in celebrants)
            msg = "🎂 今日壽星\n\n" + names + f"\n\n🎁 生日禮：彩虹幣 +{BIRTHDAY_COIN_REWARD}、EXP +{BIRTHDAY_EXP_REWARD}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text in ["生日名單", "即將生日"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=upcoming_birthdays_message(group_id)))
        return

    if text in ["活動資訊", "目前活動", "活動狀態總覽"]:
        activity_group_id = group_id if group_id != "PRIVATE" else get_selected_group(user_id)
        line_bot_api.reply_message(event.reply_token, activity_center_flex(format_activity_info(), bool(activity_group_id and is_admin(activity_group_id, user_id))))
        return

    if text in ["活動任務", "目前活動任務"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_current_activity_tasks(is_vip=is_vip_active(group_id, user_id))))
        return

    if text in ["我的活動", "活動進度"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_my_activity(group_id, user_id)))
        return

    if text in ["活動背包", "活動道具"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_event_bag(group_id, user_id)))
        return

    if text == "活動排行":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_activity_rank(group_id)))
        return

    if text.startswith("我的") and len(text) <= 8:
        item_query = text.replace("我的", "", 1).strip()
        known_event_items = ["紅包", "燈籠", "粽子", "月餅", "南瓜燈", "聖誕禮物", "煙火", "彩虹星", "櫻花", "西瓜", "楓葉", "雪花"]
        if item_query in known_event_items:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_specific_activity_item(group_id, user_id, item_query)))
            return


    if text.startswith("給予活動道具 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        parts = text.split()
        if len(parts) != 4:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：給予活動道具 名字 道具 數量\n例：給予活動道具 政佑 月餅 50"))
            return
        target, error_msg = find_player_by_name(group_id, parts[1])
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        try:
            amount = int(parts[3])
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 數量請輸入數字。"))
            return
        ok, msg = grant_event_item_by_name(group_id, target["user_id"], parts[2], amount)
        if ok:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已給予 {target['name']}：{msg}"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return


    if text == "活動商品" or text.startswith("活動商品 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        event_name = text.replace("活動商品", "", 1).strip()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_event_shop_management(event_name)))
        return

    if had_command_prefix and text == "活動商店":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_event_shop(group_id, user_id)))
        return

    if text.startswith("兌換 "):
        item_name = text.replace("兌換 ", "", 1).strip()
        if not item_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：兌換 商品名稱\n例：兌換 月光旅人"))
            return
        ok, msg = exchange_event_shop_item(group_id, user_id, item_name, user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text.startswith(("新增活動商品 ", "刪除活動商品 ", "啟用活動商品 ", "停用活動商品 ")):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=(
                "🌈 活動商店為系統內建商店。\n\n"
                "商品會依節日與季節自動替換，後台不可新增、修改、上下架或刪除。"
            )),
        )
        return

    if text == "活動管理":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=(
                "🎉 活動管理｜V2.4 Step 5\n\n"
                "目前已接入：\n"
                "✅ 活動資訊\n"
                "✅ 活動任務\n"
                "✅ 年度活動與四季活動自動判斷\n"
                "✅ 活動道具資料表\n"
                "✅ 我的活動\n"
                "✅ 活動背包\n"
                "✅ 活動排行\n"
                "✅ 活動商店\n"
                "✅ 兌換活動商品\n"
                "✅ 節日／季節自動切換活動商店\n"
                "✅ 活動商品為系統內建且不可手動新增\n\n"
                "玩家指令：!活動商店｜兌換 商品名稱"
            ))
        )
        return

    if text == "控制中心":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=control_center_message()))
        return

    if text == "指令":
        help_group_id = group_id if group_id != "PRIVATE" else get_selected_group(user_id)
        line_bot_api.reply_message(event.reply_token, command_help_flex(bool(help_group_id and is_admin(help_group_id, user_id))))
        return
    if text == "基本指令":
        line_bot_api.reply_message(event.reply_token, simple_help_page_flex("🌈 基本功能", ["功能／選單：開啟功能中心", "我的狀態：查看個人資料", "占卜／轉盤：使用每日功能", "金庫：查看群組彩虹金庫"]))
        return
    if text == "簽到生日指令":
        line_bot_api.reply_message(event.reply_token, simple_help_page_flex("📅 簽到與生日", ["簽到：每日簽到", "補簽：查看補簽功能", "!生日設定 MM/DD", "我的生日／今日壽星／生日名單"]))
        return

    if text == "機器人設定":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=bot_settings_message(group_id)))
        return

    if text in ["排程中心", "排程狀態"]:
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=scheduler_center_message(group_id)))
        return

    if text == "立即檢查排程":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        run_scheduler_tick(group_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已立即檢查排程。\n可輸入【排程中心】查看狀態。"))
        return

    if text == "公告設定":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=announcement_settings_message(group_id)))
        return

    if text.startswith("設定公告 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        content = text.replace("設定公告 ", "", 1).strip()
        if not content:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：設定公告 公告內容"))
            return
        set_announcement_content(group_id, content)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 公告內容已設定。"))
        return

    if text.startswith("修改公告 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        content = text.replace("修改公告 ", "", 1).strip()
        if not content:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：修改公告 公告內容"))
            return
        set_announcement_content(group_id, content)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 公告內容已修改。"))
        return

    if text == "查看公告":
        line_bot_api.reply_message(event.reply_token, announcement_view_flex(get_announcement_content(group_id), is_announcement_enabled(group_id), get_announcement_time(group_id)))
        return

    if text == "刪除公告":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        clear_announcement_content(group_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 公告內容已刪除。"))
        return

    if text == "查看公告時間":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⏰ 目前公告推播時間：{get_announcement_time(group_id)}"))
        return

    if text.startswith("設定公告時間 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        time_text = text.replace("設定公告時間 ", "", 1).strip()
        if not valid_hhmm(time_text):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 時間格式錯誤。請輸入：設定公告時間 HH:MM，例如：設定公告時間 20:00"))
            return
        set_announcement_time(group_id, time_text)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 公告推播時間已設定為 {time_text}"))
        return

    if text == "開啟公告推播":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        set_announcement_enabled(group_id, True)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 公告推播已開啟。"))
        return

    if text == "關閉公告推播":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        set_announcement_enabled(group_id, False)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 公告推播已關閉。"))
        return

    if text in ["立即推播公告", "推播公告"]:
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        content = get_announcement_content(group_id)
        if not content:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 尚未設定公告內容。"))
            return
        # 立即推播用 reply 直接在群組顯示公告，避免 Push API 失敗時只看到「已推播」。
        announcement_text = format_announcement_message(content)
        line_bot_api.reply_message(
            event.reply_token,
            [
                TextSendMessage(text=announcement_text),
                TextSendMessage(text="✅ 公告已推播。")
            ]
        )
        return

    if not is_bot_response_enabled(group_id):
        return

    if is_command_mode(group_id) and not is_command_text(text):
        # 使用 !／！ 開頭代表使用者正在下指令，輸入錯誤時要明確提醒。
        if had_command_prefix:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"❓ 找不到指令：{raw_text}\n\n請確認指令名稱與格式。\n輸入 !指令 可查看全部功能。")
            )
            return
        # 指令模式：一般聊天不回覆掉寶/EXP/任務進度，但活動開始/結束仍要公告。
        run_scheduler_tick(group_id)
        handle_silent_chat(group_id, user_id, text)
        return

    # 全自動模式或指令觸發時，也順手檢查排程中心。
    run_scheduler_tick(group_id)

    if text in ["今日活動", "狂歡時段"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_today_activity(group_id)))
        return

    if text == "活動狀態":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_activity_status(group_id)))
        return

    if text.startswith("綁定 群長"):
        name = text.replace("綁定 群長", "", 1).strip()
        if group_id == "PRIVATE":
            selected_group_id = get_selected_group(user_id)
            if selected_group_id:
                group_id = selected_group_id
            else:
                line_bot_api.reply_message(event.reply_token, operation_notice_flex("無法綁定", "請先在群組內完成首次綁定，或先從網頁後台選擇管理群組。", False, False, "!功能"))
                return
        if not name:
            line_bot_api.reply_message(event.reply_token, operation_notice_flex("綁定群長", "格式：綁定 群長 名字", False, False, "!功能"))
            return
        try:
            set_owner(group_id, user_id)
            register_group(group_id, get_line_group_name(group_id))
            set_selected_group(user_id, group_id)
            conn = get_connection()
            try:
                with conn.cursor() as c:
                    c.execute("UPDATE players SET name=%s WHERE group_id=%s AND user_id=%s", (name, group_id, user_id))
                conn.commit()
            finally:
                conn.close()
            if not is_admin(group_id, user_id):
                raise RuntimeError("群長權限驗證失敗")
        except Exception as exc:
            print(f"[OWNER_BIND_ERROR] group={group_id!r} user={user_id!r}: {exc}")
            line_bot_api.reply_message(event.reply_token, operation_notice_flex("綁定失敗", "群長權限未能寫入資料庫，請稍後重試。", False, False, "!功能"))
            return
        line_bot_api.reply_message(event.reply_token, operation_notice_flex("群長綁定成功", f"已將 {name} 綁定為本群群長，後台與群規權限已立即生效。", True, True, "!後台"))
        return

    # ===== V2.0.3 群長活動指令 =====
    if text.startswith("群長發福利 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        parts = text.split()
        if len(parts) != 2:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：群長發福利 數量"))
            return
        try:
            amount = int(parts[1])
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 數量請輸入數字。"))
            return
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("UPDATE players SET coins = coins + %s WHERE group_id=%s", (amount, group_id))
            count = c.rowcount
            conn.commit()
        conn.close()
        msg = f"🌈 群長發放福利！\n\n🎁 全體成員 +{amount} 彩虹幣\n👥 發放人數：{count} 人\n\n感謝群長 ❤️"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text.startswith("群長發經驗 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        parts = text.split()
        if len(parts) != 2:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：群長發經驗 數量"))
            return
        try:
            amount = int(parts[1])
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 數量請輸入數字。"))
            return
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("UPDATE players SET exp = exp + %s WHERE group_id=%s", (amount, group_id))
            count = c.rowcount
            conn.commit()
        conn.close()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⭐ 群長發放經驗！\n\n全體 EXP +{amount}\n👥 發放人數：{count} 人"))
        return

    if text.startswith("群長發等級 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        parts = text.split()
        if len(parts) != 2:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：群長發等級 數量"))
            return
        try:
            amount = int(parts[1])
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 數量請輸入數字。"))
            return
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("UPDATE players SET level = level + %s WHERE group_id=%s", (amount, group_id))
            count = c.rowcount
            conn.commit()
        conn.close()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🎉 群長發放等級！\n\n全體等級 +{amount}\n👥 發放人數：{count} 人"))
        return

    if text.startswith("開始狂歡"):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        hours = parse_hours_from_text(text, 1)
        set_activity_until(group_id, "manual_carnival_until", hours * 3600)
        set_event_value(group_id, "manual_carnival_tracked_until", get_activity_until(group_id, "manual_carnival_until"))
        set_event_value(group_id, "manual_carnival_end_announced_until", "0")
        msg = (
            "📢 👑 群長開啟全群狂歡！\n\n"
            f"⏰ 持續時間：{hours} 小時\n"
            "✨ 聊天 EXP ×2\n"
            "🌈 寶箱掉落率跟著加成\n\n"
            "快來聊天衝等吧！"
        )
        announce_group(group_id, msg)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已開啟狂歡模式。"))
        return

    if text == "停止狂歡":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        clear_activity(group_id, "manual_carnival_until")
        announce_group(group_id, "📢 群長狂歡模式已結束。")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已停止狂歡模式。"))
        return

    if text in ["開始寶箱雨", "寶箱雨模式"]:
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        set_activity_until(group_id, "chest_rain_until", 3600)
        msg = (
            "📢 🎁 群長開啟寶箱雨！\n\n"
            "⏰ 持續時間：1 小時\n"
            "🎁 聊天寶箱掉落率提升至 15%\n\n"
            "聊天室刷起來，寶箱掉起來！"
        )
        announce_group(group_id, msg)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已開啟寶箱雨模式。"))
        return

    if text == "停止寶箱雨":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        clear_activity(group_id, "chest_rain_until")
        announce_group(group_id, "📢 寶箱雨模式已結束。")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已停止寶箱雨。"))
        return

    if text in ["開始輪盤", "開始輪盤活動", "輪盤大暴送"]:
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        set_activity_until(group_id, "wheel_boost_until", 3600)
        msg = (
            "📢 🎰 群長開啟輪盤大暴送！\n\n"
            "⏰ 持續時間：1 小時\n"
            "🎰 幸運輪盤最低 250、最高 1000 彩虹幣\n\n"
            "還沒轉輪盤的快衝！"
        )
        announce_group(group_id, msg)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已開啟輪盤大暴送。"))
        return

    if text == "停止輪盤":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        clear_activity(group_id, "wheel_boost_until")
        announce_group(group_id, "📢 輪盤大暴送已結束。")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已停止輪盤活動。"))
        return

    # ===== V2.1.0 任務中心 =====
    if text == "每日任務":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_user_tasks(group_id, user_id, "daily")))
        return

    if text == "每週任務":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_user_tasks(group_id, user_id, "weekly")))
        return

    if text == "每月任務":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_user_tasks(group_id, user_id, "monthly")))
        return

    if text == "每季任務":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_user_tasks(group_id, user_id, "quarterly")))
        return

    if text == "我的任務":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_my_tasks(group_id, user_id)))
        return

    if text.startswith("領取任務 "):
        parts = text.split()
        if len(parts) != 2:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：領取任務 任務ID"))
            return

        try:
            task_id = int(parts[1])
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 任務ID請輸入數字。"))
            return

        ok, msg = claim_task_reward(group_id, user_id, task_id)
        if ok:
            msg += format_activity_item_gain(grant_active_event_items(group_id, user_id, "task"))
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text in ["任務管理", "任務列表"]:
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=task_management_message(group_id)))
        return

    if text.startswith("新增每日任務 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return

        data, error = parse_add_task_command(text, "daily")
        if error:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error))
            return

        add_task(group_id, **data)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已新增每日任務：{data['name']}"))
        return

    if text.startswith("新增每週任務 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return

        data, error = parse_add_task_command(text, "weekly")
        if error:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error))
            return

        add_task(group_id, **data)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已新增每週任務：{data['name']}"))
        return

    if text.startswith("新增每月任務 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return

        data, error = parse_add_task_command(text, "monthly")
        if error:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error))
            return

        add_task(group_id, **data)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已新增每月任務：{data['name']}"))
        return

    if text.startswith("新增每季任務 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return

        data, error = parse_add_task_command(text, "quarterly")
        if error:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error))
            return

        add_task(group_id, **data)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已新增每季任務：{data['name']}"))
        return

    if text.startswith("刪除任務 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return

        task_name = text.replace("刪除任務 ", "", 1).strip()
        if not task_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：刪除任務 任務名稱"))
            return

        changed = delete_task(group_id, task_name)
        if changed == -1:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📘 官方任務不能刪除：{task_name}\n可改用：停用任務 {task_name}"))
        elif changed:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已刪除任務：{task_name}"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到任務：{task_name}"))
        return

    if text.startswith("停用任務 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        task_name = text.replace("停用任務 ", "", 1).strip()
        if not task_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：停用任務 任務名稱"))
            return
        changed = set_task_active(group_id, task_name, False)
        if changed:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已停用任務：{task_name}"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到任務：{task_name}"))
        return

    if text.startswith("啟用任務 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        task_name = text.replace("啟用任務 ", "", 1).strip()
        if not task_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：啟用任務 任務名稱"))
            return
        changed = set_task_active(group_id, task_name, True)
        if changed:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已啟用任務：{task_name}"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到任務：{task_name}"))
        return

    if text in ["每日任務榜", "任務排行榜"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=task_rank_message(group_id, user_id, "daily")))
        return

    if text == "每週任務榜":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=task_rank_message(group_id, user_id, "weekly")))
        return

    if text == "每月任務榜":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=task_rank_message(group_id, user_id, "monthly")))
        return

    if text == "每季任務榜":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=task_rank_message(group_id, user_id, "quarterly")))
        return

    # ===== 主題簽到測試（不寫入資料、不發獎） =====
    if text == "測試簽到" or text.startswith("測試簽到 "):
        theme_arg = text[len("測試簽到"):].strip()
        valid = {"春白","春夜","夏白","夏夜","秋白","秋夜","冬白","冬夜","VIP","春季白天","春季夜晚","夏季白天","夏季夜晚","秋季白天","秋季夜晚","冬季白天","冬季夜晚"}
        if theme_arg and theme_arg not in valid:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="可用格式：\n測試簽到\n測試簽到 春白／春夜／夏白／夏夜／秋白／秋夜／冬白／冬夜／VIP"))
            return
        now_tw = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        line_bot_api.reply_message(event.reply_token, sign_result_flex({
            "preview": True, "already": False, "theme": theme_arg,
            "date": now_tw.strftime("%Y/%m/%d"), "time": now_tw.strftime("%H:%M"),
            "coins": 300, "exp": 150, "streak": 5, "total": 20,
            "bonus_lines": ["🎯 下一個里程碑：7 天（還差 2 天）", "🎁 範例活動道具 +1"],
            "next_time": "明天 05:00 後",
        }))
        return

    # ===== 簽到系統 =====
    if text == "簽到":
        ensure_sign_history_for_player(group_id, user_id)
        sign_today = get_sign_date()
        sign_month = get_sign_month()
        sign_time = current_sign_time_text()
        yesterday = (datetime.date.fromisoformat(sign_today) - datetime.timedelta(days=1)).isoformat()

        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute("""
                    SELECT name, last_sign_in, streak_count, sign_month_count
                    FROM players
                    WHERE group_id=%s AND user_id=%s
                    FOR UPDATE
                """, (group_id, user_id))
                row = c.fetchone()
                if not row:
                    conn.rollback()
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到玩家資料，請稍後再試。"))
                    return

                last_sign_in = str(row["last_sign_in"] or "")
                old_streak = int(row["streak_count"] or 0)
                total_sign_count = int(row["sign_month_count"] or 0)

                c.execute("""
                    SELECT signed_at
                    FROM sign_records
                    WHERE group_id=%s AND user_id=%s AND sign_date=%s
                """, (group_id, user_id, sign_today))
                existing = c.fetchone()

                if last_sign_in == sign_today or existing:
                    # 今日已完成簽到：不重複發獎，但仍回覆個人中心入口，
                    # 避免使用者誤以為機器人沒有反應。
                    conn.rollback()
                    line_bot_api.reply_message(
                        event.reply_token,
                        sign_result_flex({
                            "already": True,
                            "date": format_date_zh(sign_today),
                            "time": sign_time,
                            "streak": old_streak,
                            "total": total_sign_count,
                            "next_time": next_sign_time_text(),
                        })
                    )
                    return

                new_streak = old_streak + 1 if last_sign_in == yesterday else 1
                new_total_count = total_sign_count + 1
                daily_bonus_coin, milestone_coin, milestone_exp, milestone_name = get_streak_reward(new_streak)
                sign_cfg = get_sign_settings(group_id)
                fortune_exp_mult, fortune_coin_mult = get_fortune_multipliers(row)
                admin_benefit = get_admin_benefits(group_id, user_id)
                total_coin = round((int(sign_cfg.get("coin") or SIGN_COIN) + daily_bonus_coin + milestone_coin + int(admin_benefit.get("sign_coins") or 0)) * fortune_coin_mult)
                total_exp = round((int(sign_cfg.get("exp") or SIGN_EXP) + milestone_exp + int(admin_benefit.get("sign_exp") or 0)) * fortune_exp_mult)

                c.execute("""
                    INSERT INTO sign_records (group_id, user_id, sign_date, source, signed_at)
                    VALUES (%s, %s, %s, 'normal', CURRENT_TIMESTAMP)
                """, (group_id, user_id, sign_today))
                c.execute("""
                    UPDATE players
                    SET last_sign_in=%s,
                        streak_count=%s,
                        sign_month=%s,
                        sign_month_count=%s,
                        coins=COALESCE(coins, 0)+%s,
                        exp=GREATEST(COALESCE(exp, 0)+%s, 0),
                        level=GREATEST(1, (GREATEST(COALESCE(exp, 0)+%s, 0) / 100)+1)
                    WHERE group_id=%s AND user_id=%s
                """, (
                    sign_today, new_streak, sign_month, new_total_count,
                    total_coin, total_exp, total_exp, group_id, user_id
                ))
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        reward_lines = []
        if int(admin_benefit.get("sign_coins") or 0) or int(admin_benefit.get("sign_exp") or 0):
            reward_lines.append(f"🛡️ 管理員福利：+{int(admin_benefit.get('sign_coins') or 0)} 彩虹幣、+{int(admin_benefit.get('sign_exp') or 0)} EXP")
        if daily_bonus_coin > 0:
            reward_lines.append(f"🔥 連續加成：+{daily_bonus_coin} 彩虹幣")
        if milestone_name:
            reward_lines.append(f"🎉 {milestone_name}獎勵：+{milestone_coin} 彩虹幣、+{milestone_exp} EXP")
        next_milestone = get_next_streak_milestone(new_streak)
        days_left = max(next_milestone - new_streak, 0)
        reward_lines.append(f"🎯 下一個里程碑：{next_milestone} 天（還差 {days_left} 天）")
        bonus_msg = "\n" + "\n".join(reward_lines)

        update_task_progress(group_id, user_id, "sign", 1)
        activity_gain_msg = format_activity_item_gain(grant_active_event_items(group_id, user_id, "sign"))
        notice_bonus_lines = list(reward_lines)
        if activity_gain_msg:
            notice_bonus_lines.extend([line for line in activity_gain_msg.strip().split("\n") if line.strip()])
        line_bot_api.reply_message(
            event.reply_token,
            sign_result_flex({
                "already": False,
                "date": format_date_zh(sign_today),
                "time": sign_time,
                "coins": total_coin,
                "exp": total_exp,
                "streak": new_streak,
                "total": new_total_count,
                "bonus_lines": notice_bonus_lines,
                "next_time": next_sign_time_text(),
            })
        )
        return

    if text == "補簽":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=makeup_sign_help(group_id, user_id)))
        return

    if text.startswith("補簽"):
        match = re.fullmatch(r"補簽\s*([0-9]+)\s*天", text)
        if not match:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 補簽指令格式錯誤\n\n正確格式：\n!補簽 1天\n！補簽 1天\n補簽 1天\n\n可輸入 1～7 天。")
            )
            return
        days = int(match.group(1))
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=handle_makeup_sign(group_id, user_id, days)))
        return

    if text in ["簽到紀錄", "我的簽到"]:
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("""
                SELECT name, last_sign_in, streak_count, sign_month, sign_month_count
                FROM players
                WHERE group_id=%s AND user_id=%s
            """, (group_id, user_id))
            row = c.fetchone()
        conn.close()
        msg = (
            f"📘 簽到紀錄\n\n"
            f"👤 {row['name']}\n"
            f"📅 上次簽到：{row['last_sign_in'] or '尚未簽到'}\n"
            f"🔥 連續簽到：{row['streak_count'] or 0} 天\n"
            f"📝 累積簽到：{row['sign_month_count'] or 0} 天\n\n"
            f"⏰ 每天 05:00 後可重新簽到\n"
            f"♾️ 累積簽到永久保留，不會每月歸零"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # ===== 每日功能 =====
    if False and text == "每日運勢":
        player = get_player(group_id, user_id)
        if not (player.get("birthday") or ""):
            line_bot_api.reply_message(event.reply_token, operation_notice_flex("🔮 每日運勢", "請先設定生日後再占卜。\n輸入：!生日設定 07/10", "!生日設定"))
            return
        if player.get("fortune_result_date") == today and player.get("fortune_message"):
            # 今日已抽過運勢：不重新抽、不重複套用效果，直接顯示今日結果。
            saved_message = str(player.get("fortune_message") or "").strip()
            if saved_message.startswith("🌈 每日運勢"):
                saved_message = saved_message[len("🌈 每日運勢"):].lstrip("\n ")
            body = (
                "✅ 今日已使用，明天再來\n"
                "🕔 每日 05:00 更新\n\n"
                "【今日運勢結果】\n"
                f"{saved_message}"
            )
            line_bot_api.reply_message(
                event.reply_token,
                operation_notice_flex("🔮 每日運勢", body, "!功能")
            )
            return

        score = random.randint(1, 100)
        level_name = fortune_level_from_score(score)
        data = FORTUNE_LEVELS[level_name]
        exp_mult, coin_mult, wheel_bonus = data["exp"], data["coin"], data["wheel"]
        special = ""
        roll = random.random()
        if roll < 0.005:
            special = "🌈 彩虹之神降臨：今日 EXP、彩虹幣 ×3，轉盤稀有率 +40%"
            exp_mult = coin_mult = 3.0; wheel_bonus = 40
        elif roll < 0.015:
            special = "👑 幸運之神：今日 EXP、彩虹幣 ×2，轉盤稀有率 +30%"
            exp_mult = coin_mult = 2.0; wheel_bonus = 30
        elif roll < 0.035:
            special = "🍀 四葉草祝福：所有效果額外提升"
            exp_mult += 0.10; coin_mult += 0.10; wheel_bonus += 10

        fortune_data = build_fortune_data(player, score, level_name, special)
        fortune_data.update({"exp_mult": exp_mult, "coin_mult": coin_mult, "wheel_bonus": wheel_bonus})
        msg = build_fortune_message(player, level_name, special, score)
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("""
                UPDATE players SET last_fortune_date=%s, fortune_result_date=%s,
                    fortune_level=%s, fortune_message=%s, fortune_exp_multiplier=%s,
                    fortune_coin_multiplier=%s, fortune_luck_score=%s, fortune_wheel_bonus=%s
                WHERE group_id=%s AND user_id=%s
            """, (today, today, level_name, msg, exp_mult, coin_mult, score, wheel_bonus, group_id, user_id))
            conn.commit()
        conn.close()
        update_task_progress(group_id, user_id, "fortune", 1)
        line_bot_api.reply_message(event.reply_token, fortune_result_flex(fortune_data))
        return

    if text in ["幸運輪盤", "幸運轉盤"]:
        player = get_player(group_id, user_id)
        spin_date = str(player.get("wheel_spin_date") or player.get("last_wheel_date") or "")
        used = int(player.get("wheel_spin_count") or (1 if player.get("last_wheel_date") == today else 0)) if spin_date == today else 0
        admin_benefit = get_admin_benefits(group_id, user_id)
        luck_score = int(player.get("fortune_luck_score") or 50) if player.get("fortune_result_date") == today else 50
        luck_score = min(100, luck_score + int(admin_benefit.get("luck_bonus") or 0))
        luck_bonus = get_fortune_wheel_bonus(player)
        max_spins = 1 + (1 if bool(player.get("is_vip")) else 0) + (1 if luck_score >= 95 else 0) + int(admin_benefit.get("wheel_spins") or 0)
        if used >= max_spins:
            history = []
            try:
                if spin_date == today and player.get("wheel_reward_history"):
                    parsed = json.loads(player.get("wheel_reward_history") or "[]")
                    history = parsed if isinstance(parsed, list) else []
            except (TypeError, ValueError, json.JSONDecodeError):
                history = []
            if history:
                result_lines = []
                for item in history:
                    spin_no = int(item.get("spin") or (len(result_lines) + 1))
                    icon = str(item.get("icon") or "🎁")
                    rarity = str(item.get("rarity") or "獎勵")
                    reward_text = str(item.get("reward") or "獎勵已發放")
                    coin_value = int(item.get("coins") or 0)
                    exp_value = int(item.get("exp") or 0)
                    result_lines.append(f"第 {spin_no} 次｜{icon} {rarity}")
                    result_lines.append(f"🎁 {reward_text}（已發放）")
                    if coin_value or exp_value:
                        result_lines.append(f"🌈 彩虹幣 +{coin_value:,}｜⭐ EXP +{exp_value:,}")
                    result_lines.append("")
                body = (
                    "✅ 今日已使用，明天再來\n"
                    "🕔 每日 05:00 更新\n\n"
                    "【今日轉盤結果】\n"
                    + "\n".join(result_lines).rstrip()
                )
                line_bot_api.reply_message(
                    event.reply_token,
                    operation_notice_flex("🎡 豪華幸運轉盤", body, "!功能")
                )
            else:
                line_bot_api.reply_message(event.reply_token, operation_notice_flex(
                    "🎡 豪華幸運轉盤",
                    f"✅ 今日已使用，明天再來\n🕔 每日 05:00 更新\n\n今日次數：{used}/{max_spins}\n獎勵已發放，舊版本次紀錄無法還原。",
                    "!功能"
                ))
            return
        reward = choose_wheel_reward(luck_score, luck_bonus + (15 if is_wheel_boost_active(group_id) else 0))
        coin_gain = reward["amount"] if reward["type"] in ("coin", "both") else 0
        exp_gain = reward["amount"] if reward["type"] in ("exp", "both") else 0
        # 彩虹幣效果依照今日幸運值實際套用；EXP 交由 add_exp 再套 VIP 倍率。
        _, coin_mult = get_fortune_multipliers(player)
        coin_gain = max(0, round(coin_gain * coin_mult))
        if coin_gain: add_coins(group_id, user_id, coin_gain)
        exp_result = add_exp(group_id, user_id, exp_gain) if exp_gain else {"gained":0, "leveled_up":False}
        actual_exp = int(exp_result.get("gained") or 0)
        used += 1
        history = []
        if spin_date == today:
            try:
                parsed = json.loads(player.get("wheel_reward_history") or "[]")
                history = parsed if isinstance(parsed, list) else []
            except (TypeError, ValueError, json.JSONDecodeError):
                history = []
        history.append({
            "spin": used,
            "rarity": reward["rarity"],
            "icon": reward["icon"],
            "reward": reward["label"],
            "coins": coin_gain,
            "exp": actual_exp,
        })
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("""UPDATE players SET last_wheel_date=%s, wheel_spin_date=%s, wheel_spin_count=%s,
                         wheel_reward_history=%s WHERE group_id=%s AND user_id=%s""",
                      (today, today, used, json.dumps(history, ensure_ascii=False), group_id, user_id))
            conn.commit()
        conn.close()
        update_task_progress(group_id, user_id, "wheel", 1)
        activity_lines = [x for x in format_activity_item_gain(grant_active_event_items(group_id, user_id, "wheel")).strip().split("\n") if x.strip()]
        line_bot_api.reply_message(event.reply_token, wheel_result_flex({
            "name": player.get("name") or "成員", "rarity": reward["rarity"], "icon": reward["icon"],
            "reward": reward["label"], "coins": coin_gain, "exp": actual_exp,
            "luck_score": luck_score, "luck_bonus": luck_bonus,
            "used": used, "max_spins": max_spins, "activity_lines": activity_lines,
            "boost": is_wheel_boost_active(group_id),
        }))
        return

    # V4.1：一般成員金庫卡片。
    if text == "金庫" and group_id != "PRIVATE" and not is_admin(group_id, user_id):
        ensure_commerce_tables()
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute("SELECT balance,lifetime_income FROM group_vaults WHERE group_id=%s", (group_id,))
                vr = c.fetchone() or {"balance":0,"lifetime_income":0}
                c.execute("""SELECT
                    COALESCE(SUM(vault_added) FILTER (WHERE (created_at AT TIME ZONE 'Asia/Taipei')::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Taipei')::date),0) AS today,
                    COALESCE(SUM(vault_added) FILTER (WHERE (created_at AT TIME ZONE 'Asia/Taipei')::date=((CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Taipei')::date-1)),0) AS yesterday,
                    COALESCE(SUM(vault_added) FILTER (WHERE created_at >= date_trunc('week', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Taipei') AT TIME ZONE 'Asia/Taipei'),0) AS week
                    FROM purchase_history WHERE group_id=%s""", (group_id,))
                inc = c.fetchone() or {}
        finally:
            conn.close()
        balance=int(vr.get("balance") or 0); target=50000
        percent=min(int(balance/target*100),100) if target else 0
        filled=min(percent//10,10)
        data={"balance":balance,"lifetime":int(vr.get("lifetime_income") or 0),"target":target,"remaining":max(target-balance,0),"bar":"█"*filled+"░"*(10-filled)+f" {percent}%","today":int(inc.get("today") or 0),"yesterday":int(inc.get("yesterday") or 0),"week":int(inc.get("week") or 0)}
        line_bot_api.reply_message(event.reply_token, vault_card_flex(data))
        return

    # ===== V2.8.2 群長消費明細／金庫（唯讀） =====
    if text in ["今日消費", "昨日消費", "總消費", "金庫", "金庫紀錄"] or text.startswith("消費紀錄"):
        if group_id == "PRIVATE":
            private_reply = handle_private_control_command(text, user_id)
            if private_reply is not None:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=private_reply))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 尚未選擇可管理的群組，請先輸入 !後台。"))
            return
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⛔ 此功能僅限群長查看。"))
            return
        if text == "今日消費":
            msg = format_purchase_details(group_id, "today")
        elif text == "昨日消費":
            msg = format_purchase_details(group_id, "yesterday")
        elif text == "總消費":
            msg = format_purchase_details(group_id, "all")
        elif text.startswith("消費紀錄"):
            member_query = text[len("消費紀錄"):].strip()
            msg = format_member_purchase_details(group_id, member_query)
        elif text == "金庫":
            msg = format_vault(group_id)
        else:
            msg = format_vault_history(group_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # ===== V5.4.7 商店唯一入口：只接受 !商店；活動商店維持 !活動商店 =====
    if False and had_command_prefix and (text == "商店" or text.startswith("商店 ")):
        target_group_id = group_id if group_id != "PRIVATE" else get_selected_group(user_id)
        page = 1
        if text.startswith("商店 "):
            try:
                page = max(1, int(text.split(maxsplit=1)[1]))
            except Exception:
                page = 1
        settings = get_vip_settings(target_group_id)
        general_items = []
        for row in list_shop_items(active_only=True):
            item = dict(row)
            if str(item.get("category") or "").strip() == "活動":
                continue
            if str(item.get("category") or "").strip() == "VIP":
                if not settings.get("shop_enabled", True):
                    continue
                item["price"] = effective_vip_price(target_group_id, item.get("item_type"), item.get("price"))
            general_items.append(item)
        line_bot_api.reply_message(event.reply_token, front_portal_flex(is_admin(target_group_id, user_id), general_items, list_titles(include_vip=True), "shop"))
        return

    # 舊商店指令已移除，不再導向，避免多入口混亂。
    if text in ["商店中心", "VIP商店", "道具商店", "稱號商店", "查看 VIP", "查看 道具", "查看 稱號"]:
        if had_command_prefix:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="此入口已停用，請統一使用：!商店"))
        return

    if text in ["VIP稱號", "查看 VIP稱號"]:
        player = get_player(group_id, user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_vip_title_shop(bool(player["is_vip"]))))
        return

    if text.startswith("購買 "):
        item_name = text.replace("購買 ", "", 1).strip()
        if not item_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：購買 商品名稱"))
            return

        current_player = get_player(group_id, user_id)
        requested = get_shop_item(item_name)[0] if item_name else None
        if requested and requested.get("item_type") in ["vip_7", "vip_30", "vip_forever"]:
            raw_until = str((current_player or {}).get("vip_until") or "").upper()
            if raw_until in ["PERMANENT", "FOREVER", "永久", "永久VIP"]:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你已擁有永久 VIP，無法再購買期限或永久 VIP。"))
                return
        ok, msg, item = buy_shop_item(group_id, user_id, item_name)
        if not ok:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return

        if item and item["item_type"] in ["vip_7", "vip_30", "vip_forever"]:
            # VIP 已在 buy_shop_item 的同一筆交易內完成開通／累加。
            update_task_progress(group_id, user_id, "vip", 1)

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text in ["購買VIP7", "購買 VIP7天"]:
        current_player = get_player(group_id, user_id)
        if str((current_player or {}).get("vip_until") or "").upper() in ["PERMANENT", "FOREVER", "永久", "永久VIP"]:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你已擁有永久 VIP，無法重複購買。"))
            return
        ok, msg, item = buy_shop_item(group_id, user_id, "VIP7天")
        if ok:
            update_task_progress(group_id, user_id, "vip", 1)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text in ["購買VIP30", "購買 VIP30天"]:
        current_player = get_player(group_id, user_id)
        if str((current_player or {}).get("vip_until") or "").upper() in ["PERMANENT", "FOREVER", "永久", "永久VIP"]:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你已擁有永久 VIP，無法重複購買。"))
            return
        ok, msg, item = buy_shop_item(group_id, user_id, "VIP30天")
        if ok:
            update_task_progress(group_id, user_id, "vip", 1)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text in ["購買VIP永久", "購買 VIP永久"]:
        current_player = get_player(group_id, user_id)
        if str((current_player or {}).get("vip_until") or "").upper() in ["PERMANENT", "FOREVER", "永久", "永久VIP"]:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你已擁有永久 VIP，無法重複購買。"))
            return
        ok, msg, item = buy_shop_item(group_id, user_id, "VIP永久")
        if ok:
            update_task_progress(group_id, user_id, "vip", 1)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text.startswith("挑選稱號 ") or text.startswith("購買稱號 "):
        if text.startswith("挑選稱號 "):
            title_name = text.replace("挑選稱號 ", "", 1).strip()
        else:
            title_name = text.replace("購買稱號 ", "", 1).strip()

        if not title_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：挑選稱號 稱號名"))
            return

        player = get_player(group_id, user_id)
        ok, msg = buy_title(group_id, user_id, title_name, bool(player["is_vip"]))
        if ok:
            update_task_progress(group_id, user_id, "title", 1)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text == "我的稱號":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=my_titles_message(group_id, user_id)))
        return

    if text.startswith("裝備稱號 "):
        title_name = text.replace("裝備稱號 ", "", 1).strip()
        if not title_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：裝備稱號 稱號名"))
            return
        player = get_player(group_id, user_id)
        ok, msg = equip_title(group_id, user_id, title_name, bool(player["is_vip"]))
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return


    # ===== V2.2.9 稱號授予 / V2.3.0 VIP 管理 =====
    if text == "VIP管理":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=vip_management_message()))
        return

    if text == "我的VIP":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=vip_status_message(group_id, user_id, name_with_badge(group_id, user_id, player["name"]))))
        return

    if text == "VIP禮包":
        ok, msg = claim_vip_gift(group_id, user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text.startswith("給予VIP"):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, days_text = resolve_target_and_rest(event, group_id, "給予VIP")
        days_text = parse_vip_duration_text(days_text)
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target or not days_text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：給予VIP @對方 7天 / 30天 / 90天 / 永久\n給自己可用：給予VIP 名字 永久"))
            return
        ok, msg = grant_vip(group_id, target["user_id"], days_text, user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"{msg}\n👤 {name_with_badge(group_id, target['user_id'], target['name'])}"))
        return

    if text.startswith("延長VIP"):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, days_text = resolve_target_and_rest(event, group_id, "延長VIP")
        days_text = parse_vip_duration_text(days_text)
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target or not days_text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：延長VIP @對方 30天\n給自己可用：延長VIP 名字 30天"))
            return
        ok, msg = extend_vip(group_id, target["user_id"], days_text, user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"{msg}\n👤 {name_with_badge(group_id, target['user_id'], target['name'])}"))
        return

    if text.startswith("收回VIP"):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, _ = resolve_target_and_rest(event, group_id, "收回VIP")
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：收回VIP @對方\n收回自己可用：收回VIP 名字"))
            return
        cancel_vip(group_id, target["user_id"], user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已收回 VIP。\n👤 {name_with_badge(group_id, target['user_id'], target['name'])}"))
        return

    if text.startswith("查看VIP"):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, _ = resolve_target_and_rest(event, group_id, "查看VIP")
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：查看VIP @對方\n查看自己可用：查看VIP 名字"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=vip_status_message(group_id, target["user_id"], name_with_badge(group_id, target["user_id"], target["name"]))))
        return

    if text.startswith("VIP紀錄"):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, _ = resolve_target_and_rest(event, group_id, "VIP紀錄")
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：VIP紀錄 @對方\n查看自己可用：VIP紀錄 名字"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=vip_record_message(group_id, target["user_id"], target["name"])))
        return

    if text.startswith("佩戴稱號 "):
        title_name = text.replace("佩戴稱號 ", "", 1).strip()
        if not title_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：佩戴稱號 稱號名"))
            return
        player = get_player(group_id, user_id)
        ok, msg = equip_title(group_id, user_id, title_name, bool(player["is_vip"]))
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text == "卸下稱號":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=unequip_title(group_id, user_id)))
        return

    if text.startswith("給予稱號 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, title_name = resolve_target_and_rest(event, group_id, "給予稱號")
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target or not title_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：給予稱號 @對方 稱號名稱\n給自己可用：給予稱號 名字 稱號名稱"))
            return
        ok, msg = grant_title_to_user(group_id, target["user_id"], title_name, user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"{msg}\n👤 {name_with_badge(group_id, target['user_id'], target['name'])}"))
        return

    if text.startswith("收回稱號 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, title_name = resolve_target_and_rest(event, group_id, "收回稱號")
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target or not title_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：收回稱號 @對方 稱號名稱\n收回自己可用：收回稱號 名字 稱號名稱"))
            return
        ok, msg = revoke_title_from_user(group_id, target["user_id"], title_name, user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"{msg}\n👤 {name_with_badge(group_id, target['user_id'], target['name'])}"))
        return

    if text.startswith("稱號紀錄 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, _ = resolve_target_and_rest(event, group_id, "稱號紀錄")
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：稱號紀錄 @對方\n查看自己可用：稱號紀錄 名字"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=title_record_message(group_id, target["user_id"], target["name"])))
        return

    # ===== 商店管理：群長限定 =====
    if had_command_prefix and text == "商店管理":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_shop_management()))
        return

    if had_command_prefix and text.startswith("新增商品 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        raw = text.replace("新增商品 ", "", 1).strip()
        # 支援：名稱|價格|分類|說明，也支援：名稱 價格 分類
        if "|" in raw:
            parts = [x.strip() for x in raw.split("|")]
            if len(parts) < 2 or not parts[0]:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：新增商品 名稱|價格|分類|說明"))
                return
            name = parts[0]
            price_raw = parts[1]
            category = parts[2] if len(parts) >= 3 and parts[2] else "其他"
            description = parts[3] if len(parts) >= 4 else ""
        else:
            parts = raw.split()
            if len(parts) < 2:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：新增商品 名稱 價格 [分類]"))
                return
            # 從右側尋找第一個數字，允許商品名稱包含空格。
            price_index = next((i for i in range(len(parts)-1, -1, -1) if parts[i].isdigit()), -1)
            if price_index <= 0:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到價格，請輸入數字。"))
                return
            name = " ".join(parts[:price_index]).strip()
            price_raw = parts[price_index]
            category = parts[price_index+1] if len(parts) > price_index+1 else "其他"
            description = " ".join(parts[price_index+2:]).strip() if len(parts) > price_index+2 else ""
        if category == "活動":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 活動商品由系統依節日／季節自動配置，無法手動新增。"))
            return
        try:
            price = int(price_raw)
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 價格請輸入數字。"))
            return
        msg = add_shop_item(name, price, category, description=description)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if had_command_prefix and text.startswith("刪除商品 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        name = text.replace("刪除商品 ", "", 1).strip()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=delete_shop_item(name)))
        return

    if had_command_prefix and text.startswith("修改商品價格 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        raw = text[len("修改商品價格 "):].strip()
        if "|" in raw:
            name, price_raw = [x.strip() for x in raw.rsplit("|", 1)]
        else:
            pieces = raw.rsplit(maxsplit=1)
            if len(pieces) != 2:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：!修改商品價格 商品名稱|新價格"))
                return
            name, price_raw = pieces
        try:
            price = int(price_raw)
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 價格請輸入數字。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=update_shop_item_price(name, price)))
        return

    if had_command_prefix and text.startswith("修改商品 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有商店管理權限。"))
            return
        raw = text[len("修改商品 "):].strip()
        parts = [x.strip() for x in raw.split("|")]
        if len(parts) < 2 or not parts[0]:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：!修改商品 名稱|價格|分類|說明"))
            return
        name = parts[0]
        try:
            price = int(parts[1]) if len(parts) > 1 and parts[1] else None
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 價格請輸入數字。"))
            return
        category = parts[2] if len(parts) > 2 and parts[2] else None
        description = parts[3] if len(parts) > 3 else None
        if category == "活動":
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 活動商品由系統依節日／季節自動配置，無法手動修改。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=update_shop_item(name, price=price, category=category, description=description)))
        return

    if had_command_prefix and text.startswith("上架商品 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有商店管理權限。")); return
        name = text[len("上架商品 "):].strip()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=set_shop_item_active(name, True)))
        return

    if had_command_prefix and text.startswith("下架商品 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有商店管理權限。")); return
        name = text[len("下架商品 "):].strip()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=set_shop_item_active(name, False)))
        return

    # ===== 稱號管理：群長限定 =====
    if text == "稱號管理":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_title_management(True)))
        return

    if text == "VIP稱號管理":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=format_vip_title_management()))
        return

    if text.startswith("新增稱號 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        parts = text.split()
        if len(parts) != 3:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：新增稱號 名稱 價格"))
            return
        try:
            price = int(parts[2])
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 價格請輸入數字。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=add_title(parts[1], price, False)))
        return

    if text.startswith("新增VIP稱號 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        title_name = text.replace("新增VIP稱號 ", "", 1).strip()
        if not title_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：新增VIP稱號 名稱"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=add_title(title_name, 0, True)))
        return

    if text.startswith("刪除稱號 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        title_name = text.replace("刪除稱號 ", "", 1).strip()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=delete_title(title_name)))
        return

    if text.startswith("刪除VIP稱號 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        title_name = text.replace("刪除VIP稱號 ", "", 1).strip()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=delete_title(title_name)))
        return

    if text.startswith("修改稱號價格 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        parts = text.split()
        if len(parts) != 3:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：修改稱號價格 名稱 價格"))
            return
        try:
            price = int(parts[2])
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 價格請輸入數字。"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=update_title_price(parts[1], price)))
        return

    # ===== 群長管理 =====
    if text.startswith("給予金幣 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, amount_text = resolve_target_and_rest(event, group_id, "給予金幣")
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target or not amount_text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：給予金幣 @對方 數量\n給自己可用：給予金幣 名字 數量"))
            return
        try:
            amount = int(amount_text)
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 數量請輸入數字。"))
            return
        add_coins(group_id, target["user_id"], amount)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"👑 已給予 {name_with_badge(group_id, target['user_id'], target['name'])} {amount} 彩虹幣。"))
        return

    if text.startswith("給予經驗 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, amount_text = resolve_target_and_rest(event, group_id, "給予經驗")
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target or not amount_text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：給予經驗 @對方 數量\n給自己可用：給予經驗 名字 數量"))
            return
        try:
            amount = int(amount_text)
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 數量請輸入數字。"))
            return
        add_exp(group_id, target["user_id"], amount)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"👑 已給予 {name_with_badge(group_id, target['user_id'], target['name'])} {amount} 經驗。"))
        return

    if text.startswith("給予等級 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, level_text = resolve_target_and_rest(event, group_id, "給予等級")
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target or not level_text:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：給予等級 @對方 等級\n給自己可用：給予等級 名字 等級"))
            return
        try:
            level = int(level_text)
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 等級請輸入數字。"))
            return
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("""
                UPDATE players SET level=%s
                WHERE group_id=%s AND user_id=%s
            """, (level, group_id, target["user_id"]))
            conn.commit()
        conn.close()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"👑 已將 {name_with_badge(group_id, target['user_id'], target['name'])} 設為 Lv.{level}。"))
        return

    if text == "查看成員資料":
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("""
                SELECT user_id, name, level, exp, coins, is_vip, custom_title, streak_count, sign_month_count
                FROM players
                WHERE group_id=%s
                ORDER BY COALESCE(level, 1) DESC, COALESCE(exp, 0) DESC
                LIMIT 30
            """, (group_id,))
            rows = c.fetchall()
        conn.close()
        if not rows:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前沒有成員資料。"))
            return
        msg = "📋 成員資料\n\n"
        for r in rows:
            display_name = name_with_badge(group_id, r.get("user_id", ""), r['name']) if "user_id" in r else r['name']
            msg += (
                f"👤 {display_name}\n"
                f"Lv.{r['level']}｜EXP {r['exp']}\n"
                f"彩虹幣：{r['coins']}\n"
                f"連續簽到：{r['streak_count'] or 0} 天\n"
                f"累積簽到：{r['sign_month_count'] or 0} 天\n"
                f"VIP：{'是' if r['is_vip'] else '否'}\n"
                f"稱號：{r['custom_title'] or '無'}\n"
                "────────────\n"
            )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # ===== 簽到排行榜 =====
    if text in ["簽到排行榜", "累積簽到榜", "本月簽到榜"]:
        msg = get_sign_rank_message(group_id, user_id, mode="month")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text == "連續簽到榜":
        msg = get_sign_rank_message(group_id, user_id, mode="streak")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text in ["我的排名", "簽到排名"]:
        msg = get_my_sign_rank_message(group_id, user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # ===== V2.2.0 徽章系統 =====
    if text == "我的徽章":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=my_badges_message(group_id, user_id)))
        return

    if text in ["徽章列表", "徽章圖鑑"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=badge_catalog_message(False)))
        return

    if text.startswith("給予徽章 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, badge_name = resolve_target_and_rest(event, group_id, "給予徽章")
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target or not badge_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：給予徽章 @對方 徽章名稱\n給自己可用：給予徽章 名字 徽章名稱"))
            return
        ok, msg = grant_badge_by_name(group_id, target["user_id"], badge_name)
        if ok:
            msg += f"\n目前顯示：{name_with_badge(group_id, target['user_id'], target['name'])}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text.startswith("收回徽章 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        target, error_msg, badge_name = resolve_target_and_rest(event, group_id, "收回徽章")
        if error_msg:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return
        if not target or not badge_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：收回徽章 @對方 徽章名稱\n收回自己可用：收回徽章 名字 徽章名稱"))
            return
        ok, msg = revoke_badge_by_name(group_id, target["user_id"], badge_name)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text.startswith("新增徽章 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        parts = text.split()
        if len(parts) not in [6, 7]:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="格式：新增徽章 代號 emoji 名稱 類型 稀有度 優先權\n活動徽章可加天數：新增徽章 xmas 🎄 聖誕限定 event 活動 100 30")
            )
            return
        badge_key, emoji, badge_name, badge_type, rarity = parts[1], parts[2], parts[3], parts[4], parts[5]
        try:
            priority = int(parts[6]) if len(parts) == 7 else 0
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 優先權請輸入數字。"))
            return
        msg = add_custom_badge(badge_key, emoji, badge_name, badge_type, rarity, priority, False, 30 if badge_type == "event" else 0)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    if text.startswith("停用徽章 "):
        if not is_admin(group_id, user_id):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 你沒有群長權限。"))
            return
        badge_name = text.replace("停用徽章 ", "", 1).strip()
        if not badge_name:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="格式：停用徽章 徽章名稱"))
            return
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=deactivate_badge(badge_name)))
        return

    # ===== 個人 / 排行 =====
    if False and text in ["我的狀態", "我的資料"]:
        player = get_player(group_id, user_id) or {"name": display_name or "成員", "level": 1, "exp": 0, "coins": 0}
        status_data = get_status_sign_data(group_id, user_id)
        level_rank, sign_rank = get_player_ranks(group_id, user_id)
        safe_name = player.get("name") or display_name or "成員"
        level = int(player.get("level") or 1)
        exp = int(player.get("exp") or 0)
        level_exp = int(player.get("level_exp") or (exp % 100))
        prog = progress_info(level, level_exp, exp)
        raw_until = str(player.get("vip_until") or "")
        if int(player.get("is_vip") or 0) != 1:
            vip_text = "未啟用"
        elif raw_until.upper() in ["PERMANENT", "FOREVER", "永久", "永久VIP"]:
            vip_text = "永久 VIP ♾️"
        else:
            vip_text = "VIP 啟用中"
        vip_detail = ""
        if int(player.get("is_vip") or 0) == 1 and raw_until and raw_until.upper() not in ["PERMANENT", "FOREVER", "永久", "永久VIP"]:
            try:
                vip_date = datetime.strptime(raw_until[:10], "%Y-%m-%d").date()
                remain_days = max(0, (vip_date - taiwan_now().date()).days + 1)
                vip_detail = f"📅 到期：{vip_date.strftime('%Y/%m/%d')}｜剩餘 {remain_days} 天"
            except Exception:
                vip_detail = f"📅 到期：{raw_until}"
        data = {
            "name_line": f"👤 {name_with_badge(group_id, user_id, safe_name)}",
            "birthday": birthday_display(player.get("birthday"), player.get("birthday_year")),
            "level": level,
            "rank_title": rank_title(level),
            "level_title": get_level_title(level),
            "equipped_title": get_equipped_title(player),
            "vip_text": vip_text,
            "vip_detail": vip_detail,
            "exp_line": f"{prog['current']:,} / {prog['needed']:,} EXP｜累積 {exp:,}",
            "exp_bar": f"{prog['bar']} {prog['percent']}%",
            "exp_need": f"距離 Lv.{level+1}：{prog['remaining']:,} EXP",
            "coins": int(player.get("coins") or 0),
            "streak": status_data["streak_count"],
            "total_sign": status_data["total_signs"],
            "today_msg": status_data["today_msg_count"],
            "today_sticker": status_data["today_sticker_count"],
            "level_rank": level_rank,
            "sign_rank": sign_rank,
            "next_sign": status_next_sign_text(status_data["signed_today"]),
        }
        line_bot_api.reply_message(event.reply_token, status_card_flex(data))
        return

    if text == "金幣排行榜":
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("""
                SELECT user_id, name, coins
                FROM players
                WHERE group_id=%s
                ORDER BY coins DESC
                LIMIT 10
            """, (group_id,))
            rows = c.fetchall()
        conn.close()
        card_rows = [{"name": name_with_badge(group_id, r["user_id"], r["name"]), "score": int(r.get("coins") or 0)} for r in rows]
        line_bot_api.reply_message(event.reply_token, ranking_result_flex("🌈 彩虹幣排行榜", card_rows, " 枚"))
        return

    if text == "排行榜資料":
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("""
                SELECT user_id, name, COALESCE(level, 1) AS level, COALESCE(exp, 0) AS exp
                FROM players
                WHERE group_id=%s
                ORDER BY COALESCE(level, 1) DESC, COALESCE(exp, 0) DESC
                LIMIT 10
            """, (group_id,))
            rows = c.fetchall()
        conn.close()
        card_rows = []
        for r in rows:
            card_rows.append({
                "name": name_with_badge(group_id, r["user_id"], r["name"]),
                "score": f"Lv.{int(r.get('level') or 1)}",
                "detail": f"累積 EXP：{int(r.get('exp') or 0):,}"
            })
        line_bot_api.reply_message(event.reply_token, ranking_result_flex("🏅 等級排行榜", card_rows))
        return

    # 使用 !／！ 開頭但沒有命中任何功能時，提醒指令錯誤；一般聊天不受影響。
    if had_command_prefix:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text=f"❓ 找不到指令：{raw_text}\n\n請確認指令名稱與格式。\n輸入 !指令 可查看全部功能。"
            )
        )
        return

    # 普通聊天：+EXP；狂歡 EXP ×2；寶箱雨掉落率 15%
    mult = carnival_multiplier(group_id)
    fortune_exp_mult, fortune_coin_mult = get_fortune_multipliers(player)
    chat_exp_gain = max(0, round(CHAT_EXP * mult * fortune_exp_mult))
    chest_rate = get_chest_rate(group_id)

    exp_result = add_exp(group_id, user_id, chat_exp_gain, source="chat")
    update_task_progress(group_id, user_id, "chat", 1)

    chest_dropped = random.random() < chest_rate
    if chest_dropped:
        add_coins(group_id, user_id, max(0, round(CHAT_CHEST_COIN * fortune_coin_mult)))
        update_task_progress(group_id, user_id, "chest", 1)
        activity_gain_msg = format_activity_item_gain(grant_active_event_items(group_id, user_id, "chest"))
        notes = []
        if mult > 1:
            notes.append("🌈 狂歡加成中")
        if is_chest_rain_active(group_id):
            notes.append("🎁 寶箱雨模式中")
        bonus_note = "\n" + "｜".join(notes) if notes else ""
        messages = [TextSendMessage(text=f"🎁 掉落黃金寶箱！+{CHAT_CHEST_COIN} 彩虹幣{bonus_note}{activity_gain_msg}")]
        if exp_result.get("leveled_up"):
            messages.append(level_up_flex(display_name or "成員", exp_result))
        line_bot_api.reply_message(event.reply_token, messages)
    elif exp_result.get("leveled_up"):
        line_bot_api.reply_message(event.reply_token, level_up_flex(display_name or "成員", exp_result))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
