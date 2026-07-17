import json
import os
import time
from database import get_connection

GLOBAL_GROUP_ID = "__RAINBOW_LIFE_GLOBAL__"
THEME_EVENT_KEY = "rainbow_life_theme"
DEFAULT_THEME = "rainbow-starfield"

THEMES = {
    "rainbow-starfield": {
        "name": "彩虹星空",
        "icon": "🌈",
        "web_class": "rainbow-starfield",
        "primary": "#7557E8",
        "accent": "#FF69C9",
        "soft": "#EEE9FF",
        "background": "#120D2A",
        "card": "#25164D",
        "text": "#FFFFFF",
        "subtext": "#E4D9FF",
        "border": "#A879FF",
        "header": "#3A2168",
    },
    "spring-garden": {
        "name": "春日花園",
        "icon": "🌸",
        "web_class": "spring-day",
        "primary": "#E85EA6",
        "accent": "#82CFA5",
        "soft": "#FFF0F7",
        "background": "#FFF4F9",
        "card": "#FFFFFF",
        "text": "#54223D",
        "subtext": "#87566F",
        "border": "#F2A5CC",
        "header": "#FFD8EA",
    },
    "summer-ocean": {
        "name": "夏日海洋",
        "icon": "🌊",
        "web_class": "summer-day",
        "primary": "#13AFCF",
        "accent": "#6B79F2",
        "soft": "#E4F9FF",
        "background": "#EAFBFF",
        "card": "#FFFFFF",
        "text": "#123E50",
        "subtext": "#467384",
        "border": "#45CDE8",
        "header": "#CFF7FF",
    },
    "autumn-maple": {
        "name": "秋日楓葉",
        "icon": "🍁",
        "web_class": "autumn-day",
        "primary": "#DE7B22",
        "accent": "#B84B4B",
        "soft": "#FFF0D8",
        "background": "#FFF5E5",
        "card": "#FFFFFF",
        "text": "#573216",
        "subtext": "#8B6547",
        "border": "#EFA454",
        "header": "#FFE0AF",
    },
    "winter-starlight": {
        "name": "冬季星雪",
        "icon": "❄️",
        "web_class": "winter-night",
        "primary": "#6F9EEA",
        "accent": "#B18BFF",
        "soft": "#DDEBFF",
        "background": "#08172D",
        "card": "#102A4B",
        "text": "#FFFFFF",
        "subtext": "#C8DEFF",
        "border": "#6999DF",
        "header": "#163A65",
    },
    "pride-festival": {
        "name": "彩虹盛典",
        "icon": "🏳️‍🌈",
        "web_class": "pride",
        "primary": "#8357E8",
        "accent": "#FF4F9A",
        "soft": "#F4E9FF",
        "background": "#17102E",
        "card": "#2A1A4E",
        "text": "#FFFFFF",
        "subtext": "#EADFFF",
        "border": "#FF89D0",
        "header": "#43246F",
    },
}

_cache = {"key": DEFAULT_THEME, "at": 0.0}

def normalize_theme(value):
    key = str(value or "").strip().lower()
    return key if key in THEMES else DEFAULT_THEME

def _ensure_table(conn):
    with conn.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS bot_events (
            group_id TEXT NOT NULL,
            event_key TEXT NOT NULL,
            event_value TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (group_id, event_key)
        )""")
    conn.commit()

def get_theme_key(force=False):
    now = time.time()
    if not force and now - float(_cache.get("at") or 0) < 20:
        return normalize_theme(_cache.get("key"))
    env = os.getenv("RAINBOW_LIFE_THEME", "").strip()
    if env:
        key = normalize_theme(env)
    else:
        key = DEFAULT_THEME
        conn = None
        try:
            conn = get_connection()
            _ensure_table(conn)
            with conn.cursor() as c:
                c.execute("SELECT event_value FROM bot_events WHERE group_id=%s AND event_key=%s", (GLOBAL_GROUP_ID, THEME_EVENT_KEY))
                row = c.fetchone()
            if row:
                raw = row.get("event_value") if isinstance(row, dict) else row[0]
                try:
                    data = json.loads(raw)
                    raw = data.get("theme") if isinstance(data, dict) else raw
                except Exception:
                    pass
                key = normalize_theme(raw)
        except Exception:
            key = DEFAULT_THEME
        finally:
            if conn is not None:
                try: conn.close()
                except Exception: pass
    _cache.update(key=key, at=now)
    return key

def get_theme(force=False):
    key = get_theme_key(force=force)
    return {"key": key, **THEMES[key]}

def set_theme(theme_key, changed_by=""):
    key = normalize_theme(theme_key)
    payload = json.dumps({"theme": key, "changed_by": str(changed_by or ""), "updated_at": int(time.time())}, ensure_ascii=False)
    conn = get_connection()
    try:
        _ensure_table(conn)
        with conn.cursor() as c:
            c.execute("""INSERT INTO bot_events(group_id,event_key,event_value,updated_at)
                         VALUES(%s,%s,%s,CURRENT_TIMESTAMP)
                         ON CONFLICT(group_id,event_key) DO UPDATE
                         SET event_value=EXCLUDED.event_value,updated_at=CURRENT_TIMESTAMP""",
                      (GLOBAL_GROUP_ID, THEME_EVENT_KEY, payload))
        conn.commit()
    finally:
        conn.close()
    _cache.update(key=key, at=time.time())
    return get_theme(force=True)

def css_variables():
    t = get_theme()
    return ";".join([
        f"--rl-primary:{t['primary']}", f"--rl-accent:{t['accent']}",
        f"--rl-soft:{t['soft']}", f"--rl-bg:{t['background']}",
        f"--rl-card:{t['card']}", f"--rl-text:{t['text']}",
        f"--rl-subtext:{t['subtext']}", f"--rl-border:{t['border']}",
        f"--rl-header:{t['header']}"
    ])

def flex_palette():
    t = get_theme()
    return {
        "key": t["key"], "label": f"{t['icon']} {t['name']}",
        "bg": t["background"], "card": t["card"], "accent": t["primary"],
        "accent2": t["accent"], "text": t["text"], "sub": t["subtext"],
        "border": t["border"], "head": t["header"], "soft": t["soft"],
    }
