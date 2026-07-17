"""群組管理員、權限與福利管理。"""
from database import get_connection

PERMISSION_LABELS = {
    "view_member": "查看成員資料", "mute": "禁言", "unmute": "解除禁言",
    "view_mute": "查看禁言", "announcement": "發送公告", "vip_manage": "VIP管理",
    "coins_manage": "彩虹幣管理", "exp_manage": "EXP管理", "shop_manage": "商店管理",
    "event_manage": "活動管理", "wheel_manage": "轉盤設定", "fortune_manage": "運勢設定",
    "system_manage": "系統設定",
}
DEFAULT_ADMIN_PERMISSIONS = {
    "view_member": True, "mute": True, "unmute": True, "view_mute": True,
    "announcement": True, "vip_manage": False, "coins_manage": False,
    "exp_manage": False, "shop_manage": False, "event_manage": False,
    "wheel_manage": False, "fortune_manage": False, "system_manage": False,
}
ADMIN_BENEFITS = {"sign_coins": 50, "sign_exp": 100, "wheel_spins": 1, "luck_bonus": 5}


def _clean(value):
    return str(value or "").strip()


def ensure_admin_table():
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS admins (
                group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin', PRIMARY KEY(group_id, user_id)
            )""")
            c.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'admin'")
        conn.commit()
    finally:
        conn.close()


def ensure_admin_permission_tables():
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS admin_permissions (
                group_id TEXT NOT NULL, user_id TEXT NOT NULL, permission_key TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id, user_id, permission_key)
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS admin_logs (
                id BIGSERIAL PRIMARY KEY, group_id TEXT NOT NULL, operator_user_id TEXT NOT NULL,
                target_user_id TEXT DEFAULT '', action TEXT NOT NULL, detail TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.commit()
    finally:
        conn.close()


def get_admin_role(group_id, user_id):
    group_id, user_id = _clean(group_id), _clean(user_id)
    if not group_id or group_id == "PRIVATE" or not user_id:
        return ""
    ensure_admin_table()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT role FROM admins WHERE TRIM(group_id)=TRIM(%s) AND TRIM(user_id)=TRIM(%s)", (group_id, user_id))
            row = c.fetchone() or {}
            return _clean(row.get("role")).lower()
    finally:
        conn.close()


def is_owner(group_id, user_id):
    return get_admin_role(group_id, user_id) == "owner"


def is_admin(group_id, user_id):
    return get_admin_role(group_id, user_id) in {"owner", "admin"}


def set_owner(group_id, user_id):
    group_id, user_id = _clean(group_id), _clean(user_id)
    if not group_id or group_id == "PRIVATE" or not user_id:
        return False
    ensure_admin_table()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO admins(group_id,user_id,role) VALUES(%s,%s,'owner')
                ON CONFLICT(group_id,user_id) DO UPDATE SET role=EXCLUDED.role""", (group_id, user_id))
        conn.commit(); return True
    finally:
        conn.close()


def add_admin(group_id, target_user_id):
    group_id, target_user_id = _clean(group_id), _clean(target_user_id)
    if not group_id or group_id == "PRIVATE" or not target_user_id:
        return False
    ensure_admin_table()
    if is_owner(group_id, target_user_id):
        return False
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO admins(group_id,user_id,role) VALUES(%s,%s,'admin')
                ON CONFLICT(group_id,user_id) DO UPDATE SET role='admin'""", (group_id, target_user_id))
        conn.commit(); return True
    finally:
        conn.close()


def remove_admin(group_id, target_user_id):
    group_id, target_user_id = _clean(group_id), _clean(target_user_id)
    if not group_id or not target_user_id or is_owner(group_id, target_user_id):
        return False
    ensure_admin_table()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM admins WHERE group_id=%s AND user_id=%s AND role<>'owner'", (group_id, target_user_id))
            removed = c.rowcount > 0
        conn.commit(); return removed
    finally:
        conn.close()


def list_admins(group_id):
    ensure_admin_table()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""SELECT a.user_id,a.role,COALESCE(p.name,a.user_id) AS name
                FROM admins a LEFT JOIN players p ON p.group_id=a.group_id AND p.user_id=a.user_id
                WHERE a.group_id=%s
                ORDER BY CASE WHEN a.role='owner' THEN 0 ELSE 1 END, COALESCE(p.name,a.user_id)""", (_clean(group_id),))
            return list(c.fetchall() or [])
    finally:
        conn.close()


def add_admin_log(group_id, operator_user_id, action, target_user_id="", detail=""):
    ensure_admin_permission_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO admin_logs(group_id,operator_user_id,target_user_id,action,detail) VALUES(%s,%s,%s,%s,%s)",
                      (_clean(group_id), _clean(operator_user_id), _clean(target_user_id), _clean(action), _clean(detail)))
        conn.commit()
    finally:
        conn.close()


def set_admin_permission(group_id, target_user_id, permission_key, enabled):
    permission_key = _clean(permission_key)
    if permission_key not in DEFAULT_ADMIN_PERMISSIONS or is_owner(group_id, target_user_id):
        return False
    ensure_admin_permission_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO admin_permissions(group_id,user_id,permission_key,enabled,updated_at)
                VALUES(%s,%s,%s,%s,CURRENT_TIMESTAMP)
                ON CONFLICT(group_id,user_id,permission_key)
                DO UPDATE SET enabled=EXCLUDED.enabled,updated_at=CURRENT_TIMESTAMP""",
                      (_clean(group_id), _clean(target_user_id), permission_key, 1 if enabled else 0))
        conn.commit(); return True
    finally:
        conn.close()


def has_admin_permission(group_id, user_id, permission_key):
    permission_key = _clean(permission_key)
    role = get_admin_role(group_id, user_id)
    if role == "owner":
        return True
    if role != "admin" or permission_key not in DEFAULT_ADMIN_PERMISSIONS:
        return False
    ensure_admin_permission_tables()
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT enabled FROM admin_permissions WHERE group_id=%s AND user_id=%s AND permission_key=%s",
                      (_clean(group_id), _clean(user_id), permission_key))
            row = c.fetchone()
            return bool(row.get("enabled")) if row else DEFAULT_ADMIN_PERMISSIONS[permission_key]
    finally:
        conn.close()


def list_admin_permissions(group_id, target_user_id):
    return [(key, PERMISSION_LABELS[key], has_admin_permission(group_id, target_user_id, key)) for key in DEFAULT_ADMIN_PERMISSIONS]


def get_admin_badge(group_id, user_id):
    role = get_admin_role(group_id, user_id)
    return "👑 群長" if role == "owner" else ("🛡️ 管理員" if role == "admin" else "")


def get_admin_benefits(group_id, user_id):
    return dict(ADMIN_BENEFITS) if get_admin_role(group_id, user_id) == "admin" else {
        "sign_coins": 0, "sign_exp": 0, "wheel_spins": 0, "luck_bonus": 0
    }
