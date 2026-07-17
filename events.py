"""
Rainbow Bot V2.4 Step 1 - 活動核心定義模組

這一版只負責：
1. 內建年度活動與四季活動資料
2. 判斷今天有哪些活動啟用
3. 回傳活動資訊文字

尚未接入 main.py；下一步才會把指令「活動資訊 / 活動管理」接上。
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

TW_TZ = datetime.timezone(datetime.timedelta(hours=8))


@dataclass(frozen=True)
class ActivityTemplate:
    key: str
    name: str
    emoji: str
    item_name: str
    item_emoji: str
    title_reward: str
    badge_name: str
    badge_emoji: str
    description: str
    start_month: Optional[int] = None
    start_day: Optional[int] = None
    end_month: Optional[int] = None
    end_day: Optional[int] = None
    is_lunar: bool = False
    lunar_key: Optional[str] = None
    is_season: bool = False


# 固定國曆活動與四季活動
ACTIVITY_TEMPLATES: Dict[str, ActivityTemplate] = {
    "new_year": ActivityTemplate(
        key="new_year",
        name="新年活動",
        emoji="🧧",
        item_name="紅包",
        item_emoji="🧧",
        title_reward="新春福星",
        badge_name="新年徽章",
        badge_emoji="🧧",
        description="新年快樂！完成活動任務，迎接好運一整年。",
        start_month=1,
        start_day=1,
        end_month=1,
        end_day=7,
    ),
    "pride_month": ActivityTemplate(
        key="pride_month",
        name="彩虹月",
        emoji="🌈",
        item_name="彩虹星",
        item_emoji="🌈",
        title_reward="彩虹旅人",
        badge_name="彩虹月徽章",
        badge_emoji="🌈",
        description="彩虹月活動開始！一起收集彩虹星，解鎖限定獎勵。",
        start_month=6,
        start_day=1,
        end_month=6,
        end_day=30,
    ),
    "double_ten": ActivityTemplate(
        key="double_ten",
        name="雙十活動",
        emoji="🇹🇼",
        item_name="國慶紀念章",
        item_emoji="🇹🇼",
        title_reward="榮耀先鋒",
        badge_name="國慶徽章",
        badge_emoji="🇹🇼",
        description="雙十活動開始！完成任務獲得限定紀念獎勵。",
        start_month=10,
        start_day=10,
        end_month=10,
        end_day=12,
    ),
    "halloween": ActivityTemplate(
        key="halloween",
        name="萬聖節活動",
        emoji="🎃",
        item_name="南瓜燈",
        item_emoji="🎃",
        title_reward="萬聖旅人",
        badge_name="萬聖徽章",
        badge_emoji="🎃",
        description="萬聖節活動開始！收集南瓜燈兌換限定獎勵。",
        start_month=10,
        start_day=25,
        end_month=10,
        end_day=31,
    ),
    "christmas": ActivityTemplate(
        key="christmas",
        name="聖誕活動",
        emoji="🎄",
        item_name="聖誕禮物",
        item_emoji="🎁",
        title_reward="聖誕使者",
        badge_name="聖誕限定徽章",
        badge_emoji="🎄",
        description="聖誕活動開始！完成任務收集聖誕禮物。",
        start_month=12,
        start_day=20,
        end_month=12,
        end_day=26,
    ),
    "countdown": ActivityTemplate(
        key="countdown",
        name="跨年活動",
        emoji="🎆",
        item_name="煙火",
        item_emoji="🎆",
        title_reward="星光倒數者",
        badge_name="跨年徽章",
        badge_emoji="🎆",
        description="跨年活動開始！一起收集煙火迎接新的一年。",
        start_month=12,
        start_day=31,
        end_month=1,
        end_day=1,
    ),
    "spring": ActivityTemplate(
        key="spring",
        name="春季活動",
        emoji="🌸",
        item_name="櫻花",
        item_emoji="🌸",
        title_reward="春日守護者",
        badge_name="春櫻徽章",
        badge_emoji="🌸",
        description="春暖花開，收集櫻花解鎖春季限定獎勵。",
        start_month=3,
        start_day=1,
        end_month=5,
        end_day=31,
        is_season=True,
    ),
    "summer": ActivityTemplate(
        key="summer",
        name="夏季活動",
        emoji="☀️",
        item_name="西瓜",
        item_emoji="🍉",
        title_reward="盛夏旅者",
        badge_name="夏日徽章",
        badge_emoji="☀️",
        description="夏日狂歡，收集西瓜解鎖夏季限定獎勵。",
        start_month=6,
        start_day=1,
        end_month=8,
        end_day=31,
        is_season=True,
    ),
    "autumn": ActivityTemplate(
        key="autumn",
        name="秋季活動",
        emoji="🍁",
        item_name="楓葉",
        item_emoji="🍁",
        title_reward="秋楓旅者",
        badge_name="秋楓徽章",
        badge_emoji="🍁",
        description="秋日豐收，收集楓葉解鎖秋季限定獎勵。",
        start_month=9,
        start_day=1,
        end_month=11,
        end_day=30,
        is_season=True,
    ),
    "winter": ActivityTemplate(
        key="winter",
        name="冬季活動",
        emoji="❄️",
        item_name="雪花",
        item_emoji="❄️",
        title_reward="冬雪守護者",
        badge_name="冰雪徽章",
        badge_emoji="❄️",
        description="冬日慶典，收集雪花解鎖冬季限定獎勵。",
        start_month=12,
        start_day=1,
        end_month=2,
        end_day=29,
        is_season=True,
    ),
}


# 農曆節日換算表（先支援 2026～2035；之後可改接 lunardate 套件）
# 日期格式：YYYY-MM-DD
LUNAR_EVENT_DATES: Dict[int, Dict[str, Tuple[str, str]]] = {
    2026: {
        "lunar_new_year": ("2026-02-16", "2026-02-23"),
        "lantern": ("2026-03-03", "2026-03-03"),
        "dragon_boat": ("2026-06-19", "2026-06-21"),
        "mid_autumn": ("2026-09-25", "2026-09-27"),
    },
    2027: {
        "lunar_new_year": ("2027-02-05", "2027-02-12"),
        "lantern": ("2027-02-19", "2027-02-19"),
        "dragon_boat": ("2027-06-09", "2027-06-11"),
        "mid_autumn": ("2027-09-15", "2027-09-17"),
    },
    2028: {
        "lunar_new_year": ("2028-01-26", "2028-02-02"),
        "lantern": ("2028-02-09", "2028-02-09"),
        "dragon_boat": ("2028-05-28", "2028-05-30"),
        "mid_autumn": ("2028-10-03", "2028-10-05"),
    },
    2029: {
        "lunar_new_year": ("2029-02-13", "2029-02-20"),
        "lantern": ("2029-02-27", "2029-02-27"),
        "dragon_boat": ("2029-06-16", "2029-06-18"),
        "mid_autumn": ("2029-09-22", "2029-09-24"),
    },
    2030: {
        "lunar_new_year": ("2030-02-03", "2030-02-10"),
        "lantern": ("2030-02-17", "2030-02-17"),
        "dragon_boat": ("2030-06-05", "2030-06-07"),
        "mid_autumn": ("2030-09-12", "2030-09-14"),
    },
    2031: {
        "lunar_new_year": ("2031-01-23", "2031-01-30"),
        "lantern": ("2031-02-06", "2031-02-06"),
        "dragon_boat": ("2031-06-24", "2031-06-26"),
        "mid_autumn": ("2031-10-01", "2031-10-03"),
    },
    2032: {
        "lunar_new_year": ("2032-02-11", "2032-02-18"),
        "lantern": ("2032-02-25", "2032-02-25"),
        "dragon_boat": ("2032-06-12", "2032-06-14"),
        "mid_autumn": ("2032-09-19", "2032-09-21"),
    },
    2033: {
        "lunar_new_year": ("2033-01-31", "2033-02-07"),
        "lantern": ("2033-02-14", "2033-02-14"),
        "dragon_boat": ("2033-06-01", "2033-06-03"),
        "mid_autumn": ("2033-09-08", "2033-09-10"),
    },
    2034: {
        "lunar_new_year": ("2034-02-19", "2034-02-26"),
        "lantern": ("2034-03-05", "2034-03-05"),
        "dragon_boat": ("2034-06-20", "2034-06-22"),
        "mid_autumn": ("2034-09-27", "2034-09-29"),
    },
    2035: {
        "lunar_new_year": ("2035-02-08", "2035-02-15"),
        "lantern": ("2035-02-22", "2035-02-22"),
        "dragon_boat": ("2035-06-10", "2035-06-12"),
        "mid_autumn": ("2035-09-17", "2035-09-19"),
    },
}

LUNAR_TEMPLATES: Dict[str, ActivityTemplate] = {
    "lunar_new_year": ActivityTemplate(
        key="lunar_new_year",
        name="春節活動",
        emoji="🧧",
        item_name="紅包",
        item_emoji="🧧",
        title_reward="新春福星",
        badge_name="春節徽章",
        badge_emoji="🧧",
        description="春節活動開始！收集紅包迎接新年好運。",
        is_lunar=True,
        lunar_key="lunar_new_year",
    ),
    "lantern": ActivityTemplate(
        key="lantern",
        name="元宵節活動",
        emoji="🏮",
        item_name="燈籠",
        item_emoji="🏮",
        title_reward="燈火旅人",
        badge_name="元宵徽章",
        badge_emoji="🏮",
        description="元宵節活動開始！收集燈籠兌換限定獎勵。",
        is_lunar=True,
        lunar_key="lantern",
    ),
    "dragon_boat": ActivityTemplate(
        key="dragon_boat",
        name="端午活動",
        emoji="🐲",
        item_name="粽子",
        item_emoji="🎋",
        title_reward="龍舟勇者",
        badge_name="端午徽章",
        badge_emoji="🐲",
        description="端午活動開始！收集粽子兌換限定獎勵。",
        is_lunar=True,
        lunar_key="dragon_boat",
    ),
    "mid_autumn": ActivityTemplate(
        key="mid_autumn",
        name="中秋活動",
        emoji="🌕",
        item_name="月餅",
        item_emoji="🥮",
        title_reward="月光旅人",
        badge_name="中秋徽章",
        badge_emoji="🌕",
        description="中秋活動開始！收集月餅兌換限定獎勵。",
        is_lunar=True,
        lunar_key="mid_autumn",
    ),
}


def today_tw() -> datetime.date:
    return datetime.datetime.now(TW_TZ).date()


def parse_date(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value)


def fixed_period_for_year(template: ActivityTemplate, year: int) -> Tuple[datetime.date, datetime.date]:
    if template.start_month is None or template.start_day is None or template.end_month is None or template.end_day is None:
        raise ValueError("template does not have fixed dates")

    start = datetime.date(year, template.start_month, template.start_day)

    # 跨年活動或冬季活動：結束月份小於開始月份，代表跨到隔年
    end_year = year + 1 if template.end_month < template.start_month else year

    # 冬季遇到非閏年 2/29，要自動改成 2/28
    end_day = template.end_day
    try:
        end = datetime.date(end_year, template.end_month, end_day)
    except ValueError:
        end = datetime.date(end_year, template.end_month, 28)

    return start, end


def is_date_in_period(day: datetime.date, start: datetime.date, end: datetime.date) -> bool:
    return start <= day <= end


def active_activities(day: Optional[datetime.date] = None) -> List[Tuple[ActivityTemplate, datetime.date, datetime.date]]:
    day = day or today_tw()
    result: List[Tuple[ActivityTemplate, datetime.date, datetime.date]] = []

    # 固定日期與四季活動
    for template in ACTIVITY_TEMPLATES.values():
        start, end = fixed_period_for_year(template, day.year)

        # 冬季、跨年：如果今天在 1~2 月，需要用前一年起算的冬季/跨年
        if template.end_month is not None and template.start_month is not None and template.end_month < template.start_month:
            prev_start, prev_end = fixed_period_for_year(template, day.year - 1)
            if is_date_in_period(day, prev_start, prev_end):
                result.append((template, prev_start, prev_end))
                continue

        if is_date_in_period(day, start, end):
            result.append((template, start, end))

    # 農曆活動
    lunar_periods = LUNAR_EVENT_DATES.get(day.year, {})
    for key, period in lunar_periods.items():
        template = LUNAR_TEMPLATES[key]
        start = parse_date(period[0])
        end = parse_date(period[1])
        if is_date_in_period(day, start, end):
            result.append((template, start, end))

    return result


def format_activity_info(day: Optional[datetime.date] = None) -> str:
    day = day or today_tw()
    active = active_activities(day)
    if not active:
        return (
            "🎉 活動資訊\n\n"
            "目前沒有進行中的活動。\n\n"
            "可等待年度節日、四季活動或群長手動活動開啟。"
        )

    msg = "🎉 目前活動資訊\n\n"
    for template, start, end in active:
        msg += (
            f"{template.emoji} {template.name}\n"
            f"📅 期間：{start.isoformat()} ～ {end.isoformat()}\n"
            f"🎊 活動道具：{template.item_emoji} {template.item_name}\n"
            f"🏷️ 稱號：{template.title_reward}\n"
            f"🏅 徽章：{template.badge_emoji} {template.badge_name}\n"
            f"📝 {template.description}\n"
            "────────────\n"
        )
    return msg.rstrip("─\n")


def format_activity_task_template(template: ActivityTemplate, is_vip: bool = False) -> str:
    item = f"{template.item_emoji} {template.item_name}"
    msg = (
        f"📋 {template.name} 任務\n\n"
        "🌞 每日任務\n"
        "• 📅 每日簽到（0/1）\n"
        f"  🎁 獎勵：{template.item_emoji}×5｜⭐EXP×100｜🌈彩虹幣×100\n"
        "• 🎁 開寶箱 3 次（0/3）\n"
        f"  🎁 獎勵：{template.item_emoji}×10｜⭐EXP×150\n"
        "• 🎰 輪盤 1 次（0/1）\n"
        f"  🎁 獎勵：{template.item_emoji}×5｜🌈彩虹幣×100\n"
        "• ⭐ 獲得指定 EXP（0/500）\n"
        f"  🎁 獎勵：{template.item_emoji}×5｜⭐EXP×100\n"
        f"• 🎊 收集 {item} 20 個（0/20）\n"
        "  🎁 獎勵：⭐EXP×300｜🌈彩虹幣×200\n\n"
        "📅 每週任務\n"
        "• 📅 簽到 5 天（0/5）\n"
        f"  🎁 獎勵：{template.item_emoji}×30｜⭐EXP×500\n"
        "• 🎁 開寶箱 20 次（0/20）\n"
        f"  🎁 獎勵：{template.item_emoji}×50｜🌈彩虹幣×500\n"
        "• 🎰 輪盤 7 次（0/7）\n"
        f"  🎁 獎勵：{template.item_emoji}×40｜⭐EXP×800\n"
        f"• 🎊 收集 {item} 150 個（0/150）\n"
        f"  🎁 獎勵：{template.item_emoji}×50｜🌈彩虹幣×800\n\n"
        "🗓️ 活動總任務\n"
        f"• 🎊 收集 {item} 1000 個（0/1000）\n"
        "• 📅 完成活動簽到（0/20）\n"
        "• 🎁 開寶箱 150 次（0/150）\n"
        "• 🎰 輪盤 80 次（0/80）\n\n"
        "🎁 完成全部活動任務可獲得：\n"
        f"🏷️ {template.title_reward}\n"
        f"🏅 {template.badge_emoji} {template.badge_name}\n"
        "🌈 彩虹幣 ×3000\n"
        "⭐ EXP ×5000"
    )
    if is_vip:
        msg += (
            "\n\n━━━━━━━━━━━━━━\n"
            "💎 VIP 專屬活動任務\n\n"
            "💎 VIP 每日任務\n"
            f"• VIP每日簽到（0/1）\n  🎁 獎勵：{template.item_emoji}×10｜🌈彩虹幣×300｜⭐EXP×300\n"
            f"• 開寶箱 5 次（0/5）\n  🎁 獎勵：{template.item_emoji}×20｜⭐EXP×500\n"
            f"• 輪盤 1 次（0/1）\n  🎁 獎勵：{template.item_emoji}×10｜🌈彩虹幣×300\n"
            f"• VIP禮包 1 次（0/1）\n  🎁 獎勵：{template.item_emoji}×15｜🌈彩虹幣×500｜⭐EXP×500\n\n"
            "💎 VIP 每週任務\n"
            f"• VIP簽到 7 天（0/7）\n  🎁 獎勵：{template.item_emoji}×80｜⭐EXP×1500\n"
            f"• 開寶箱 35 次（0/35）\n  🎁 獎勵：{template.item_emoji}×120｜🌈彩虹幣×1200\n"
            f"• 輪盤 7 次（0/7）\n  🎁 獎勵：{template.item_emoji}×80｜⭐EXP×2000\n"
            f"• 開啟 VIP 禮包 7 次（0/7）\n  🎁 獎勵：{template.item_emoji}×100｜🌈彩虹幣×2000\n\n"
            "🏆 VIP 活動總任務\n"
            f"• 收集 {item} 2000 個（0/2000）\n"
            "• 完成全部 VIP 每日任務\n"
            "• 完成全部 VIP 每週任務\n\n"
            "🎁 VIP 完成獎勵：\n"
            f"🏅 💎{template.badge_emoji} {template.name}至尊徽章\n"
            f"🏷️ 💎{template.title_reward.replace('旅者','至尊')}\n"
            "🌈 彩虹幣 ×5000\n"
            "⭐ EXP ×10000"
        )
    return msg


def format_current_activity_tasks(day: Optional[datetime.date] = None, is_vip: bool = False) -> str:
    active = active_activities(day)
    if not active:
        return "📋 活動任務\n\n目前沒有進行中的活動。"
    return "\n\n━━━━━━━━━━━━━━\n\n".join(format_activity_task_template(t, is_vip=is_vip) for t, _, _ in active)


# ===== V2.4 Step 3：活動道具資料表與玩家查詢 =====
def ensure_event_tables():
    """建立活動道具資料表。"""
    from database import get_connection

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS event_items (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                item_name TEXT NOT NULL,
                item_emoji TEXT DEFAULT '',
                quantity INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_id, user_id, event_key, item_name)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS event_shop_items (
                id SERIAL PRIMARY KEY,
                event_key TEXT NOT NULL,
                item_name TEXT NOT NULL,
                cost INTEGER NOT NULL DEFAULT 0,
                reward_type TEXT NOT NULL,
                reward_value TEXT NOT NULL,
                reward_amount INTEGER NOT NULL DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                UNIQUE(event_key, item_name)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS event_exchange_logs (
                id SERIAL PRIMARY KEY,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                shop_item_name TEXT NOT NULL,
                cost INTEGER NOT NULL DEFAULT 0,
                reward_type TEXT NOT NULL,
                reward_value TEXT NOT NULL,
                reward_amount INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 既有資料庫升級保護：舊版表已存在時自動補欄位
        c.execute("ALTER TABLE event_shop_items ADD COLUMN IF NOT EXISTS purchase_limit INTEGER DEFAULT 0")
        c.execute("ALTER TABLE event_shop_items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        c.execute("ALTER TABLE event_exchange_logs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        conn.commit()
    conn.close()


def add_event_item(group_id: str, user_id: str, event_key: str, amount: int):
    """增加玩家活動道具。amount 可以為正或負。"""
    from database import get_connection

    ensure_event_tables()

    template = ACTIVITY_TEMPLATES.get(event_key) or LUNAR_TEMPLATES.get(event_key)
    if not template:
        return False, "❌ 找不到活動。"

    amount = int(amount)
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO event_items (
                group_id, user_id, event_key, item_name, item_emoji, quantity
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (group_id, user_id, event_key, item_name)
            DO UPDATE SET
                quantity = GREATEST(event_items.quantity + EXCLUDED.quantity, 0),
                item_emoji = EXCLUDED.item_emoji,
                updated_at = CURRENT_TIMESTAMP
        """, (
            group_id,
            user_id,
            event_key,
            template.item_name,
            template.item_emoji,
            amount,
        ))
        conn.commit()
    conn.close()
    return True, f"{template.item_emoji} {template.item_name} +{amount}"


def get_user_event_items(group_id: str, user_id: str):
    from database import get_connection

    ensure_event_tables()
    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT event_key, item_name, item_emoji, quantity
            FROM event_items
            WHERE group_id=%s AND user_id=%s
            ORDER BY updated_at DESC
        """, (group_id, user_id))
        rows = c.fetchall()
    conn.close()
    return rows


def get_user_event_item_quantity(group_id: str, user_id: str, event_key: str) -> int:
    from database import get_connection

    ensure_event_tables()
    template = ACTIVITY_TEMPLATES.get(event_key) or LUNAR_TEMPLATES.get(event_key)
    if not template:
        return 0

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT quantity
            FROM event_items
            WHERE group_id=%s AND user_id=%s AND event_key=%s AND item_name=%s
        """, (group_id, user_id, event_key, template.item_name))
        row = c.fetchone()
    conn.close()
    return int(row["quantity"] if row else 0)


def format_event_bag(group_id: str, user_id: str) -> str:
    rows = get_user_event_items(group_id, user_id)
    if not rows:
        return (
            "🎒 活動背包\n\n"
            "目前沒有任何活動道具。\n\n"
            "活動期間可透過簽到、寶箱、輪盤與任務取得活動道具。"
        )

    msg = "🎒 活動背包\n\n"
    for r in rows:
        qty = r["quantity"] or 0
        if qty <= 0:
            continue
        msg += f"{r['item_emoji']} {r['item_name']}：{qty}\n"

    if msg.strip() == "🎒 活動背包":
        msg += "目前沒有任何活動道具。"

    return msg.rstrip()


def format_my_activity(group_id: str, user_id: str, day: Optional[datetime.date] = None) -> str:
    day = day or today_tw()
    active = active_activities(day)

    if not active:
        return (
            "🎊 我的活動\n\n"
            "目前沒有進行中的活動。\n\n"
            "可輸入【活動背包】查看你收藏的活動道具。"
        )

    msg = "🎊 我的活動\n\n"
    for template, start, end in active:
        qty = get_user_event_item_quantity(group_id, user_id, template.key)
        msg += (
            f"{template.emoji} {template.name}\n"
            f"📅 期間：{start.isoformat()} ～ {end.isoformat()}\n"
            f"🎊 {template.item_emoji} {template.item_name}：{qty} 個\n"
            f"🎯 總目標：1000 個\n"
        )
        if qty >= 1000:
            msg += "✅ 活動道具收集已達標\n"
        else:
            msg += f"📌 距離總目標還差：{1000 - qty} 個\n"
        msg += "────────────\n"

    return msg.rstrip("─\n")


def format_specific_activity_item(group_id: str, user_id: str, item_name: str) -> str:
    # 支援「我的月餅」「我的粽子」「我的櫻花」等自然查詢
    item_name = item_name.strip()
    templates = list(ACTIVITY_TEMPLATES.values()) + list(LUNAR_TEMPLATES.values())

    matched = None
    for t in templates:
        if item_name == t.item_name or item_name in [f"{t.item_emoji}{t.item_name}", f"{t.item_emoji} {t.item_name}"]:
            matched = t
            break

    if not matched:
        return "❌ 找不到這個活動道具。"

    qty = get_user_event_item_quantity(group_id, user_id, matched.key)

    next_goal = 1000
    if qty < 20:
        next_goal = 20
    elif qty < 150:
        next_goal = 150
    elif qty < 1000:
        next_goal = 1000

    msg = (
        f"{matched.item_emoji} 我的{matched.item_name}\n\n"
        f"目前擁有：{qty} 個\n"
    )

    if qty >= 1000:
        msg += "\n✅ 已達成活動總收集目標。"
    else:
        msg += f"\n下一個目標：{next_goal} 個\n還差：{next_goal - qty} 個"

    return msg


def format_activity_rank(group_id: str, day: Optional[datetime.date] = None) -> str:
    from database import get_connection

    ensure_event_tables()
    active = active_activities(day)
    if not active:
        return "🏆 活動排行\n\n目前沒有進行中的活動。"

    # 若同時多個活動，先顯示第一個活動排行，之後可擴充指定活動排行
    template = active[0][0]

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT p.name, e.quantity
            FROM event_items e
            JOIN players p
              ON p.group_id=e.group_id
             AND p.user_id=e.user_id
            WHERE e.group_id=%s
              AND e.event_key=%s
              AND e.item_name=%s
              AND e.quantity > 0
            ORDER BY e.quantity DESC, p.name ASC
            LIMIT 10
        """, (group_id, template.key, template.item_name))
        rows = c.fetchall()
    conn.close()

    msg = f"🏆 {template.name}排行\n\n"
    if not rows:
        msg += f"目前還沒有人取得 {template.item_emoji} {template.item_name}。"
        return msg

    for i, r in enumerate(rows, start=1):
        if i == 1:
            prefix = "🥇"
        elif i == 2:
            prefix = "🥈"
        elif i == 3:
            prefix = "🥉"
        else:
            prefix = f"{i}."
        msg += f"{prefix} {r['name']}｜{template.item_emoji} {r['quantity']} 個\n"

    return msg.rstrip()


# ===== V2.4 Step 4：活動道具取得來源 =====
EVENT_ITEM_REWARD_BY_SOURCE = {
    # 活動系統不使用聊天掉落，避免洗版。
    "sign": 10,          # 每日簽到
    "chest": 3,          # 寶箱掉落 / 開寶箱
    "wheel": 5,          # 幸運輪盤
    "task": 20,          # 一般任務完成獎勵
    "daily_task": 20,
    "weekly_task": 50,
    "monthly_task": 100,
    "quarterly_task": 200,
    "admin": 0,
}


def grant_active_event_items(group_id: str, user_id: str, source: str, amount: Optional[int] = None, day: Optional[datetime.date] = None):
    """依照目前進行中的活動，發放對應活動道具。

    回傳 list[dict]，每筆包含活動名稱、道具名稱、emoji、數量。
    不會主動推播，讓 main.py 決定是否把訊息附加到原本指令回覆中。
    """
    active = active_activities(day)
    if not active:
        return []

    reward_amount = int(amount if amount is not None else EVENT_ITEM_REWARD_BY_SOURCE.get(source, 0))
    if reward_amount <= 0:
        return []

    rewards = []
    for template, _, _ in active:
        ok, _ = add_event_item(group_id, user_id, template.key, reward_amount)
        if ok:
            rewards.append({
                "event_key": template.key,
                "event_name": template.name,
                "item_name": template.item_name,
                "item_emoji": template.item_emoji,
                "amount": reward_amount,
            })
    return rewards


def format_activity_item_gain(rewards) -> str:
    """把活動道具獲得結果格式化成可附加在回覆後面的文字。"""
    if not rewards:
        return ""
    lines = ["", "🎊 活動道具獲得"]
    for r in rewards:
        lines.append(f"{r['item_emoji']} {r['item_name']} +{r['amount']}｜{r['event_name']}")
    return "\n".join(lines)


def grant_event_item_by_name(group_id: str, user_id: str, item_name: str, amount: int):
    """依活動道具名稱直接發放，供群長測試或活動獎勵使用。"""
    item_name = str(item_name).strip()
    for template in list(ACTIVITY_TEMPLATES.values()) + list(LUNAR_TEMPLATES.values()):
        if item_name in [template.item_name, f"{template.item_emoji}{template.item_name}", f"{template.item_emoji} {template.item_name}"]:
            return add_event_item(group_id, user_id, template.key, int(amount))
    return False, "❌ 找不到這個活動道具。"


# ===== V2.4 Step 5：活動商店與兌換 =====
def _all_activity_templates():
    return list(ACTIVITY_TEMPLATES.values()) + list(LUNAR_TEMPLATES.values())


def seed_default_event_shop_items():
    """同步系統內建活動商品。

    活動商店完全由節日／季節活動模板控制，不接受後台新增、修改、上下架或刪除。
    每次同步都會移除舊版手動建立的活動商品，並把內建商品恢復為固定內容。
    """
    from database import get_connection

    ensure_event_tables()
    conn = get_connection()
    with conn.cursor() as c:
        for t in _all_activity_templates():
            defaults = [
                (t.key, f"{t.item_name}彩虹幣包", 100, "coins", "彩虹幣", 500),
                (t.key, f"{t.item_name}EXP包", 300, "exp", "EXP", 1000),
                (t.key, t.title_reward, 500, "title", t.title_reward, 1),
                (t.key, t.badge_name, 1000, "badge", t.badge_name, 1),
            ]
            builtin_names = [row[1] for row in defaults]
            c.execute(
                "DELETE FROM event_shop_items WHERE event_key=%s AND NOT (item_name = ANY(%s))",
                (t.key, builtin_names),
            )
            for row in defaults:
                c.execute("""
                    INSERT INTO event_shop_items(
                        event_key, item_name, cost, reward_type, reward_value, reward_amount, is_active, purchase_limit
                    )
                    VALUES(%s,%s,%s,%s,%s,%s,TRUE,0)
                    ON CONFLICT(event_key, item_name) DO UPDATE SET
                        cost=EXCLUDED.cost,
                        reward_type=EXCLUDED.reward_type,
                        reward_value=EXCLUDED.reward_value,
                        reward_amount=EXCLUDED.reward_amount,
                        purchase_limit=0,
                        is_active=TRUE,
                        updated_at=CURRENT_TIMESTAMP
                """, row)
        conn.commit()
    conn.close()


def _template_by_event_key(event_key: str):
    return ACTIVITY_TEMPLATES.get(event_key) or LUNAR_TEMPLATES.get(event_key)


def format_event_shop(group_id: str, user_id: str, day: Optional[datetime.date] = None) -> str:
    """顯示目前第一個進行中活動的活動商店。"""
    from database import get_connection

    ensure_event_tables()
    seed_default_event_shop_items()
    active = active_activities(day)
    if not active:
        return "🛍️ 活動商店\n\n目前沒有進行中的活動。"

    template = active[0][0]
    qty = get_user_event_item_quantity(group_id, user_id, template.key)

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT item_name, cost, reward_type, reward_value, reward_amount, COALESCE(purchase_limit, 0) AS purchase_limit
            FROM event_shop_items
            WHERE event_key=%s AND is_active=TRUE
            ORDER BY cost ASC, id ASC
        """, (template.key,))
        rows = c.fetchall()
    conn.close()

    msg = (
        f"🛍️ {template.name}商店\n\n"
        f"你目前擁有：{template.item_emoji} {template.item_name} ×{qty}\n\n"
    )
    if not rows:
        msg += "目前沒有可兌換商品。"
        return msg

    for r in rows:
        reward = r["reward_value"]
        if r["reward_type"] in ["coins", "exp"]:
            reward = f"{reward} ×{r['reward_amount']}"
        msg += f"・{r['item_name']}｜需要 {template.item_emoji}{r['cost']}｜獎勵：{reward}\n"

    msg += "\n輸入：兌換 商品名稱\n例：兌換 月光旅人"
    return msg.rstrip()


def _find_active_shop_item(shop_item_name: str, day: Optional[datetime.date] = None):
    """依目前進行中活動尋找商店商品。"""
    from database import get_connection

    active = active_activities(day)
    if not active:
        return None, None, "目前沒有進行中的活動。"

    name = str(shop_item_name or "").strip()
    conn = get_connection()
    with conn.cursor() as c:
        # 先精準找，再模糊找。若多個活動同時進行，依 active_activities 的順序為主。
        for template, _, _ in active:
            c.execute("""
                SELECT *
                FROM event_shop_items
                WHERE event_key=%s AND is_active=TRUE AND item_name=%s
                LIMIT 1
            """, (template.key, name))
            row = c.fetchone()
            if row:
                conn.close()
                return template, row, None
        for template, _, _ in active:
            c.execute("""
                SELECT *
                FROM event_shop_items
                WHERE event_key=%s AND is_active=TRUE AND item_name LIKE %s
                ORDER BY cost ASC
                LIMIT 1
            """, (template.key, f"%{name}%"))
            row = c.fetchone()
            if row:
                conn.close()
                return template, row, None
    conn.close()
    return None, None, f"找不到活動商品：{name}"


def exchange_event_shop_item(group_id: str, user_id: str, shop_item_name: str, actor_user_id: str = ""):
    """兌換目前活動商店商品並自動發獎。"""
    from database import get_connection
    from game import add_coins, add_exp

    ensure_event_tables()
    seed_default_event_shop_items()

    template, item, error = _find_active_shop_item(shop_item_name)
    if error:
        return False, f"❌ {error}"

    cost = int(item["cost"] or 0)
    purchase_limit = int(item.get("purchase_limit") or 0)
    if purchase_limit > 0:
        conn = get_connection()
        with conn.cursor() as c:
            c.execute("""
                SELECT COUNT(*) AS cnt
                FROM event_exchange_logs
                WHERE group_id=%s AND user_id=%s AND event_key=%s AND shop_item_name=%s
            """, (group_id, user_id, template.key, item["item_name"]))
            used_row = c.fetchone()
        conn.close()
        used = int((used_row or {}).get("cnt") or 0)
        if used >= purchase_limit:
            return False, f"❌ 這個商品已達限購次數：{purchase_limit} 次。"

    current_qty = get_user_event_item_quantity(group_id, user_id, template.key)
    if current_qty < cost:
        return False, (
            f"❌ {template.item_emoji} {template.item_name} 不足。\n"
            f"需要：{cost}\n目前：{current_qty}"
        )

    ok, _ = add_event_item(group_id, user_id, template.key, -cost)
    if not ok:
        return False, "❌ 扣除活動道具失敗。"

    reward_type = item["reward_type"]
    reward_value = item["reward_value"]
    reward_amount = int(item["reward_amount"] or 0)

    reward_msg = ""
    if reward_type == "coins":
        add_coins(group_id, user_id, reward_amount)
        reward_msg = f"🌈 彩虹幣 +{reward_amount}"
    elif reward_type == "exp":
        add_exp(group_id, user_id, reward_amount)
        reward_msg = f"⭐ EXP +{reward_amount}"
    elif reward_type == "title":
        try:
            from shop import grant_title_to_user
            ok_title, msg_title = grant_title_to_user(group_id, user_id, reward_value, actor_user_id or "SYSTEM", source="活動商店")
            reward_msg = msg_title if ok_title else msg_title
        except Exception:
            reward_msg = f"🏷️ 活動稱號：{reward_value}"
    elif reward_type == "badge":
        try:
            from badges import add_custom_badge, grant_badge_by_name
            add_custom_badge(
                f"event_{template.key}_{reward_value}",
                template.badge_emoji,
                reward_value,
                "活動",
                "限定",
                1000,
                False,
                30,
            )
            ok_badge, msg_badge = grant_badge_by_name(group_id, user_id, reward_value)
            reward_msg = msg_badge if ok_badge else msg_badge
        except Exception:
            reward_msg = f"🏅 活動徽章：{reward_value}"
    else:
        reward_msg = f"🎁 {reward_value} ×{reward_amount}"

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO event_exchange_logs(
                group_id, user_id, event_key, shop_item_name, cost,
                reward_type, reward_value, reward_amount
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            group_id,
            user_id,
            template.key,
            item["item_name"],
            cost,
            reward_type,
            reward_value,
            reward_amount,
        ))
        conn.commit()
    conn.close()

    left_qty = get_user_event_item_quantity(group_id, user_id, template.key)
    msg = (
        "🎉 兌換成功！\n\n"
        f"🛍️ 商品：{item['item_name']}\n"
        f"{template.item_emoji} 已扣除：{cost}\n"
        f"剩餘：{left_qty}\n\n"
        f"🎁 獲得：\n{reward_msg}"
    )
    return True, msg


def add_event_shop_item(event_name_or_key: str, item_name: str, cost: int, reward_type: str, reward_value: str, reward_amount: int = 1, purchase_limit: int = 0):
    """群長新增/更新活動商品。event 可輸入活動 key 或中文名稱片段。"""
    from database import get_connection

    ensure_event_tables()
    seed_default_event_shop_items()
    event_name_or_key = str(event_name_or_key).strip()
    template = None
    for t in _all_activity_templates():
        if event_name_or_key in [t.key, t.name, t.name.replace("活動", "")] or event_name_or_key in t.name:
            template = t
            break
    if not template:
        return False, "❌ 找不到活動。"

    reward_type_map = {
        "彩虹幣": "coins", "金幣": "coins", "coins": "coins",
        "EXP": "exp", "exp": "exp", "經驗": "exp",
        "稱號": "title", "title": "title",
        "徽章": "badge", "badge": "badge",
    }
    rt = reward_type_map.get(str(reward_type).strip(), str(reward_type).strip())
    if rt not in ["coins", "exp", "title", "badge"]:
        return False, "❌ 獎勵類型只能是：彩虹幣、EXP、稱號、徽章。"

    try:
        cost = int(cost)
        reward_amount = int(reward_amount)
        purchase_limit = int(purchase_limit or 0)
    except Exception:
        return False, "❌ 花費、數量、限購請輸入數字。"
    if cost < 0 or reward_amount < 0 or purchase_limit < 0:
        return False, "❌ 數值不能小於 0。"

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO event_shop_items(event_key, item_name, cost, reward_type, reward_value, reward_amount, is_active, purchase_limit)
            VALUES(%s,%s,%s,%s,%s,%s,TRUE,%s)
            ON CONFLICT(event_key, item_name)
            DO UPDATE SET
                cost=EXCLUDED.cost,
                reward_type=EXCLUDED.reward_type,
                reward_value=EXCLUDED.reward_value,
                reward_amount=EXCLUDED.reward_amount,
                purchase_limit=EXCLUDED.purchase_limit,
                is_active=TRUE,
                updated_at=CURRENT_TIMESTAMP
        """, (template.key, item_name, cost, rt, reward_value, reward_amount, purchase_limit))
        conn.commit()
    conn.close()
    limit_msg = "無限" if purchase_limit == 0 else f"限購 {purchase_limit} 次"
    return True, (
        f"✅ 已新增/更新活動商品\n\n"
        f"活動：{template.name}\n"
        f"商品：{item_name}\n"
        f"花費：{template.item_emoji}{cost}\n"
        f"獎勵：{reward_type} {reward_value} ×{reward_amount}\n"
        f"限制：{limit_msg}"
    )


def set_event_shop_item_active(event_name_or_key: str, item_name: str, active: bool):
    from database import get_connection

    ensure_event_tables()
    template = None
    for t in _all_activity_templates():
        if event_name_or_key in [t.key, t.name, t.name.replace("活動", "")] or event_name_or_key in t.name:
            template = t
            break
    if not template:
        return False, "❌ 找不到活動。"

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            UPDATE event_shop_items
            SET is_active=%s, updated_at=CURRENT_TIMESTAMP
            WHERE event_key=%s AND item_name=%s
        """, (bool(active), template.key, item_name))
        changed = c.rowcount
        conn.commit()
    conn.close()
    if not changed:
        return False, "❌ 找不到這個活動商品。"
    return True, f"✅ 已{'啟用' if active else '停用'}活動商品：{item_name}"


def format_event_shop_management(event_name_or_key: str = ""):
    """活動商店為系統內建唯讀內容，僅顯示目前自動套用狀態。"""
    active = active_activities()
    if not active:
        return (
            "🌈 活動商店設定\n\n"
            "目前沒有進行中的節日或季節活動。\n"
            "活動開始時，系統會自動開啟對應商店並套用內建商品。"
        )
    template = active[0][0]
    return (
        "🌈 活動商店設定\n\n"
        f"目前活動：{template.name}\n"
        f"活動道具：{template.item_emoji} {template.item_name}\n\n"
        "商品由系統依節日／季節自動替換，後台不可新增、修改、上下架或刪除。"
    )


def delete_event_shop_item(event_name_or_key: str, item_name: str):
    from database import get_connection

    ensure_event_tables()
    event_name_or_key = str(event_name_or_key).strip()
    template = None
    for t in _all_activity_templates():
        if event_name_or_key in [t.key, t.name, t.name.replace("活動", "")] or event_name_or_key in t.name:
            template = t
            break
    if not template:
        return False, "❌ 找不到活動。"

    conn = get_connection()
    with conn.cursor() as c:
        c.execute("""
            UPDATE event_shop_items
            SET is_active=FALSE, updated_at=CURRENT_TIMESTAMP
            WHERE event_key=%s AND item_name=%s
        """, (template.key, item_name))
        changed = c.rowcount
        conn.commit()
    conn.close()
    if changed:
        return True, f"✅ 已刪除活動商品：{item_name}"
    return False, "❌ 找不到這個活動商品。"
