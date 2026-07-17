import json
from datetime import datetime
from zoneinfo import ZoneInfo
from database import get_connection

TZ = ZoneInfo('Asia/Taipei')
DEFAULTS = {
    'sign_coin': 300,
    'sign_exp': 150,
    'makeup_prices': {'1':100,'2':200,'3':300,'4':450,'5':600,'6':800,'7':1000},
    'daily_chat_exp_cap': 0,  # 0 = 無上限
    'welcome_enabled': True,
    'welcome_text': '歡迎加入群組！請先查看群規並完成自我介紹。',
    'birthday_reminder': True,
    'sign_reminder': False,
    'sign_reminder_time': '20:00',
    'vip_expiry_reminder': True,
    'vault_target': 50000,
    'vault_reward_coin': 300,
    'vault_reward_exp': 100,
}

def ensure_v4_tables():
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS group_runtime_settings(
                group_id TEXT NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id, setting_key))''')
            c.execute('''CREATE TABLE IF NOT EXISTS admin_audit_log(
                id BIGSERIAL PRIMARY KEY, group_id TEXT NOT NULL, admin_user_id TEXT NOT NULL,
                action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            c.execute('''CREATE TABLE IF NOT EXISTS member_achievements(
                group_id TEXT NOT NULL, user_id TEXT NOT NULL, achievement_key TEXT NOT NULL,
                unlocked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,user_id,achievement_key))''')
        conn.commit()
    finally: conn.close()

def _encode(value):
    if isinstance(value,(dict,list,bool,int,float)): return json.dumps(value,ensure_ascii=False)
    return str(value)

def get_setting(group_id,key,default=None):
    ensure_v4_tables()
    if default is None: default=DEFAULTS.get(key)
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute('SELECT setting_value FROM group_runtime_settings WHERE group_id=%s AND setting_key=%s',(group_id,key))
            row=c.fetchone()
        if not row: return default
        raw=row.get('setting_value')
        try: return json.loads(raw)
        except Exception: return raw
    finally: conn.close()

def set_setting(group_id,key,value,admin_user_id='SYSTEM'):
    ensure_v4_tables(); encoded=_encode(value)
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute('''INSERT INTO group_runtime_settings(group_id,setting_key,setting_value,updated_at)
                VALUES(%s,%s,%s,CURRENT_TIMESTAMP) ON CONFLICT(group_id,setting_key) DO UPDATE
                SET setting_value=EXCLUDED.setting_value, updated_at=CURRENT_TIMESTAMP''',(group_id,key,encoded))
            c.execute('INSERT INTO admin_audit_log(group_id,admin_user_id,action,detail) VALUES(%s,%s,%s,%s)',
                      (group_id,admin_user_id,'修改設定',f'{key}={encoded}'))
        conn.commit()
    finally: conn.close()

def add_audit(group_id,admin_user_id,action,detail=''):
    ensure_v4_tables(); conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute('INSERT INTO admin_audit_log(group_id,admin_user_id,action,detail) VALUES(%s,%s,%s,%s)',(group_id,admin_user_id,action,detail))
        conn.commit()
    finally: conn.close()

def get_sign_settings(group_id):
    return {
        'coin': int(get_setting(group_id,'sign_coin',DEFAULTS['sign_coin'])),
        'exp': int(get_setting(group_id,'sign_exp',DEFAULTS['sign_exp'])),
        'makeup_prices': get_setting(group_id,'makeup_prices',DEFAULTS['makeup_prices']),
        'reset_hour': 5,
    }

def set_makeup_price(group_id,days,price,admin_user_id):
    days=int(days); price=int(price)
    if days<1 or days>7: return False,'❌ 天數需介於 1～7。'
    if price<0: return False,'❌ 價格不可小於 0。'
    prices=dict(get_setting(group_id,'makeup_prices',DEFAULTS['makeup_prices']))
    prices[str(days)]=price
    set_setting(group_id,'makeup_prices',prices,admin_user_id)
    return True,f'✅ 補簽 {days} 天前價格已改為 🌈{price:,}。'

def analytics_message(group_id, group_name='目前群組'):
    ensure_v4_tables(); conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute('''SELECT COUNT(*) members, COUNT(*) FILTER(WHERE COALESCE(today_msg_count,0)>0 OR COALESCE(today_sticker_count,0)>0) active,
                COALESCE(SUM(today_msg_count),0) messages, COALESCE(SUM(today_sticker_count),0) stickers,
                COUNT(*) FILTER(WHERE COALESCE(is_vip,0)=1) vip_count,
                COUNT(*) FILTER(WHERE last_sign_in<>\'\') signed FROM players WHERE group_id=%s''',(group_id,))
            s=c.fetchone() or {}
            c.execute('''SELECT COALESCE(SUM(cost),0) spend, COUNT(*) tx FROM purchase_history WHERE group_id=%s AND
                (created_at AT TIME ZONE 'Asia/Taipei')::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Taipei')::date''',(group_id,))
            p=c.fetchone() or {}
            c.execute('SELECT COALESCE(balance,0) balance FROM group_vaults WHERE group_id=%s',(group_id,)); v=c.fetchone() or {}
    finally: conn.close()
    members=int(s.get('members') or 0); active=int(s.get('active') or 0)
    rate=(active/members*100) if members else 0
    return (f'📊 群組數據分析\n\n📂 {group_name}\n\n👥 建檔成員：{members} 人\n🟢 今日活躍：{active} 人（{rate:.1f}%）\n'
            f'💬 今日訊息：{int(s.get("messages") or 0):,}\n🖼️ 今日貼圖：{int(s.get("stickers") or 0):,}\n'
            f'💎 VIP：{int(s.get("vip_count") or 0)} 人\n📅 有簽到紀錄：{int(s.get("signed") or 0)} 人\n\n'
            f'💸 今日消費：🌈{int(p.get("spend") or 0):,}\n🧾 今日交易：{int(p.get("tx") or 0)} 筆\n🏦 金庫：🌈{int(v.get("balance") or 0):,}')

def audit_message(group_id,limit=20):
    ensure_v4_tables(); conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute('''SELECT action,detail,created_at FROM admin_audit_log WHERE group_id=%s ORDER BY id DESC LIMIT %s''',(group_id,limit))
            rows=c.fetchall()
    finally: conn.close()
    if not rows: return '📜 管理操作日誌\n\n目前沒有紀錄。'
    lines=['📜 管理操作日誌','']
    for r in rows:
        t=r['created_at'].astimezone(TZ).strftime('%m/%d %H:%M') if r.get('created_at') else ''
        lines += [f'• {t}｜{r.get("action")}',f'  {r.get("detail") or "-"}']
    return '\n'.join(lines)

def reminder_summary(group_id):
    return (f'🔔 自動提醒中心\n\n'
            f'📅 簽到提醒：{"開啟" if get_setting(group_id,"sign_reminder",False) else "關閉"}\n'
            f'⏰ 提醒時間：{get_setting(group_id,"sign_reminder_time","20:00")}\n'
            f'💎 VIP 到期提醒：{"開啟" if get_setting(group_id,"vip_expiry_reminder",True) else "關閉"}\n'
            f'🎂 生日提醒：{"開啟" if get_setting(group_id,"birthday_reminder",True) else "關閉"}')
