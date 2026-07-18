import datetime
import hashlib
import hmac
import html
import re
from html.parser import HTMLParser
import json
import os
import secrets
import base64
import threading
import time
from urllib.parse import urlencode, quote
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from database import get_connection

TZ = datetime.timezone(datetime.timedelta(hours=8))
WEB_SECRET = os.getenv('RAINBOW_WEB_SECRET') or os.getenv('LINE_CHANNEL_SECRET') or 'rainbow-life-change-me'
OWNER_USER_ID = os.getenv('RAINBOW_OWNER_USER_ID', '').strip()
TOKEN_TTL = 60 * 60 * 12
PLAYER_COOKIE = 'rainbow_player'
ADMIN_COOKIE = 'rainbow_admin'
SESSION_TTL = 60 * 60 * 12

router = APIRouter()

_WEATHER_CACHE = {}
_WEATHER_LOCK = threading.Lock()
WEATHER_CACHE_SECONDS = 30
_FAMILY_CACHE = {}
_FAMILY_LOCK = threading.Lock()
FAMILY_CACHE_SECONDS = 30
FAMILY_EVENT_URL = 'https://www.family.com.tw/Marketing/zh/Event'
_SEVEN_CACHE = {}
_SEVEN_LOCK = threading.Lock()
SEVEN_CACHE_SECONDS = 30
SEVEN_EVENT_URL = 'https://www.7-11.com.tw/special/article_new.aspx?item=Event_E001'
_MCD_CACHE = {}
_MCD_LOCK = threading.Lock()
MCD_CACHE_SECONDS = 30
MCD_EVENT_URL = 'https://www.mcdonalds.com/tw/zh-tw.html'
OFFICIAL_INFO_CACHE_SECONDS = 30
_OFFICIAL_INFO_CACHE = {}
_OFFICIAL_INFO_LOCK = threading.Lock()
OFFICIAL_INFO_SOURCES = {
    'tra': {'title':'台鐵最新公告','url':'https://www.railway.gov.tw/tra-tip-web/tip/tip009/tip911/newsList','source':'國營臺灣鐵路股份有限公司'},
    'thsr': {'title':'高鐵最新公告','url':'https://www.thsrc.com.tw/ArticleContent/cc283668-bfd4-4e33-9f5d-788f5d7e3f80','source':'台灣高鐵'},
    'pokemon_go': {'title':'Pokémon GO 官方更新','url':'https://pokemongolive.com/zh_hant/post/','source':'Pokémon GO 官方網站'},
    'aov': {'title':'傳說對決官方更新','url':'https://moba.garena.tw/news/','source':'Garena 傳說對決官方網站'},
}
CWA_DATASET_ID = os.getenv('CWA_WEATHER_DATASET', 'F-D0047-089').strip()
CWA_WARNING_DATASET_ID = os.getenv('CWA_WARNING_DATASET', 'W-C0033-001').strip()
CWA_WARNING_URL = 'https://www.cwa.gov.tw/V8/C/W/Warning.html'


def _now_ts():
    return int(datetime.datetime.now(TZ).timestamp())


def _sign(user_id, group_id, exp):
    raw = f'{user_id}|{group_id}|{exp}'.encode('utf-8')
    return hmac.new(WEB_SECRET.encode('utf-8'), raw, hashlib.sha256).hexdigest()


def _public_base_url():
    raw = (os.getenv('PUBLIC_BASE_URL') or os.getenv('RENDER_EXTERNAL_URL') or os.getenv('APP_BASE_URL') or '').strip()
    if not raw:
        host = (os.getenv('RENDER_EXTERNAL_HOSTNAME') or '').strip()
        if host:
            raw = 'https://' + host
    if raw and not raw.startswith(('http://','https://')):
        raw = 'https://' + raw
    return raw.rstrip('/')


def make_access_url(user_id, group_id, path='/player'):
    base = _public_base_url()
    if not base:
        return ''
    exp = _now_ts() + TOKEN_TTL
    sep = '&' if '?' in path else '?'
    query = urlencode({'uid': user_id, 'gid': group_id, 'exp': exp, 'sig': _sign(user_id, group_id, exp)})
    return f'{base}{path}{sep}{query}'


def make_player_entry_url(group_id, target='/player'):
    base = _public_base_url()
    if not base or not group_id or group_id == 'PRIVATE':
        return ''
    safe_target = target if str(target).startswith('/player') else '/player'
    return f"{base}/player/entry?{urlencode({'gid':str(group_id),'target':safe_target})}"


def _token_secret(kind='player'):
    if kind == 'admin':
        return os.getenv('ADMIN_WEB_SECRET') or os.getenv('LINE_CHANNEL_SECRET') or WEB_SECRET
    return WEB_SECRET


def _encode_token(data, kind='player'):
    payload = base64.urlsafe_b64encode(json.dumps(data,separators=(',',':')).encode()).decode().rstrip('=')
    sig = hmac.new(_token_secret(kind).encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + '.' + sig


def _decode_token(token, kind='player'):
    try:
        payload, sig = token.split('.',1)
        expected = hmac.new(_token_secret(kind).encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected): return None
        data = json.loads(base64.urlsafe_b64decode((payload + '=' * (-len(payload)%4)).encode()).decode())
        if int(data.get('exp',0)) < _now_ts(): return None
        return data
    except Exception:
        return None


def _session_auth(request):
    for cookie, kind in ((PLAYER_COOKIE,'player'),(ADMIN_COOKIE,'admin')):
        data = _decode_token(request.cookies.get(cookie,''), kind)
        if data and data.get('uid') and data.get('gid'):
            return str(data['uid']), str(data['gid'])
    return None


def _auth(request: Request):
    session = _session_auth(request)
    if session:
        return session
    uid = str(request.query_params.get('uid') or request.headers.get('x-rainbow-user') or '').strip()
    gid = str(request.query_params.get('gid') or request.headers.get('x-rainbow-group') or '').strip()
    exp = str(request.query_params.get('exp') or '').strip()
    sig = str(request.query_params.get('sig') or '').strip()
    if not uid or not gid or not exp or not sig:
        raise HTTPException(status_code=401, detail='登入連結不完整，請回到 LINE 重新開啟個人中心。')
    try:
        exp_i = int(exp)
    except ValueError:
        raise HTTPException(status_code=401, detail='登入連結格式錯誤。')
    if exp_i < _now_ts():
        raise HTTPException(status_code=401, detail='登入連結已過期，請回到 LINE 重新開啟。')
    if not hmac.compare_digest(sig, _sign(uid, gid, exp_i)):
        raise HTTPException(status_code=401, detail='登入驗證失敗。')
    return uid, gid


def _oauth_ready():
    return bool(os.getenv('LINE_LOGIN_CHANNEL_ID') and os.getenv('LINE_LOGIN_CHANNEL_SECRET') and _public_base_url())


def _oauth_callback_url():
    return _public_base_url() + '/player/oauth/callback'


def _line_authorize_url(gid, target):
    state = _encode_token({'gid':gid,'target':target,'kind':'line_oauth_state','exp':_now_ts()+600})
    params = {'response_type':'code','client_id':os.getenv('LINE_LOGIN_CHANNEL_ID',''),'redirect_uri':_oauth_callback_url(),'state':state,'scope':'openid profile'}
    return 'https://access.line.me/oauth2/v2.1/authorize?' + urlencode(params)


def _exchange_line_code(code):
    payload = urlencode({'grant_type':'authorization_code','code':code,'redirect_uri':_oauth_callback_url(),'client_id':os.getenv('LINE_LOGIN_CHANNEL_ID',''),'client_secret':os.getenv('LINE_LOGIN_CHANNEL_SECRET','')}).encode()
    req = UrlRequest('https://api.line.me/oauth2/v2.1/token', data=payload, headers={'Content-Type':'application/x-www-form-urlencoded'}, method='POST')
    try:
        with urlopen(req, timeout=15) as r: return json.loads(r.read().decode())
    except Exception: return {}


def _verify_id_token(id_token):
    payload = urlencode({'id_token':id_token,'client_id':os.getenv('LINE_LOGIN_CHANNEL_ID','')}).encode()
    req = UrlRequest('https://api.line.me/oauth2/v2.1/verify', data=payload, headers={'Content-Type':'application/x-www-form-urlencoded'}, method='POST')
    try:
        with urlopen(req, timeout=15) as r: return json.loads(r.read().decode())
    except Exception: return {}


def _role(group_id, user_id):
    if OWNER_USER_ID and user_id == OWNER_USER_ID:
        return 'owner'
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute('SELECT role FROM admins WHERE group_id=%s AND user_id=%s', (group_id, user_id))
            row = c.fetchone() or {}
            role = str(row.get('role') or '').lower()
            if role in ('owner', 'superadmin', 'system_owner'):
                return 'owner'
            if role in ('leader', 'group_owner', '群長'):
                return 'leader'
            if row:
                return 'admin'
    finally:
        conn.close()
    return 'member'


def _line_profile(line_bot_api, group_id, user_id, fallback_name):
    name = fallback_name or 'Rainbow 成員'
    picture = ''
    try:
        profile = line_bot_api.get_group_member_profile(group_id, user_id)
        name = getattr(profile, 'display_name', None) or name
        picture = getattr(profile, 'picture_url', None) or ''
    except Exception:
        try:
            profile = line_bot_api.get_profile(user_id)
            name = getattr(profile, 'display_name', None) or name
            picture = getattr(profile, 'picture_url', None) or ''
        except Exception:
            pass
    return name, picture


def ensure_web_tables():
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS web_announcements (
                id BIGSERIAL PRIMARY KEY, group_id TEXT NOT NULL, title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1,
                image_data BYTEA, image_mime TEXT NOT NULL DEFAULT '', link_url TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )''')
            c.execute("ALTER TABLE web_announcements ADD COLUMN IF NOT EXISTS image_data BYTEA")
            c.execute("ALTER TABLE web_announcements ADD COLUMN IF NOT EXISTS image_mime TEXT NOT NULL DEFAULT ''")
            c.execute("ALTER TABLE web_announcements ADD COLUMN IF NOT EXISTS link_url TEXT NOT NULL DEFAULT ''")
            c.execute('''CREATE TABLE IF NOT EXISTS web_calendar_events (
                id BIGSERIAL PRIMARY KEY, group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                event_date TEXT NOT NULL, title TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS web_profile_settings (
                group_id TEXT NOT NULL, user_id TEXT NOT NULL, bio TEXT NOT NULL DEFAULT '',
                region TEXT NOT NULL DEFAULT '', theme TEXT NOT NULL DEFAULT 'rainbow-cosmos',
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,user_id)
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_web_events_user_date ON web_calendar_events(group_id,user_id,event_date)')
            c.execute('''CREATE TABLE IF NOT EXISTS web_activity_overrides (
                group_id TEXT NOT NULL, activity_key TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '',
                period TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 50, is_visible INTEGER NOT NULL DEFAULT 1,
                updated_by TEXT NOT NULL DEFAULT '', updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,activity_key)
            )''')
            c.execute("""CREATE TABLE IF NOT EXISTS web_avatar_frames (
                frame_key TEXT PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL DEFAULT 0,
                vip_only BOOLEAN NOT NULL DEFAULT FALSE, owner_only BOOLEAN NOT NULL DEFAULT FALSE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS web_user_frames (
                group_id TEXT NOT NULL, user_id TEXT NOT NULL, frame_key TEXT NOT NULL,
                equipped BOOLEAN NOT NULL DEFAULT FALSE, acquired_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,user_id,frame_key)
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS web_achievements (
                achievement_key TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                condition_type TEXT NOT NULL DEFAULT 'manual', condition_value INTEGER NOT NULL DEFAULT 0,
                frame_key TEXT NOT NULL DEFAULT '', is_active BOOLEAN NOT NULL DEFAULT TRUE
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS web_user_achievements (
                group_id TEXT NOT NULL, user_id TEXT NOT NULL, achievement_key TEXT NOT NULL,
                unlocked_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,user_id,achievement_key)
            )""")
            c.execute("ALTER TABLE web_achievements ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'daily'")
            c.execute("ALTER TABLE web_achievements ADD COLUMN IF NOT EXISTS icon TEXT NOT NULL DEFAULT '🏆'")
            c.execute("ALTER TABLE web_achievements ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 50")
            c.execute("ALTER TABLE web_announcements ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 50")
            c.execute("ALTER TABLE web_announcements ADD COLUMN IF NOT EXISTS starts_at TIMESTAMPTZ")
            c.execute("ALTER TABLE web_announcements ADD COLUMN IF NOT EXISTS ends_at TIMESTAMPTZ")
            c.execute("""CREATE TABLE IF NOT EXISTS web_admin_logs (
                id BIGSERIAL PRIMARY KEY, group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS web_group_settings (
                group_id TEXT PRIMARY KEY, inactive_days INTEGER NOT NULL DEFAULT 7,
                low_activity_messages INTEGER NOT NULL DEFAULT 3,
                maintenance_mode BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS web_member_controls (
                group_id TEXT NOT NULL, user_id TEXT NOT NULL, is_muted BOOLEAN NOT NULL DEFAULT FALSE,
                muted_until TIMESTAMPTZ, disabled_features TEXT NOT NULL DEFAULT '',
                updated_by TEXT NOT NULL DEFAULT '', updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(group_id,user_id)
            )""")
            for ach in [('first_steps','🌱 初次啟程','累積簽到 7 天','sign_total',7,'star_guard'),('steady_rainbow','🌈 穩定彩虹','累積簽到 30 天','sign_total',30,'ice_crystal')]:
                c.execute("""INSERT INTO web_achievements(achievement_key,name,description,condition_type,condition_value,frame_key)
                VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(achievement_key) DO NOTHING""",ach)

            for frame in [('rainbow_basic','🌈 彩虹星光框',0,False,False),('star_guard','✨ 星曜守護框',0,False,False),('ice_crystal','❄️ 冰晶彩虹框',0,False,False),('diamond_crown','💎 VIP鑽石框',0,True,False),('leader_glory','👑 Owner榮耀框',0,False,True)]:
                c.execute("""INSERT INTO web_avatar_frames(frame_key,name,price,vip_only,owner_only)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(frame_key) DO UPDATE SET price=0""", frame)
        conn.commit()
    finally:
        conn.close()


def _player_data(line_bot_api, group_id, user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute('SELECT * FROM players WHERE group_id=%s AND user_id=%s', (group_id, user_id))
            p = c.fetchone()
            if not p:
                raise HTTPException(status_code=404, detail='找不到成員資料，請先在群組內使用一次機器人。')
            c.execute('SELECT COUNT(*) AS total FROM sign_records WHERE group_id=%s AND user_id=%s', (group_id, user_id))
            total_sign = int((c.fetchone() or {}).get('total') or 0)
            c.execute('SELECT bio,region,theme FROM web_profile_settings WHERE group_id=%s AND user_id=%s', (group_id, user_id))
            profile = c.fetchone() or {}
            c.execute("SELECT id,title,content,link_url,(image_data IS NOT NULL) AS has_image,created_at FROM web_announcements WHERE group_id=%s AND is_active=1 AND (starts_at IS NULL OR starts_at<=CURRENT_TIMESTAMP) AND (ends_at IS NULL OR ends_at>=CURRENT_TIMESTAMP) ORDER BY COALESCE(sort_order,50) ASC,id DESC LIMIT 8", (group_id,))
            announcements = [dict(x) for x in c.fetchall()]
            for item in announcements:
                item['image_url'] = f"/api/rainbow/public/announcement/{int(item.get('id') or 0)}/image" if item.get('has_image') else ''
            c.execute('SELECT id,event_date,title,note FROM web_calendar_events WHERE group_id=%s AND user_id=%s ORDER BY event_date ASC LIMIT 30', (group_id, user_id))
            events = [dict(x) for x in c.fetchall()]
            c.execute('''SELECT f.frame_key,f.name,f.price,f.vip_only,f.owner_only,a.name AS achievement_name,
                               CASE WHEN uf.frame_key IS NOT NULL THEN TRUE ELSE FALSE END AS owned,
                               CASE WHEN uf.equipped=TRUE THEN TRUE ELSE FALSE END AS equipped
                        FROM web_avatar_frames f
                        LEFT JOIN web_user_frames uf ON uf.frame_key=f.frame_key AND uf.group_id=%s AND uf.user_id=%s
                        LEFT JOIN web_achievements a ON a.frame_key=f.frame_key AND a.is_active=TRUE
                        WHERE f.is_active=TRUE ORDER BY f.owner_only DESC,f.vip_only DESC,f.price ASC,f.frame_key''',(group_id,user_id))
            frames = [dict(x) for x in c.fetchall()]
            c.execute('''SELECT a.achievement_key,a.name,a.description,a.condition_type,a.condition_value,a.frame_key,
                               a.category,a.icon,a.sort_order,
                               CASE WHEN ua.achievement_key IS NOT NULL THEN TRUE ELSE FALSE END AS unlocked,
                               ua.unlocked_at,f.name AS frame_name
                        FROM web_achievements a
                        LEFT JOIN web_user_achievements ua ON ua.achievement_key=a.achievement_key AND ua.group_id=%s AND ua.user_id=%s
                        LEFT JOIN web_avatar_frames f ON f.frame_key=a.frame_key
                        WHERE a.is_active=TRUE ORDER BY a.sort_order,a.condition_value,a.achievement_key''',(group_id,user_id))
            achievements = [dict(x) for x in c.fetchall()]
    finally:
        conn.close()
    name, picture = _line_profile(line_bot_api, group_id, user_id, p.get('name'))
    role = _role(group_id, user_id)
    level = max(1, int(p.get('level') or 1))
    exp = int(p.get('exp') or 0)
    level_exp = int(p.get('level_exp') or (exp % 100))
    needed = max(100, int(100 * (1.15 ** max(level - 1, 0))))
    vip_until = str(p.get('vip_until') or '')
    is_vip = int(p.get('is_vip') or 0) == 1
    equipped_frame = next((str(x.get('frame_key')) for x in frames if x.get('equipped')), '')
    if role == 'owner':
        equipped_frame = equipped_frame or 'leader_glory'
    elif is_vip:
        equipped_frame = equipped_frame or 'diamond_crown'
    else:
        equipped_frame = equipped_frame or 'rainbow_basic'
    # VIP 到期或權限不符時只在顯示層安全退回基本框，不破壞原始收藏紀錄。
    if equipped_frame == 'diamond_crown' and not is_vip:
        equipped_frame = 'rainbow_basic'
    if equipped_frame == 'leader_glory' and role != 'owner':
        equipped_frame = 'rainbow_basic'
    for frame in frames:
        frame['available'] = bool((not frame.get('owner_only') or role == 'owner') and (not frame.get('vip_only') or is_vip))
        frame['owned'] = bool(frame.get('owned') or frame.get('frame_key') == 'rainbow_basic' or frame.get('vip_only') and is_vip or frame.get('owner_only') and role == 'owner')
        frame['equipped'] = str(frame.get('frame_key')) == equipped_frame
    for achievement in achievements:
        kind = str(achievement.get('condition_type') or 'manual')
        target = max(0, int(achievement.get('condition_value') or 0))
        current = total_sign if kind == 'sign_total' else int(p.get('today_msg_count') or 0) if kind == 'messages' else (target if achievement.get('unlocked') else 0)
        achievement['current'] = current
        achievement['target'] = target
        achievement['progress'] = 100 if achievement.get('unlocked') or target <= 0 else min(100, int(current * 100 / target))
        achievement['unlocked_at'] = str(achievement.get('unlocked_at') or '')
    return {
        'user_id': user_id, 'group_id': group_id, 'name': name, 'picture_url': picture,
        'role': role, 'level': level, 'exp': exp, 'level_exp': level_exp, 'exp_needed': needed,
        'vip': is_vip, 'vip_until': vip_until, 'title': str(p.get('custom_title') or '彩虹旅人'),
        'birthday': str(p.get('birthday') or '尚未設定'), 'streak': int(p.get('streak_count') or 0),
        'total_sign': total_sign, 'today_messages': int(p.get('today_msg_count') or 0),
        'today_stickers': int(p.get('today_sticker_count') or 0),
        'tickets': int(p.get('lottery_tickets') or p.get('tickets') or 0),
        'lucky': int(p.get('lucky') or p.get('luck_value') or 0),
        'last_wheel_date': str(p.get('last_wheel_date') or ''),
        'fortune': str(p.get('fortune_level') or '尚未占卜'), 'fortune_message': str(p.get('fortune_message') or ''),
        'bio': str(profile.get('bio') or ''), 'region': str(profile.get('region') or ''),
        'theme': str(profile.get('theme') or 'rainbow-cosmos'),
        'announcements': announcements, 'events': events,
        'frames': frames, 'equipped_frame': equipped_frame, 'achievements': achievements,
    }



def _equipped_frame(group_id, user_id):
    conn=get_connection()
    try:
        with conn.cursor() as c:
            c.execute('SELECT frame_key FROM web_user_frames WHERE group_id=%s AND user_id=%s AND equipped=TRUE LIMIT 1',(group_id,user_id))
            row=c.fetchone() or {}
            return row.get('frame_key') or 'rainbow_basic'
    except Exception:
        return 'rainbow_basic'
    finally:
        conn.close()

def _query_suffix(request):
    return '?' + urlencode({k: request.query_params[k] for k in ('uid','gid','exp','sig') if request.query_params.get(k)})


def _dashboard_html():
    return r'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#120b38"><title>Rainbow Life</title>
<style>
:root{--panel:rgba(24,16,67,.82);--line:rgba(197,172,255,.38);--muted:#d6cff0}*{box-sizing:border-box}body{margin:0;color:#fff;font-family:system-ui,-apple-system,"Noto Sans TC",sans-serif;background:#07051a;min-height:100vh;overflow-x:hidden}body:before{content:"";position:fixed;inset:-25%;z-index:-3;background:radial-gradient(circle at 15% 18%,rgba(70,155,255,.32),transparent 26%),radial-gradient(circle at 80% 20%,rgba(255,86,192,.28),transparent 25%),radial-gradient(circle at 50% 85%,rgba(144,82,255,.34),transparent 28%),linear-gradient(145deg,#07051a,#16073b 55%,#07152b);animation:cosmos 14s ease-in-out infinite alternate}.stars{position:fixed;inset:0;z-index:-2;pointer-events:none;background-image:radial-gradient(circle,#fff 1px,transparent 1.6px),radial-gradient(circle,rgba(151,216,255,.9) 1px,transparent 1.8px);background-size:42px 42px,73px 73px;opacity:.22;animation:stars 28s linear infinite}@keyframes cosmos{to{transform:scale(1.07) rotate(2deg);filter:hue-rotate(15deg)}}@keyframes stars{to{background-position:220px 330px,310px 250px}}@keyframes spin{to{transform:rotate(360deg)}}@keyframes float{50%{transform:translateY(-7px)}}@keyframes shimmer{to{background-position:220% center}}.app{display:grid;grid-template-columns:230px minmax(0,1fr);min-height:100vh}.side{padding:22px 14px;border-right:1px solid rgba(155,129,255,.24);background:rgba(8,5,29,.73);backdrop-filter:blur(22px);position:sticky;top:0;height:100vh}.logo{font-size:22px;font-weight:900;margin:0 12px 24px;background:linear-gradient(90deg,#79d8ff,#d98cff,#ff8dcf,#ffe17b);background-size:220% auto;-webkit-background-clip:text;color:transparent;animation:shimmer 4s linear infinite}.nav-title{font-size:11px;letter-spacing:.14em;color:#aaa0d3;margin:20px 12px 8px}.nav button{width:100%;border:1px solid transparent;color:#f4efff;background:transparent;text-align:left;padding:12px 13px;border-radius:14px;margin:3px 0;font-size:14px}.nav button.active,.nav button:hover{background:linear-gradient(90deg,rgba(122,80,255,.78),rgba(233,74,185,.72));border-color:rgba(255,255,255,.16)}.main{padding:22px;max-width:1300px;width:100%;margin:auto}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}.top h2{margin:0;font-size:20px}.badge{padding:8px 13px;border-radius:999px;background:rgba(42,29,100,.72);border:1px solid var(--line);font-size:12px}.card{border:1px solid var(--line);background:linear-gradient(155deg,rgba(31,21,80,.84),rgba(15,11,48,.80));border-radius:22px;padding:17px;box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 18px 50px rgba(0,0,0,.23);backdrop-filter:blur(17px)}.card h3{margin:0 0 12px}.version-mark{font-size:11px;color:#cfc4ff;text-align:right;margin:-6px 2px 8px;opacity:.75}.dashboard-carousel{display:none;margin-bottom:14px;background:linear-gradient(125deg,rgba(70,30,130,.92),rgba(105,35,125,.84))}.dashboard-carousel.show{display:block}.carousel-head{display:flex;justify-content:space-between;align-items:center}.dots{display:flex;gap:5px}.dot{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.3)}.dot.active{width:18px;border-radius:8px;background:#fff}.slide{min-height:118px;display:none;align-items:center;gap:14px}.slide.active{display:flex}.slide-icon{font-size:42px;width:62px;height:62px;display:grid;place-items:center;border-radius:18px;background:rgba(255,255,255,.1)}.slide b{font-size:22px}.slide small{display:block;color:var(--muted);margin-bottom:5px}.hero{position:relative;overflow:hidden;min-height:300px;padding:27px;display:grid;grid-template-columns:210px 1fr;gap:25px;align-items:center;background:linear-gradient(125deg,rgba(34,21,88,.94),rgba(62,38,143,.84) 52%,rgba(137,48,147,.79))}.avatar-wrap{position:relative;display:grid;place-items:center;animation:float 5s ease-in-out infinite}.avatar-wrap:before{content:"";position:absolute;width:212px;height:212px;border-radius:50%;background:conic-gradient(#6ed8ff,#9a72ff,#ff68c8,#ffd86a,#6ed8ff);animation:spin 7s linear infinite;box-shadow:0 0 40px rgba(177,111,255,.7)}.avatar-wrap.leader:before,.avatar-wrap.owner:before{width:222px;height:222px;background:conic-gradient(#ffe98a,#ffb32f,#ff6dcc,#8edfff,#fff4a8,#ffe98a);box-shadow:0 0 24px #ffd45f,0 0 55px rgba(255,93,203,.8)}.avatar-wrap.leader:after,.avatar-wrap.owner:after{content:"✦  ✧  ✦";position:absolute;z-index:5;bottom:-25px;color:#ffe688;font-size:20px;letter-spacing:10px;text-shadow:0 0 10px #fff,0 0 20px #ffbd42;animation:float 2.5s ease-in-out infinite}.avatar{position:relative;z-index:2;width:194px;height:194px;border-radius:50%;object-fit:cover;object-position:center;background:#17103f;border:7px solid #100b33}.crown{position:absolute;z-index:6;top:-35px;left:50%;transform:translateX(-50%);font-size:49px;filter:drop-shadow(0 0 12px #ffd75a);animation:float 2.7s ease-in-out infinite}.hero-info{min-width:0}.eyebrow{font-size:11px;letter-spacing:.16em;color:#d7ccff}.hero h1{font-size:35px;margin:4px 0 8px;background:linear-gradient(90deg,#fff,#cfbaff,#ff9fd8);-webkit-background-clip:text;color:transparent}.title-pill{display:inline-flex;padding:7px 12px;border:1px solid rgba(255,255,255,.18);border-radius:999px;background:rgba(12,8,43,.42)}.personal-card{margin-top:14px;background:linear-gradient(135deg,rgba(43,27,105,.92),rgba(95,34,119,.82));min-height:160px}.personal-carousel{margin-top:17px}.personal-slide{display:none}.personal-slide.active{display:block}.personal-slide small{color:var(--muted)}.personal-value{font-size:20px;font-weight:800;margin:5px 0}.level-row{display:flex;justify-content:space-between;gap:10px}.progress{height:13px;background:rgba(7,5,30,.72);border-radius:99px;overflow:hidden;margin-top:9px;border:1px solid rgba(255,255,255,.09)}.progress i{display:block;min-width:10px;height:100%;background:linear-gradient(90deg,#62c7ff,#9b74ff,#ff68c8,#ffd86a);background-size:220% auto;animation:shimmer 3s linear infinite;box-shadow:0 0 16px rgba(255,104,200,.8)}.grid{display:grid;grid-template-columns:1.25fr .95fr;gap:14px;margin-top:14px}.announcement{min-height:220px;position:relative;overflow:hidden;background:linear-gradient(130deg,rgba(48,29,117,.92),rgba(120,42,125,.80))}.announcement .boy{position:absolute;right:-10px;bottom:-35px;width:220px;filter:drop-shadow(0 14px 18px rgba(0,0,0,.32));animation:float 5s ease-in-out infinite}.announcement-content{max-width:64%;min-height:130px}.announcement-content h2{font-size:23px}.announcement-content p{line-height:1.7}.daily-slide{display:none;min-height:58px;padding:14px;border-radius:16px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08)}.daily-slide.active{display:block}.daily-slide small{color:var(--muted)}.daily-slide b{display:block;font-size:18px;margin-top:5px}.quick-card{margin-top:14px}.quick{display:grid;grid-template-columns:repeat(7,1fr);gap:9px}.quick button{border:1px solid rgba(178,153,255,.28);background:linear-gradient(155deg,rgba(37,25,96,.9),rgba(25,18,67,.82));color:white;border-radius:17px;padding:15px 6px;font-size:12px}.quick span{display:block;font-size:25px;margin-bottom:6px}.home-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}.summary-tile{position:relative;overflow:hidden;min-height:112px;padding:15px;border:1px solid rgba(205,187,255,.28);border-radius:20px;background:linear-gradient(145deg,rgba(47,31,112,.88),rgba(22,15,62,.86));box-shadow:inset 0 1px 0 rgba(255,255,255,.08)}.summary-tile:after{content:"";position:absolute;width:90px;height:90px;border-radius:50%;right:-36px;bottom:-45px;background:radial-gradient(circle,rgba(255,255,255,.22),transparent 68%)}.summary-tile .summary-icon{font-size:25px}.summary-tile small{display:block;color:var(--muted);margin-top:7px}.summary-tile b{display:block;font-size:19px;margin-top:3px}.summary-tile span{display:block;font-size:11px;color:#bfb6e5;margin-top:4px}.recommend-card{margin-top:14px}.recommend-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.recommend-item{display:flex;align-items:center;gap:11px;padding:13px;border-radius:16px;background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.08)}.recommend-item .recommend-icon{font-size:26px}.recommend-item b{display:block}.recommend-item small{color:var(--muted)}.quick-card .quick{grid-template-columns:repeat(3,1fr)}.quick-card .quick button{min-height:84px}.bottom button.active{color:#fff;text-shadow:0 0 12px rgba(186,142,255,.9)}.admin-zone,.owner-zone{display:none;margin-top:14px}.admin-zone.show,.owner-zone.show{display:block}.btn{border:0;border-radius:12px;padding:10px 14px;background:linear-gradient(90deg,#744dff,#e14cb9);color:#fff}.events,.admin-list{padding:0}.events li,.admin-list li{list-style:none;padding:11px 0;border-bottom:1px solid rgba(117,99,190,.35)}.sub{color:var(--muted)}.modal{display:none;position:fixed;inset:0;z-index:50;background:rgba(2,1,12,.75);place-items:center;padding:18px}.modal.show{display:grid}.dialog{width:min(520px,100%);max-height:80vh;overflow:auto;background:#171044;border:1px solid var(--line);border-radius:22px;padding:20px}.dialog input,.dialog textarea{width:100%;margin:7px 0;padding:12px;border-radius:12px;border:1px solid var(--line);background:#0c082b;color:#fff}.toast{display:none;position:fixed;left:50%;bottom:85px;transform:translateX(-50%);z-index:70;background:#20155b;border:1px solid var(--line);padding:11px 16px;border-radius:99px}.activity-manage{display:none;height:30px;padding:0 11px;border:1px solid rgba(255,255,255,.18);border-radius:999px;background:linear-gradient(90deg,rgba(116,77,255,.72),rgba(225,76,185,.68));color:#fff;font-size:11px;font-weight:800}.activity-manage.show{display:inline-flex;align-items:center}.activity-form label{display:block;margin-top:10px;font-size:12px;color:#dcd2ff}.activity-form select{width:100%;margin:7px 0;padding:12px;border-radius:12px;border:1px solid var(--line);background:#0c082b;color:#fff}.activity-check{display:flex!important;align-items:center;gap:8px}.activity-check input{width:auto!important}.bottom{display:none}.announcement-content{max-width:100%;min-height:160px}.announcement-slide{display:none;position:relative;min-height:180px;border-radius:17px;overflow:hidden}.announcement-slide.active{display:grid}.announcement-slide.has-image{grid-template-columns:minmax(0,1fr) minmax(180px,42%);align-items:stretch;background:rgba(7,5,30,.28);border:1px solid rgba(255,255,255,.1)}.announcement-copy{padding:18px;align-self:center;position:relative;z-index:2}.announcement-copy h2{margin:0 0 8px;font-size:23px}.announcement-copy p{margin:0;line-height:1.7;white-space:pre-wrap}.announcement-image{width:100%;height:100%;min-height:180px;object-fit:cover;display:block}.announcement-link{display:inline-flex;margin-top:12px;padding:8px 12px;border-radius:999px;color:#fff;text-decoration:none;background:linear-gradient(90deg,#744dff,#e14cb9);font-size:12px}.announcement-upload-preview{display:none;width:100%;max-height:230px;object-fit:contain;margin:9px 0 12px;border-radius:14px;background:#090622;border:1px solid var(--line)}.upload-hint{display:block;color:var(--muted);font-size:12px;margin:3px 0 9px}.dialog input[type=file]{padding:9px}.announcement.has-upload .boy{display:none}.announcement-editor{display:grid;grid-template-columns:minmax(0,1fr) minmax(290px,.95fr);gap:15px;align-items:start}.announcement-fields{display:grid;gap:9px}.announcement-fields label{font-size:12px;color:#d9d0f4}.announcement-file-meta{display:none;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;padding:10px;border-radius:14px;background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.08);font-size:11px;color:#dcd4f5}.announcement-file-meta.show{display:grid}.announcement-file-meta b{display:block;color:#fff;font-size:12px;margin-top:2px}.preview-panel{position:sticky;top:12px;padding:12px;border-radius:18px;background:rgba(8,5,30,.76);border:1px solid var(--line)}.preview-panel-head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:9px}.preview-panel-head b{font-size:13px}.preview-state{font-size:10px;color:#ffe28b;padding:5px 8px;border-radius:999px;background:rgba(255,197,70,.12);border:1px solid rgba(255,222,139,.25)}.preview-tools{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:9px}.preview-tools button{border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.06);color:#fff;border-radius:10px;padding:6px 9px;font-size:11px}.preview-tools button.active{background:linear-gradient(90deg,#744dff,#e14cb9)}.announcement-live-shell{width:100%;margin:auto;transition:max-width .25s ease}.announcement-live-shell.mobile{max-width:340px}.announcement-live-card{position:relative;overflow:hidden;min-height:250px;border-radius:18px;background:linear-gradient(135deg,rgba(49,28,118,.96),rgba(116,39,128,.9));border:1px solid rgba(255,255,255,.16);box-shadow:0 16px 35px rgba(0,0,0,.28)}.announcement-live-card.playing:after{content:"";position:absolute;inset:-60% -100%;background:linear-gradient(110deg,transparent 42%,rgba(255,255,255,.22) 50%,transparent 58%);animation:previewSweep 1.5s ease}.announcement-live-image{display:none;width:100%;height:175px;object-fit:cover;background:#090622}.announcement-live-image.show{display:block}.announcement-live-copy{padding:15px}.announcement-live-copy h4{font-size:19px;margin:0 0 7px}.announcement-live-copy p{font-size:13px;line-height:1.55;margin:0;white-space:pre-wrap;color:#eee8ff}.announcement-live-link{display:none;margin-top:11px;padding:7px 10px;border-radius:999px;background:linear-gradient(90deg,#744dff,#e14cb9);font-size:11px}.announcement-live-link.show{display:inline-flex}.image-warning{display:none;color:#ffb6c8;font-size:11px;margin-top:4px}.image-warning.show{display:block}@keyframes previewSweep{from{transform:translateX(-35%)}to{transform:translateX(35%)}}@media(max-width:760px){.announcement-editor{grid-template-columns:1fr}.preview-panel{position:static}}@media(max-width:760px){.app{display:block}.side{display:none}.main{padding:12px 12px 85px}.top h2{font-size:17px}.hero{grid-template-columns:1fr;text-align:center;padding:24px 17px}.avatar{width:174px;height:174px}.avatar-wrap:before{width:190px;height:190px}.avatar-wrap.leader:before,.avatar-wrap.owner:before{width:202px;height:202px}.grid{grid-template-columns:1fr}.home-summary{grid-template-columns:repeat(2,1fr)}.recommend-list{grid-template-columns:1fr}.announcement-content{max-width:66%}.announcement .boy{width:170px}.quick{grid-template-columns:repeat(3,1fr)}.bottom{display:grid;grid-template-columns:repeat(5,1fr);position:fixed;left:0;right:0;bottom:0;z-index:30;background:rgba(9,6,31,.94);backdrop-filter:blur(18px);border-top:1px solid var(--line);padding:8px 8px calc(8px + env(safe-area-inset-bottom))}.bottom button{border:0;background:transparent;color:#fff}.bottom span{display:block;font-size:21px}}

.life-push{position:relative;margin-top:14px;overflow:hidden;padding:18px;background:linear-gradient(145deg,rgba(30,20,88,.86),rgba(91,34,139,.80));border-color:rgba(226,205,255,.46);box-shadow:0 22px 60px rgba(7,3,31,.38),inset 0 1px 0 rgba(255,255,255,.13)}.life-push:before{content:"";position:absolute;inset:-1px;pointer-events:none;background:radial-gradient(circle at 12% 10%,rgba(102,210,255,.17),transparent 28%),radial-gradient(circle at 88% 15%,rgba(255,111,205,.17),transparent 28%)}.life-push-head{position:relative;display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:13px}.life-push-head h3{margin:0;font-size:19px}
.smart-gradient-title,#home .card h3,#home .carousel-head h3,#home .quick-card h3,#home>.version-mark+section h3{display:inline-block!important;background-image:linear-gradient(90deg,#8fe6ff 0%,#c8b6ff 24%,#ff9ed8 50%,#ffe48a 76%,#8fe6ff 100%)!important;background-size:260% 100%!important;background-position:0% center;-webkit-background-clip:text!important;background-clip:text!important;-webkit-text-fill-color:transparent!important;color:transparent!important;animation:homeTitleFlow 4s linear infinite!important;filter:drop-shadow(0 0 5px rgba(143,230,255,.42)) drop-shadow(0 0 10px rgba(255,158,216,.32));letter-spacing:.02em;font-weight:900}.smart-gradient-title::selection{background:#9b75ff;color:#fff;-webkit-text-fill-color:#fff}
#home .card h3::selection,#home .carousel-head h3::selection,#home .quick-card h3::selection{color:#fff;background:rgba(143,112,255,.55)}
@keyframes homeTitleFlow{0%{background-position:0% center}100%{background-position:240% center}}.life-push-note{font-size:11px;color:#e7deff;padding:6px 10px;border:1px solid rgba(255,255,255,.13);border-radius:999px;background:rgba(12,8,43,.30)}.life-head-tools{display:flex;align-items:center;gap:7px;flex-wrap:wrap;justify-content:flex-end}.life-sync-badge{font-size:11px;color:#effcff;padding:6px 10px;border:1px solid rgba(125,229,255,.26);border-radius:999px;background:rgba(10,33,65,.44)}.life-sync-badge.warn{color:#fff3c4;border-color:rgba(255,218,112,.35);background:rgba(86,57,12,.42)}.life-sync-badge.error{color:#ffd5e5;border-color:rgba(255,120,169,.34);background:rgba(91,20,50,.42)}.life-refresh{height:30px;padding:0 11px;border:1px solid rgba(255,255,255,.18);border-radius:999px;background:rgba(18,12,61,.52);color:#fff;font-size:11px;font-weight:800;cursor:pointer}.life-refresh:disabled{opacity:.55;cursor:wait}.life-refresh:not(:disabled):hover{background:rgba(125,77,220,.62)}.life-carousel{position:relative;overflow:hidden;border-radius:22px;touch-action:pan-y;border:1px solid rgba(255,255,255,.16);box-shadow:0 16px 35px rgba(6,3,28,.28)}.life-track{display:flex;transition:transform .52s cubic-bezier(.22,.75,.25,1)}.life-slide{position:relative;isolation:isolate;min-width:100%;min-height:196px;padding:20px;overflow:hidden;background:linear-gradient(135deg,rgba(18,12,61,.78),rgba(60,26,102,.67));display:grid;grid-template-columns:72px minmax(0,1fr);gap:16px;align-items:center}.life-slide:before{content:"";position:absolute;z-index:-1;inset:0;background:radial-gradient(circle at 85% 15%,rgba(255,255,255,.12),transparent 31%)}.life-slide:after{content:"";position:absolute;z-index:-1;left:0;right:0;bottom:0;height:3px;background:linear-gradient(90deg,#67dcff,#9b75ff,#ff75cc,#ffe17c);opacity:.8}.life-card-weather{background:linear-gradient(135deg,rgba(20,49,100,.86),rgba(63,38,130,.75))}.life-card-family{background:linear-gradient(135deg,rgba(24,89,74,.80),rgba(34,67,124,.74))}.life-card-seven{background:linear-gradient(135deg,rgba(26,94,82,.78),rgba(45,54,121,.76))}.life-card-mcd{background:linear-gradient(135deg,rgba(121,51,39,.77),rgba(101,37,89,.72))}.life-card-tra,.life-card-thsr{background:linear-gradient(135deg,rgba(27,63,111,.82),rgba(61,47,130,.75))}.life-card-holiday{background:linear-gradient(135deg,rgba(105,56,111,.80),rgba(129,64,72,.73))}.life-card-rainbow{background:linear-gradient(135deg,rgba(61,39,132,.82),rgba(140,48,126,.76))}.life-push-icon{font-size:39px;width:66px;height:66px;border-radius:21px;display:grid;place-items:center;background:linear-gradient(145deg,rgba(255,255,255,.18),rgba(255,255,255,.07));border:1px solid rgba(255,255,255,.18);box-shadow:inset 0 1px 0 rgba(255,255,255,.22),0 12px 26px rgba(0,0,0,.20);animation:float 5.5s ease-in-out infinite}.life-copy{min-width:0}.life-copy>b{display:block;font-size:21px;margin-bottom:8px;letter-spacing:.02em;text-shadow:0 2px 13px rgba(0,0,0,.26)}.life-push-status{min-height:43px;font-size:14px;color:#f8f4ff;line-height:1.6}.life-push-status strong{font-size:15px}.life-meta{display:block;margin-top:8px;color:#ddd3ff;font-size:11px}.official-btn{display:inline-flex;align-items:center;justify-content:center;margin-top:12px;padding:9px 15px;border:1px solid rgba(255,255,255,.20);border-radius:999px;color:#fff;text-decoration:none;background:linear-gradient(90deg,rgba(102,79,255,.94),rgba(225,72,185,.91));box-shadow:0 8px 20px rgba(92,42,181,.28);font-weight:800;font-size:13px;cursor:pointer;transition:transform .18s ease,filter .18s ease,box-shadow .18s ease}.official-btn:hover,.official-btn:focus-visible{transform:translateY(-2px);filter:brightness(1.1);box-shadow:0 11px 26px rgba(126,64,221,.38);outline:none}.life-progress{height:4px;margin:11px 3px 0;border-radius:99px;overflow:hidden;background:rgba(255,255,255,.10)}.life-progress i{display:block;width:0;height:100%;border-radius:99px;background:linear-gradient(90deg,#69dfff,#a877ff,#ff72ca,#ffe279);box-shadow:0 0 12px rgba(255,117,204,.85)}.life-progress i.run{animation:lifeProgress 5s linear forwards}@keyframes lifeProgress{from{width:0}to{width:100%}}.life-carousel.paused+.life-progress i{animation-play-state:paused}.life-controls{position:relative;display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:12px}.life-dots{display:flex;align-items:center;gap:6px;min-width:0;flex-wrap:wrap}.life-dot{width:7px;height:7px;padding:0;border:0;border-radius:99px;background:rgba(255,255,255,.30);transition:.2s;cursor:pointer}.life-dot.active{width:24px;background:linear-gradient(90deg,#7de2ff,#e08cff,#ffe58a);box-shadow:0 0 11px rgba(217,140,255,.75)}.life-nav{display:flex;align-items:center;gap:7px}.life-nav button{min-width:34px;height:34px;border:1px solid rgba(255,255,255,.17);border-radius:12px;background:rgba(12,8,43,.48);color:#fff;font-size:21px;cursor:pointer}.life-nav .life-play{width:auto;padding:0 11px;font-size:12px;font-weight:800}.life-nav button:hover{background:rgba(123,81,223,.62)}.loading:after{content:"";display:inline-block;width:12px;height:12px;margin-left:8px;border:2px solid rgba(255,255,255,.28);border-top-color:#fff;border-radius:50%;animation:spin .8s linear infinite}@media(max-width:760px){.life-push{padding:14px}.life-push-head{align-items:flex-start;flex-direction:column}.life-head-tools{width:100%;justify-content:space-between}.life-sync-badge{flex:1}.life-refresh{flex:none}.life-push-note{max-width:142px;text-align:right;line-height:1.35}.life-slide{min-height:220px;padding:17px;grid-template-columns:58px minmax(0,1fr);gap:12px}.life-push-icon{width:54px;height:54px;border-radius:17px;font-size:31px}.life-copy>b{font-size:18px}.life-push-status{font-size:13px}.official-btn{width:100%;margin-top:13px}.life-controls{align-items:flex-start}.life-dots{padding-top:8px}.life-nav{flex-shrink:0}}@media(max-width:620px){.announcement-slide.has-image{grid-template-columns:1fr}.announcement-image{order:-1;min-height:150px;max-height:210px}.announcement-copy{padding:14px}.announcement-copy h2{font-size:19px}}@media(prefers-reduced-motion:reduce){.life-track,.official-btn{transition:none}.life-push-icon,.life-progress i.run{animation:none}}

/* Rainbow Life Step 1.1｜穩定動態主題核心 */
:root{
  --rainbow-flow:linear-gradient(90deg,#7ee8ff 0%,#9f86ff 22%,#ff8bd5 45%,#ffd67a 68%,#8ef2c2 84%,#7ee8ff 100%);
  --rainbow-flow-soft:linear-gradient(100deg,#dff8ff 0%,#d7c8ff 30%,#ffc9ea 58%,#fff0ad 82%,#dff8ff 100%);
  --glass-bg:linear-gradient(145deg,rgba(41,27,101,.78),rgba(15,11,49,.74));
  --glass-border:rgba(221,207,255,.34);
}
.logo,.top h2,.gradient-title,.card>h3,.quick-card>h3,.life-push h3{
  background-image:var(--rainbow-flow);
  background-size:260% 100%;
  -webkit-background-clip:text;
  background-clip:text;
  color:transparent;
  animation:rainbowTextFlow 8s linear infinite;
  text-shadow:0 0 24px rgba(151,126,255,.16);
}
.hero h1{
  background-image:var(--rainbow-flow-soft);
  background-size:240% 100%;
  animation:rainbowTextFlow 10s linear infinite;
}
.card,.hero,.dashboard-carousel,.life-push{
  border-color:var(--glass-border);
  backdrop-filter:blur(18px) saturate(125%);
  -webkit-backdrop-filter:blur(18px) saturate(125%);
}
.card{
  transition:transform .22s ease,border-color .22s ease,box-shadow .22s ease;
}
@media (hover:hover){
  .card:hover{
    transform:translateY(-2px);
    border-color:rgba(235,222,255,.52);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.10),0 22px 58px rgba(0,0,0,.28),0 0 28px rgba(129,93,255,.10);
  }
}
.stars:after{
  content:"";
  position:fixed;
  inset:0;
  pointer-events:none;
  background:radial-gradient(circle at 20% 25%,rgba(255,255,255,.72) 0 1px,transparent 1.8px),radial-gradient(circle at 75% 35%,rgba(171,220,255,.64) 0 1.2px,transparent 2px),radial-gradient(circle at 45% 78%,rgba(255,188,234,.58) 0 1px,transparent 1.8px);
  background-size:180px 180px,260px 260px,220px 220px;
  opacity:.30;
  animation:starPulse 6s ease-in-out infinite alternate;
}
@keyframes rainbowTextFlow{to{background-position:260% center}}
@keyframes starPulse{from{opacity:.20;transform:scale(1)}to{opacity:.42;transform:scale(1.015)}}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important;transition-duration:.001ms!important}
}

/* Step 1.3｜動態等級條與首頁最佳化 */
.level-showcase{position:relative;overflow:hidden;margin-top:14px;padding:18px 19px;background:linear-gradient(135deg,rgba(33,24,92,.94),rgba(78,35,128,.86) 55%,rgba(130,42,124,.72))}
.level-showcase:before{content:"";position:absolute;inset:-80% -30%;pointer-events:none;background:conic-gradient(from 90deg,transparent,rgba(111,217,255,.12),rgba(255,103,203,.13),rgba(255,221,112,.10),transparent);animation:levelAura 9s linear infinite}
.level-showcase>*{position:relative;z-index:1}.level-showcase-head{display:flex;justify-content:space-between;align-items:flex-end;gap:14px}.level-showcase-head small{color:#cfc6f5;letter-spacing:.14em;font-size:10px}.level-showcase h3{margin:5px 0 0;font-size:20px}.level-percent{font-size:27px;font-weight:900;background:linear-gradient(90deg,#7de3ff,#c793ff,#ff91d5,#ffe276);background-size:250% auto;-webkit-background-clip:text;color:transparent;animation:rainbowTextFlow 4s linear infinite}.level-track{position:relative;height:18px;margin-top:14px;border-radius:999px;background:rgba(6,4,29,.78);border:1px solid rgba(255,255,255,.12);overflow:visible;box-shadow:inset 0 3px 8px rgba(0,0,0,.34)}.level-track i{display:block;width:0;height:100%;border-radius:inherit;background:linear-gradient(90deg,#60d4ff,#9d78ff,#ff6dc8,#ffd96e,#60d4ff);background-size:260% auto;box-shadow:0 0 12px rgba(108,207,255,.75),0 0 24px rgba(255,96,202,.40);animation:levelFlow 3s linear infinite;transition:width 1.35s cubic-bezier(.22,.9,.28,1)}.level-track i:after{content:"";display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,transparent,rgba(255,255,255,.72),transparent);background-size:70px 100%;animation:levelSweep 2.2s linear infinite}.level-spark{position:absolute;top:50%;opacity:.72;text-shadow:0 0 8px #fff,0 0 15px #ff9fd7;animation:sparkDrift 3.2s ease-in-out infinite}.spark-one{left:22%;animation-delay:-.5s}.spark-two{left:57%;animation-delay:-1.5s}.spark-three{left:84%;animation-delay:-2.4s}.level-meta{display:flex;justify-content:space-between;gap:12px;margin-top:10px;color:#d7d0f1;font-size:12px}.level-boost{margin-top:11px;padding:10px 12px;border-radius:13px;background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.08);color:#e8e3fb;font-size:12px}.summary-tile{transition:transform .22s ease,border-color .22s ease}.summary-tile.is-ready{animation:tileReady .48s ease both}.quick button{transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.quick button:active{transform:scale(.96)}
@keyframes levelFlow{to{background-position:260% center}}@keyframes levelSweep{to{background-position:220px 0}}@keyframes levelAura{to{transform:rotate(360deg)}}@keyframes sparkDrift{0%,100%{transform:translate(-50%,-50%) scale(.7);opacity:.25}50%{transform:translate(-50%,-115%) scale(1.15);opacity:1}}@keyframes tileReady{from{opacity:.3;transform:translateY(8px)}to{opacity:1;transform:none}}
@media(max-width:620px){.level-showcase{padding:16px}.level-showcase h3{font-size:17px}.level-percent{font-size:22px}.level-meta{flex-direction:column;gap:3px}.level-track{height:16px}}

/* Step 2.1｜個人中心、名片與機器人通知共用主題核心 */
:root{--theme-bg:#10092f;--theme-surface:rgba(39,25,92,.94);--theme-surface-2:rgba(83,34,111,.88);--theme-accent:#9d76ff;--theme-accent-2:#ff72c7;--theme-text:#ffffff;--theme-muted:#d7cff2;--theme-border:rgba(208,190,255,.34);--theme-static-bg:linear-gradient(135deg,#241557 0%,#4d267c 55%,#7b2d73 100%)}
.theme-surface{color:var(--theme-text);border:1px solid var(--theme-border);background:var(--theme-static-bg);box-shadow:inset 0 1px 0 rgba(255,255,255,.10),0 18px 45px rgba(0,0,0,.24)}
.unified-theme-block{margin-top:14px}.unified-theme-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}.unified-theme-head small{color:var(--theme-muted)}.preview-switch{display:flex;gap:7px;flex-wrap:wrap}.preview-switch button{border:1px solid var(--theme-border);background:rgba(9,6,34,.48);color:#fff;padding:8px 11px;border-radius:999px}.preview-switch button.active{background:linear-gradient(90deg,var(--theme-accent),var(--theme-accent-2))}.unified-preview-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.member-card-preview,.bot-notice-preview{position:relative;overflow:hidden;min-height:210px;border-radius:22px;padding:17px}.member-card-preview:after,.bot-notice-preview:after{content:"";position:absolute;right:-35px;bottom:-45px;width:135px;height:135px;border-radius:50%;background:radial-gradient(circle,rgba(255,255,255,.22),transparent 68%)}.member-card-top{display:flex;align-items:center;gap:13px}.member-card-avatar{width:72px;height:72px;border-radius:50%;object-fit:cover;border:4px solid rgba(255,255,255,.82);background:#150d3c}.member-card-copy{min-width:0}.member-card-copy small,.bot-notice-preview small{color:var(--theme-muted)}.member-card-copy b{display:block;font-size:20px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.member-card-title{display:inline-flex;margin-top:5px;padding:5px 9px;border-radius:999px;background:rgba(8,5,29,.34);border:1px solid rgba(255,255,255,.18);font-size:12px}.member-card-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:15px}.member-card-stats div{padding:9px;border-radius:13px;background:rgba(8,5,29,.30);border:1px solid rgba(255,255,255,.12);text-align:center}.member-card-stats b{display:block}.member-card-actions{display:flex;gap:8px;margin-top:12px}.member-card-actions span,.notice-action-preview{display:inline-flex;align-items:center;justify-content:center;padding:9px 12px;border-radius:12px;background:linear-gradient(90deg,var(--theme-accent),var(--theme-accent-2));font-weight:800;font-size:12px}.bot-notice-preview{display:flex;flex-direction:column;justify-content:space-between}.notice-brand{display:flex;align-items:center;gap:9px}.notice-brand-icon{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;background:rgba(255,255,255,.15);font-size:21px}.notice-title-preview{font-size:21px;font-weight:900;margin:14px 0 7px}.notice-body-preview{line-height:1.65;color:var(--theme-muted)}.preview-note{margin-top:10px;font-size:11px;color:var(--theme-muted);text-align:right}.preview-pane[hidden]{display:none!important}@media(max-width:760px){.unified-preview-grid{grid-template-columns:1fr}.unified-theme-head{align-items:flex-start;flex-direction:column}.member-card-preview,.bot-notice-preview{min-height:190px}}

/* Step 2.3｜頭像框與 VIP 個人中心整合 */
.frame-vip-center{margin-top:14px;overflow:hidden;background:linear-gradient(145deg,rgba(37,25,96,.92),rgba(92,36,128,.82))}
.frame-vip-head{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}.frame-vip-head small{display:block;color:var(--muted);margin-top:4px}.vip-status-chip{padding:8px 12px;border-radius:999px;border:1px solid rgba(255,255,255,.18);background:rgba(15,10,51,.48);font-size:12px;font-weight:900}.vip-status-chip.active{background:linear-gradient(90deg,rgba(93,73,221,.88),rgba(221,72,181,.84));box-shadow:0 0 22px rgba(213,100,255,.28)}
.frame-vip-layout{display:grid;grid-template-columns:250px minmax(0,1fr);gap:17px;margin-top:16px}.frame-live-preview{display:grid;place-items:center;min-height:286px;border-radius:22px;border:1px solid rgba(255,255,255,.14);background:radial-gradient(circle at 50% 35%,rgba(161,116,255,.22),transparent 42%),rgba(10,7,38,.42)}.frame-avatar-shell{position:relative;width:190px;height:190px;display:grid;place-items:center}.frame-avatar-shell:before{content:"";position:absolute;inset:-9px;border-radius:50%;background:conic-gradient(#69dfff,#9c75ff,#ff72ca,#ffe176,#69dfff);box-shadow:0 0 25px rgba(154,113,255,.55);transition:.25s}.frame-avatar-shell:after{content:"";position:absolute;inset:-18px;border-radius:50%;border:2px solid rgba(255,255,255,.15);pointer-events:none}.frame-avatar-shell img{position:relative;z-index:2;width:174px;height:174px;border-radius:50%;object-fit:cover;border:7px solid #100b33;background:#17103f}.frame-avatar-shell.frame-star_guard:before{background:conic-gradient(#e9f8ff,#69dfff,#9e7dff,#e9f8ff)}.frame-avatar-shell.frame-ice_crystal:before{background:conic-gradient(#fff,#b6f4ff,#78cfff,#d8c8ff,#fff)}.frame-avatar-shell.frame-diamond_crown:before{background:conic-gradient(#8ff3ff,#f7fbff,#c692ff,#ff8fd7,#8ff3ff);box-shadow:0 0 20px #8eefff,0 0 42px rgba(202,127,255,.72)}.frame-avatar-shell.frame-leader_glory:before{background:conic-gradient(#fff4a5,#ffbd3c,#ff76cb,#83e7ff,#fff4a5);box-shadow:0 0 22px #ffd65a,0 0 46px rgba(255,108,206,.70)}.frame-avatar-shell.frame-diamond_crown:after{content:"💎  ✦  💎";display:grid;place-items:end center;padding-bottom:-2px;color:#fff;font-size:17px;letter-spacing:5px;text-shadow:0 0 9px #8defff}.frame-avatar-shell.frame-leader_glory:after{content:"👑";display:grid;place-items:start center;font-size:38px;transform:translateY(-22px);border-color:transparent;filter:drop-shadow(0 0 9px #ffd75a)}.frame-preview-copy{text-align:center;margin-top:10px}.frame-preview-copy b{display:block;font-size:17px}.frame-preview-copy small{color:var(--muted)}
.frame-shop-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.frame-option{position:relative;padding:13px;border-radius:17px;border:1px solid rgba(255,255,255,.13);background:rgba(255,255,255,.055);transition:.2s}.frame-option.active{border-color:rgba(255,231,141,.72);box-shadow:0 0 0 2px rgba(255,220,111,.12),0 12px 28px rgba(0,0,0,.18)}.frame-option.locked{opacity:.58}.frame-option-top{display:flex;justify-content:space-between;gap:8px}.frame-option b{display:block}.frame-option small{display:block;color:var(--muted);margin-top:4px}.frame-price{font-size:11px;padding:5px 8px;border-radius:999px;background:rgba(8,5,29,.48);white-space:nowrap}.frame-option button{width:100%;margin-top:10px;border:1px solid rgba(255,255,255,.17);border-radius:12px;padding:9px;background:linear-gradient(90deg,rgba(107,80,235,.92),rgba(220,72,181,.88));color:#fff;font-weight:800}.frame-option button:disabled{opacity:.55}.frame-note{margin-top:10px;padding:10px 12px;border-radius:13px;background:rgba(255,255,255,.05);color:#dcd4f5;font-size:11px}.vip-highlight{color:#ffe88d;text-shadow:0 0 8px rgba(255,221,104,.45)}
.avatar-wrap.frame-rainbow_basic:before{background:conic-gradient(#6ed8ff,#9a72ff,#ff68c8,#ffd86a,#6ed8ff)}.avatar-wrap.frame-star_guard:before{background:conic-gradient(#e9f8ff,#69dfff,#9e7dff,#e9f8ff)}.avatar-wrap.frame-ice_crystal:before{background:conic-gradient(#fff,#b6f4ff,#78cfff,#d8c8ff,#fff)}.avatar-wrap.frame-diamond_crown:before{background:conic-gradient(#8ff3ff,#fff,#c692ff,#ff8fd7,#8ff3ff);box-shadow:0 0 22px #8eefff,0 0 48px rgba(202,127,255,.72)}.avatar-wrap.frame-leader_glory:before{background:conic-gradient(#fff4a5,#ffbd3c,#ff76cb,#83e7ff,#fff4a5);box-shadow:0 0 24px #ffd45f,0 0 55px rgba(255,93,203,.8)}
.member-card-avatar.frame-ring{outline:4px solid rgba(173,131,255,.75);outline-offset:3px;box-shadow:0 0 16px rgba(255,112,205,.45)}
@media(max-width:760px){.frame-vip-head{flex-direction:column}.frame-vip-layout{grid-template-columns:1fr}.frame-live-preview{min-height:255px}.frame-shop-grid{grid-template-columns:1fr}}

/* Step 2.2｜個人中心每日資訊整合 */
.profile-daily-panel{margin-top:14px;padding:0;overflow:hidden}.profile-daily-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 17px 12px}.profile-daily-head h3{margin:0}.profile-daily-head small{color:var(--theme-muted)}.profile-daily-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:rgba(255,255,255,.10);border-top:1px solid rgba(255,255,255,.09)}.profile-daily-item{min-height:108px;padding:14px;background:linear-gradient(145deg,rgba(37,24,91,.97),rgba(21,14,59,.96));display:flex;flex-direction:column;justify-content:center}.profile-daily-item .daily-icon{font-size:23px}.profile-daily-item small{color:var(--theme-muted);margin-top:6px}.profile-daily-item b{font-size:18px;margin-top:3px;word-break:break-word}.profile-daily-item span{font-size:11px;color:#bfb6e5;margin-top:3px}.profile-daily-alert{margin:12px 17px 16px;padding:12px 13px;border-radius:15px;background:rgba(255,255,255,.065);border:1px solid rgba(255,255,255,.10);display:flex;align-items:center;gap:10px}.profile-daily-alert strong{display:block}.profile-daily-alert small{color:var(--theme-muted)}@media(max-width:900px){.profile-daily-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:520px){.profile-daily-head{align-items:flex-start;flex-direction:column}.profile-daily-grid{grid-template-columns:1fr 1fr}.profile-daily-item{min-height:98px;padding:12px}.profile-daily-item b{font-size:16px}}

/* Step 3.1 抽獎中心（開發中） */
.lottery-page{display:none}.lottery-hero{position:relative;overflow:hidden;min-height:300px;padding:28px;background:linear-gradient(135deg,rgba(54,30,132,.95),rgba(119,42,145,.88) 58%,rgba(217,70,157,.82));text-align:center}.lottery-hero:before{content:"";position:absolute;inset:-60%;background:conic-gradient(from 0deg,transparent,rgba(255,255,255,.12),transparent 28%);animation:spin 12s linear infinite}.lottery-content{position:relative;z-index:1}.lottery-lock{width:92px;height:92px;margin:0 auto 16px;display:grid;place-items:center;border-radius:28px;background:rgba(10,7,45,.48);border:1px solid rgba(255,255,255,.22);font-size:44px;box-shadow:0 0 34px rgba(255,190,87,.25)}.lottery-status{display:inline-flex;align-items:center;gap:7px;padding:7px 13px;border-radius:999px;background:rgba(255,196,72,.16);border:1px solid rgba(255,215,126,.45);color:#ffe7a8;font-weight:800}.lottery-hero h1{margin:14px 0 7px;font-size:32px}.lottery-hero p{margin:0;color:#e7dcff;line-height:1.75}.lottery-countdown{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;max-width:430px;margin:20px auto 0}.lottery-countdown div{padding:13px;border-radius:17px;background:rgba(10,7,45,.42);border:1px solid rgba(255,255,255,.12)}.lottery-countdown b{display:block;font-size:25px}.lottery-countdown small{color:#cabfff}.lottery-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}.lottery-preview-card{min-height:145px;text-align:center;display:grid;place-items:center;background:linear-gradient(150deg,rgba(45,28,108,.92),rgba(21,15,62,.9));opacity:.82}.lottery-preview-card span{font-size:34px}.lottery-preview-card b{display:block;margin-top:8px}.lottery-preview-card small{display:block;color:var(--muted);margin-top:4px}.lottery-disabled{width:100%;margin-top:14px;cursor:not-allowed;opacity:.66;background:linear-gradient(90deg,#675a86,#8e789e)!important}.lottery-note{margin-top:14px;padding:13px;border-radius:15px;background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.09);color:#dcd3fb}.dev-chip{display:inline-flex;margin-left:5px;padding:2px 7px;border-radius:999px;background:rgba(255,183,72,.17);border:1px solid rgba(255,209,119,.36);color:#ffe4a4;font-size:10px}.quick button.dev-feature{position:relative}.quick button.dev-feature:after{content:"開發中";position:absolute;right:7px;top:7px;padding:2px 6px;border-radius:999px;background:#6e4a2b;color:#ffe8b9;font-size:9px}.admin-lottery-preview{margin-top:13px;padding:15px;border-radius:18px;background:linear-gradient(135deg,rgba(57,34,130,.9),rgba(128,43,139,.8));border:1px solid rgba(255,255,255,.13)}
@media(max-width:760px){.lottery-grid{grid-template-columns:1fr}.lottery-hero{padding:22px 15px}.lottery-hero h1{font-size:26px}.lottery-countdown b{font-size:21px}}
.admin-head{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:14px}.admin-head h3{margin:0 0 4px}.admin-head small{color:var(--muted)}.admin-table-wrap{overflow:auto;border:1px solid var(--line);border-radius:16px;margin-top:14px}.admin-table{width:100%;border-collapse:collapse;min-width:720px}.admin-table th,.admin-table td{padding:11px 12px;text-align:left;border-bottom:1px solid rgba(255,255,255,.09);font-size:13px}.admin-table th{background:rgba(103,70,190,.25);position:sticky;top:0}.status-pill{display:inline-flex;padding:5px 9px;border-radius:999px;background:rgba(255,255,255,.1);font-size:12px}.status-pill.warn{background:rgba(255,183,77,.18);color:#ffd38a}.status-pill.danger{background:rgba(255,91,126,.18);color:#ffb0c2}.status-pill.ok{background:rgba(88,225,179,.17);color:#a8f5d7}.admin-filter{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.admin-filter input,.admin-filter select{background:rgba(10,7,35,.72);color:white;border:1px solid var(--line);border-radius:12px;padding:10px}.admin-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.admin-metric{padding:14px;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.05)}.admin-metric small{display:block;color:var(--muted)}.admin-metric b{font-size:24px}.achievement-admin-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.achievement-admin-card{padding:14px;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.05)}@media(max-width:720px){.admin-head{align-items:flex-start;flex-direction:column}.admin-metrics,.achievement-admin-grid{grid-template-columns:1fr 1fr}}

/* Step 3.3 重製版｜成就中心完整化 */
.step33-achievement-center{margin-top:14px}.achievement-filter{display:flex;gap:7px;flex-wrap:wrap}.achievement-filter button{border:1px solid rgba(194,174,255,.28);background:rgba(255,255,255,.055);color:#fff;border-radius:999px;padding:7px 11px}.achievement-filter button.active{background:linear-gradient(90deg,#7558ff,#d94dac)}.achievement-summary{display:flex;gap:9px;flex-wrap:wrap;margin:12px 0}.achievement-summary span{padding:8px 11px;border-radius:999px;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.1);font-size:12px}.step33-achievement-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.step33-achievement-card{padding:14px;border-radius:18px;background:rgba(255,255,255,.055);border:1px solid rgba(198,179,255,.18);position:relative;overflow:hidden}.step33-achievement-card.unlocked{border-color:rgba(255,222,113,.55);box-shadow:0 0 25px rgba(255,204,94,.1)}.achievement-card-head{display:flex;gap:10px;align-items:center}.achievement-card-icon{width:43px;height:43px;border-radius:14px;display:grid;place-items:center;font-size:23px;background:rgba(255,255,255,.08)}.achievement-card-copy b{display:block}.achievement-card-copy small{color:var(--muted)}.achievement-progress{height:9px;border-radius:99px;background:rgba(5,4,25,.7);overflow:hidden;margin:12px 0 7px}.achievement-progress i{display:block;height:100%;background:linear-gradient(90deg,#6fd7ff,#9d77ff,#ff70ca,#ffe47b)}.achievement-reward{font-size:12px;color:#d8cdf9}.achievement-state{position:absolute;right:11px;top:11px;font-size:11px;padding:5px 8px;border-radius:999px;background:rgba(255,255,255,.08)}.achievement-empty{grid-column:1/-1;color:var(--muted);padding:20px;text-align:center}.admin-form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.admin-form-grid input,.admin-form-grid select,.admin-form-grid textarea{width:100%;padding:10px;border-radius:12px;border:1px solid rgba(190,170,255,.25);background:rgba(8,5,35,.7);color:#fff}.admin-form-grid .full{grid-column:1/-1}.admin-inline-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.mini-btn{border:1px solid rgba(190,170,255,.28);background:rgba(255,255,255,.06);color:#fff;border-radius:10px;padding:6px 8px;font-size:11px}@media(max-width:720px){.step33-achievement-grid,.admin-form-grid{grid-template-columns:1fr}}

/* Step 3.4｜最高權限管理後台首頁 */
.step34-admin{display:none;background:none!important;border:0!important;padding:0!important;margin:0!important}.step34-admin.show{display:block}.step34-admin-shell{display:grid;gap:12px}.step34-admin-head{display:flex;justify-content:space-between;gap:16px;align-items:center;padding:18px;border-radius:22px;background:linear-gradient(135deg,rgba(43,25,105,.96),rgba(113,43,128,.9));border:1px solid rgba(230,207,255,.3)}.step34-admin-head h2{margin:4px 0;font-size:22px}.step34-admin-head small,.step34-kicker{color:#d9cff3}.step34-kicker{font-size:10px;letter-spacing:.14em}.step34-back{border:1px solid rgba(255,255,255,.22);background:rgba(255,255,255,.09);color:#fff;border-radius:13px;padding:10px 13px;white-space:nowrap}.step34-status{display:flex;align-items:center;justify-content:space-between;padding:11px 14px;border-radius:16px;background:rgba(25,17,68,.86);border:1px solid rgba(202,181,255,.22)}.step34-status>div{display:grid;grid-template-columns:auto auto;align-items:center;gap:3px 8px}.step34-status small{grid-column:2;color:#cfc5ea}.step34-status button{border:0;border-radius:999px;padding:8px 11px;background:rgba(255,255,255,.08);color:#fff}.step34-live{width:9px;height:9px;border-radius:50%;background:#6dffbb;box-shadow:0 0 12px #6dffbb;grid-row:1/3}.step34-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.step34-metrics article{padding:13px;border-radius:18px;background:linear-gradient(145deg,rgba(48,31,112,.92),rgba(25,17,66,.9));border:1px solid rgba(213,194,255,.24)}.step34-metrics article span{font-size:22px}.step34-metrics article small,.step34-metrics article b{display:block}.step34-metrics article small{margin-top:6px;color:#d0c6e9;font-size:11px}.step34-metrics article b{font-size:24px;margin-top:2px}.step34-menu-card,.step34-result{padding:14px;border-radius:22px;background:rgba(31,21,77,.9);border:1px solid rgba(211,190,255,.24)}.step34-section-head,.step34-result-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}.step34-section-head h3,.step34-result-head h3{margin:0}.step34-section-head small,.step34-result-head small{color:#cfc5e9}.step34-menu-viewport{overflow:hidden;touch-action:pan-y}.step34-menu-track{display:flex;transition:transform .32s ease}.step34-menu-page{min-width:100%;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px}.step34-menu-page button{min-height:98px;padding:11px 8px;border-radius:16px;border:1px solid rgba(255,255,255,.12);background:linear-gradient(145deg,rgba(255,255,255,.075),rgba(255,255,255,.025));color:#fff;text-align:left}.step34-menu-page button.active{border-color:#ffe09a;box-shadow:0 0 0 1px rgba(255,224,154,.28),0 0 18px rgba(177,111,255,.25)}.step34-menu-page button span{font-size:23px}.step34-menu-page button b,.step34-menu-page button small{display:block}.step34-menu-page button b{font-size:13px;margin-top:5px}.step34-menu-page button small{font-size:10px;color:#cec5e7;margin-top:3px}.step34-menu-dots{display:flex;gap:5px}.step34-menu-dots i{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.25)}.step34-menu-dots i.active{width:18px;border-radius:8px;background:#fff}.step34-result{min-height:220px}.step34-result #adminResult{margin-top:4px}.step34-result .admin-metrics{display:none}.step34-result .quick{grid-template-columns:repeat(2,minmax(0,1fr))}.step34-owner-only{display:none!important}body.step34-owner .step34-owner-only{display:block!important}
@media(max-width:700px){.step34-admin-head{align-items:flex-start;flex-direction:column}.step34-back{width:100%}.step34-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.step34-menu-page{grid-template-columns:repeat(2,minmax(0,1fr))}.step34-menu-page button{min-height:88px}.step34-result{padding:12px}.admin-table{min-width:680px}}
</style></head><body><div class="stars"></div><div class="app"><aside class="side"><div class="logo">🌈 Rainbow Life</div><div class="nav"><button class="active" onclick="go('home')">🏠 個人中心</button><button onclick="go('calendar')">📅 行事曆</button><button onclick="action('frame')">🏆 成就／頭像框</button><button onclick="action('fortune')">🔮 今日運勢</button><button onclick="go('lottery')">🎟️ 抽獎中心 <span class="dev-chip">開發中</span></button><div class="nav-title">管理中心</div><button class="admin-menu" onclick="openAdminWeb()">🛠️ 進入管理後台</button><div class="nav-title owner-menu">Rainbow Life 控制台</div><button class="owner-menu" onclick="go('owner')">👑 系統最高權限</button></div></aside><main class="main"><header class="top"><h2>👑 Rainbow Life 個人中心</h2><div class="badge" id="roleBadge">載入中</div></header><section id="home"><div class="version-mark">Rainbow Life・Step 3.5.3｜活動推播恢復修正版</div><section class="card dashboard-carousel" id="leaderDashboard"><div class="carousel-head"><h3>👑 群長儀表板輪播</h3><div class="dots" id="dashDots"></div></div><div id="dashSlides"><div class="slide active"><div class="slide-icon">👑</div><div><small>群長儀表板</small><b>資料載入中</b><div>正在讀取群組資訊</div></div></div></div></section><section class="card hero"><div class="avatar-wrap" id="avatarWrap"><span class="crown" id="crown">👑</span><img class="avatar" id="avatar" alt="LINE 大頭照"></div><div class="hero-info"><div class="eyebrow">RAINBOW COSMOS PROFILE</div><h1 id="name">Rainbow</h1><div class="title-pill">🌈 <span id="title">Rainbow Life</span></div></div></section><section class="card level-showcase" id="levelShowcase" aria-label="動態等級進度"><div class="level-showcase-head"><div><small>RAINBOW LEVEL JOURNEY</small><h3><span id="levelBadge">⭐</span> <span id="levelTitle">LV. -- 載入中</span></h3></div><div class="level-percent" id="levelPercent">0%</div></div><div class="level-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" id="levelTrack"><i id="levelProgress"></i><span class="level-spark spark-one">✦</span><span class="level-spark spark-two">✧</span><span class="level-spark spark-three">✦</span></div><div class="level-meta"><span id="levelExpText">EXP -- / --</span><span id="levelRemaining">距離下一級尚需 -- EXP</span></div><div class="level-boost" id="levelBoost">今日的每一點互動，都會讓彩虹再亮一點。</div></section><section class="home-summary" aria-label="首頁資訊摘要"><article class="summary-tile"><div class="summary-icon">⭐</div><small>目前等級</small><b id="homeLevel">LV. --</b><span id="homeLevelSub">讀取經驗資料中</span></article><article class="summary-tile"><div class="summary-icon">🎟️</div><small>本月抽獎券</small><b id="homeTickets">-- 張</b><span>可於抽獎中心查看來源</span></article><article class="summary-tile"><div class="summary-icon">📅</div><small>每日簽到</small><b id="homeSignIn">讀取中</b><span id="homeSignInSub">同步連續簽到紀錄</span></article><article class="summary-tile"><div class="summary-icon">👑</div><small>VIP 狀態</small><b id="homeVip">讀取中</b><span id="homeVipSub">同步會員期限</span></article></section><section class="card personal-card"><div class="carousel-head"><h3>👤 個人資訊輪播</h3><div class="dots" id="personalDots"></div></div><div id="personalSlides"><div class="personal-slide active"><small>個人資訊</small><div class="personal-value">資料載入中</div><div>正在同步你的最新資料</div></div></div></section><section class="card profile-daily-panel theme-surface" id="profileDailyPanel"><div class="profile-daily-head"><div><h3>🌈 今日個人資訊</h3><small>重要資料集中在同一區，不必等待輪播</small></div><span class="badge" id="profileTodayDate">今日</span></div><div class="profile-daily-grid"><article class="profile-daily-item"><span class="daily-icon">🔮</span><small>今日運勢</small><b id="profileDailyFortune">尚未占卜</b><span id="profileDailyFortuneSub">前往今日運勢查看</span></article><article class="profile-daily-item"><span class="daily-icon">🎡</span><small>今日轉盤</small><b id="profileDailyWheel">尚未完成</b><span id="profileDailyWheelSub">每日可完成一次</span></article><article class="profile-daily-item"><span class="daily-icon">💬</span><small>今日聊天／貼圖</small><b id="profileDailyActivity">0／0</b><span>即時同步群組活躍</span></article><article class="profile-daily-item"><span class="daily-icon">🎟️</span><small>抽獎券</small><b id="profileDailyAssets">0 張</b><span>目前不由轉盤或運勢發放</span></article><article class="profile-daily-item"><span class="daily-icon">🎂</span><small>生日與星座</small><b id="profileDailyBirthday">尚未設定</b><span id="profileDailyZodiac">設定生日後自動判斷</span></article><article class="profile-daily-item"><span class="daily-icon">🔥</span><small>連續／累積簽到</small><b id="profileDailySign">0／0 天</b><span>每日 05:00 更新</span></article><article class="profile-daily-item"><span class="daily-icon">💎</span><small>VIP 狀態</small><b id="profileDailyVip">一般會員</b><span id="profileDailyVipSub">尚未啟用 VIP</span></article><article class="profile-daily-item"><span class="daily-icon">🏷️</span><small>目前稱號</small><b id="profileDailyTitle">彩虹旅人</b><span id="profileDailyFrame">彩虹星光框</span></article></div><div class="profile-daily-alert" id="profileDailyAlert"><span style="font-size:24px">✨</span><div><strong>今日資訊同步中</strong><small>完成載入後會顯示尚未完成的事項。</small></div></div></section><section class="card step33-achievement-center" id="achievementCenter"><div class="frame-vip-head"><div><h3>🏆 成就中心</h3><small>查看已解鎖、進行中與成就獎勵；頭像框只能由成就、活動、VIP 或管理身分取得</small></div><div class="achievement-filter"><button class="active" onclick="filterAchievements('all',this)">全部</button><button onclick="filterAchievements('unlocked',this)">已解鎖</button><button onclick="filterAchievements('progress',this)">進行中</button></div></div><div class="achievement-summary" id="achievementSummary"><span>資料載入中</span></div><div class="step33-achievement-grid" id="achievementGrid"><div class="achievement-empty">正在同步成就資料…</div></div></section><section class="card frame-vip-center" id="frameVipCenter"><div class="frame-vip-head"><div><h3>🏆 成就與頭像框</h3><small>頭像框不可購買，只能由成就、活動、VIP 或管理身分解鎖</small></div><span class="vip-status-chip" id="frameVipChip">讀取中</span></div><div class="frame-vip-layout"><div><div class="frame-live-preview"><div><div class="frame-avatar-shell frame-rainbow_basic" id="frameAvatarShell"><img id="framePreviewAvatar" alt="頭像框預覽"></div><div class="frame-preview-copy"><b id="framePreviewName">載入中</b><small id="framePreviewLabel">彩虹星光框</small></div></div></div><div class="frame-note" id="frameVipNote">頭像框不可購買。VIP 到期後專屬框會安全切回一般框，已完成的成就紀錄不會消失。</div></div><div><div class="frame-shop-grid" id="frameShopGrid"><div class="frame-option"><b>頭像框資料載入中</b><small>正在同步收藏與權限</small></div></div></div></div></section><section class="card unified-theme-block" id="unifiedThemeBlock"><div class="unified-theme-head"><div><h3>🎨 個人中心一致主題預覽</h3><small>名片與機器人通知會沿用個人中心的靜態背景、文字與按鈕顏色</small></div><div class="preview-switch"><button class="active" type="button" data-preview="both" onclick="switchUnifiedPreview('both',this)">全部</button><button type="button" data-preview="card" onclick="switchUnifiedPreview('card',this)">名片</button><button type="button" data-preview="notice" onclick="switchUnifiedPreview('notice',this)">通知</button></div></div><div class="unified-preview-grid"><article class="member-card-preview theme-surface preview-pane" id="memberCardPreview"><div><div class="member-card-top"><img class="member-card-avatar" id="cardPreviewAvatar" alt="名片頭像"><div class="member-card-copy"><small>RAINBOW LIFE MEMBER CARD</small><b id="cardPreviewName">載入中</b><span class="member-card-title" id="cardPreviewTitle">🌈 彩虹旅人</span></div></div><div class="member-card-stats"><div><small>等級</small><b id="cardPreviewLevel">LV.--</b></div><div><small>VIP</small><b id="cardPreviewVip">一般</b></div><div><small>連續簽到</small><b id="cardPreviewStreak">-- 天</b></div></div></div><div class="member-card-actions"><span>個人中心</span><span id="cardAdminAction" hidden>管理中心</span></div></article><article class="bot-notice-preview theme-surface preview-pane" id="botNoticePreview"><div><div class="notice-brand"><span class="notice-brand-icon">🌈</span><div><b>Rainbow Life</b><small>機器人通知</small></div></div><div class="notice-title-preview" id="noticePreviewTitle">✨ 個人通知</div><div class="notice-body-preview" id="noticePreviewBody">正在同步你的個人資料與通知主題。</div></div><div><span class="notice-action-preview">開啟個人中心</span><div class="preview-note">預覽畫面・實際推播沿用相同主題色</div></div></article></div></section><div class="grid"><section class="card announcement"><h3>📢 公告輪播</h3><div class="announcement-content" id="announcement"></div><div class="dots" id="announcementDots"></div><img class="boy" src="/rainbow-static/rainbow_life_boy.png"></section><section class="card"><h3>✨ 每日訊息輪播</h3><div id="dailySlides"></div><div class="dots" id="dailyDots" style="margin-top:12px"></div></section></div><section class="card life-push" id="lifePushCenter"><div class="life-push-head"><h3>🌈 智慧生活資訊中心</h3><div class="life-head-tools"><span class="life-sync-badge" id="lifeSyncBadge">正在同步全部資訊…</span><button class="life-refresh" id="lifeRefreshBtn" type="button" onclick="refreshAllLifeInfo(true)">↻ 立即更新</button><button class="activity-manage admin-menu" id="activityManageBtn" type="button" onclick="openActivityManager()">⚙ 活動顯示</button></div></div><div class="life-carousel" id="lifeCarousel"><div class="life-track" id="lifeTrack"><article class="life-slide life-card-weather" id="weatherSlide" data-priority="100"><div class="life-push-icon">🚨</div><div class="life-copy"><b>氣象局即時警報</b><div class="life-push-status loading" id="weatherStatus">資訊更新中！</div><small class="life-meta" id="weatherMeta">正在取得官方資訊</small><a class="official-btn" id="weatherOfficial" href="https://www.cwa.gov.tw/V8/C/W/Warning.html" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-family" id="familySlide" data-priority="80"><div class="life-push-icon">🏪</div><div class="life-copy"><b>全家康康5</b><div class="life-push-status loading" id="familyStatus">資訊更新中！</div><small class="life-meta" id="familyMeta">正在取得官方資訊</small><a class="official-btn" id="familyOfficial" href="https://www.family.com.tw/Marketing/zh/News" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-seven" id="sevenSlide" data-priority="40"><div class="life-push-icon">7️⃣</div><div class="life-copy"><b>7-ELEVEN</b><div class="life-push-status loading" id="sevenStatus">資訊更新中！</div><small class="life-meta" id="sevenMeta">正在取得官方資訊</small><a class="official-btn" id="sevenOfficial" href="https://www.7-11.com.tw/event/index.aspx" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-mcd" id="mcdSlide" data-priority="40"><div class="life-push-icon">🍔</div><div class="life-copy"><b>麥當勞</b><div class="life-push-status loading" id="mcdStatus">資訊更新中！</div><small class="life-meta" id="mcdMeta">正在取得官方資訊</small><a class="official-btn" id="mcdOfficial" href="https://www.mcdonalds.com/tw/zh-tw/whats-hot.html" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-tra" id="traSlide" data-priority="45"><div class="life-push-icon">🚆</div><div class="life-copy"><b>台鐵最新公告</b><div class="life-push-status loading" id="traStatus">資訊更新中！</div><small class="life-meta" id="traMeta">正在取得官方資訊</small><a class="official-btn" id="traOfficial" href="https://www.railway.gov.tw/tra-tip-web/tip/tip009/tip911/newsList" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-thsr" id="thsrSlide" data-priority="45"><div class="life-push-icon">🚄</div><div class="life-copy"><b>高鐵最新公告</b><div class="life-push-status loading" id="thsrStatus">資訊更新中！</div><small class="life-meta" id="thsrMeta">正在取得官方資訊</small><a class="official-btn" id="thsrOfficial" href="https://www.thsrc.com.tw/ArticleContent/cc283668-bfd4-4e33-9f5d-788f5d7e3f80" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-game" id="pokemon_goSlide" data-priority="55"><div class="life-push-icon">🎮</div><div class="life-copy"><b>Pokémon GO 官方更新</b><div class="life-push-status loading" id="pokemon_goStatus">資訊更新中！</div><small class="life-meta" id="pokemon_goMeta">正在取得官方最新消息</small><a class="official-btn" id="pokemon_goOfficial" href="https://pokemongolive.com/zh_hant/post/" target="_blank" rel="noopener">〔點擊今日頁面〕</a></div></article><article class="life-slide life-card-game" id="aovSlide" data-priority="55"><div class="life-push-icon">⚔️</div><div class="life-copy"><b>傳說對決官方更新</b><div class="life-push-status loading" id="aovStatus">資訊更新中！</div><small class="life-meta" id="aovMeta">正在取得官方最新消息</small><a class="official-btn" id="aovOfficial" href="https://moba.garena.tw/news/" target="_blank" rel="noopener">〔點擊今日頁面〕</a></div></article><article class="life-slide life-card-holiday" id="holidaySlide" data-priority="70"><div class="life-push-icon">🎉</div><div class="life-copy"><b>節日生活提醒</b><div class="life-push-status" id="holidayStatus">正在確認近期節日</div><small class="life-meta" id="holidayMeta">依日期自動更新</small><a class="official-btn" id="holidayOfficial" href="https://www.dgpa.gov.tw/information?uid=30&pid=11633" target="_blank" rel="noopener">〔前往官網〕</a></div></article><article class="life-slide life-card-rainbow" id="rainbowNoticeSlide" data-priority="60"><div class="life-push-icon">🌈</div><div class="life-copy"><b>Rainbow Life 系統公告</b><div class="life-push-status" id="rainbowNoticeStatus">目前沒有新的系統公告</div><small class="life-meta" id="rainbowNoticeMeta">Rainbow Life 即時公告</small><button class="official-btn" type="button" onclick="go('home')">〔查看更多〕</button></div></article></div></div><div class="life-progress" aria-hidden="true"><i id="lifeProgress"></i></div><div class="life-controls"><div class="life-dots" id="lifeDots"></div><div class="life-nav"><button type="button" onclick="moveLife(-1)" aria-label="上一則">‹</button><button type="button" class="life-play" id="lifePlay" onclick="toggleLifePlay()" aria-label="暫停輪播">⏸ 暫停</button><button type="button" onclick="moveLife(1)" aria-label="下一則">›</button></div></div></section><section class="card recommend-card"><h3>✨ 為你推薦</h3><div class="recommend-list" id="recommendList"><div class="recommend-item"><div class="recommend-icon">🌈</div><div><b>正在整理今日提醒</b><small>會依照你的狀態自動更新</small></div></div></div></section><section class="card quick-card"><h3>⚡ 快捷功能</h3><div class="quick"><button onclick="action('card')"><span>🪪</span>我的名片</button><button onclick="action('fortune')"><span>🔮</span>今日運勢</button><button onclick="unavailable('每日轉盤')"><span>🎡</span>每日轉盤</button><button class="dev-feature" onclick="go('lottery')"><span>🎟️</span>抽獎中心</button><button onclick="go('calendar')"><span>📅</span>行事曆</button><button onclick="unavailable('活動中心')"><span>🎁</span>活動中心</button><button onclick="unavailable('排行榜')"><span>🏆</span>排行榜</button><button onclick="document.getElementById('announcement').scrollIntoView({behavior:'smooth'})"><span>📢</span>公告</button><button onclick="editProfile()"><span>⚙️</span>更多</button></div></section></section><section id="lottery" class="lottery-page"><section class="card lottery-hero"><div class="lottery-content"><div class="lottery-lock">🔒</div><span class="lottery-status">🚧 開發中</span><h1>Rainbow 抽獎中心</h1><p>目前正在建置中，預計 8／1 正式上線。<br>敬請期待 🌈</p><div class="lottery-countdown"><div><b id="lotteryDays">--</b><small>天</small></div><div><b id="lotteryHours">--</b><small>小時</small></div><div><b id="lotteryMinutes">--</b><small>分鐘</small></div></div><button class="btn lottery-disabled" disabled>🔒 即將開放</button></div></section><section class="lottery-grid"><article class="card lottery-preview-card"><div><span>🎁</span><b>獎品展示</b><small>正式開放後顯示活動獎品</small></div></article><article class="card lottery-preview-card"><div><span>🎟️</span><b>我的抽獎券</b><small>目前僅保留既有數量</small></div></article><article class="card lottery-preview-card"><div><span>🏆</span><b>中獎紀錄</b><small>正式開放後可查看紀錄</small></div></article></section><div class="lottery-note">ℹ️ 目前轉盤與每日運勢皆不會發放抽獎券。</div></section><section id="calendar" class="card" style="display:none"><h3>📅 我的行事曆</h3><button class="btn" onclick="eventModal()">新增提醒</button><ul class="events" id="eventList"></ul></section><section id="admin" class="admin-zone step34-admin"><div class="step34-admin-shell"><header class="step34-admin-head"><div><span class="step34-kicker">RAINBOW LIFE CONTROL CENTER</span><h2>👑 最高權限管理後台</h2><small>與個人中心共用登入狀態・所有操作依目前群組權限執行</small></div><button class="step34-back" onclick="backToProfile()">← 返回個人中心</button></header><section class="step34-status"><div><span class="step34-live"></span><b>系統運作正常</b><small id="step34RoleText">權限確認中</small></div><button onclick="loadAdmin()">↻ 重新整理</button></section><section class="step34-metrics" id="step34Metrics"><article><span>👥</span><small>成員總數</small><b>--</b></article><article><span>✅</span><small>今日已簽到</small><b>--</b></article><article><span>⚠️</span><small>需要注意</small><b>--</b></article><article><span>💬</span><small>今日聊天</small><b>--</b></article></section><section class="step34-menu-card"><div class="step34-section-head"><div><h3>管理功能</h3><small>左右滑動・每頁四項</small></div><div class="step34-menu-dots" id="step34MenuDots"></div></div><div class="step34-menu-viewport" id="step34MenuViewport"><div class="step34-menu-track" id="step34MenuTrack"><div class="step34-menu-page"><button onclick="step34Open('members',this)"><span>👥</span><b>成員管理</b><small>資料與狀態</small></button><button onclick="step34Open('inactive',this)"><span>⚠️</span><b>低活躍名單</b><small>未簽到提醒</small></button><button onclick="announcementModal()"><span>📢</span><b>公告管理</b><small>發布與圖片</small></button><button onclick="step34Open('vip',this)"><span>💎</span><b>VIP 管理</b><small>資格與期限</small></button></div><div class="step34-menu-page"><button onclick="step34Open('achievements',this)"><span>🏆</span><b>成就管理</b><small>成就與頭像框</small></button><button onclick="step34Open('permissions',this)"><span>🛡️</span><b>權限管理</b><small>管理員與禁言</small></button><button onclick="step34Open('activities',this)"><span>🎉</span><b>活動管理</b><small>活動顯示設定</small></button><button onclick="previewLotteryAdmin()"><span>🎟️</span><b>抽獎設定</b><small>功能開發中</small></button></div><div class="step34-menu-page"><button onclick="step34Open('settings',this)"><span>⚙️</span><b>系統設定</b><small>維護與功能</small></button><button onclick="step34Open('logs',this)"><span>📋</span><b>操作紀錄</b><small>管理操作追蹤</small></button><button class="step34-owner-only" onclick="go('owner')"><span>🌈</span><b>全站控制台</b><small>Owner 專屬</small></button><button onclick="backToProfile()"><span>🏠</span><b>個人中心</b><small>返回我的頁面</small></button></div></div></div></section><section class="step34-result card"><div class="step34-result-head"><div><h3 id="step34ResultTitle">📊 後台總覽</h3><small id="step34ResultSub">選擇上方功能即可開始管理</small></div></div><div id="adminResult"><p class="sub">後台資料載入中…</p></div></section></div></section><section id="owner" class="card owner-zone"><h3>👑 Rainbow Life 控制台</h3><div class="quick"><button onclick="ownerAction('overview')"><span>📊</span>系統總覽</button><button onclick="ownerAction('groups')"><span>🌈</span>所有群組</button><button onclick="ownerAction('leaders')"><span>👑</span>群長管理</button><button onclick="ownerAction('global')"><span>📢</span>全站公告</button></div><div id="ownerResult"></div></section></main></div><nav class="bottom"><button class="active" onclick="go('home')"><span>🏠</span>首頁</button><button onclick="step331OpenActivities()"><span>🎁</span>活動</button><button onclick="go('lottery')"><span>🎟️</span>抽獎</button><button onclick="document.getElementById('announcement').scrollIntoView({behavior:'smooth'})"><span>🔔</span>通知</button><button onclick="go(DATA&&DATA.role==='owner'?'owner':(DATA&&DATA.role!=='member'?'admin':'home'))"><span>👤</span>我的</button></nav><div class="modal" id="modal"><div class="dialog" id="dialog"></div></div><div class="toast" id="toast"></div>
<script>


let ACHIEVEMENT_FILTER='all';
function filterAchievements(kind,btn){ACHIEVEMENT_FILTER=kind;document.querySelectorAll('.achievement-filter button').forEach(x=>x.classList.remove('active'));if(btn)btn.classList.add('active');renderAchievementCenter()}
function renderAchievementCenter(){if(!DATA)return;let all=DATA.achievements||[],shown=all.filter(a=>ACHIEVEMENT_FILTER==='all'||(ACHIEVEMENT_FILTER==='unlocked'&&a.unlocked)||(ACHIEVEMENT_FILTER==='progress'&&!a.unlocked));let unlocked=all.filter(a=>a.unlocked).length,summary=document.getElementById('achievementSummary');if(summary)summary.innerHTML='<span>🏆 全部 '+all.length+'</span><span>✨ 已解鎖 '+unlocked+'</span><span>🌱 進行中 '+Math.max(0,all.length-unlocked)+'</span>';let grid=document.getElementById('achievementGrid');if(!grid)return;grid.innerHTML=shown.length?shown.map(a=>'<article class="step33-achievement-card '+(a.unlocked?'unlocked':'')+'"><span class="achievement-state">'+(a.unlocked?'已解鎖':'進行中')+'</span><div class="achievement-card-head"><div class="achievement-card-icon">'+esc(a.icon||'🏆')+'</div><div class="achievement-card-copy"><b>'+esc(a.name)+'</b><small>'+esc(a.description||'')+'</small></div></div><div class="achievement-progress"><i style="width:'+Number(a.progress||0)+'%"></i></div><div class="achievement-reward">進度 '+Number(a.current||0)+' / '+Number(a.target||0)+' ・ '+Number(a.progress||0)+'%<br>獎勵：'+esc(a.frame_name||'成就徽章')+(a.unlocked_at?'<br>解鎖時間：'+esc(a.unlocked_at):'')+'</div></article>').join(''):'<div class="achievement-empty">這個分類目前沒有成就。</div>'}
function applySmartGradientTitles(){document.querySelectorAll('#home h3,#home .carousel-head h3,#home .quick-card h3').forEach(function(el){el.classList.add('smart-gradient-title')});let topTitle=document.querySelector('.top h2');if(topTitle)topTitle.classList.add('smart-gradient-title')}document.addEventListener('DOMContentLoaded',applySmartGradientTitles);let DATA=null,timers=[];const Q=location.search;function api(path,opt={}){return fetch(path+Q,{...opt,headers:{'Content-Type':'application/json',...(opt.headers||{})}}).then(async r=>{let j=await r.json();if(!r.ok)throw new Error(j.detail||'操作失敗');return j})}function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function toast(t){let e=document.getElementById('toast');e.textContent=t;e.style.display='block';setTimeout(()=>e.style.display='none',2200)}function unavailable(n){toast(n+'功能尚未開放')}function closeModal(){document.getElementById('modal').classList.remove('show')}function go(id){['home','lottery','calendar','admin','owner'].forEach(x=>{let e=document.getElementById(x);if(e)e.style.display=x===id?'block':'none'});document.querySelectorAll('.nav button,.bottom button').forEach(x=>x.classList.remove('active'));if(id==='lottery')renderLotteryCountdown();if(id==='admin')loadAdmin();if(id==='owner')ownerAction('overview');window.scrollTo({top:0,behavior:'smooth'})}function renderLotteryCountdown(){let target=new Date('2026-08-01T00:00:00+08:00').getTime(),now=Date.now(),diff=Math.max(0,target-now),days=Math.floor(diff/86400000),hours=Math.floor(diff%86400000/3600000),mins=Math.floor(diff%3600000/60000);[['lotteryDays',days],['lotteryHours',hours],['lotteryMinutes',mins]].forEach(([id,v])=>{let e=document.getElementById(id);if(e)e.textContent=String(v).padStart(2,'0')})}function previewLotteryAdmin(){document.getElementById('dialog').innerHTML='<h3>🎟️ 抽獎中心後台預覽</h3><div class="admin-lottery-preview"><b>🚧 開發中，預計 8／1 上線</b><p>一般成員目前只會看到鎖定頁面與倒數資訊。</p><button class="btn lottery-disabled" disabled>🔒 即將開放</button></div><p class="sub">目前僅供預覽，尚未開放抽獎、獎項設定及抽獎券發放。</p><button class="btn" onclick="closeModal()">關閉</button>';document.getElementById('modal').classList.add('show')}setInterval(()=>{let e=document.getElementById('lottery');if(e&&e.style.display==='block')renderLotteryCountdown()},60000);function carousel(slides,dots,interval=4500){if(!slides.length)return;let i=0;function show(n){slides.forEach((e,k)=>e.classList.toggle('active',k===n));dots.forEach((e,k)=>e.classList.toggle('active',k===n))}show(0);if(slides.length>1)timers.push(setInterval(()=>{i=(i+1)%slides.length;show(i)},interval))}function makeDots(id,n){let e=document.getElementById(id);e.innerHTML=Array.from({length:n},()=>'<i class="dot"></i>').join('');return [...e.children]}function compact(n){n=Number(n)||0;return n>=1e15?(n/1e15).toFixed(2)+'Q':n>=1e12?(n/1e12).toFixed(2)+'T':n>=1e9?(n/1e9).toFixed(2)+'B':n>=1e6?(n/1e6).toFixed(2)+'M':n.toLocaleString()}async function action(name){if(name==='frame'){let e=document.getElementById('frameVipCenter');if(e){e.scrollIntoView({behavior:'smooth',block:'start'});return}}try{let j=await api('/api/rainbow/feature/'+name,{method:name==='fortune'?'POST':'GET'});showFeature(name,j);if(name==='fortune'){DATA=await api('/api/rainbow/me');renderPersonal();renderDaily()}}catch(e){toast(e.message)}}function showFeature(name,j){let h='<h3>'+esc(j.title||name)+'</h3>';if(j.message)h+='<p style="white-space:pre-wrap">'+esc(j.message)+'</p>';if(j.items)h+='<ul class="admin-list">'+j.items.map(x=>'<li><b>'+esc(x.name||x.title_name||x.item_name)+'</b>'+(x.description?'<br><span class="sub">'+esc(x.description)+'</span>':'')+(name==='frame'?'<br><button class="btn" onclick="equipFrame('+JSON.stringify(x.frame_key)+')">套用</button>':'')+'</li>').join('')+'</ul>';document.getElementById('dialog').innerHTML=h+'<button class="btn" onclick="closeModal()">關閉</button>';document.getElementById('modal').classList.add('show')}async function equipFrame(n){try{let j=await api('/api/rainbow/frame/equip',{method:'POST',body:JSON.stringify({frame_key:n})});toast(j.message);closeModal()}catch(e){toast(e.message)}}function roleText(r){return {owner:'👑 Rainbow Life Owner',leader:'👑 群長',admin:'🛡️ 管理員',member:'👤 一般成員'}[r]||r}function switchUnifiedPreview(mode,btn){document.querySelectorAll('.preview-switch button').forEach(x=>x.classList.toggle('active',x===btn));let card=document.getElementById('memberCardPreview'),notice=document.getElementById('botNoticePreview');if(card)card.hidden=mode==='notice';if(notice)notice.hidden=mode==='card'}function zodiacFromBirthday(value){let m=String(value||'').match(/(?:\d{4}[-\/])?(\d{1,2})[-\/](\d{1,2})/);if(!m)return '';let month=Number(m[1]),day=Number(m[2]),edge=[20,19,21,20,21,21,23,23,23,23,22,22],names=['摩羯座','水瓶座','雙魚座','牡羊座','金牛座','雙子座','巨蟹座','獅子座','處女座','天秤座','天蠍座','射手座'];return day<edge[month-1]?names[(month+10)%12]:names[(month+11)%12]}function isTodayDate(value){if(!value)return false;let d=new Date(value),n=new Date();return !Number.isNaN(d.getTime())&&d.getFullYear()===n.getFullYear()&&d.getMonth()===n.getMonth()&&d.getDate()===n.getDate()}function renderProfileDaily(){if(!DATA)return;let set=(id,v)=>{let e=document.getElementById(id);if(e)e.textContent=v};let now=new Date();set('profileTodayDate',now.toLocaleDateString('zh-TW',{month:'2-digit',day:'2-digit',weekday:'short'}));set('profileDailyFortune',DATA.fortune||'尚未占卜');set('profileDailyFortuneSub',DATA.fortune_message||((DATA.fortune&&DATA.fortune!=='尚未占卜')?'今日運勢已同步':'前往今日運勢查看'));let wheelDone=isTodayDate(DATA.last_wheel_date);set('profileDailyWheel',wheelDone?'今日已完成':'尚未完成');set('profileDailyWheelSub',wheelDone?'結果已同步到公告':'每日可完成一次');set('profileDailyActivity',compact(DATA.today_messages||0)+'／'+compact(DATA.today_stickers||0));set('profileDailyAssets',compact(DATA.tickets||0)+' 張');set('profileDailyBirthday',DATA.birthday||'尚未設定');set('profileDailyZodiac',zodiacFromBirthday(DATA.birthday)||'設定生日後自動判斷');set('profileDailySign',(DATA.streak||0)+'／'+(DATA.total_sign||0)+' 天');set('profileDailyVip',DATA.vip?'VIP 會員':'一般會員');set('profileDailyVipSub',DATA.vip?(DATA.vip_until||'永久會員'):'尚未啟用 VIP');set('profileDailyTitle',DATA.title||'彩虹旅人');set('profileDailyFrame',DATA.equipped_frame||'rainbow_basic');let alerts=[];if(!DATA.fortune||DATA.fortune==='尚未占卜')alerts.push('今日運勢尚未完成');if(!wheelDone)alerts.push('今日轉盤尚未完成');if(!Number(DATA.streak||0))alerts.push('今日尚未簽到');let box=document.getElementById('profileDailyAlert');if(box){box.innerHTML='<span style="font-size:24px">'+(alerts.length?'🔔':'✅')+'</span><div><strong>'+(alerts.length?'今日提醒':'今日進度已完成')+'</strong><small>'+esc(alerts.length?alerts.join('・'):'目前沒有待完成事項')+'</small></div>'}}function renderUnifiedPreviews(){if(!DATA)return;let avatar=DATA.picture_url||'/rainbow-static/rainbow_life_boy.png';let set=(id,value)=>{let e=document.getElementById(id);if(e)e.textContent=value};let img=document.getElementById('cardPreviewAvatar');if(img)img.src=avatar;set('cardPreviewName',DATA.name||'Rainbow');set('cardPreviewTitle','🌈 '+(DATA.title||'彩虹旅人'));set('cardPreviewLevel','LV.'+(DATA.level||1));set('cardPreviewVip',DATA.vip?'VIP':'一般');set('cardPreviewStreak',(DATA.streak||0)+' 天');let admin=document.getElementById('cardAdminAction');if(admin)admin.hidden=DATA.role==='member';set('noticePreviewTitle',DATA.vip?'💎 VIP 專屬通知':'✨ '+(DATA.name||'你')+' 的個人通知');set('noticePreviewBody','目前稱號：'+(DATA.title||'彩虹旅人')+'｜等級 LV.'+(DATA.level||1)+'。所有名片與機器人通知皆沿用個人中心的靜態背景與相同配色。');document.documentElement.dataset.profileTheme=DATA.theme||'rainbow-cosmos'}function frameLabel(key){let map={rainbow_basic:'🌈 彩虹星光框',star_guard:'✨ 星曜守護框',ice_crystal:'❄️ 冰晶彩虹框',diamond_crown:'💎 VIP 鑽石框',leader_glory:'👑 Owner 榮耀框'};return map[key]||key||'彩虹星光框'}function applyEquippedFrame(){if(!DATA)return;let key=DATA.equipped_frame||'rainbow_basic';let wrap=document.getElementById('avatarWrap');if(wrap){[...wrap.classList].filter(x=>x.indexOf('frame-')===0).forEach(x=>wrap.classList.remove(x));wrap.classList.add('frame-'+key)}let shell=document.getElementById('frameAvatarShell');if(shell){shell.className='frame-avatar-shell frame-'+key}let cardAvatar=document.getElementById('cardPreviewAvatar');if(cardAvatar)cardAvatar.classList.add('frame-ring');let label=document.getElementById('framePreviewLabel');if(label)label.textContent=frameLabel(key);let daily=document.getElementById('profileDailyFrame');if(daily)daily.textContent=frameLabel(key)}function renderFrameVipCenter(){if(!DATA)return;let img=document.getElementById('framePreviewAvatar');if(img)img.src=DATA.picture_url||'/rainbow-static/rainbow_life_boy.png';let name=document.getElementById('framePreviewName');if(name)name.textContent=DATA.name||'Rainbow';let chip=document.getElementById('frameVipChip');if(chip){chip.textContent=DATA.vip?'💎 VIP '+(DATA.vip_until||'永久'):'一般會員';chip.classList.toggle('active',!!DATA.vip)}let frames=DATA.frames||[];let grid=document.getElementById('frameShopGrid');if(grid){grid.innerHTML=frames.map(f=>{let locked=!f.available,active=!!f.equipped,needBuy=!f.owned&&Number(f.price||0)>0;let status=active?'使用中':(!f.available?(f.owner_only?'Owner 專屬':'需 VIP'):(needBuy?'購買並收藏':'套用'));let price=Number(f.price||0)>0?compact(f.price)+' 彩虹幣':(f.vip_only?'VIP 專屬':f.owner_only?'Owner 專屬':'免費');return '<article class="frame-option '+(active?'active ':'')+(locked?'locked':'')+'"><div class="frame-option-top"><div><b>'+esc(f.name||frameLabel(f.frame_key))+'</b><small>'+(active?'目前已同步到所有個人畫面':'靜態框・沿用個人中心主題色')+'</small></div><span class="frame-price">'+esc(price)+'</span></div><button '+(locked||active?'disabled':'')+' onclick="'+(needBuy?'purchaseFrameInline':'equipFrameInline')+'('+JSON.stringify(f.frame_key)+')">'+status+'</button></article>'}).join('')||'<div class="frame-option"><b>目前沒有可用頭像框</b></div>'}applyEquippedFrame()}async function purchaseFrameInline(key){try{let j=await api('/api/rainbow/frame/purchase',{method:'POST',body:JSON.stringify({frame_key:key})});toast(j.message||'已加入收藏');DATA=await api('/api/rainbow/me');renderFrameVipCenter();renderAchievementCenter();renderHomeSummary()}catch(e){toast(e.message)}}async function equipFrameInline(key){try{let j=await api('/api/rainbow/frame/equip',{method:'POST',body:JSON.stringify({frame_key:key})});toast(j.message||'頭像框已套用');DATA=await api('/api/rainbow/me');renderFrameVipCenter();renderUnifiedPreviews();renderProfileDaily();renderPersonal()}catch(e){toast(e.message)}}function renderPersonal(){let pct=Math.min(100,Math.max(1,Math.round(DATA.level_exp/Math.max(1,DATA.exp_needed)*100)));let items=[['⭐ 等級與經驗','LV.'+DATA.level,'<div class="level-row"><small>目前 '+compact(DATA.level_exp)+'</small><small>需要 '+compact(DATA.exp_needed)+'</small></div><div class="progress"><i style="width:'+pct+'%"></i></div>'],['💎 VIP 狀態',DATA.vip?(DATA.vip_until||'永久 VIP'):'一般會員','成就達標後可永久解鎖 VIP'],['🏆 成就進度',DATA.achievement_stage||'持續累積中','完成條件後會自動升級'],['🌈 目前稱號',DATA.title||'彩虹旅人','頭像框：'+(DATA.equipped_frame||'rainbow_basic')],['🎂 生日資訊',DATA.birthday||'尚未設定','連續簽到 '+DATA.streak+' 天'],['🔮 今日運勢',DATA.fortune||'尚未占卜',esc(DATA.fortune_message||'點選快捷鍵查看今日運勢')],['💬 今日活躍',DATA.today_messages+' 則訊息','貼圖 '+DATA.today_stickers+' 張']];let box=document.getElementById('personalSlides');box.innerHTML=items.map(x=>'<div class="personal-slide"><small>'+x[0]+'</small><div class="personal-value">'+esc(x[1])+'</div><div>'+x[2]+'</div></div>').join('');carousel([...box.children],makeDots('personalDots',items.length),4300)}async function renderDashboard(){if(DATA.role==='member')return;document.getElementById('leaderDashboard').classList.add('show');let d={members:'--',vip:'--',admins:'--',today_messages:DATA.today_messages};try{d=await api('/api/rainbow/admin/overview')}catch(e){}let items=[['👥','群組成員',d.members+' 人','目前群組成員總數'],['🤖','機器人狀態','正常運行','Rainbow Life 服務正常'],['💬','今日聊天',d.today_messages+' 則','群組今日活躍統計'],['💎','VIP 成員',d.vip+' 人','已解鎖 VIP 的成員'],['🛡️','管理團隊',d.admins+' 人','群長與管理員'],['🎂','生日提醒',DATA.birthday==='尚未設定'?'尚未設定':'已設定','個人生日：'+DATA.birthday],['📢','最新公告',(DATA.announcements||[]).length?'有新公告':'目前無公告','向左輪播查看公告內容']];let box=document.getElementById('dashSlides');box.innerHTML=items.map(x=>'<div class="slide"><div class="slide-icon">'+x[0]+'</div><div><small>'+x[1]+'</small><b>'+esc(x[2])+'</b><div>'+esc(x[3])+'</div></div></div>').join('');document.getElementById('leaderDashboard').classList.add('show');carousel([...box.children],makeDots('dashDots',items.length),4600)}function renderAnnouncements(){let a=(DATA.announcements||[]).filter(x=>!/(轉盤|輪盤|抽獎)/.test((x.title||'')+(x.content||'')));if(!a.length)a=[{title:'全新主題上線',content:'歡迎回到 Rainbow Life 個人中心。',has_image:false}];let box=document.getElementById('announcement'),card=box.closest('.announcement');card&&card.classList.toggle('has-upload',a.some(x=>x.has_image));box.innerHTML=a.map(x=>{let image=x.has_image?'<img class="announcement-image" loading="lazy" alt="'+esc(x.title||'活動公告圖片')+'" src="/api/rainbow/announcement/'+encodeURIComponent(x.id)+'/image'+Q+'">':'';let link=x.link_url?'<a class="announcement-link" href="'+esc(x.link_url)+'" target="_blank" rel="noopener">查看活動詳情</a>':'';return '<div class="announcement-slide '+(x.has_image?'has-image':'')+'"><div class="announcement-copy"><h2>'+esc(x.title)+'</h2><p>'+esc(x.content||'')+'</p>'+link+'</div>'+image+'</div>'}).join('');carousel([...box.children],makeDots('announcementDots',a.length),6200)}function renderDaily(){let items=[['🔮 今日運勢',DATA.fortune||'尚未占卜'],['💬 今日聊天',DATA.today_messages+' 則'],['🖼️ 今日貼圖',DATA.today_stickers+' 張'],['🔥 連續簽到',DATA.streak+' 天'],['🎂 生日資訊',DATA.birthday||'尚未設定']];let box=document.getElementById('dailySlides');box.innerHTML=items.map(x=>'<div class="daily-slide"><small>'+x[0]+'</small><b>'+esc(x[1])+'</b></div>').join('');carousel([...box.children],makeDots('dailyDots',items.length),4100)}function renderDynamicLevel(){let current=Math.max(0,Number(DATA.level_exp||0)),needed=Math.max(1,Number(DATA.exp_needed||100)),pct=Math.max(0,Math.min(100,Math.round(current/needed*100))),remaining=Math.max(0,needed-current),level=Math.max(1,Number(DATA.level||1)),title=String(DATA.title||'彩虹旅人');let titleEl=document.getElementById('levelTitle'),percentEl=document.getElementById('levelPercent'),bar=document.getElementById('levelProgress'),track=document.getElementById('levelTrack'),exp=document.getElementById('levelExpText'),remain=document.getElementById('levelRemaining'),badge=document.getElementById('levelBadge'),boost=document.getElementById('levelBoost');if(titleEl)titleEl.textContent='LV. '+level+' '+title;if(percentEl)percentEl.textContent=pct+'%';if(exp)exp.textContent='EXP '+compact(current)+' / '+compact(needed);if(remain)remain.textContent=remaining>0?'距離下一級尚需 '+compact(remaining)+' EXP':'已達成目前等級目標';if(track)track.setAttribute('aria-valuenow',String(pct));if(badge)badge.textContent=level>=200?'🌈':level>=100?'👑':level>=50?'✨':level>=20?'🌟':'⭐';if(boost)boost.textContent=pct>=90?'快升級了！再完成一些互動就能迎接升級特效。':pct>=60?'彩虹能量正在快速累積，繼續保持。':pct>=30?'等級旅程穩定前進中。':'今日的每一點互動，都會讓彩虹再亮一點。';if(bar){bar.style.width='0%';requestAnimationFrame(()=>requestAnimationFrame(()=>{bar.style.width=pct+'%'}))}document.querySelectorAll('.summary-tile').forEach((el,index)=>{el.classList.remove('is-ready');setTimeout(()=>el.classList.add('is-ready'),index*70)})}function renderHomeSummary(){let level=document.getElementById('homeLevel'),levelSub=document.getElementById('homeLevelSub'),tickets=document.getElementById('homeTickets'),sign=document.getElementById('homeSignIn'),signSub=document.getElementById('homeSignInSub'),vip=document.getElementById('homeVip'),vipSub=document.getElementById('homeVipSub');if(level)level.textContent='LV. '+DATA.level;if(levelSub)levelSub.textContent=compact(DATA.level_exp)+' / '+compact(DATA.exp_needed)+' EXP';if(tickets)tickets.textContent=compact(DATA.tickets||0)+' 張';if(sign)sign.textContent=Number(DATA.streak||0)>0?'已累積 '+DATA.streak+' 天':'今日尚未簽到';if(signSub)signSub.textContent=Number(DATA.streak||0)>0?'連續簽到持續中':'回到 LINE 輸入「簽到」';if(vip)vip.textContent=DATA.vip?'VIP 會員':'一般會員';if(vipSub)vipSub.textContent=DATA.vip?(DATA.vip_until||'永久會員'):'尚未啟用 VIP';let rec=[];if(!DATA.fortune||DATA.fortune==='尚未占卜')rec.push(['🔮','查看今日運勢','完成後會同步到首頁公告']);if(!Number(DATA.streak||0))rec.push(['📅','今日尚未簽到','回到 LINE 群組輸入「簽到」']);if(!Number(DATA.tickets||0))rec.push(['🎟️','本月尚無抽獎券','抽獎券來源尚未開放']);if(!rec.length)rec.push(['🌈','今日進度很完整','可以查看活動與最新公告']);let box=document.getElementById('recommendList');if(box)box.innerHTML=rec.slice(0,4).map(x=>'<div class="recommend-item"><div class="recommend-icon">'+x[0]+'</div><div><b>'+esc(x[1])+'</b><small>'+esc(x[2])+'</small></div></div>').join('')}async function load(){try{DATA=await api('/api/rainbow/me');document.getElementById('name').textContent=DATA.name;document.getElementById('title').textContent=DATA.title;document.getElementById('roleBadge').textContent=roleText(DATA.role);document.getElementById('avatar').src=DATA.picture_url||'/rainbow-static/rainbow_life_boy.png';document.getElementById('avatarWrap').classList.add(DATA.role);if(DATA.role==='member'){document.querySelectorAll('.admin-menu,.owner-menu').forEach(e=>e.style.display='none');document.getElementById('crown').style.display='none'}else{document.getElementById('admin').classList.add('show');let amb=document.getElementById('activityManageBtn');if(amb)amb.classList.add('show')}if(DATA.role==='owner'){document.querySelectorAll('.owner-menu').forEach(e=>e.style.display='block');document.getElementById('owner').classList.add('show')}try{renderDashboard()}catch(e){console.error('dashboard',e)}try{renderPersonal()}catch(e){console.error('personal',e)}try{renderProfileDaily()}catch(e){console.error('profile-daily',e)}try{renderUnifiedPreviews()}catch(e){console.error('unified-preview',e)}try{renderFrameVipCenter()}catch(e){console.error('frame-vip',e)}try{renderHomeSummary()}catch(e){console.error('home-summary',e)}try{renderDynamicLevel()}catch(e){console.error('dynamic-level',e)}try{renderAnnouncements()}catch(e){console.error('announcement',e)}try{renderDaily()}catch(e){console.error('daily',e)}try{renderEvents()}catch(e){console.error('events',e)}}catch(e){document.body.innerHTML='<div style="padding:40px;color:white;text-align:center"><h2>無法開啟 Rainbow Life</h2><p>'+esc(e.message)+'</p><p>請回到 LINE 群組重新點選「個人中心」。</p></div>'}}function renderEvents(){document.getElementById('eventList').innerHTML=(DATA.events||[]).map(e=>'<li><b>'+esc(e.event_date)+'</b>　'+esc(e.title)+'<br><span class="sub">'+esc(e.note)+'</span></li>').join('')||'<li>目前沒有提醒事項</li>'}function eventModal(){document.getElementById('dialog').innerHTML='<h3>新增提醒</h3><input id="ed" type="date"><input id="et" placeholder="提醒標題"><textarea id="en" placeholder="備註"></textarea><button class="btn" onclick="saveEvent()">儲存</button> <button class="btn" onclick="closeModal()">取消</button>';document.getElementById('modal').classList.add('show')}async function saveEvent(){try{await api('/api/rainbow/calendar',{method:'POST',body:JSON.stringify({event_date:ed.value,title:et.value,note:en.value})});closeModal();toast('已儲存提醒');DATA=await api('/api/rainbow/me');renderEvents()}catch(e){toast(e.message)}}function editProfile(){document.getElementById('dialog').innerHTML='<h3>個人設定</h3><input id="bio" placeholder="自我介紹" value="'+esc(DATA.bio)+'"><input id="region" placeholder="地區" value="'+esc(DATA.region)+'"><button class="btn" onclick="saveProfile()">儲存</button> <button class="btn" onclick="closeModal()">取消</button>';document.getElementById('modal').classList.add('show')}async function saveProfile(){try{await api('/api/rainbow/profile',{method:'POST',body:JSON.stringify({bio:bio.value,region:region.value})});closeModal();toast('個人設定已儲存');DATA=await api('/api/rainbow/me');loadWeather()}catch(e){toast(e.message)}}let ANNOUNCEMENT_IMAGE_DATA='';let ANNOUNCEMENT_IMAGE_INFO=null;const ANNOUNCEMENT_MAX_BYTES=2*1024*1024;const ANNOUNCEMENT_MAX_INPUT_BYTES=8*1024*1024;const ANNOUNCEMENT_MAX_W=1920;const ANNOUNCEMENT_MAX_H=1080;function formatBytes(n){n=Number(n||0);if(n<1024)return n+' B';if(n<1024*1024)return(n/1024).toFixed(1)+' KB';return(n/1024/1024).toFixed(2)+' MB'}function announcementModal(){document.getElementById('dialog').innerHTML='<h3>📢 新增活動推播公告</h3><div class="announcement-editor"><div class="announcement-fields"><label>公告／活動標題</label><input id="at" maxlength="100" placeholder="公告／活動標題" oninput="updateAnnouncementPreview()"><label>公告內容</label><textarea id="ac" maxlength="1000" placeholder="公告內容（可留空）" oninput="updateAnnouncementPreview()"></textarea><label>活動連結</label><input id="al" maxlength="500" placeholder="活動連結（選填，https://...）" oninput="updateAnnouncementPreview()"><label>活動圖片（選填）</label><input id="ai" type="file" accept="image/jpeg,image/png,image/webp,image/gif" onchange="previewAnnouncementImage(this)"><small class="upload-hint">最終檔案上限 2MB；JPG、PNG、WEBP 會自動縮至 1920×1080 內並壓縮。GIF 保留動畫且須小於 2MB。</small><div id="announcementFileMeta" class="announcement-file-meta"><div>原始大小<b id="afiOriginal">--</b></div><div>處理後大小<b id="afiFinal">--</b></div><div>原始尺寸<b id="afiDimensions">--</b></div><div>輸出格式<b id="afiMime">--</b></div></div><small id="announcementImageWarning" class="image-warning"></small><div><button class="btn" id="announcementSaveBtn" onclick="saveAnnouncement()">發布並顯示</button> <button class="btn" onclick="closeModal()">取消</button></div></div><aside class="preview-panel"><div class="preview-panel-head"><b>👁️ 即時預覽</b><span class="preview-state">僅預覽・尚未儲存</span></div><div class="preview-tools"><button id="previewMobileBtn" class="active" type="button" onclick="setAnnouncementPreviewMode(\'mobile\')">手機版</button><button id="previewDesktopBtn" type="button" onclick="setAnnouncementPreviewMode(\'desktop\')">電腦版</button><button type="button" onclick="replayAnnouncementPreview()">▶ 重播動畫</button></div><div id="announcementLiveShell" class="announcement-live-shell mobile"><article id="announcementLiveCard" class="announcement-live-card"><img id="announcementLiveImage" class="announcement-live-image" alt="公告圖片預覽"><div class="announcement-live-copy"><h4 id="announcementLiveTitle">活動公告標題</h4><p id="announcementLiveContent">輸入內容後，這裡會即時顯示實際公告效果。</p><span id="announcementLiveLink" class="announcement-live-link">查看活動詳情</span></div></article></div></aside></div>';ANNOUNCEMENT_IMAGE_DATA='';ANNOUNCEMENT_IMAGE_INFO=null;document.getElementById('modal').classList.add('show');updateAnnouncementPreview();replayAnnouncementPreview()}function setAnnouncementPreviewMode(mode){let shell=document.getElementById('announcementLiveShell');if(!shell)return;shell.classList.toggle('mobile',mode==='mobile');document.getElementById('previewMobileBtn').classList.toggle('active',mode==='mobile');document.getElementById('previewDesktopBtn').classList.toggle('active',mode!=='mobile')}function replayAnnouncementPreview(){let card=document.getElementById('announcementLiveCard');if(!card)return;card.classList.remove('playing');void card.offsetWidth;card.classList.add('playing');setTimeout(()=>card&&card.classList.remove('playing'),1700)}function updateAnnouncementPreview(){let title=document.getElementById('at'),content=document.getElementById('ac'),link=document.getElementById('al');if(!title)return;document.getElementById('announcementLiveTitle').textContent=title.value.trim()||'活動公告標題';document.getElementById('announcementLiveContent').textContent=content.value.trim()||'輸入內容後，這裡會即時顯示實際公告效果。';document.getElementById('announcementLiveLink').classList.toggle('show',/^https?:\/\//i.test(link.value.trim()))}function loadImageElement(dataUrl){return new Promise((resolve,reject)=>{let img=new Image();img.onload=()=>resolve(img);img.onerror=reject;img.src=dataUrl})}function fileAsDataUrl(file){return new Promise((resolve,reject)=>{let r=new FileReader();r.onload=()=>resolve(String(r.result||''));r.onerror=reject;r.readAsDataURL(file)})}async function compressAnnouncementImage(file){let originalData=await fileAsDataUrl(file);if(file.type==='image/gif'){if(file.size>ANNOUNCEMENT_MAX_BYTES)throw new Error('GIF 圖片不可超過 2MB。');return{data:originalData,mime:file.type,width:0,height:0,size:file.size,originalSize:file.size}}let img=await loadImageElement(originalData),scale=Math.min(1,ANNOUNCEMENT_MAX_W/img.naturalWidth,ANNOUNCEMENT_MAX_H/img.naturalHeight),w=Math.max(1,Math.round(img.naturalWidth*scale)),h=Math.max(1,Math.round(img.naturalHeight*scale)),canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;let ctx=canvas.getContext('2d',{alpha:true});ctx.drawImage(img,0,0,w,h);let outputMime=file.type==='image/jpeg'?'image/jpeg':'image/webp',quality=.86,data=canvas.toDataURL(outputMime,quality);while(Math.round((data.length-data.indexOf(',')-1)*.75)>ANNOUNCEMENT_MAX_BYTES&&quality>.48){quality-=.08;data=canvas.toDataURL(outputMime,quality)}let finalSize=Math.round((data.length-data.indexOf(',')-1)*.75);if(finalSize>ANNOUNCEMENT_MAX_BYTES)throw new Error('壓縮後仍超過 2MB，請選擇較小的圖片。');return{data,mime:outputMime,width:w,height:h,size:finalSize,originalSize:file.size,originalWidth:img.naturalWidth,originalHeight:img.naturalHeight}}async function previewAnnouncementImage(input){let file=input.files&&input.files[0],img=document.getElementById('announcementLiveImage'),meta=document.getElementById('announcementFileMeta'),warning=document.getElementById('announcementImageWarning');ANNOUNCEMENT_IMAGE_DATA='';ANNOUNCEMENT_IMAGE_INFO=null;warning.classList.remove('show');warning.textContent='';if(!file){img.classList.remove('show');img.removeAttribute('src');meta.classList.remove('show');return}if(!/^image\/(jpeg|png|webp|gif)$/.test(file.type)){input.value='';toast('只支援 JPG、PNG、WEBP、GIF');return}if(file.size>ANNOUNCEMENT_MAX_INPUT_BYTES){input.value='';toast('原始圖片不可超過 8MB');return}try{warning.textContent='正在最佳化圖片…';warning.classList.add('show');let info=await compressAnnouncementImage(file);ANNOUNCEMENT_IMAGE_DATA=info.data;ANNOUNCEMENT_IMAGE_INFO=info;img.src=info.data;img.classList.add('show');document.getElementById('afiOriginal').textContent=formatBytes(info.originalSize);document.getElementById('afiFinal').textContent=formatBytes(info.size);document.getElementById('afiDimensions').textContent=info.originalWidth?(info.originalWidth+'×'+info.originalHeight+' → '+info.width+'×'+info.height):'GIF 動畫保留';document.getElementById('afiMime').textContent=String(info.mime||'').replace('image/','').toUpperCase();meta.classList.add('show');warning.classList.remove('show');replayAnnouncementPreview()}catch(e){input.value='';img.classList.remove('show');meta.classList.remove('show');warning.textContent=e.message||'圖片處理失敗';warning.classList.add('show');toast(warning.textContent)}}async function saveAnnouncement(){let btn=document.getElementById('announcementSaveBtn');try{btn.disabled=true;btn.textContent='上傳中…';if(ANNOUNCEMENT_IMAGE_INFO&&ANNOUNCEMENT_IMAGE_INFO.size>ANNOUNCEMENT_MAX_BYTES)throw new Error('圖片不可超過 2MB');await api('/api/rainbow/admin/announcement',{method:'POST',body:JSON.stringify({title:at.value,content:ac.value,link_url:al.value,image_data:ANNOUNCEMENT_IMAGE_DATA})});closeModal();toast('活動公告已發布');DATA=await api('/api/rainbow/me');renderAnnouncements();loadRainbowNotice()}catch(e){toast(e.message)}finally{if(btn){btn.disabled=false;btn.textContent='發布並顯示'}}}async function loadAdmin(){if(!DATA||DATA.role==='member')return;let j=await api('/api/rainbow/admin/overview');document.getElementById('adminResult').innerHTML='<ul class="admin-list"><li>👥 成員總數：<b>'+j.members+'</b></li><li>💎 VIP：<b>'+j.vip+'</b></li><li>🛡️ 管理人員：<b>'+j.admins+'</b></li><li>💬 今日聊天：<b>'+j.today_messages+'</b></li></ul>'}async function adminAction(k){if(k==='members'){let j=await api('/api/rainbow/admin/members');document.getElementById('adminResult').innerHTML='<ul class="admin-list">'+j.items.map(x=>'<li>'+esc(x.name)+'　Lv.'+x.level+'</li>').join('')+'</ul>'}else{let j=await api('/api/rainbow/admin/'+k);document.getElementById('adminResult').innerHTML='<pre style="white-space:pre-wrap">'+esc(JSON.stringify(j,null,2))+'</pre>'}}async function ownerAction(k){if(!DATA||DATA.role!=='owner')return;let j=await api('/api/rainbow/owner/'+k);document.getElementById('ownerResult').innerHTML='<pre style="white-space:pre-wrap">'+esc(JSON.stringify(j,null,2))+'</pre>'}const LIFE_SOURCE_STATE={weather:'loading',family:'loading',seven:'loading',mcd:'loading',tra:'loading',thsr:'loading',pokemon_go:'loading',aov:'loading',holiday:'ok',rainbow:'ok'};let LIFE_REFRESHING=false;function markLifeSource(key,state){LIFE_SOURCE_STATE[key]=state;updateLifeSyncBadge()}function updateLifeSyncBadge(){let badge=document.getElementById('lifeSyncBadge');if(!badge)return;let values=Object.values(LIFE_SOURCE_STATE),ok=values.filter(x=>x==='ok').length,stale=values.filter(x=>x==='stale').length,error=values.filter(x=>x==='error').length,total=values.length;badge.classList.remove('warn','error');if(LIFE_REFRESHING){badge.textContent='正在同步全部資訊…';return}if(error){badge.classList.add('error');badge.textContent='已同步 '+ok+'/'+total+'・'+error+' 項等待重試';return}if(stale){badge.classList.add('warn');badge.textContent='已同步 '+ok+'/'+total+'・'+stale+' 項顯示備援';return}badge.textContent='● 全部 '+total+' 項已即時同步'}async function refreshAllLifeInfo(manual=false){if(LIFE_REFRESHING)return;LIFE_REFRESHING=true;let btn=document.getElementById('lifeRefreshBtn');if(btn){btn.disabled=true;btn.textContent='同步中…'}updateLifeSyncBadge();await Promise.allSettled([loadWeather(),loadFamily(),loadSeven(),loadMcd(),loadOfficial('tra','台鐵官方'),loadOfficial('thsr','高鐵官方'),loadOfficial('pokemon_go','Pokémon GO 官方'),loadOfficial('aov','傳說對決官方')]);loadHolidayNotice();loadRainbowNotice();await loadActivityOverrides();LIFE_REFRESHING=false;if(btn){btn.disabled=false;btn.textContent='↻ 立即更新'}updateLifeSyncBadge();if(manual)toast('生活資訊已完成更新')}let LIFE_INDEX=0,LIFE_TIMER=null,LIFE_START_X=0,LIFE_PAUSED=false,LIFE_DRAGGING=false;function restartLifeProgress(){let bar=document.getElementById('lifeProgress');if(!bar)return;bar.classList.remove('run');void bar.offsetWidth;if(!LIFE_PAUSED)bar.classList.add('run')}function renderLifeCarousel(){let track=document.getElementById('lifeTrack'),slides=track?[...track.children]:[],dots=document.getElementById('lifeDots');if(!track||!slides.length)return;LIFE_INDEX=(LIFE_INDEX+slides.length)%slides.length;track.style.transform='translateX(-'+(LIFE_INDEX*100)+'%)';if(dots){dots.innerHTML=slides.map((_,i)=>'<button type="button" class="life-dot '+(i===LIFE_INDEX?'active':'')+'" onclick="goLife('+i+')" aria-label="切換到第 '+(i+1)+' 則" aria-current="'+(i===LIFE_INDEX?'true':'false')+'"></button>').join('')}restartLifeProgress()}function goLife(i){LIFE_INDEX=i;renderLifeCarousel();resetLifeTimer()}function moveLife(step){LIFE_INDEX+=step;renderLifeCarousel();resetLifeTimer()}function updateLifePlayButton(){let b=document.getElementById('lifePlay'),c=document.getElementById('lifeCarousel');if(c)c.classList.toggle('paused',LIFE_PAUSED);if(!b)return;b.textContent=LIFE_PAUSED?'▶ 播放':'⏸ 暫停';b.setAttribute('aria-label',LIFE_PAUSED?'播放輪播':'暫停輪播')}function pauseLife(){LIFE_PAUSED=true;clearInterval(LIFE_TIMER);LIFE_TIMER=null;let bar=document.getElementById('lifeProgress');if(bar)bar.classList.remove('run');updateLifePlayButton()}function playLife(){LIFE_PAUSED=false;updateLifePlayButton();resetLifeTimer()}function toggleLifePlay(){LIFE_PAUSED?playLife():pauseLife()}function resetLifeTimer(){clearInterval(LIFE_TIMER);LIFE_TIMER=null;restartLifeProgress();if(!LIFE_PAUSED)LIFE_TIMER=setInterval(()=>{LIFE_INDEX++;renderLifeCarousel()},5000)}function initLifeCarousel(){let c=document.getElementById('lifeCarousel');renderLifeCarousel();resetLifeTimer();updateLifePlayButton();if(!c)return;c.addEventListener('touchstart',e=>{LIFE_START_X=e.touches[0].clientX;LIFE_DRAGGING=true},{passive:true});c.addEventListener('touchend',e=>{if(!LIFE_DRAGGING)return;LIFE_DRAGGING=false;let dx=e.changedTouches[0].clientX-LIFE_START_X;if(Math.abs(dx)>45)moveLife(dx<0?1:-1)},{passive:true});c.addEventListener('mouseenter',()=>{if(!LIFE_PAUSED){clearInterval(LIFE_TIMER);LIFE_TIMER=null}});c.addEventListener('mouseleave',()=>{if(!LIFE_PAUSED)resetLifeTimer()});c.addEventListener('focusin',()=>{if(!LIFE_PAUSED){clearInterval(LIFE_TIMER);LIFE_TIMER=null}});c.addEventListener('focusout',()=>{if(!LIFE_PAUSED)resetLifeTimer()});document.addEventListener('visibilitychange',()=>{if(document.hidden){clearInterval(LIFE_TIMER);LIFE_TIMER=null}else if(!LIFE_PAUSED){resetLifeTimer()}})}function setOfficial(id,url){let a=document.getElementById(id);if(a&&url)a.href=url}async function loadWeather(){let status=document.getElementById('weatherStatus'),meta=document.getElementById('weatherMeta');if(!status)return;try{let w=await api('/api/rainbow/weather');setOfficial('weatherOfficial',w.url);if(w.needs_setting){status.classList.remove('loading');status.innerHTML='<strong>📍 請先設定警報地區</strong><br><button class="btn" onclick="editProfile()">立即設定</button>';meta.textContent='設定後將依所在地區顯示警特報';markLifeSource('weather','ok');return;}status.classList.remove('loading');status.innerHTML='<strong>'+esc(w.warning||'目前沒有發布任何氣象警特報')+'</strong>';meta.textContent=(w.region||'')+'｜更新 '+(w.updated_at||'')+(w.stale?'｜上次成功資料':'');markLifeSource('weather',w.stale?'stale':'ok');let ws=document.getElementById('weatherSlide');if(ws)ws.dataset.priority=w.has_warning?'120':'50';sortLifeSlides();if(w.has_warning){let track=document.getElementById('lifeTrack'),slide=status.closest('.life-slide');if(track&&slide&&track.firstElementChild!==slide){track.insertBefore(slide,track.firstElementChild);LIFE_INDEX=0;renderLifeCarousel();}}}catch(e){status.textContent='資訊更新中！';meta.textContent='下一次將自動重新取得';markLifeSource('weather','error');}}async function loadFamily(){let status=document.getElementById('familyStatus'),meta=document.getElementById('familyMeta');if(!status)return;try{let f=await api('/api/rainbow/familymart');setOfficial('familyOfficial',f.url);status.classList.remove('loading');status.innerHTML='<strong>'+esc(f.title||'資訊更新中！')+'</strong>'+(f.period?'<br>'+esc(f.period):'');meta.textContent='官方活動｜更新 '+esc(f.updated_at||'')+(f.stale?'｜上次成功資料':'');markLifeSource('family',f.stale?'stale':'ok');let fs=document.getElementById('familySlide');if(fs)fs.dataset.priority=f.is_kangkang5?'95':'65';sortLifeSlides();}catch(e){status.textContent='資訊更新中！';meta.textContent='下一次將自動重新取得';markLifeSource('family','error');}}async function loadSeven(){let status=document.getElementById('sevenStatus'),meta=document.getElementById('sevenMeta');if(!status)return;try{let f=await api('/api/rainbow/seven');setOfficial('sevenOfficial',f.url);status.classList.remove('loading');status.innerHTML='<strong>'+esc(f.title||'資訊更新中！')+'</strong>'+(f.period?'<br>'+esc(f.period):'');meta.textContent='官方活動｜更新 '+esc(f.updated_at||'')+(f.stale?'｜上次成功資料':'');markLifeSource('seven',f.stale?'stale':'ok');}catch(e){status.textContent='資訊更新中！';meta.textContent='下一次將自動重新取得';markLifeSource('seven','error');}}async function loadMcd(){let status=document.getElementById('mcdStatus'),meta=document.getElementById('mcdMeta');if(!status)return;try{let f=await api('/api/rainbow/mcdonalds');setOfficial('mcdOfficial',f.url);status.classList.remove('loading');status.innerHTML='<strong>'+esc(f.title||'資訊更新中！')+'</strong>'+(f.period?'<br>'+esc(f.period):'');meta.textContent='官方活動｜更新 '+esc(f.updated_at||'')+(f.stale?'｜上次成功資料':'');markLifeSource('mcd',f.stale?'stale':'ok');}catch(e){status.textContent='資訊更新中！';meta.textContent='下一次將自動重新取得';markLifeSource('mcd','error');}}async function loadOfficial(kind,label){let status=document.getElementById(kind+'Status'),meta=document.getElementById(kind+'Meta');if(!status)return;try{let f=await api('/api/rainbow/official/'+kind);setOfficial(kind+'Official',f.url);status.classList.remove('loading');status.innerHTML='<strong>'+esc(f.title||'資訊更新中！')+'</strong>';meta.textContent=label+'｜更新 '+esc(f.updated_at||'')+(f.stale?'｜上次成功資料':'');markLifeSource(kind,f.stale?'stale':'ok');}catch(e){status.textContent='資訊更新中！';meta.textContent='下一次將自動重新取得';markLifeSource(kind,'error');}}function loadExpandedOfficial(){loadOfficial('tra','台鐵官方');loadOfficial('thsr','高鐵官方');loadOfficial('pokemon_go','Pokémon GO 官方');loadOfficial('aov','傳說對決官方')}function sortLifeSlides(){let track=document.getElementById('lifeTrack');if(!track)return;let active=track.children[LIFE_INDEX],slides=[...track.children];slides.sort((a,b)=>Number(b.dataset.priority||0)-Number(a.dataset.priority||0));slides.forEach(x=>track.appendChild(x));LIFE_INDEX=Math.max(0,[...track.children].indexOf(active));renderLifeCarousel()}function loadHolidayNotice(){let s=document.getElementById('holidayStatus'),m=document.getElementById('holidayMeta'),slide=document.getElementById('holidaySlide');if(!s||!slide)return;let now=new Date(),month=now.getMonth()+1,day=now.getDate(),title='近期沒有特別節日提醒',detail='依日期自動更新',priority=25;if(month===1&&day<=5){title='🎆 元旦假期期間';detail='出門前請留意交通與營業時間';priority=70}else if((month===1&&day>=20)||(month===2&&day<=20)){title='🧧 春節生活提醒';detail='留意交通、採買與店家營業時間';priority=75}else if(month===4&&day<=7){title='🌿 清明連假提醒';detail='返鄉與掃墓請留意交通資訊';priority=70}else if(month===6&&day<=15){title='🐉 端午節生活提醒';detail='留意交通與節慶活動資訊';priority=70}else if((month===9&&day>=20)||(month===10&&day<=10)){title='🌕 中秋節生活提醒';detail='留意交通、活動與店家營業時間';priority=70}else if(month===12&&day>=20){title='🎄 聖誕與跨年活動提醒';detail='外出請留意交通與人潮資訊';priority=72}slide.dataset.priority=String(priority);s.innerHTML='<strong>'+title+'</strong>';m.textContent=detail+'｜'+now.toLocaleString('zh-TW',{hour12:false});sortLifeSlides()}const ACTIVITY_KEYS={weather:'氣象警特報',family:'全家康康5',seven:'7-ELEVEN',mcd:'麥當勞',tra:'台鐵',thsr:'高鐵',pokemon_go:'Pokémon GO',aov:'傳說對決',holiday:'節日提醒',rainbow_notice:'Rainbow Life 公告'};let ACTIVITY_OVERRIDES={};function activityElements(key){let map={weather:['weatherSlide','weatherStatus','weatherMeta','weatherOfficial'],family:['familySlide','familyStatus','familyMeta','familyOfficial'],seven:['sevenSlide','sevenStatus','sevenMeta','sevenOfficial'],mcd:['mcdSlide','mcdStatus','mcdMeta','mcdOfficial'],tra:['traSlide','traStatus','traMeta','traOfficial'],thsr:['thsrSlide','thsrStatus','thsrMeta','thsrOfficial'],pokemon_go:['pokemon_goSlide','pokemon_goStatus','pokemon_goMeta','pokemon_goOfficial'],aov:['aovSlide','aovStatus','aovMeta','aovOfficial'],holiday:['holidaySlide','holidayStatus','holidayMeta',''],rainbow_notice:['rainbowNoticeSlide','rainbowNoticeStatus','rainbowNoticeMeta','']};let ids=map[key]||[];return ids.map(id=>id?document.getElementById(id):null)}function applyActivityOverrides(){Object.entries(ACTIVITY_OVERRIDES||{}).forEach(([key,o])=>{let [slide,status,meta,link]=activityElements(key);if(!slide)return;slide.style.display=Number(o.is_visible)===0?'none':'';if(o.title&&status)status.innerHTML='<strong>'+esc(o.title)+'</strong>'+(o.content?'<br>'+esc(o.content):'')+(o.period?'<br>'+esc(o.period):'');else if((o.content||o.period)&&status)status.innerHTML=status.innerHTML+(o.content?'<br>'+esc(o.content):'')+(o.period?'<br>'+esc(o.period):'');if(o.url&&link)link.href=o.url;if(o.priority!==undefined&&o.priority!==null)slide.dataset.priority=String(o.priority);if(meta&&o.updated_at)meta.textContent='自訂活動顯示｜更新 '+o.updated_at});sortLifeSlides()}async function loadActivityOverrides(){try{let j=await api('/api/rainbow/activities');ACTIVITY_OVERRIDES=j.items||{};applyActivityOverrides()}catch(e){}}function openActivityManager(){let options=Object.entries(ACTIVITY_KEYS).map(([k,v])=>'<option value="'+k+'">'+v+'</option>').join('');document.getElementById('dialog').innerHTML='<h3>⚙ 活動中心顯示管理</h3><div class="activity-form"><label>選擇活動</label><select id="activityKey" onchange="fillActivityForm()">'+options+'</select><label>顯示標題</label><input id="activityTitle" maxlength="120" placeholder="留空＝沿用自動內容"><label>補充內容</label><textarea id="activityContent" maxlength="500" placeholder="可隨時修改顯示內容"></textarea><label>活動日期</label><input id="activityPeriod" maxlength="100" placeholder="例如：2026/07/17～2026/07/21"><label>點擊連結</label><input id="activityUrl" maxlength="500" placeholder="https://..."><label>輪播順位</label><input id="activityPriority" type="number" min="0" max="999" value="50"><label class="activity-check"><input id="activityVisible" type="checkbox" checked> 顯示在活動中心</label><button class="btn" onclick="saveActivityDisplay()">儲存活動顯示</button> <button class="btn" onclick="resetActivityDisplay()">恢復自動</button> <button class="btn" onclick="closeModal()">關閉</button></div>';document.getElementById('modal').classList.add('show');fillActivityForm()}function fillActivityForm(){let k=document.getElementById('activityKey').value,o=ACTIVITY_OVERRIDES[k]||{};document.getElementById('activityTitle').value=o.title||'';document.getElementById('activityContent').value=o.content||'';document.getElementById('activityPeriod').value=o.period||'';document.getElementById('activityUrl').value=o.url||'';document.getElementById('activityPriority').value=o.priority??50;document.getElementById('activityVisible').checked=Number(o.is_visible??1)!==0}async function saveActivityDisplay(){let k=document.getElementById('activityKey').value,p={activity_key:k,title:document.getElementById('activityTitle').value,content:document.getElementById('activityContent').value,period:document.getElementById('activityPeriod').value,url:document.getElementById('activityUrl').value,priority:Number(document.getElementById('activityPriority').value||50),is_visible:document.getElementById('activityVisible').checked};try{await api('/api/rainbow/admin/activity',{method:'POST',body:JSON.stringify(p)});toast('活動顯示已更新');await loadActivityOverrides()}catch(e){toast(e.message)}}async function resetActivityDisplay(){let k=document.getElementById('activityKey').value;try{await api('/api/rainbow/admin/activity/reset',{method:'POST',body:JSON.stringify({activity_key:k})});delete ACTIVITY_OVERRIDES[k];toast('已恢復官方自動顯示');closeModal();await refreshAllLifeInfo(false)}catch(e){toast(e.message)}}function loadRainbowNotice(){let status=document.getElementById('rainbowNoticeStatus'),meta=document.getElementById('rainbowNoticeMeta'),slide=document.getElementById('rainbowNoticeSlide');if(!status||!slide)return;let items=(DATA&&DATA.announcements)||[];if(items.length){let item=items[0]||{},img=item.image_url?'<img src="'+esc(item.image_url)+'" alt="公告圖片" style="display:block;width:100%;max-height:190px;object-fit:cover;border-radius:16px;margin:0 0 12px" onerror="this.style.display=\'none\'">':'';status.innerHTML=img+'<strong>'+esc(item.title||'Rainbow Life 最新公告')+'</strong>'+(item.content?'<br>'+esc(item.content):'');meta.textContent='系統公告｜即時同步';slide.dataset.priority='85'}else{status.innerHTML='<strong>目前沒有新的系統公告</strong>';meta.textContent='Rainbow Life 即時公告';slide.dataset.priority='20'}sortLifeSlides()}applySmartGradientTitles();setInterval(()=>refreshAllLifeInfo(false),30000);
load();initLifeCarousel();setTimeout(()=>refreshAllLifeInfo(false),500);

/* Step 3.2 overrides */
function openAdminWeb(section){location.href='/admin'+(section?'?section='+encodeURIComponent(section):'')}
function backToProfile(){location.href='/player'+location.search}
const _step32RenderProfileDaily=renderProfileDaily;
renderProfileDaily=function(){_step32RenderProfileDaily();if(DATA){let e=document.getElementById('profileDailyAssets');if(e)e.textContent=compact(DATA.tickets||0)+' 張'}};
renderFrameVipCenter=function(){if(!DATA)return;let img=document.getElementById('framePreviewAvatar');if(img)img.src=DATA.picture_url||'/rainbow-static/rainbow_life_boy.png';let name=document.getElementById('framePreviewName');if(name)name.textContent=DATA.name||'Rainbow';let chip=document.getElementById('frameVipChip');if(chip){chip.textContent=DATA.vip?'💎 VIP '+(DATA.vip_until||'永久'):'一般會員';chip.classList.toggle('active',!!DATA.vip)}let grid=document.getElementById('frameShopGrid'),frames=DATA.frames||[];if(grid){grid.innerHTML=frames.map(function(f){let locked=!f.available,active=!!f.equipped,owned=!!f.owned;let source=f.vip_only?'VIP 專屬':f.owner_only?'群長專屬':(f.achievement_name||'成就／活動解鎖');let status=active?'使用中':locked?'權限限定':owned?'套用':'尚未解鎖';let disabled=locked||active||!owned;return '<article class="frame-option '+(active?'active ':'')+(disabled?'locked':'')+'"><div class="frame-option-top"><div><b>'+esc(f.name||frameLabel(f.frame_key))+'</b><small>不可購買・'+esc(source)+'</small></div><span class="frame-price">'+esc(source)+'</span></div><button class="step32-frame" data-key="'+esc(f.frame_key)+'" '+(disabled?'disabled':'')+'>'+status+'</button></article>';}).join('');grid.querySelectorAll('.step32-frame:not([disabled])').forEach(function(b){b.onclick=function(){equipFrameInline(b.dataset.key)}})}applyEquippedFrame()};
async function loadAdmin(){if(!DATA||DATA.role==='member')return;let j=await api('/api/rainbow/admin/overview');document.getElementById('adminResult').innerHTML='<div class="admin-metrics"><div class="admin-metric"><small>成員總數</small><b>'+j.members+'</b></div><div class="admin-metric"><small>今日已簽到</small><b>'+j.today_signed+'</b></div><div class="admin-metric"><small>未簽到／低活躍</small><b>'+j.attention+'</b></div><div class="admin-metric"><small>今日聊天</small><b>'+j.today_messages+'</b></div></div><p class="sub">固定 Rainbow Life 彩虹星空主題；不含商店、彩虹幣、稱號及主題設定。</p>'}
function adminTable(headers,rows){return '<div class="admin-table-wrap"><table class="admin-table"><thead><tr>'+headers.map(function(x){return '<th>'+x+'</th>'}).join('')+'</tr></thead><tbody>'+rows.join('')+'</tbody></table></div>'}

async function saveAchievementAdmin(){let p={achievement_key:document.getElementById('achKey').value,name:document.getElementById('achName').value,description:document.getElementById('achDescription').value,condition_type:document.getElementById('achType').value,condition_value:Number(document.getElementById('achValue').value||0),frame_key:document.getElementById('achFrame').value,icon:document.getElementById('achIcon').value};try{await api('/api/rainbow/admin/achievement',{method:'POST',body:JSON.stringify(p)});toast('成就已儲存');adminAction('achievements')}catch(e){toast(e.message)}}
async function editAchievementAdmin(key){try{let j=await api('/api/rainbow/admin/achievement/'+encodeURIComponent(key));document.getElementById('achKey').value=j.item.achievement_key||'';document.getElementById('achName').value=j.item.name||'';document.getElementById('achDescription').value=j.item.description||'';document.getElementById('achType').value=j.item.condition_type||'manual';document.getElementById('achValue').value=j.item.condition_value||0;document.getElementById('achFrame').value=j.item.frame_key||'';document.getElementById('achIcon').value=j.item.icon||'🏆'}catch(e){toast(e.message)}}
async function toggleAchievementAdmin(key,active){try{await api('/api/rainbow/admin/achievement/toggle',{method:'POST',body:JSON.stringify({achievement_key:key,is_active:active})});toast(active?'成就已啟用':'成就已停用');adminAction('achievements')}catch(e){toast(e.message)}}
adminAction=async function(k){
 let box=document.getElementById('adminResult');
 if(!box)return;
 box.innerHTML='<p>資料載入中…</p>';
 try{
  if(k==='members'){
   let j=await api('/api/rainbow/admin/members');
   box.innerHTML='<div class="s35-toolbar"><input class="s35-search" placeholder="搜尋成員" oninput="s35FilterCards(this.value)"><span class="status-pill ok">共 '+j.items.length+' 人</span></div><div class="s35-grid">'+j.items.map(x=>{
    const uid=JSON.stringify(x.user_id);
    const muteAction=x.is_muted?'unmute':'mute';
    return '<article class="s35-card s35-filter-item"><b>'+esc(x.name||'未命名')+'</b><small>Lv.'+Number(x.level||1)+'・'+(x.is_vip?'💎 VIP':'一般會員')+(x.admin_role?'・🛡️ '+esc(x.admin_role):'')+'</small><small>今日聊天 '+Number(x.today_messages||0)+'・最近簽到 '+esc(s35Date(x.last_sign_in))+'</small><small>'+(x.is_muted?'🔇 禁言中':'✅ 功能正常')+'</small><div class="s35-actions"><button onclick=\'s35MemberAction('+uid+',"vip_on")\'>增加 VIP</button>'+(x.is_vip?'<button onclick=\'s35MemberAction('+uid+',"vip_off")\'>取消 VIP</button>':'')+'<button class="danger" onclick=\'s35MemberAction('+uid+',"'+muteAction+'")\'>'+(x.is_muted?'解除禁言':'禁言')+'</button></div></article>';
   }).join('')+'</div>';
  }else if(k==='permissions'){
   let j=await api('/api/rainbow/admin/permissions');
   box.innerHTML='<div class="s35-form"><input id="s35PermUid" class="full" placeholder="成員 LINE User ID"><select id="s35PermRole"><option value="admin">管理員</option><option value="manager">進階管理員</option><option value="leader">群長</option></select><div class="s35-actions"><button onclick="s35Permission(false)">授予／更新權限</button><button class="danger" onclick="s35Permission(true)">移除權限</button></div></div><div class="s35-grid">'+(j.items||[]).map(x=>'<article class="s35-card"><b>🛡️ '+esc(x.role||'admin')+'</b><small>'+esc(x.user_id)+'</small></article>').join('')+'</div><p class="sub">只有 Owner 能調整管理權限。</p>';
  }else if(k==='activities'){
   let j=await api('/api/rainbow/admin/announcements');
   box.innerHTML='<div class="s35-form"><input id="s35AnnTitle" placeholder="公告標題"><input id="s35AnnLink" placeholder="活動連結（可空白）"><textarea id="s35AnnContent" class="full" placeholder="公告內容"></textarea><input id="s35AnnImage" class="full" type="file" accept="image/jpeg,image/png,image/webp,image/gif"><button class="btn full" onclick="s35CreateAnnouncement()">發布公告</button></div><button class="mini-btn" onclick="openActivityManager()">🎉 開啟活動顯示設定</button><div class="s35-grid" style="margin-top:12px">'+(j.items.length?j.items.map(x=>'<article class="s35-card"><b>'+esc(x.title)+'</b><small>'+esc(x.content||'無文字內容')+'</small><small>'+esc(x.created_at||'')+(x.has_image?'・含圖片':'')+'</small><div class="s35-actions"><button onclick="s35AnnStatus('+x.id+','+(!x.is_active)+')">'+(x.is_active?'暫停顯示':'重新啟用')+'</button><button class="danger" onclick="s35AnnDelete('+x.id+')">刪除</button></div></article>').join(''):'<div class="s35-empty">尚未建立公告</div>')+'</div>';
  }else if(k==='settings'){
   let j=await api('/api/rainbow/admin/settings'),x=j.item||{};
   box.innerHTML='<div class="s35-form"><label>未簽到判定天數<input id="s35Inactive" type="number" min="1" value="'+Number(x.inactive_days||7)+'"></label><label>低活躍聊天門檻<input id="s35Low" type="number" min="0" value="'+Number(x.low_activity_messages||3)+'"></label><label class="s35-switch full"><span><b>維護模式</b><small>供網站其他流程判斷</small></span><input id="s35Maintenance" type="checkbox" '+(x.maintenance_mode?'checked':'')+'></label><div class="s35-card full"><b>🌈 Rainbow Life 彩虹星空主題</b><small>永久固定，不提供主題、顏色或動畫切換。</small></div><button class="btn full" onclick="s35SaveSettings()">儲存系統設定</button></div>';
  }else if(k==='logs'){
   let j=await api('/api/rainbow/admin/logs');
   box.innerHTML='<div class="s35-toolbar"><input class="s35-search" placeholder="搜尋操作紀錄" oninput="s35FilterCards(this.value)"></div><div class="s35-grid">'+((j.items||[]).length?(j.items||[]).map(x=>'<article class="s35-card s35-filter-item"><b>📋 '+esc(x.action||'system')+'</b><small>'+esc(x.detail||'')+'</small><small>'+esc(s35Date(x.created_at))+'</small></article>').join(''):'<div class="s35-empty">目前沒有操作紀錄</div>')+'</div>';
  }else{
   await s35OldAdminAction(k);
  }
 }catch(e){
  box.innerHTML='<div class="s35-empty">'+esc(e.message||'資料載入失敗')+'</div>';
 }
}
function step34Init(){let vp=document.getElementById('step34MenuViewport'),pages=document.querySelectorAll('#step34MenuTrack .step34-menu-page').length,d=document.getElementById('step34MenuDots');if(d)d.innerHTML=Array.from({length:pages},(_,i)=>`<i class="${i===0?'active':''}"></i>`).join('');if(vp&&!vp.dataset.bound){vp.dataset.bound='1';let x=0;vp.addEventListener('touchstart',e=>x=e.touches[0].clientX,{passive:true});vp.addEventListener('touchend',e=>{let dx=e.changedTouches[0].clientX-x;if(Math.abs(dx)>38){STEP34_MENU_PAGE=dx<0?Math.min(pages-1,STEP34_MENU_PAGE+1):Math.max(0,STEP34_MENU_PAGE-1);step34Move()}},{passive:true})}}
function step352RestoreAdminSection(){let kind='';try{kind=sessionStorage.getItem('rainbow_admin_section')||'';sessionStorage.removeItem('rainbow_admin_section')}catch(e){}if(!kind)return;setTimeout(()=>{let map={overview:'members',members:'members',activities:'activities',settings:'settings',inactive:'inactive',achievements:'achievements',vip:'vip',permissions:'permissions',logs:'logs'};let target=map[kind]||kind;let buttons=[...document.querySelectorAll('.step34-menu-page button')];let btn=buttons.find(b=>(b.getAttribute('onclick')||'').includes("step34Open('"+target+"'"));step34Open(target,btn||null)},350)}
function step34Open(kind,btn){document.querySelectorAll('.step34-menu-page button').forEach(x=>x.classList.remove('active'));if(btn)btn.classList.add('active');let labels={members:['👥 成員管理','查看成員資料、等級與活躍狀態'],inactive:['⚠️ 未簽到／低活躍','快速查看需要關注的成員'],achievements:['🏆 成就管理','建立、編輯與停用成就'],vip:['💎 VIP 管理','查看會員資格與期限'],permissions:['🛡️ 權限管理','管理員、禁言與功能權限'],activities:['🎉 活動管理','活動內容與顯示設定'],settings:['⚙️ 系統設定','維護模式與系統功能'],logs:['📋 操作紀錄','查看後台操作軌跡']};let x=labels[kind]||['🛠️ 管理功能',''];let a=document.getElementById('step34ResultTitle'),b=document.getElementById('step34ResultSub');if(a)a.textContent=x[0];if(b)b.textContent=x[1];adminAction(kind)}
const _step34OldLoadAdmin=loadAdmin;
loadAdmin=async function(){if(!DATA||DATA.role==='member')return;step34Init();document.body.classList.toggle('step34-owner',DATA.role==='owner'||DATA.role==='system_owner'||DATA.role==='superadmin');let role=document.getElementById('step34RoleText');if(role)role.textContent=roleText(DATA.role||'admin')+'・最高權限介面';try{let j=await api('/api/rainbow/admin/overview');let vals=[j.members,j.today_signed,j.attention,j.today_messages];document.querySelectorAll('#step34Metrics article b').forEach((e,i)=>e.textContent=compact(vals[i]||0));let box=document.getElementById('adminResult');if(box)box.innerHTML='<div class="step34-welcome"><h3>歡迎回到 Rainbow Life 管理中心</h3><p class="sub">上方可左右滑動切換管理功能。現在顯示的是目前群組即時總覽。</p></div>';let title=document.getElementById('step34ResultTitle'),sub=document.getElementById('step34ResultSub');if(title)title.textContent='📊 後台總覽';if(sub)sub.textContent='群組即時狀態已同步'}catch(e){let box=document.getElementById('adminResult');if(box)box.innerHTML='<p class="sub">'+esc(e.message||'後台資料載入失敗')+'</p>'}}
setTimeout(step34Init,0);

/* Step 3.3.1｜單一步驟首頁精簡：四區塊＋四小格輪播 */
function step331Setup(){
  const home=document.getElementById('home');
  if(!home||document.getElementById('step331CompactHome'))return;
  const style=document.createElement('style');
  style.textContent=`
  #home.step331-ready>*:not(.version-mark):not(#step331CompactHome):not(#lifePushCenter){display:none!important}
  #home.step331-ready .version-mark{margin-bottom:8px}
  #step331CompactHome{display:grid;gap:12px}
  .s331-alert{min-height:44px;padding:9px 12px;border-radius:15px;display:flex;align-items:center;gap:10px;background:linear-gradient(90deg,rgba(53,35,112,.96),rgba(112,46,127,.91));border:1px solid rgba(255,255,255,.19);box-shadow:0 10px 26px rgba(0,0,0,.2);overflow:hidden}
  .s331-alert-icon{font-size:20px;flex:0 0 auto}.s331-alert-copy{min-width:0;flex:1}.s331-alert-copy b,.s331-alert-copy span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.s331-alert-copy b{font-size:12px}.s331-alert-copy span{font-size:11px;color:#ddd5f7;margin-top:2px}.s331-alert-dots{display:flex;gap:4px}.s331-alert-dots i,.s331-dots i{width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,.25)}.s331-alert-dots i.active,.s331-dots i.active{background:#fff;box-shadow:0 0 8px #fff}
  .s331-profile{display:flex;align-items:center;gap:13px;padding:15px;border-radius:22px;background:linear-gradient(135deg,rgba(31,20,83,.96),rgba(83,38,119,.88));border:1px solid rgba(211,190,255,.32)}
  .s331-avatar{width:76px;height:76px;border-radius:50%;object-fit:cover;border:4px solid rgba(255,255,255,.86);box-shadow:0 0 22px rgba(164,112,255,.5)}.s331-profile-main{min-width:0;flex:1}.s331-profile h2{font-size:20px;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.s331-role{font-size:11px;color:#dcd2f8;margin-top:3px}.s331-profile-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.s331-chip{font-size:11px;padding:5px 8px;border-radius:999px;background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.13)}.s331-exp{height:9px;border-radius:99px;background:rgba(4,2,27,.65);overflow:hidden;margin-top:9px}.s331-exp i{display:block;height:100%;background:linear-gradient(90deg,#68dfff,#9b79ff,#ff77cd,#ffe16e);transition:width .7s ease}
  .s331-panel{padding:14px;border-radius:22px;background:rgba(34,24,83,.9);border:1px solid rgba(211,190,255,.25);overflow:hidden}.s331-panel-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}.s331-panel-head h3{margin:0;font-size:16px}.s331-swipe{overflow:hidden;touch-action:pan-y}.s331-track{display:flex;transition:transform .32s ease}.s331-page{min-width:100%;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.s331-tile{min-height:92px;border:1px solid rgba(255,255,255,.12);background:linear-gradient(145deg,rgba(255,255,255,.075),rgba(255,255,255,.025));border-radius:16px;padding:11px;color:#fff;text-align:left}.s331-tile:active{transform:scale(.97)}.s331-tile .ico{font-size:22px}.s331-tile b{display:block;font-size:13px;margin-top:5px}.s331-tile small{display:block;color:#d7cff0;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.s331-tile.admin{background:linear-gradient(145deg,rgba(255,201,91,.18),rgba(163,89,255,.16));border-color:rgba(255,220,141,.35)}.s331-dots{display:flex;justify-content:center;gap:5px;margin-top:10px}
  .s331-recent-list{display:grid;gap:7px}.s331-recent-item{display:flex;align-items:center;gap:9px;padding:9px 10px;border-radius:13px;background:rgba(255,255,255,.055)}.s331-recent-item span{font-size:18px}.s331-recent-item div{min-width:0}.s331-recent-item b,.s331-recent-item small{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.s331-recent-item small{color:#cbc2e8;margin-top:2px}.s331-empty{color:#cfc6e9;font-size:12px;text-align:center;padding:12px}
  @media(max-width:620px){#step331CompactHome{gap:10px}.s331-profile{padding:13px}.s331-avatar{width:68px;height:68px}.s331-tile{min-height:84px;padding:10px}.main{padding-bottom:90px}}
  `;
  document.head.appendChild(style);
  const wrap=document.createElement('div');wrap.id='step331CompactHome';
  wrap.innerHTML=`<section class="s331-alert" id="s331Alert"><span class="s331-alert-icon" id="s331AlertIcon">🔔</span><div class="s331-alert-copy"><b id="s331AlertTitle">提醒載入中</b><span id="s331AlertText">正在同步最新公告</span></div><div class="s331-alert-dots" id="s331AlertDots"></div></section>
  <section class="s331-profile"><img class="s331-avatar" id="s331Avatar" alt="LINE 大頭照"><div class="s331-profile-main"><h2 id="s331Name">Rainbow</h2><div class="s331-role" id="s331Role">載入中</div><div class="s331-profile-meta"><span class="s331-chip" id="s331Level">LV.--</span><span class="s331-chip" id="s331Vip">一般會員</span><span class="s331-chip" id="s331Title">彩虹旅人</span></div><div class="s331-exp"><i id="s331Exp" style="width:0%"></i></div></div></section>
  <section class="s331-panel"><div class="s331-panel-head"><h3>✨ 今日資訊與快捷功能</h3><small>左右滑動</small></div><div class="s331-swipe" id="s331Swipe"><div class="s331-track" id="s331Track"></div></div><div class="s331-dots" id="s331Dots"></div></section>
  <section class="s331-panel"><div class="s331-panel-head"><h3>🕘 最近活動</h3><small>最新 3 筆</small></div><div class="s331-recent-list" id="s331Recent"><div class="s331-empty">資料載入中…</div></div></section>`;
  const version=home.querySelector('.version-mark');(version||home.firstChild).after(wrap);home.classList.add('step331-ready');
  step331BindSwipe();step331Render();
}
let STEP331_PAGE=0,STEP331_ALERT=0,STEP331_ALERT_TIMER=null;
function step331Tile(icon,title,sub,onclick,admin){return `<button class="s331-tile${admin?' admin':''}" onclick="${onclick}"><span class="ico">${icon}</span><b>${esc(title)}</b><small>${esc(sub||'')}</small></button>`}
function step331Render(){if(!DATA)return;const $=id=>document.getElementById(id);let avatar=$('s331Avatar');if(avatar)avatar.src=DATA.picture_url||'/rainbow-static/rainbow_life_boy.png';if($('s331Name'))$('s331Name').textContent=DATA.name||'Rainbow';if($('s331Role'))$('s331Role').textContent=roleText(DATA.role||'member');if($('s331Level'))$('s331Level').textContent='⭐ LV.'+(DATA.level||1);if($('s331Vip'))$('s331Vip').textContent=DATA.vip?'💎 VIP '+(DATA.vip_until||'永久'):'一般會員';if($('s331Title'))$('s331Title').textContent='🏷️ '+(DATA.title||'彩虹旅人');let percent=Number(DATA.level_percent??DATA.exp_percent??0);if(!percent&&DATA.exp_next)percent=Math.min(100,Math.round(Number(DATA.exp||0)/Number(DATA.exp_next||1)*100));if($('s331Exp'))$('s331Exp').style.width=Math.max(0,Math.min(100,percent))+'%';
 const wheel=isTodayDate(DATA.last_wheel_date);const admin=DATA.role&&DATA.role!=='member';
 const pages=[[
  ['🔮','今日運勢',DATA.fortune||'尚未占卜',"action('fortune')"],['🎡','今日轉盤',wheel?'今日已完成':'尚未完成',"unavailable('每日轉盤')"],['📅','簽到狀態',(DATA.streak||0)+' 天連續',"toast('請在群組輸入：簽到')"],['💬','聊天／貼圖',compact(DATA.today_messages||0)+'／'+compact(DATA.today_stickers||0),"toast('今日活躍資料已同步')"]],
  [['🏆','成就中心','查看解鎖進度',"step331OpenAchievements()"],['🖼️','頭像框設定',frameLabel(DATA.equipped_frame),"step331OpenFrames()"],['📆','行事曆','新增生活提醒',"go('calendar')"],['🎁','活動中心','查看近期活動',"step331OpenActivities()"]]
 ];
 if(admin)pages.push([['👑','管理後台','Owner／管理權限',"openAdminWeb('overview')",true],['📢','公告管理','發布與管理公告',"openAdminWeb('activities')",true],['👥','成員管理','成員與活躍狀態',"openAdminWeb('members')",true],['⚙️','系統設定','維護與權限設定',"openAdminWeb('settings')",true]]);
 else pages.push([['🪪','我的名片','查看個人名片',"action('card')"],['🎟️','抽獎中心','開發中',"go('lottery')"],['🎂','生日／星座',DATA.birthday||'尚未設定',"editProfile()"],['⚙️','個人設定','修改個人資料',"editProfile()"]]);
 const track=$('s331Track');if(track)track.innerHTML=pages.map(pg=>`<div class="s331-page">${pg.map(x=>step331Tile(...x)).join('')}</div>`).join('');const dots=$('s331Dots');if(dots)dots.innerHTML=pages.map((_,i)=>`<i class="${i===STEP331_PAGE?'active':''}"></i>`).join('');STEP331_PAGE=Math.min(STEP331_PAGE,pages.length-1);step331Move();step331RenderAlerts();step331RenderRecent();
}
function step331Move(){const t=document.getElementById('s331Track');if(t)t.style.transform=`translateX(-${STEP331_PAGE*100}%)`;document.querySelectorAll('#s331Dots i').forEach((d,i)=>d.classList.toggle('active',i===STEP331_PAGE))}
function step331BindSwipe(){let el=document.getElementById('s331Swipe'),x=0;if(!el)return;el.addEventListener('touchstart',e=>x=e.touches[0].clientX,{passive:true});el.addEventListener('touchend',e=>{let dx=e.changedTouches[0].clientX-x,n=document.querySelectorAll('#s331Track .s331-page').length;if(Math.abs(dx)>38){STEP331_PAGE=dx<0?Math.min(n-1,STEP331_PAGE+1):Math.max(0,STEP331_PAGE-1);step331Move()}},{passive:true})}
function step331Alerts(){let a=[];if(!DATA)return [{icon:'✨',title:'今天沒有新的提醒',text:'祝你有美好的一天'}];if(!DATA.fortune||DATA.fortune==='尚未占卜')a.push({icon:'🔮',title:'今日運勢尚未完成',text:'點擊今日運勢即可查看'});if(!isTodayDate(DATA.last_wheel_date))a.push({icon:'🎡',title:'今日轉盤尚未完成',text:'每日可完成一次'});if(!Number(DATA.streak||0))a.push({icon:'📅',title:'今天尚未簽到',text:'請到群組輸入「簽到」'});(DATA.announcements||[]).slice(0,3).forEach(x=>a.push({icon:'📢',title:x.title||'Rainbow Life 公告',text:x.content||'點擊查看最新公告'}));return a.length?a:[{icon:'✅',title:'今天沒有新的提醒',text:'所有重要事項都完成了'}]}
function step331RenderAlerts(){let a=step331Alerts();STEP331_ALERT%=a.length;let x=a[STEP331_ALERT],$=id=>document.getElementById(id);if($('s331AlertIcon'))$('s331AlertIcon').textContent=x.icon;if($('s331AlertTitle'))$('s331AlertTitle').textContent=x.title;if($('s331AlertText'))$('s331AlertText').textContent=x.text;if($('s331AlertDots'))$('s331AlertDots').innerHTML=a.map((_,i)=>`<i class="${i===STEP331_ALERT?'active':''}"></i>`).join('');clearInterval(STEP331_ALERT_TIMER);if(a.length>1)STEP331_ALERT_TIMER=setInterval(()=>{STEP331_ALERT=(STEP331_ALERT+1)%a.length;step331RenderAlerts()},4200)}
function step331RenderRecent(){let list=[],ach=DATA.achievements||[];ach.filter(x=>x.unlocked).slice(0,2).forEach(x=>list.push(['🏆',x.name||'成就已解鎖',x.description||'新的成就紀錄']));(DATA.announcements||[]).slice(0,3-list.length).forEach(x=>list.push(['📢',x.title||'最新公告',x.content||'Rainbow Life 最新消息']));if(!list.length)list=[['✨','目前沒有新活動','完成簽到或活動後會顯示在這裡']];let e=document.getElementById('s331Recent');if(e)e.innerHTML=list.slice(0,3).map(x=>`<div class="s331-recent-item"><span>${x[0]}</span><div><b>${esc(x[1])}</b><small>${esc(x[2])}</small></div></div>`).join('')}
async function step331OpenAchievements(){try{let j=await api('/api/rainbow/achievements');showFeature('achievement',j)}catch(e){let items=(DATA&&DATA.achievements)||[];showFeature('achievement',{title:'🏆 成就中心',items:items.map(x=>({name:(x.unlocked?'✅ ':'🔒 ')+(x.name||'成就'),description:x.description||''}))})}}
async function step331OpenFrames(){try{let j=await api('/api/rainbow/feature/frame');showFeature('frame',j)}catch(e){toast(e.message)}}
function step331OpenActivities(){go('home');let el=document.getElementById('lifePushCenter');if(el){el.style.display='';setTimeout(()=>el.scrollIntoView({behavior:'smooth',block:'start'}),80)}else unavailable('活動中心')}
function homeRestoreSection(el,title){document.getElementById('dialog').innerHTML='<h3>'+title+'</h3><p class="sub">完整內容請由活動中心頁面查看。</p><button class="btn" onclick="closeModal()">關閉</button>';document.getElementById('modal').classList.add('show')}
const _step331OldRenderPersonal=typeof renderPersonal==='function'?renderPersonal:null;if(_step331OldRenderPersonal)renderPersonal=function(){_step331OldRenderPersonal();step331Render()};
setTimeout(step331Setup,0);setTimeout(step331Render,900);setTimeout(step352RestoreAdminSection,1100);
</script></body></html>'''

def _profile_region(group_id, user_id):
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute('SELECT region FROM web_profile_settings WHERE group_id=%s AND user_id=%s', (group_id, user_id))
            row = c.fetchone() or {}
            return str(row.get('region') or '').strip()
    finally:
        conn.close()


def _normalize_region(region):
    text = (region or '').replace('台', '臺').replace(' ', '')
    city = ''
    district = ''
    for suffix in ('市', '縣'):
        idx = text.find(suffix)
        if idx >= 0:
            city = text[:idx + 1]
            district = text[idx + 1:]
            break
    if not city:
        city = '臺中市'
        district = text or '太平區'
    if district and not district.endswith(('區', '鄉', '鎮', '市')):
        district += '區'
    return city, district or '太平區'


def _first_value(items, *keys):
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for key in keys:
            val = item.get(key)
            if val not in (None, ''):
                return val
    return None


def _weather_elements(location):
    result = {}
    for element in location.get('WeatherElement') or location.get('weatherElement') or []:
        name = str(element.get('ElementName') or element.get('elementName') or '')
        times = element.get('Time') or element.get('time') or []
        first = times[0] if times else {}
        values = first.get('ElementValue') or first.get('elementValue') or []
        value = _first_value(values, 'Temperature', 'ApparentTemperature', 'ProbabilityOfPrecipitation', 'Weather', 'UVIndex', 'Value')
        if value is None:
            value = _first_value(values, 'temperature', 'apparentTemperature', 'probabilityOfPrecipitation', 'weather', 'uvIndex', 'value')
        result[name] = value
    return result


def _walk_json(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_json(value)


def _fetch_cwa_weather(region):
    """取得中央氣象署即時警特報，依個人地區優先篩選。"""
    api_key = (os.getenv('CWA_API_KEY') or os.getenv('CWA_AUTHORIZATION') or '').strip()
    if not api_key:
        raise RuntimeError('CWA_API_KEY 未設定')
    city, district = _normalize_region(region)
    params = urlencode({'Authorization': api_key, 'format': 'JSON'})
    url = f'https://opendata.cwa.gov.tw/api/v1/rest/datastore/{CWA_WARNING_DATASET_ID}?{params}'
    req = UrlRequest(url, headers={'User-Agent': 'Rainbow-Life/2.0'})
    with urlopen(req, timeout=18) as response:
        payload = json.loads(response.read().decode('utf-8'))

    warning_words = ('颱風','豪雨','大雨','大雷雨','強風','長浪','低溫','高溫','濃霧','地震','警報','特報','即時訊息')
    candidates = []
    for obj in _walk_json(payload.get('records') or payload.get('Records') or payload):
        texts = []
        for key, value in obj.items():
            if isinstance(value, (str, int, float)) and value not in ('', None):
                label = str(key).lower()
                if any(k in label for k in ('title','headline','description','content','phenomena','event','status','location','area','info','text','name')):
                    texts.append(str(value))
        joined = ' '.join(texts).strip()
        if joined and any(word in joined for word in warning_words):
            candidates.append(joined)

    # 地區相關警報優先，其次顯示全臺重要警報。
    selected = ''
    for text in candidates:
        if city in text or district in text or city.replace('臺','台') in text:
            selected = text
            break
    if not selected and candidates:
        selected = candidates[0]
    selected = re.sub(r'\s+', ' ', selected).strip()
    if len(selected) > 180:
        selected = selected[:177] + '…'
    now = datetime.datetime.now(TZ)
    return {
        'ok': True,
        'region': f'{city}{district}',
        'has_warning': bool(selected),
        'warning': selected or '目前沒有發布任何氣象警特報',
        'url': CWA_WARNING_URL,
        'updated_at': now.strftime('%Y/%m/%d %H:%M'),
        'refresh_seconds': WEATHER_CACHE_SECONDS,
        'source': '中央氣象署'
    }


def _cached_weather(region):
    key = region or '臺中市太平區'
    now = time.time()
    with _WEATHER_LOCK:
        cached = _WEATHER_CACHE.get(key)
        if cached and now - cached['time'] < WEATHER_CACHE_SECONDS:
            return cached['data']
    try:
        data = _fetch_cwa_weather(key)
        with _WEATHER_LOCK:
            _WEATHER_CACHE[key] = {'time': now, 'data': data}
        return data
    except Exception:
        with _WEATHER_LOCK:
            cached = _WEATHER_CACHE.get(key)
            if cached:
                stale = dict(cached['data'])
                stale['stale'] = True
                stale['message'] = '目前顯示上一次成功取得的資訊'
                return stale
        raise


class _FamilyTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = 0
        self.tokens = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ('script', 'style', 'noscript'):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in ('script', 'style', 'noscript') and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip:
            return
        text = re.sub(r'\s+', ' ', html.unescape(data or '')).strip()
        if text:
            self.tokens.append(text)


def _kangkang5_period(now=None):
    """回傳本期康康5狀態與週五至隔週二的日期。"""
    now = now or datetime.datetime.now(TZ)
    day = now.date()
    weekday = day.weekday()  # Mon=0 ... Sun=6
    active = weekday in (4, 5, 6, 0, 1)
    if weekday >= 4:
        start = day - datetime.timedelta(days=weekday - 4)
    else:
        start = day - datetime.timedelta(days=weekday + 3)
    end = start + datetime.timedelta(days=4)
    return active, start, end


def _fetch_familymart_event():
    now = datetime.datetime.now(TZ)
    is_kangkang5, start_date, end_date = _kangkang5_period(now)
    if is_kangkang5:
        return {
            'ok': True,
            'title': '〔全家康康5〕五天5好康 好康優惠中',
            'is_kangkang5': True,
            'period': f'{start_date.strftime("%Y/%m/%d")}（五）～{end_date.strftime("%Y/%m/%d")}（二）',
            'url': FAMILY_EVENT_URL,
            'updated_at': now.strftime('%Y/%m/%d %H:%M'),
            'refresh_seconds': FAMILY_CACHE_SECONDS,
            'source': '全家便利商店官方網站'
        }

    req = UrlRequest(FAMILY_EVENT_URL, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; Rainbow-Life/2.0; +https://www.family.com.tw/)',
        'Accept-Language': 'zh-TW,zh;q=0.9'
    })
    with urlopen(req, timeout=18) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or 'utf-8'
    text = raw.decode(charset, errors='replace')
    parser = _FamilyTextParser(); parser.feed(text)
    tokens = [t for t in parser.tokens if t not in ('Image', '最新活動', '全家便利商店-最新活動')]
    date_re = re.compile(r'^(?:\d{4}/\d{2}/\d{2}\s*-\s*\d{4}/\d{2}/\d{2}|長期活動)$')
    categories = {'主題活動','抽獎活動','支付優惠','便利快訊','會員優惠','鮮食優惠','商品優惠','最新活動'}
    title = ''; period = ''
    for idx, token in enumerate(tokens):
        if not date_re.match(token): continue
        period = token
        for candidate in tokens[idx + 1:idx + 8]:
            if candidate in categories or date_re.match(candidate) or len(candidate) < 4: continue
            title = candidate; break
        if title: break
    if not title: title = '查看全家本期官方活動'
    return {
        'ok': True, 'title': title[:120], 'is_kangkang5': False, 'period': period,
        'url': FAMILY_EVENT_URL, 'updated_at': now.strftime('%Y/%m/%d %H:%M'),
        'refresh_seconds': FAMILY_CACHE_SECONDS, 'source': '全家便利商店官方網站'
    }


def _cached_familymart_event():
    now = time.time()
    with _FAMILY_LOCK:
        cached = _FAMILY_CACHE.get('latest')
        if cached and now - cached['time'] < FAMILY_CACHE_SECONDS:
            return cached['data']
    try:
        data = _fetch_familymart_event()
        with _FAMILY_LOCK:
            _FAMILY_CACHE['latest'] = {'time': now, 'data': data}
        return data
    except Exception:
        with _FAMILY_LOCK:
            cached = _FAMILY_CACHE.get('latest')
            if cached:
                stale = dict(cached['data']); stale['stale'] = True
                return stale
        raise



def _fetch_seven_event():
    req = UrlRequest(SEVEN_EVENT_URL, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; Rainbow-Life/2.0; +https://www.7-11.com.tw/)',
        'Accept-Language': 'zh-TW,zh;q=0.9'
    })
    with urlopen(req, timeout=18) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or 'utf-8'
    text = raw.decode(charset, errors='replace')
    parser = _FamilyTextParser()
    parser.feed(text)
    tokens = []
    ignored = {
        'Image','本期優惠','主題活動','精選美味','嚴選商品','便利生活',
        'CITY CAFE','CITY TEA','小七食堂','優惠','總覽','繁體中文',
        '產品與服務','聯合服務中心','專線電話：0800-008711'
    }
    for token in parser.tokens:
        token = re.sub(r'\s+', ' ', token).strip()
        if not token or token in ignored or len(token) < 4:
            continue
        if '食品業登錄字號' in token or token.startswith('Copyright'):
            continue
        tokens.append(token)
    title = ''
    period = ''
    date_re = re.compile(r'(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}).{0,12}(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2})')
    for idx, token in enumerate(tokens):
        if date_re.search(token):
            period = date_re.search(token).group(0)
            for candidate in tokens[max(0, idx - 4):idx + 5]:
                if candidate == token or date_re.search(candidate) or len(candidate) > 120:
                    continue
                if any(word in candidate for word in ('活動','優惠','新品','集點','咖啡','CITY')):
                    title = candidate
                    break
            if title:
                break
    if not title:
        for candidate in tokens:
            if len(candidate) <= 120 and any(word in candidate for word in ('活動','優惠','新品','集點')):
                title = candidate
                break
    if not title:
        title = '查看 7-ELEVEN 本期官方優惠'
    now = datetime.datetime.now(TZ)
    return {
        'ok': True,
        'title': title[:120],
        'period': period[:80],
        'url': SEVEN_EVENT_URL,
        'updated_at': now.strftime('%Y/%m/%d %H:%M'),
        'refresh_seconds': SEVEN_CACHE_SECONDS,
        'source': '7-ELEVEN 台灣官方網站'
    }


def _cached_seven_event():
    now = time.time()
    with _SEVEN_LOCK:
        cached = _SEVEN_CACHE.get('latest')
        if cached and now - cached['time'] < SEVEN_CACHE_SECONDS:
            return cached['data']
    try:
        data = _fetch_seven_event()
        with _SEVEN_LOCK:
            _SEVEN_CACHE['latest'] = {'time': now, 'data': data}
        return data
    except Exception:
        with _SEVEN_LOCK:
            cached = _SEVEN_CACHE.get('latest')
            if cached:
                stale = dict(cached['data']); stale['stale'] = True
                return stale
        raise

def _fetch_mcdonalds_event():
    req = UrlRequest(MCD_EVENT_URL, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; Rainbow-Life/2.0; +https://www.mcdonalds.com/tw/zh-tw.html)',
        'Accept-Language': 'zh-TW,zh;q=0.9'
    })
    with urlopen(req, timeout=18) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or 'utf-8'
    text = raw.decode(charset, errors='replace')
    parser = _FamilyTextParser()
    parser.feed(text)
    tokens = []
    ignored = {'了解更多','瞭解詳情','馬上探索、馬上行動！','麥當勞台灣官網首頁','現正推出'}
    for token in parser.tokens:
        token = re.sub(r'\s+', ' ', token).strip()
        if not token or token in ignored or len(token) < 4 or len(token) > 140:
            continue
        if token.startswith('Copyright') or '隱私權政策' in token or '網站使用條款' in token:
            continue
        tokens.append(token)
    title = ''
    keywords = ('優惠','新品','新登場','限定','活動','買一送一','APP','聯名','回歸','推出')
    for candidate in tokens:
        if any(word in candidate for word in keywords):
            title = candidate
            break
    if not title:
        title = '查看麥當勞最新官方活動'
    period = ''
    date_re = re.compile(r'(?:20\d{2}[./-])?\d{1,2}[./-]\d{1,2}.{0,12}(?:20\d{2}[./-])?\d{1,2}[./-]\d{1,2}')
    for token in tokens:
        found = date_re.search(token)
        if found:
            period = found.group(0)
            break
    now = datetime.datetime.now(TZ)
    return {
        'ok': True,
        'title': title[:120],
        'period': period[:80],
        'url': MCD_EVENT_URL,
        'updated_at': now.strftime('%Y/%m/%d %H:%M'),
        'refresh_seconds': MCD_CACHE_SECONDS,
        'source': '台灣麥當勞官方網站'
    }


def _cached_mcdonalds_event():
    now = time.time()
    with _MCD_LOCK:
        cached = _MCD_CACHE.get('latest')
        if cached and now - cached['time'] < MCD_CACHE_SECONDS:
            return cached['data']
    try:
        data = _fetch_mcdonalds_event()
        with _MCD_LOCK:
            _MCD_CACHE['latest'] = {'time': now, 'data': data}
        return data
    except Exception:
        with _MCD_LOCK:
            cached = _MCD_CACHE.get('latest')
            if cached:
                stale = dict(cached['data']); stale['stale'] = True
                return stale
        raise



def _fetch_official_info(kind):
    cfg = OFFICIAL_INFO_SOURCES[kind]
    req = UrlRequest(cfg['url'], headers={
        'User-Agent': 'Mozilla/5.0 (compatible; Rainbow-Life/2.0)',
        'Accept-Language': 'zh-TW,zh;q=0.9'
    })
    with urlopen(req, timeout=18) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or 'utf-8'
    text = raw.decode(charset, errors='replace')
    parser = _FamilyTextParser(); parser.feed(text)
    ignored = {'首頁','最新消息','更多','上一頁','下一頁',':::','網站導覽','回首頁'}
    keywords = {
        'tra': ('公告','營運','列車','延誤','異常','疏運','新聞'),
        'thsr': ('公告','營運','列車','異常','疏運','優惠','最新'),
        'pokemon_go': ('活動','更新','社群日','登場','季節','調查','團體戰','最新消息'),
        'aov': ('公告','活動','更新','版本','賽事','造型','系統','最新'),
    }[kind]
    title = ''
    for token in parser.tokens:
        token = re.sub(r'\s+', ' ', token).strip()
        if not token or token in ignored or len(token) < 5 or len(token) > 120:
            continue
        if any(k in token for k in keywords):
            title = token; break
    if not title:
        title = f"查看{cfg['title']}"
    now = datetime.datetime.now(TZ)
    return {'ok':True,'title':title[:120],'period':'','url':cfg['url'],
            'updated_at':now.strftime('%Y/%m/%d %H:%M'),'refresh_seconds':OFFICIAL_INFO_CACHE_SECONDS,
            'source':cfg['source']}

def _cached_official_info(kind):
    now = time.time()
    with _OFFICIAL_INFO_LOCK:
        cached = _OFFICIAL_INFO_CACHE.get(kind)
        if cached and now - cached['time'] < OFFICIAL_INFO_CACHE_SECONDS:
            return cached['data']
    try:
        data = _fetch_official_info(kind)
        with _OFFICIAL_INFO_LOCK:
            _OFFICIAL_INFO_CACHE[kind] = {'time': now, 'data': data}
        return data
    except Exception:
        with _OFFICIAL_INFO_LOCK:
            cached = _OFFICIAL_INFO_CACHE.get(kind)
            if cached:
                stale = dict(cached['data']); stale['stale'] = True
                return stale
        cfg = OFFICIAL_INFO_SOURCES[kind]
        return {'ok':False,'title':f"{cfg['title']}目前無法取得最新內容",'period':'',
                'url':cfg['url'],'updated_at':datetime.datetime.now(TZ).strftime('%Y/%m/%d %H:%M'),
                'refresh_seconds':OFFICIAL_INFO_CACHE_SECONDS,'source':cfg['source'],'stale':True}



def _admin_html():
    return '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#10092e"><title>Rainbow Life 管理後台</title><style>\n:root{--ink:#3f3a52;--muted:#7d768f;--line:#e6ddf5;--card:rgba(255,255,255,.88);--purple:#7459d9;--pink:#df70b8;--danger:#d95372}*{box-sizing:border-box}html{background:#f8f5ff}body{margin:0;min-width:1180px;color:var(--ink);font-family:system-ui,-apple-system,"Noto Sans TC",sans-serif;background:radial-gradient(circle at 8% 10%,rgba(153,203,255,.28),transparent 25%),radial-gradient(circle at 92% 14%,rgba(255,183,224,.30),transparent 26%),linear-gradient(135deg,#f3f7ff,#fff6fb 46%,#f7f2ff);background-attachment:fixed}.stars{display:none}.shell{display:block;min-height:100vh}.side{position:sticky;top:0;height:auto;padding:0 24px;background:rgba(255,255,255,.9);border:0;border-bottom:1px solid #dbcdf1;box-shadow:0 5px 18px rgba(94,72,132,.08);backdrop-filter:blur(16px);z-index:20;display:flex;align-items:center;gap:18px}.brand{padding:0;font-size:19px;white-space:nowrap;color:#6853bd;background:none;-webkit-background-clip:initial}.role{margin:0;padding:0;border:0;border-radius:0;color:#625b71;font-size:12px;order:3;margin-left:auto}.nav{display:flex;align-items:center;gap:4px;flex:1}.nav button{width:auto;margin:0;padding:18px 11px;border:0;border-radius:10px;background:transparent;color:#514b61;text-align:center;font-size:13px;white-space:nowrap}.nav button.active,.nav button:hover{background:linear-gradient(90deg,#eee9ff,#fff0f9);color:#6549bf;font-weight:800}.return{position:static;order:4}.return .btn{width:auto!important;white-space:nowrap}.main{width:100%;max-width:1480px;margin:auto;padding:14px 16px 40px}.top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;padding:10px 14px;background:rgba(255,255,255,.76);border:1px solid var(--line);border-radius:16px;box-shadow:0 8px 25px rgba(93,73,130,.08)}.top h1{margin:0;font-size:17px}.chip{padding:7px 11px;border:1px solid var(--line);border-radius:999px;background:#f5efff;font-size:12px}.card{border:1px solid var(--line);border-radius:18px;background:var(--card);box-shadow:0 10px 26px rgba(95,72,128,.08);backdrop-filter:blur(12px);padding:15px}.metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}.metric{min-height:106px}.metric small{color:var(--muted)}.metric b{display:block;font-size:27px;margin-top:8px;color:#7058c9}.workspace{margin-top:12px}.section-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px}.section-head h2{margin:0;font-size:17px}.tools{display:flex;gap:8px;flex-wrap:wrap}.input,.select,.textarea{width:100%;padding:10px 11px;border-radius:10px;border:1px solid #ddd3ea;background:#fff;color:#40384f}.textarea{min-height:100px;resize:vertical}.btn{border:0;border-radius:10px;padding:9px 12px;background:linear-gradient(90deg,#725bd6,#dc70b8);color:#fff;font-weight:800}.btn.secondary{background:#f3eefc;color:#66587e;border:1px solid #ded4ef}.btn.danger{background:linear-gradient(90deg,#c44e6b,#ef748d)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.item{padding:13px;border:1px solid #eee6f6;border-radius:14px;background:linear-gradient(145deg,#fff,#fff9fd)}.item b{display:block}.item small{display:block;color:var(--muted);margin-top:5px}.actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}.actions .btn{font-size:12px;padding:8px 10px}.empty{padding:34px;text-align:center;color:var(--muted)}.form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:12px}.form .full{grid-column:1/-1}.admin-modal{display:none;position:fixed;inset:0;z-index:120;background:rgba(12,5,35,.76);backdrop-filter:blur(5px);align-items:center;justify-content:center;padding:18px}.admin-modal.show{display:flex}.admin-dialog{width:min(560px,94vw);max-height:86vh;overflow:auto;border:1px solid rgba(198,174,255,.55);border-radius:26px;background:linear-gradient(155deg,#21134f,#120a32);box-shadow:0 28px 80px rgba(16,5,48,.55),inset 0 1px 0 rgba(255,255,255,.12);padding:22px;color:#fff}.admin-dialog h3{margin:0 0 15px;font-size:22px}.admin-dialog .input,.admin-dialog .textarea{background:#0c0829;border-color:rgba(198,174,255,.4);color:#fff}.admin-dialog .textarea{min-height:130px}.admin-dialog small{color:#d8cdf4}.admin-dialog-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:14px}.admin-dialog-preview{width:100%;max-height:240px;object-fit:cover;border-radius:17px;border:1px solid rgba(255,255,255,.14);margin:8px 0 12px}.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:140;padding:11px 16px;border-radius:999px;border:1px solid var(--line);background:#51426d;color:#fff;display:none}.toast.show{display:block}.mobilebar{display:none!important}.loading{opacity:.7}.tag{display:inline-flex;margin-top:7px;padding:5px 8px;border-radius:999px;background:#efeaff;color:#6955b8;font-size:11px}.danger-text{color:#c34f6d}@media(max-width:1250px){body{min-width:1080px}.nav button{padding:18px 8px}.metrics{grid-template-columns:repeat(3,1fr)}}\n</style></head><body><div class="stars"></div><div class="shell"><aside class="side"><div class="brand">🌈 Rainbow Life</div><div class="role" id="sideRole">管理權限確認中</div><nav class="nav" id="nav"><button class="active" data-section="overview" onclick="openSection(\'overview\',this)">⌂ 首頁</button><button data-section="members" onclick="openSection(\'members\',this)">👥 成員管理</button><button data-section="inactive" onclick="openSection(\'inactive\',this)">⚠️ 未簽到／低活躍</button><button data-section="announcements" onclick="openSection(\'announcements\',this)">📢 公告管理</button><button data-section="achievements" onclick="openSection(\'achievements\',this)">🏆 成就管理</button><button data-section="permissions" onclick="openSection(\'permissions\',this)">🛡️ 權限管理</button><button data-section="settings" onclick="openSection(\'settings\',this)">⚙️ 系統設定</button><button data-section="logs" onclick="openSection(\'logs\',this)">📋 操作紀錄</button></nav><div class="return"><button class="btn secondary" style="width:100%" onclick="location.href=\'/player\'">← 返回個人中心</button></div></aside><main class="main"><header class="top"><div><small style="color:var(--muted)">RAINBOW LIFE 社群管理後台</small><h1 id="pageTitle">🌈 Rainbow Life 管理中心</h1></div><span class="chip" id="roleChip">載入中</span></header><section class="card" style="margin-bottom:12px;padding:0;overflow:hidden"><div style="display:grid;grid-template-columns:120px repeat(6,1fr)"><div style="padding:16px;background:linear-gradient(145deg,#7c68e4,#df75bc);color:#fff"><small>本日</small><b style="display:block;font-size:21px;margin-top:6px">Rainbow Life</b><small>社群管理</small></div><div style="padding:15px;border-right:1px solid #eee7f4"><b>👥 總成員</b><small style="display:block;color:var(--muted);margin-top:8px" id="wMembers">-- 人</small></div><div style="padding:15px;border-right:1px solid #eee7f4"><b>✅ 今日簽到</b><small style="display:block;color:var(--muted);margin-top:8px" id="wSigned">-- 人</small></div><div style="padding:15px;border-right:1px solid #eee7f4"><b>💬 今日聊天</b><small style="display:block;color:var(--muted);margin-top:8px" id="wMessages">-- 則</small></div><div style="padding:15px;border-right:1px solid #eee7f4"><b>💎 VIP</b><small style="display:block;color:var(--muted);margin-top:8px" id="wVip">-- 人</small></div><div style="padding:15px;border-right:1px solid #eee7f4"><b>🛡️ 管理員</b><small style="display:block;color:var(--muted);margin-top:8px" id="wAdmins">-- 人</small></div><div style="padding:15px"><b>⚠️ 需關注</b><small style="display:block;color:var(--muted);margin-top:8px" id="wAttention">-- 人</small></div></div></section><section class="metrics" id="metrics"><article class="card metric"><small>總成員</small><b id="mMembers">--</b></article><article class="card metric"><small>今日簽到</small><b id="mSigned">--</b></article><article class="card metric"><small>今日聊天</small><b id="mMessages">--</b></article><article class="card metric"><small>VIP</small><b id="mVip">--</b></article><article class="card metric"><small>管理員</small><b id="mAdmins">--</b></article><article class="card metric"><small>未簽到／低活躍</small><b id="mAttention">--</b></article></section><section class="card workspace"><div class="section-head"><div><h2 id="sectionTitle">⌂ 首頁</h2><small id="sectionSub" style="color:var(--muted)">群組即時狀態</small></div><div class="tools" id="sectionTools"></div></div><div id="content" class="loading">資料載入中…</div></section></main></div><nav class="mobilebar"><button class="active" onclick="openSection(\'overview\',this)"><span>📊</span>總覽</button><button onclick="openSection(\'members\',this)"><span>👥</span>成員</button><button onclick="openSection(\'announcements\',this)"><span>📢</span>公告</button><button onclick="openSection(\'settings\',this)"><span>⚙️</span>設定</button><button onclick="location.href=\'/player\'"><span>🏠</span>返回</button></nav><div class="admin-modal" id="adminModal" onclick="if(event.target===this)closeAdminModal()"><div class="admin-dialog" id="adminDialog"></div></div><div class="toast" id="toast"></div><script>\nconst $=s=>document.querySelector(s), esc=s=>String(s??\'\').replace(/[&<>"\']/g,m=>({\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\',\'"\':\'&quot;\',"\'":\'&#39;\'}[m]));let ROLE=\'admin\';\nfunction toast(t){let e=$(\'#toast\');e.textContent=t;e.classList.add(\'show\');setTimeout(()=>e.classList.remove(\'show\'),2200)}\nasync function api(url,opt={}){opt.headers={...(opt.headers||{}),\'Content-Type\':\'application/json\'};let r=await fetch(url,opt);let j={};try{j=await r.json()}catch(e){}if(!r.ok)throw new Error(j.detail||\'操作失敗\');return j}\nfunction roleText(r){return ({owner:\'Owner 最高權限\',leader:\'群長\',admin:\'管理員\'})[r]||r}\nasync function loadOverview(){let j=await api(\'/api/rainbow/admin/overview\');ROLE=j.role||\'admin\';$(\'#roleChip\').textContent=roleText(ROLE);$(\'#sideRole\').textContent=\'目前身分：\'+roleText(ROLE);$(\'#mMembers\').textContent=j.members;$(\'#mSigned\').textContent=j.today_signed;$(\'#mMessages\').textContent=j.today_messages;$(\'#mVip\').textContent=j.vip;$(\'#mAdmins\').textContent=j.admins;$(\'#mAttention\').textContent=j.attention;$(\'#wMembers\').textContent=j.members+\' 人\';$(\'#wSigned\').textContent=j.today_signed+\' 人\';$(\'#wMessages\').textContent=j.today_messages+\' 則\';$(\'#wVip\').textContent=j.vip+\' 人\';$(\'#wAdmins\').textContent=j.admins+\' 人\';$(\'#wAttention\').textContent=j.attention+\' 人\';return j}\nconst labels={overview:[\'⌂ 首頁\',\'群組即時狀態\'],members:[\'👥 成員管理\',\'搜尋、VIP 與禁言\'],inactive:[\'⚠️ 未簽到／低活躍\',\'查看需要關注的成員\'],announcements:[\'📢 公告管理\',\'發布、停用與刪除公告\'],achievements:[\'🏆 成就管理\',\'查看成就與解鎖人數\'],permissions:[\'🛡️ 權限管理\',\'Owner 可調整管理權限\'],settings:[\'⚙️ 系統設定\',\'維護模式與低活躍標準\'],logs:[\'📋 操作紀錄\',\'最近 100 筆後台操作\']};\nfunction activeNav(kind,btn){document.querySelectorAll(\'[data-section]\').forEach(x=>x.classList.toggle(\'active\',x.dataset.section===kind));document.querySelectorAll(\'.mobilebar button\').forEach(x=>x.classList.remove(\'active\'));if(btn&&btn.closest(\'.mobilebar\'))btn.classList.add(\'active\')}\nasync function openSection(kind,btn){activeNav(kind,btn);let l=labels[kind]||labels.overview;$(\'#sectionTitle\').textContent=l[0];$(\'#sectionSub\').textContent=l[1];$(\'#content\').className=\'loading\';$(\'#content\').textContent=\'資料載入中…\';$(\'#sectionTools\').innerHTML=\'\';try{if(kind===\'overview\')await renderOverview();else if(kind===\'members\')await renderMembers();else if(kind===\'inactive\')await renderInactive();else if(kind===\'announcements\')await renderAnnouncements();else if(kind===\'achievements\')await renderAchievements();else if(kind===\'permissions\')await renderPermissions();else if(kind===\'settings\')await renderSettings();else if(kind===\'logs\')await renderLogs()}catch(e){$(\'#content\').innerHTML=\'<div class="empty danger-text">\'+esc(e.message)+\'</div>\'}$(\'#content\').classList.remove(\'loading\')}\nasync function renderOverview(){let j=await loadOverview();$(\'#content\').innerHTML=\'<div class="grid"><article class="item"><b>🌈 Rainbow Life 後台已獨立</b><small>獨立電腦網頁版後台已啟用，版面採用 Work Life 相同的清楚卡片式管理配置。</small><span class="tag">固定彩虹背景</span></article><article class="item"><b>⚡ 今日狀態</b><small>目前 \'+j.members+\' 位成員，\'+j.today_signed+\' 位已簽到，今日聊天 \'+j.today_messages+\' 則。</small><span class="tag">需關注 \'+j.attention+\' 人</span></article></div>\'}\nasync function renderMembers(){let j=await api(\'/api/rainbow/admin/members\');$(\'#sectionTools\').innerHTML=\'<input class="input" id="memberSearch" placeholder="搜尋名稱或 User ID" oninput="filterItems(this.value)">\';$(\'#content\').innerHTML=\'<div class="grid">\'+(j.items||[]).map(x=>\'<article class="item filter-item" data-filter="\'+esc((x.name||\'\')+\' \'+x.user_id)+\'"><b>\'+esc(x.name||\'未命名\')+\'</b><small>Lv.\'+Number(x.level||1)+\'・\'+(x.is_vip?\'💎 VIP\':\'一般會員\')+(x.admin_role?\'・🛡️ \'+esc(x.admin_role):\'\')+\'</small><small>今日聊天 \'+Number(x.today_messages||0)+\'・最近簽到 \'+esc(x.last_sign_in||\'無紀錄\')+\'</small><div class="actions"><button class="btn" onclick="memberAction(\\\'\'+esc(x.user_id)+\'\\\',\\\'vip_on\\\')">VIP +30天</button>\'+(x.is_vip?\'<button class="btn secondary" onclick="memberAction(\\\'\'+esc(x.user_id)+\'\\\',\\\'vip_off\\\')">取消 VIP</button>\':\'\')+\'<button class="btn danger" onclick="memberAction(\\\'\'+esc(x.user_id)+\'\\\',\\\'\'+(x.is_muted?\'unmute\':\'mute\')+\'\\\')">\'+(x.is_muted?\'解除禁言\':\'禁言 24h\')+\'</button></div></article>\').join(\'\')+\'</div>\'}\nfunction filterItems(v){v=v.toLowerCase();document.querySelectorAll(\'.filter-item\').forEach(x=>x.style.display=(x.dataset.filter||\'\').toLowerCase().includes(v)?\'\':\'none\')}\nasync function memberAction(uid,action){await api(\'/api/rainbow/admin/member/control\',{method:\'POST\',body:JSON.stringify({user_id:uid,action,days:30,hours:24})});toast(\'操作完成\');renderMembers();loadOverview()}\nasync function renderInactive(){let j=await api(\'/api/rainbow/admin/inactive\');$(\'#content\').innerHTML=\'<div class="grid">\'+(j.items||[]).map(x=>\'<article class="item"><b>\'+esc(x.name||\'未命名\')+\'</b><small>\'+(x.signed_today?\'✅ 今日已簽到\':\'⚠️ 今日未簽到\')+\'・今日聊天 \'+x.today_messages+\'</small><small>未簽到 \'+x.days_without_sign+\' 天・狀態：\'+esc(x.activity_status)+\'</small></article>\').join(\'\')+\'</div>\'}\nasync function renderAnnouncements(){let j=await api(\'/api/rainbow/admin/announcements\');window.announcementItems=j.items||[];$(\'#sectionTools\').innerHTML=\'<button class="btn" onclick="announcementForm()">＋ 新增公告</button>\';$(\'#content\').innerHTML=\'<div class="grid">\'+(j.items||[]).map(x=>\'<article class="item"><b>📢 \'+esc(x.title)+\'</b><small>\'+esc(x.content||\'\')+\'</small><small>\'+esc(x.created_at||\'\')+\'・\'+(x.is_active?\'顯示中\':\'已停用\')+(x.has_image?\'・🖼️ 有圖片\':\'\')+\'</small><div class="actions"><button class="btn secondary" onclick="announcementView(\'+x.id+\')">查看</button><button class="btn secondary" onclick="announcementForm(\'+x.id+\')">編輯</button><button class="btn secondary" onclick="announcementStatus(\'+x.id+\',\'+(!x.is_active)+\')">\'+(x.is_active?\'停用\':\'啟用\')+\'</button><button class="btn danger" onclick="announcementDelete(\'+x.id+\')">刪除</button></div></article>\').join(\'\')+\'</div>\'}\nfunction announcementView(id){let x=((window.announcementItems||[]).find(v=>Number(v.id)===Number(id))||{}),img=x.has_image?\'<img class="admin-dialog-preview" src="\'+esc(x.image_url||(\'/api/rainbow/public/announcement/\'+id+\'/image\'))+\'" onerror="this.style.display=\\\'none\\\'">\':\'\';openAdminModal(\'<h3>📢 \'+esc(x.title||\'公告內容\')+\'</h3>\'+img+\'<p style="white-space:pre-wrap;line-height:1.7">\'+esc(x.content||\'\')+\'</p><small>\'+(x.is_active?\'目前顯示中\':\'目前已停用\')+\'・順位 \'+Number(x.sort_order||50)+\'</small><div class="admin-dialog-actions"><button class="btn" onclick="announcementForm(\'+id+\')">編輯</button><button class="btn secondary" onclick="closeAdminModal()">關閉</button></div>\')}\nfunction announcementForm(id=0){let x=((window.announcementItems||[]).find(v=>Number(v.id)===Number(id))||{}),preview=x.has_image?\'<img class="admin-dialog-preview" src="\'+esc(x.image_url||(\'/api/rainbow/public/announcement/\'+id+\'/image\'))+\'" onerror="this.style.display=\\\'none\\\'">\':\'\';openAdminModal(\'<h3>\'+(id?\'✏️ 編輯公告\':\'📢 新增公告\')+\'</h3>\'+preview+\'<input id="aTitle" class="input" placeholder="公告標題" value="\'+esc(x.title||\'\')+\'"><textarea id="aContent" class="textarea" placeholder="公告內容">\'+esc(x.content||\'\')+\'</textarea><input id="aLink" class="input" placeholder="連結（選填）" value="\'+esc(x.link_url||\'\')+\'"><input id="aOrder" class="input" type="number" min="0" max="999" value="\'+Number(x.sort_order||50)+\'" placeholder="輪播順位"><input id="aImage" class="input" type="file" accept="image/jpeg,image/png,image/webp,image/gif"><small>圖片上限 2MB；按下儲存後會自動同步個人中心並立即推播到 LINE 聊天室。</small><div class="admin-dialog-actions"><button class="btn" onclick="announcementSave(\'+Number(id||0)+\')">\'+(id?\'儲存並自動推播\':\'發布並自動推播\')+\'</button><button class="btn secondary" onclick="closeAdminModal()">取消</button></div>\')}\nasync function announcementSave(id=0){try{let file=$(\'#aImage\').files[0],image_data=\'\';if(file){if(file.size>2*1024*1024)throw new Error(\'圖片不可超過 2MB。\');image_data=await new Promise((ok,no)=>{let r=new FileReader();r.onload=()=>ok(r.result);r.onerror=no;r.readAsDataURL(file)})}let body={title:$(\'#aTitle\').value,content:$(\'#aContent\').value,link_url:$(\'#aLink\').value,sort_order:Number($(\'#aOrder\').value||50)};if(image_data)body.image_data=image_data;let saved=await api(\'/api/rainbow/admin/announcement\'+(id?\'/\'+id:\'\'),{method:id?\'PUT\':\'POST\',body:JSON.stringify(body)});let aid=id||Number(saved.id||0);if(!aid)throw new Error(\'公告已儲存，但無法取得公告編號。\');let pushError=\'\';try{await api(\'/api/rainbow/admin/announcement/\'+aid+\'/push\',{method:\'POST\',body:\'{}\'})}catch(e){pushError=e.message||\'聊天室推播失敗\'}closeAdminModal();await renderAnnouncements();if(pushError)modalMessage(\'公告已儲存\',\'個人中心已同步，但 LINE 推播失敗：\'+pushError,false);else modalMessage(\'公告完成\',\'公告已同步到個人中心並自動推播到 LINE 聊天室。\',true)}catch(e){modalMessage(\'公告發布失敗\',e.message||\'請稍後再試。\',false)}}\nasync function announcementStatus(id,is_active){try{await api(\'/api/rainbow/admin/announcement/status\',{method:\'POST\',body:JSON.stringify({id,is_active})});await renderAnnouncements();modalMessage(\'公告狀態已更新\',is_active?\'公告已啟用。\':\'公告已停用。\',true)}catch(e){modalMessage(\'狀態更新失敗\',e.message||\'請稍後再試。\',false)}}\nfunction announcementDelete(id){openAdminModal(\'<h3>🗑️ 刪除公告</h3><p>確定要刪除這則公告嗎？刪除後個人中心也會同步移除。</p><div class="admin-dialog-actions"><button class="btn danger" onclick="announcementDeleteConfirm(\'+id+\')">確定刪除</button><button class="btn secondary" onclick="closeAdminModal()">取消</button></div>\')}\nasync function announcementDeleteConfirm(id){try{await api(\'/api/rainbow/admin/announcement/\'+id,{method:\'DELETE\'});closeAdminModal();await renderAnnouncements();modalMessage(\'公告已刪除\',\'個人中心已同步更新。\',true)}catch(e){modalMessage(\'刪除失敗\',e.message||\'請稍後再試。\',false)}}\nasync function renderAchievements(){let j=await api(\'/api/rainbow/admin/achievements\');$(\'#content\').innerHTML=\'<div class="grid">\'+(j.items||[]).map(x=>\'<article class="item"><b>\'+esc(x.icon||\'🏆\')+\' \'+esc(x.name)+\'</b><small>\'+esc(x.description||\'\')+\'</small><small>\'+esc(x.condition_label||\'\')+\'・解鎖 \'+Number(x.unlocked_count||0)+\' 人</small><span class="tag">\'+(x.is_active?\'啟用中\':\'已停用\')+\'</span></article>\').join(\'\')+\'</div>\'}\nasync function renderPermissions(){let j=await api(\'/api/rainbow/admin/permissions\');let form=ROLE===\'owner\'?\'<div class="form"><input id="pUid" class="input full" placeholder="LINE User ID"><select id="pRole" class="select"><option value="admin">管理員</option><option value="manager">進階管理員</option><option value="leader">群長</option></select><button class="btn" onclick="permissionSave(false)">授予／更新</button><button class="btn danger" onclick="permissionSave(true)">移除權限</button></div>\':\'\';$(\'#content\').innerHTML=form+\'<div class="grid">\'+(j.items||[]).map(x=>\'<article class="item"><b>🛡️ \'+esc(x.role||\'admin\')+\'</b><small>\'+esc(x.user_id)+\'</small></article>\').join(\'\')+\'</div>\'}\nasync function permissionSave(remove){await api(\'/api/rainbow/admin/permission\',{method:\'POST\',body:JSON.stringify({user_id:$(\'#pUid\').value,role:$(\'#pRole\').value,remove})});toast(\'權限已更新\');renderPermissions();loadOverview()}\nasync function renderSettings(){let j=await api(\'/api/rainbow/admin/settings\'),x=j.item||{};$(\'#content\').innerHTML=\'<div class="form"><label>未簽到判定天數<input id="sInactive" class="input" type="number" value="\'+Number(x.inactive_days||7)+\'"></label><label>低活躍訊息數<input id="sLow" class="input" type="number" value="\'+Number(x.low_activity_messages||3)+\'"></label><label class="full item"><input id="sMaint" type="checkbox" \'+(x.maintenance_mode?\'checked\':\'\')+\'> 開啟維護模式</label><button class="btn" onclick="settingsSave()">儲存設定</button></div><div class="item"><b>🎨 背景設定</b><small>固定使用與個人中心相同的彩虹星空背景，不提供動態主題與切換功能。</small></div>\'}\nasync function settingsSave(){await api(\'/api/rainbow/admin/settings\',{method:\'POST\',body:JSON.stringify({inactive_days:Number($(\'#sInactive\').value),low_activity_messages:Number($(\'#sLow\').value),maintenance_mode:$(\'#sMaint\').checked})});toast(\'設定已儲存\')}\nasync function renderLogs(){let j=await api(\'/api/rainbow/admin/logs\');$(\'#content\').innerHTML=\'<div>\'+((j.items||[]).map(x=>\'<article class="item" style="margin-bottom:8px"><b>\'+esc(x.action||\'操作\')+\'</b><small>\'+esc(x.detail||\'\')+\'</small><small>\'+esc(x.created_at||\'\')+\'</small></article>\').join(\'\')||\'<div class="empty">尚無操作紀錄</div>\')+\'</div>\'}\n(async()=>{try{await loadOverview();let q=new URLSearchParams(location.search).get(\'section\')||\'overview\';openSection(labels[q]?q:\'overview\')}catch(e){$(\'#content\').innerHTML=\'<div class="empty danger-text">\'+esc(e.message)+\'<br><br>請回 LINE 使用後台指令重新取得連結。</div>\'}})();\n</script></body></html>'

def register_rainbow_web(app, line_bot_api):
    # 不讓資料庫短暫連線失敗阻止 Render 啟動；實際開啟頁面/API 時仍會回報連線錯誤。
    try:
        ensure_web_tables()
    except Exception as exc:
        print(f'[Rainbow Life] web table initialization deferred: {exc}')
    assets_dir = os.path.join(os.path.dirname(__file__), 'rainbow_static')
    try:
        app.mount('/rainbow-static', StaticFiles(directory=assets_dir), name='rainbow-static')
    except RuntimeError:
        pass

    @app.get('/player/admin', response_class=HTMLResponse)
    async def player_admin_page(request: Request):
        uid,gid=_auth(request)
        if _role(gid,uid)=='member':
            raise HTTPException(403,'一般成員無法進入管理後台。')
        return RedirectResponse('/admin' + _query_suffix(request), status_code=302)

    @app.get('/player', response_class=HTMLResponse)
    async def player_page(request: Request):
        _auth(request)
        return HTMLResponse(_dashboard_html(), headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0','Pragma':'no-cache','Expires':'0'})

    @app.get('/player/entry')
    async def player_entry(request: Request, gid: str = '', target: str = '/player'):
        # 新舊入口皆支援：舊簽章直接通過；只有 gid 的共用入口則走 LINE Login。
        if request.query_params.get('uid') and request.query_params.get('sig'):
            _auth(request)
            return RedirectResponse('/player' + _query_suffix(request), status_code=302)
        gid = str(gid or '').strip()
        target = target if str(target).startswith('/player') else '/player'
        if not gid:
            raise HTTPException(400, '入口缺少群組資訊，請回 LINE 群組重新開啟。')
        if not _oauth_ready():
            raise HTTPException(503, 'LINE Login 尚未設定完整。')
        response = RedirectResponse(_line_authorize_url(gid, target), status_code=302)
        response.delete_cookie(PLAYER_COOKIE, path='/')
        return response

    @app.get('/player/oauth/callback')
    async def player_oauth_callback(code: str = '', state: str = '', error: str = ''):
        data = _decode_token(state, 'player')
        if not data or data.get('kind') != 'line_oauth_state' or error or not code:
            raise HTTPException(401, 'LINE 登入已取消或驗證失敗。')
        token_data = _exchange_line_code(code)
        verified = _verify_id_token(str(token_data.get('id_token') or ''))
        uid = str(verified.get('sub') or '').strip()
        gid = str(data.get('gid') or '').strip()
        target = str(data.get('target') or '/player')
        if not uid or not gid:
            raise HTTPException(401, '無法取得 LINE 登入身分。')
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(PLAYER_COOKIE, _encode_token({'uid':uid,'gid':gid,'kind':'session','exp':_now_ts()+SESSION_TTL},'player'), httponly=True, secure=True, samesite='lax', max_age=SESSION_TTL, path='/')
        return response

    @app.get('/admin', response_class=HTMLResponse)
    async def admin_page(request: Request):
        uid, gid = _auth(request)
        if _role(gid, uid) == 'member':
            raise HTTPException(403, '你沒有管理權限。')
        return HTMLResponse(_admin_html(), headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0','Pragma':'no-cache','Expires':'0'})

    @app.get('/admin/access')
    async def admin_access(request: Request, token: str = ''):
        # 支援舊 admin_web token 與新版簽章網址。
        if token:
            data = _decode_token(token, 'admin')
            if not data or data.get('kind') != 'access':
                raise HTTPException(401, '管理連結已失效。')
            uid, gid = str(data.get('uid') or ''), str(data.get('gid') or '')
            if not uid or not gid or _role(gid, uid) == 'member':
                raise HTTPException(403, '你沒有管理權限。')
            response = RedirectResponse('/admin', status_code=303)
            response.set_cookie(ADMIN_COOKIE, _encode_token({'uid':uid,'gid':gid,'kind':'session','exp':_now_ts()+SESSION_TTL},'admin'), httponly=True, secure=True, samesite='lax', max_age=SESSION_TTL, path='/')
            return response
        uid, gid = _auth(request)
        if _role(gid, uid) == 'member':
            raise HTTPException(403, '你沒有管理權限。')
        return RedirectResponse('/admin' + _query_suffix(request), status_code=302)

    @app.get('/api/rainbow/me')
    async def api_me(request: Request):
        uid, gid = _auth(request)
        return JSONResponse(jsonable_encoder(_player_data(line_bot_api, gid, uid)), headers={'Cache-Control': 'no-store'})

    @app.get('/api/rainbow/feature/{name}')
    async def feature_get(name: str, request: Request):
        uid, gid = _auth(request)
        if name == 'achievement':
            return {'title': '🏆 成就系統', 'message': '五階成就系統將於 Phase 2 正式啟用。'}
        if name == 'frame':
            return {'title': '🖼️ 頭像效果', 'message': '一般會員使用主題光環；群長、管理員、VIP 與成就將套用不同專屬框。'}
        raise HTTPException(404, '找不到功能。')

    @app.post('/api/rainbow/feature/{name}')
    async def feature_post(name: str, request: Request):
        uid, gid = _auth(request)
        if name != 'fortune':
            raise HTTPException(404, '找不到功能。')
        today = datetime.datetime.now(TZ).date().isoformat()
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT last_fortune_date FROM players WHERE group_id=%s AND user_id=%s FOR UPDATE', (gid, uid))
                player = c.fetchone() or {}
                fortunes = [
                    ('★★★★★ 大吉', '今天的彩虹能量非常旺盛，適合主動出擊。'),
                    ('★★★★☆ 吉', '人際與工作運順利，保持好心情。'),
                    ('★★★☆☆ 平', '穩穩完成今天的事情，就是最好的進展。'),
                    ('★★☆☆☆ 小凶', '放慢腳步，多確認一次能避開小失誤。'),
                ]
                if str(player.get('last_fortune_date') or '') == today:
                    c.execute('SELECT fortune_level,fortune_message FROM players WHERE group_id=%s AND user_id=%s', (gid, uid))
                    row = c.fetchone() or {}
                    return {'title': '🔮 今日運勢', 'message': f"{row.get('fortune_level') or '今日運勢'}\n{row.get('fortune_message') or ''}"}
                level, message = fortunes[int(hashlib.sha256(f'{uid}|{today}|fortune'.encode()).hexdigest(), 16) % len(fortunes)]
                c.execute('UPDATE players SET last_fortune_date=%s,fortune_level=%s,fortune_message=%s WHERE group_id=%s AND user_id=%s', (today, level, message, gid, uid))
                conn.commit()
                return {'title': '🔮 今日運勢', 'message': level + '\n' + message}
        finally:
            conn.close()

    @app.post('/api/rainbow/frame/purchase')
    async def web_purchase_frame(request: Request):
        _auth(request)
        raise HTTPException(410,'頭像框不可購買，只能透過成就、活動、VIP 或管理身分解鎖。')

    @app.post('/api/rainbow/frame/equip')
    async def web_equip_frame(request: Request):
        uid,gid=_auth(request); payload=await request.json(); key=str(payload.get('frame_key') or ''); role=_role(gid,uid); data=_player_data(line_bot_api,gid,uid); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT * FROM web_avatar_frames WHERE frame_key=%s AND is_active=TRUE',(key,)); frame=c.fetchone()
                if not frame: raise HTTPException(404,'找不到頭像框。')
                if frame.get('owner_only') and role!='owner': raise HTTPException(403,'這是 Owner 專屬頭像框。')
                if frame.get('vip_only') and not data['vip']: raise HTTPException(403,'這是 VIP 專屬頭像框。')
                if not frame.get('vip_only') and not frame.get('owner_only') and key!='rainbow_basic':
                    c.execute('SELECT 1 AS ok FROM web_user_frames WHERE group_id=%s AND user_id=%s AND frame_key=%s',(gid,uid,key))
                    if not c.fetchone(): raise HTTPException(403,'此頭像框尚未透過成就或活動解鎖。')
                c.execute('UPDATE web_user_frames SET equipped=FALSE WHERE group_id=%s AND user_id=%s',(gid,uid))
                c.execute("INSERT INTO web_user_frames(group_id,user_id,frame_key,equipped) VALUES(%s,%s,%s,TRUE) ON CONFLICT(group_id,user_id,frame_key) DO UPDATE SET equipped=TRUE",(gid,uid,key)); conn.commit()
        finally: conn.close()
        return {'ok':True,'message':'頭像框已套用。'}

    @app.get('/api/rainbow/weather')
    async def api_weather(request: Request):
        uid, gid = _auth(request)
        region = _profile_region(gid, uid)
        if not region:
            return {'ok': True, 'needs_setting': True, 'message': '請先設定天氣地區', 'refresh_seconds': WEATHER_CACHE_SECONDS}
        try:
            return _cached_weather(region)
        except Exception:
            return JSONResponse(status_code=503, content={
                'ok': False,
                'message': '資訊更新中！',
                'refresh_seconds': WEATHER_CACHE_SECONDS
            })


    @app.get('/api/rainbow/familymart')
    async def api_familymart(request: Request):
        _auth(request)
        try:
            return _cached_familymart_event()
        except Exception:
            return JSONResponse(status_code=503, content={
                'ok': False,
                'message': '資訊更新中！',
                'refresh_seconds': FAMILY_CACHE_SECONDS
            })

    @app.get('/api/rainbow/seven')
    async def api_seven(request: Request):
        _auth(request)
        try:
            return _cached_seven_event()
        except Exception:
            return JSONResponse(status_code=503, content={
                'ok': False,
                'message': '資訊更新中！',
                'refresh_seconds': SEVEN_CACHE_SECONDS
            })

    @app.get('/api/rainbow/mcdonalds')
    async def api_mcdonalds(request: Request):
        _auth(request)
        try:
            return _cached_mcdonalds_event()
        except Exception:
            return JSONResponse(status_code=503, content={
                'ok': False,
                'message': '資訊更新中！',
                'refresh_seconds': MCD_CACHE_SECONDS
            })

    @app.get('/api/rainbow/official/{kind}')
    async def api_official_info(request: Request, kind: str):
        _auth(request)
        if kind not in OFFICIAL_INFO_SOURCES:
            raise HTTPException(404, '找不到官方資訊來源。')
        return _cached_official_info(kind)

    @app.get('/api/rainbow/activities')
    async def api_activity_overrides(request: Request):
        _, gid = _auth(request)
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute('''SELECT activity_key,title,content,period,url,priority,is_visible,
                             TO_CHAR(updated_at AT TIME ZONE 'Asia/Taipei','YYYY/MM/DD HH24:MI') updated_at
                             FROM web_activity_overrides WHERE group_id=%s''', (gid,))
                rows = c.fetchall()
        finally:
            conn.close()
        return {'items': {str(x['activity_key']): dict(x) for x in rows}}

    @app.post('/api/rainbow/admin/activity')
    async def admin_activity_display(request: Request):
        uid, gid, _ = require_admin(request)
        p = await request.json()
        key = str(p.get('activity_key') or '').strip()
        allowed = {'weather','family','seven','mcd','tra','thsr','pokemon_go','aov','holiday','rainbow_notice'}
        if key not in allowed:
            raise HTTPException(400, '找不到活動項目。')
        title = str(p.get('title') or '').strip()[:120]
        content = str(p.get('content') or '').strip()[:500]
        period = str(p.get('period') or '').strip()[:100]
        url = str(p.get('url') or '').strip()[:500]
        try: priority = max(0, min(999, int(p.get('priority', 50))))
        except Exception: priority = 50
        visible = 1 if bool(p.get('is_visible', True)) else 0
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute('''INSERT INTO web_activity_overrides
                    (group_id,activity_key,title,content,period,url,priority,is_visible,updated_by,updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
                    ON CONFLICT(group_id,activity_key) DO UPDATE SET
                    title=EXCLUDED.title,content=EXCLUDED.content,period=EXCLUDED.period,url=EXCLUDED.url,
                    priority=EXCLUDED.priority,is_visible=EXCLUDED.is_visible,updated_by=EXCLUDED.updated_by,
                    updated_at=CURRENT_TIMESTAMP''',
                    (gid,key,title,content,period,url,priority,visible,uid))
            conn.commit()
        finally:
            conn.close()
        return {'ok': True}

    @app.post('/api/rainbow/admin/activity/reset')
    async def admin_activity_reset(request: Request):
        _, gid, _ = require_admin(request)
        p = await request.json(); key = str(p.get('activity_key') or '').strip()
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute('DELETE FROM web_activity_overrides WHERE group_id=%s AND activity_key=%s', (gid,key))
            conn.commit()
        finally:
            conn.close()
        return {'ok': True}

    @app.post('/api/rainbow/profile')
    async def api_profile(request: Request):
        uid, gid = _auth(request); payload = await request.json()
        bio = str(payload.get('bio') or '')[:300]; region = str(payload.get('region') or '')[:80]
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute('''INSERT INTO web_profile_settings(group_id,user_id,bio,region,updated_at) VALUES(%s,%s,%s,%s,CURRENT_TIMESTAMP)
                ON CONFLICT(group_id,user_id) DO UPDATE SET bio=EXCLUDED.bio,region=EXCLUDED.region,updated_at=CURRENT_TIMESTAMP''',(gid,uid,bio,region))
            conn.commit()
        finally: conn.close()
        return {'ok': True}

    @app.post('/api/rainbow/calendar')
    async def api_calendar(request: Request):
        uid, gid = _auth(request); p = await request.json()
        d = str(p.get('event_date') or '')[:10]; title = str(p.get('title') or '').strip()[:80]; note = str(p.get('note') or '')[:300]
        if not d or not title: raise HTTPException(400, '請填寫日期與提醒標題。')
        conn=get_connection()
        try:
            with conn.cursor() as c: c.execute('INSERT INTO web_calendar_events(group_id,user_id,event_date,title,note) VALUES(%s,%s,%s,%s,%s)',(gid,uid,d,title,note))
            conn.commit()
        finally: conn.close()
        return {'ok':True}

    def require_admin(request):
        uid,gid=_auth(request); r=_role(gid,uid)
        if r=='member': raise HTTPException(403,'沒有管理權限。')
        return uid,gid,r

    @app.get('/api/rainbow/admin/overview')
    async def admin_overview(request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT COUNT(*) total, COUNT(*) FILTER(WHERE COALESCE(is_vip,0)=1) vip, COALESCE(SUM(today_msg_count),0) today_messages, COUNT(*) FILTER(WHERE COALESCE(today_msg_count,0)<3) low_activity FROM players WHERE group_id=%s',(gid,)); a=c.fetchone() or {}
                c.execute("SELECT COUNT(*) signed FROM players WHERE group_id=%s AND DATE(last_sign_in)=CURRENT_DATE",(gid,)); sr=c.fetchone() or {}
                c.execute('SELECT COUNT(*) admins FROM admins WHERE group_id=%s',(gid,)); b=c.fetchone() or {}
        finally: conn.close()
        return {'members':int(a.get('total') or 0),'vip':int(a.get('vip') or 0),'today_messages':int(a.get('today_messages') or 0),'admins':int(b.get('admins') or 0),'today_signed':int(sr.get('signed') or 0),'attention':max(0,int(a.get('total') or 0)-int(sr.get('signed') or 0))+int(a.get('low_activity') or 0),'role':r}

    @app.get('/api/rainbow/admin/members')
    async def admin_members(request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute("SELECT p.user_id,p.name,p.level,p.is_vip,p.vip_until,p.today_msg_count AS today_messages,p.last_sign_in,COALESCE(mc.is_muted,FALSE) is_muted,mc.muted_until,COALESCE(a.role,'') admin_role FROM players p LEFT JOIN web_member_controls mc ON mc.group_id=p.group_id AND mc.user_id=p.user_id LEFT JOIN admins a ON a.group_id=p.group_id AND a.user_id=p.user_id WHERE p.group_id=%s ORDER BY p.level DESC,p.exp DESC LIMIT 300",(gid,)); rows=c.fetchall()
        finally: conn.close()
        return {'items':[dict(x) for x in rows]}

    @app.get('/api/rainbow/admin/inactive')
    async def admin_inactive(request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute("SELECT inactive_days,low_activity_messages FROM web_group_settings WHERE group_id=%s",(gid,)); settings=c.fetchone() or {'inactive_days':7,'low_activity_messages':3}
                c.execute("""SELECT p.user_id,p.name,COALESCE(p.today_msg_count,0) today_messages,p.last_sign_in,
                    CASE WHEN p.last_sign_in IS NULL THEN 999 ELSE GREATEST(0,CURRENT_DATE-DATE(p.last_sign_in)) END days_without_sign,
                    CASE WHEN DATE(p.last_sign_in)=CURRENT_DATE THEN TRUE ELSE FALSE END signed_today
                    FROM players p WHERE p.group_id=%s ORDER BY signed_today ASC,today_messages ASC,days_without_sign DESC LIMIT 300""",(gid,)); rows=[dict(x) for x in c.fetchall()]
        finally: conn.close()
        unsigned=0; low=0
        for x in rows:
            x['days_without_sign']=int(x.get('days_without_sign') or 0)
            x['today_messages']=int(x.get('today_messages') or 0)
            if not x.get('signed_today'): unsigned+=1
            if x['today_messages']==0 or x['days_without_sign']>=int(settings.get('inactive_days') or 7): x['activity_status']='低活躍'; low+=1
            elif x['today_messages']<int(settings.get('low_activity_messages') or 3): x['activity_status']='注意'
            else: x['activity_status']='正常'
            x['last_sign_in']=str(x.get('last_sign_in') or '')
        return {'items':rows,'summary':{'unsigned':unsigned,'low_activity':low},'settings':settings}

    @app.get('/api/rainbow/admin/achievements')
    async def admin_achievements(request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute("""SELECT a.achievement_key,a.name,a.description,a.condition_type,a.condition_value,a.frame_key,a.category,a.icon,a.sort_order,a.is_active,
                    f.name frame_name,COUNT(ua.user_id) unlocked_count FROM web_achievements a
                    LEFT JOIN web_avatar_frames f ON f.frame_key=a.frame_key
                    LEFT JOIN web_user_achievements ua ON ua.achievement_key=a.achievement_key AND ua.group_id=%s
                    GROUP BY a.achievement_key,a.name,a.description,a.condition_type,a.condition_value,a.frame_key,a.category,a.icon,a.sort_order,a.is_active,f.name ORDER BY a.is_active DESC,a.sort_order,a.condition_value""",(gid,)); rows=[dict(x) for x in c.fetchall()]
        finally: conn.close()
        labels={'sign_total':'累積簽到','messages':'聊天數','manual':'手動／活動'}
        for x in rows: x['condition_label']=f"{labels.get(x.get('condition_type'),x.get('condition_type'))} {int(x.get('condition_value') or 0)}"
        return {'items':rows}

    @app.get('/api/rainbow/admin/achievement/{achievement_key}')
    async def admin_achievement_detail(achievement_key: str, request: Request):
        require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT achievement_key,name,description,condition_type,condition_value,frame_key,category,icon,sort_order,is_active FROM web_achievements WHERE achievement_key=%s',(achievement_key,)); row=c.fetchone()
        finally: conn.close()
        if not row: raise HTTPException(404,'找不到成就。')
        return {'item':dict(row)}

    @app.post('/api/rainbow/admin/achievement')
    async def admin_achievement_save(request: Request):
        uid,gid,r=require_admin(request); p=await request.json()
        key=re.sub(r'[^a-zA-Z0-9_-]','',str(p.get('achievement_key') or '').strip())[:60]
        name=str(p.get('name') or '').strip()[:80]; description=str(p.get('description') or '').strip()[:300]
        ctype=str(p.get('condition_type') or 'manual')[:30]; value=max(0,min(100000000,int(p.get('condition_value') or 0)))
        frame_key=re.sub(r'[^a-zA-Z0-9_-]','',str(p.get('frame_key') or '').strip())[:60]
        icon=str(p.get('icon') or '🏆').strip()[:8] or '🏆'
        if not key or not name: raise HTTPException(400,'請填寫成就代碼與名稱。')
        if ctype not in ('sign_total','messages','manual'): raise HTTPException(400,'不支援的解鎖條件。')
        conn=get_connection()
        try:
            with conn.cursor() as c:
                if frame_key:
                    c.execute('SELECT 1 FROM web_avatar_frames WHERE frame_key=%s',(frame_key,))
                    if not c.fetchone(): raise HTTPException(400,'找不到指定的頭像框代碼。')
                c.execute('''INSERT INTO web_achievements(achievement_key,name,description,condition_type,condition_value,frame_key,icon,is_active)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,TRUE) ON CONFLICT(achievement_key) DO UPDATE SET name=EXCLUDED.name,description=EXCLUDED.description,condition_type=EXCLUDED.condition_type,condition_value=EXCLUDED.condition_value,frame_key=EXCLUDED.frame_key,icon=EXCLUDED.icon''',(key,name,description,ctype,value,frame_key,icon))
                c.execute('INSERT INTO web_admin_logs(group_id,user_id,action,detail) VALUES(%s,%s,%s,%s)',(gid,uid,'achievement_save',key))
            conn.commit()
        finally: conn.close()
        return {'ok':True,'achievement_key':key}

    @app.post('/api/rainbow/admin/achievement/toggle')
    async def admin_achievement_toggle(request: Request):
        uid,gid,r=require_admin(request); p=await request.json(); key=str(p.get('achievement_key') or '')
        active=bool(p.get('is_active'))
        conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('UPDATE web_achievements SET is_active=%s WHERE achievement_key=%s',(active,key))
                if c.rowcount==0: raise HTTPException(404,'找不到成就。')
                c.execute('INSERT INTO web_admin_logs(group_id,user_id,action,detail) VALUES(%s,%s,%s,%s)',(gid,uid,'achievement_toggle',key+':'+str(active)))
            conn.commit()
        finally: conn.close()
        return {'ok':True,'is_active':active}

    @app.get('/api/rainbow/admin/vip')
    async def admin_vip(request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c: c.execute('SELECT user_id,name,is_vip,vip_until FROM players WHERE group_id=%s ORDER BY is_vip DESC,name LIMIT 300',(gid,)); rows=c.fetchall()
        finally: conn.close()
        return {'items':[dict(x) for x in rows]}

    @app.get('/api/rainbow/admin/settings')
    async def admin_settings_get(request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT inactive_days,low_activity_messages,maintenance_mode,updated_at FROM web_group_settings WHERE group_id=%s',(gid,)); row=c.fetchone()
                if not row:
                    c.execute('INSERT INTO web_group_settings(group_id) VALUES(%s) ON CONFLICT(group_id) DO NOTHING',(gid,)); conn.commit(); row={'inactive_days':7,'low_activity_messages':3,'maintenance_mode':False}
        finally: conn.close()
        return {'item':dict(row),'theme':'rainbow-cosmos','fixed_theme':True}

    @app.post('/api/rainbow/admin/settings')
    async def admin_settings_save(request: Request):
        uid,gid,r=require_admin(request); p=await request.json(); inactive=max(1,min(365,int(p.get('inactive_days') or 7))); low=max(0,min(9999,int(p.get('low_activity_messages') or 3))); maintenance=bool(p.get('maintenance_mode'))
        conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('INSERT INTO web_group_settings(group_id,inactive_days,low_activity_messages,maintenance_mode,updated_at) VALUES(%s,%s,%s,%s,CURRENT_TIMESTAMP) ON CONFLICT(group_id) DO UPDATE SET inactive_days=EXCLUDED.inactive_days,low_activity_messages=EXCLUDED.low_activity_messages,maintenance_mode=EXCLUDED.maintenance_mode,updated_at=CURRENT_TIMESTAMP',(gid,inactive,low,maintenance))
                c.execute('INSERT INTO web_admin_logs(group_id,user_id,action,detail) VALUES(%s,%s,%s,%s)',(gid,uid,'settings_save',f'inactive={inactive},low={low},maintenance={maintenance}'))
            conn.commit()
        finally: conn.close()
        return {'ok':True}

    @app.get('/api/rainbow/admin/announcements')
    async def admin_announcements(request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute("SELECT id,title,content,link_url,is_active,sort_order,(image_data IS NOT NULL) has_image,TO_CHAR(created_at AT TIME ZONE 'Asia/Taipei','YYYY/MM/DD HH24:MI') created_at FROM web_announcements WHERE group_id=%s ORDER BY is_active DESC,sort_order,id DESC LIMIT 100",(gid,)); rows=c.fetchall()
        finally: conn.close()
        items=[dict(x) for x in rows]
        for item in items:
            item['image_url']=f"/api/rainbow/public/announcement/{int(item.get('id') or 0)}/image" if item.get('has_image') else ''
        return {'items':items}

    @app.post('/api/rainbow/admin/announcement/status')
    async def admin_announcement_status(request: Request):
        uid,gid,r=require_admin(request); p=await request.json(); aid=int(p.get('id') or 0); active=1 if bool(p.get('is_active')) else 0; conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('UPDATE web_announcements SET is_active=%s WHERE id=%s AND group_id=%s',(active,aid,gid))
                if c.rowcount==0: raise HTTPException(404,'找不到公告。')
                c.execute('INSERT INTO web_admin_logs(group_id,user_id,action,detail) VALUES(%s,%s,%s,%s)',(gid,uid,'announcement_status',f'{aid}:{active}'))
            conn.commit()
        finally: conn.close()
        return {'ok':True}


    @app.put('/api/rainbow/admin/announcement/{announcement_id}')
    async def admin_announcement_edit(announcement_id: int, request: Request):
        uid,gid,r=require_admin(request); p=await request.json()
        title=str(p.get('title') or '').strip()[:100]
        content=str(p.get('content') or '').strip()[:1000]
        link_url=str(p.get('link_url') or '').strip()[:500]
        sort_order=max(0,min(999,int(p.get('sort_order') or 50)))
        if not title: raise HTTPException(400,'請填寫公告標題。')
        if link_url and not re.match(r'^https?://',link_url,re.I): raise HTTPException(400,'活動連結必須以 http:// 或 https:// 開頭。')
        conn=get_connection()
        try:
            with conn.cursor() as c:
                raw_image=str(p.get('image_data') or '').strip()
                image_bytes=None; image_mime=''
                if raw_image:
                    match=re.match(r'^data:(image/(?:jpeg|png|webp|gif));base64,([A-Za-z0-9+/=\s]+)$',raw_image,re.I)
                    if not match: raise HTTPException(400,'圖片格式不支援。')
                    image_mime=match.group(1).lower()
                    try: image_bytes=base64.b64decode(re.sub(r'\s+','',match.group(2)),validate=True)
                    except Exception: raise HTTPException(400,'圖片內容無法讀取。')
                    if len(image_bytes)>2*1024*1024: raise HTTPException(413,'圖片不可超過 2MB。')
                if image_bytes is not None:
                    c.execute('UPDATE web_announcements SET title=%s,content=%s,link_url=%s,sort_order=%s,image_data=%s,image_mime=%s WHERE id=%s AND group_id=%s',(title,content,link_url,sort_order,image_bytes,image_mime,announcement_id,gid))
                else:
                    c.execute('UPDATE web_announcements SET title=%s,content=%s,link_url=%s,sort_order=%s WHERE id=%s AND group_id=%s',(title,content,link_url,sort_order,announcement_id,gid))
                if c.rowcount==0: raise HTTPException(404,'找不到公告。')
                c.execute('INSERT INTO web_admin_logs(group_id,user_id,action,detail) VALUES(%s,%s,%s,%s)',(gid,uid,'announcement_edit',str(announcement_id)))
            conn.commit()
        finally: conn.close()
        return {'ok':True}

    @app.delete('/api/rainbow/admin/announcement/{announcement_id}')
    async def admin_announcement_delete(announcement_id: int, request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('DELETE FROM web_announcements WHERE id=%s AND group_id=%s',(announcement_id,gid))
                if c.rowcount==0: raise HTTPException(404,'找不到公告。')
                c.execute('INSERT INTO web_admin_logs(group_id,user_id,action,detail) VALUES(%s,%s,%s,%s)',(gid,uid,'announcement_delete',str(announcement_id)))
            conn.commit()
        finally: conn.close()
        return {'ok':True}

    @app.post('/api/rainbow/admin/member/control')
    async def admin_member_control(request: Request):
        uid,gid,r=require_admin(request); p=await request.json(); target=str(p.get('user_id') or '').strip(); action=str(p.get('action') or '')
        if not target: raise HTTPException(400,'缺少成員資料。')
        conn=get_connection()
        try:
            with conn.cursor() as c:
                if action=='vip_on':
                    days=max(1,min(3650,int(p.get('days') or 30))); c.execute("UPDATE players SET is_vip=1,vip_until=GREATEST(COALESCE(vip_until,CURRENT_TIMESTAMP),CURRENT_TIMESTAMP)+(%s || ' days')::interval WHERE group_id=%s AND user_id=%s",(days,gid,target))
                elif action=='vip_off': c.execute('UPDATE players SET is_vip=0,vip_until=NULL WHERE group_id=%s AND user_id=%s',(gid,target))
                elif action in ('mute','unmute'):
                    muted=action=='mute'; hours=max(1,min(720,int(p.get('hours') or 24))); c.execute("INSERT INTO web_member_controls(group_id,user_id,is_muted,muted_until,updated_by,updated_at) VALUES(%s,%s,%s,CASE WHEN %s THEN CURRENT_TIMESTAMP+(%s || ' hours')::interval ELSE NULL END,%s,CURRENT_TIMESTAMP) ON CONFLICT(group_id,user_id) DO UPDATE SET is_muted=EXCLUDED.is_muted,muted_until=EXCLUDED.muted_until,updated_by=EXCLUDED.updated_by,updated_at=CURRENT_TIMESTAMP",(gid,target,muted,muted,hours,uid))
                else: raise HTTPException(400,'不支援的成員操作。')
                if c.rowcount==0 and action.startswith('vip_'): raise HTTPException(404,'找不到成員。')
                c.execute('INSERT INTO web_admin_logs(group_id,user_id,action,detail) VALUES(%s,%s,%s,%s)',(gid,uid,'member_'+action,target))
            conn.commit()
        finally: conn.close()
        return {'ok':True}

    @app.post('/api/rainbow/admin/permission')
    async def admin_permission_save(request: Request):
        uid,gid,r=require_admin(request); p=await request.json(); target=str(p.get('user_id') or '').strip(); role=str(p.get('role') or 'admin').strip().lower(); remove=bool(p.get('remove'))
        if r!='owner': raise HTTPException(403,'只有 Owner 可調整管理權限。')
        if not target: raise HTTPException(400,'請輸入成員 User ID。')
        if role not in ('admin','manager','leader'): raise HTTPException(400,'不支援的權限角色。')
        conn=get_connection()
        try:
            with conn.cursor() as c:
                if remove: c.execute("DELETE FROM admins WHERE group_id=%s AND user_id=%s AND LOWER(COALESCE(role,''))<>'owner'",(gid,target))
                else: c.execute('INSERT INTO admins(group_id,user_id,role) VALUES(%s,%s,%s) ON CONFLICT(group_id,user_id) DO UPDATE SET role=EXCLUDED.role',(gid,target,role))
                c.execute('INSERT INTO web_admin_logs(group_id,user_id,action,detail) VALUES(%s,%s,%s,%s)',(gid,uid,'permission_remove' if remove else 'permission_save',target+':'+role))
            conn.commit()
        finally: conn.close()
        return {'ok':True}

    @app.get('/api/rainbow/admin/{section}')
    async def admin_section(section: str, request: Request):
        uid,gid,r=require_admin(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                if section=='permissions': c.execute('SELECT user_id,role FROM admins WHERE group_id=%s ORDER BY role,user_id',(gid,))
                elif section=='activities': return {'title':'🎉 活動管理','message':'活動顯示、日期、連結與輪播順位可由首頁的活動顯示管理即時調整。','items':[]}
                elif section=='logs': c.execute('SELECT action,detail,created_at FROM web_admin_logs WHERE group_id=%s ORDER BY id DESC LIMIT 100',(gid,))
                elif section=='settings': return {'title':'⚙️ 系統設定','group_id':gid,'message':'Rainbow Life 彩虹星空主題已固定；商店、彩虹幣、稱號與主題設定均不提供。維護模式與低活躍標準已保留於後台設定資料。'}
                else: raise HTTPException(404,'找不到管理功能。')
                return {'section':section,'items':[dict(x) for x in c.fetchall()]}
        finally: conn.close()


    @app.get('/api/rainbow/public/announcement/{announcement_id}/image')
    async def public_announcement_image(announcement_id: int, gid: str = ''):
        gid=str(gid or '').strip()
        conn=get_connection()
        try:
            with conn.cursor() as c:
                if gid:
                    c.execute("""SELECT image_data,image_mime FROM web_announcements
                                 WHERE id=%s AND group_id=%s AND is_active=1
                                   AND (starts_at IS NULL OR starts_at<=CURRENT_TIMESTAMP)
                                   AND (ends_at IS NULL OR ends_at>=CURRENT_TIMESTAMP)""",(announcement_id,gid))
                else:
                    c.execute("""SELECT image_data,image_mime FROM web_announcements
                                 WHERE id=%s AND is_active=1
                                   AND (starts_at IS NULL OR starts_at<=CURRENT_TIMESTAMP)
                                   AND (ends_at IS NULL OR ends_at>=CURRENT_TIMESTAMP)""",(announcement_id,))
                row=c.fetchone() or {}
        finally: conn.close()
        data=row.get('image_data')
        if not data: raise HTTPException(404,'公告圖片不存在。')
        return Response(content=bytes(data),media_type=str(row.get('image_mime') or 'image/jpeg'),headers={'Cache-Control':'public, max-age=3600'})

    @app.get('/api/rainbow/announcement/{announcement_id}/image')
    async def announcement_image(announcement_id: int, request: Request):
        _, gid = _auth(request)
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT image_data,image_mime FROM web_announcements WHERE id=%s AND group_id=%s AND is_active=1', (announcement_id, gid))
                row = c.fetchone() or {}
        finally:
            conn.close()
        data = row.get('image_data')
        if not data:
            raise HTTPException(404, '公告圖片不存在。')
        return Response(content=bytes(data), media_type=str(row.get('image_mime') or 'image/jpeg'), headers={'Cache-Control':'private, max-age=3600'})

    @app.post('/api/rainbow/admin/announcement')
    async def admin_announcement(request: Request):
        uid,gid,r=require_admin(request); p=await request.json()
        title=str(p.get('title') or '').strip()[:100]
        content=str(p.get('content') or '').strip()[:1000]
        link_url=str(p.get('link_url') or '').strip()[:500]
        raw_image=str(p.get('image_data') or '').strip()
        sort_order=max(0,min(999,int(p.get('sort_order') or 50)))
        if not title: raise HTTPException(400,'請填寫公告標題。')
        if link_url and not re.match(r'^https?://', link_url, re.I): raise HTTPException(400,'活動連結必須以 http:// 或 https:// 開頭。')
        image_bytes=None; image_mime=''
        if raw_image:
            match=re.match(r'^data:(image/(?:jpeg|png|webp|gif));base64,([A-Za-z0-9+/=\s]+)$',raw_image,re.I)
            if not match: raise HTTPException(400,'圖片格式不支援。')
            image_mime=match.group(1).lower()
            try: image_bytes=base64.b64decode(re.sub(r'\s+','',match.group(2)),validate=True)
            except Exception: raise HTTPException(400,'圖片內容無法讀取。')
            if len(image_bytes)>2*1024*1024: raise HTTPException(413,'圖片不可超過 2MB。')
            signatures={'image/jpeg':(b'\xff\xd8\xff',),'image/png':(b'\x89PNG\r\n\x1a\n',),'image/webp':(b'RIFF',),'image/gif':(b'GIF87a',b'GIF89a')}
            if not any(image_bytes.startswith(sig) for sig in signatures.get(image_mime,())): raise HTTPException(400,'圖片檔案驗證失敗。')
        conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('INSERT INTO web_announcements(group_id,title,content,image_data,image_mime,link_url,created_by,sort_order) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',(gid,title,content,image_bytes,image_mime,link_url,uid,sort_order))
                inserted=c.fetchone() or {}
                announcement_id=int(inserted.get('id') or 0)
            conn.commit()
        finally: conn.close()
        return {'ok':True,'has_image':bool(image_bytes),'id':announcement_id}


    def _announcement_line_target(gid: str, uid: str) -> str:
        gid=str(gid or '').strip()
        if gid and gid != 'PRIVATE':
            return gid
        conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute("SELECT group_id FROM admins WHERE user_id=%s ORDER BY CASE WHEN LOWER(COALESCE(role,''))='owner' THEN 0 ELSE 1 END, group_id LIMIT 2",(uid,))
                rows=[str((x or {}).get('group_id') or '') for x in c.fetchall()]
                rows=[x for x in rows if x and x!='PRIVATE']
                if len(rows)==1:
                    return rows[0]
        finally:
            conn.close()
        return gid

    @app.post('/api/rainbow/admin/announcement/{announcement_id}/push')
    async def admin_announcement_push(announcement_id: int, request: Request):
        uid,gid,r=require_admin(request)
        conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT id,title,content,link_url,(image_data IS NOT NULL) has_image FROM web_announcements WHERE id=%s AND group_id=%s',(announcement_id,gid))
                row=c.fetchone()
                if not row: raise HTTPException(404,'找不到公告。')
                row=dict(row)
        finally: conn.close()
        try:
            from linebot.models import FlexSendMessage
            base=_public_base_url()
            bubble={'type':'bubble','size':'mega','styles':{'body':{'backgroundColor':'#120B38'},'footer':{'backgroundColor':'#120B38','separator':True,'separatorColor':'#4C3C88'}},'body':{'type':'box','layout':'vertical','paddingAll':'18px','spacing':'md','contents':[{'type':'text','text':'📢 RAINBOW LIFE','size':'xs','weight':'bold','color':'#C4B5FD'},{'type':'text','text':str(row.get('title') or 'Rainbow Life 公告')[:80],'size':'xl','weight':'bold','color':'#FFFFFF','wrap':True},{'type':'text','text':str(row.get('content') or '')[:500],'size':'sm','color':'#EDE9FE','wrap':True,'maxLines':8,'lineSpacing':'5px'}]},'footer':{'type':'box','layout':'vertical','paddingAll':'12px','contents':[{'type':'button','style':'primary','height':'sm','color':'#8B5CF6','action':{'type':'uri','label':'查看完整公告','uri':make_player_entry_url(gid,f'/player?tab=announcements&announcement={announcement_id}') or base or 'https://line.me'}}]}}
            if row.get('has_image') and base:
                bubble['hero']={'type':'image','url':f"{base}/api/rainbow/public/announcement/{announcement_id}/image",'size':'full','aspectRatio':'20:13','aspectMode':'cover'}
            target_gid=_announcement_line_target(gid,uid)
            if not target_gid or target_gid=='PRIVATE':
                raise RuntimeError('找不到可推播的 LINE 群組，請先從群組內重新開啟後台。')
            line_bot_api.push_message(target_gid,FlexSendMessage(alt_text='📢 Rainbow Life 公告',contents=bubble))
        except Exception as exc:
            raise HTTPException(500,f'聊天室推播失敗：{exc}')
        conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('INSERT INTO web_admin_logs(group_id,user_id,action,detail) VALUES(%s,%s,%s,%s)',(gid,uid,'announcement_push',str(announcement_id)))
            conn.commit()
        finally: conn.close()
        return {'ok':True,'message':'公告已推播到聊天室。'}

    def require_owner(request):
        uid,gid=_auth(request)
        if _role(gid,uid)!='owner': raise HTTPException(403,'僅 Rainbow Life Owner 可使用。')
        return uid,gid

    @app.get('/api/rainbow/owner/overview')
    async def owner_overview(request: Request):
        require_owner(request); conn=get_connection()
        try:
            with conn.cursor() as c:
                c.execute('SELECT COUNT(DISTINCT group_id) groups,COUNT(*) members FROM players'); row=c.fetchone() or {}
        finally: conn.close()
        return {k:int(row.get(k) or 0) for k in ('groups','members')}

    @app.get('/api/rainbow/owner/groups')
    async def owner_groups(request: Request):
        require_owner(request); conn=get_connection()
        try:
            with conn.cursor() as c: c.execute('SELECT group_id,COUNT(*) members FROM players GROUP BY group_id ORDER BY members DESC LIMIT 100'); rows=c.fetchall()
        finally: conn.close()
        return {'groups':[dict(x) for x in rows]}

    @app.get('/api/rainbow/owner/leaders')
    async def owner_leaders(request: Request):
        require_owner(request); conn=get_connection()
        try:
            with conn.cursor() as c: c.execute("SELECT group_id,user_id,role FROM admins WHERE LOWER(COALESCE(role,'')) IN ('owner','leader','group_owner','群長') ORDER BY group_id"); rows=c.fetchall()
        finally: conn.close()
        return {'leaders':[dict(x) for x in rows]}

    @app.get('/api/rainbow/owner/global')
    async def owner_global(request: Request):
        require_owner(request)
        return {'status':'ready','message':'全站公告管理入口已建立'}
