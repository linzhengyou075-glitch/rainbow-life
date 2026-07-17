import datetime
from zoneinfo import ZoneInfo

from config import VIP_7_PRICE, VIP_30_PRICE, VIP_FOREVER_PRICE, VIP_EXP_RATE
from database import get_connection


def ensure_vip_settings_table():
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS group_vip_settings (
                    group_id TEXT PRIMARY KEY,
                    price_7 INTEGER NOT NULL DEFAULT %s,
                    price_30 INTEGER NOT NULL DEFAULT %s,
                    price_forever INTEGER NOT NULL DEFAULT %s,
                    shop_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    exp_multiplier NUMERIC(5,2) NOT NULL DEFAULT %s,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """, (VIP_7_PRICE, VIP_30_PRICE, VIP_FOREVER_PRICE, VIP_EXP_RATE))
        conn.commit()
    finally:
        conn.close()


def get_vip_settings(group_id):
    ensure_vip_settings_table()
    defaults = {
        "price_7": int(VIP_7_PRICE),
        "price_30": int(VIP_30_PRICE),
        "price_forever": int(VIP_FOREVER_PRICE),
        "shop_enabled": True,
        "exp_multiplier": float(VIP_EXP_RATE),
    }
    if not group_id or group_id == "PRIVATE":
        return defaults
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO group_vip_settings(group_id)
                VALUES(%s)
                ON CONFLICT(group_id) DO NOTHING
            """, (group_id,))
            c.execute("""
                SELECT price_7, price_30, price_forever, shop_enabled, exp_multiplier
                FROM group_vip_settings WHERE group_id=%s
            """, (group_id,))
            row = c.fetchone() or {}
        conn.commit()
    finally:
        conn.close()
    return {
        "price_7": int(row.get("price_7") or defaults["price_7"]),
        "price_30": int(row.get("price_30") or defaults["price_30"]),
        "price_forever": int(row.get("price_forever") or defaults["price_forever"]),
        "shop_enabled": bool(row.get("shop_enabled", True)),
        "exp_multiplier": float(row.get("exp_multiplier") or defaults["exp_multiplier"]),
    }


def update_vip_price(group_id, plan, price):
    ensure_vip_settings_table()
    plan_map = {"7": "price_7", "7天": "price_7", "30": "price_30", "30天": "price_30", "永久": "price_forever", "永久VIP": "price_forever"}
    column = plan_map.get(str(plan).strip())
    if not column:
        return False, "❌ 方案只能輸入 7天、30天或永久。"
    try:
        price = int(price)
    except (TypeError, ValueError):
        return False, "❌ 價格請輸入整數。"
    if price < 0 or price > 100000000:
        return False, "❌ 價格需介於 0～100,000,000。"
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO group_vip_settings(group_id) VALUES(%s) ON CONFLICT(group_id) DO NOTHING", (group_id,))
            c.execute(f"UPDATE group_vip_settings SET {column}=%s, updated_at=CURRENT_TIMESTAMP WHERE group_id=%s", (price, group_id))
        conn.commit()
    finally:
        conn.close()
    label = "永久" if column == "price_forever" else ("7 天" if column == "price_7" else "30 天")
    return True, f"✅ 已修改 VIP {label}價格為 🌈{price:,}。"


def set_vip_shop_enabled(group_id, enabled):
    ensure_vip_settings_table()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO group_vip_settings(group_id) VALUES(%s) ON CONFLICT(group_id) DO NOTHING", (group_id,))
            c.execute("UPDATE group_vip_settings SET shop_enabled=%s, updated_at=CURRENT_TIMESTAMP WHERE group_id=%s", (bool(enabled), group_id))
        conn.commit()
    finally:
        conn.close()
    return f"✅ VIP 商店已{'開啟' if enabled else '關閉'}。"


def set_vip_exp_multiplier(group_id, multiplier):
    ensure_vip_settings_table()
    try:
        multiplier = float(multiplier)
    except (TypeError, ValueError):
        return False, "❌ 倍率請輸入數字，例如 2 或 1.5。"
    if multiplier < 1 or multiplier > 10:
        return False, "❌ VIP EXP 倍率需介於 1～10 倍。"
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO group_vip_settings(group_id) VALUES(%s) ON CONFLICT(group_id) DO NOTHING", (group_id,))
            c.execute("UPDATE group_vip_settings SET exp_multiplier=%s, updated_at=CURRENT_TIMESTAMP WHERE group_id=%s", (multiplier, group_id))
        conn.commit()
    finally:
        conn.close()
    text = f"{multiplier:g}"
    return True, f"✅ VIP EXP 倍率已設定為 ×{text}。"


def effective_vip_price(group_id, item_type, fallback=0):
    settings = get_vip_settings(group_id)
    mapping = {"vip_7": "price_7", "vip_30": "price_30", "vip_forever": "price_forever"}
    key = mapping.get(str(item_type or ""))
    return int(settings[key]) if key else int(fallback or 0)


def vip_is_active_value(is_vip, vip_until):
    if not bool(is_vip):
        return False
    raw = str(vip_until or "").strip()
    upper = raw.upper()
    if upper in {"PERMANENT", "FOREVER", "永久", "永久VIP"} or raw[:10] in {"9999-12-31", "9999-12-30"}:
        return True
    try:
        return datetime.date.fromisoformat(raw[:10]) >= datetime.datetime.now(ZoneInfo("Asia/Taipei")).date()
    except Exception:
        return False
